import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, memory


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "user.txt").write_text("original\n")
    (tmp_path / "AGENTS.md").write_text("User instructions.\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "baseline")
    monkeypatch.setattr(memory, "collect", lambda repo, **kwargs: [])
    monkeypatch.setattr(memory, "render_block", lambda entries, repo: memory.START + "\nGenerated\n" + memory.END)
    return tmp_path


def refresh(repo):
    result = CliRunner().invoke(cli.app, ["memory", "refresh", "--commit", "--repo", str(repo), "--json"])
    return result, json.loads(result.stdout)


def test_memory_commit_excludes_and_preserves_unrelated_staging(repo):
    (repo / "user.txt").write_text("staged user change\n")
    git(repo, "add", "user.txt")
    staged = git(repo, "ls-files", "--stage", "--", "user.txt")
    (repo / "user.txt").write_text("later unstaged user change\n")
    result, payload = refresh(repo)
    assert result.exit_code == 0, result.output
    assert payload["committed"] is True
    assert set(git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").decode().splitlines()) == {"CLAUDE.md", "AGENTS.md"}
    assert git(repo, "ls-files", "--stage", "--", "user.txt") == staged
    assert (repo / "user.txt").read_text() == "later unstaged user change\n"


def test_failed_commit_preserves_index_and_reports_failure(repo):
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    original_head = git(repo, "rev-parse", "HEAD")
    original_index = (repo / ".git/index").read_bytes()
    result, payload = refresh(repo)
    assert result.exit_code != 0
    assert payload["committed"] is False
    assert git(repo, "rev-parse", "HEAD") == original_head
    assert (repo / ".git/index").read_bytes() == original_index
    assert "Generated" in (repo / "AGENTS.md").read_text()


@pytest.mark.parametrize("staged", [False, True])
def test_existing_managed_file_edits_are_not_committed(repo, staged):
    (repo / "AGENTS.md").write_text("My unrelated instruction edit.\n")
    if staged:
        git(repo, "add", "AGENTS.md")
    original_index = (repo / ".git/index").read_bytes()
    original_head = git(repo, "rev-parse", "HEAD")
    result, payload = refresh(repo)
    assert result.exit_code != 0 and payload["committed"] is False
    assert git(repo, "rev-parse", "HEAD") == original_head
    assert (repo / ".git/index").read_bytes() == original_index
    assert "My unrelated instruction edit." in (repo / "AGENTS.md").read_text()


def test_no_change_does_not_make_an_empty_commit(repo):
    assert refresh(repo)[0].exit_code == 0
    head = git(repo, "rev-parse", "HEAD")
    result, payload = refresh(repo)
    assert result.exit_code == 0
    assert payload["committed"] is False and payload["commit_status"] == "nothing_to_commit"
    assert git(repo, "rev-parse", "HEAD") == head


def test_existing_index_lock_is_not_removed_or_overwritten(repo):
    lock = repo / ".git/index.lock"
    lock.write_bytes(b"another process owns this")
    original = (repo / "AGENTS.md").read_bytes()
    result, payload = refresh(repo)
    assert result.exit_code != 0 and payload["committed"] is False
    assert lock.read_bytes() == b"another process owns this"
    assert (repo / "AGENTS.md").read_bytes() == original


def test_staging_failure_keeps_real_index_and_head(repo, monkeypatch):
    from amu import memory_commit

    original_index = (repo / ".git/index").read_bytes()
    head = git(repo, "rev-parse", "HEAD")
    run = memory_commit._git

    def fail_add(repo, *args, **kwargs):
        if args[0] == "add":
            raise memory_commit.CommitError("simulated staging failure")
        return run(repo, *args, **kwargs)

    monkeypatch.setattr(memory_commit, "_git", fail_add)
    result, payload = refresh(repo)
    assert result.exit_code != 0 and payload["committed"] is False
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == original_index


def test_default_refresh_never_commits_or_stages(repo):
    original_index = (repo / ".git/index").read_bytes()
    head = git(repo, "rev-parse", "HEAD")
    result = CliRunner().invoke(cli.app, ["memory", "refresh", "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["committed"] is False
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == original_index


def test_crlf_prose_outside_memory_block_is_preserved(repo):
    original = b"User instructions.\r\nSecond line.\r\n"
    (repo / "AGENTS.md").write_bytes(original)
    git(repo, "add", "AGENTS.md")
    git(repo, "commit", "-qm", "CRLF baseline")
    result, payload = refresh(repo)
    assert result.exit_code == 0, result.output
    assert payload["committed"] is True
    assert (repo / "AGENTS.md").read_bytes().startswith(original)


def test_index_update_failure_does_not_hide_a_successful_commit(repo, monkeypatch):
    from amu import memory_commit

    run = memory_commit._git
    original_index = (repo / ".git/index").read_bytes()

    def fail_update(repo, *args, **kwargs):
        if args[0] == "reset":
            raise memory_commit.CommitError("simulated index reconciliation failure")
        return run(repo, *args, **kwargs)

    monkeypatch.setattr(memory_commit, "_git", fail_update)
    result, payload = refresh(repo)
    assert result.exit_code != 0
    assert payload["committed"] is True
    assert payload["commit_status"] == "committed_index_update_failed"
    assert git(repo, "rev-parse", "HEAD").decode().strip() == payload["commit_sha"]
    assert (repo / ".git/index").read_bytes() == original_index


def test_staged_additions_and_deletions_remain_staged(repo):
    (repo / "new user file.txt").write_text("User's new file\n")
    git(repo, "add", "new user file.txt")
    git(repo, "rm", "user.txt")
    staged_before = git(repo, "diff", "--cached", "--binary")
    result, payload = refresh(repo)
    assert result.exit_code == 0, result.output
    assert payload["committed"] is True
    assert git(repo, "diff", "--cached", "--binary") == staged_before
