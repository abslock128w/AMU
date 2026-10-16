"""Rerun each flip on the unchanged side, in a fresh job, to measure false flips.

Usage: python -m amu.ci.verify --dir DIR --out DIR [--repo .] [--python PY] [--timeout SECONDS]

The checkout must be the merge base the flips were measured against. A flip is a test that passed on the merge base
and failed on the head. Here every flipped test runs again on the merge base, in a job that shares nothing with the
one that found it. Each flip is classed by its result there:
- ``held``: it passes again, so the flip stands;
- ``false``: it fails, so it was not the change;
- ``other``: skipped, could not run, or no outcome.
Writes ``verify.json`` with the counts, the merge base it ran on, and each test's state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core import runners
from .render import failure_id, load


def verify(repo, report, python=None, timeout=600):
    repo = Path(repo).resolve()
    ids = [failure_id(row) for row in report.get('predicted_failures') or [] if isinstance(failure_id(row), str)]
    name = (report.get('executed') or {}).get('runner') or report.get('runner')
    where = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True).stdout.strip()
    result = dict(v=1, merge_base=where or None, checked=len(ids), held=0, false=0, other=0, states={}, problem=None)
    if not ids:
        return result
    with tempfile.TemporaryDirectory(prefix='amu-verify-') as scratch:
        try:
            runner = runners.get(repo, name if name in runners.RUNNERS else None)
            found, problem = runners.execute(runner, python or sys.executable, repo, ids, Path(scratch)/'report.xml', timeout)
        except Exception as exc:  # measurement only: never fail the job
            found, problem = {}, f'{type(exc).__name__}: {exc}'
    result['problem'] = problem
    for ident in ids:
        state = found.get(ident, 'no outcome')
        result['states'][ident] = state
        key = 'held' if state == 'passed' else 'false' if state == 'failed' else 'other'
        result[key] += 1
    return result


def main(argv=None):
    p = argparse.ArgumentParser(prog='amu ci verify', description=__doc__.split('\n')[0])
    p.add_argument('--dir', required=True, help='the run job\'s artifact, with report.json')
    p.add_argument('--out', required=True)
    p.add_argument('--repo', default='.')
    p.add_argument('--python')
    p.add_argument('--timeout', type=int, default=600)
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        report, _ = load(a.dir)
        result = verify(a.repo, report, a.python, a.timeout)
    except Exception as exc:
        result = dict(v=1, checked=0, held=0, false=0, other=0, states={}, problem=f'{type(exc).__name__}: {exc}')
    (out/'verify.json').write_text(json.dumps(result, indent=1))
    print(f"amu verify: {result['checked']} checked, {result['held']} held, {result['false']} false, {result['other']} other")
    return 0


if __name__ == '__main__':
    sys.exit(main())
