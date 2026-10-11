"""Independent code/doc captures retain evidence from one worktree read."""

import subprocess
from pathlib import Path

import pytest

from amu import freshness, state


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def repo_at(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "module.py").write_text("def value():\n    return 1\n")
    (repo / "README.md").write_text("# Usage\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "initial")
    return repo


@pytest.mark.parametrize("path", ["README.md", "docs/usage.rst", "docs/image.svg"])
def test_prose_changes_only_documentation_digest(tmp_path, path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    document = repo / path
    document.parent.mkdir(exist_ok=True)
    document.write_text("updated documentation\n")
    after = freshness.capture(str(repo))
    assert after["schema_version"] == 1
    assert not after["error"]
    assert before["code"]["digest"] == after["code"]["digest"]
    assert before["docs"]["digest"] != after["docs"]["digest"]
    assert after["docs"]["files"] == [path]
    assert before["full"]["digest"] != after["full"]["digest"]


@pytest.mark.parametrize("path", ["module.py", "docs/conf.py", "docs/demo.ipynb", "docs/example.mdx", "package-lock.json", "requirements.txt", "constraints-dev.txt", "CMakeLists.txt", "docs/config.yaml"])
def test_source_examples_locks_and_config_change_code_digest(tmp_path, path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    source = repo / path
    source.parent.mkdir(exist_ok=True)
    source.write_text("updated source or build input\n")
    after = freshness.capture(str(repo))
    assert before["code"]["digest"] != after["code"]["digest"]
    assert after["code"]["files"] == [path]


@pytest.mark.parametrize("name", ["config.json", "feature_map.json"])
def test_internal_configuration_is_always_captured(tmp_path, name):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    config = repo / ".amu" / name
    config.parent.mkdir()
    config.write_text('{"value": 1}\n')
    after = freshness.capture(str(repo))
    assert before["full"]["digest"] == after["full"]["digest"]
    assert before["config_hash"] != after["config_hash"]
    assert before["code"]["digest"] != after["code"]["digest"]
    assert before["docs"]["digest"] != after["docs"]["digest"]


def test_committing_prose_retains_strict_head_boundary(tmp_path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    (repo / "README.md").write_text("new docs\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "docs")
    after = freshness.capture(str(repo))
    assert before["head"] != after["head"]
    assert before["code"]["digest"] != after["code"]["digest"]


def test_rename_across_domains_retains_both_paths(tmp_path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    git(repo, "mv", "module.py", "module.md")
    after = freshness.capture(str(repo))
    assert after["full"]["files"] == ["module.md", "module.py"]
    assert after["code"]["files"] == ["module.py"]
    assert after["docs"]["files"] == ["module.md"]
    assert before["code"]["digest"] != after["code"]["digest"]
    assert before["docs"]["digest"] != after["docs"]["digest"]


def test_status_and_executable_mode_are_fingerprinted(tmp_path):
    repo = repo_at(tmp_path)
    source = repo / "module.py"
    source.write_text("new source\n")
    unstaged = freshness.capture(str(repo))
    git(repo, "add", "module.py")
    staged = freshness.capture(str(repo))
    assert unstaged["code"]["digest"] != staged["code"]["digest"]
    source.chmod(source.stat().st_mode | 0o111)
    executable = freshness.capture(str(repo))
    assert staged["code"]["digest"] != executable["code"]["digest"]


def test_file_descriptor_capture_is_shared_by_all_domains(tmp_path, monkeypatch):
    repo = repo_at(tmp_path)
    document = repo / "README.md"
    document.write_text("updated docs\n")
    original = state._open_worktree_file
    opens = []

    def record_open(root, resolved):
        opens.append(resolved)
        return original(root, resolved)

    monkeypatch.setattr(state, "_open_worktree_file", record_open)
    captured = freshness.capture(str(repo))
    assert not captured["error"]
    # Two bounded passes share one descriptor; code/docs derive their evidence
    # from this one stable capture, rather than re-opening/re-reading the file.
    assert opens.count(document) == 1


@pytest.mark.parametrize("path", ["module.py", "README.md", ".amu/config.json", ".amu/feature_map.json"])
def test_read_failures_never_create_freshness_proof(tmp_path, monkeypatch, path):
    repo = repo_at(tmp_path)
    source = repo / path
    source.parent.mkdir(exist_ok=True)
    source.write_text("changed\n")
    original_open = state._open_worktree_file

    def denied_open(root, resolved):
        if resolved == source:
            raise PermissionError("test read denied")
        return original_open(root, resolved)

    monkeypatch.setattr(state, "_open_worktree_file", denied_open)
    original_regular = freshness.state_io._regular

    def denied_configuration(candidate, info, **kwargs):
        if candidate == source:
            raise PermissionError("test read denied")
        return original_regular(candidate, info, **kwargs)

    monkeypatch.setattr(freshness.state_io, "_regular", denied_configuration)
    captured = freshness.capture(str(repo))
    assert captured["error"]
    assert captured["code"]["digest"] is None
    assert captured["docs"]["digest"] is None


def test_legacy_fingerprint_uses_strict_full_fallback(tmp_path, monkeypatch):
    repo = repo_at(tmp_path)
    legacy = {"digest": "legacy-doc-change", "files": ["README.md"], "error": None}
    monkeypatch.setattr(state, "worktree_fingerprint", lambda *_: legacy)
    captured = freshness.capture(str(repo))
    assert "schema_version" not in captured
    assert captured["code"] == captured["docs"] == captured["full"] == legacy


@pytest.mark.parametrize("path", ["module.py", "README.md"])
def test_deletions_remain_evidence_in_the_matching_domain(tmp_path, path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    (repo / path).unlink()
    after = freshness.capture(str(repo))
    selected, other = ("code", "docs") if freshness.is_code_path(path) else ("docs", "code")
    assert after[selected]["files"] == [path]
    assert before[selected]["digest"] != after[selected]["digest"]
    assert before[other]["digest"] == after[other]["digest"]
    assert freshness.valid_capture(after)


def test_prose_rename_does_not_invalidate_code(tmp_path):
    repo = repo_at(tmp_path)
    before = freshness.capture(str(repo))
    git(repo, "mv", "README.md", "GUIDE.rst")
    after = freshness.capture(str(repo))
    assert before["code"]["digest"] == after["code"]["digest"]
    assert after["docs"]["files"] == ["GUIDE.rst", "README.md"]


def test_source_rename_into_amu_state_keeps_source_removal_evidence(tmp_path):
    repo = repo_at(tmp_path)
    (repo / ".amu").mkdir()
    git(repo, "mv", "module.py", ".amu/module.py")
    captured = freshness.capture(str(repo))
    assert captured["code"]["files"] == captured["full"]["files"] == ["module.py"]
    assert captured["full"]["records"][0]["kind"] == "relocated_to_state"
    assert freshness.valid_capture(captured)


@pytest.mark.parametrize("path", ["README.md", ".amu/config.json"])
def test_symlinks_outside_repository_fail_before_external_read(tmp_path, monkeypatch, path):
    repo = repo_at(tmp_path)
    candidate = repo / path
    candidate.parent.mkdir(exist_ok=True)
    candidate.unlink(missing_ok=True)
    external = tmp_path / "external.txt"
    external.write_text("external must not be read\n")
    candidate.symlink_to(external)
    original = Path.read_bytes

    def read_inside(path):
        assert path != external and path != candidate
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_inside)
    captured = freshness.capture(str(repo))
    assert captured["error"]
    assert not freshness.valid_capture(captured)
    assert captured["code"]["digest"] is None


def test_dirty_submodule_cannot_become_documentation_only(tmp_path, monkeypatch):
    repo = repo_at(tmp_path)
    (repo / "docs").mkdir()
    original = state.process.run

    def status_with_submodule(args, **kwargs):
        if args[:4] == ["git", "-c", "core.fsmonitor=false", "status"]:
            return state.process.Result(0, b" M docs\0", b"", None, 8, 0)
        return original(args, **kwargs)

    monkeypatch.setattr(state.process, "run", status_with_submodule)
    captured = freshness.capture(str(repo))
    assert "directory or submodule" in captured["error"]
    assert captured["code"]["digest"] is None
    assert captured["docs"]["digest"] is None


def test_head_change_during_capture_fails_closed(tmp_path, monkeypatch):
    repo = repo_at(tmp_path)
    original = state.worktree_fingerprint

    def commit_during_capture(path):
        captured = original(path)
        (repo / "README.md").write_text("changed mid-capture\n")
        git(repo, "add", ".")
        git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "new head")
        return captured

    monkeypatch.setattr(state, "worktree_fingerprint", commit_during_capture)
    captured = freshness.capture(str(repo))
    assert "Git revision changed" in captured["error"]
    assert not freshness.valid_capture(captured)


def test_stub_records_without_content_proof_use_full_fallback(tmp_path, monkeypatch):
    repo = repo_at(tmp_path)
    stub = {"digest": "fixture", "files": ["README.md"], "error": None, "evidence_version": 1,
            "records": [{"path": "README.md", "paths": ["README.md"], "status": " M"}]}
    monkeypatch.setattr(state, "worktree_fingerprint", lambda *_: stub)
    captured = freshness.capture(str(repo))
    assert "schema_version" not in captured
    assert captured["code"]["digest"] == captured["docs"]["digest"] == "fixture"


def test_valid_capture_rejects_errors_legacy_and_wrong_versions(tmp_path):
    captured = freshness.capture(str(repo_at(tmp_path)))
    assert freshness.valid_capture(captured)
    for version in (None, True, "1", 2):
        assert not freshness.valid_capture({**captured, "schema_version": version})
    assert not freshness.valid_capture({**captured, "error": "failed"})
    assert not freshness.valid_capture({**captured, "code": {"digest": None, "files": [], "error": None}})
