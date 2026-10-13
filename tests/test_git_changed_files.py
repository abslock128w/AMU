"""Git change enumeration must never turn incomplete evidence into a clean result."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, state
from tests.test_check_e2e import _commit_source, _write_approved_state


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "tracked.py").write_text("old\n")
    _git(tmp_path, "add", "tracked.py")
    _git(tmp_path, "-c", "user.name=AMU Test", "-c", "user.email=amu@example.invalid", "commit", "-qm", "baseline")
    return _git(tmp_path, "rev-parse", "HEAD")


def test_clean_and_changed_enumeration(tmp_path):
    base = _repo(tmp_path)
    assert state.git_changed_files(str(tmp_path), base) == []
    (tmp_path / "tracked.py").write_text("new\n")
    (tmp_path / "new.py").write_text("new\n")
    assert state.git_changed_files(str(tmp_path), base) == ["new.py", "tracked.py"]


def test_committed_change_since_base_is_included(tmp_path):
    base = _repo(tmp_path)
    (tmp_path / "tracked.py").write_text("new\n")
    _git(tmp_path, "add", "tracked.py")
    _git(tmp_path, "-c", "user.name=AMU Test", "-c", "user.email=amu@example.invalid", "commit", "-qm", "change")
    assert state.git_changed_files(str(tmp_path), base) == ["tracked.py"]


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "missing"])
def test_failed_git_query_is_not_a_clean_result(tmp_path, monkeypatch, failure):
    base = _repo(tmp_path)
    original = state.subprocess.run

    def fail_first(argv, **kwargs):
        if argv[:3] == ["git", "diff", "--name-only"]:
            if failure == "nonzero":
                return subprocess.CompletedProcess(argv, 128, "", "fatal: bad revision")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, 30)
            raise FileNotFoundError("git")
        return original(argv, **kwargs)

    monkeypatch.setattr(state.subprocess, "run", fail_first)
    with pytest.raises(state.GitChangeError, match="git diff"):
        state.git_changed_files(str(tmp_path), base)


def test_later_query_failure_discards_partial_success(tmp_path, monkeypatch):
    base = _repo(tmp_path)
    (tmp_path / "tracked.py").write_text("new\n")
    original = state.subprocess.run

    def fail_untracked(argv, **kwargs):
        if argv[:2] == ["git", "ls-files"]:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: index unavailable")
        return original(argv, **kwargs)

    monkeypatch.setattr(state.subprocess, "run", fail_untracked)
    with pytest.raises(state.GitChangeError, match="git ls-files"):
        state.git_changed_files(str(tmp_path), base)


@pytest.mark.parametrize("command", ["check", "sweep", "done"])
def test_scope_and_completion_commands_fail_closed_with_actionable_error(
    fake_entire, tmp_path, monkeypatch, command
):
    if command == "check":
        _commit_source(tmp_path)
        _write_approved_state(tmp_path)
    else:
        base = _repo(tmp_path)
        state.write_json("map.json", {"map_hash": "fixture", "depth": 2, "nodes": []}, str(tmp_path))
        state.write_json("contract.json", {
            "targets": [], "base_sha": base, "map_hash": "fixture", "phases": [],
            "frozen_signatures": [], "allowed_files": [], "created_at": "fixture",
        }, str(tmp_path))
        monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})

    original = state.subprocess.run

    def fail_diff(argv, **kwargs):
        if argv[:3] == ["git", "diff", "--name-only"]:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: bad revision")
        return original(argv, **kwargs)

    monkeypatch.setattr(state.subprocess, "run", fail_diff)
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: pytest.fail("tests must not run"))
    result = CliRunner().invoke(cli.app, [command, "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["error_kind"] == "git_change_enumeration_failed"
    assert "git diff failed (exit 128)" in payload["error"]
    assert "retry" in payload["error"]
    if command == "check":
        assert state.read_json("state.json", str(tmp_path))["history"][-1]["passed"] is False
