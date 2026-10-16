"""B1 check: how many of the call edges tests exercise at runtime AMU's static graph holds (DyPyBench: about half).

Usage: b1_static_vs_runtime.py --tree DIR --python PY --runner NAME --scratch DIR --out OUT.json

Records the suite with the edge tracer on a scratch copy of ``tree``, builds AMU's static graph of the same copy, and
compares first-party function-to-function edges (record identities, ``path#qual``):
- in the static graph: AMU has an edge from the caller to the callee;
- callee missing: AMU has no record for the callee at all (DyPyBench's dominant cause);
- missing edge: both records exist, the edge does not.
Counted over direct calls (the caller is the immediate frame) and over every edge, all callers and non-test callers.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from amu.core import edges  # noqa: E402
from amu.core.index import cache_dir  # noqa: E402
from amu.core.index import state as load_state  # noqa: E402
from amu.core.replay import is_test_file  # noqa: E402


def compare(graph, data):
    names = data['names']
    pairs = set()
    for test_pairs in [*data['edges'], data.get('outside', [])]:
        for caller, callee, *kind in test_pairs:
            if caller >= 0:
                pairs.add((edges.record_id(names[caller]), edges.record_id(names[callee]), kind[0] if kind else 1))
    rows = {}
    for scope in ('all callers', 'non-test callers'):
        for which in ('direct calls', 'every edge'):
            chosen = {(a, b) for a, b, direct in pairs if a != b and (which == 'every edge' or direct)
                      and (scope == 'all callers' or not is_test_file(a.split('#')[0]))}
            held = sum(b in graph.edges.get(a, {}) for a, b in chosen)
            no_callee = sum(b not in graph.records for a, b in chosen)
            no_caller = sum(a not in graph.records for a, b in chosen)
            rows[f'{scope}, {which}'] = dict(edges=len(chosen), in_static=held, share_in_static=round(held / len(chosen), 3) if chosen else None,
                                             callee_not_in_static=no_callee, caller_not_in_static=no_caller,
                                             both_present_edge_missing=len(chosen) - held - sum(b not in graph.records or a not in graph.records
                                                                                                for a, b in chosen))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--tree', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--runner', default=None)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args(argv)
    work = Path(a.scratch)
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(a.tree, work/'tree', symlinks=True, ignore=shutil.ignore_patterns('.amu', '__pycache__', '.git'))
    info = edges.record(work/'tree', a.python, a.runner, index_dir=work/'index')
    data = edges.load(cache_dir(work/'tree', work/'index'))
    _, _, _, _, graph = load_state(work/'tree', work/'index')
    result = dict(tree=a.tree, record=info, comparison=compare(graph, data) if data else None)
    Path(a.out).write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps(result['comparison'], indent=1))


if __name__ == '__main__':
    main()
