"""Repository diagnostics never grant authority or execute project commands."""

import json
import subprocess

import pytest

from amu import mcp_access, state_io
from amu import repository_diagnostics as diagnostics


def _git_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("def target():\n    return 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)
    return repo


def _ready_provider():
    return {"ready": True}


def _row(report, name):
    return next(row for row in report["checks"] if row["name"] == name)


def _record_index(repo, *, sha=None, ready=True):
    if sha is None:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
    state_io.write_atomic(repo / ".amu" / "graph-readiness.json", {"sha": sha, "ready": ready})


def test_missing_repository_explains_client_server_filesystems_without_probing_provider(tmp_path):
    missing = str(tmp_path / "missing-private-name")
    result = diagnostics.repo_preflight(missing, provider_probe=lambda: pytest.fail("provider before access"))
    assert result["ready"] is False
    assert result["category"] == "REPOSITORY_NOT_FOUND"
    assert result["error_kind"] == "invalid_repository"
    assert "filesystem" in result["hint"] and "operator" in result["hint"]
    assert missing not in json.dumps(result)


@pytest.mark.parametrize("value", ["relative", "", None, 42, "/\x00"])
def test_invalid_repository_syntax_is_not_misreported_as_missing(value):
    result = diagnostics.repo_preflight(value, provider_probe=lambda: pytest.fail("provider before access"))
    assert result["category"] == "INVALID_REPOSITORY"
    assert result["ready"] is False


def test_file_is_not_a_directory(tmp_path):
    file = tmp_path / "file"
    file.write_text("private-content")
    result = diagnostics.repo_preflight(str(file))
    assert result["category"] == "INVALID_REPOSITORY"
    assert "private-content" not in json.dumps(result)


def test_unauthorized_root_stops_before_git_state_and_provider(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside-private-name"
    outside.mkdir()
    policy = mcp_access.MCPAccessPolicy([str(allowed)])
    monkeypatch.setattr(diagnostics, "git_metadata", lambda _: pytest.fail("Git after denial"))
    monkeypatch.setattr(diagnostics.state_io, "read", lambda *a, **k: pytest.fail("state after denial"))
    result = diagnostics.repo_preflight(str(outside), access_policy=policy,
                                       provider_probe=lambda: pytest.fail("provider after denial"))
    assert result["category"] == "REPOSITORY_NOT_AUTHORIZED"
    assert result["error_kind"] == "repository_not_allowed"
    encoded = json.dumps(result)
    assert str(allowed) not in encoded and str(outside) not in encoded


def test_parent_child_and_symlink_escape_cannot_expand_grant(tmp_path):
    repo = _git_repo(tmp_path)
    child = repo / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    policy = mcp_access.MCPAccessPolicy([str(repo)])
    for selected in (repo.parent, child, repo / "escape"):
        result = diagnostics.repo_preflight(str(selected), access_policy=policy)
        assert result["category"] == "REPOSITORY_NOT_AUTHORIZED"


def test_replacement_invalidates_grant_with_existing_error_code(tmp_path):
    repo = _git_repo(tmp_path)
    policy = mcp_access.MCPAccessPolicy([str(repo)])
    repo.rename(tmp_path / "original")
    repo.mkdir()
    result = diagnostics.repo_preflight(str(repo), access_policy=policy)
    assert result["category"] == "REPOSITORY_NOT_AUTHORIZED"
    assert result["error_kind"] == "repository_changed"


def test_authorized_git_root_without_index_reports_initialization(tmp_path):
    repo = _git_repo(tmp_path)
    policy = mcp_access.MCPAccessPolicy([str(repo)])
    before = sorted(str(path.relative_to(repo)) for path in repo.rglob("*"))
    result = diagnostics.repo_preflight(str(repo), access_policy=policy, provider_probe=_ready_provider)
    assert result["category"] == "REPOSITORY_NOT_INDEXED"
    assert result["initialization_required"] is True and not result["ready"]
    assert _row(result, "repository_authorization")["status"] == "authorized"
    assert _row(result, "git_repository")["status"] == "passed"
    assert before == sorted(str(path.relative_to(repo)) for path in repo.rglob("*"))
    assert not (repo / ".amu").exists()


def test_plain_directory_is_not_a_git_repository(tmp_path):
    result = diagnostics.repo_preflight(str(tmp_path), provider_probe=_ready_provider)
    assert result["category"] == "INVALID_REPOSITORY"
    assert _row(result, "git_repository")["status"] == "failed"


def test_git_subdirectory_requires_explicit_root_and_does_not_auto_select_parent(tmp_path):
    repo = _git_repo(tmp_path)
    child = repo / "child"
    child.mkdir()
    result = diagnostics.repo_preflight(str(child), provider_probe=_ready_provider)
    assert result["category"] == "INVALID_REPOSITORY"
    assert "root" in result["hint"]
    assert str(repo) not in json.dumps(result)


def test_unavailable_provider_is_distinct_and_sanitized(tmp_path):
    repo = _git_repo(tmp_path)
    result = diagnostics.repo_preflight(str(repo), provider_probe=lambda: {"ready": False, "error": "secret"})
    assert result["category"] == "GRAPH_PROVIDER_UNAVAILABLE"
    assert _row(result, "graph_provider")["status"] == "failed"
    assert "secret" not in json.dumps(result)


def test_provider_exception_is_not_an_empty_success(tmp_path):
    repo = _git_repo(tmp_path)

    def fail():
        raise OSError("private-provider-path")

    result = diagnostics.repo_preflight(str(repo), provider_probe=fail)
    assert result["category"] == "GRAPH_PROVIDER_UNAVAILABLE"
    assert "private-provider-path" not in json.dumps(result)


def test_stale_index_is_not_claimed_current(tmp_path):
    repo = _git_repo(tmp_path)
    _record_index(repo, sha="0" * 40)
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert result["category"] == "GRAPH_STALE"
    assert _row(result, "graph_freshness")["status"] == "stale"
    assert not result["ready"]


def test_current_head_index_does_not_certify_uncommitted_worktree(tmp_path):
    repo = _git_repo(tmp_path)
    _record_index(repo)
    (repo / "a.py").write_text("def target():\n    return 2\n")
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert _row(result, "graph_index")["status"] == "passed"
    freshness = _row(result, "graph_freshness")
    assert freshness["status"] == "current_head" and freshness["ready"] is False
    assert result["ready"] is False and result["category"] == "GRAPH_FRESHNESS_UNKNOWN"
    assert "worktree" in freshness["message"]


def test_trusted_current_worktree_probe_supports_diagnostic_readiness(tmp_path):
    repo = _git_repo(tmp_path)
    _record_index(repo)
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider,
                                       freshness_probe=lambda _: {"ready": True, "status": "current"})
    assert result["ready"] is True and result["category"] is None
    assert result["initialization_required"] is False
    assert _row(result, "repository_authorization")["status"] == "operator_selected"


@pytest.mark.parametrize("record", [{}, {"ready": "true"}, {"ready": False}, {"ready": True, "sha": "bad"}])
def test_incomplete_or_degraded_index_is_not_readiness_evidence(tmp_path, record):
    repo = _git_repo(tmp_path)
    state_io.write_atomic(repo / ".amu" / "graph-readiness.json", record)
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert result["ready"] is False and result["category"] == "REPOSITORY_NOT_INDEXED"


def test_index_symlink_is_rejected_without_reading_target(tmp_path):
    repo = _git_repo(tmp_path)
    root = repo / ".amu"
    root.mkdir(mode=0o700)
    private = tmp_path / "private.json"
    private.write_text('{"ready":true,"secret":"private-content"}')
    (root / "graph-readiness.json").symlink_to(private)
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert result["ready"] is False and "private-content" not in json.dumps(result)
    assert _row(result, "graph_index")["status"] == "failed"


def test_git_metadata_does_not_execute_fsmonitor_filter_or_hooks(tmp_path):
    repo = _git_repo(tmp_path)
    marker = tmp_path / "executed"
    script = tmp_path / "untrusted-helper"
    script.write_text(f"#!/bin/sh\nprintf hit > '{marker}'\ncat\n")
    script.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(script)], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", str(tmp_path)], cwd=repo, check=True)
    subprocess.run(["git", "config", "filter.fixture.clean", str(script)], cwd=repo, check=True)
    (repo / ".gitattributes").write_text("*.py filter=fixture\n")
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert _row(result, "git_repository")["status"] == "passed"
    assert result["error_kind"] == "unsafe_repository"
    assert _row(result, "git_configuration")["status"] == "blocked"
    assert not marker.exists()


def test_git_metadata_ignores_ambient_repository_redirection(tmp_path, monkeypatch):
    authorized = _git_repo(tmp_path, "authorized")
    unrelated = _git_repo(tmp_path, "unrelated")
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    result = diagnostics.repo_preflight(str(authorized), provider_probe=_ready_provider)
    assert _row(result, "git_repository")["status"] == "passed"
    assert str(unrelated) not in json.dumps(result)


def test_git_transport_failure_is_bounded_and_does_not_echo_stderr(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    from amu import process
    monkeypatch.setattr(diagnostics.process, "run", lambda *a, **k: process.Result(125, b"", b"", "output_limit", 8193, 0))
    result = diagnostics.repo_preflight(str(repo), provider_probe=_ready_provider)
    assert result["category"] == "INVALID_REPOSITORY"
    assert _row(result, "git_repository")["status"] == "failed"


def test_onboarding_is_read_only_and_does_not_expand_authority(tmp_path):
    repo = _git_repo(tmp_path)
    policy = mcp_access.MCPAccessPolicy([str(repo)])
    result = diagnostics.onboarding_preflight(str(repo), access_policy=policy, provider_probe=_ready_provider)
    assert result["initialization_required"] is True
    assert "amu init" in " ".join(result["next_actions"])
    assert policy.grants[0].path == repo.resolve() and len(policy.grants) == 1
    assert not (repo / ".amu").exists()


def test_workspace_local_registration_rejects_executable_helpers_before_index_or_state(tmp_path, monkeypatch):
    from amu import workspace
    repo = _git_repo(tmp_path)
    subprocess.run(["git", "config", "filter.fixture.process", "private-helper-value"], cwd=repo, check=True)
    monkeypatch.setattr(workspace, "index_repo", lambda _: pytest.fail("index after unsafe selection"))
    monkeypatch.setattr(workspace, "update", lambda _: pytest.fail("workspace write after unsafe selection"))
    with pytest.raises(workspace.WorkspaceError, match="executable Git filters") as exc:
        workspace.add(str(repo))
    assert exc.value.code == 2 and "private-helper-value" not in str(exc.value)


def test_workspace_local_registration_does_not_auto_select_parent(tmp_path, monkeypatch):
    from amu import workspace
    repo = _git_repo(tmp_path)
    child = repo / "child"
    child.mkdir()
    monkeypatch.setattr(workspace, "update", lambda _: pytest.fail("workspace write after invalid selection"))
    with pytest.raises(workspace.WorkspaceError, match="exact Git repository root"):
        workspace.add(str(child), do_index=False)


def test_local_git_config_include_cannot_hide_executable_helper(tmp_path):
    repo = _git_repo(tmp_path)
    included = repo / "included.gitconfig"
    included.write_text("[filter \"hidden\"]\n\tclean = private-helper-value\n")
    subprocess.run(["git", "config", "include.path", str(included)], cwd=repo, check=True)
    result = diagnostics.repo_preflight(str(repo), provider_probe=lambda: pytest.fail("provider after unsafe include"))
    assert result["error_kind"] == "unsafe_repository"
    assert "private-helper-value" not in json.dumps(result)


def test_local_registration_cannot_be_redirected_by_ambient_git_metadata(tmp_path, monkeypatch):
    from amu import workspace
    selected = _git_repo(tmp_path, "selected")
    unrelated = _git_repo(tmp_path, "unrelated")
    subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/selected/repository"],
                   cwd=selected, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/private/unrelated"],
                   cwd=unrelated, check=True)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    entry = workspace.add(str(selected), do_index=False)
    assert entry["path"] == str(selected.resolve())
    assert entry["origin"] == "https://example.invalid/selected/repository"
    assert entry["name"] == "repository"
    assert "private/unrelated" not in json.dumps(entry)


@pytest.mark.parametrize("name", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0",
                                 "GIT_CONFIG_VALUE_0", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_TRACE",
                                 "GIT_TRACE_SETUP", "GIT_EXTERNAL_DIFF", "GIT_PROXY_COMMAND", "GIT_SSH_COMMAND"])
def test_mcp_children_do_not_inherit_git_repository_or_execution_redirection(monkeypatch, name):
    monkeypatch.setenv(name, "private-redirection")
    environment = mcp_access.child_environment()
    assert name not in environment
    assert "private-redirection" not in environment.values()
    assert environment.get("PATH") == mcp_access.os.environ.get("PATH")
    assert environment.get("HOME") == mcp_access.os.environ.get("HOME")


def test_mcp_missing_directory_category_keeps_legacy_code_and_hides_paths(tmp_path):
    missing = str(tmp_path / "private-missing")
    with pytest.raises(mcp_access.AccessError) as exc:
        mcp_access._directory(missing)
    assert exc.value.kind == "invalid_repository"
    assert exc.value.category == "REPOSITORY_NOT_FOUND"
    assert "filesystems" in exc.value.hint
    assert missing not in str(exc.value) + exc.value.hint


def test_mcp_unauthorized_category_does_not_replace_authorization_error(tmp_path):
    authorized = tmp_path / "authorized"
    unauthorized = tmp_path / "unauthorized"
    authorized.mkdir()
    unauthorized.mkdir()
    policy = mcp_access.MCPAccessPolicy([str(authorized)])
    with pytest.raises(mcp_access.AccessError) as exc:
        policy.repository(str(unauthorized))
    assert exc.value.kind == "repository_not_allowed"
    assert exc.value.category == "REPOSITORY_NOT_AUTHORIZED"
    assert str(authorized) not in str(exc.value) + exc.value.hint


@pytest.mark.parametrize("health", [{}, {"healthy": False}, {"status": "degraded"}, {"rc": 1},
                                    {"checks": [{"name": "provider", "status": "failed", "required": True}]}])
def test_default_preflight_uses_actual_provider_health_failures(tmp_path, monkeypatch, health):
    repo = _git_repo(tmp_path)
    monkeypatch.setattr(diagnostics.entire, "available", lambda: True)
    monkeypatch.setattr(diagnostics.entire, "graph_available", lambda: True)
    monkeypatch.setattr(diagnostics.entire, "doctor", lambda: health)
    result = diagnostics.repo_preflight(str(repo))
    assert result["category"] == "GRAPH_PROVIDER_UNAVAILABLE"


def test_workspace_selection_rechecks_git_repository_before_changing_active(tmp_path):
    from amu import workspace
    first = _git_repo(tmp_path, "first")
    second = _git_repo(tmp_path, "second")
    entry = workspace.add(str(first), do_index=False)
    workspace.add(str(second), do_index=False)
    previous = workspace.load()
    first.rename(tmp_path / "previous-first")
    first.mkdir()
    with pytest.raises(workspace.WorkspaceError, match="not inside a Git worktree"):
        workspace.use(entry["name"])
    assert workspace.load() == previous
