"""Run bare ``amu`` on a pull request against its merge base and save the JSON. Never fails the job.

Usage: python -m amu.ci.run --base SHA --head SHA --out DIR [--repo .] [--python PY] [--runner NAME] [--pr N]
                             [--budget SECONDS] [--wall-cap SECONDS] [--observe]

The working tree must be the pull request's head. The patch is ``git diff <merge base> <head>``, so commits that
reached the base branch after the pull request was opened are not in it. Writes to DIR:
- ``report.json``: amu's full-evidence report, or ``{"error": ...}`` when amu produced none;
- ``meta.json``: what was compared and how the run went;
- ``patch.diff``: the diff amu was given.
Under GitHub Actions, ``merge-base`` and ``flips`` are also written to ``$GITHUB_OUTPUT``.

The index and any per-test recording live in ``.amu/index/`` (the action caches that folder on the merge base). ``--observe`` records
one on the merge base, in a temporary worktree, when the cache has none. ``--wall-cap`` bounds everything: past it the
run is stopped and the report says so. The recording gets only what the wall cap leaves after amu's own share, and is
skipped when it would not fit there, so amu still runs and reports. A recording's hook files that an earlier, killed run
left in the interpreter are removed first, and the report says so.
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
from ..core import edges as core_edges
from ..core import sitehooks
from ..core.index import cache_dir
from ..core.shadow import SIDE_BY_SIDE

RESERVE = 60  # seconds kept back from the wall cap for reading results
RECORDING_MIN = 120  # a recording is not started with less than this
RECORDING_STORE = 60  # of the recording's share, kept for storing what it recorded
ATTEMPT = 'recording-attempt.json'  # beside the index: how the last recording that did not finish went
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


def amu_command(patch, python, runner, budget, generate=None, merge_base=None):
    command = [sys.executable, *SAFE, '-m', 'amu.core', '--repo', '.', '--patch-file', str(patch), '--run', '--full-evidence',
               '--python', python]
    if merge_base:
        # A file the patch cannot be undone in is taken from the merge base, not lost with the whole run.
        command += ['--merge-base', merge_base]
    if generate and generate[0] > 0:
        dollars, minutes, model = generate
        command += ['--generate-dollars', str(dollars), '--generate-minutes', str(minutes), '--generate-model', model]
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


def recording(repo, calls=False):
    return cache_dir(repo)/('edges' if calls else 'observed')


def amu_share(budget):
    """Seconds of the wall cap kept for amu itself before a recording may start: the budget for each side, the
    side-by-side run's cap, and a minute for the index and the rest."""
    return 2 * (budget or 60) + SIDE_BY_SIDE + 60


def remove_hooks(python):
    """Take the recorders' hook files out of the interpreter's site-packages. A recording stopped at its limit is
    killed before it can remove them itself, and every test process the interpreter starts afterwards would load them.
    Returns the sweep's gaps."""
    return sitehooks.sweep(python)


def earlier_attempt(repo, calls):
    try:
        attempt = json.loads((cache_dir(repo)/ATTEMPT).read_text())
    except (OSError, ValueError):
        return None
    return attempt if isinstance(attempt, dict) and attempt.get('calls') == calls else None


def observe(repo, found, python, runner, seconds):
    """Record the suite on the merge base, in at most ``seconds``, and keep the recording with the head's index:
    per-test call edges on Python 3.12 and later (the sys.monitoring tracer), coverage contexts below that. A recording
    that does not finish is not kept, and is not tried again with no more time than it had. Returns a note for the
    gaps, or None."""
    calls = core_edges.supported(python)
    if recording(repo, calls).is_dir():
        return None
    earlier = earlier_attempt(repo, calls)
    if earlier and earlier.get('seconds', 0) >= seconds:
        return (f"recording skipped: an earlier one did not finish in {earlier['seconds']} s and this run has {seconds} s "
                f"for it; static ranking only")
    scratch = Path(tempfile.mkdtemp(prefix='amu-base-'))
    tree = scratch/'tree'
    try:
        if git(repo, 'worktree', 'add', '--detach', '--quiet', str(tree), found) is None:
            return 'recording skipped: the merge base could not be checked out'
        # The suite stops itself first, so the recorder removes its own hook; the kill at ``seconds`` is the backstop.
        command = [sys.executable, *SAFE, '-m', 'amu.core', '--repo', str(tree), '--observe-calls' if calls else '--observe',
                   '--python', python, '--observe-seconds', str(max(1, seconds - RECORDING_STORE))]
        done, problem = bounded(command + (['--runner', runner] if runner else []), tree, seconds)
        try:
            gaps = json.loads(done.stdout).get('gaps', []) if done else []
        except (ValueError, AttributeError):
            gaps = []
        unfinished = problem or any('did not finish' in str(gap) for gap in gaps)
        source = recording(tree, calls)
        if unfinished:
            cache_dir(repo).mkdir(parents=True, exist_ok=True)
            (cache_dir(repo)/ATTEMPT).write_text(json.dumps(dict(calls=calls, seconds=seconds, merge_base=found)))
            return f'recording on the merge base did not finish in its share of {seconds} s; static ranking only'
        if not source.is_dir():
            return 'recording on the merge base produced nothing; static ranking only'
        shutil.copytree(source, recording(repo, calls), dirs_exist_ok=True)
        (cache_dir(repo)/ATTEMPT).unlink(missing_ok=True)
        return None
    finally:
        remove_hooks(python)
        git(repo, 'worktree', 'remove', '--force', str(tree))
        shutil.rmtree(scratch, ignore_errors=True)


def run(repo, base, head, out, python=None, runner=None, pr=None, budget=None, install_ok=None, amu_ref=None,
        wall_cap=None, record=False, counters=False, generate=None):
    repo, out = Path(repo).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(pr=pr, base=base, head=head, merge_base=None, amu_version=__version__, amu_ref=amu_ref,
                runner=runner or None, budget=budget, wall_cap=wall_cap, install_ok=install_ok, status=None, seconds=None,
                cached_index=cache_dir(repo).is_dir(), recording=None, counters=bool(counters))
    started = time.monotonic()
    # Before anything: a hook an earlier recording could not remove (killed) would load in every test process.
    report, notes = None, sitehooks.sweep(python or sys.executable)
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
                    # The recording has its own share: what the wall cap leaves after amu's.
                    share = None if deadline is None else int(left() - amu_share(budget))
                    if share is not None and share < RECORDING_MIN:
                        notes.append(f'recording skipped: the wall cap leaves {max(share, 0)} s for it after amu\'s share')
                    else:
                        note = observe(repo, found, python or sys.executable, runner, 7200 if share is None else share)
                        notes += [note] if note else []
                meta['recording'] = recording(repo).is_dir() or recording(repo, calls=True).is_dir()
                seconds = left()
                if seconds is not None and seconds < 1:
                    report = dict(error=f'stopped at the wall cap of {wall_cap} s before amu ran')
                else:
                    done, problem = bounded(amu_command(patch, python or sys.executable, runner, budget, generate, found), repo,
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
    p.add_argument('--generate-dollars', type=float, default=0.0, help='dollar cap for generated tests per run (needs ANTHROPIC_API_KEY)')
    p.add_argument('--generate-minutes', type=float, default=20.0, help='minute cap for generated tests per run')
    p.add_argument('--generate-model', default='claude-opus-5-5')
    p.add_argument('--pr', type=int)
    p.add_argument('--install-ok', choices=['true', 'false'])
    p.add_argument('--amu-ref')
    a = p.parse_args(argv)
    try:
        report, meta = run(a.repo, a.base, a.head or None, a.out, a.python, a.runner, a.pr, a.budget,
                           None if a.install_ok is None else a.install_ok == 'true', a.amu_ref, a.wall_cap, a.observe, a.counters,
                           (a.generate_dollars, a.generate_minutes, a.generate_model) if a.generate_dollars > 0 else None)
        print(f"amu: {meta['status']}; flips: {report.get('flips')}; {report.get('error') or report.get('summary', '')}"[:500])
    except Exception as exc:  # the job never fails because of AMU
        print(f'amu ci run failed: {type(exc).__name__}: {exc}')
        Path(a.out).mkdir(parents=True, exist_ok=True)
        if not (Path(a.out)/'report.json').exists():
            (Path(a.out)/'report.json').write_text(json.dumps(dict(error=f'amu ci run failed: {type(exc).__name__}: {exc}'[:400])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
