"""Reviewed inspection contract and bounded runtime identity, using stdlib only.

The expected schemas are maintained independently of SDK-generated tool lists.
Presentation titles and unordered schema lists are normalized; semantic fields,
output envelopes and safety annotations are not discarded. Build hashes identify
actual Python payload bytes, not provenance, authenticity or runtime correctness.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import sys
from pathlib import Path

from amu import __version__

MANIFEST_VERSION = "amu-inspection-v1"
PATCH_MANIFEST_VERSION = "amu-inspection-v3"
SCHEMA_VERSION = 1
IMPACT_SECTIONS = ("impact", "source_contradictions", "source_uncertainties", "dynamic_boundaries", "coverage", "reasons")
PATCH_SECTIONS = ("summary", "changes", "findings", "state_dependencies", "recommended_checks", "uncertainties")
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_BUILD_FILES = 256
_MAX_BUILD_ENTRIES = 4096
_MAX_BUILD_DIRECTORIES = 256
_MAX_BUILD_BYTES = 16 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
MCP_WIRE_LIMIT = 128 * 1024
_ANNOTATIONS = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
_OUTPUT = {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


# This is a reviewed contract, NOT generated from function signatures or an
# observed server response. Change the version when changing this contract.
_REVIEWED_TOOLS = [
    {"name": "amu_search", "inputSchema": _schema({"query": {"type": "string"}, "repo": {"type": "string"}}, ["query", "repo"]),
     "outputSchema": _OUTPUT, "annotations": _ANNOTATIONS},
    {"name": "amu_graph", "inputSchema": _schema({
        "target": {"type": "string"}, "repo": {"type": "string"},
        "depth": {"type": "integer", "default": 2}, "relation": _NULLABLE_STRING,
    }, ["target", "repo"]), "outputSchema": _OUTPUT, "annotations": _ANNOTATIONS},
    {"name": "amu_impact", "inputSchema": _schema({
        "file": {"type": "string"}, "line": {"type": "integer"},
        "proposed_change": {"type": "string"}, "repo": {"type": "string"},
        "depth": {"type": "integer", "default": 2}, "change_kind": {"type": "string", "default": "body"},
        "section": {"type": "string", "enum": list(IMPACT_SECTIONS), "default": "impact"},
        "cursor": {"anyOf": [{"type": "string", "maxLength": 1024}, {"type": "null"}], "default": None},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
    }, ["file", "line", "proposed_change", "repo"]), "outputSchema": _OUTPUT, "annotations": _ANNOTATIONS},
    {"name": "amu_docs_reviews", "inputSchema": _schema({"repo": {"type": "string"}, "preview": _NULLABLE_STRING}, ["repo"]),
     "outputSchema": _OUTPUT, "annotations": _ANNOTATIONS},
]

_REVIEWED_PATCH_TOOL = {
    "name": "amu_analyze_patch", "inputSchema": _schema({
        "patch": {"type": "string"}, "repo": {"type": "string"},
        "base": {"type": "string", "default": "HEAD"},
        "depth": {"type": "integer", "default": 1},
        "include_graph": {"type": "boolean", "default": False},
        "section": {"type": "string", "enum": list(PATCH_SECTIONS), "default": "summary"},
        "cursor": {"anyOf": [{"type": "string", "maxLength": 1024}, {"type": "null"}], "default": None},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    }, ["patch", "repo"]), "outputSchema": _OUTPUT, "annotations": _ANNOTATIONS,
}


def _encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def tool_result_bytes(text: str) -> int:
    """Conservative serialized response size for AMU's string-returning tools.

    The SDK sends the same string in text content and structured ``result``.
    Count both, worst-case Unicode escapes, a <=1024-UTF8-byte request id and
    1 KiB of envelope slack. The SDK owns incoming request ids; this bound is
    not a raw stdin quota or a guarantee for arbitrarily large client ids.
    """
    envelope = {"jsonrpc": "2.0", "id": "\x00" * 1024, "result": {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text}, "isError": False,
    }}
    return len(json.dumps(envelope, ensure_ascii=True, allow_nan=False).encode("utf-8")) + 1024


def _schema_normalized(value, depth: int = 0):
    if depth > 40 or not isinstance(value, (dict, bool)):
        raise ValueError("invalid or deeply nested MCP schema")
    if isinstance(value, bool):
        return value
    result = {}
    for key, item in value.items():
        if key == "title":
            continue  # Schema presentation only; property names are handled below.
        if key in {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}:
            if not isinstance(item, dict):
                raise ValueError("invalid MCP schema mapping")
            result[key] = {name: _schema_normalized(schema, depth + 1) for name, schema in item.items()}
        elif key in {"allOf", "anyOf", "oneOf"}:
            if not isinstance(item, list):
                raise ValueError("invalid MCP schema alternatives")
            result[key] = sorted((_schema_normalized(schema, depth + 1) for schema in item), key=_encoded)
        elif key in {"items", "additionalProperties", "unevaluatedProperties", "contains", "not", "if", "then", "else", "propertyNames"}:
            result[key] = _schema_normalized(item, depth + 1)
        elif key == "prefixItems":
            if not isinstance(item, list):
                raise ValueError("invalid MCP positional schema")
            result[key] = [_schema_normalized(schema, depth + 1) for schema in item]
        elif key in {"required", "enum"}:
            if not isinstance(item, list):
                raise ValueError("invalid MCP schema set")
            result[key] = sorted(copy.deepcopy(item), key=_encoded)
        else:
            result[key] = copy.deepcopy(item)  # Preserve defaults, formats, constraints and unknown semantic keywords.
    return result


def normalize_tools(tools: object) -> list[dict]:
    """Normalize the full input/output schema and annotations of each tool."""
    try:
        if len(_encoded(tools)) > _MAX_MANIFEST_BYTES:
            raise ValueError("MCP manifest exceeds its byte allowance")
        if not isinstance(tools, list) or len(tools) > 64:
            raise ValueError("MCP tools must be a bounded list")
        rows, names = [], set()
        for tool in tools:
            if not isinstance(tool, dict):
                raise ValueError("MCP tool must be an object")
            name = tool.get("name")
            if not isinstance(name, str) or not name or len(name) > 128 or name in names:
                raise ValueError("MCP tool names must be unique nonempty strings")
            if not isinstance(tool.get("inputSchema"), dict):
                raise ValueError("MCP tool needs an input schema")
            names.add(name)
            annotations = tool.get("annotations") or {}
            if not isinstance(annotations, dict):
                raise ValueError("MCP tool annotations must be an object")
            rows.append({"name": name, "inputSchema": _schema_normalized(tool["inputSchema"]),
                         "outputSchema": _schema_normalized(tool["outputSchema"]) if tool.get("outputSchema") is not None else None,
                         "annotations": {key: copy.deepcopy(item) for key, item in annotations.items() if key != "title"}})
        return sorted(rows, key=lambda row: row["name"])
    except (TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise ValueError("MCP manifest is malformed or exceeds its limits") from exc


def inspection_manifest(*, enable_patch_analysis: bool = False) -> dict:
    """Return the reviewed legacy contract or its explicit inspection extension."""
    if type(enable_patch_analysis) is not bool:
        raise ValueError("enable_patch_analysis must be a boolean")
    reviewed = _REVIEWED_TOOLS + ([_REVIEWED_PATCH_TOOL] if enable_patch_analysis else [])
    tools = normalize_tools(copy.deepcopy(reviewed))
    version = PATCH_MANIFEST_VERSION if enable_patch_analysis else MANIFEST_VERSION
    return {"schema_version": SCHEMA_VERSION, "manifest_version": version, "profile": "inspection", "tools": tools,
            "sha256": hashlib.sha256(_encoded(tools)).hexdigest()}


def compare_tools(observed: object, *, enable_patch_analysis: bool = False) -> dict:
    expected = inspection_manifest(enable_patch_analysis=enable_patch_analysis)
    try:
        actual = normalize_tools(observed)
    except ValueError:
        return {"match": False, "expected_version": expected["manifest_version"], "expected_sha256": expected["sha256"],
                "observed_sha256": None, "missing_tools": [], "unexpected_tools": [], "schema_mismatches": [],
                "error_kind": "manifest_invalid", "reason": "observed tool manifest is malformed or exceeds its limits"}
    wanted = {row["name"]: row for row in expected["tools"]}
    received = {row["name"]: row for row in actual}
    missing, unexpected = sorted(wanted.keys() - received.keys()), sorted(received.keys() - wanted.keys())
    changed = [name for name in sorted(wanted.keys() & received.keys()) if _encoded(wanted[name]) != _encoded(received[name])]
    return {"match": not (missing or unexpected or changed), "expected_version": expected["manifest_version"],
            "expected_sha256": expected["sha256"], "observed_sha256": hashlib.sha256(_encoded(actual)).hexdigest(),
            "missing_tools": missing, "unexpected_tools": unexpected, "schema_mismatches": changed}


def _file_signature(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _package_inventory(package: Path) -> tuple[list[Path], dict[Path, tuple]]:
    """Bound enumeration and retain directory identities for the final recheck."""
    files, directories_seen = [], {}
    entries = 0

    def unreadable(error):
        raise error

    for directory, directories, names in os.walk(package, followlinks=False, onerror=unreadable):
        current = Path(directory)
        info = current.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("package contains directory aliases")
        directories_seen[current] = _file_signature(info)
        entries += len(directories) + len(names)
        if len(directories_seen) > _MAX_BUILD_DIRECTORIES or entries > _MAX_BUILD_ENTRIES:
            raise ValueError("package enumeration bound exceeded")
        if any(Path(directory, name).is_symlink() for name in directories):
            raise ValueError("package contains directory aliases")
        directories[:] = [name for name in directories if name != "__pycache__"]
        files.extend(Path(directory, name) for name in names if name.endswith(".py"))
        if len(files) > _MAX_BUILD_FILES:
            raise ValueError("package file bound exceeded")
    return sorted(files), directories_seen


def package_build(package: Path) -> dict:
    """Hash actual bounded Python bytes; partial, raced or aliased captures fail."""
    total, digest = 0, hashlib.sha256()
    try:
        if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK")):
            raise ValueError("safe package identity is unavailable on this platform")
        if not stat.S_ISDIR(package.stat(follow_symlinks=False).st_mode):
            raise ValueError("package must be a non-aliased directory")
        files, directories = _package_inventory(package)
        if not files:
            raise ValueError("package source is unavailable")
        captured = {}
        for path in files:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE_BYTES:
                    raise ValueError("package source is not bounded regular text")
                source = stream.read(_MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                current = path.stat(follow_symlinks=False)
                if _file_signature(before) != _file_signature(after) or _file_signature(before) != _file_signature(current):
                    raise ValueError("package changed while identity was captured")
                captured[path] = _file_signature(after)
            total += len(source)
            if len(source) > _MAX_FILE_BYTES or total > _MAX_BUILD_BYTES:
                raise ValueError("package byte bound exceeded")
            digest.update(path.relative_to(package).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(source).digest())
        # Opening the last file is not enough: an earlier file can change, or
        # traversal can miss a new/deleted module while bytes are being read.
        final_files, final_directories = _package_inventory(package)
        if final_files != files or final_directories != directories:
            raise ValueError("package inventory changed while identity was captured")
        if any(_file_signature(path.stat(follow_symlinks=False)) != signature for path, signature in captured.items()):
            raise ValueError("package source changed while identity was captured")
        return {"sha256": digest.hexdigest(), "complete": True, "files": len(files), "bytes": total,
                "scope": "actual AMU Python payload only; not a wheel signature or provider identity"}
    except (OSError, ValueError, UnicodeError, RuntimeError):
        return {"sha256": None, "complete": False, "scope": "actual AMU Python payload only",
                "reason": "package identity unavailable, changed, aliased or exceeds bounds"}


def runtime_identity(*, enable_patch_analysis: bool = False) -> dict:
    """Operator diagnostics for this loaded runtime, without SDK/provider imports."""
    try:
        distribution_version = importlib.metadata.version("amu")
    except importlib.metadata.PackageNotFoundError:
        distribution_version = None
    build = package_build(Path(__file__).parent)
    manifest = inspection_manifest(enable_patch_analysis=enable_patch_analysis)
    return {"amu_version": __version__, "version": __version__, "distribution_version": distribution_version, "python_version": platform.python_version(),
            "executable": sys.executable, "module": str(Path(__file__).parent / "mcp_server.py"),
            "build_sha256": build["sha256"], "build": build, "manifest_version": manifest["manifest_version"],
            "manifest_sha256": manifest["sha256"]}


def public_identity(*, enable_patch_analysis: bool = False) -> dict:
    """Initialize metadata excludes executable paths and repository authority."""
    runtime = runtime_identity(enable_patch_analysis=enable_patch_analysis)
    return {key: runtime[key] for key in ("amu_version", "distribution_version", "build_sha256", "manifest_version", "manifest_sha256")}


def server_version(identity: dict) -> str:
    """Public protocol version ties advertised contract to payload identity."""
    return (f"amu/{identity['amu_version']} manifest/{identity['manifest_version']}:{identity['manifest_sha256']}"
            f" build/{identity['build_sha256'] or 'unavailable'}")
