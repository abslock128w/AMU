"""Run bare ``amu`` on a pull request against its merge base and save the JSON. Never fails the job.

Usage: python -m amu.ci.run --base SHA --head SHA --out DIR [--repo .] [--python PY] [--runner NAME] [--pr N]
                             [--budget SECONDS] [--wall-cap SECONDS] [--observe]

The working tree must be the pull request's head. The patch is ``git diff <merge base> <head>``, so commits that
reached the base branch after the pull request was opened are not in it. Writes to DIR:
- ``report.json``: amu's full-evidence report, or ``{"error": ...}`` when amu produced none;
- ``meta.json``: what was compared and how the run went;
- ``patch.diff``: the diff amu was given.
Under GitHub Actions, ``merge-base`` and ``flips`` are also written to ``$GITHUB_OUTPUT``.

The index and any per-test recording live in ``.amu/`` (the action caches it on the merge base). ``--observe`` records
one on the merge base, in a temporary worktree, when the cache has none. ``--wall-cap`` bounds everything: past it the
run is stopped and the report says so.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import __version__
from ..core.index import cache_dir

RESERVE = 60  # seconds kept back from the wall cap for reading results
# The checkout may hold its own ``amu`` package (AMU's own repository does); -P keeps the current directory off
# sys.path so the installed AMU runs, not the one under test.
SAFE = ['-P']


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
    command = [sys.executable, *SAFE, '-m', 'amu.core', '--repo', '.', '--patch-file', str(patch), '--run', '--full-evidence',
               '--python', python]
    if runner:
        command += ['--runner', runner]
    if budget:
        command += ['--budget', str(budget)]
    return command


def bounded(command, cwd, seconds):
    """Run a command in its own process group; past ``seconds`` the whole group is killed. Returns
    (CompletedProcess, None) or (None, 'timed out')."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=max(1, seconds))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.communicate()
        return None, 'timed out'
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), None


def recording(repo):
    return cache_dir(repo)/'observed'


def observe(repo, found, python, runner, seconds):
    """Record the suite under coverage on the merge base and keep the recording with the head's index.
    Returns a note for the gaps, or None."""
    if recording(repo).is_dir():
        return None
    scratch = Path(tempfile.mkdtemp(prefix='amu-base-'))
    tree = scratch/'tree'
    try:
        if git(repo, 'worktree', 'add', '--detach', '--quiet', str(tree), found) is None:
            return 'recording skipped: the merge base could not be checked out'
        command = [sys.executable, *SAFE, '-m', 'amu.core', '--repo', str(tree), '--observe', '--python', python]
        done, problem = bounded(command + (['--runner', runner] if runner else []), tree, seconds)
        source = recording(tree)
        if problem or not source.is_dir():
            return f"recording on the merge base {'stopped at the wall cap' if problem else 'produced nothing'}; static ranking only"
        shutil.copytree(source, recording(repo), dirs_exist_ok=True)
        return None
    finally:
        git(repo, 'worktree', 'remove', '--force', str(tree))
        shutil.rmtree(scratch, ignore_errors=True)


def run(repo, base, head, out, python=None, runner=None, pr=None, budget=None, install_ok=None, amu_ref=None,
        wall_cap=None, record=False, counters=False):
    repo, out = Path(repo).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(pr=pr, base=base, head=head, merge_base=None, amu_version=__version__, amu_ref=amu_ref,
                runner=runner or None, budget=budget, wall_cap=wall_cap, install_ok=install_ok, status=None, seconds=None,
                cached_index=cache_dir(repo).is_dir(), recording=None, counters=bool(counters))
    report, notes = None, []
    started = time.monotonic()
    deadline = started + wall_cap if wall_cap else None

    def left():
        return None if deadline is None else deadline - time.monotonic() - RESERVE
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
                if record:
                    # The recording may take what the comparison does not need: twice the budget and a minute.
                    spare = None if deadline is None else left() - 2 * (budget or 60) - 60
                    if spare is not None and spare < 30:
                        notes.append('recording skipped: the wall cap leaves no time for it')
                    else:
                        note = observe(repo, found, python or sys.executable, runner, spare or 7200)
                        notes += [note] if note else []
                meta['recording'] = recording(repo).is_dir()
                seconds = left()
                if seconds is not None and seconds < 1:
                    report = dict(error=f'stopped at the wall cap of {wall_cap} s before amu ran')
                else:
                    done, problem = bounded(amu_command(patch, python or sys.executable, runner, budget), repo,
                                            seconds if seconds is not None else 24 * 3600)
                    if problem:
                        report = dict(error=f'stopped at the wall cap of {wall_cap} s; amu reported nothing')
                    else:
                        try:
                            report = json.loads(done.stdout)
                        except ValueError:
                            tail = (done.stderr or done.stdout or 'no output').strip().splitlines()[-1:] or ['no output']
                            report = dict(error=f'amu exited with status {done.returncode} and no report: {tail[0][:300]}')
    except OSError as exc:
        report = dict(error=f'could not run: {exc}')
    if notes and isinstance(report.get('gaps'), list):
        report['gaps'] = notes + report['gaps']
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
    p.add_argument('--wall-cap', type=int, help='seconds for everything, recording included; past it the run is stopped')
    p.add_argument('--observe', action='store_true', help='record the suite on the merge base when the cache has no recording')
    p.add_argument('--counters', action='store_true', help='opt in to the counters kept in the comment (counts and SHAs only)')
    p.add_argument('--pr', type=int)
    p.add_argument('--install-ok', choices=['true', 'false'])
    p.add_argument('--amu-ref')
    a = p.parse_args(argv)
    try:
        report, meta = run(a.repo, a.base, a.head or None, a.out, a.python, a.runner, a.pr, a.budget,
                           None if a.install_ok is None else a.install_ok == 'true', a.amu_ref, a.wall_cap, a.observe, a.counters)
        print(f"amu: {meta['status']}; flips: {report.get('flips')}; {report.get('error') or report.get('summary', '')}"[:500])
    except Exception as exc:  # the job never fails because of AMU
        print(f'amu ci run failed: {type(exc).__name__}: {exc}')
        Path(a.out).mkdir(parents=True, exist_ok=True)
        if not (Path(a.out)/'report.json').exists():
            (Path(a.out)/'report.json').write_text(json.dumps(dict(error=f'amu ci run failed: {type(exc).__name__}: {exc}'[:400])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
