"""`.amu/` state: JSON read/write, hashes, git helpers, and schema validation (validated on read and write)."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from amu import process, state_io, state_schema

AMU_DIR = ".amu"

SCHEMAS: dict[str, set[str]] = {
    "map": {"root", "nodes", "ripples", "features", "radius", "resolution", "next", "map_hash"},
    "node": {"id", "path", "symbol", "confidence", "risk", "feature", "order", "action", "via", "reason", "verify"},
    "contract": {"targets", "base_sha", "map_hash", "phases", "frozen_signatures", "allowed_files", "created_at"},
    "state": {"nodes", "violations", "updated_at"},
    "handoff": {"decision", "confidence", "escalate", "evidence", "next"},
    "memory_entry": {"id", "kind", "text", "evidence", "sha", "file_hash", "created", "last_seen", "pinned"},
}
CONFIDENCE = ("sound", "guessed", "unknown")
RISK = ("will_break", "might_break", "unknown", "none")
NODE_STATES = ("planned", "editing", "green", "red", "drift")

# Logical content caps apply once per dirty record, even though preserving the
# established digest framing requires two streaming passes over each file.
MAX_WORKTREE_STATUS_BYTES = 4 * 1024 * 1024
MAX_WORKTREE_STDERR_BYTES = 64 * 1024
MAX_WORKTREE_FILES = 25_000
MAX_WORKTREE_FILE_BYTES = 32 * 1024 * 1024
MAX_WORKTREE_TOTAL_BYTES = 256 * 1024 * 1024
WORKTREE_READ_CHUNK_BYTES = 64 * 1024
WORKTREE_CAPTURE_SECONDS = 30


class SchemaError(ValueError):
    pass


class StateReadError(SchemaError):
    """Existing state could not be trusted; it must not be treated as absent."""


_STATE_FIELDS = {
    "map.json": {"nodes": list, "ripples": list, "features": dict},
    "contract.json": {"targets": list, "phases": list, "frozen_signatures": list,
                      "allowed_files": list, "base_sha": str, "map_hash": str},
    "state.json": {"nodes": dict, "violations": list, "history": list},
    "config.json": {},
    "memory.json": {"entries": list, "history": list, "tombstones": dict},
    "refresh-state.json": {},
    "unknown-answers.json": {"answers": list},
    "sync-state.json": {},
    "nav.json": {"stack": list},
}


def _validate_saved(path: Path, value: Any) -> Any:
    """Check known field types while retaining unversioned legacy artifacts.

    Full workflow validation remains at its consumer: old partial maps are used
    by inspection tools, but malformed fields must never become default state.
    """
    fields = _STATE_FIELDS.get(path.name)
    if fields is None:
        return value
    if not isinstance(value, dict):
        raise StateReadError(f"{path.name}: expected a JSON object; restore or repair this state file")
    version = value.get("schema_version", 0)
    if type(version) is not int or version not in (0, 1):
        raise StateReadError(f"{path.name}: unsupported schema_version; use a compatible AMU version")
    for key, expected in fields.items():
        if key in value and not isinstance(value[key], expected):
            raise StateReadError(f"{path.name}: invalid {key} field; restore or repair this state file")
    try:
        state_schema.check(path.name, value)
    except (state_schema.InvalidState, TypeError) as exc:
        raise StateReadError(f"{path.name}: {exc}") from exc
    return value


def _read_json_path(path: Path, default: Any = None, *, managed_root: Path | None = None) -> Any:
    try:
        return state_io.read(path, default, lambda value: _validate_saved(path, value), managed_root=managed_root)
    except state_io.StateIOError as exc:
        raise StateReadError(str(exc)) from exc


def validate(kind: str, obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError(f"{kind} must be a JSON object")
    missing = SCHEMAS[kind] - set(obj)
    if missing:
        raise SchemaError(f"{kind} missing keys: {sorted(missing)}")
    try:
        state_schema.generated(kind, obj)
    except state_schema.InvalidState as exc:
        raise SchemaError(f"{kind}: {exc}") from exc
    if kind == "map":
        for n in obj["nodes"]:
            validate("node", n)
            if n["confidence"] not in CONFIDENCE:
                raise SchemaError(f"node {n['id']} bad confidence {n['confidence']}")
            if n["confidence"] != "sound" and not (n.get("reason") and n.get("verify")):
                raise SchemaError(f"node {n['id']} is {n['confidence']} but lacks reason/verify")
    return obj


def amu_dir(repo: str = ".") -> Path:
    p = Path(repo) / AMU_DIR
    if p.is_symlink():
        raise StateReadError(".amu: symbolic-link state directories are not supported")
    try:
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise StateReadError(".amu: unable to access the managed state directory") from exc
    return p


def read_json(name: str, repo: str = ".", default: Any = None) -> Any:
    """Return defaults only for absent files, never corrupt or unreadable state."""
    root, path = _managed_path(name, repo)
    return _read_json_path(path, default, managed_root=root)


def _managed_path(name: str, repo: str) -> tuple[Path, Path]:
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise StateReadError("state path must remain inside its managed directory")
    root = amu_dir(repo)
    return root, root / name


@contextmanager
def _json_write_lock(path: Path, *, managed_root: Path | None = None):
    """Serialize state writers across watcher/check processes for one file."""
    try:
        with state_io.lock(path, managed_root=managed_root):
            yield
    except state_io.StateIOError as exc:
        raise StateReadError(str(exc)) from exc


def _write_json_atomic(path: Path, obj: Any, *, managed_root: Path | None = None) -> Path:
    """Replace a JSON file atomically so readers never observe a partial write."""
    try:
        return state_io.write_atomic(path, obj, managed_root=managed_root)
    except state_io.StateIOError as exc:
        raise StateReadError(str(exc)) from exc


def write_json(name: str, obj: Any, repo: str = ".", kind: str | None = None) -> Path:
    if kind:
        validate(kind, obj)
    root, p = _managed_path(name, repo)
    _validate_saved(p, obj)
    with _json_write_lock(p, managed_root=root):
        _write_json_atomic(p, obj, managed_root=root)
    return p


def update_json(
    name: str,
    repo: str,
    default: Any,
    update: Callable[[Any], Any],
    kind: str | None = None,
) -> Any:
    """Read, update, and atomically replace one JSON state file under one lock."""
    root, p = _managed_path(name, repo)
    with _json_write_lock(p, managed_root=root):
        current = _read_json_path(p, default, managed_root=root)
        updated = update(current)
        _validate_saved(p, updated)
        if kind:
            validate(kind, updated)
        _write_json_atomic(p, updated, managed_root=root)
        return updated

def config(repo: str = ".") -> dict:
    return read_json("config.json", repo, default={
        "language": "unknown", "test_cmd": "pytest -q", "build_cmd": "", "docs_dir": "docs",
        "docs": {"mode": "draft"}, "model": "claude-opus-5", "web": {"report_url": ""},
    })


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_hash(path: str, repo: str = ".") -> str:
    p = Path(repo) / path
    if not p.exists() or p.is_dir():
        return "missing"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _git(args: list[str], repo: str = ".") -> str:
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_head(repo: str = ".") -> str:
    return _git(["rev-parse", "HEAD"], repo) or "no-git"


def git_changed_files(repo: str = ".", base: str | None = None) -> list[str]:
    """Files changed since `base` (committed) plus working-tree modifications and untracked files."""
    files: set[str] = set()
    if base and base != "no-git":
        files.update(f for f in _git(["diff", "--name-only", base, "HEAD"], repo).splitlines() if f)
    files.update(f for f in _git(["diff", "--name-only", "HEAD"], repo).splitlines() if f)
    files.update(f for f in _git(["ls-files", "--others", "--exclude-standard"], repo).splitlines() if f)
    return sorted(f for f in files if "__pycache__" not in f and not f.endswith((".pyc", ".DS_Store")))


def _worktree_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ValueError("worktree capture deadline exceeded")


def _worktree_same_stat(before: os.stat_result, after: os.stat_result) -> bool:
    return all(getattr(before, key) == getattr(after, key)
               for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode"))


def _open_worktree_file(root: Path, resolved: Path) -> int:
    """Open the already-resolved in-repo target without a symlink-race escape."""
    relative = resolved.relative_to(root)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if os.open not in os.supports_dir_fd:
        # Windows lacks descriptor-relative traversal. Final identity checks
        # still fail closed if a path changes, matching the prior boundary.
        return os.open(resolved, flags)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return os.open(relative.name, flags, dir_fd=directory)
    finally:
        os.close(directory)


def _worktree_stream_hash(descriptor: int, maximum: int, deadline: float, digest=None) -> tuple[str, int]:
    """Read bounded chunks and optionally retain the legacy raw-byte framing."""
    content_hash = hashlib.sha256()
    size = 0
    while True:
        _worktree_deadline(deadline)
        chunk = os.read(descriptor, min(WORKTREE_READ_CHUNK_BYTES, maximum - size + 1))
        _worktree_deadline(deadline)
        if not chunk:
            return content_hash.hexdigest(), size
        size += len(chunk)
        if size > maximum:
            raise ValueError("worktree file or aggregate byte limit exceeded while reading")
        content_hash.update(chunk)
        if digest is not None:
            digest.update(chunk)


def worktree_fingerprint(repo: str = ".") -> dict[str, Any]:
    """Return a content-sensitive fingerprint of tracked and untracked edits.

    A graph snapshot and a later live query are separate provider operations.
    Refresh compares this value immediately before the snapshot and immediately
    after the query so it can reject a graph assembled while an editor was
    changing files.  AMU's own state directory is intentionally excluded:
    recording refresh evidence must not make that evidence stale.
    Captures have status/record/per-file/aggregate byte bounds and one deadline.
    Source is hashed in fixed chunks, never allocated as a complete file. Two
    passes preserve the existing digest format; content/hash/identity checks
    refuse changes between those passes. Unreadable or unexpectedly missing
    paths return no digest. Only a deletion
    already reported by Git can be represented as a stable missing file.
    """
    deadline = time.monotonic() + WORKTREE_CAPTURE_SECONDS
    try:
        result = process.run(
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo,
            timeout=WORKTREE_CAPTURE_SECONDS,
            stdout_limit=MAX_WORKTREE_STATUS_BYTES,
            stderr_limit=MAX_WORKTREE_STDERR_BYTES,
        )
    except (OSError, ValueError) as exc:
        return {"digest": None, "files": [], "error": f"could not read Git worktree state: {exc}",
                "evidence_version": 1, "records": []}
    if result.error:
        detail = {"output_limit": "Git worktree status output byte limit exceeded",
                  "timeout": "worktree capture deadline exceeded while reading Git status",
                  "missing": "Git executable is unavailable",
                  "io": "could not safely read Git worktree status"}.get(result.error, "could not read Git worktree status")
        return {"digest": None, "files": [], "error": detail, "evidence_version": 1, "records": []}
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return {"digest": None, "files": [], "error": detail or "repository is not a Git worktree",
                "evidence_version": 1, "records": []}

    # In porcelain -z, every normal record begins ``XY ``. Rename/copy records
    # include a second, old-path token. Both endpoints affect scope, while the
    # current path supplies the bytes. Retain per-path hashes from that same
    # read so freshness domains do not re-read a different version of a file.
    tokens = iter(result.stdout.decode("utf-8", errors="surrogateescape").split("\0"))
    changes: list[dict[str, Any]] = []
    paths: set[str] = set()

    def internal(path: str) -> bool:
        return path == AMU_DIR or path.startswith(f"{AMU_DIR}/")

    for token in tokens:
        if time.monotonic() >= deadline:
            return {"digest": None, "files": sorted(paths), "error": "worktree capture deadline exceeded",
                    "evidence_version": 1, "records": []}
        if not token:
            continue
        if len(token) < 4 or token[2] != " ":
            return {"digest": None, "files": [], "error": "Git returned malformed worktree status",
                    "evidence_version": 1, "records": []}
        status, raw_path = token[:2], token[3:]
        old_path = None
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            old_path = next(tokens, None)
            if not old_path:
                return {"digest": None, "files": [], "error": "Git returned malformed rename status",
                        "evidence_version": 1, "records": []}
        if internal(raw_path) and (old_path is None or internal(old_path)):
            continue
        if len(changes) >= MAX_WORKTREE_FILES:
            return {"digest": None, "files": sorted(paths), "error": "worktree file count limit exceeded",
                    "evidence_version": 1, "records": []}
        for path in (raw_path, old_path):
            if path is None:
                continue
            parsed = Path(path)
            if not path or parsed.is_absolute() or ".." in parsed.parts:
                return {"digest": None, "files": sorted(paths), "failed_path": path,
                        "error": "Git returned an unsafe worktree path", "evidence_version": 1, "records": []}
            if not internal(path):
                paths.add(path)
        changes.append({"path": raw_path, "status": status, "old_path": old_path})

    root = Path(repo).resolve()
    digest = hashlib.sha256()
    records: list[dict[str, Any]] = []
    total_bytes = 0
    for change in sorted(changes, key=lambda row: (row["path"], row["old_path"] or "", row["status"])):
        path = change["path"]
        status = change["status"]
        record = dict(change)
        record["paths"] = sorted(p for p in (path, change["old_path"]) if p and not internal(p))
        digest.update(json.dumps(change, sort_keys=True).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        # A rename into AMU state still removes its old source/doc path. The
        # destination is excluded like all other AMU state, including its bytes.
        if internal(path):
            record.update(kind="relocated_to_state", content_hash=None, mode=None)
            records.append(record)
            digest.update(b"relocated_to_state\0")
            continue
        candidate = root / path
        try:
            _worktree_deadline(deadline)
            # lstat distinguishes an actual deletion from a dangling symlink.
            # Unlike is_file/is_dir, stat errors must remain explicit failures.
            try:
                original = candidate.lstat()
            except FileNotFoundError:
                if not (status[1] == "D" or status == "D "):
                    raise
                record.update(kind="deleted", content_hash=None, mode=None)
                records.append(record)
                digest.update(b"missing\0")
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("path resolves outside the repository")
            resolved_stat = resolved.stat()
            mode = resolved_stat.st_mode
            # Git reports a dirty submodule as one directory entry.  Treating
            # that directory as a missing file gives every nested edit the
            # same digest, allowing a stale graph refresh to look current.
            # Until AMU has a recursive submodule fingerprint, this must be a
            # hard uncertainty rather than a fabricated stable state.
            if stat.S_ISDIR(mode):
                raise ValueError("changed directory or submodule cannot be fingerprinted")
            if not stat.S_ISREG(mode):
                raise ValueError("path is not a regular file")
            if resolved_stat.st_size > MAX_WORKTREE_FILE_BYTES:
                raise ValueError("worktree file byte limit exceeded")
            if resolved_stat.st_size > MAX_WORKTREE_TOTAL_BYTES - total_bytes:
                raise ValueError("worktree aggregate byte limit exceeded")
            descriptor = _open_worktree_file(root, resolved)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or not _worktree_same_stat(resolved_stat, opened):
                    raise ValueError("file changed while its content was captured")
                maximum = min(MAX_WORKTREE_FILE_BYTES, MAX_WORKTREE_TOTAL_BYTES - total_bytes)
                content_hash, captured_bytes = _worktree_stream_hash(descriptor, maximum, deadline)
                if not _worktree_same_stat(opened, os.fstat(descriptor)) or captured_bytes != opened.st_size:
                    raise ValueError("file changed while its content was captured")
                record.update(kind="file", content_hash=content_hash,
                              mode=stat.S_IMODE(original.st_mode), resolved_mode=stat.S_IMODE(mode))
                if stat.S_ISLNK(original.st_mode):
                    record["symlink"] = os.readlink(candidate)
                digest.update(json.dumps(record, sort_keys=True).encode("utf-8", errors="surrogateescape"))
                digest.update(b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                second_hash, second_size = _worktree_stream_hash(descriptor, maximum, deadline, digest)
                if second_hash != content_hash or second_size != captured_bytes or not _worktree_same_stat(opened, os.fstat(descriptor)):
                    raise ValueError("file changed while its content was captured")
                total_bytes += captured_bytes
            finally:
                os.close(descriptor)
            current = resolved.stat()
            current_link = candidate.lstat()
            if (not _worktree_same_stat(resolved_stat, current) or not _worktree_same_stat(original, current_link)
                    or candidate.resolve(strict=True) != resolved):
                raise ValueError("file changed while its content was captured")
            digest.update(b"\0")
            records.append(record)
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "digest": None,
                "files": sorted(paths),
                "failed_path": path,
                "error": f"cannot fingerprint {path}: {type(exc).__name__}: {exc}",
                "evidence_version": 1,
                "records": records,
            }
    if time.monotonic() >= deadline:
        return {"digest": None, "files": sorted(paths), "error": "worktree capture deadline exceeded",
                "evidence_version": 1, "records": records}
    return {"digest": digest.hexdigest()[:24], "files": sorted(paths), "error": None,
            "evidence_version": 1, "records": records}


def repo_name(repo: str = ".") -> str:
    url = _git(["remote", "get-url", "origin"], repo)
    if url:
        return url.rstrip("/").removesuffix(".git").split("/")[-1]
    return os.path.basename(os.path.abspath(repo))


def detect_commands(repo: str = ".") -> dict:
    r = Path(repo)
    if (r / "pyproject.toml").exists():
        # Run through the interpreter that launched amu.  Bare `python` is not
        # present on many Python 3 installations, and compileall is a useful
        # dependency-free baseline build check for an arbitrary pyproject repo.
        python = shlex.quote(sys.executable)
        return {"language": "Python", "test_cmd": f"{python} -m pytest -q", "build_cmd": f"{python} -m compileall -q ."}
    if (r / "package.json").exists():
        return {"language": "TypeScript", "test_cmd": "npm test", "build_cmd": "npm run build"}
    if (r / "go.mod").exists():
        return {"language": "Go", "test_cmd": "go test ./...", "build_cmd": "go build ./..."}
    return {"language": "unknown", "test_cmd": "", "build_cmd": ""}
