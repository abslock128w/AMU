"""Bounded, stateless retrieval of an intent-based impact report.

Cursors are version bindings, not authorization tokens. Callers must repeat
repository authorization, source validation and live collection before using
one. Nothing here reads paths, executes proposals or persists a report.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

from amu.harness.context import estimate, rank_rows
from amu.mcp_manifest import MCP_WIRE_LIMIT, tool_result_bytes

SECTIONS = frozenset({"impact", "source_contradictions", "source_uncertainties", "dynamic_boundaries", "coverage", "reasons"})
PATCH_SECTIONS = frozenset({"changes", "findings", "state_dependencies", "recommended_checks", "uncertainties"})
MAX_CURSOR_BYTES = 1024
FRAGMENT_BYTES = 4096
MAX_ITEM_BYTES = 32 * 1024 * 1024
MAX_NORMAL_ITEM_BYTES = 8192
RETRIEVAL_VERSION = 1


class ReportError(ValueError):
    pass


def _encode(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReportError("impact evidence is not bounded JSON; repeat inspection with a narrower target") from exc


def _pairs(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate cursor field")
        value[key] = child
    return value


def cursor(report_hash: str, section: str, offset: int) -> str:
    return base64.urlsafe_b64encode(_encode({"v": 1, "report_hash": report_hash, "section": section, "offset": offset})).decode("ascii").rstrip("=")


def decode_cursor(value: str, *, sections: frozenset = SECTIONS) -> dict:
    """Reject malformed envelopes before any filesystem/provider inspection."""
    try:
        if (not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CURSOR_BYTES
                or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None):
            raise ValueError()
        body = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        decoded = json.loads(body, object_pairs_hook=_pairs)
        if (not isinstance(decoded, dict) or set(decoded) != {"v", "report_hash", "section", "offset"}
                or type(decoded["v"]) is not int or decoded["v"] != 1
                or type(decoded["offset"]) is not int or decoded["offset"] < 0
                or decoded["section"] not in sections
                or re.fullmatch(r"[0-9a-f]{64}", decoded["report_hash"]) is None):
            raise ValueError()
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReportError("cursor is invalid; start a new impact request without it") from exc
    return decoded


def cursor_offset(value: str | None, report_hash: str, section: str, *, sections: frozenset = SECTIONS) -> int:
    if value is None:
        return 0
    decoded = decode_cursor(value, sections=sections)
    if decoded["report_hash"] != report_hash or decoded["section"] != section:
        raise ReportError("cursor is stale or belongs to a different analysis or section; restart inspection")
    return decoded["offset"]


def _fragment_items(items: list) -> tuple[list, int]:
    """Do not skip an oversized row: expose reconstructable, hash-bound chunks."""
    result, fragmented = [], 0
    for item in items:
        if estimate(item) <= MAX_NORMAL_ITEM_BYTES:
            result.append(item)
            continue
        body = _encode(item)
        if len(body) > MAX_ITEM_BYTES:
            raise ReportError("one evidence item exceeds the bounded retrieval limit; narrow the target")
        count = (len(body) + FRAGMENT_BYTES - 1) // FRAGMENT_BYTES
        digest = hashlib.sha256(body).hexdigest()
        for number in range(count):
            result.append({"evidence_fragment": {
                "encoding": "base64-json-utf8", "item_sha256": digest, "fragment_index": number,
                "fragment_count": count, "data": base64.b64encode(body[number * FRAGMENT_BYTES:(number + 1) * FRAGMENT_BYTES]).decode("ascii"),
                "instruction": "Concatenate decoded bytes in index order only after retrieving all fragments from this report; verify item_sha256 before parsing. Partial fragments are not usable evidence.",
            }})
        fragmented += 1
    return result, fragmented


def build_packet(request: dict, target: dict, *, repo_binding: str, status: str, reasons: list[str], evidence: dict,
                 rows: list, boundaries: list, limitations: list[str], max_bytes: int, max_rows: int) -> dict:
    section, page_size = request["section"], request["page_size"]
    raw_coverage = {key: evidence.get(key) for key in (
        "impact_coverage", "edge_baseline_errors", "completeness_levels", "isolated_targets", "capabilities_error",
        "semantic_edges", "unresolved_focus", "unverified_python_calls", "edge_baselines", "baseline_ready", "pre_edit_graph_files",
    ) if evidence.get(key) is not None}
    groups = {
        "impact": rank_rows(rows),
        "source_contradictions": list(evidence.get("source_contradictions") or []),
        "source_uncertainties": list(evidence.get("source_uncertainties") or []),
        "dynamic_boundaries": boundaries,
        "coverage": [{"field": key, "value": value} for key, value in sorted(raw_coverage.items())],
        "reasons": [{"reason": reason} for reason in reasons],
    }
    critical = {key: evidence.get(key) for key in (
        "live_worktree", "provider_reported_coverage_complete", "map_hash", "sha", "worktree",
    ) if key in evidence}
    # Display timestamps and requested page size do not change the evidence
    # identity. Source/configuration/HEAD/worktree and every detailed item do.
    analysis_request = {key: value for key, value in request.items() if key not in {"cursor", "section", "page_size"}}
    report_hash = hashlib.sha256(_encode({"repo_binding": repo_binding, "request": analysis_request,
                                        "target": target, "status": status, "critical": critical, "groups": groups,
                                        "retrieval_format": {"version": RETRIEVAL_VERSION, "fragment_bytes": FRAGMENT_BYTES,
                                                             "max_normal_item_bytes": MAX_NORMAL_ITEM_BYTES,
                                                             "max_item_bytes": MAX_ITEM_BYTES}})).hexdigest()
    overview = []
    if reasons:
        overview.append("Limitations or failures were reported; retrieve the reasons section before relying on this result.")
    if groups["source_contradictions"]:
        overview.append("Source contradicted some provider relationships; those contradictions remain review evidence.")
    if groups["source_uncertainties"]:
        overview.append("Some relationship identities are unverified in source.")
    if boundaries:
        overview.append("Target source contains dynamic or runtime-dispatch boundaries.")
    packet = {
        "schema_version": 1, "operation": "proposed-change-impact", "request": request, "target": target,
        "status": status, "report_hash": report_hash,
        "assessment": {"likely_impacted": len(rows), "provider_reported_coverage_complete": bool(evidence.get("provider_reported_coverage_complete")),
                       "safe_to_change": None, "reasons": overview},
        "impact": {"rows": [], "omitted": len(rows), "summary": {"candidate_count": len(rows)}},
        "graph_evidence": {**critical, "source_contradictions_count": len(groups["source_contradictions"]),
                           "source_uncertainties_count": len(groups["source_uncertainties"]),
                           "coverage_detail_count": len(groups["coverage"])},
        "dynamic_boundaries": [], "dynamic_boundaries_count": len(boundaries), "limitations": list(limitations),
        "retrieval": {}, "page": {}, "truncated": False,
    }
    # Reserve final counters/cursors and impose the limit on standard escaped
    # JSON, which is larger than compact UTF-8 transport JSON.
    overhead = estimate(packet) + 6000
    if overhead >= max_bytes:
        raise ReportError("mandatory impact metadata exceeds the response limit; no partial report can be supplied")
    available = max_bytes - overhead
    paged_groups = {}
    for name, items in groups.items():
        # Fragmentation is independent of cursor length/page size. Otherwise
        # later requests could interpret the same offset in a different list.
        entries, fragmented = _fragment_items(items)
        paged_groups[name] = entries
        packet["retrieval"][name] = {
            "logical_items": len(items), "entries": len(entries), "fragmented_items": fragmented,
            "cursor": cursor(report_hash, name, 0) if entries else None,
        }
    items = paged_groups[section]
    offset = cursor_offset(request["cursor"], report_hash, section)
    if offset > len(items):
        raise ReportError("cursor offset is outside this report; restart inspection")
    returned, size = [], 0
    for item in items[offset:offset + min(page_size, max_rows)]:
        item_size = estimate(item) + 2
        if size + item_size > available:
            break
        returned.append(item)
        size += item_size
    if offset < len(items) and not returned:
        raise ReportError("one evidence entry cannot fit the response budget; narrow the target")
    next_offset = offset + len(returned)
    packet["page"] = {"section": section, "offset": offset, "returned": len(returned), "total": len(items),
                      "remaining": len(items) - next_offset, "fragmented_items": packet["retrieval"][section]["fragmented_items"],
                      "next_cursor": cursor(report_hash, section, next_offset) if next_offset < len(items) else None}
    if section == "impact":
        packet["impact"].update(rows=returned, omitted=len(items) - len(returned),
                                selection={"row_limit": max_rows, "page_size": page_size, "budget_bytes": max_bytes,
                                           "order": "shared context relevance, with stable source order for ties"})
    else:
        packet["details"] = {"section": section, "items": returned}
        if section == "dynamic_boundaries":
            packet["dynamic_boundaries"] = returned
        if section in {"source_contradictions", "source_uncertainties"}:
            packet["graph_evidence"][section] = returned
    # Other sections are deliberately separate rather than silently discarded.
    packet["truncated"] = any(name != section and value for name, value in paged_groups.items()) or next_offset < len(items)
    if packet["truncated"] or packet["page"]["fragmented_items"]:
        packet["assessment"]["reasons"].append("Detailed evidence is paged; retrieve all relevant sections/fragments from the same report before relying on it.")
    if packet["page"]["fragmented_items"]:
        packet["status"] = "uncertain"
    if (estimate(packet) > max_bytes
            or tool_result_bytes(json.dumps(packet, ensure_ascii=False)) > MCP_WIRE_LIMIT):
        # A conservative retry at a smaller page size keeps every entry
        # retrievable, including sections represented twice for compatibility.
        if len(returned) > 1:
            return build_packet(request, target,
                                repo_binding=repo_binding, status=status, reasons=reasons, evidence=evidence,
                                rows=rows, boundaries=boundaries, limitations=limitations, max_bytes=max_bytes,
                                max_rows=max(1, len(returned) // 2))
        raise ReportError("impact response cannot fit its protocol budget; no partial evidence was returned")
    return packet


def build_patch_packet(request: dict, *, identity: dict, groups: dict, limitations: list[str],
                       max_bytes: int, max_rows: int) -> dict:
    """Use the same lossless fragments and freshness-bound cursors as intent reports."""
    if set(groups) != PATCH_SECTIONS:
        raise ReportError("invalid patch evidence sections")
    public_request = {key: value for key, value in request.items() if key != "patch"}
    public_request.update(patch_sha256=hashlib.sha256(request["patch"].encode("utf-8")).hexdigest(),
                          patch_bytes=len(request["patch"].encode("utf-8")))
    analysis_request = {key: value for key, value in public_request.items() if key not in {"cursor", "section", "page_size"}}
    report_hash = hashlib.sha256(_encode({"operation": "concrete-patch-impact", "identity": identity,
                                        "request": analysis_request, "groups": groups,
                                        "retrieval_format": {"version": RETRIEVAL_VERSION, "fragment_bytes": FRAGMENT_BYTES,
                                                             "max_normal_item_bytes": MAX_NORMAL_ITEM_BYTES,
                                                             "max_item_bytes": MAX_ITEM_BYTES}})).hexdigest()
    section, page_size = request["section"], request["page_size"]
    packet = {"schema_version": 2, "operation": "concrete-patch-impact", "request": public_request,
              "report_hash": report_hash, "identity": identity, "status": "uncertain" if groups["uncertainties"] else "inspectable",
              "assessment": {"regression_proven": False, "safe_to_change": None, "executed": False,
                             "counts": {key: len(value) for key, value in groups.items()},
                             "observations": [row.get("observation", "")[:400] for row in groups["findings"][:3]],
                             "recommended_test_ids": [row.get("test_id") for row in groups["recommended_checks"][:3]]},
              "limitations": limitations, "retrieval": {}, "page": {}, "details": {}, "truncated": False}
    available = max_bytes - estimate(packet) - 6000
    if available <= 0:
        raise ReportError("mandatory patch metadata exceeds the response limit")
    paged = {}
    for name, values in groups.items():
        entries, fragmented = _fragment_items(values)
        paged[name] = entries
        packet["retrieval"][name] = {"logical_items": len(values), "entries": len(entries), "fragmented_items": fragmented,
                                   "cursor": cursor(report_hash, name, 0) if entries else None}
    items = paged[section]
    offset = cursor_offset(request["cursor"], report_hash, section, sections=PATCH_SECTIONS)
    if offset > len(items):
        raise ReportError("cursor offset is outside this report; restart inspection")
    returned, size = [], 0
    for item in items[offset:offset + min(page_size, max_rows)]:
        item_size = estimate(item) + 2
        if size + item_size > available:
            break
        returned.append(item)
        size += item_size
    if offset < len(items) and not returned:
        raise ReportError("one patch evidence entry cannot fit the response budget")
    next_offset = offset + len(returned)
    packet["page"] = {"section": section, "offset": offset, "returned": len(returned), "total": len(items),
                      "remaining": len(items) - next_offset, "fragmented_items": packet["retrieval"][section]["fragmented_items"],
                      "next_cursor": cursor(report_hash, section, next_offset) if next_offset < len(items) else None}
    packet["details"] = {"section": section, "items": returned}
    packet["truncated"] = next_offset < len(items) or any(name != section and entries for name, entries in paged.items())
    if estimate(packet) > max_bytes or tool_result_bytes(json.dumps(packet, ensure_ascii=False)) > MCP_WIRE_LIMIT:
        if len(returned) > 1:
            return build_patch_packet(request, identity=identity, groups=groups, limitations=limitations,
                                      max_bytes=max_bytes, max_rows=max(1, len(returned) // 2))
        raise ReportError("patch response cannot fit its protocol budget")
    return packet
