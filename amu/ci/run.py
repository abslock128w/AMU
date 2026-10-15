"""Run bare ``amu`` on a pull request against its merge base and save the JSON. Never fails the job.

Usage: python -m amu.ci.run --base SHA --head SHA --out DIR [--repo .] [--python PY] [--runner NAME] [--pr N]

The working tree must be the pull request's head. The patch is ``git diff <merge base> <head>``, so commits that
reached the base branch after the pull request was opened are not in it. Writes to DIR:
- ``report.json``: amu's full-evidence report, or ``{"error": ...}`` when amu produced none;
- ``meta.json``: what was compared and how the run went;
- ``patch.diff``: the diff amu was given.
Under GitHub Actions, ``merge-base`` and ``flips`` are also written to ``$GITHUB_OUTPUT``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .. import __version__


def git(repo, *args):
    done = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def merge_base(repo, base, head):
    found = git(repo, 'merge-base', base, head)
    if found is None:
        # A shallow or single-branch checkout may not have the base commit yet.
        git(repo, 'fetch', '--no-tags', '--quiet', 'origin', base)
        found = git(repo, 'merge-base', base, head)
    return found


def amu_command(patch, python, runner, budget):
    command = [sys.executable, '-m', 'amu.core', '--repo', '.', '--patch-file', str(patch), '--run', '--full-evidence',
               '--python', python]
    if runner:
        command += ['--runner', runner]
    if budget:
        command += ['--budget', str(budget)]
    return command


def run(repo, base, head, out, python=None, runner=None, pr=None, budget=None, install_ok=None, amu_ref=None):
    repo, out = Path(repo).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(pr=pr, base=base, head=head, merge_base=None, amu_version=__version__, amu_ref=amu_ref,
                runner=runner or None, budget=budget, install_ok=install_ok, status=None, seconds=None)
    report = None
    started = time.monotonic()
    try:
        live = git(repo, 'rev-parse', 'HEAD')
        if head and live != head:
            report = dict(error=f'the checkout is at {live}, not the pull request head {head}; nothing was run')
        elif (found := merge_base(repo, base, head or 'HEAD')) is None:
            report = dict(error=f'no merge base between {base} and {head}; fetch the history (fetch-depth: 0)')
        else:
            meta['merge_base'] = found
            diff = git(repo, 'diff', '--no-color', '--no-ext-diff', found, head or 'HEAD')
            patch = out/'patch.diff'
            patch.write_text((diff + '\n') if diff else '')
            if not diff:
                report = dict(error='the pull request changes nothing against its merge base; nothing was run')
            else:
                done = subprocess.run(amu_command(patch, python or sys.executable, runner, budget), cwd=repo,
                                      capture_output=True, text=True, stdin=subprocess.DEVNULL)
                try:
                    report = json.loads(done.stdout)
                except ValueError:
                    tail = (done.stderr or done.stdout or 'no output').strip().splitlines()[-1:] or ['no output']
                    report = dict(error=f'amu exited with status {done.returncode} and no report: {tail[0][:300]}')
    except OSError as exc:
        report = dict(error=f'could not run: {exc}')
    meta['seconds'] = round(time.monotonic() - started, 1)
    meta['status'] = 'error' if 'error' in report else 'ran'
    (out/'report.json').write_text(json.dumps(report, indent=1))
    (out/'meta.json').write_text(json.dumps(meta, indent=1))
    outputs = os.environ.get('GITHUB_OUTPUT')
    if outputs:
        with open(outputs, 'a') as handle:
            handle.write(f"merge-base={meta['merge_base'] or ''}\n")
            handle.write(f"flips={'' if report.get('flips') is None else report['flips']}\n")
    return report, meta


def main(argv=None):
    p = argparse.ArgumentParser(prog='amu ci run', description=__doc__.split('\n')[0])
    p.add_argument('--repo', default='.')
    p.add_argument('--base', required=True, help='base commit of the pull request')
    p.add_argument('--head', default='', help='head commit of the pull request; default: HEAD')
    p.add_argument('--out', required=True)
    p.add_argument('--python', help="the project's interpreter, with its test dependencies")
    p.add_argument('--runner', help='test runner; default: detected')
    p.add_argument('--budget', type=int, help='seconds allowed per side; default: amu\'s')
    p.add_argument('--pr', type=int)
    p.add_argument('--install-ok', choices=['true', 'false'])
    p.add_argument('--amu-ref')
    a = p.parse_args(argv)
    try:
        report, meta = run(a.repo, a.base, a.head or None, a.out, a.python, a.runner, a.pr, a.budget,
                           None if a.install_ok is None else a.install_ok == 'true', a.amu_ref)
        print(f"amu: {meta['status']}; flips: {report.get('flips')}; {report.get('error') or report.get('summary', '')}"[:500])
    except Exception as exc:  # the job never fails because of AMU
        print(f'amu ci run failed: {type(exc).__name__}: {exc}')
        Path(a.out).mkdir(parents=True, exist_ok=True)
        if not (Path(a.out)/'report.json').exists():
            (Path(a.out)/'report.json').write_text(json.dumps(dict(error=f'amu ci run failed: {type(exc).__name__}: {exc}'[:400])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
