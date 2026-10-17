"""B2 check: side-by-side execution on Django's non-breaking commits.

Usage: b2_django.py --repo django.git --python PY --scratch DIR --out OUT.json [--jobs 3] [--budget 60] [--limit N]

The commits are the Phase 0 screening's (evaluation/before-tests/phase0/protocol.json) that broke no existing test and
change production code under django/, the same 102 the untested-row scoring used. For each: the parent tree, checked
out from the repository into a scratch folder, and the commit's production diff, analysed by bare amu with execution
(the Django runner, ``--budget`` seconds per side). Recorded per commit: flips, the side-by-side numbers, and every
stable difference with its input and both outcomes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
PROTOCOL = HERE.parents[1]/'before-tests'/'phase0'/'protocol.json'


def git(repo, *args):
    return subprocess.run(['git', '--git-dir=' + str(repo), *args], capture_output=True, check=True).stdout


def commits(repo):
    protocol = json.loads(PROTOCOL.read_text())
    history = git(repo, 'rev-list', '--no-merges', protocol['head']).decode().splitlines()
    screened = [next(c for c in history if c.startswith(short)) for short, reason in protocol['screened']
                if 'breaking' not in reason.replace('no existing test breaks', '') and reason != 'kept']
    found = []
    for commit in reversed(screened):
        parent = git(repo, 'rev-parse', commit + '^').decode().strip()
        prod = [n for n in git(repo, 'diff', '--name-only', parent, commit).decode().splitlines()
                if n.startswith('django/') and n.endswith('.py')]
        if prod:
            found.append((commit, parent, prod))
    return found


WORKER = r'''
import json, sys, time
from pathlib import Path
engine, tree, patch, index, python, budget = sys.argv[1:7]
sys.path.insert(0, engine)
from amu.core import analyze
began = time.perf_counter()
report = analyze(Path(tree), Path(patch).read_text(), full_evidence=True, index_dir=Path(index), run=True, python=python,
                 budget=int(budget), runner='django-runtests')
keep = ('flips', 'summary', 'behaviour_differences', 'side_by_side', 'executed', 'flip_trust', 'changed_functions')
print(json.dumps(dict({k: report.get(k) for k in keep}, seconds=round(time.perf_counter() - began, 1))))
'''


def one(job, repo, engine, python, scratch, budget, worker_id):
    commit, parent, prod = job
    work = Path(scratch)/f'worker-{worker_id}'
    tree = work/'tree'
    tree.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_INDEX_FILE=str(work/'git.index'))
    subprocess.run(['git', '--git-dir=' + str(repo), '--work-tree=' + str(tree), 'read-tree', '--reset', '-u', parent],
                   check=True, env=env, capture_output=True)
    patch = work/'change.diff'
    patch.write_bytes(git(repo, 'diff', parent, commit, '--', *prod))
    message = git(repo, 'log', '-1', '--format=%s', commit).decode(errors='replace').strip()
    done = subprocess.run([python, '-P', str(Path(scratch)/'worker.py'), engine, str(tree), str(patch), str(work/'index'), python, str(budget)],
                          capture_output=True, text=True, timeout=3600)
    try:
        result = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result = dict(error=(done.stderr or 'no output')[-800:])
    return dict(commit=commit, parent=parent, message=message, files=prod, **result)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--engine', required=True)
    p.add_argument('--python', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--budget', type=int, default=60)
    p.add_argument('--limit', type=int)
    p.add_argument('--commits', nargs='*', help='only these commits (prefixes)')
    a = p.parse_args(argv)
    Path(a.scratch).mkdir(parents=True, exist_ok=True)
    (Path(a.scratch)/'worker.py').write_text(WORKER)
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {r['commit'] for r in results if 'error' not in r}
    results = [r for r in results if r['commit'] in done]
    jobs = [job for job in commits(Path(a.repo)) if job[0] not in done
            and (not a.commits or any(job[0].startswith(c) for c in a.commits))][:a.limit]
    print(f'{len(jobs)} commits to run, {len(done)} already done', flush=True)
    slots = list(range(a.jobs))

    def run(job):
        slot = slots.pop()
        try:
            return one(job, Path(a.repo), a.engine, a.python, a.scratch, a.budget, slot)
        finally:
            slots.append(slot)
    with ThreadPoolExecutor(a.jobs) as pool:
        for result in pool.map(run, jobs):
            results.append(result)
            out.write_text(json.dumps(results, indent=1) + '\n')
            side = result.get('side_by_side') or {}
            print(result['commit'][:10], result.get('flips'), side.get('compared_calls'), len(result.get('behaviour_differences') or []),
                  result.get('seconds'), (result.get('error') or '')[:120], flush=True)


if __name__ == '__main__':
    started = time.time()
    main()
    print('seconds', round(time.time() - started))
    sys.exit(0)
