"""Fail-closed evidence checks for graph-backed approval decisions.

An index can succeed as a process while carrying only structural records (for
example ``DEFINES`` and ``CONTAINS``) or explicitly report degraded analysis.
Those are useful diagnostics, but they cannot support a scope contract.  A
separate live symbols/impact pass can still provide actionable evidence when a
cached index is broken, so index readiness and map readiness intentionally
remain distinct.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Number
from typing import Any

NON_APPROVABLE_LEVELS = frozenset({"unsafe", "degraded", "incomplete", "failed", "unavailable"})
SEMANTIC_RELATIONS = frozenset({"CALLS", "TYPE_CONSUMERS", "DATA_FLOWS", "USES_TYPE", "INHERITS", "EXTENDS"})
IMPACT_SECTIONS = ("callers", "callees", "type_consumers", "data_flows")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, Number):
        return None
    return max(0, int(value))


def _normal_relation(name: object) -> str:
    return str(name).strip().upper().replace("-", "_").replace(" ", "_")


def _levels(payload: object) -> set[str]:
    """Collect explicit completeness/status levels from known response shapes."""
    root = _mapping(payload)
    candidates: list[object] = []
    for container in (
        root,
        _mapping(root.get("counts")),
        _mapping(root.get("stats")),
        _mapping(root.get("completeness")),
        _mapping(root.get("completeness_scope")),
    ):
        candidates.extend(container.get(key) for key in ("completeness_level", "level", "status"))
    return {str(value).strip().lower() for value in candidates if isinstance(value, str) and value.strip()}


def _partial_failure_count(payload: object) -> int:
    root = _mapping(payload)
    for container in (root, _mapping(root.get("counts")), _mapping(root.get("stats"))):
        value = container.get("partial_failures")
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return len(value)
        count = _count(value)
        if count is not None:
            return count
    return 0


def _relation_evidence(payload: object) -> tuple[dict[str, int], list[int]]:
    """Return typed and aggregate relation counts across documented result shapes."""
    root = _mapping(payload)
    typed: dict[str, int] = {}
    aggregate: list[int] = []
    for container in (
        root,
        _mapping(root.get("counts")),
        _mapping(root.get("stats")),
        _mapping(root.get("completeness")),
    ):
        relations = container.get("relations")
        if isinstance(relations, Mapping):
            for name, amount in relations.items():
                count = _count(amount)
                if count is not None:
                    key = _normal_relation(name)
                    typed[key] = max(typed.get(key, 0), count)
        else:
            count = _count(relations)
            if count is not None:
                aggregate.append(count)
    return typed, aggregate


def index_evidence(index: object) -> dict:
    """Assess whether an index response can support an approvable contract.

    A numeric aggregate relation count is accepted for older graph versions;
    live impacts are still required before approval.  When a response exposes
    a typed relation table, however, structural-only output is conclusive and
    must be rejected.
    """
    root = _mapping(index)
    reasons: list[str] = []
    if not root:
        reasons.append("graph index returned no structured evidence")
    if root.get("error"):
        reasons.append(f"graph index failed: {root['error']}")

    levels = _levels(root)
    bad_levels = sorted(level for level in levels if level in NON_APPROVABLE_LEVELS)
    if bad_levels:
        reasons.append(f"graph index completeness is {', '.join(bad_levels)}")

    relation_types, aggregate_relations = _relation_evidence(root)
    semantic_relations = sum(count for name, count in relation_types.items() if name in SEMANTIC_RELATIONS)
    if relation_types and semantic_relations == 0:
        kinds = ", ".join(sorted(relation_types)) or "none"
        reasons.append(f"graph index has no semantic relations (reported: {kinds})")
    elif not relation_types:
        if not aggregate_relations:
            reasons.append("graph index did not report relationship evidence")
        elif max(aggregate_relations) == 0:
            reasons.append("graph index reported zero relations")

    counts = _mapping(root.get("counts"))
    file_count = _count(counts.get("files"))
    partial_failures = _partial_failure_count(root)
    if file_count and partial_failures >= file_count:
        reasons.append(f"graph index reported {partial_failures} partial failures for {file_count} files")

    return {
        "ready": not reasons,
        "stage": "index",
        "completeness_levels": sorted(levels),
        "partial_failures": partial_failures,
        "relation_types": relation_types,
        "semantic_relations": semantic_relations if relation_types else None,
        "aggregate_relations": max(aggregate_relations) if aggregate_relations else None,
        "reasons": reasons,
    }


def _impact_edges(impact: Mapping[str, Any]) -> int:
    total = 0
    for section in IMPACT_SECTIONS:
        data = impact.get(section)
        if isinstance(data, Mapping):
            count = _count(data.get("total"))
            entries = data.get("entries")
            entry_count = (
                len(entries)
                if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, bytearray))
                else 0
            )
            total += max(count or 0, entry_count)
        elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
            total += len(data)
    return total


def live_map_evidence(symbol_rows: object, impacts: object, *, root_file: str | None = None) -> dict:
    """Assess live symbols + impact output without conflating it with cache health.

    The map remains printable when this returns ``approvable=False``; callers
    must show the evidence/unknowns but must not turn it into a contract.
    """
    rows = (
        symbol_rows
        if isinstance(symbol_rows, Sequence) and not isinstance(symbol_rows, (str, bytes, bytearray))
        else []
    )
    impact_rows = _mapping(impacts)
    reasons: list[str] = []
    levels: set[str] = set()
    for row in rows:
        levels.update(_levels(row))

    unresolved_focus: list[str] = []
    isolated_targets: list[str] = []
    semantic_edges = 0
    if not impact_rows:
        reasons.append("map made no live impact queries")
    for symbol, raw_impact in impact_rows.items():
        impact = _mapping(raw_impact)
        levels.update(_levels(impact))
        if not impact or impact.get("error"):
            unresolved_focus.append(str(symbol))
            continue
        focus = _mapping(impact.get("focus"))
        if not focus or not any(focus.get(key) for key in ("id", "name", "qualified_name", "file_path")):
            unresolved_focus.append(str(symbol))
            continue
        count = _impact_edges(impact)
        semantic_edges += count
        if root_file and count:
            from amu.graph_refresh import live_edges
            coverage = live_edges(f"{root_file}#{symbol}", impact)
            if not coverage.available:
                reasons.append(f"incomplete exact-target coverage for {symbol}: {coverage.reason}")
        if not count:
            # Zero is legitimate only with positive, exact coverage evidence.
            # A nonempty neighboring query cannot mask an uncovered symbol.
            from amu.graph_refresh import live_edges
            target = f"{root_file}#{symbol}" if root_file else ""
            coverage = live_edges(target, impact)
            if (coverage.available and not _partial_failure_count(impact)
                    and _levels(impact) & {"complete", "full", "sound"}):
                isolated_targets.append(target)
            else:
                reasons.append(f"live impact has no semantic edges and no complete exact-target coverage for {symbol}")
    if unresolved_focus:
        reasons.append(f"live impact did not resolve focus for: {', '.join(unresolved_focus)}")
    bad_levels = sorted(level for level in levels if level in NON_APPROVABLE_LEVELS)
    if bad_levels:
        reasons.append(f"live graph completeness is {', '.join(bad_levels)}")

    return {
        "approvable": not reasons,
        "stage": "live-map",
        "completeness_levels": sorted(levels),
        "queried_symbols": sorted(str(symbol) for symbol in impact_rows),
        "unresolved_focus": unresolved_focus,
        "semantic_edges": semantic_edges,
        "isolated_targets": sorted(isolated_targets),
        "reasons": reasons,
    }


def live_target_evidence(target: str, impact: object) -> dict:
    """Assess one post-edit live impact before it can produce an edge delta.

    Unlike a map, a refresh is allowed to have *zero* semantic edges: an edit
    can legitimately remove the last caller.  It still needs a resolved focus,
    no declared degraded/incomplete level, and no provider-reported partial
    failures.  Otherwise a missing edge could be a parser omission rather than
    a relationship removal.
    """
    raw = _mapping(impact)
    reasons: list[str] = []
    if not raw:
        reasons.append("live graph returned no structured impact")
    if raw.get("error"):
        reasons.append(f"live graph failed: {raw['error']}")
    focus = _mapping(raw.get("focus"))
    if not focus or not any(focus.get(key) for key in ("id", "name", "qualified_name", "file_path")):
        reasons.append(f"live graph did not resolve focus for: {target}")
    levels = _levels(raw)
    bad_levels = sorted(level for level in levels if level in NON_APPROVABLE_LEVELS)
    if bad_levels:
        reasons.append(f"live graph completeness is {', '.join(bad_levels)}")
    partial_failures = _partial_failure_count(raw)
    if partial_failures:
        reasons.append(f"live graph reported {partial_failures} partial failure(s)")
    return {
        "approvable": not reasons,
        "stage": "live-refresh",
        "target": target,
        "completeness_levels": sorted(levels),
        "partial_failures": partial_failures,
        "semantic_edges": _impact_edges(raw),
        "reasons": reasons,
    }


def with_source_contradictions(evidence: Mapping[str, Any], contradictions: Sequence[Mapping[str, Any]]) -> dict:
    """Make independently disproved raw edges an explicit approval blocker.

    The graph remains useful as a candidate generator, but a single direct
    Python ``CALLS`` relation shown to be docstring-only means this particular
    response cannot safely authorize a scope contract.
    """
    rows = [dict(row) for row in contradictions]
    result = dict(evidence)
    result["contradicted_python_calls"] = rows
    if not rows:
        return result
    reasons = list(result.get("reasons") or [])
    reasons.append(f"source AST contradicted {len(rows)} direct Python CALLS edge(s)")
    result["reasons"] = reasons
    result["approvable"] = False
    return result


def with_source_call_evidence(
    evidence: Mapping[str, Any],
    contradictions: Sequence[Mapping[str, Any]],
    uncertainties: Sequence[Mapping[str, Any]],
) -> dict:
    """Attach independent source-call evidence to graph evidence.

    Source can *disprove* a provider edge (for example, a docstring-only call
    or an import that resolves to a different module), and those
    contradictions remain approval blockers.  Missing, generated, or
    otherwise inconclusive source is different: the PRD defines a resolved
    graph edge as sound and source reading as heuristic evidence, so an AST
    that cannot independently resolve the identity must be disclosed without
    downgrading the provider's resolved edge by itself.
    """
    result = with_source_contradictions(evidence, contradictions)
    rows = [dict(row) for row in uncertainties]
    result["unverified_python_calls"] = rows
    return result


def summary(evidence: Mapping[str, Any]) -> str:
    """Compact, deterministic explanation safe for CLI JSON/text output."""
    reasons = evidence.get("reasons")
    if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes, bytearray)) and reasons:
        return "; ".join(str(reason) for reason in reasons)
    return "graph evidence is not ready"
