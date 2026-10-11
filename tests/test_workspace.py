import json
import subprocess
import sys

import pytest

from amu import workspace


def _amu(args, cwd):
    return subprocess.run([sys.executable, "-m", "amu.cli", *args], cwd=cwd, capture_output=True, text=True)


def _git_repo(tmp_path, name="proj"):
    d = tmp_path / name
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d)
    (d / "a.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "."], cwd=d)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=d)
    return d


def _run_git(args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_add_rejects_non_git_path(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(workspace.WorkspaceError) as exc:
        workspace.add(str(tmp_path / "plain"), do_index=False)
    assert exc.value.code == 2 and "not inside a Git worktree" in str(exc.value)


def test_add_local_indexes_and_activates(fake_entire, tmp_path):
    d = _git_repo(tmp_path)
    e = workspace.add(str(d))
    ws = workspace.load()
    assert ws["active"] == e["name"] and e["counts"]["symbols"] == 12 and (d / ".amu" / "snapshot.ndjson").exists()


def test_index_repo_surfaces_graph_snapshot_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.entire, "index", lambda _repo: {"counts": {"files": 1, "relations": 1}})
    monkeypatch.setattr(
        workspace.entire, "snapshot", lambda _repo, _path, worktree=False: {"error": "snapshot unavailable"}
    )

    with pytest.raises(workspace.WorkspaceError, match="snapshot failed.*snapshot unavailable") as exc:
        workspace.index_repo(tmp_path)

    assert exc.value.code == 3


def test_workspace_index_snapshots_the_current_worktree(tmp_path, monkeypatch):
    snapshots = []
    monkeypatch.setattr(workspace.entire, "index", lambda _repo: {"counts": {"files": 1, "relations": {"CALLS": 1}}})

    def snapshot(repo, out_path, worktree=False):
        snapshots.append((repo, out_path, worktree))
        return {"lines": 1, "path": out_path}

    monkeypatch.setattr(workspace.entire, "snapshot", snapshot)

    workspace.index_repo(tmp_path)

    assert snapshots == [(str(tmp_path), str(tmp_path / ".amu" / "snapshot.ndjson"), True)]


def test_use_list_remove(fake_entire, tmp_path):
    a, b = _git_repo(tmp_path, "aa"), _git_repo(tmp_path, "bb")
    workspace.add(str(a), do_index=False)
    workspace.add(str(b), do_index=False)
    assert workspace.load()["active"] == "bb"
    workspace.use("aa")
    assert workspace.load()["active"] == "aa"
    r = workspace.remove("aa")
    assert r["deleted_clone"] is False and workspace.load()["active"] == "bb" and a.exists()


def test_remove_never_deletes_outside_managed_dir(tmp_path):
    d = _git_repo(tmp_path)
    workspace.add(str(d), do_index=False)
    r = workspace.remove(d.name, delete_clone=True)
    assert r["deleted_clone"] is False and d.exists()


def test_remove_delete_clone_rejects_managed_prefix_escape():
    outside_clone = workspace.home() / "repos-evil" / "clone"
    outside_clone.mkdir(parents=True)
    workspace.save({"active": "escape", "repos": [{"name": "escape", "path": str(outside_clone)}]})

    result = workspace.remove("escape", delete_clone=True)

    assert result["deleted_clone"] is False
    assert outside_clone.exists()


def test_remove_delete_clone_rejects_symlink_escape(tmp_path):
    managed = workspace.home() / "repos"
    managed.mkdir()
    outside_clone = tmp_path / "outside" / "clone"
    outside_clone.mkdir(parents=True)
    (managed / "redirect").symlink_to(outside_clone.parent, target_is_directory=True)
    workspace.save({"active": "escape", "repos": [{"name": "escape", "path": str(managed / "redirect" / "clone")}]})

    result = workspace.remove("escape", delete_clone=True)

    assert result["deleted_clone"] is False
    assert outside_clone.exists()


def test_remove_delete_clone_deletes_real_managed_clone():
    clone = workspace.home() / "repos" / "clone"
    clone.mkdir(parents=True)
    workspace.save({"active": "clone", "repos": [{"name": "clone", "path": str(clone)}]})

    result = workspace.remove("clone", delete_clone=True)

    assert result["deleted_clone"] is True
    assert not clone.exists()


def test_sync_surfaces_fetch_failure(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    entry = workspace.add(str(repo), do_index=False)

    monkeypatch.setattr(workspace, "_git", lambda args, cwd=None: (1, "network unavailable"))
    with pytest.raises(workspace.WorkspaceError, match="git fetch failed.*network unavailable") as exc:
        workspace.sync(entry["name"])

    assert exc.value.code == 3
    assert workspace.get(entry["name"])["sha"] == entry["sha"]


def test_sync_surfaces_fast_forward_failure(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    entry = workspace.add(str(repo), do_index=False)
    calls = []

    def fake_git(args, cwd=None):
        calls.append(args)
        if args == ["fetch", "--quiet"]:
            return 0, ""
        if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return 0, "origin/main"
        if args == ["merge", "--ff-only", "--", "origin/main"]:
            return 1, "not possible to fast-forward"
        pytest.fail(f"unexpected git command: {args}")

    monkeypatch.setattr(workspace, "_git", fake_git)
    with pytest.raises(workspace.WorkspaceError, match="git fast-forward failed.*not possible to fast-forward") as exc:
        workspace.sync(entry["name"])

    assert exc.value.code == 3
    assert calls == [
        ["fetch", "--quiet"],
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        ["merge", "--ff-only", "--", "origin/main"],
    ]


@pytest.mark.parametrize("diff", [None, {"error": "graph output exceeds byte limit", "rc": 125}])
def test_sync_diff_failure_cannot_claim_zero_changes_or_replace_successful_registration(tmp_path, monkeypatch, diff):
    repo = _git_repo(tmp_path)
    entry = workspace.add(str(repo), do_index=False)
    previous = workspace.load()
    monkeypatch.setattr(workspace, "_git", lambda _args, cwd=None: (0, ""))
    monkeypatch.setattr(workspace, "_fast_forward_tracked_branch", lambda _repo: None)
    monkeypatch.setattr(workspace, "index_repo", lambda _repo: {"tree": "new-tree", "counts": {"files": 2}})
    monkeypatch.setattr(workspace.state, "git_head", lambda _repo: "new-commit")
    monkeypatch.setattr(workspace.entire, "diff_json", lambda *_args: diff)

    with pytest.raises(workspace.WorkspaceError, match="graph diff failed") as exc:
        workspace.sync(entry["name"])

    assert exc.value.code == 3
    assert workspace.load() == previous


def test_sync_same_commit_does_not_require_or_invent_a_graph_diff(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    entry = workspace.add(str(repo), do_index=False)
    monkeypatch.setattr(workspace, "_git", lambda _args, cwd=None: (0, ""))
    monkeypatch.setattr(workspace, "_fast_forward_tracked_branch", lambda _repo: None)
    monkeypatch.setattr(workspace, "index_repo", lambda _repo: {"counts": {"files": 1}})
    monkeypatch.setattr(workspace.entire, "diff_json", lambda *_args: pytest.fail("unchanged HEAD needs no diff"))

    result = workspace.sync(entry["name"])

    assert result["entity_changes"] == result["files_changed"] == 0
    assert result["sha"] == result["old_sha"] == entry["sha"]


def test_sync_fast_forwards_checked_out_tracked_branch(fake_entire, tmp_path):
    source = _git_repo(tmp_path, "source")
    remote = tmp_path / "origin.git"
    _run_git(["init", "--bare", "-q", str(remote)])
    _run_git(["remote", "add", "origin", str(remote)], source)
    branch = _run_git(["branch", "--show-current"], source).stdout.strip()
    _run_git(["push", "-u", "origin", branch], source)

    checkout = tmp_path / "checkout"
    _run_git(["clone", "--branch", branch, "-q", str(remote), str(checkout)])
    entry = workspace.add(str(checkout), do_index=False)
    old_sha = _run_git(["rev-parse", "HEAD"], checkout).stdout.strip()

    (source / "a.py").write_text("def f():\n    return 2\n")
    _run_git(["add", "a.py"], source)
    _run_git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "update"], source)
    _run_git(["push"], source)
    updated_sha = _run_git(["rev-parse", "HEAD"], source).stdout.strip()

    result = workspace.sync(entry["name"])

    assert _run_git(["rev-parse", "HEAD"], checkout).stdout.strip() == updated_sha
    assert result["old_sha"] == old_sha
    assert result["sha"] == updated_sha


def test_resolve_order(tmp_path, monkeypatch):
    d = _git_repo(tmp_path)
    workspace.add(str(d), do_index=False)
    monkeypatch.chdir(tmp_path)  # not a git worktree → falls to the active repo
    assert workspace.resolve(None) == str(d)
    assert workspace.resolve("/explicit") == "/explicit"
    workspace.remove(d.name)
    with pytest.raises(workspace.WorkspaceError):
        workspace.resolve(None)


def test_touch_does_not_break_a_direct_repo_when_workspace_home_is_unavailable(monkeypatch):
    monkeypatch.setattr(workspace, "load", lambda: (_ for _ in ()).throw(OSError("read-only home")))

    workspace.touch("/repo", last_map="src/module.py")


def test_remote_spec_detection():
    assert workspace.is_remote_spec("numpy/numpy") and workspace.is_remote_spec("https://github.com/numpy/numpy.git")
    assert not workspace.is_remote_spec(".")


def test_cli_repo_list_and_cd(fake_entire, tmp_path):
    d = _git_repo(tmp_path)
    r = _amu(["repo", "add", str(d), "--json"], tmp_path)
    assert r.returncode == 0, r.stderr
    lst = json.loads(_amu(["repo", "list", "--json"], tmp_path).stdout)
    assert lst["active"] == d.name and lst["repos"][0]["active"] is True
    assert _amu(["cd", d.name], tmp_path).stdout.strip() == str(d)
    assert _amu(["cd", "nope"], tmp_path).returncode == 2
