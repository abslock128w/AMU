"""Which tests to run, by subtraction rather than by ranking.

The candidate set is every test in a test file within the file-level import closure of the changed files, at most
DEPTH imports away. A hub (a file imported by more files than HUB_PERCENTILE of files are) is not expanded through its
importers: almost everything imports it, so reaching it says nothing. Tests past a hub come in only on recorded
execution evidence, that is from a per-test recording that saw them run the changed file. Tests the static reach
analysis found and tests the change itself adds or edits are always candidates.

Order: tests the change adds or edits first; then the most recent flip first; then the fastest first, by the durations
earlier runs measured; the static ranking only breaks ties, which is all it decides on a cold start.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

DEPTH = 10
HUB_PERCENTILE = 99
HUB_FLOOR = 20


def deps(fact):
    """Module names a file imports anywhere in it, from its full facts or as kept in a snapshot."""
    if 'deps' in fact:
        return fact['deps']
    found = set()
    for record in fact.get('records', {}).values():
        found.update(str(name) for name in (record.get('imports') or {}).values())
    return sorted(found)


def resolve(name, modules):
    """The file a module name (or a name imported from a module) refers to: its longest known module prefix."""
    parts = name.split('.')
    for cut in range(len(parts), 0, -1):
        path = modules.get('.'.join(parts[:cut]))
        if path:
            return path
    return None


def importers(facts, modules):
    """{file: files that import it}."""
    table = defaultdict(set)
    for path, fact in facts.items():
        for name in deps(fact):
            target = resolve(name, modules)
            if target and target != path:
                table[target].add(path)
    return table


def hubs(table, files=(), percentile=HUB_PERCENTILE, floor=HUB_FLOOR):
    """Files whose import fan-in is above the ``percentile`` of all ``files`` (those nothing imports count as 0), and
    above ``floor``: in a small tree no file is a hub."""
    counts = sorted([len(table.get(path, ())) for path in set(files) | set(table)])
    if not counts:
        return set()
    threshold = max(counts[min(len(counts) - 1, int(len(counts) * percentile / 100))], floor)
    return {path for path, v in table.items() if len(v) > threshold}


def closure(table, changed, hub_files, depth=DEPTH):
    """Files that import a changed file within ``depth`` steps, not expanding past hubs. Returns ({file: distance},
    hubs reached)."""
    seen, reached, frontier = {path: 0 for path in changed}, set(), list(changed)
    for distance in range(1, depth + 1):
        following = []
        for path in frontier:
            if path in hub_files:
                reached.add(path)
                continue
            for importer in table.get(path, ()):
                if importer not in seen:
                    seen[importer] = distance
                    following.append(importer)
        frontier = following
    return seen, reached


def is_test_path(path):
    parts = PurePosixPath(path).parts
    name = parts[-1] if parts else ''
    return 'tests' in parts[:-1] or name.startswith('test_') or name.endswith('_test.py') or name == 'tests.py'


def candidates(graph, facts, changed_paths, tests, observed_ids=(), static_ids=()):
    """(tests in the candidate set, info). ``tests`` are the graph's test records by identity; ``observed_ids`` are
    tests a recording saw running a changed file; ``static_ids`` are those static reach found."""
    table = importers(facts, graph.modules)
    hub_files = hubs(table, facts)
    files, reached_hubs = closure(table, [p for p in changed_paths if p in facts], hub_files)
    in_closure = {ident for ident, record in tests.items() if record['path'] in files and is_test_path(record['path'])}
    chosen = in_closure | set(observed_ids) | set(static_ids)
    info = dict(closure_files=len(files), closure_tests=len(in_closure), hubs=len(hub_files),
                hubs_reached=sorted(reached_hubs)[:20], observed=len(set(observed_ids) - in_closure),
                static_only=len(set(static_ids) - in_closure - set(observed_ids)), depth_cap=DEPTH)
    return chosen, info


def order(ids, touched=(), flips=None, durations=None, tiebreak=None):
    """``ids`` in run order: touched first, then most recent flip, then shortest measured duration, then ``tiebreak``
    (position in the static ranking; lower first)."""
    flips, durations, tiebreak = flips or {}, durations or {}, tiebreak or {}
    known = sorted(durations.values())
    typical = known[len(known) // 2] if known else 0.0
    first = list(dict.fromkeys(touched))
    rest = [i for i in dict.fromkeys(ids) if i not in set(first)]
    rest.sort(key=lambda i: (-(flips.get(i) or 0), durations.get(i, typical), tiebreak.get(i, len(tiebreak))))
    return first + rest
