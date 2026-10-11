"""Static public-surface manifests and review-only comparisons. No source execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import tomllib
from pathlib import Path

from amu import outbound

MAX_SOURCE_BYTES = 2_000_000
MAX_ENTRIES = 5000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_QUALIFIED_NAME = 2048


def _unknown(path: str, reason: str, language: str = "unknown") -> dict:
    return {"schema_version": 1, "path": path, "language": language, "status": "partial", "entries": [],
            "unknowns": [{"path": path, "reason": reason}], "provenance": "parser"}


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate configuration key")
        value[key] = item
    return value


def _finite_values(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite configuration value")
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "").replace("_", "")
    return any(token in normalized for token in ("password", "passwd", "secret", "token", "apikey", "credential",
                                                "privatekey", "accesskey"))


def _safe_display_value(value, depth: int = 0):
    """Redact structured secret bindings before converting containers to text."""
    if depth >= 16:
        return "[DISPLAY DEPTH OMITTED]"
    if isinstance(value, dict):
        result = {key: "[REDACTED]" if _sensitive_key(key) else _safe_display_value(item, depth + 1)
                  for key, item in list(value.items())[:100]}
        if len(value) > 100:
            result["[DISPLAY ITEMS OMITTED]"] = len(value) - 100
        return result
    if isinstance(value, list):
        result = [_safe_display_value(item, depth + 1) for item in value[:100]]
        if len(value) > 100:
            result.append("[DISPLAY ITEMS OMITTED]")
        return result
    return value


def analyze(source: str, path: str) -> dict:
    if not isinstance(source, str) or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return _unknown(path, "source exceeds the bounded analysis limit")
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".pyi"}:
        from amu.api_python import analyze as python
        result = python(source, path)
        result["schema_version"] = 1
        return result
    if suffix not in {".json", ".toml"}:
        result = _unknown(path, "no static public-surface adapter for this source language")
        result["status"] = "unsupported"
        return result
    try:
        parsed = (json.loads(source, object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                  if suffix == ".json" else tomllib.loads(source))
        _finite_values(parsed)
        if not isinstance(parsed, dict):
            return _unknown(path, "configuration/schema root must be an object", suffix[1:])
        entries = []
        emitted_bytes = 0
        pending = [((), parsed)]
        while pending:
            keys, value = pending.pop()
            if isinstance(value, dict) and value:
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("unsupported configuration key")
                pending.extend(((*keys, key), item) for key, item in sorted(value.items(), reverse=True))
                continue
            qualified = ".".join(key.replace("\\", "\\\\").replace(".", "\\.") if key else "\\e" for key in keys)
            if not qualified:
                continue
            if len(qualified.encode("utf-8")) > MAX_QUALIFIED_NAME:
                return {**_unknown(path, "configuration key identity exceeds the bounded limit", suffix[1:]), "entries": entries}
            display = repr(_safe_display_value(value))[:200]
            if any(_sensitive_key(key) for key in keys):
                display = "[REDACTED]"
            display = outbound.redact(display)
            entry = {"id": f"{path}#{qualified}", "qualified_name": qualified, "kind": "configuration",
                            "visibility": "unknown", "signature": f"{qualified} = {display}",
                            "signature_fingerprint": _hash(value), "body_fingerprint": _hash(value),
                            "facets": {"type": type(value).__name__, "default": display},
                            "provenance": "parser", "confidence": "requires_verification"}
            emitted_bytes += len(json.dumps(entry).encode("utf-8")) + 2
            if emitted_bytes > MAX_MANIFEST_BYTES - 16384:
                return {**_unknown(path, "configuration manifest byte limit reached", suffix[1:]), "entries": entries}
            entries.append(entry)
            if len(entries) >= MAX_ENTRIES:
                return {**_unknown(path, "configuration entry limit reached", suffix[1:]), "entries": entries}
        return {"schema_version": 1, "path": path, "language": suffix[1:], "status": "complete", "entries": entries,
                "unknowns": [], "provenance": "parser", "note": "static keys only; public intent and runtime response behavior require review"}
    except (ValueError, TypeError, RecursionError):
        return _unknown(path, "configuration/schema could not be parsed unambiguously", suffix[1:])


def compare(before: dict, after: dict) -> dict:
    path = after.get("path") or before.get("path")
    old = {entry["id"]: entry for entry in before.get("entries", [])}
    new = {entry["id"]: entry for entry in after.get("entries", [])}
    changes = []
    complete = before.get("status") == after.get("status") == "complete"
    unknowns = [*before.get("unknowns", []), *after.get("unknowns", [])]
    for identity in sorted(old.keys() | new.keys()):
        a, b = old.get(identity), new.get(identity)
        facets = sorted(key for key in (a or {}).get("facets", {}).keys() | (b or {}).get("facets", {}).keys()
                        if (a or {}).get("facets", {}).get(key) != (b or {}).get("facets", {}).get(key))
        if a is None or b is None:
            if not complete:
                continue  # Missing extraction is not an export removal/addition.
            kind = "added" if a is None else "removed"
        elif a.get("kind") != b.get("kind"):
            kind = "surface"
        elif a.get("kind") in {"alias", "reexport", "constant", "configuration", "config_key", "cli_option"}:
            if a.get("signature_fingerprint") == b.get("signature_fingerprint") and a.get("body_fingerprint") == b.get("body_fingerprint") and not facets:
                continue
            kind = "surface"
        elif a.get("signature_fingerprint") != b.get("signature_fingerprint"):
            kind = "signature"
        elif any(key not in {"returns", "exceptions", "return_shapes", "raised_exceptions"} for key in facets):
            kind = "surface"
        elif a.get("body_fingerprint") != b.get("body_fingerprint") or facets:
            kind = "behavior"
        else:
            continue
        changes.append({"id": identity, "change": kind, "facets_changed": facets, "before": a, "after": b,
                        "confidence": "requires_verification", "reason": "static public-surface change; confirm user-facing documentation impact"})
    return {"schema_version": 1, "path": path, "complete": complete, "changes": changes,
            "unknowns": unknowns, "before": before, "after": after}


def compare_sources(before: str, after: str, path: str) -> dict:
    return compare(analyze(before, path), analyze(after, path))


def _source_path(repo: str, path: str) -> tuple[Path, str]:
    raw = Path(path)
    if not path or not raw.parts or raw.is_absolute() or ".." in raw.parts or "\0" in path:
        raise ValueError("use a repository-relative source path")
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory is unavailable")
    candidate = root / raw
    # Resolve non-strictly first: a dangling parent alias is unsafe, not absence.
    if candidate.resolve(strict=False) != candidate or not candidate.is_relative_to(root):
        raise ValueError("source aliases are not supported")
    return candidate, raw.as_posix()


def _read(repo: str, path: str) -> str:
    candidate, _ = _source_path(repo, path)
    fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("source must be a regular file")
        source = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
        if any(getattr(before, key) != getattr(after, key) for key in ("st_size", "st_mtime_ns", "st_ctime_ns")):
            raise ValueError("source changed while being read")
        try:
            latest = candidate.stat(follow_symlinks=False)
            _source_path(repo, path)
        except (OSError, ValueError) as exc:
            raise ValueError("source disappeared or changed aliases while being read") from exc
        if (latest.st_dev, latest.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("source was replaced while being read")
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds analysis byte limit")
    return source.decode("utf-8")


def report(repo: str, path: str, base: str = "HEAD") -> dict:
    before, after = None, None
    capture = {"baseline": {"status": "unknown"}, "current": {"status": "unknown"}}
    try:
        _, path = _source_path(repo, path)
    except (OSError, ValueError):
        result = compare(_unknown(path, "source path is unsafe or unavailable"),
                         _unknown(path, "source path is unsafe or unavailable"))
        return {**result, "source_capture": capture}
    try:
        current = _read(repo, path)
        after = analyze(current, path)
        capture["current"] = {"status": "present", "sha256": hashlib.sha256(current.encode()).hexdigest()}
    except FileNotFoundError:
        # Only verified filesystem absence is an empty surface. IO errors remain unknown.
        after = _empty(path)
        capture["current"] = {"status": "absent"}
    except (OSError, ValueError, UnicodeError):
        after = _unknown(path, "current source is missing, unsafe, oversized or unreadable")
    try:
        revision = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"],
                                  cwd=repo, capture_output=True, text=True, timeout=15)
        if revision.returncode:
            raise ValueError("baseline revision unavailable")
        sha = revision.stdout.strip()
        capture["baseline"]["revision"] = sha
        tree = subprocess.run(["git", "--literal-pathspecs", "ls-tree", "-z", sha, "--", path],
                              cwd=repo, capture_output=True, timeout=15)
        if tree.returncode:
            raise ValueError("baseline path lookup failed")
        if not tree.stdout:
            before = _empty(path)
            capture["baseline"] = {"status": "absent", "revision": sha}
        else:
            entries = tree.stdout.rstrip(b"\0").split(b"\0")
            if len(entries) != 1:
                raise ValueError("baseline source is ambiguous")
            metadata, tree_path = entries[0].split(b"\t", 1)
            mode, kind, blob = metadata.decode("ascii").split()
            if tree_path.decode("utf-8") != path or kind != "blob" or mode not in {"100644", "100755"}:
                raise ValueError("baseline must be a regular source file")
            size = subprocess.run(["git", "cat-file", "-s", blob], cwd=repo, capture_output=True, text=True, timeout=15)
            if size.returncode or not 0 <= int(size.stdout) <= MAX_SOURCE_BYTES:
                raise ValueError("baseline exceeds analysis byte limit or is unavailable")
            source = subprocess.run(["git", "cat-file", "blob", blob], cwd=repo, capture_output=True, timeout=15)
            if source.returncode or len(source.stdout) > MAX_SOURCE_BYTES:
                raise ValueError("baseline source unavailable or oversized")
            before = analyze(source.stdout.decode("utf-8"), path)
            capture["baseline"] = {"status": "present", "revision": sha, "blob": blob,
                                   "sha256": hashlib.sha256(source.stdout).hexdigest()}
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        before = _unknown(path, "baseline source is unavailable, unsafe or oversized")
    return {**compare(before, after), "source_capture": capture}


def _empty(path: str) -> dict:
    suffix = Path(path).suffix.lower()
    if suffix not in {".py", ".pyi", ".json", ".toml"}:
        return analyze("", path)
    return {"schema_version": 1, "path": path, "language": suffix[1:], "status": "complete", "entries": [],
            "unknowns": [], "provenance": "parser", "note": "verified absent source"}
