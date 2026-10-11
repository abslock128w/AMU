"""Code and documentation revisions derived from one strict worktree capture.

The full fingerprint remains available for lifecycle evidence. Code/doc
digests additionally bind the commit and AMU's own configuration, whose state
directory is intentionally absent from the worktree fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from amu import state, state_io

_DOC_SUFFIXES = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}
_CONFIG_PATHS = (".amu/config.json", ".amu/feature_map.json")
MAX_CONFIGURATION_BYTES = state_io.MAX_JSON_BYTES
CONFIGURATION_READ_CHUNK_BYTES = 64 * 1024
CONFIGURATION_CAPTURE_SECONDS = 30


def is_code_path(path: str) -> bool:
    """Match the conservative graph-scope classifier, including docs examples."""
    if not isinstance(path, str) or not path:
        return False
    raw = Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        return False
    if any(part in {".git", ".amu", "__pycache__"} for part in raw.parts):
        return False
    if raw.suffix.lower() == ".txt" and (
        raw.name.lower() == "cmakelists.txt"
        or raw.name.lower().startswith(("requirements", "constraints"))
        or any(part.lower() in {"requirements", "constraints"} for part in raw.parts[:-1])
    ):
        return True
    return raw.suffix.lower() not in _DOC_SUFFIXES


def _configuration(repo: str) -> tuple[str | None, str | None]:
    """Hash raw, bounded configuration through the strict state-file boundary."""
    root = Path(repo).resolve()
    deadline = time.monotonic() + CONFIGURATION_CAPTURE_SECONDS
    rows = []
    for name in _CONFIG_PATHS:
        path = root / name
        try:
            try:
                parent = path.parent.lstat()
            except FileNotFoundError:
                rows.append({"path": name, "content_hash": "missing"})
                continue
            if not stat.S_ISDIR(parent.st_mode):
                raise ValueError("AMU configuration directory is not a regular directory")
            with state_io._directory(path, root / ".amu", create=False) as directory:
                try:
                    descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                         dir_fd=directory.descriptor)
                except FileNotFoundError:
                    directory.verify()
                    rows.append({"path": name, "content_hash": "missing"})
                    continue
                try:
                    before = os.fstat(descriptor)
                    state_io._regular(path, before)
                    if before.st_size > MAX_CONFIGURATION_BYTES:
                        raise ValueError("configuration byte limit exceeded")
                    content_hash = hashlib.sha256()
                    size = 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise ValueError("configuration capture deadline exceeded")
                        chunk = os.read(descriptor, min(CONFIGURATION_READ_CHUNK_BYTES, MAX_CONFIGURATION_BYTES - size + 1))
                        if time.monotonic() >= deadline:
                            raise ValueError("configuration capture deadline exceeded")
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_CONFIGURATION_BYTES:
                            raise ValueError("configuration byte limit exceeded while reading")
                        content_hash.update(chunk)
                    after = os.fstat(descriptor)
                    current = directory.target(path.name)
                    if (not state._worktree_same_stat(before, after) or not state._worktree_same_stat(before, current)
                            or size != before.st_size):
                        raise ValueError("configuration changed while being captured")
                    directory.verify()
                    rows.append({"path": name, "content_hash": content_hash.hexdigest(),
                                 "mode": stat.S_IMODE(before.st_mode)})
                finally:
                    os.close(descriptor)
        except (OSError, RuntimeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "configuration cannot be safely accessed"
            return None, f"cannot capture {name}: {type(exc).__name__}: {reason}"
    if time.monotonic() >= deadline:
        return None, "configuration capture deadline exceeded"
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(), None


def _domain(records: list[dict], code: bool, head: str, config_hash: str) -> dict[str, Any]:
    rows = [record for record in records if any(is_code_path(path) == code for path in record["paths"])]
    files = sorted({path for record in rows for path in record["paths"] if is_code_path(path) == code})
    revision = {"head": head, "config_hash": config_hash, "records": rows}
    digest = hashlib.sha256(json.dumps(revision, sort_keys=True).encode("utf-8", errors="surrogateescape")).hexdigest()[:24]
    return {"digest": digest, "files": files, "error": None}


def _has_path_evidence(full: Mapping[str, Any]) -> bool:
    """Only partition captures whose records actually carry file evidence."""
    if type(full.get("evidence_version")) is not int or full["evidence_version"] != 1:
        return False
    records, files = full.get("records"), full.get("files")
    if not isinstance(records, list) or not isinstance(files, list):
        return False
    if full.get("error"):
        # Real failed captures can contain only the records collected before
        # the failing path. Their error blocks every derived domain below.
        return True
    if any(not isinstance(path, str) or not path for path in files):
        return False
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("paths"), list) or not row["paths"]:
            return False
        if any(not isinstance(path, str) or not path for path in row["paths"]):
            return False
        if not isinstance(row.get("path"), str) or not row["path"] or not isinstance(row.get("status"), str) or len(row["status"]) != 2:
            return False
        kind = row.get("kind")
        if kind == "file":
            content_hash = row.get("content_hash")
            if not isinstance(content_hash, str) or len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
                return False
            if any(type(row.get(key)) is not int or not 0 <= row[key] <= 0o7777 for key in ("mode", "resolved_mode")):
                return False
        elif kind not in {"deleted", "relocated_to_state"} or row.get("content_hash") is not None or row.get("mode") is not None:
            return False
    return sorted({path for row in records for path in row["paths"]}) == files


def capture(repo: str = ".") -> dict[str, Any]:
    """Capture strict full/code/doc revisions; legacy evidence stays strict full.

    If a stub or old fingerprint lacks per-path evidence, both domains receive
    its full fingerprint without a schema version. Consumers must retain full
    comparisons for such captures rather than inventing excluded doc paths.
    """
    head = state.git_head(repo)
    full = state.worktree_fingerprint(repo)
    config_hash, config_error = _configuration(repo)
    after_head = state.git_head(repo)
    error = full.get("error") or config_error
    if head == "no-git" or not isinstance(head, str) or not head:
        error = error or "cannot capture a valid Git revision"
    if head != after_head:
        error = error or "Git revision changed while freshness was captured"
    result: dict[str, Any] = {"head": head, "full": full, "config_hash": config_hash, "error": error}
    records = full.get("records")
    if not _has_path_evidence(full):
        result.update(code=dict(full), docs=dict(full))
        return result
    result["schema_version"] = 1
    if not error and not full.get("digest"):
        error = "worktree capture has no digest"
    result["error"] = error
    if error:
        files = full.get("files") if isinstance(full.get("files"), list) else []
        result.update(code={"digest": None, "files": [p for p in files if is_code_path(p)], "error": error},
                      docs={"digest": None, "files": [p for p in files if not is_code_path(p)], "error": error})
        return result
    result.update(code=_domain(records, True, head, config_hash), docs=_domain(records, False, head, config_hash))
    return result


def valid_capture(value: object) -> bool:
    """Return whether versioned freshness can safely support a comparison."""
    if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or value.get("error"):
        return False
    head = value.get("head")
    config_hash = value.get("config_hash")
    if not isinstance(head, str) or not head or head == "no-git" or not isinstance(config_hash, str) or not config_hash:
        return False
    for name in ("full", "code", "docs"):
        domain = value.get(name)
        if not isinstance(domain, Mapping) or domain.get("error"):
            return False
        if not isinstance(domain.get("digest"), str) or not domain["digest"]:
            return False
        if not isinstance(domain.get("files"), list) or any(not isinstance(path, str) or not path for path in domain["files"]):
            return False
    return True
