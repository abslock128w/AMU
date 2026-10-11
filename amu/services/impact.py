"""Read-only, live dependency context for a proposed source change.

The proposal is intent, not executed code. Source-derived graph relationships
are queried anew on each request and do not prove that a consumer will break.
"""

from __future__ import annotations

import ast
import hashlib
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from amu import graph_refresh, state
from amu.services import impact_report, mapping

MAX_DEPTH = 3
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PROPOSED_CHANGE_BYTES = 8192
MAX_FILE_BYTES = 4096
MAX_PACKET_BYTES = 64 * 1024
MAX_ROWS = 100
MAX_AST_NODES = 50_000
MAX_AST_DEPTH = 200
MAX_SYMBOL_BYTES = 1024


class ImpactInputError(ValueError):
    """Invalid or unsafe impact input, independent of CLI/MCP transports."""

    def __init__(self, message: str):
        super().__init__(message)
        self.exit_code = 2
        self.payload = {"error": message, "exit": self.exit_code}


@dataclass(frozen=True)
class ImpactRequest:
    file: str
    line: int
    proposed_change: str
    depth: int = 2
    change_kind: str = "body"
    section: str = "impact"
    cursor: str | None = None
    page_size: int = 50


def _bytes(value: str, field: str, maximum: int) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ImpactInputError(f"{field} must be valid UTF-8 text") from exc
    if size > maximum:
        raise ImpactInputError(f"{field} exceeds its {maximum}-byte limit")


def validate_request(request: ImpactRequest) -> None:
    """Validate bounded scalar fields without filesystem or provider access."""
    if not isinstance(request, ImpactRequest):
        raise ImpactInputError("request must be an ImpactRequest")
    if not isinstance(request.file, str) or not request.file.strip():
        raise ImpactInputError("file must be a nonempty repository-relative path")
    _bytes(request.file, "file", MAX_FILE_BYTES)
    path = PurePosixPath(request.file)
    if (path.is_absolute() or path.as_posix() != request.file or path == PurePosixPath(".")
            or any(part in {"..", ".git", ".amu"} for part in path.parts)
            or "\\" in request.file or "#" in request.file
            or any(ord(char) < 32 or ord(char) == 127 for char in request.file)):
        raise ImpactInputError("file must be a normalized repository-relative source path")
    if type(request.line) is not int or request.line < 1:
        raise ImpactInputError("line must be an integer of at least 1")
    if type(request.depth) is not int or not 1 <= request.depth <= MAX_DEPTH:
        raise ImpactInputError(f"depth must be an integer from 1 to {MAX_DEPTH}")
    if not isinstance(request.proposed_change, str) or not request.proposed_change.strip():
        raise ImpactInputError("proposed_change must be nonempty text describing the intended change")
    _bytes(request.proposed_change, "proposed_change", MAX_PROPOSED_CHANGE_BYTES)
    if any((ord(char) < 32 and char not in "\n\r\t") or ord(char) == 127 for char in request.proposed_change):
        raise ImpactInputError("proposed_change contains unsupported control characters")
    if not isinstance(request.change_kind, str) or request.change_kind not in mapping.CHANGE_CLASSES:
        raise ImpactInputError("change_kind must be body, signature, remove, rename or move")
    if not isinstance(request.section, str) or request.section not in impact_report.SECTIONS:
        raise ImpactInputError("section must be impact, source_contradictions, source_uncertainties, dynamic_boundaries, coverage or reasons")
    if type(request.page_size) is not int or not 1 <= request.page_size <= MAX_ROWS:
        raise ImpactInputError(f"page_size must be an integer from 1 to {MAX_ROWS}")
    if request.cursor is not None:
        if not isinstance(request.cursor, str) or not request.cursor:
            raise ImpactInputError("cursor must be nonempty bounded text or null")
        _bytes(request.cursor, "cursor", impact_report.MAX_CURSOR_BYTES)
        try:
            impact_report.decode_cursor(request.cursor)
        except impact_report.ReportError as exc:
            raise ImpactInputError(str(exc)) from exc


def _repo(repo: str) -> Path:
    if not isinstance(repo, str) or not repo.strip() or "\x00" in repo or not Path(repo).is_absolute():
        raise ImpactInputError("repo must be an explicit absolute directory")
    try:
        root = Path(repo).resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor):
            raise ImpactInputError("repo must be an existing project directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ImpactInputError):
            raise
        raise ImpactInputError("repo must be an existing project directory") from exc
    return root


def _identity(info: os.stat_result) -> tuple:
    return tuple(getattr(info, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode"))


def _source(root: Path, relative: str, *, read: bool = True) -> tuple[bytes, tuple]:
    """Pin every path component, refuse symlinks/FIFOs and bound the source read."""
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        raise ImpactInputError("secure source inspection is unavailable on this host")
    descriptors, links = [], []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        root_identity = _identity(os.fstat(parent))[:2]
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            links.append((parent, part, _identity(os.fstat(child))[:2]))
            parent = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptors.append(descriptor)
        original = os.fstat(descriptor)
        if not stat.S_ISREG(original.st_mode):
            raise ImpactInputError("file must be a regular source file")
        if original.st_size > MAX_SOURCE_BYTES:
            raise ImpactInputError(f"source exceeds its {MAX_SOURCE_BYTES}-byte limit")
        payload = bytearray()
        if read:
            while len(payload) <= MAX_SOURCE_BYTES:
                chunk = os.read(descriptor, min(65536, MAX_SOURCE_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > MAX_SOURCE_BYTES:
                raise ImpactInputError(f"source exceeds its {MAX_SOURCE_BYTES}-byte limit")
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if _identity(original) != _identity(os.fstat(descriptor)) or _identity(original) != _identity(current):
            raise ImpactInputError("source changed while it was being read; retry")
        for directory, name, identity in links:
            child = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISDIR(child.st_mode) or _identity(child)[:2] != identity:
                raise ImpactInputError("source directory changed while it was being read; retry")
        if _identity(root.lstat())[:2] != root_identity:
            raise ImpactInputError("repository changed while source was being read; retry")
        return bytes(payload), _identity(original)
    except (OSError, RuntimeError) as exc:
        raise ImpactInputError("file must be an existing regular source file inside repo without symbolic links") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _declarations(tree: ast.AST) -> list[tuple[str, ast.AST, int, int, int]]:
    declarations = []
    pending = [(tree, "", 0)]
    visited = 0
    while pending:
        node, prefix, depth = pending.pop()
        visited += 1
        if visited > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise ImpactInputError("source AST exceeds the bounded inspection limit")
        nested = prefix
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested = f"{prefix}.{node.name}" if prefix else node.name
            start = min([node.lineno, *[decorator.lineno for decorator in node.decorator_list]])
            declarations.append((nested, node, start, node.end_lineno, nested.count(".")))
        pending.extend((child, nested, depth + 1) for child in ast.iter_child_nodes(node))
    return declarations


def _boundaries(node: ast.AST) -> list[dict]:
    result = []
    for child in ast.walk(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            names = [name.name for name in child.names]
            if any(name == "*" for name in names):
                result.append({"kind": "star-import", "line": child.lineno, "reason": "imported names need export resolution"})
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Name) and function.id in {"getattr", "setattr", "eval", "exec", "__import__", "globals", "locals"}:
            result.append({"kind": "dynamic-call", "line": child.lineno, "reason": f"{function.id} may change or resolve runtime targets"})
        elif isinstance(function, ast.Attribute):
            if function.attr in {"import_module", "load_module"}:
                reason, kind = "dynamic import needs runtime resolution", "dynamic-import"
            else:
                reason, kind = "attribute call may depend on runtime object dispatch or a native boundary", "attribute-dispatch"
            result.append({"kind": kind, "line": child.lineno, "reason": reason})
        elif not isinstance(function, ast.Name):
            result.append({"kind": "indirect-call", "line": child.lineno, "reason": "callable is computed at runtime"})
    return sorted(result, key=lambda row: (row["line"], row["kind"]))


_LIMITATIONS = [
    "The proposed change is intent only; it has not been applied or executed.",
    "Rows describe current source-derived dependencies, not proof that a listed caller will break.",
    "Static source analysis cannot guarantee runtime impact; reflection, dispatch, generated code, native boundaries and feature flags can hide relationships.",
    "A zero-edge query is not proof that a change is safe. This operation does not call a model, import application modules, apply the proposal or run project tests/builds. Native Git/graph tools are not an OS sandbox.",
    "The graph provider may update its local cache; this operation does not save AMU maps, plans, contracts, history or workspace registrations.",
]


def _packet(request: ImpactRequest, target: dict, *, status: str, reasons: list[str], evidence: dict | None = None,
            rows: list | None = None, boundaries: list | None = None, repo_binding: str = "") -> dict:
    try:
        return impact_report.build_packet(asdict(request), target, repo_binding=repo_binding,
                                          status=status, reasons=reasons, evidence=evidence or {}, rows=rows or [],
                                          boundaries=boundaries or [], limitations=_LIMITATIONS,
                                          max_bytes=MAX_PACKET_BYTES, max_rows=MAX_ROWS)
    except impact_report.ReportError as exc:
        raise ImpactInputError(str(exc)) from exc


def execute(repo: str, request: ImpactRequest) -> dict:
    """Inspect the live relationship graph around a source location, read-only."""
    validate_request(request)
    root = _repo(repo)
    def packet(target, **options):
        return _packet(request, target, repo_binding=str(root), **options)
    target = {"file": request.file, "symbol": None, "kind": None, "range": None,
              "resolution": "unsupported", "source_sha256": None}
    if PurePosixPath(request.file).suffix not in {".py", ".pyi"}:
        _source(root, request.file, read=False)
        return packet(target, status="unsupported", reasons=["source-location analysis currently supports Python declarations only; file contents were not read"])
    source, identity = _source(root, request.file)
    try:
        text = source.decode("utf-8")
        if "\x00" in text:
            raise ValueError("NUL in source")
        tree = ast.parse(text, filename=request.file)
    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ImpactInputError("target source is not safely parseable UTF-8 Python") from exc
    if request.line > len(text.splitlines()):
        raise ImpactInputError("line is past the end of the source file")
    target["source_sha256"] = hashlib.sha256(source).hexdigest()
    declarations = _declarations(tree)
    matches = [row for row in declarations if row[2] <= request.line <= row[3]]
    if not matches:
        target["resolution"] = "module-scope"
        return packet(target, status="unsupported", reasons=["line is outside a supported function or class; module-level changes require broader analysis"])
    name, node, start, end, _ = max(matches, key=lambda row: (row[4], -(row[3] - row[2])))
    _bytes(name, "containing symbol identity", MAX_SYMBOL_BYTES)
    target.update(symbol=f"{request.file}#{name}", qualified_name=name,
                  kind="class" if isinstance(node, ast.ClassDef) else "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                  range={"start_line": start, "definition_line": node.lineno, "end_line": end}, resolution="resolved")
    if sum(row[0] == name for row in declarations) != 1:
        target["resolution"] = "ambiguous"
        return packet(target, status="uncertain", reasons=["repeated declarations share this symbol identity; an exact graph target cannot be assumed"])
    boundaries = _boundaries(node)
    try:
        before = mapping.worktree_fingerprint(str(root), "before source inspection")
        # The graph uses current editable source on every request (head=False).
        mapped = mapping.inspect(str(root), mapping.MapRequest(symbol=target["symbol"], depth=request.depth, change=request.change_kind))
        after = mapping.worktree_fingerprint(str(root), "after source inspection")
        current, current_identity = _source(root, request.file)
        if (identity != current_identity or current != source or before["head"] != after["head"]
                or not graph_refresh.relation_capture_matches(before, after)):
            raise mapping.GraphDataError("source, worktree or Git revision changed during impact inspection; retry")
        graph = mapped["graph_evidence"]
        reasons = list(graph.get("reasons", []))
        if graph.get("unverified_python_calls"):
            reasons.append("some call identities remain unverified in source")
        if boundaries:
            reasons.append("target source contains dynamic or runtime-dispatch boundaries")
        if after["head"] == "no-git":
            reasons.append("no committed Git revision is available")
        coverage_complete = bool(graph.get("impact_coverage") and not graph.get("edge_baseline_errors")
                                 and not graph.get("unresolved_focus") and graph.get("approvable") is True)
        evidence = {
            "live_worktree": True, "provider_reported_coverage_complete": coverage_complete,
            "map_hash": mapped["map_hash"], "sha": graph.get("sha"), "at": graph.get("at"),
            "impact_coverage": graph.get("impact_coverage"), "edge_baseline_errors": graph.get("edge_baseline_errors"),
            "completeness_levels": graph.get("completeness_levels"), "semantic_edges": graph.get("semantic_edges"),
            "isolated_targets": graph.get("isolated_targets"), "capabilities_error": graph.get("capabilities_error"),
            "unresolved_focus": graph.get("unresolved_focus"), "unverified_python_calls": graph.get("unverified_python_calls"),
            "edge_baselines": graph.get("edge_baselines"), "baseline_ready": graph.get("baseline_ready"),
            "pre_edit_graph_files": graph.get("pre_edit_graph_files"),
            "source_contradictions": mapped["resolution"].get("source_contradictions", []),
            "source_uncertainties": mapped["resolution"].get("source_uncertainties", []),
            "worktree": {"stable": True, "before": {"head": before["head"], "relation_digest": graph_refresh.relation_digest(before)},
                         "after": {"head": after["head"], "relation_digest": graph_refresh.relation_digest(after)}},
        }
        return packet(target, status="uncertain" if reasons else "inspectable", reasons=reasons,
                       evidence=evidence, rows=mapped["nodes"], boundaries=boundaries)
    except (mapping.GraphDataError, state.StateReadError, state.SchemaError, RecursionError) as exc:
        return packet(target, status="uncertain", reasons=[str(exc) or "graph evidence could not be verified"],
                       evidence={"live_worktree": True, "provider_reported_coverage_complete": False}, boundaries=boundaries)
