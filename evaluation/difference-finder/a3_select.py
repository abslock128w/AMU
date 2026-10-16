"""Check A3 of brief 4: what bare amu would run, old selection against selection by subtraction. Nothing executes.

Usage: a3_select.py --engine NAME=PATH [--engine ...] --scratch DIR --out OUT.json

The 26 cases of the testmon comparison (blind/results-pieces/testmon-baseline.json): a git working copy of the case's
parent with its production patch applied and uncommitted, bare ``analyze`` with shadow execution replaced by a recorder,
as in that comparison. Per engine: the candidates handed to shadow execution, the truth tests among them, the truth
tests within what the default time cap would run at 60 s and at 300 s per side (15 tests per second, one process), and
the analysis wall time. Each engine runs in its own interpreter, from its own source tree.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
DATA = HERE.parents[1]/'before-tests'
MANIFESTS = {'holdout3': 'holdout3.json', 'werkzeug': 'werkzeug-v2.json', 'holdout2': 'holdout2.json'}

WORKER = r'''
import json, subprocess, sys, time
from pathlib import Path
engine, tree, index = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, engine)
from amu.core import analyze, shadow
seen, info = [], {}
def record(root, edits, ids, *args, **kwargs):
    seen.extend(ids)
    return [], dict(ran=0, seconds=0.0, runner='recorded'), []
shadow.run = record
analyze(tree, None, index_dir=index, run=True, python=sys.executable, full_evidence=True)   # cold index, not timed
began = time.perf_counter()
seen.clear()
report = analyze(tree, None, index_dir=index, run=True, python=sys.executable, full_evidence=True)
seconds = time.perf_counter() - began
print(json.dumps(dict(ids=list(dict.fromkeys(seen)), seconds=round(seconds, 2),
                      selection=(report.get('executed') or {}).get('selection'))))
'''


def tree_for(row, work):
    tree = work/'tree'
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(row['tree'], tree, ignore=shutil.ignore_patterns('.amu', '.testmondata', '__pycache__', '.pytest_cache', '.git'))
    git = ['git', '-C', str(tree), '-c', 'user.email=b@example.invalid', '-c', 'user.name=b']
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'parent']):
        subprocess.run([*git, *args], check=True, capture_output=True)
    with open(row['patch'], 'rb') as handle:
        subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, check=True, capture_output=True)
    return tree


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--engine', action='append', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--python', default=sys.executable)
    a = p.parse_args(argv)
    engines = dict(item.split('=', 1) for item in a.engine)
    scratch = Path(a.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch/'worker.py').write_text(WORKER)
    cases = json.loads((DATA/'blind/results-pieces/testmon-baseline.json').read_text())
    rows = {r['case_id']: r for name in MANIFESTS.values() for r in json.loads((DATA/name).read_text())}
    results = []
    for case in cases:
        row = rows[case['case_id']]
        truth = case.get('existing_truth') or row['existing_truth']
        result = dict(case_id=case['case_id'], suite=case['suite'], truth=len(truth))
        for name, path in engines.items():
            work = scratch/f"{case['case_id']}-{name}"
            tree = tree_for(row, work)
            done = subprocess.run([a.python, '-P', str(scratch/'worker.py'), path, str(tree), str(work/'index')],
                                  capture_output=True, text=True, timeout=1800, cwd=str(scratch))
            try:
                got = json.loads(done.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                result[name] = dict(error=done.stderr[-600:])
                continue
            ids = got['ids']
            result[name] = dict(candidates=len(ids), found=len(set(truth) & set(ids)),
                                found_in_60s=len(set(truth) & set(ids[:max(40, int(15 * 60 * 0.8))])),
                                found_in_300s=len(set(truth) & set(ids[:max(40, int(15 * 300 * 0.8))])),
                                seconds=got['seconds'], selection=got.get('selection'),
                                truth_positions=sorted(ids.index(t) for t in truth if t in ids))
            shutil.rmtree(work, ignore_errors=True)
        results.append(result)
        Path(a.out).write_text(json.dumps(results, indent=1) + '\n')
        print(json.dumps({k: (v if not isinstance(v, dict) else {x: v.get(x) for x in ('candidates', 'found', 'found_in_60s', 'seconds', 'error')})
                          for k, v in result.items()}), flush=True)


if __name__ == '__main__':
    main()
