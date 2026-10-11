"""Stable, reviewable semantic-edge deltas for post-edit graph refreshes.

``entire graph impact`` is the authority for a live relationship query.  This
module does *not* try to infer calls from filenames or text: it only normalizes
the resolved semantic edges that the graph already returned so an agent can
compare its pre-edit map with a post-edit query.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amu import entire, freshness, relations, state

_SEMANTIC_SECTIONS = {"callers", "callees", "type_consumers", "data_flows"}
_SECTION_DIRECTIONS = {
    "callers": "in",
    "callees": "out",
    "type_consumers": "in",
    "data_flows": "in",
}
# ``entire graph impact`` groups entries by the traversal used to discover
# them, but the ``DATA_FLOWS`` relation itself can legitimately point either
# toward or away from the focus.  The real provider emits both forms: older
# recorded responses include ``in`` and a live worktree call result emits
# ``out``.  Keep the other sections narrow, and preserve the provider's
# explicit direction for data flow rather than silently rewriting it.
_SECTION_ALLOWED_DIRECTIONS = {
    "callers": frozenset({"in"}),
    "callees": frozenset({"out"}),
    "type_consumers": frozenset({"in"}),
    "data_flows": frozenset({"in", "out"}),
}


@dataclass(frozen=True)
class EdgeEvidence:
    """Comparable semantic-edge evidence, or an explicit reason it is unsafe.

    A missing baseline must never be represented as an empty baseline: that
    would turn every current relationship into a misleading "added" edge.
    Likewise, a truncated or ambiguously focused live response cannot support
    a removal claim.  Consumers should calculate a delta only when
    ``available`` is true.
    """

    available: bool
    edges: list[dict[str, object]]
    reason: str | None = None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _symbol_ref(path: object, symbol: object) -> str | None:
    clean_path = _text(path)
    clean_symbol = _text(symbol)
    if clean_path and clean_symbol:
        return f"{clean_path}#{clean_symbol}"
    return None


def _external_ref(boundary: object) -> str | None:
    """Validate an explicit source-established external boundary, never a fake file path."""
    if not isinstance(boundary, Mapping):
        return None
    identity = _text(boundary.get("identity"))
    path = _text(boundary.get("source_path"))
    digest = _text(boundary.get("source_sha256"))
    locations = boundary.get("locations")
    if (not identity or not all(part.isidentifier() for part in identity.split("."))
            or identity.split(".")[0] not in sys.stdlib_module_names
            or boundary.get("kind") != "python-stdlib-import"
            or boundary.get("evidence") != "source:python-ast:stdlib-import"
            or boundary.get("runtime_verified") is not False
            or not path or Path(path).is_absolute() or ".." in Path(path).parts or Path(path).suffix != ".py"
            or not digest or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(locations, list) or not locations
            or any(not isinstance(site, Mapping) or _positive_int(site.get("line")) is None
                   or _positive_int(site.get("end_line")) is None or site["end_line"] < site["line"]
                   for site in locations)
            or not isinstance(boundary.get("provider_endpoint"), Mapping)):
        return None
    endpoint = boundary["provider_endpoint"]
    if (_text(endpoint.get("qualified_name")) or _text(endpoint.get("name"))) != identity:
        return None
    return "external:python:" + identity


def _target_parts(target: object) -> tuple[str, str] | None:
    """Split the public ``path#qualified_name`` target without guessing."""
    clean_target = _text(target)
    if not clean_target:
        return None
    path, separator, symbol = clean_target.partition("#")
    if not separator or "#" in symbol:
        return None
    clean_path = _text(path)
    clean_symbol = _text(symbol)
    return (clean_path, clean_symbol) if clean_path and clean_symbol else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _sequence(value: object) -> Sequence[object] | None:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else None


def _exact_focus(target: str, focus: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Validate a focus record against an exact public target identity."""
    parts = _target_parts(target)
    if not parts:
        return False, "target must use the exact form path#qualified_name"
    if not isinstance(focus, Mapping):
        return False, "graph returned no resolved focus"
    path, symbol = parts
    focus_path = _text(focus.get("file_path"))
    if focus_path != path:
        return False, f"graph resolved {focus_path or 'no file'}, not {path}"
    qualified_name = _text(focus.get("qualified_name"))
    focus_name = _text(focus.get("name"))
    if qualified_name:
        if qualified_name != symbol:
            return False, f"graph resolved {qualified_name}, not {symbol}"
    elif focus_name != symbol:
        return False, f"graph resolved {focus_name or 'no symbol'}, not {symbol}"
    if not _text(focus.get("id")):
        return False, "graph resolved the target without a stable focus id"
    return True, None


def coverage_from_impact(target: str, depth: int, impact: Mapping[str, Any] | None) -> dict[str, object]:
    """Record per-section total-versus-captured evidence for a map query.

    The map builder records this alongside its graph evidence.  A later
    refresh uses it to reject an old response that was silently truncated by
    the provider's result limit.
    """
    focus = impact.get("focus") if isinstance(impact, Mapping) else None
    # Provider focus IDs are opaque repository-qualified strings.  They are
    # useful to validate a response at capture time, but persisting one leaks
    # repository/branding details into otherwise portable JSON output.  Keep a
    # boolean proving that validation succeeded plus the public path/name
    # identity needed to validate the saved baseline later.
    exact_focus, _focus_reason = _exact_focus(target, focus if isinstance(focus, Mapping) else None)
    sections: dict[str, dict[str, object]] = {}
    for section in _SECTION_DIRECTIONS:
        group = impact.get(section) if isinstance(impact, Mapping) else None
        entries = group.get("entries") if isinstance(group, Mapping) else None
        sections[section] = {
            "total": group.get("total") if isinstance(group, Mapping) else None,
            "captured": len(entries) if isinstance(entries, list) else None,
        }
    return {
        "target": target,
        "depth": depth,
        "focus": {
            "resolved": exact_focus,
            "file_path": _text(focus.get("file_path")) if isinstance(focus, Mapping) else None,
            "qualified_name": _text(focus.get("qualified_name")) if isinstance(focus, Mapping) else None,
            "name": _text(focus.get("name")) if isinstance(focus, Mapping) else None,
        },
        "sections": sections,
    }


def _coverage_reason(target: str, depth: int, graph_evidence: Mapping[str, Any]) -> str | None:
    """Return why saved raw-impact coverage cannot be used as a baseline."""
    coverage = graph_evidence.get("impact_coverage")
    if not isinstance(coverage, Mapping):
        return "saved map has no raw-impact coverage; create a new map before comparing edges"
    record = coverage.get(target)
    if not isinstance(record, Mapping):
        return f"saved map has no raw-impact coverage for the exact target {target}"
    if _text(record.get("target")) != target:
        return f"saved map raw-impact coverage does not identify the exact target {target}"
    covered_depth = _positive_int(record.get("depth"))
    if covered_depth != depth:
        return f"saved map raw-impact coverage was made at depth {covered_depth or 'unknown'}, not {depth}"
    saved_focus = record.get("focus")
    if not isinstance(saved_focus, Mapping) or saved_focus.get("resolved") is not True:
        return "saved map raw-impact coverage did not prove an exact resolved focus"
    # The stored coverage deliberately omits the opaque provider ID; retain
    # exact public target validation without weakening the capture-time check.
    parts = _target_parts(target)
    assert parts is not None  # validated by map_baseline before this call
    expected_path, expected_symbol = parts
    saved_path = _text(saved_focus.get("file_path"))
    saved_qualified_name = _text(saved_focus.get("qualified_name"))
    saved_name = _text(saved_focus.get("name"))
    if saved_path != expected_path:
        return f"saved map raw-impact coverage resolved {saved_path or 'no file'}, not {expected_path}"
    if saved_qualified_name:
        if saved_qualified_name != expected_symbol:
            return f"saved map raw-impact coverage resolved {saved_qualified_name}, not {expected_symbol}"
    elif saved_name != expected_symbol:
        return f"saved map raw-impact coverage resolved {saved_name or 'no symbol'}, not {expected_symbol}"
    sections = record.get("sections")
    if not isinstance(sections, Mapping):
        return "saved map raw-impact coverage has no semantic section counts"
    for section in _SECTION_DIRECTIONS:
        section_coverage = sections.get(section)
        if not isinstance(section_coverage, Mapping):
            return f"saved map raw-impact coverage is missing the {section} section"
        total = section_coverage.get("total")
        captured = section_coverage.get("captured")
        if (
            not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or not isinstance(captured, int)
            or isinstance(captured, bool)
            or captured < 0
        ):
            return f"saved map raw-impact coverage has invalid {section} counts"
        if total != captured:
            return f"saved map {section} response was truncated ({captured} of {total} edges captured)"
    return None


def graph_capture(repo: str) -> dict:
    """Keep full evidence for reporting; versioned relations use the code domain."""
    captured = freshness.capture(repo)
    full = dict(captured["full"])
    out = {**full, "head": captured["head"]}
    if captured["head"] == "no-git" and not full.get("error") and captured.get("config_hash"):
        # An initialized repo with no commit is still inspectable. It cannot
        # acquire a versioned code revision or pass contract approval.
        out["freshness_unavailable"] = "no committed Git revision; inspection uses full worktree evidence"
        return out
    if captured.get("schema_version") == 1:
        out["freshness"] = captured
        if not freshness.valid_capture(captured):
            out["error"] = captured.get("error") or "invalid versioned freshness capture"
    return out


def snapshot_hash(repo: str) -> str | None:
    path = Path(repo) / ".amu" / "snapshot.ndjson"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > entire.MAX_SNAPSHOT_BYTES:
                return None
            digest = hashlib.sha256()
            read_bytes = 0
            while block := stream.read(131072):
                read_bytes += len(block)
                if read_bytes > entire.MAX_SNAPSHOT_BYTES:
                    return None
                digest.update(block)
            after = os.fstat(stream.fileno())
            if any(getattr(before, k) != getattr(after, k) for k in ("st_size", "st_mtime_ns", "st_ctime_ns")):
                return None
            return digest.hexdigest()
    except OSError:
        return None


def relation_digest(capture: Mapping[str, Any]) -> str | None:
    if "freshness" not in capture:
        return _text(capture.get("digest")) if not capture.get("error") else None
    domains = capture.get("freshness")
    if not freshness.valid_capture(domains):
        return None
    if capture.get("head") is not None and capture["head"] != domains["head"]:
        return None
    return domains["code"]["digest"]


def relation_capture_matches(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    if "freshness" not in before:
        # Old captures retain their full-worktree rule; do not upgrade them.
        return bool(_text(before.get("digest")) and not before.get("error")
                    and not after.get("error") and before["digest"] == after.get("digest"))
    old, new = before.get("freshness"), after.get("freshness")
    return bool(freshness.valid_capture(old) and freshness.valid_capture(new)
                and relation_digest(before) and relation_digest(after)
                and old["head"] == new["head"] and old["config_hash"] == new["config_hash"]
                and old["code"]["digest"] == new["code"]["digest"])


def map_worktree_capture_reason(graph_evidence: Mapping[str, Any]) -> str | None:
    """Return why a saved map lacks a stable live-worktree capture.

    A map is only a reliable pre-edit baseline when the repository did not
    change while the graph and the independent source checks were collected.
    The capture deliberately compares both the content-sensitive worktree
    fingerprint and the Git revision: a commit or checkout can change a clean
    worktree without changing the dirty-file fingerprint.
    """
    capture = graph_evidence.get("worktree")
    if not isinstance(capture, Mapping):
        return "map has no stable worktree capture"
    if capture.get("stable") is not True:
        return "map worktree capture is not marked stable"
    before = capture.get("before")
    after = capture.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return "map worktree capture is malformed"
    if before.get("error") or after.get("error"):
        return "map worktree capture recorded an error"
    before_digest = relation_digest(before)
    after_digest = relation_digest(after)
    before_head = _text(before.get("head"))
    after_head = _text(after.get("head"))
    if not before_digest or not after_digest or not before_head or not after_head:
        return "map worktree capture is malformed"
    if ("freshness" in before) != ("freshness" in after):
        return "map worktree capture mixes incompatible freshness versions"
    if not relation_capture_matches(before, after):
        return "map worktree capture spans different worktree states"
    if before_head != after_head:
        return "map worktree capture spans different Git revisions"
    pre_edit_paths = capture.get("pre_edit_graph_files")
    if not isinstance(pre_edit_paths, list) or any(not _text(path) for path in pre_edit_paths):
        return "map has no pre-edit graph-scope evidence"
    if pre_edit_paths:
        return "map began with graph-relevant uncommitted changes"
    return None


def map_current_capture_reason(repo: str, graph_evidence: Mapping[str, Any]) -> str | None:
    """Return why a stable map is no longer the repository's current baseline.

    ``map_worktree_capture_reason`` proves only that the repository stayed put
    while AMU collected a graph.  Before approving that graph into a contract,
    it must also still name the exact current commit and editable worktree.
    Otherwise an intervening commit can add a consumer that the saved baseline
    never checked.
    """
    captured_reason = map_worktree_capture_reason(graph_evidence)
    if captured_reason:
        return captured_reason
    capture = graph_evidence["worktree"]
    assert isinstance(capture, Mapping)  # narrowed by map_worktree_capture_reason
    after = capture["after"]
    assert isinstance(after, Mapping)  # narrowed by map_worktree_capture_reason
    captured_digest = relation_digest(after)
    captured_head = _text(after.get("head"))
    assert captured_digest and captured_head  # narrowed by map_worktree_capture_reason
    if captured_head == "no-git":
        return "saved map has no committed Git revision; commit a baseline before approving"

    head_before = state.git_head(repo)
    current = graph_capture(repo)
    head_after = state.git_head(repo)
    if current.get("error") or not _text(current.get("digest")):
        return f"could not fingerprint the current worktree: {current.get('error') or 'no digest'}"
    if head_before != head_after:
        return "Git revision changed while verifying the saved map; re-run amu map"
    if head_after != captured_head:
        return "saved map is for a different Git revision than the current repository"
    if not relation_capture_matches(after, current):
        return "saved map is for a different editable worktree than the current repository"
    return None


def _edge_key(edge: Mapping[str, object]) -> tuple[str, str, str, str, int]:
    return (
        str(edge["target"]),
        str(edge["related"]),
        str(edge["relation"]),
        str(edge["direction"]),
        int(edge["depth"]),
    )


def _sorted(edges: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    # One normalized record per semantic relationship.  A graph can emit the
    # same relation through multiple internal paths; the target/related pair,
    # relation, direction, and hop count are the useful review boundary here.
    unique = {_edge_key(edge): dict(edge) for edge in edges}
    return [unique[key] for key in sorted(unique)]


def from_graph_payload(target: str, graph: Mapping[str, Any]) -> list[dict[str, object]]:
    """Normalize resolved semantic edges from :mod:`amu.graphview` output."""
    nodes = {
        _text(node.get("id")): node
        for node in graph.get("nodes", [])
        if isinstance(node, Mapping) and _text(node.get("id"))
    }
    focus = _text(graph.get("focus"))
    if not focus:
        return []
    out: list[dict[str, object]] = []
    for raw in graph.get("edges", []):
        if not isinstance(raw, Mapping) or raw.get("section") not in _SEMANTIC_SECTIONS:
            continue
        direction = _text(raw.get("direction"))
        source = _text(raw.get("from"))
        destination = _text(raw.get("to"))
        if direction == "in":
            related_id = source
        elif direction == "out":
            related_id = destination
        else:
            # Semantic graphview edges are always inward or outward.  Do not
            # manufacture a relationship if a future provider changes shape.
            continue
        related_node = nodes.get(related_id or "")
        if not isinstance(related_node, Mapping):
            continue
        related = _symbol_ref(related_node.get("path"), related_node.get("name"))
        relation = _text(raw.get("relation"))
        depth = raw.get("depth")
        if not related or not relation or not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
            continue
        out.append(
            {
                "target": target,
                "related": related,
                "relation": relation,
                "direction": direction,
                "depth": depth,
                **relations.evidence(raw),
            }
        )
    return _sorted(out)


def map_baseline(target: str, depth: int, amu_map: Mapping[str, Any] | None) -> EdgeEvidence:
    """Return a comparable pre-edit baseline only for the exact mapped target.

    A map for the same file is *not* automatically evidence for every symbol
    in that file.  Maps record their exact queried symbols in
    ``graph_evidence.queried_symbols``.  The requested depth must also match:
    comparing a depth-two map to a depth-one live query can otherwise fabricate
    removals for relationships that were merely outside the second query.
    """
    parts = _target_parts(target)
    if not parts:
        return EdgeEvidence(False, [], "target must use the exact form path#qualified_name")
    if _positive_int(depth) is None:
        return EdgeEvidence(False, [], "refresh depth must be a positive integer")
    if not isinstance(amu_map, Mapping):
        return EdgeEvidence(False, [], "no saved map exists for this refresh")
    path, symbol = parts
    root = amu_map.get("root")
    if not isinstance(root, Mapping) or _text(root.get("file")) != path:
        mapped_file = _text(root.get("file")) if isinstance(root, Mapping) else None
        if mapped_file:
            return EdgeEvidence(False, [], f"saved map is for {mapped_file}, not {path}")
        return EdgeEvidence(False, [], "saved map does not record a root file")

    mapped_depth = _positive_int(amu_map.get("depth"))
    if mapped_depth is None:
        return EdgeEvidence(False, [], "saved map does not record a valid graph depth")
    if mapped_depth != depth:
        return EdgeEvidence(
            False,
            [],
            f"saved map was made at depth {mapped_depth}; refresh requested depth {depth}",
        )

    graph_evidence = amu_map.get("graph_evidence")
    if not isinstance(graph_evidence, Mapping):
        return EdgeEvidence(
            False,
            [],
            "saved map does not record exact queried symbols; create a new map before comparing edges",
        )
    queried_symbols = _sequence(graph_evidence.get("queried_symbols"))
    if queried_symbols is None:
        return EdgeEvidence(False, [], "saved map has malformed queried-symbol evidence")
    if symbol not in {_text(value) for value in queried_symbols}:
        return EdgeEvidence(False, [], f"saved map did not query the exact target {target}")
    unresolved_focus = _sequence(graph_evidence.get("unresolved_focus"))
    if unresolved_focus is None:
        return EdgeEvidence(False, [], "saved map has malformed focus-resolution evidence")
    if symbol in {_text(value) for value in unresolved_focus}:
        return EdgeEvidence(False, [], f"saved map did not resolve the exact target {target}")

    map_hash = _text(amu_map.get("map_hash"))
    evidence_hash = _text(graph_evidence.get("map_hash"))
    if not map_hash or map_hash != evidence_hash:
        return EdgeEvidence(False, [], "saved map graph evidence does not match its map hash; create a new map")

    worktree_reason = map_worktree_capture_reason(graph_evidence)
    if worktree_reason:
        return EdgeEvidence(False, [], f"saved {worktree_reason}; create a new map before comparing edges")

    coverage_reason = _coverage_reason(target, depth, graph_evidence)
    if coverage_reason:
        return EdgeEvidence(False, [], coverage_reason)

    # The human-facing ripple list is intentionally lossy: it groups graph
    # facts with heuristic neighbours for planning.  A refresh baseline must
    # instead use the exact, fully-normalized semantic records captured from
    # the provider.  Missing this field makes older maps inspection-only.
    baselines = graph_evidence.get("edge_baselines")
    if not isinstance(baselines, Mapping):
        return EdgeEvidence(False, [], "saved map has no complete semantic-edge baseline; create a new map")
    raw_edges = baselines.get(target)
    if not isinstance(raw_edges, list):
        return EdgeEvidence(False, [], f"saved map has no complete semantic-edge baseline for the exact target {target}")
    out: list[dict[str, object]] = []
    for position, raw in enumerate(raw_edges, start=1):
        if not isinstance(raw, Mapping):
            return EdgeEvidence(False, [], f"saved map semantic edge {position} is malformed")
        if relations.evidence(raw)["confidence"] != "sound":
            return EdgeEvidence(False, [], f"saved map semantic edge {position} is not resolved evidence")
        related = _text(raw.get("related"))
        relation = _text(raw.get("relation"))
        direction = _text(raw.get("direction"))
        edge_depth = _positive_int(raw.get("depth"))
        external = _external_ref(raw.get("external_boundary"))
        if (
            _text(raw.get("target")) != target
            or (_target_parts(related or "") is None and (external != related or direction != "out"
                                                         or relation not in {"CALLS", "ASYNC_CALLS"} or edge_depth != 1
                                                         or raw["external_boundary"]["source_path"] != path))
            or not relation
            or direction not in {"in", "out"}
            or edge_depth is None
        ):
            return EdgeEvidence(False, [], f"saved map semantic edge {position} is incomplete")
        out.append(
            {
                "target": target,
                "related": related,
                "relation": relation,
                "direction": direction,
                "depth": edge_depth,
                **relations.evidence(raw),
                **({"external_boundary": dict(raw["external_boundary"])} if external else {}),
                **({"source_resolution": dict(raw["source_resolution"])} if isinstance(raw.get("source_resolution"), Mapping) else {}),
            }
        )
    return EdgeEvidence(True, _sorted(out))


def from_map_ripples(target: str, amu_map: Mapping[str, Any] | None) -> tuple[bool, list[dict[str, object]]]:
    """Backward-compatible map extraction for callers that predate ``refresh``.

    New refresh callers must use :func:`map_baseline` with the requested
    depth, so the comparison cannot accidentally cross traversal depths.  This
    wrapper uses the saved map's own depth only to avoid silently widening the
    historical two-value API.
    """
    map_depth = amu_map.get("depth") if isinstance(amu_map, Mapping) else None
    evidence = map_baseline(target, map_depth, amu_map)
    return evidence.available, evidence.edges


def focus_matches_target(target: str, impact: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    """Verify that a provider response resolves the requested target exactly.

    A graph query that resolves a same-named symbol in another file (or a
    nested symbol when an unqualified name was requested) is useful diagnostic
    data, but cannot be attached to the requested ``path#qualified_name``.
    """
    parts = _target_parts(target)
    if not parts:
        return False, "target must use the exact form path#qualified_name"
    if not isinstance(impact, Mapping):
        return False, "live graph returned no structured impact response"
    if impact.get("error"):
        return False, f"live graph failed: {impact['error']}"
    if impact.get("disambiguation_required"):
        return False, "live graph requires target disambiguation"
    return _exact_focus(target, impact.get("focus"))


def live_edges(target: str, impact: Mapping[str, Any] | None) -> EdgeEvidence:
    """Extract complete, depth-preserving semantic edges from a raw impact.

    ``total`` must match the returned entries for each semantic section.  A
    truncated response cannot safely report removed relationships, so it is
    explicitly unavailable instead of being treated as a smaller graph.
    """
    exact, reason = focus_matches_target(target, impact)
    if not exact:
        return EdgeEvidence(False, [], reason)
    assert isinstance(impact, Mapping)  # narrowed by focus_matches_target

    out: list[dict[str, object]] = []
    for section, expected_direction in _SECTION_DIRECTIONS.items():
        allowed_directions = _SECTION_ALLOWED_DIRECTIONS[section]
        group = impact.get(section)
        if not isinstance(group, Mapping):
            return EdgeEvidence(False, [], f"live graph did not return a {section} section")
        total = group.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            return EdgeEvidence(False, [], f"live graph {section} section has no valid total")
        entries = group.get("entries")
        if not isinstance(entries, list):
            return EdgeEvidence(False, [], f"live graph {section} section has no entry list")
        if len(entries) != total:
            return EdgeEvidence(
                False,
                [],
                f"live graph {section} response is truncated ({len(entries)} of {total} edges returned)",
            )
        for position, raw in enumerate(entries, start=1):
            if not isinstance(raw, Mapping):
                return EdgeEvidence(False, [], f"live graph {section} edge {position} is malformed")
            raw_direction = _text(raw.get("direction"))
            if raw_direction is None and len(allowed_directions) > 1:
                return EdgeEvidence(
                    False,
                    [],
                    f"live graph {section} edge {position} has no direction; expected one of {', '.join(sorted(allowed_directions))}",
                )
            if raw_direction and raw_direction not in allowed_directions:
                return EdgeEvidence(
                    False,
                    [],
                    f"live graph {section} edge {position} has direction {raw_direction}, expected one of {', '.join(sorted(allowed_directions))}",
                )
            direction = raw_direction or expected_direction
            endpoint = raw.get("endpoint")
            if relations.evidence(raw)["confidence"] != "sound":
                return EdgeEvidence(False, [], f"live graph {section} edge {position} is not resolved evidence")
            if not isinstance(endpoint, Mapping):
                return EdgeEvidence(False, [], f"live graph {section} edge {position} has no endpoint")
            related = _symbol_ref(endpoint.get("file_path"), endpoint.get("qualified_name") or endpoint.get("name"))
            relation = _text(raw.get("relation"))
            edge_depth = _positive_int(raw.get("depth"))
            external = _external_ref(raw.get("external_boundary"))
            if (related is None and external and section == "callees" and direction == "out"
                    and relation in {"CALLS", "ASYNC_CALLS"} and edge_depth == 1
                    and raw["external_boundary"]["source_path"] == impact["focus"]["file_path"]):
                related = external
            if not related or not relation or edge_depth is None:
                return EdgeEvidence(False, [], f"live graph {section} edge {position} is incomplete")
            out.append(
                {
                    "target": target,
                    "related": related,
                    "relation": relation,
                    "direction": direction,
                    "depth": edge_depth,
                    **relations.evidence(raw),
                    **({"external_boundary": dict(raw["external_boundary"])} if related == external else {}),
                    **({"source_resolution": dict(raw["source_resolution"])} if isinstance(raw.get("source_resolution"), Mapping) else {}),
                }
            )
    return EdgeEvidence(True, _sorted(out))


def edge_baselines_from_impacts(
    root_file: str,
    impacts: Mapping[str, object],
    depth: int,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, str]]:
    """Capture a lossless semantic baseline for every map query.

    The map UI also records ``ripples``, which intentionally combines semantic
    graph facts with planning heuristics.  It is not safe to reconstruct a
    removal baseline from that display data: a provider may omit a redundant
    direction field which is valid for callers/callees, or a malformed entry
    may be skipped by presentation code.  This helper runs the same strict
    normalizer used after an edit and records either every comparable edge or
    a precise reason the map cannot be approved.
    """
    if _positive_int(depth) is None:
        return {}, {"<map>": "map depth must be a positive integer"}
    baselines: dict[str, list[dict[str, object]]] = {}
    errors: dict[str, str] = {}
    for raw_symbol in sorted(impacts, key=lambda value: str(value)):
        symbol = _text(raw_symbol)
        if not symbol:
            errors["<map>"] = "map queried an invalid symbol"
            continue
        target = f"{root_file}#{symbol}"
        raw_impact = impacts[raw_symbol]
        evidence = live_edges(target, raw_impact if isinstance(raw_impact, Mapping) else None)
        if evidence.available:
            baselines[target] = evidence.edges
        else:
            errors[target] = evidence.reason or "live graph response cannot support a complete semantic baseline"
    return baselines, errors


def delta(before: Iterable[Mapping[str, object]], after: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Compare normalized semantic graph edges without hiding unchanged scope."""
    old = {_edge_key(edge): dict(edge) for edge in before}
    new = {_edge_key(edge): dict(edge) for edge in after}
    return {
        "added": [new[key] for key in sorted(new.keys() - old.keys())],
        "removed": [old[key] for key in sorted(old.keys() - new.keys())],
        "unchanged": len(old.keys() & new.keys()),
    }
