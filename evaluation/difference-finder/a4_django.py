"""Check A4 of brief 4 on Django: a second run against the same base skips the base side; warm analysis time.

Usage: a4_django.py --engine PATH --case DIR --python PY --scratch DIR --out OUT.json [--budget 120]

The case's parent tree (Phase 0, frozen) becomes a git working copy with the production patch applied and uncommitted.
Bare amu runs on it twice with execution, sharing one index directory; then three more times with shadow execution
replaced by a recorder, to time the warm path before execution. Each run is its own interpreter.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

STUBBED = r'''
import json, sys, time
from pathlib import Path
engine, tree, index, python = sys.argv[1:5]
sys.path.insert(0, engine)
from amu.core import analyze, shadow
shadow.run = lambda root, edits, ids, *a, **k: ([], dict(ran=0, seconds=0.0, runner='recorded'), [])
began = time.perf_counter()
analyze(Path(tree), None, index_dir=Path(index), run=True, python=python, full_evidence=True)
print(json.dumps(dict(seconds=round(time.perf_counter() - began, 2))))
'''


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--engine', required=True)
    p.add_argument('--case', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--budget', type=int, default=120)
    a = p.parse_args(argv)
    scratch, case = Path(a.scratch), Path(a.case)
    shutil.rmtree(scratch, ignore_errors=True)
    tree = scratch/'tree'
    shutil.copytree(case/'parent', tree, ignore=shutil.ignore_patterns('.amu', '__pycache__', '.git'))
    git = ['git', '-C', str(tree), '-c', 'user.email=b@example.invalid', '-c', 'user.name=b']
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'parent']):
        subprocess.run([*git, *args], check=True, capture_output=True)
    with open(case/'production.patch', 'rb') as handle:
        subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, check=True, capture_output=True)
    index = scratch/'index'
    env = None
    runs = []
    for number in (1, 2):
        began = time.perf_counter()
        done = subprocess.run([a.python, '-P', '-m', 'amu.core', '--repo', str(tree), '--full-evidence', '--budget', str(a.budget),
                               '--python', a.python], cwd=a.engine, capture_output=True, text=True, timeout=3600,
                              env={**__import__('os').environ, 'PYTHONPATH': a.engine, 'AMU_INDEX_DIR': str(index)})
        wall = round(time.perf_counter() - began, 1)
        try:
            report = json.loads(done.stdout)
        except ValueError:
            runs.append(dict(run=number, error=done.stderr[-800:], wall=wall))
            continue
        runs.append(dict(run=number, wall=wall, flips=report.get('flips'), summary=report.get('summary'),
                         executed=report.get('executed'), flip_trust=report.get('flip_trust'),
                         predicted_failures=report.get('predicted_failures'), gaps=report.get('gaps')))
        if number == 2:
            import sys
            sys.path.insert(0, a.engine)
            from amu.ci.render import render
            runs[-1]['comment'] = render(report, dict(head='0' * 40, merge_base='1' * 40))
    (scratch/'stubbed.py').write_text(STUBBED)
    warm = []
    for _ in range(3):
        done = subprocess.run([a.python, '-P', str(scratch/'stubbed.py'), a.engine, str(tree), str(index), a.python],
                              capture_output=True, text=True, timeout=600, cwd=str(scratch), env=env)
        try:
            warm.append(json.loads(done.stdout.strip().splitlines()[-1])['seconds'])
        except (ValueError, IndexError):
            warm.append(done.stderr[-300:])
    Path(a.out).write_text(json.dumps(dict(case=case.name, budget=a.budget, runs=runs, warm_stubbed_seconds=warm), indent=1) + '\n')
    for run in runs:
        print(json.dumps({k: run.get(k) for k in ('run', 'wall', 'flips', 'error')}), json.dumps((run.get('executed') or {}).get('base_cache')))
    print('warm', warm)


if __name__ == '__main__':
    main()
