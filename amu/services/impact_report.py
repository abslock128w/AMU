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
from collections import defaultdict

from amu.harness.context import estimate, rank_rows
from amu.mcp_manifest import MCP_WIRE_LIMIT, tool_result_bytes

SECTIONS = frozenset({"impact", "source_contradictions", "source_uncertainties", "dynamic_boundaries", "coverage", "reasons"})
PATCH_SECTIONS = frozenset({"changes", "findings", "state_dependencies", "recommended_checks", "uncertainties"})
PATCH_REQUEST_SECTIONS = PATCH_SECTIONS | {"summary"}
MAX_CURSOR_BYTES = 1024
FRAGMENT_BYTES = 4096
MAX_ITEM_BYTES = 32 * 1024 * 1024
MAX_NORMAL_ITEM_BYTES = 8192
RETRIEVAL_VERSION = 1
PATCH_SUMMARY_ENTITY_LIMIT = 4
PATCH_SUMMARY_FINDING_LIMIT = 3
PATCH_SUMMARY_CHECK_LIMIT = 3
PATCH_SUMMARY_STATE_LIMIT = 4
PATCH_SUMMARY_UNCERTAINTY_GROUP_LIMIT = 6


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


def _brief_patch_step(step: dict) -> dict:
    brief = {"role": step["role"]}
    location = step.get("location")
    if isinstance(location, dict) and "line" in location:
        brief["line"] = location["line"]
    if step.get("role") == "test_reference":
        brief["reference"] = step.get("reference")
    elif step.get("role") == "source_call":
        brief["caller"] = step.get("caller_id", "").split("#")[-1]
        brief["callee"] = step.get("callee_id", "").split("#")[-1]
    elif step.get("role") in {"source_declaration", "state_access"}:
        brief["entity"] = step.get("entity_id", "").split("#")[-1]
    return brief


def _patch_summary(identity: dict, groups: dict) -> dict:
    """Rank small source-backed previews; raw evidence stays in the five sections."""
    coverage = {row["path"]: row for row in identity.get("file_coverage", [])}
    files = [{"path": row["path"], "operation": row["operation"],
              "file_type": coverage.get(row["path"], {}).get("file_type", "unknown"),
              "analysis": coverage.get(row["path"], {}).get("analysis", "unspecified")}
             for row in identity.get("files", [])]
    changes = [row for row in groups["changes"] if row.get("kind") != "markdown"]
    kinds = {row.get("id"): row.get("kind") for row in changes}
    function_kinds = {"function", "async_function", "method"}
    changed_entities = sorted(changes, key=lambda row: (
        row.get("kind") not in function_kinds, row.get("path", ""), row.get("id", "")))
    entity_preview = [{"id": row.get("id"), "kind": row.get("kind"), "change": row.get("change"),
                       "before_line": (row.get("before_location") or {}).get("line"),
                       "after_line": (row.get("after_location") or {}).get("line")}
                      for row in changed_entities[:PATCH_SUMMARY_ENTITY_LIMIT]]

    categories = {"call": 0, "branch": 1, "return": 2, "exception": 3,
                  "state_access": 4, "state_declaration": 5}
    findings = sorted(groups["findings"], key=lambda row: (
        kinds.get(row.get("entity_id")) not in function_kinds,
        not bool(row.get("recommended_test_ids")),
        categories.get(row.get("category"), 6), row.get("entity_id", ""), row.get("id", "")))
    finding_preview = [{"entity_id": row.get("entity_id"), "category": row.get("category"),
                        "observation": row.get("observation"),
                        "existing_check_count": len(row.get("recommended_test_ids", []))}
                       for row in findings[:PATCH_SUMMARY_FINDING_LIMIT]]

    state_buckets = defaultdict(list)
    for row in groups["state_dependencies"]:
        key = (row.get("state_id", ""), row.get("consumer_id", ""), row.get("access", ""),
               row.get("expression_hash") or "")
        state_buckets[key].append(row)
    state_preview = []
    for key, rows in sorted(state_buckets.items(), key=lambda item: item[0]):
        phases = [phase for phase in ("before", "after") if any(row.get("phase") == phase for row in rows)]
        samples = [{"phase": phase, "line": (next(row.get("location") for row in rows if row.get("phase") == phase) or {}).get("line")}
                   for phase in phases]
        state_preview.append({"state_id": key[0], "consumer_id": key[1], "access": key[2],
                              "phases": phases, "count": len(rows), "samples": samples})

    relationship_rank = {"source-member-path": 0, "source-state-path": 0,
                         "direct-source-reference": 0, "source-call-path": 0,
                         "broad-class-reference": 1, "broad-source-reference": 1}
    ranked_checks = []
    for row in groups["recommended_checks"]:
        associations = sorted(row.get("associations", []), key=lambda item: (
            relationship_rank.get(item.get("relationship"), 2), item.get("entity_id", ""),
            item.get("relationship", "")))
        best = associations[0] if associations else {}
        rank = relationship_rank.get(best.get("relationship"), 2)
        ranked_checks.append((rank, row.get("test_id", ""), row, best))
    ranked_checks.sort(key=lambda item: (item[0], item[1]))
    check_preview = [{"test_id": row.get("test_id"),
                      "kind": "supported-source-path" if rank == 0 else "broad-candidate" if rank == 1 else "unresolved",
                      "relationship": best.get("relationship"), "entity_id": best.get("entity_id"),
                      "path": [_brief_patch_step(step) for step in best.get("path", [])],
                      "verification": row.get("verification"), "coverage_proven": False}
                     for rank, _, row, best in ranked_checks[:PATCH_SUMMARY_CHECK_LIMIT]]

    uncertainty_buckets = defaultdict(list)
    for row in groups["uncertainties"]:
        reason = row.get("reason") or f"{row.get('kind', 'other')} evidence"
        uncertainty_buckets[reason].append(row)
    uncertainty_groups = []

    def phase_of(row):
        return row.get("phase") if row.get("phase") in {"before", "after"} else "unphased"

    for reason, rows in sorted(uncertainty_buckets.items(), key=lambda item: (-len(item[1]), item[0])):
        phase_counts = {phase: sum(phase_of(row) == phase for row in rows)
                        for phase in ("before", "after", "unphased")}
        phase_counts = {phase: count for phase, count in phase_counts.items() if count}
        path_counts = defaultdict(int)
        for row in rows:
            path_counts[row.get("path") or "(unscoped)"] += 1
        ranked_paths = sorted(path_counts, key=lambda path: (-path_counts[path], path))
        shown_paths = ranked_paths[:4]
        samples = []
        for path in shown_paths:
            for phase in ("before", "after", "unphased"):
                candidates = [row for row in rows if (row.get("path") or "(unscoped)") == path
                              and phase_of(row) == phase]
                if candidates and len(samples) < 4:
                    first = min(candidates, key=lambda row: (
                        (row.get("location") or {}).get("line", 0),
                        (row.get("location") or {}).get("column", 0), _encode(row)))
                    sample = {"phase": phase, "path": path}
                    if isinstance(first.get("location"), dict):
                        sample["line"] = first["location"].get("line")
                    samples.append(sample)
        presence = ("before-and-after" if "before" in phase_counts and "after" in phase_counts
                    else "after-only" if "after" in phase_counts else "before-only" if "before" in phase_counts
                    else "unphased")
        uncertainty_groups.append({"reason": reason, "count": len(rows), "phase_counts": phase_counts,
                                   "path_counts": {path: path_counts[path] for path in shown_paths},
                                   "omitted_paths": len(ranked_paths) - len(shown_paths),
                                   "omitted_path_occurrences": sum(path_counts[path] for path in ranked_paths[4:]),
                                   "presence": presence,
                                   "samples": samples, "omitted_samples": len(rows) - len(samples)})

    scope = identity.get("recommendation_scope", {})
    if ranked_checks:
        search = "Existing checks are baseline test references with located source paths or broad candidates; references do not prove executed coverage or assertions."
    elif not any(row.get("file_type") == "python" for row in files):
        search = "No Python entity changed; existing Python checks were not inferred for documentation-only content."
    else:
        gaps = scope.get("unresolved_package_imports") or []
        limits = scope.get("local_call_limits") or []
        unavailable = scope.get("unavailable_baseline_tests") or []
        incomplete = identity.get("test_inventory_complete") is False or bool(limits or unavailable)
        search = ("Search incomplete; no candidates found in the inspected portion. " if incomplete else
                  "No candidates found within the completed bounded search. ")
        search += (f"No supported direct test reference or at-most-two-hop local direct-call path to a changed Python entity "
                   f"was found in {scope.get('baseline_test_files_inspected', 0)} parsed baseline Python test files. "
                   "Dynamic command dispatch, subprocess calls, and assertions are outside this search. "
                   "Package-submodule paths require an unchanged inert initializer and one unambiguous repository module path.")
        if gaps:
            search += f" {len(gaps)} package-submodule relationship(s) remain unresolved; first: {gaps[0]['reason']}."
        if limits:
            search += f" {len(limits)} local-call search limit(s) were reached."
        if unavailable:
            search += f" {len(unavailable)} baseline test file(s) could not be parsed."
        if identity.get("test_inventory_complete") is False:
            search += " The test inventory was incomplete."
    added_tests = identity.get("candidate_added_test_files") or []
    if added_tests:
        search += f" {len(added_tests)} candidate-added test file(s) were excluded from baseline discovery."

    return {"changed_files": files, "changed_entities": entity_preview,
            "omitted_changed_entities": len(changed_entities) - len(entity_preview),
            "findings": finding_preview, "omitted_findings": len(findings) - len(finding_preview),
            "state_relationships": state_preview[:PATCH_SUMMARY_STATE_LIMIT],
            "omitted_state_relationships": len(state_preview) - min(len(state_preview), PATCH_SUMMARY_STATE_LIMIT),
            "existing_checks": check_preview,
            "omitted_existing_checks": len(ranked_checks) - len(check_preview),
            "candidate_test_files": identity.get("candidate_test_files", []),
            "candidate_existing_test_files": identity.get("candidate_existing_test_files", []),
            "candidate_added_test_files": added_tests,
            "recommendation_search": search,
            "uncertainty_groups": uncertainty_groups[:PATCH_SUMMARY_UNCERTAINTY_GROUP_LIMIT],
            "uncertainty_group_count": len(uncertainty_groups),
            "omitted_uncertainty_groups": len(uncertainty_groups) - min(len(uncertainty_groups), PATCH_SUMMARY_UNCERTAINTY_GROUP_LIMIT),
            "omitted_uncertainty_occurrences": sum(row["count"] for row in uncertainty_groups[PATCH_SUMMARY_UNCERTAINTY_GROUP_LIMIT:]),
            "other_uncertainty_group_counts": [{"reason": row["reason"], "count": row["count"]}
                                               for row in uncertainty_groups[PATCH_SUMMARY_UNCERTAINTY_GROUP_LIMIT:]],
            "raw_uncertainty_count": len(groups["uncertainties"]),
            "note": "Previews omit detail; use retrieval cursors for raw evidence. Uncertainty in either phase is not proof of a new bug."}


def build_patch_packet(request: dict, *, identity: dict, groups: dict, limitations: list[str],
                       max_bytes: int, max_rows: int) -> dict:
    """Use the same lossless fragments and freshness-bound cursors as intent reports."""
    if set(groups) != PATCH_SECTIONS:
        raise ReportError("invalid patch evidence sections")
    if request["section"] not in PATCH_REQUEST_SECTIONS:
        raise ReportError("invalid patch report section")
    if request["section"] == "summary" and request["cursor"] is not None:
        raise ReportError("summary has no cursor; choose an evidence section for retrieval")
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
    public_identity = identity if section != "summary" else {
        **{key: identity[key] for key in ("repository", "baseline_kind", "git_head", "files", "file_coverage",
                                           "test_sources_sha256", "test_inventory_complete") if key in identity},
        "test_source_count": len(identity.get("test_sources", [])),
    }
    assessment = {"regression_proven": False, "safe_to_change": None, "executed": False,
                  "counts": {key: len(value) for key, value in groups.items()}}
    if section != "summary":
        assessment.update({"observations": [row.get("observation", "")[:400] for row in groups["findings"][:3]],
                           "recommended_test_ids": [row.get("test_id") for row in groups["recommended_checks"][:3]],
                           "recommendation_relationships": [
                               {"test_id": row.get("test_id"),
                                "relationships": sorted({a["relationship"] for a in row.get("associations", [])})}
                               for row in groups["recommended_checks"][:3]]})
    packet = {"schema_version": 3, "operation": "concrete-patch-impact", "request": public_request,
              "report_hash": report_hash, "identity": public_identity, "status": "uncertain" if groups["uncertainties"] else "inspectable",
              "assessment": assessment,
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
    if section == "summary":
        packet["summary"] = _patch_summary(identity, groups)
        packet["page"] = {"section": "summary", "offset": 0, "returned": 0, "total": 0,
                          "remaining": 0, "fragmented_items": 0, "next_cursor": None}
        packet["details"] = {"section": "summary", "items": []}
        packet["truncated"] = any(paged.values())
        if estimate(packet) > max_bytes or tool_result_bytes(json.dumps(packet, ensure_ascii=False)) > MCP_WIRE_LIMIT:
            raise ReportError("patch summary cannot fit its protocol budget; retrieve a narrower evidence section")
        return packet
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
