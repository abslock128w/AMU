"""B2 on breaking commits: the 5 Django Phase 0 cases and the 8 sympy slow-suite tasks, each run once.

Usage: b2_breaking.py --engine DIR --scratch DIR --out OUT.json [--budget 60]

Each case's frozen parent tree is copied to a scratch folder and analysed by bare amu with execution and the case's
production patch (Django: django-runtests with the Phase 0 environment; sympy: pytest with the slow-suite environment).
Recorded: flips, the side-by-side numbers, every stable difference with its input and both outcomes, and the commit
message, for labelling each difference as intended or not.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve()
AMU = HERE.parents[3]
PHASE0 = AMU/'phase0-django-2026-10-13'
SLOW = Path('/Users/Shared/amu-slow-suite-2026-10-15')
RECORDS = AMU/'observed-tests-cycle-2026-10-13'/'evaluation'/'before-tests'

WORKER = r'''
import json, sys, time
from pathlib import Path
engine, tree, patch, index, python, budget, runner = sys.argv[1:8]
sys.path.insert(0, engine)
from amu.core import analyze
began = time.perf_counter()
report = analyze(Path(tree), Path(patch).read_text(), full_evidence=True, index_dir=Path(index), run=True, python=python,
                 budget=int(budget), runner=runner)
keep = ('flips', 'summary', 'predicted_failures', 'behaviour_differences', 'side_by_side', 'executed', 'flip_trust', 'changed_functions')
print(json.dumps(dict({k: report.get(k) for k in keep}, seconds=round(time.perf_counter() - began, 1))))
'''


def cases():
    found = []
    for row in json.loads((RECORDS/'phase0'/'django.json').read_text()):
        message = subprocess.run(['git', '--git-dir=' + str(PHASE0/'django.git'), 'log', '-1', '--format=%s%n%b', row['commit']],
                                 capture_output=True, text=True).stdout.strip()
        found.append(dict(case=row['case_id'], commit=row['commit'], parent_tree=str(PHASE0/'cases'/row['case_id']/'parent'),
                          patch=str(PHASE0/'cases'/row['case_id']/'production.patch'), python=str(PHASE0/'env'/'bin'/'python'),
                          runner='django-runtests', message=message, breaking=row['breaking']))
    for task, row in json.loads((RECORDS/'slow-suite'/'tasks.json').read_text()).items():
        message = row['text'].split('follows. The code change is yours to write.', 1)[-1].strip()
        found.append(dict(case=row['case'], task=task, commit=row['commit'], parent_tree=str(SLOW/'cases'/row['case']/'parent'),
                          patch=str(SLOW/'cases'/row['case']/'production.patch'), python=str(SLOW/'slow'/'env'/'bin'/'python'),
                          runner='pytest', message=message))
    return found


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--engine', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--budget', type=int, default=60)
    a = p.parse_args(argv)
    scratch = Path(a.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch/'worker.py').write_text(WORKER)
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {r['case'] for r in results if 'error' not in r}
    results = [r for r in results if r['case'] in done]
    for case in cases():
        if case['case'] in done:
            continue
        work = scratch/case['case']
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(case['parent_tree'], work/'tree', symlinks=True, ignore=shutil.ignore_patterns('.amu', '__pycache__', '.git'))
        began = time.perf_counter()
        done_run = subprocess.run([case['python'], '-P', str(scratch/'worker.py'), a.engine, str(work/'tree'), case['patch'],
                                   str(work/'index'), case['python'], str(a.budget), case['runner']],
                                  capture_output=True, text=True, timeout=3600)
        try:
            result = json.loads(done_run.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            result = dict(error=(done_run.stderr or 'no output')[-800:], seconds=round(time.perf_counter() - began, 1))
        results.append(dict({k: v for k, v in case.items() if k not in ('parent_tree', 'python')}, **result))
        out.write_text(json.dumps(results, indent=1) + '\n')
        side = result.get('side_by_side') or {}
        print(case['case'], result.get('flips'), side.get('compared_calls'), len(result.get('behaviour_differences') or []),
              result.get('seconds'), (result.get('error') or '')[:150], flush=True)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
