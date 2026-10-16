"""Check A2 of brief 4: the sympy slow-suite trial's 32 recorded trees, under the engine before and after the trust layer.

Usage: a2_sympy.py --engine NAME=PATH [--engine NAME=PATH ...] --python PY --scratch DIR --out OUT.json
                   [--runs w01 w02 ...] [--budget 60] [--jobs 3]

A recorded tree is the trial workspace's committed parent (``git archive HEAD``) with the run's ``agent.diff`` applied
and left uncommitted, as the hook saw it. Bare ``amu`` (``python -P -m amu.core --run --full-evidence``) runs on a copy
of it once per engine, with the same budget, and its report is kept. Nothing in the trial workspaces is changed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
RESULTS = HERE.parents[1]/'before-tests'/'slow-suite'/'results'/'runs'
WORKSPACES = Path('/Users/Shared/amu-slow-suite-2026-10-15/slow/ws')


def tree(run, scratch, engine):
    work = Path(scratch)/f'{run}-{engine}'
    if work.exists():
        subprocess.run(['rm', '-rf', str(work)], check=True)
    work.mkdir(parents=True)
    archive = subprocess.run(['git', '-C', str(WORKSPACES/run), 'archive', 'HEAD'], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        handle.extractall(work, filter='data')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=a@b', '-c', 'user.name=amu', 'commit', '-qm', 'parent']):
        subprocess.run(['git', *args], cwd=work, check=True, capture_output=True)
    diff = (RESULTS/run/'agent.diff').read_bytes()
    if diff.strip():
        subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], cwd=work, input=diff, check=True, capture_output=True)
    return work


def one(run, engine, path, python, scratch, budget):
    started = time.perf_counter()
    try:
        work = tree(run, scratch, engine)
    except subprocess.CalledProcessError as exc:
        return dict(run=run, engine=engine, error=f'tree: {exc.stderr.decode(errors="replace")[-300:]}')
    env = dict(os.environ, PYTHONPATH=str(path))
    done = subprocess.run([python, '-P', '-m', 'amu.core', '--repo', str(work), '--run', '--full-evidence', '--python', python,
                           '--budget', str(budget)], cwd=str(path), env=env, capture_output=True, text=True, timeout=3600)
    try:
        report = json.loads(done.stdout)
    except ValueError:
        return dict(run=run, engine=engine, error=(done.stderr or 'no output')[-500:], seconds=round(time.perf_counter() - started, 1))
    subprocess.run(['rm', '-rf', str(work)])
    keep = ('flips', 'predicted_failures', 'flip_trust', 'unstable', 'summary', 'executed', 'change_evidence', 'replayed', 'order', 'failure')
    return dict(run=run, engine=engine, seconds=round(time.perf_counter() - started, 1), **{k: report.get(k) for k in keep})


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--engine', action='append', required=True, help='NAME=PATH of an AMU source tree')
    p.add_argument('--python', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--runs', nargs='*')
    p.add_argument('--budget', type=int, default=60)
    p.add_argument('--jobs', type=int, default=3)
    a = p.parse_args(argv)
    engines = dict(item.split('=', 1) for item in a.engine)
    runs = a.runs or sorted(d.name for d in RESULTS.iterdir() if (d/'agent.diff').exists())
    out = Path(a.out)
    results = json.loads(out.read_text()) if out.exists() else []
    done = {(r['run'], r['engine']) for r in results if 'error' not in r}
    results = [r for r in results if (r['run'], r['engine']) in done]
    jobs = [(run, name, path) for run in runs for name, path in engines.items() if (run, name) not in done]
    with ThreadPoolExecutor(a.jobs) as pool:
        for result in pool.map(lambda job: one(*job, a.python, a.scratch, a.budget), jobs):
            results.append(result)
            out.write_text(json.dumps(results, indent=1) + '\n')
            print(json.dumps({k: result.get(k) for k in ('run', 'engine', 'flips', 'seconds', 'error')}), flush=True)


if __name__ == '__main__':
    main()
