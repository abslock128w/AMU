"""Commit generated memory files without consuming the user's staging area."""

import os
import subprocess
import tempfile
from pathlib import Path

from amu import memory

FILES = ("CLAUDE.md", "AGENTS.md")


class CommitError(ValueError):
    pass


def _git(repo: str, *args: str, env: dict | None = None) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, timeout=120)
    if result.returncode:
        # Hook output can contain secrets. Preserve the failing operation and
        # return code, not an arbitrary transcript in JSON/model context.
        raise CommitError(f"git {args[0]} failed (exit {result.returncode}); inspect Git configuration/hooks manually")
    return result.stdout


def _generated_only(before: str, after: str) -> bool:
    """Reject extra edits made while refresh was generating its block."""
    if after.count(memory.START) != 1 or after.count(memory.END) != 1:
        return False
    begin, end = after.index(memory.START), after.index(memory.END) + len(memory.END)
    block = after[begin:end]
    if memory.START in before and memory.END in before:
        pre, _, rest = before.partition(memory.START)
        _, _, post = rest.partition(memory.END)
        expected = pre + block + post
    else:
        expected = before + ("\n" if before and not before.endswith("\n") else "") + "\n" + block + "\n"
    return expected == after


def refresh(repo: str) -> dict:
    out = {"committed": False, "commit_status": "blocked", "commit_error": None,
           "refreshed": False, "changed_files": []}
    lock = None
    lock_fd = None
    head = None
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        if any(os.environ.get(key) for key in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE")):
            raise CommitError("custom Git directory/index environment is active; refresh without --commit and review manually")
        root = Path(repo).resolve()
        if Path(os.fsdecode(_git(repo, "rev-parse", "--show-toplevel", env=env)).strip()).resolve() != root:
            raise CommitError("memory commits require the repository root")
        head = _git(repo, "rev-parse", "--verify", "HEAD", env=env).strip()
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
            marker = Path(os.fsdecode(_git(repo, "rev-parse", "--git-path", name, env=env)).strip())
            if (root / marker).exists():
                raise CommitError("a merge, rebase, or cherry-pick is in progress; commit memory later")
        index = Path(os.fsdecode(_git(repo, "rev-parse", "--git-path", "index", env=env)).strip())
        index = root / index
        lock_path = index.with_name(index.name + ".lock")
        # Git itself honors this lock. Keep it through final index replacement
        # while all staging/commit commands use disposable alternate indexes.
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        lock = lock_path
        if index.is_symlink():
            raise CommitError("Git index must be a regular file; review manually")
        original_index = index.read_bytes()
        dirty = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *FILES, env=env)
        before = {}
        for name in FILES:
            path = root / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise CommitError("managed memory paths must be regular files; review manually")
            before[name] = path.read_bytes().decode("utf-8") if path.exists() else ""
        out.update(memory.refresh(repo))
        out["refreshed"] = True
        if dirty:
            raise CommitError("managed files already had staged, unstaged, or untracked edits; refreshed locally, commit manually")
        paths = out["changed_files"]
        if not paths:
            out["commit_status"] = "nothing_to_commit"
            return out
        if not set(paths) <= set(FILES):
            raise CommitError("unexpected memory commit path; review manually")
        generated = {name: (root / name).read_bytes() for name in paths}
        if any(not _generated_only(before[name], generated[name].decode()) for name in paths):
            raise CommitError("managed file changed outside the generated block; commit manually")
        with tempfile.TemporaryDirectory(prefix="amu-memory-commit-") as directory:
            commit_index = Path(directory) / "commit-index"
            preserved_index = Path(directory) / "preserved-index"
            commit_env = {**env, "GIT_INDEX_FILE": str(commit_index)}
            preserved_env = {**env, "GIT_INDEX_FILE": str(preserved_index)}
            preserved_index.write_bytes(original_index)
            _git(repo, "read-tree", os.fsdecode(head), env=commit_env)
            _git(repo, "add", "--", *paths, env=commit_env)
            staged = set(_git(repo, "diff", "--cached", "--name-only", "-z", env=commit_env).decode().split("\0")) - {""}
            if staged != set(paths):
                raise CommitError("staged memory patch does not match generated files; commit manually")
            if (_git(repo, "rev-parse", "HEAD", env=env).strip() != head
                    or index.read_bytes() != original_index
                    or any((root / name).is_symlink() or (root / name).read_bytes() != generated[name] for name in paths)):
                raise CommitError("repository changed during memory refresh; commit manually")
            _git(repo, "commit", "-qm", "chore: amu memory refresh", env=commit_env)
            out.update(committed=True, commit_status="committed")
            new_head = _git(repo, "rev-parse", "HEAD", env=env).strip()
            if new_head == head:
                out["committed"] = False
                raise CommitError("Git reported success without a new commit; inspect repository manually")
            out.update(committed=True, commit_sha=os.fsdecode(new_head), commit_status="committed")
            # Only these entries change in the real index. All unrelated
            # staged blobs and partial staging come from the original copy.
            _git(repo, "reset", "-q", os.fsdecode(new_head), "--", *paths, env=preserved_env)
            with os.fdopen(lock_fd, "wb") as stream:
                lock_fd = None
                stream.write(preserved_index.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(lock, index)
            lock = None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        out["commit_error"] = str(exc) if isinstance(exc, CommitError) else f"memory commit failed ({type(exc).__name__}); inspect repository manually"
        if out["committed"]:
            out["commit_status"] = "committed_index_update_failed"
        elif head is not None:
            try:
                if _git(repo, "rev-parse", "HEAD", env=env).strip() != head:
                    out["committed"] = None
                    out["commit_status"] = "outcome_unknown"
                    out["commit_error"] += "; HEAD changed unexpectedly; do not retry automatically"
            except (OSError, ValueError, subprocess.TimeoutExpired):
                out["committed"] = None
                out["commit_status"] = "outcome_unknown"
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        if lock is not None:
            lock.unlink(missing_ok=True)
    return out
