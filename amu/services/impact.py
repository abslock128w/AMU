"""Read-only, live dependency context for a proposed source change.

The proposal is intent, not executed code. Source-derived graph relationships
are queried anew on each request and do not prove that a consumer will break.
"""

from __future__ import annotations

import ast
import codecs
import hashlib
import io
import os
import re
import stat
import tokenize
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


@dataclass(frozen=True)
class PatchRequest:
    patch: str
    base: str = "HEAD"
    depth: int = 1
    include_graph: bool = False
    section: str = "changes"
    cursor: str | None = None
    page_size: int = 20


def validate_patch_request(request: PatchRequest) -> None:
    """Validate the entire diff/cursor envelope before touching the repository."""
    from amu.patch import PatchError, parse_diff

    if not isinstance(request, PatchRequest):
        raise ImpactInputError("request must be a PatchRequest")
    if not isinstance(request.base, str) or not (request.base in {"HEAD", "worktree"} or re.fullmatch(r"[0-9a-f]{40}", request.base)):
        raise ImpactInputError("base must be HEAD, worktree or the full current Git HEAD SHA")
    if type(request.depth) is not int or not 1 <= request.depth <= MAX_DEPTH:
        raise ImpactInputError(f"depth must be an integer from 1 to {MAX_DEPTH}")
    if type(request.include_graph) is not bool:
        raise ImpactInputError("include_graph must be a boolean")
    if not isinstance(request.section, str) or request.section not in impact_report.PATCH_SECTIONS:
        raise ImpactInputError("section must be changes, findings, state_dependencies, recommended_checks or uncertainties")
    if type(request.page_size) is not int or not 1 <= request.page_size <= MAX_ROWS:
        raise ImpactInputError(f"page_size must be an integer from 1 to {MAX_ROWS}")
    if request.cursor is not None:
        if not isinstance(request.cursor, str) or not request.cursor:
            raise ImpactInputError("cursor must be nonempty bounded text or null")
        try:
            impact_report.decode_cursor(request.cursor, sections=impact_report.PATCH_SECTIONS)
        except impact_report.ReportError as exc:
            raise ImpactInputError(str(exc)) from exc
    try:
        parse_diff(request.patch)
    except PatchError as exc:
        raise ImpactInputError(str(exc)) from exc


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


def _direct_parameter_calls(node: ast.AST) -> set[ast.Call]:
    """Match direct calls to named parameters within each named function body.

    This syntax check covers positional-only, positional and keyword-only
    parameters of ``def`` and ``async def`` declarations. Each nested named
    function is checked against its own parameters. Calls in nested classes,
    lambdas, comprehensions, decorators, defaults and annotations are not
    attributed to an enclosing parameter. Bindings and rebinding are not
    resolved.
    """
    calls: set[ast.Call] = set()
    nested_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                     ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    for scope in ast.walk(node):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = {arg.arg for arg in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs)}
        if not parameters:
            continue
        pending = list(scope.body)
        while pending:
            child = pending.pop()
            if isinstance(child, nested_scopes):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id in parameters:
                calls.add(child)
            pending.extend(ast.iter_child_nodes(child))
    return calls


def _boundaries(node: ast.AST) -> list[dict]:
    result = []
    parameter_calls = _direct_parameter_calls(node)
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
        elif child in parameter_calls:
            result.append({"kind": "indirect-call", "line": child.lineno,
                           "reason": "direct call by a function-parameter name has an unresolved runtime target; local rebinding is not resolved"})
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
    "Direct calls by positional-only, positional and keyword-only parameter names in def/async def bodies are tagged as unresolved runtime targets, not defects. Rebinding, aliases, forwarding and nested lambda/comprehension calls are not resolved.",
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


MAX_TEST_FILES = 200
MAX_GRAPH_TARGETS = 8
_PATCH_LIMITATIONS = [
    "The diff was applied exactly in memory to live working-tree bytes; HEAD is an anchor, not a claim that those bytes equal the commit.",
    "Observed syntax and established source references are distinct from inferred behavioral risks. No regression or safety is proved by this inspection.",
    "No candidate module was imported, executed or written. Execution authorization is a separate operation.",
    "Test recommendations use direct unshadowed import references in at most 200 Python test files, not executed coverage. Indirect imports, aliases beyond imports, dispatch and descriptors remain unresolved.",
    "Optional provider relationships describe the baseline source only. The graph provider may update its local cache; native tools are not an OS sandbox.",
    "Retrieve all relevant sections and complete fragments from the same report before relying on detailed evidence.",
]


def _absent_source(root: Path, relative: str) -> None:
    """Pin existing parent directories and establish an absent addition target."""
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
        try:
            os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ImpactInputError("addition target already exists, including a symbolic link")
        for directory, name, identity in links:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or _identity(current)[:2] != identity:
                raise ImpactInputError("source directory changed during inspection; retry")
        if _identity(root.lstat())[:2] != root_identity:
            raise ImpactInputError("repository changed during inspection; retry")
    except OSError as exc:
        raise ImpactInputError("addition requires existing nonsymlink parent directories inside repo") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _test_inventory(root: Path) -> tuple[list[str], bool]:
    """Bound directory traversal, skipping managed/build and symlink trees."""
    from amu.plan import _is_test_path

    pending, paths, visited = [([], _identity(root.stat())[:2])], [], 0
    ignored = {".git", ".amu", ".entire", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
    while pending:
        parts, expected_identity = pending.pop()
        descriptors = []
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(descriptor)
            for part in parts:
                descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                descriptors.append(descriptor)
            if _identity(os.fstat(descriptor))[:2] != expected_identity:
                raise ImpactInputError("test directory changed during inspection; retry")
            entries = []
            with os.scandir(descriptor) as scan:
                for entry in scan:
                    visited += 1
                    if visited > 10000:
                        return sorted(paths), False
                    entries.append(entry)
            entries.sort(key=lambda entry: entry.name)
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".") and entry.name not in ignored:
                    pending.append(([*parts, entry.name], _identity(entry.stat(follow_symlinks=False))[:2]))
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".py"):
                    relative = "/".join([*parts, entry.name])
                    if _is_test_path(relative):
                        paths.append(relative)
                        if len(paths) > MAX_TEST_FILES:
                            return sorted(paths[:MAX_TEST_FILES]), False
        except OSError as exc:
            raise ImpactInputError("test inventory changed or became unreadable during inspection") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    return sorted(paths), True


def _python_tree(text: str, path: str) -> ast.AST:
    try:
        if "\x00" in text:
            raise ValueError("NUL in source")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(text.encode("utf-8")).readline)
        if codecs.lookup(encoding).name != "utf-8":
            raise ValueError("only declared UTF-8 source is supported")
        tree = ast.parse(text, filename=path)
        _declarations(tree)
        return tree
    except (SyntaxError, ValueError, LookupError, RecursionError, MemoryError) as exc:
        raise ImpactInputError(f"{path} is not safely parseable bounded UTF-8 Python") from exc


def execute_patch(repo: str, request: PatchRequest) -> dict:
    """Inspect a concrete diff and relevant existing test syntax, without execution."""
    from amu.change_analysis import analyze_python_change
    from amu.patch import MAX_TOTAL_SOURCE_BYTES, PatchError, apply_file, digest, parse_diff
    from amu.plan import patch_test_recommendations

    validate_patch_request(request)
    root = _repo(repo)
    root_identity = _identity(root.stat())[:2]
    head = state.git_head(str(root))
    if request.base != "worktree" and (head == "no-git" or request.base not in {"HEAD", head}):
        raise ImpactInputError("base does not identify the current Git HEAD; use worktree for a source-only baseline")
    files = parse_diff(request.patch)
    groups = {section: [] for section in sorted(impact_report.PATCH_SECTIONS)}
    snapshots, file_identities, affected, graph_targets, declarations = {}, [], {}, [], {}
    total_bytes = 0
    for file in files:
        if file.operation == "add":
            _absent_source(root, file.path)
            before, source_identity = None, None
            snapshots[file.path] = (None, None)
        else:
            payload, source_identity = _source(root, file.path)
            total_bytes += len(payload)
            try:
                before = payload.decode("utf-8")
            except UnicodeError as exc:
                raise ImpactInputError(f"{file.path} must be UTF-8 Python source") from exc
            snapshots[file.path] = (payload, source_identity)
        try:
            after = apply_file(file, before)
        except PatchError as exc:
            raise ImpactInputError(str(exc)) from exc
        total_bytes += len(after.encode("utf-8")) if after is not None else 0
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise ImpactInputError("combined baseline and candidate source exceeds its byte limit")
        for text in (before, after):
            if text is not None:
                _python_tree(text, file.path)
        analysis = analyze_python_change(file.path, before, after)
        declarations.update(analysis.get("recommendation_entities", {}))
        changed_ids = {row["id"] for row in analysis["modified_entities"]}
        analysis["state_dependencies"] = [row for row in analysis["state_dependencies"]
                                           if row.get("state_id") in changed_ids or row.get("consumer_id") in changed_ids]
        groups["changes"].extend(analysis["modified_entities"])
        for section in ("findings", "state_dependencies", "uncertainties"):
            groups[section].extend(analysis[section])
        for entity in analysis["modified_entities"]:
            name = entity["qualified_name"]
            affected.setdefault(file.path, set()).add(name)
            if before is not None and entity.get("before_location") and entity["kind"] in {"function", "async_function", "class", "method"}:
                graph_targets.append(entity["id"])
        for consumer in analysis["state_dependencies"]:
            consumer_id = consumer.get("consumer_id", "")
            if "#" in consumer_id:
                path, name = consumer_id.split("#", 1)
                affected.setdefault(path, set()).add(name)
        file_identities.append({"path": file.path, "operation": file.operation,
                                "baseline_sha256": digest(before), "candidate_sha256": digest(after)})
    inventory, complete = _test_inventory(root)
    tests, test_hashes = [], []
    for path in inventory:
        payload, identity = _source(root, path)
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            groups["uncertainties"].append({"reason": "test-source byte budget exhausted; additional tests were not inspected"})
            complete = False
            break
        if path in snapshots and snapshots[path] != (payload, identity):
            raise ImpactInputError("changed test source no longer matches the patch baseline; retry")
        snapshots[path] = (payload, identity)
        sha = hashlib.sha256(payload).hexdigest()
        test_hashes.append({"path": path, "sha256": sha})
        try:
            tree = _python_tree(payload.decode("utf-8"), path)
        except (UnicodeError, ImpactInputError) as exc:
            groups["uncertainties"].append({"path": path, "reason": str(exc)})
            continue
        tests.append({"path": path, "tree": tree, "sha256": sha})
    if not complete:
        groups["uncertainties"].append({"reason": "test inventory is incomplete within bounded directory/file/byte limits"})
    groups["recommended_checks"] = patch_test_recommendations(
        tests, affected, declarations=declarations, state_dependencies=groups["state_dependencies"])
    for finding in groups["findings"]:
        entity_id = finding.get("entity_id")
        finding["recommended_test_ids"] = [test["test_id"] for test in groups["recommended_checks"]
                                           if entity_id in test["entities"]]
        # Keep containing-class candidates visible without placing them in the
        # member's dependency list. The detailed association carries provenance
        # and explicitly unresolved member-path semantics.
        finding["broad_class_test_ids"] = [test["test_id"] for test in groups["recommended_checks"]
                                           if test["test_id"] not in finding["recommended_test_ids"] and any(
                                               a["relationship"] == "broad-class-reference"
                                               and entity_id and entity_id.startswith(a["entity_id"] + ".")
                                               for a in test["associations"])]
        finding["relationships"] = [{**{key: row[key] for key in ("state_id", "consumer_id", "access", "phase", "location")},
                                      "evidence_section": "state_dependencies"} for row in groups["state_dependencies"]
                                     if row.get("state_id") == entity_id or row.get("consumer_id") == entity_id]
    graph_records = []
    if request.include_graph:
        try:
            capture = mapping.worktree_fingerprint(str(root), "before concrete patch graph inspection")
            targets = sorted(set(graph_targets))
            for symbol in targets[:MAX_GRAPH_TARGETS]:
                mapped = mapping.inspect(str(root), mapping.MapRequest(symbol=symbol, depth=request.depth, change="body"))
                graph_records.append({"symbol": symbol, "map_hash": mapped["map_hash"], "baseline_only": True,
                                      "graph_evidence": {key: value for key, value in mapped["graph_evidence"].items() if key != "at"}, "relationships": mapped["nodes"],
                                      "source_resolution": mapped["resolution"]})
            current = mapping.worktree_fingerprint(str(root), "after concrete patch graph inspection")
            if capture["head"] != current["head"] or not graph_refresh.relation_capture_matches(capture, current):
                raise ImpactInputError("worktree changed during graph inspection; retry")
            if len(targets) > MAX_GRAPH_TARGETS:
                groups["uncertainties"].append({"reason": "graph target limit reached; some changed entities have no provider query"})
            if not targets:
                groups["uncertainties"].append({"reason": "changed state/additions have no supported baseline provider declaration target"})
        except (mapping.GraphDataError, state.StateReadError, state.SchemaError, RecursionError) as exc:
            groups["uncertainties"].append({"reason": str(exc) or "graph provider evidence unavailable"})
        groups["uncertainties"].extend({"kind": "baseline-provider-evidence", **record} for record in graph_records)
    else:
        groups["uncertainties"].append({"reason": "provider graph was not requested; relationships are limited to supported source syntax"})
    for finding in groups["findings"]:
        finding["provider_context"] = [{"symbol": record["symbol"], "map_hash": record["map_hash"],
                                        "baseline_only": True, "relationship_count": len(record["relationships"]),
                                        "evidence_section": "uncertainties"}
                                       for record in graph_records if record["symbol"] == finding.get("entity_id")]
    for path, (payload, identity) in snapshots.items():
        if payload is None:
            _absent_source(root, path)
        else:
            current, current_identity = _source(root, path)
            if current != payload or current_identity != identity:
                raise ImpactInputError("source or test evidence changed during inspection; retry")
    final_inventory, final_complete = _test_inventory(root)
    if inventory != final_inventory or complete and not final_complete or state.git_head(str(root)) != head or _identity(root.stat())[:2] != root_identity:
        raise ImpactInputError("repository revision, identity or test inventory changed during inspection; retry")
    identity = {"repository": {"path": str(root), "device": root_identity[0], "inode": root_identity[1]},
                "baseline_kind": "live-worktree", "git_head": None if head == "no-git" else head, "files": file_identities,
                "test_sources": test_hashes, "test_inventory_sha256": hashlib.sha256("\n".join(inventory).encode()).hexdigest(),
                "test_inventory_complete": complete, "graph_evidence_sha256": hashlib.sha256(impact_report._encode(graph_records)).hexdigest(),
                "configuration": {"analysis": "python-concrete-patch-v1", "max_test_files": MAX_TEST_FILES,
                                  "recommendation_evidence": "member-specific-v1",
                                  "max_graph_targets": MAX_GRAPH_TARGETS, "max_ast_nodes": MAX_AST_NODES, "max_ast_depth": MAX_AST_DEPTH}}
    try:
        return impact_report.build_patch_packet(asdict(request), identity=identity, groups=groups,
                                                limitations=_PATCH_LIMITATIONS, max_bytes=MAX_PACKET_BYTES, max_rows=MAX_ROWS)
    except impact_report.ReportError as exc:
        raise ImpactInputError(str(exc)) from exc
