"""Live graph mapping without repository selection or presentation dependencies.

Every entry point shares exact-target coverage, independent source evidence,
pre-edit capture and persisted map identity through this application service.
A useful inspection result can remain unapprovable; missing evidence is never
silently converted into proof that a target has no relationships.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from amu import (
    entire,
    freshness,
    graph_readiness,
    graph_refresh,
    source_evidence,
    state,
    state_io,
    state_schema,
    workspace,
)
from amu.classify import build_map
from amu.graph import capabilities

CHANGE_CLASSES = frozenset({"signature", "remove", "rename", "move", "body"})


class GraphDataError(RuntimeError):
    """A required graph query completed without usable evidence."""


class MapInputError(ValueError):
    """An expected input failure, independent of a command-line transport."""

    def __init__(self, message: str):
        super().__init__(message)
        self.exit_code = 2
        self.payload = {"error": message, "exit": self.exit_code}


@dataclass(frozen=True)
class MapRequest:
    """An explicit file or symbol map request, with a selected traversal depth."""

    file: str | None = None
    symbol: str | None = None
    depth: int = 2
    change: str = "signature"


def _repo(repo: str) -> str:
    if not isinstance(repo, str) or not repo.strip() or not Path(repo).is_absolute():
        raise MapInputError("repo must be an explicit absolute directory")
    try:
        root = Path(repo).resolve(strict=True)
        if not root.is_dir():
            raise MapInputError("repo must be an existing directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, MapInputError):
            raise
        raise MapInputError("repo must be an existing directory") from exc
    return str(root)


def _target_path(repo: str, value: str) -> None:
    path = Path(value)
    if not value.strip() or "\x00" in value or path.is_absolute() or ".." in path.parts:
        raise MapInputError("map target must be a repository-relative path")
    try:
        (Path(repo) / path).resolve().relative_to(Path(repo))
    except (OSError, RuntimeError, ValueError) as exc:
        raise MapInputError("map target must be a repository-relative path") from exc


def _validate(repo: str, request: MapRequest, progress: Callable[[str], None] | None) -> None:
    if not isinstance(request, MapRequest):
        raise MapInputError("request must be a MapRequest")
    for name in ("file", "symbol"):
        value = getattr(request, name)
        if value is not None and (not isinstance(value, str) or not value.strip() or "\x00" in value):
            raise MapInputError(f"--{name} must be a nonempty string")
    if (request.file is None) == (request.symbol is None):
        raise MapInputError("provide exactly one of --file or --symbol path#name")
    if type(request.depth) is not int or request.depth < 1:
        raise MapInputError("--depth must be an integer of at least 1")
    if not isinstance(request.change, str) or request.change not in CHANGE_CLASSES:
        raise MapInputError(f"invalid --change {request.change!r}; choose one of: {', '.join(sorted(CHANGE_CLASSES))}")
    if progress is not None and not callable(progress):
        raise MapInputError("progress must be a callable or None")
    if request.file is not None:
        _target_path(repo, request.file)
    elif "#" in request.symbol:
        path, separator, name = request.symbol.partition("#")
        if not separator or not path or not name.strip() or "#" in name:
            raise MapInputError("--symbol must use the form path#name")
        _target_path(repo, path)
    elif "/" in request.symbol or "\\" in request.symbol:
        # Bare legacy symbol names remain useful for inspection. A path-like
        # value without a symbol cannot be an unqualified symbol identity.
        raise MapInputError("--symbol must use the form path#name")


def impact_coverage(root_file: str, impacts: dict[str, object], requested_depth: int) -> dict[str, dict]:
    """Keep exact provider pagination evidence for future edge comparisons."""
    return {
        f"{root_file}#{symbol}": graph_refresh.coverage_from_impact(
            f"{root_file}#{symbol}", requested_depth, raw if isinstance(raw, dict) else None,
        )
        for symbol, raw in impacts.items()
    }


def worktree_fingerprint(repo: str, stage: str) -> dict:
    """Capture editable state or fail before persisting graph claims."""
    fingerprint = graph_refresh.graph_capture(repo)
    if fingerprint.get("error") or not fingerprint.get("digest"):
        detail = fingerprint.get("error") or "no digest"
        raise GraphDataError(f"could not fingerprint worktree {stage} graph map: {detail}")
    # Clean worktrees can change during a checkout or commit. Bind HEAD even
    # for captures whose dirty-file fingerprints happen to be identical.
    return {**fingerprint, "head": state.git_head(repo)}


def _nav_push(repo: str, target: str) -> None:
    def push(nav):
        nav["stack"] = (nav["stack"] + [{"kind": "map", "target": target, "at": state.now_iso()}])[-20:]
        return nav
    state.update_json("nav.json", repo, {"stack": []}, push)


def _edge_count(impact: object) -> int:
    """Presentation-only progress must not reinterpret malformed graph data."""
    if not isinstance(impact, dict):
        return 0
    total = 0
    for section in ("callers", "callees", "type_consumers", "data_flows"):
        row = impact.get(section)
        count = row.get("total") if isinstance(row, dict) else None
        if type(count) is int and count >= 0:
            total += count
    return total


def _inspection_setting(repo: str, name: str, default: dict) -> dict:
    """Read existing settings without creating AMU state or lock files."""
    root = Path(repo) / ".amu"
    try:
        value = state_io.read(root / name, default, managed_root=root, max_bytes=2 * 1024 * 1024)
        if not isinstance(value, dict):
            raise ValueError("settings must be a JSON object")
        version = value.get("schema_version", 0)
        if type(version) is not int or version not in (0, 1):
            raise ValueError("unsupported settings version")
        state_schema.check(name, value)
    except (state_io.StateIOError, state_schema.InvalidState, ValueError, TypeError) as exc:
        raise state.StateReadError(f"{name}: inspection could not read valid settings") from exc
    return value


def _collect(repo: str, request: MapRequest, progress: Callable[[str], None] | None, *, readonly: bool) -> dict:
    """Share the live evidence collection independently of map persistence."""
    repo = _repo(repo)
    _validate(repo, request, progress)
    file, symbol, depth, change = request.file, request.symbol, request.depth, request.change
    # Read settings before freshness captures too: oversized/corrupt settings
    # must fail without the fingerprint's configuration reader loading them.
    cfg = _inspection_setting(repo, "config.json", {"test_cmd": "pytest -q"}) if readonly else None
    feature_map = _inspection_setting(repo, "feature_map.json", {}) if readonly else None
    before_worktree = worktree_fingerprint(repo, "before")
    if not readonly:
        cfg = state.config(repo)
    # A stable dirty worktree is inspectable, but cannot prove that mapping
    # happened before the edit that a contract will eventually authorize.
    pre_edit_graph_files = sorted(
        path for path in before_worktree.get("files", [])
        if isinstance(path, str) and freshness.is_code_path(path)
    )
    caps = capabilities(repo)
    symbols, symbols_error = entire.symbols_result(repo)
    if symbols_error:
        raise GraphDataError(f"entire graph symbols failed: {symbols_error}")
    if symbol:
        path, _, name = symbol.rpartition("#")
        root_file, names = (path or "?"), [name]
    else:
        root_file = file
        names = [
            entry.get("qualified_name") or entry["name"]
            for entry in symbols
            if entry.get("record_type", "symbol") == "symbol"
            and entry.get("file_path") == file
            and entry.get("kind") in ("function", "class")
            and not entry.get("name", "_").startswith("_")
        ]
    impacts: dict[str, dict] = {}
    if progress is not None:
        progress("resolving…")
    for name in names:
        # Map the editable worktree rather than the potentially incomplete
        # cached index. Exact focus and pagination are checked independently.
        impacts[name] = entire.impact_json(repo, name, depth, file=(root_file if root_file != "?" else None), head=False)
        if progress is not None:
            progress(f"resolving callers of {name} · {_edge_count(impacts[name])} edges")
    if symbol and root_file == "?":
        raw = impacts[names[0]]
        focus = raw.get("focus") if isinstance(raw, dict) else None
        inferred = focus.get("file_path") if isinstance(focus, dict) else None
        if inferred is None or inferred == "":
            root_file = "?"
        else:
            if not isinstance(inferred, str):
                raise GraphDataError("graph returned an unsafe inferred target path; no map was saved")
            try:
                _target_path(repo, inferred)
            except MapInputError as exc:
                raise GraphDataError("graph returned an unsafe inferred target path; no map was saved") from exc
            root_file = inferred
    try:
        impacts, source_contradictions, source_uncertainties = source_evidence.filter_python_call_evidence(repo, impacts)
    except RecursionError:
        if not readonly:
            raise
        # A hostile or deeply nested neighboring file can exhaust the AST
        # visitor even when the selected target was safely parsed. Retain
        # candidate graph rows and explicitly abstain from source proof.
        source_contradictions = []
        source_uncertainties = [{
            "target": f"{root_file}#{name}", "source_path": root_file, "section": "callers/callees",
            "endpoint": "*", "evidence": "source:python-ast:resource-limit",
        } for name in names]
    live_evidence = graph_readiness.with_source_call_evidence(
        graph_readiness.live_map_evidence(symbols, impacts, root_file=root_file),
        source_contradictions, source_uncertainties,
    )
    if caps.get("error"):
        live_evidence.update(
            approvable=False,
            capabilities_error={key: caps.get(key) for key in ("error", "rc", "error_type")},
            reasons=[*live_evidence.get("reasons", []), f"graph capabilities unavailable: {caps['error']}"],
        )
    # Unresolved focus is an unknown, not evidence for an isolated target.
    for unresolved_symbol in live_evidence["unresolved_focus"]:
        raw = impacts.get(unresolved_symbol)
        if isinstance(raw, dict) and "error" not in raw:
            impacts[unresolved_symbol] = {**raw, "error": "live graph did not resolve the requested focus"}
    edge_baselines, baseline_errors = graph_refresh.edge_baselines_from_impacts(root_file, impacts, depth)
    if baseline_errors:
        live_evidence = {
            **live_evidence,
            "approvable": False,
            "edge_baseline_errors": baseline_errors,
            "reasons": [
                *list(live_evidence.get("reasons") or []),
                *[f"complete semantic baseline unavailable for {target}: {reason}"
                  for target, reason in sorted(baseline_errors.items())],
            ],
        }
    result = build_map(root_file, names, impacts, change,
                       feature_map if readonly else state.read_json("feature_map.json", repo, default={}),
                       caps, depth, cfg.get("test_cmd", "pytest"))
    if source_contradictions:
        result["resolution"]["source_contradictions"] = source_contradictions
    if source_uncertainties:
        result["resolution"]["source_uncertainties"] = source_uncertainties
    result["root"]["internal"] = sum(
        1 for entry in symbols
        if entry.get("record_type", "symbol") == "symbol"
        and entry.get("file_path") == root_file
        and str(entry.get("name") or "").startswith("_")
    )
    coverage = impact_coverage(root_file, impacts, depth)
    after_worktree = worktree_fingerprint(repo, "after")
    if before_worktree["head"] != after_worktree["head"]:
        raise GraphDataError("Git revision changed while graph map was running; no map was saved")
    if not graph_refresh.relation_capture_matches(before_worktree, after_worktree):
        raise GraphDataError("worktree changed while graph map was running; no map was saved")
    worktree_capture = {"stable": True, "before": before_worktree, "after": after_worktree,
                        "pre_edit_graph_files": pre_edit_graph_files}
    result["map_hash"] = state.stable_hash({
        "root": result["root"],
        "nodes": [(node["path"], node["symbol"], node["confidence"], node["evidence"]) for node in result["nodes"]],
        "ripples": result["ripples"], "resolution": result["resolution"], "change": change, "depth": depth,
        "impact_coverage": coverage, "edge_baselines": edge_baselines, "worktree": worktree_capture,
    })
    result["graph_evidence"] = {
        **live_evidence, "impact_coverage": coverage, "edge_baselines": edge_baselines,
        "edge_baseline_errors": baseline_errors, "worktree": worktree_capture,
        "baseline_ready": not pre_edit_graph_files, "pre_edit_graph_files": pre_edit_graph_files,
        "map_hash": result["map_hash"], "sha": after_worktree["head"], "at": state.now_iso(),
    }
    result["resolution"]["graph_evidence"] = {
        "approvable": live_evidence["approvable"], "reasons": live_evidence["reasons"],
        "semantic_edges": live_evidence["semantic_edges"],
    }
    state.validate("map", result)
    if not readonly:
        state.write_json("map.json", result, repo)
        _nav_push(repo, f"{root_file}#{names[0]}" if names else root_file)
        workspace.touch(repo, last_map=root_file)
    return result


def inspect(repo: str, request: MapRequest, *, progress: Callable[[str], None] | None = None) -> dict:
    """Collect the current source-derived graph without saving AMU state.

    The graph provider may maintain its own local index/cache. No map,
    navigation history, AMU directory or workspace registration is created.
    """
    return _collect(repo, request, progress, readonly=True)


def execute(repo: str, request: MapRequest, *, progress: Callable[[str], None] | None = None) -> dict:
    """Build and persist a stable live map, keeping approval evidence explicit."""
    return _collect(repo, request, progress, readonly=False)
