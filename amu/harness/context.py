"""Shared deterministic ranking with an explicit conservative context budget.

UTF-8 serialized bytes are measured directly; these are not billing tokens.
Critical evidence is never truncated to fit: an oversized safety packet fails
closed and asks for a narrower target. Optional rows have disclosed omissions.
"""

from __future__ import annotations

import json


class ContextLimitError(ValueError):
    pass


def encode(value: object) -> str:
    def default(item):
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "__dict__"):
            return vars(item)
        return str(item)
    return json.dumps(value, default=default, ensure_ascii=False, separators=(",", ":"))


def estimate(value: object) -> int:
    # Match the outbound boundary's standard JSON encoding (including escaped
    # Unicode and spacing); the compact context projection is smaller than this.
    def default(item):
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "__dict__"):
            return vars(item)
        return str(item)
    return len(json.dumps(value, default=default).encode("utf-8"))


def limits(config: dict) -> dict:
    result = {"context_bytes": 65536, "session_input_bytes": 4_000_000, "session_requests": 100}
    raw = config.get("model_limits", {})
    if not isinstance(raw, dict) or set(raw) - result.keys():
        raise ContextLimitError("model_limits must contain supported budget settings")
    for key, value in raw.items():
        minimum = 2048 if key == "context_bytes" else 1
        maximum = 262144 if key == "context_bytes" else (10_000 if key == "session_requests" else 100_000_000)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ContextLimitError(f"invalid model_limits.{key}")
        result[key] = value
    return result


def _rank(row: object, key: str) -> tuple[int, str]:
    if not isinstance(row, dict):
        return 10, "supporting context"
    path = str(row.get("path") or row.get("file_path") or "").lower()
    relation = str(row.get("relation") or str(row.get("via") or "").split(":", 1)[0] or row.get("kind") or "").upper()
    if row.get("changed") or row.get("is_root") or row.get("is_target") or relation in ("ROOT", "TARGET"):
        return 100, "changed symbol or target"
    if row.get("confidence") == "unknown" or row.get("risk") == "unknown":
        return 95, "uncertain boundary requires verification"
    if relation in ("CALLS", "CALLER", "CALLEE", "CALLERS", "CALLEES") and row.get("depth", row.get("order", 1)) == 1:
        return 90, "direct caller or callee"
    if "/test" in "/" + path or row.get("is_test"):
        return 80, "test coverage"
    if relation in ("HANDLES_ROUTE", "ENTRY_POINT", "ROUTE", "HANDLES_TOOL"):
        return 75, "entry point"
    if relation in ("IMPORTS", "INHERITS", "EXTENDS"):
        return 70, "import or inheritance neighbor"
    if key in ("results", "candidates"):
        return 60, "retrieval order"
    return 20, "supporting relation or memory"


def rank_rows(rows: list, key: str = "nodes") -> list:
    """Return stable relevance order without discarding any optional row.

    Pagination uses the same policy as ranked_slice, but applies its own page
    byte budget. Keep source order for ties and expose the ranking reason.
    """
    ranked = [(_rank(row, key), position, row) for position, row in enumerate(rows)]
    return [{**row, "context_reason": why} if isinstance(row, dict) else row
            for (_, why), _, row in sorted(ranked, key=lambda item: (-item[0][0], item[1]))]


def ranked_slice(value: dict, budget_bytes: int = 12000) -> dict:
    """Rank optional rows across the whole packet; retain mandatory metadata."""
    row_keys = {"nodes", "edges", "ripples", "results", "candidates", "memory", "hot_spots"}
    candidates = []
    groups = []

    def project(item, path=""):
        if not isinstance(item, dict):
            return item
        output = {}
        for key, child in item.items():
            if key in ("context_selection", "truncation", "truncated"):
                continue
            if key == "graph_evidence" and isinstance(child, dict):
                # Source fingerprints are machine validation inputs, not model
                # instructions. Retain all verdicts and warnings verbatim.
                machine_fields = {"worktree", "pre_edit_edges", "baseline", "edge_baselines", "impact_coverage"}
                omitted_fields = sorted(machine_fields & child.keys())
                child = {k: v for k, v in child.items() if k not in machine_fields}
                if omitted_fields:
                    child = {**child, "omitted_validation_fields": omitted_fields}
            if key in row_keys and isinstance(child, list):
                output[key] = []
                group = {"path": f"{path}{key}", "total": len(child), "included": 0, "omitted": len(child)}
                groups.append(group)
                for row in child:
                    score, why = _rank(row, key)
                    candidates.append((score, len(candidates), output[key], row, why, group))
            else:
                output[key] = project(child, f"{path}{key}.") if isinstance(child, dict) else child
        return output

    result = project(value)
    result["context_selection"] = {
        "budget_bytes": budget_bytes, "measure": "UTF-8 serialized bytes, not billing tokens",
        "groups": groups, "omission_reason": "lower-ranked optional rows that do not fit; narrow the target for more detail",
    }
    # Reserve room for the final flags and counters, so no safety field needs
    # to be cut after selection.
    result["truncated"] = True
    size = estimate(result)
    reserve = 100 + 20 * len(groups)
    if size + reserve > budget_bytes:
        raise ContextLimitError("mandatory evidence exceeds the context budget; narrow the target; do not approve from an incomplete packet")
    for _, _, destination, row, why, group in sorted(candidates, key=lambda item: (-item[0], item[1])):
        projected = {**row, "context_reason": why} if isinstance(row, dict) else row
        row_size = estimate(projected) + (2 if destination else 0)
        if size + row_size + reserve <= budget_bytes:
            destination.append(projected)
            group["included"] += 1
            group["omitted"] -= 1
            size += row_size
    result["truncated"] = any(group["included"] != group["total"] for group in groups)
    if estimate(result) > budget_bytes:
        raise ContextLimitError("mandatory context metadata exceeds the context budget")
    return result
