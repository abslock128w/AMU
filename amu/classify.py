from amu import relations


def classify_consumers(symbol: str, raw_impact_output: str, caps: dict) -> dict:
    """
    Classifies consumers into will_break, might_break, and unknown.
    Satisfies Track 2 Curveball requirements by flagging unresolvable/partial analysis explicitly.
    """
    buckets = {
        "will_break": [],
        "might_break": [],
        "unknown": {
            "unresolved_callsites": 0,
            "dynamic_dispatch_sites": 0,
            "out_of_coverage": caps.get("out_of_coverage", []),
            "notes": ["Dynamic dispatch or reflection patterns treated as heuristic/incomplete."]
        }
    }

    if raw_impact_output.startswith("UNSAFE_LEGACY_GRAPH:"):
        buckets["unknown"]["unresolved_callsites"] = 1
        buckets["unknown"]["notes"].append(
            "Raw impact compatibility output is retired; it cannot classify consumers for an edit plan."
        )
        return buckets

    lines = raw_impact_output.splitlines()
    for line in lines:
        if "hop: 1" in line or "direct" in line.lower():
            buckets["might_break"].append({"symbol": symbol, "reason": "Potential direct caller; compatibility not assessed"})
        elif "hop: 2" in line or "transitive" in line.lower():
            buckets["might_break"].append({"symbol": symbol, "reason": "Transitive hop-2 consumer"})
        elif "unresolved" in line.lower() or "dynamic" in line.lower():
            buckets["unknown"]["unresolved_callsites"] += 1

    return buckets


# ---------------------------------------------------------------------------
# Map builder (PRD S2): confidence axis + feature axis + verify lines.
# Rules: resolved graph edge ⇒ sound; co-change / name / sibling ⇒ guessed;
#        unresolved call site / partial parse / dynamic dispatch ⇒ unknown.
# ---------------------------------------------------------------------------

_EDGE_SECTIONS = ("callers", "callees", "type_consumers", "data_flows")
_GUESS_SECTIONS = ("co_changes", "siblings")
_RIPPLE_SECTIONS = _EDGE_SECTIONS + _GUESS_SECTIONS
_EDGE_SECTION_DIRECTIONS = {"callers": "in", "callees": "out", "type_consumers": "in"}


def _text(value) -> str | None:
    """Return a non-empty graph field as text without guessing at malformed data."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _entries(impact: dict, section: str) -> list[dict]:
    """Read a graph-impact entry list defensively; unsupported shapes carry no edge evidence."""
    group = impact.get(section) or {}
    entries = group.get("entries") if isinstance(group, dict) else None
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _depth(value) -> int:
    """Keep graph-provided depths when valid; use one only as the documented direct-edge default."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return 1


def _unresolved_patterns(caps: dict | None) -> list[str]:
    """Normalize capability limits so their display and evidence are stable across graph output order."""
    raw = (caps or {}).get("unresolved_patterns", []) if isinstance(caps, dict) else []
    return sorted({_text(pattern) for pattern in raw if _text(pattern)}) if isinstance(raw, list) else []


def _ripples_from_impacts(root_file: str, impacts: dict[str, dict]) -> list[dict]:
    """Return graph-backed ripple records only when an impact entry names a concrete endpoint.

    A ripple is deliberately separate from a map node: it preserves the relation-level
    evidence (including direction and call site) without claiming that every graph edge
    is an edit target.  Heuristic co-change/sibling records remain guessed rather than
    being upgraded to sound evidence.
    """
    ripples: list[dict] = []
    seen: set[tuple] = set()

    for raw_symbol in sorted(impacts, key=lambda value: str(value)):
        impact = impacts[raw_symbol]
        if not isinstance(impact, dict) or not impact or impact.get("error"):
            continue
        symbol = _text(raw_symbol) if isinstance(raw_symbol, str) else str(raw_symbol)
        for section in _RIPPLE_SECTIONS:
            is_resolved_edge = section in _EDGE_SECTIONS
            for entry in _entries(impact, section):
                endpoint = entry.get("endpoint")
                if not isinstance(endpoint, dict) and not is_resolved_edge:
                    endpoint = entry
                if not isinstance(endpoint, dict):
                    continue
                path = _text(endpoint.get("file_path"))
                target_symbol = _text(endpoint.get("qualified_name")) or _text(endpoint.get("name"))
                if not path and not target_symbol:
                    continue

                relation = _text(entry.get("relation")) or section.upper()
                # Entire may omit the redundant direction for callers,
                # callees, and type-consumers.  Keep map presentation aligned
                # with the strict refresh normalizer instead of dropping a
                # real relation later just because the display ripple carried
                # ``None``.  DATA_FLOWS remains explicit because it can point
                # either way.
                direction = _text(entry.get("direction")) or _EDGE_SECTION_DIRECTIONS.get(section)
                depth = _depth(entry.get("depth")) if is_resolved_edge else None
                site = entry.get("call_site")
                via = None
                if isinstance(site, dict):
                    site_path = _text(site.get("file_path"))
                    line = site.get("line")
                    via = f"{site_path}:{line}" if site_path and isinstance(line, int) else site_path
                relation_evidence = relations.evidence(entry, heuristic=not is_resolved_edge)
                confidence = relation_evidence["confidence"]
                reason = (f"resolved {relation} edge at depth {depth}" if is_resolved_edge
                          else f"{section.replace('_', '-')} only, no resolved edge")
                evidence = f"impact:{section}"
                key = (root_file, symbol, path, target_symbol, relation, direction, depth, confidence, evidence, via)
                if key in seen:
                    continue
                seen.add(key)
                ripples.append({
                    "source": {"path": root_file, "symbol": symbol},
                    "endpoint": {"path": path, "symbol": target_symbol},
                    "relation": relation,
                    "direction": direction,
                    "depth": depth,
                    "confidence": confidence,
                    "provenance": relation_evidence["provenance"],
                    "reason": reason,
                    "via": via,
                    "evidence": [evidence],
                })

    return sorted(
        ripples,
        key=lambda ripple: (
            ripple["source"]["path"], ripple["source"]["symbol"] or "",
            ripple["evidence"][0], ripple["depth"] if ripple["depth"] is not None else -1,
            ripple["relation"], ripple["direction"] or "",
            ripple["endpoint"]["path"] or "", ripple["endpoint"]["symbol"] or "", ripple["via"] or "",
        ),
    )


def _feature_for(path: str, feature_map: dict) -> tuple[str, str]:
    for feature, globs in (feature_map or {}).items():
        for g in globs:
            prefix = g.rstrip("*/")
            if path.startswith(prefix):
                return feature, "map"
    top = path.split("/")[0] if "/" in path else "root"
    return top.capitalize(), "derived"


def _verify_for(path: str, symbol: str | None, node_id: str, test_cmd: str) -> str:
    if "test" in path.split("/")[0] or path.rsplit("/", 1)[-1].startswith("test_"):
        target = f"{path}::{symbol.split('.')[-1]}" if symbol else path
        # Keep the complete configured command.  Dropping ``-m pytest`` (or
        # runner flags) made generated verification commands impossible to
        # execute for otherwise supported Python configurations.
        return f"{test_cmd.strip() or 'pytest'} {target}"
    return f"amu verify --node {node_id}"


def build_map(root_file: str, root_symbols: list[str], impacts: dict[str, dict], change: str,
              feature_map: dict | None, caps: dict, depth: int, test_cmd: str = "pytest") -> dict:
    """Turn `entire graph impact --format json` payloads (one per root symbol) into the PRD map object."""
    nodes: list[dict] = []
    seen: dict[str, int] = {}
    unresolved_sites = 0
    partial_files: list[str] = []
    partial_unknown_files = 0
    n = 0

    def add(path, symbol, confidence, risk, via, reason, evidence, order):
        nonlocal n
        key = f"{path}#{symbol}"
        if key in seen:
            return False
        n += 1
        nid = f"n{n}"
        seen[key] = n
        feature, src = _feature_for(path, feature_map)
        nodes.append({
            "id": nid, "path": path, "symbol": symbol, "confidence": confidence, "risk": risk,
            "feature": feature if src == "map" else f"{feature} (derived)", "order": order,
            "action": "edit" if risk == "will_break" else ("review" if risk == "might_break" else "ask"),
            "via": via, "reason": reason, "verify": _verify_for(path, symbol, nid, test_cmd), "evidence": evidence,
            "compatibility": relations.unassessed_compatibility(),
        })
        return True

    for sym, imp in impacts.items():
        if not isinstance(imp, dict) or not imp or "error" in imp:
            unresolved_sites += 1
            add(root_file, sym, "unknown", "unknown", None,
                f"impact query failed: {imp.get('error', 'no data') if isinstance(imp, dict) else 'invalid data'}",
                ["impact:error"], 99)
            continue
        for section in _EDGE_SECTIONS:
            for e in _entries(imp, section):
                ep = e.get("endpoint")
                if not isinstance(ep, dict):
                    continue
                path = _text(ep.get("file_path"))
                symbol = _text(ep.get("qualified_name")) or _text(ep.get("name"))
                if not path and not symbol:
                    continue
                rel = _text(e.get("relation")) or section.upper()
                d = _depth(e.get("depth"))
                relation_evidence = relations.evidence(e)
                risk = "unknown" if relation_evidence["confidence"] == "unknown" else "might_break"
                site = e.get("call_site")
                if isinstance(site, dict):
                    site_path = _text(site.get("file_path"))
                    via = f"{rel}:{site_path}:{site.get('line')}" if site_path else f"{rel}:depth{d}"
                else:
                    via = f"{rel}:depth{d}"
                add(path or "(re-export, no file)", symbol, relation_evidence["confidence"], risk, via,
                    f"reported {rel} edge at depth {d}; potentially affected, compatibility not assessed",
                    [f"impact:{section}"], d)
        for section in _GUESS_SECTIONS:
            for e in _entries(imp, section):
                ep = e.get("endpoint") if isinstance(e.get("endpoint"), dict) else e
                path = _text(ep.get("file_path")) or _text(e.get("file_path"))
                symbol = _text(ep.get("qualified_name")) or _text(ep.get("name")) or _text(e.get("name"))
                if not path and not symbol:
                    continue
                add(path or "?", symbol, "guessed", "might_break", None,
                    f"{section.replace('_', '-')} only, no resolved edge", [f"impact:{section}"], 3)
        if imp.get("disambiguation_required"):
            defs = imp.get("definitions") or []
            unresolved_sites += 1
            add(root_file, sym, "unknown", "unknown", None,
                f"ambiguous: {len(defs)} definitions of {sym} ({', '.join(d.get('file_path', '?') for d in defs[:3])}); no edges returned",
                ["impact:disambiguation_required"], 99)
        # Repo-wide parse failures are honesty context, not blast radius: only syntax-error files in the root's own
        # package *and* language become unknown nodes (they could hide a caller); the rest are counted in the qualifier.
        root_pkg = root_file.split("/")[0]
        root_ext = root_file.rsplit(".", 1)[-1] if "." in root_file else ""
        for pf in imp.get("partial_failures", []) or []:
            fp = pf.get("file_path")
            if not fp or fp in partial_files or fp.startswith("docs/evidence"):
                continue
            partial_files.append(fp)
            if pf.get("code") == "E_PARSE_ERROR" and fp.split("/")[0] == root_pkg and fp.rsplit(".", 1)[-1] == root_ext:
                if add(fp, None, "unknown", "unknown", None,
                       f"{pf.get('code')}: {pf.get('effect_on_semantic_completeness', 'not parsed')}",
                       ["impact:partial_failures"], 98):
                    partial_unknown_files += 1

    # Capability output is evidence of a graph boundary, even when the graph has
    # not returned a concrete call site.  Keep it out of ``nodes``: capability
    # patterns are global limits, not proof that this target has an unknown
    # consumer, so they must not turn an otherwise actionable map into a block.
    patterns = _unresolved_patterns(caps)
    limitations = [
        {
            "kind": "unresolved_pattern",
            "pattern": pattern,
            "reason": "graph capability reports this unresolved pattern; no concrete affected callsite was returned",
            "evidence": f"capability:unresolved_patterns:{pattern}",
        }
        for pattern in patterns
    ]

    counts = {c: sum(1 for x in nodes if x["confidence"] == c) for c in ("sound", "guessed", "unknown")}
    features: dict[str, int] = {}
    for x in nodes:
        features[x["feature"]] = features.get(x["feature"], 0) + 1
    files = {x["path"] for x in nodes}
    unparsed_note = f"{len(partial_files)} files unparsed repo-wide (not counted as nodes)" if partial_files else "0 files unparsed repo-wide"
    capability_note = (f"capability limits: {', '.join(patterns)} (no concrete affected callsite was returned)"
                       if patterns else "no unresolved capability patterns reported")
    if counts["unknown"]:
        qualifier = (f"{unresolved_sites} unresolved impact queries, {partial_unknown_files} syntax-error files in "
                     f"{root_file.split('/')[0]}/; {capability_note}; {unparsed_note}")
    else:
        qualifier = (f"graph reported 0 unresolved sites at depth {depth}; dynamic dispatch and files outside parser "
                     f"coverage ({', '.join(((caps or {}).get('out_of_coverage') or [])[:3]) or 'none listed'}) are not counted; "
                     f"{capability_note}; {unparsed_note}")
    first_target = root_symbols[0] if root_symbols else root_file
    return {
        "root": {"file": root_file, "exports": len(root_symbols), "internal": 0,
                 "feature": _feature_for(root_file, feature_map)[0], "feature_source": _feature_for(root_file, feature_map)[1]},
        "change": change, "depth": depth, "nodes": nodes,
        "ripples": _ripples_from_impacts(root_file, impacts), "features": features,
        "radius": {"files": len(files), "packages": len({p.split('/')[0] for p in files}), "exports": len(root_symbols),
                   "consumers": counts["sound"] + counts["guessed"], "tests": sum(1 for x in nodes if x["verify"].startswith(test_cmd.split()[0]))},
        "resolution": {**counts, "limitations": limitations, "qualifier": qualifier},
        "next": f"amu plan --targets {root_file}#{first_target}:{change} --approve",
    }
