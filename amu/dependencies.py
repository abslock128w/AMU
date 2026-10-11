"""Consumer-first ordering from observed direct dependencies, with cycle groups."""

import heapq


def order(amu_map: dict, nodes: list[dict]) -> tuple[list[dict], set[str], dict]:
    baseline = (amu_map.get("graph_evidence") or {}).get("edge_baselines") or {}
    by_ref = {}
    for node in nodes:
        if node.get("symbol"):
            by_ref.setdefault(f"{node['path']}#{node['symbol']}", []).append(node)
    refs = set(by_ref) | set(baseline)
    adjacency = {ref: set() for ref in refs}
    reverse = {ref: set() for ref in refs}
    covered = {ref for ref, rows in baseline.items() if isinstance(rows, list)}
    skipped = 0
    for ref, rows in baseline.items():
        if not isinstance(rows, list):
            continue
        for edge in rows:
            if (not isinstance(edge, dict) or edge.get("depth") != 1
                    or edge.get("confidence", "sound") != "sound"
                    or edge.get("provenance") == "heuristic"
                    or edge.get("relation") not in {"CALLS", "IMPORTS", "EXTENDS", "INHERITS", "USES_TYPE", "REEXPORTS"}
                    or edge.get("direction") not in {"in", "out"}):
                covered.discard(ref)
                skipped += 1
                continue
            related = edge.get("related")
            if related not in refs or edge.get("target") != ref:
                covered.discard(ref)
                skipped += 1
                continue
            source, target = (related, ref) if edge["direction"] == "in" else (ref, related)
            if any(n.get("confidence") != "sound" for r in (source, target) for n in by_ref.get(r, [])):
                covered.discard(ref)
                skipped += 1
                continue
            adjacency[source].add(target)
            reverse[target].add(source)

    # Iterative Kosaraju avoids recursion depth failures on long impact chains.
    seen, finish = set(), []
    for ref in sorted(refs):
        if ref in seen:
            continue
        seen.add(ref)
        stack = [(ref, iter(sorted(adjacency[ref])))]
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                finish.append(current)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(sorted(adjacency[child]))))
    groups, owner = [], {}
    for ref in reversed(finish):
        if ref in owner:
            continue
        index = len(groups)
        group, stack = [], [ref]
        owner[ref] = index
        while stack:
            current = stack.pop()
            group.append(current)
            for child in sorted(reverse[current]):
                if child not in owner:
                    owner[child] = index
                    stack.append(child)
        groups.append(sorted(group))
    predecessors = {i: set() for i in range(len(groups))}
    successors = {i: set() for i in predecessors}
    for source in refs:
        for target in adjacency[source]:
            a, b = owner[source], owner[target]
            if a != b:
                predecessors[b].add(a)
                successors[a].add(b)
    original_roots = {i for i, parents in predecessors.items() if not parents}
    ready = [(tuple(groups[i]), i) for i in original_roots]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, index = heapq.heappop(ready)
        ordered.append(groups[index])
        for nxt in sorted(successors[index]):
            predecessors[nxt].remove(index)
            if not predecessors[nxt]:
                heapq.heappush(ready, (tuple(groups[nxt]), nxt))
    cycles = [g for g in ordered if len(g) > 1 or g[0] in adjacency[g[0]]]
    leaves = {g[0] for i, g in enumerate(groups) if i in original_roots and len(g) == 1
              and g[0] in covered and g[0] not in adjacency[g[0]]}
    ranked = [node for group in ordered for ref in group for node in by_ref.get(ref, [])]
    ranked_ids = {n["id"] for n in ranked}
    ranked.extend(n for n in nodes if n["id"] not in ranked_ids)
    missing = sorted(set(by_ref) - covered)
    warnings = []
    if missing:
        warnings.append("Some symbols lack complete caller coverage; ordering is partial and they are not claimed as leaves.")
    if skipped:
        warnings.append(f"{skipped} transitive, heuristic, unsupported or incomplete edges excluded from ordering constraints.")
    if cycles:
        warnings.append("Cycle members must migrate as a group; their displayed order is not a dependency order.")
    return ranked, leaves, {"method": "observed_dependency_scc", "groups": ordered, "cycles": cycles,
                            "missing_coverage": missing, "edge_count": sum(map(len, adjacency.values())), "warnings": warnings}
