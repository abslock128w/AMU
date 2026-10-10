"""`amu graph`: a relation graph payload from `entire graph impact`, rendered as ASCII, Graphviz dot, or Mermaid.
Deterministic: nodes and edges are sorted, so the same input always draws the same picture."""

from __future__ import annotations

import hashlib
import re

from amu import graph_refresh, relations
from amu.render import GLYPH

_EDGE = {"callers": ("in", "sound"), "callees": ("out", "sound"), "type_consumers": ("in", "sound"), "data_flows": ("in", "sound"),
         "co_changes": ("side", "guessed"), "siblings": ("side", "guessed")}


def payload_from_impact(
    symbol: str,
    imp: dict,
    relation: str | None = None,
    *,
    source_contradictions: list[dict] | None = None,
    source_uncertainties: list[dict] | None = None,
) -> dict:
    """Render an impact response and preserve any source-proven exclusions.

    ``source_contradictions`` is deliberately part of the display payload.
    Otherwise a caller can remove a false provider edge and accidentally make
    the resulting graph look complete and independently verified.
    """
    focus = imp.get("focus") or {}
    fid = focus.get("id") or symbol
    nodes = {fid: {"id": fid, "name": focus.get("qualified_name") or focus.get("name") or symbol, "path": focus.get("file_path") or "?",
                   "kind": focus.get("kind") or "?", "confidence": "sound" if focus.get("id") else "unknown", "section": "focus",
                   "reason": "the target" if focus.get("id") else "graph returned no focus (ambiguous or not found)",
                   "verify": f"entire graph def {symbol}"}}
    edges = []
    for section, (direction, conf) in _EDGE.items():
        for e in (imp.get(section) or {}).get("entries") or []:
            relation_evidence = relations.evidence(e, heuristic=section in {"co_changes", "siblings"})
            conf = relation_evidence["confidence"]
            ep = e.get("endpoint") or e
            rel = e.get("relation") or section.upper()
            # ``DATA_FLOWS`` can legitimately be returned in either
            # direction by Entire.  Keep the explicit provider orientation so
            # the rendered graph agrees with a refresh delta; older recorded
            # payloads without a direction retain the section default.
            raw_direction = e.get("direction")
            edge_direction = raw_direction if raw_direction in {"in", "out"} else direction
            if relation and rel != relation:
                continue
            nid = ep.get("id") or f"{ep.get('file_path')}#{ep.get('name')}"
            depth = e.get("depth", 1)
            nodes.setdefault(nid, {"id": nid, "name": ep.get("qualified_name") or ep.get("name") or "?", "path": ep.get("file_path") or "?",
                                   "kind": ep.get("kind") or "?", "confidence": conf, "section": section, "depth": depth,
                                   "reason": f"resolved {rel} edge at depth {depth}" if conf == "sound" else f"{section.replace('_', '-')} only, no resolved edge",
                                   "verify": f"entire graph neighbors --symbol {ep.get('name')} --relation {rel}"})
            src, dst = (nid, fid) if edge_direction == "in" else (fid, nid)
            edges.append({"from": src, "to": dst, "relation": rel, "direction": edge_direction, "section": section, "depth": depth,
                          **relation_evidence})
    candidates = [{"kind": d.get("kind"), "path": d.get("file_path"), "name": d.get("qualified_name") or d.get("name"),
                   "next": f"amu graph {d.get('file_path')}#{d.get('qualified_name') or d.get('name')}"} for d in imp.get("definitions") or []]
    if imp.get("disambiguation_required"):
        nodes[fid]["confidence"] = "unknown"
        nodes[fid]["reason"] = f"ambiguous: {len(candidates)} definitions match '{symbol}' ({'fuzzy ' + imp['fuzzy_match_kind'] if imp.get('fuzzy_match') else 'exact'}); no edges returned"
        nodes[fid]["verify"] = candidates[0]["next"] if candidates else f"entire graph search --query '{symbol}'"
    seen = set()
    uniq = []
    for e in sorted(edges, key=lambda e: (e["section"], e["depth"], e["from"], e["to"], e["relation"])):
        # A relationship at depth one and the same relationship reached again
        # at depth two are distinct impact evidence.  Collapsing them here
        # made post-edit refreshes report the depth-two edge as "removed" even
        # when the provider returned it live.
        k = (e["from"], e["to"], e["relation"], e["direction"], e["section"], e["depth"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    contradictions = [dict(row) for row in source_contradictions or []]
    uncertainties = [dict(row) for row in source_uncertainties or []]
    warnings = [w.get("code") for w in imp.get("warnings") or []]
    if contradictions:
        warnings.append(
            f"source AST excluded {len(contradictions)} contradicted direct Python CALLS edge(s); "
            "use map and refresh before approving edits"
        )
    if uncertainties:
        warnings.append(
            f"source AST could not resolve {len(uncertainties)} direct Python CALLS edge(s) to exact endpoints; "
            "the graph is candidate-only"
        )
    return {"focus": fid, "symbol": symbol, "depth": imp.get("depth", 2),
            "nodes": sorted(nodes.values(), key=lambda n: (n["section"] != "focus", n["section"], n.get("depth", 0), n["name"], n["path"])),
            "edges": uniq, "counts": {s: len([e for e in uniq if e["section"] == s]) for s in _EDGE},
            "status": "ambiguous" if imp.get("disambiguation_required") else ("not_found" if not focus.get("id") else "ok"),
            "candidates": candidates if imp.get("disambiguation_required") else [],
            "warnings": warnings, "source_contradictions": contradictions, "source_uncertainties": uncertainties}


def payload_from_live_impact(
    repo: str,
    symbol: str,
    imp: dict,
    relation: str | None = None,
    *,
    target: str | None = None,
    capture_before: dict | None = None,
) -> dict:
    """Render a live provider graph without exposing source-proven false calls.

    This is the single safe path for interactive graph displays.  An exact
    ``path#symbol`` target also gets the same pagination/direction check used
    by refresh. Maps and refreshes remain the only paths that can authorize a
    lifecycle step.
    """
    from amu import graph_readiness, graph_refresh, source_evidence

    filtered_rows, contradictions, uncertainties = source_evidence.filter_python_call_evidence(repo, {symbol: imp})
    filtered = filtered_rows.get(symbol)
    if not isinstance(filtered, dict):
        filtered = {"warnings": [{"code": "malformed_graph_response"}]}
    identity = target or symbol
    evidence = graph_readiness.with_source_call_evidence(
        graph_readiness.live_target_evidence(identity, filtered), contradictions, uncertainties
    )
    if target:
        strict_edges = graph_refresh.live_edges(target, filtered)
        if not strict_edges.available:
            evidence = {
                **evidence,
                "approvable": False,
                "reasons": [*list(evidence.get("reasons") or []), strict_edges.reason or "live graph edges are incomplete"],
            }
    else:
        evidence = {
            **evidence,
            "approvable": False,
            "reasons": [
                *list(evidence.get("reasons") or []),
                "graph display target is not an exact path#symbol",
            ],
        }
    capture_after = live_worktree_capture(repo)
    capture_reason = _live_capture_reason(capture_before, capture_after)
    evidence["worktree"] = {"before": capture_before, "after": capture_after, "stable": capture_reason is None}
    if capture_reason:
        evidence = {
            **evidence,
            "approvable": False,
            "reasons": [*list(evidence.get("reasons") or []), capture_reason],
        }
    payload = payload_from_impact(
        symbol, filtered, relation, source_contradictions=contradictions, source_uncertainties=uncertainties
    )
    payload["graph_evidence"] = evidence
    if not evidence["approvable"]:
        # A resolved edge is still useful to investigate, but a truncated,
        # ambiguous, or source-contradicted response must never look like a
        # complete green graph in the terminal, MCP response, or web viewer.
        # Keep the underlying relationships while making their presentation
        # explicitly candidate-only.
        if payload["status"] == "ok":
            payload["status"] = "incomplete"
        for node in payload["nodes"]:
            if node["confidence"] == "sound":
                node["confidence"] = "unknown"
                node["reason"] = f"candidate only: {node['reason']}"
        for edge in payload["edges"]:
            edge["confidence"] = "unknown"
        payload["warnings"].append(f"graph evidence is not approvable: {graph_readiness.summary(evidence)}")
    return payload


def live_worktree_capture(repo: str) -> dict:
    """Capture the source state immediately before or after a live graph query."""
    from amu import state

    head_before = state.git_head(repo)
    worktree = graph_refresh.graph_capture(repo)
    head_after = state.git_head(repo)
    return {"head_before": head_before, "head_after": head_after, "worktree": worktree}


def _live_capture_reason(before: object, after: dict) -> str | None:
    """A display query is candidate-only unless its source bracket is stable."""
    if not isinstance(before, dict):
        return "graph display was not captured around the live provider query"
    for capture, label in ((before, "before"), (after, "after")):
        worktree = capture.get("worktree") if isinstance(capture, dict) else None
        if not isinstance(worktree, dict) or worktree.get("error") or not worktree.get("digest"):
            return f"could not fingerprint worktree {label} graph query"
        if capture.get("head_before") != capture.get("head_after"):
            return f"Git revision changed while capturing {label} graph query"
    if before["head_after"] != after["head_before"]:
        return "Git revision changed during the live graph query"
    if not graph_refresh.relation_capture_matches(before["worktree"], after["worktree"]):
        return "worktree changed during the live graph query"
    return None


def _label(n: dict) -> str:
    return f"{GLYPH[n['confidence']]} {n['name']}  {n['path']}"


def _integrity_warning(p: dict) -> str | None:
    """Return a portable warning for rendered formats that lose JSON fields."""
    evidence = p.get("graph_evidence")
    if isinstance(evidence, dict) and evidence.get("approvable") is True:
        return None
    reasons = evidence.get("reasons") if isinstance(evidence, dict) else None
    detail = "; ".join(str(reason) for reason in reasons[:3]) if isinstance(reasons, list) and reasons else (
        "no verified graph evidence is embedded in this rendered artifact"
    )
    return f"NOT APPROVAL-GRADE: {detail}"


def to_ascii(p: dict) -> str:
    by = {n["id"]: n for n in p["nodes"]}
    focus = by[p["focus"]]
    above = [n for n in p["nodes"] if n["section"] in ("callers",)]
    below = [n for n in p["nodes"] if n["section"] == "callees"]
    types = [n for n in p["nodes"] if n["section"] == "type_consumers"]
    flows = [n for n in p["nodes"] if n["section"] == "data_flows"]
    guessed = [n for n in p["nodes"] if n["section"] in ("co_changes", "siblings")]
    rel_of = {(e["from"], e["to"]): e["relation"] for e in p["edges"]}
    out = []
    warning = _integrity_warning(p)
    if warning:
        out.append(warning)
    if above:
        out.append("callers")
        for n in above:
            out.append(f"  {_label(n)}")
            out.append(f"      │ {rel_of.get((n['id'], focus['id']), 'CALLS')} (depth {n.get('depth', 1)})")
        out.append("      ▼")
    out.append(f"┌─ {_label(focus)} ─┐  [{focus['kind']}] {focus['reason']}")
    if p.get("candidates"):
        out.append("candidates (pick one — amu never guesses a symbol)")
        for c in p["candidates"]:
            out.append(f"  {c['kind']:<9} {c['path']}#{c['name']}   → {c['next']}")
    if types or flows:
        for n in types:
            out.append(f"  ◀─ {rel_of.get((n['id'], focus['id']), 'USES_TYPE')} ── {_label(n)}")
        for n in flows:
            out.append(f"  ◀─ {rel_of.get((n['id'], focus['id']), 'DATA_FLOWS')} ── {_label(n)}")
    if below:
        out.append("      ▼")
        out.append("callees")
        for n in below:
            out.append(f"      │ {rel_of.get((focus['id'], n['id']), 'CALLS')}")
            out.append(f"  {_label(n)}")
    if guessed:
        out.append("guessed (no resolved edge)")
        for n in guessed:
            out.append(f"  {_label(n)}  — {n['reason']} · verify: {n['verify']}")
    c = p["counts"]
    out.append(f"edges  callers {c['callers']} · callees {c['callees']} · type {c['type_consumers']} · data {c['data_flows']} · co-change {c['co_changes']} · siblings {c['siblings']}")
    return "\n".join(out)


def _mid(s: str) -> str:
    """Mermaid/dot-safe id: readable tail + short hash so two long ids with the same tail never collide."""
    return "n_" + re.sub(r"[^A-Za-z0-9]", "_", s)[-32:] + "_" + hashlib.sha1(s.encode()).hexdigest()[:6]


def to_mermaid(p: dict) -> str:
    lines = ["graph TD"]
    warning = _integrity_warning(p)
    if warning:
        lines.append(f"    %% {warning}")
    for n in p["nodes"]:
        label = f"{GLYPH[n['confidence']]} {n['name']}<br/>{n['path']}".replace('"', "'")
        shape = ("[[", "]]") if n["id"] == p["focus"] else (("(", ")") if n["confidence"] == "guessed" else ("[", "]"))
        lines.append(f'    {_mid(n["id"])}{shape[0]}"{label}"{shape[1]}')
    for e in p["edges"]:
        lines.append(f'    {_mid(e["from"])} -->|{e["relation"]}| {_mid(e["to"])}')
    lines.append("    classDef sound stroke:#3CB371;\n    classDef guessed stroke:#E08A1E,stroke-dasharray:4;\n    classDef unknown stroke:#8A8578;")
    for conf in ("sound", "guessed", "unknown"):
        ids = [_mid(n["id"]) for n in p["nodes"] if n["confidence"] == conf]
        if ids:
            lines.append(f"    class {','.join(ids)} {conf};")
    return "\n".join(lines)


def to_dot(p: dict) -> str:
    col = {"sound": "#3CB371", "guessed": "#E08A1E", "unknown": "#8A8578"}
    lines = ["digraph amu {", '  rankdir=TB; node [shape=box, fontname="monospace"]; bgcolor="#161412"; fontcolor="#FDF6E3";']
    warning = _integrity_warning(p)
    if warning:
        safe_warning = warning.replace('"', "'").replace("\\", "/")
        lines.append(f'  labelloc="t"; label="{safe_warning}";')
    for n in p["nodes"]:
        pen = 3 if n["id"] == p["focus"] else 1
        lines.append(f'  "{_mid(n["id"])}" [label="{GLYPH[n["confidence"]]} {n["name"]}\\n{n["path"]}", color="{col[n["confidence"]]}", fontcolor="#FDF6E3", penwidth={pen}];')
    for e in p["edges"]:
        style = "dashed" if e["section"] in ("co_changes", "siblings") else "solid"
        lines.append(f'  "{_mid(e["from"])}" -> "{_mid(e["to"])}" [label="{e["relation"]}", style={style}, color="#E08A1E", fontcolor="#8A8578"];')
    lines.append("}")
    return "\n".join(lines)
