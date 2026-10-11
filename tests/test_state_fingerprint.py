"""Regression tests for the worktree fingerprint guarding live graph reads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from amu import cli, lifecycle, state


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "module.py").write_text("def value():\n    return 1\n")
    _git(repo, "add", "module.py")
    _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-qm", "initial")
    return repo


def test_worktree_fingerprint_tracks_content_when_git_status_is_unchanged(tmp_path):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.write_text("def value():\n    return 2\n")

    first_status = _git(repo, "status", "--porcelain=v1")
    first = state.worktree_fingerprint(str(repo))

    source.write_text("def value():\n    return 3\n")
    second_status = _git(repo, "status", "--porcelain=v1")
    second = state.worktree_fingerprint(str(repo))

    assert first_status == second_status == " M module.py\n"
    assert first["error"] is None and second["error"] is None
    assert first["files"] == second["files"] == ["module.py"]
    assert first["digest"] != second["digest"]


def test_worktree_fingerprint_excludes_amu_state_changes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "module.py").write_text("def value():\n    return 2\n")
    before = state.worktree_fingerprint(str(repo))

    amu = repo / ".amu"
    amu.mkdir()
    (amu / "sync-state.json").write_text('{"at":"now"}\n')
    after_create = state.worktree_fingerprint(str(repo))
    (amu / "sync-state.json").write_text('{"at":"later"}\n')
    after_edit = state.worktree_fingerprint(str(repo))

    assert before["error"] is None
    assert before["files"] == ["module.py"]
    assert after_create == before
    assert after_edit == before


def test_worktree_fingerprint_fails_closed_for_a_dirty_submodule_directory(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "vendor").mkdir()

    def fake_status(args, **_kwargs):
        assert args[:5] == ["git", "-c", "core.fsmonitor=false", "status", "--porcelain=v1"]
        return state.process.Result(0, b" M vendor\0", b"", None, 10, 0)

    monkeypatch.setattr(state.process, "run", fake_status)

    fingerprint = state.worktree_fingerprint(str(repo))

    assert fingerprint["digest"] is None
    assert fingerprint["files"] == ["vendor"]
    assert "directory or submodule" in fingerprint["error"]


def test_worktree_fingerprint_reports_non_git_directory_explicitly(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    fingerprint = state.worktree_fingerprint(str(plain))

    assert fingerprint["digest"] is None
    assert fingerprint["files"] == []
    assert fingerprint["error"]
    assert "not a git repository" in fingerprint["error"].lower()


@pytest.mark.parametrize("error", [PermissionError("read denied"), FileNotFoundError("disappeared during read")])
def test_unreadable_changed_file_has_no_usable_digest(tmp_path, monkeypatch, error):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.write_text("def value():\n    return 2\n")
    original_open = state._open_worktree_file

    def unreadable(root, resolved):
        if resolved == source:
            raise error
        return original_open(root, resolved)

    monkeypatch.setattr(state, "_open_worktree_file", unreadable)
    fingerprint = state.worktree_fingerprint(str(repo))
    assert fingerprint["digest"] is None
    assert fingerprint["files"] == ["module.py"]
    assert fingerprint["failed_path"] == "module.py"
    assert "module.py" in fingerprint["error"] and str(error) in fingerprint["error"]


def test_stat_permission_failure_is_not_mistaken_for_a_missing_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.write_text("changed\n")
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == source:
            raise PermissionError("cannot inspect file")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    fingerprint = state.worktree_fingerprint(str(repo))
    assert fingerprint["digest"] is None
    assert "cannot inspect file" in fingerprint["error"]


def test_file_disappearing_after_git_status_is_not_a_verified_deletion(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.write_text("changed\n")
    original_run = state.process.run

    def disappear_after_status(args, **kwargs):
        result = original_run(args, **kwargs)
        if args[:4] == ["git", "-c", "core.fsmonitor=false", "status"]:
            source.unlink()
        return result

    monkeypatch.setattr(state.process, "run", disappear_after_status)
    fingerprint = state.worktree_fingerprint(str(repo))
    assert fingerprint["digest"] is None
    assert fingerprint["failed_path"] == "module.py"
    assert fingerprint["error"]


@pytest.mark.parametrize("staged", [False, True])
def test_git_reported_deletion_remains_fingerprintable(tmp_path, staged):
    repo = _repo(tmp_path)
    before = state.worktree_fingerprint(str(repo))
    (repo / "module.py").unlink()
    if staged:
        _git(repo, "add", "module.py")
    deleted = state.worktree_fingerprint(str(repo))
    assert deleted["error"] is None
    assert deleted["files"] == ["module.py"]
    assert deleted["digest"] and deleted["digest"] != before["digest"]
    assert state.worktree_fingerprint(str(repo)) == deleted


@pytest.mark.parametrize("target_kind", ["outside", "dangling", "loop"])
def test_unresolvable_or_external_symlink_cannot_produce_freshness_proof(tmp_path, monkeypatch, target_kind):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.unlink()
    target = tmp_path / "outside.py" if target_kind == "outside" else repo / "absent.py"
    if target_kind == "outside":
        target.write_text("outside content must not be read\n")
    if target_kind == "loop":
        target = source
    source.symlink_to(target)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("must reject before reading target"))
    fingerprint = state.worktree_fingerprint(str(repo))
    assert fingerprint["digest"] is None
    assert fingerprint["failed_path"] == "module.py"
    assert "module.py" in fingerprint["error"]


def test_readable_symlink_inside_repository_remains_supported(tmp_path):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    target = repo / "target.py"
    target.write_text("value = 1\n")
    source.unlink()
    source.symlink_to("target.py")
    first = state.worktree_fingerprint(str(repo))
    target.write_text("value = 2\n")
    second = state.worktree_fingerprint(str(repo))
    assert first["error"] is None and second["error"] is None
    assert first["digest"] != second["digest"]


def test_read_failure_blocks_map_refresh_and_phase_evidence(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    source = repo / "module.py"
    source.write_text("def value():\n    return 2\n")
    original_open = state._open_worktree_file

    def denied(root, resolved):
        if resolved == source:
            raise PermissionError("read denied")
        return original_open(root, resolved)

    monkeypatch.setattr(state, "_open_worktree_file", denied)
    with pytest.raises(cli.GraphDataError, match="module.py.*read denied"):
        cli._map_worktree_fingerprint(str(repo), "before")
    violations = cli._refresh_gate_violations(
        str(repo), {"targets": [{"symbol": "module.py#value", "change_class": "body"}]}, {"module.py"},
    )
    assert any("cannot verify post-edit graph freshness" in v.detail and "module.py" in v.detail for v in violations)
    evidence = lifecycle.capture_source(str(repo))
    assert not lifecycle.valid_source(evidence)
    assert evidence["digest"] is None and "read denied" in evidence["error"]
