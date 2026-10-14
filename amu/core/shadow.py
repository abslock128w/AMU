"""Shadow execution: run candidate tests against the code before and after the change, and compare.

Only runs when the caller asks for it. It executes the repository's own tests with the
interpreter the caller names, so it belongs to the developer working on their own tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import runners
from .git import _git
from .patch import applied, candidate, original

CHUNK = 40
CONFIRM = 20  # seconds always allowed for confirming a failure on the unchanged code
PARTS = max(1, min(4, (os.cpu_count() or 2) // 2))  # processes side by side, for runners that allow it
RATE = 15.0  # tests per second per process, until a run in this tree has measured it
IGNORED = ('.git', '.amu', '.amu-index', '.venv', 'venv', 'node_modules', '__pycache__', '.tox', '.nox',
           '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build', '*.egg-info', '.coverage', 'coverage.json')


def parent_key(root):
    """Identity of the code before the change: the commit, plus any uncommitted change outside .py files,
    which the before side shares with the after side. None outside a git repository."""
    try:
        head = _git(root, 'rev-parse', 'HEAD').strip()
        other = [line for line in _git(root, 'status', '--porcelain').splitlines() if not line.rstrip().endswith(('.py', '.pyi'))]
    except OSError:
        return None
    return hashlib.sha256('\n'.join([head, *sorted(other)]).encode()).hexdigest()[:16]


def load_memo(memo, key):
    try:
        data = json.loads(Path(memo).read_text())
    except (OSError, ValueError, TypeError):
        return dict(key=key, before={}, rate=None)
    return data if data.get('key') == key else dict(key=key, before={}, rate=data.get('rate'))


def execute(runner, python, tree, ids, scratch, name, timeout, parts):
    """runners.execute over ``parts`` processes side by side. Returns ({id: state}, [problems])."""
    slices = [ids[i::parts] for i in range(parts)] if parts > 1 else [ids]
    slices = [part for part in slices if part]
    with ThreadPoolExecutor(len(slices)) as pool:
        results = list(pool.map(lambda pair: runners.execute(runner, python, tree, pair[1], Path(scratch)/f'{name}-{pair[0]}.xml', timeout),
                                enumerate(slices)))
    found, problems = {}, []
    for got, problem in results:
        found.update(got)
        if problem and problem not in problems:
            problems.append(problem)
    return found, problems


def run(root, edits, ids, python=None, budget=60, runner=None, memo=None):
    """Outcomes of ``ids`` before and after the change. Returns (rows, info, gaps).

    ``memo`` is a file kept between runs on the same tree: outcomes already seen on the code before the change
    (valid while that code is the same) and the measured test rate, which caps how many candidates are tried."""
    python, gaps, started = python or sys.executable, [], time.perf_counter()
    runner = runners.get(root, runner)
    if not ids:
        return [], dict(ran=0, seconds=0.0, runner=runner.name), gaps
    parts = PARTS if runner.parallel else 1
    key = parent_key(root) if memo else None
    remembered = load_memo(memo, key) if key else dict(before={}, rate=None)
    rate = remembered.get('rate') or RATE
    cap = max(CHUNK, int(rate * parts * budget * 0.8))
    if len(ids) > cap:
        gaps.append(f'{len(ids) - cap} lower-ranked candidate(s) not run: about {rate:.0f} tests/s per process fit {cap} in the {budget} s allowed')
        ids = ids[:cap]
    with tempfile.TemporaryDirectory(prefix='amu-shadow-') as scratch:
        other = Path(scratch)/'tree'
        shutil.copytree(root, other, ignore=shutil.ignore_patterns(*IGNORED), symlinks=True)
        on_disk = {}
        for path, edit in edits.items():
            on_disk[path] = applied(root, path, edit)
            source = original(root, path, edit) if on_disk[path] else candidate(root, path, edit)
            if source is None:
                return [], dict(ran=0, seconds=0.0, runner=runner.name), [f'{path}: other side of the change could not be built; nothing executed']
            (other/path).parent.mkdir(parents=True, exist_ok=True)
            (other/path).write_text(source)
        # Whichever side the working tree holds, the copy holds the other one.
        live_is_after = any(on_disk.values())
        trees = dict(after=root if live_is_after else other, before=other if live_is_after else root)
        states = dict(after={}, before={})
        # Most promising candidates first, in chunks. Each chunk runs on the changed code; only the tests
        # that fail there are run on the code as it was, to confirm they passed before. A test that passes
        # after the change cannot be a new failure, so the unchanged side is never paid for it.
        left, done, aborted, after_seconds = float(budget), 0, None, 0.0
        for start in range(0, len(ids), CHUNK * parts):
            chunk = ids[start:start + CHUNK * parts]
            if left <= 1:
                break
            began = time.perf_counter()
            found, problems = execute(runner, python, trees['after'], chunk, scratch, 'after', max(left, 1), parts)
            after_seconds += time.perf_counter() - began
            left -= time.perf_counter() - began
            states['after'].update(found)
            problem = next((p for p in problems if p != 'timed out'), problems[0] if problems else None)
            if problem and problem != 'timed out':
                gaps.append(f'shadow run (after the change, {runner.name}): {problem}')
                if not any(state != 'unrunnable' for state in states['after'].values()):
                    # Nothing ran on the changed code. If the same candidates run on the code before the change,
                    # the change is what stops the runner: that is a failure of the change, not a gap.
                    began = time.perf_counter()
                    probe, again = runners.execute(runner, python, trees['before'], chunk, Path(scratch)/'probe.xml', max(left, CONFIRM))
                    left -= time.perf_counter() - began
                    ran_before = sum(state != 'unrunnable' for state in probe.values())
                    if ran_before:
                        aborted = dict(error=problem.removeprefix('no outcomes read: '), ran_before=ran_before, probed=len(chunk))
                break
            if problem == 'timed out':
                break
            failing = [ident for ident in chunk if found.get(ident) == 'failed']
            # The code before the change is the same on every run until the commit moves: what it did then, it does now.
            states['before'].update({ident: remembered['before'][ident] for ident in failing if ident in remembered['before']})
            unknown = [ident for ident in failing if ident not in remembered['before']]
            if unknown:
                began = time.perf_counter()
                confirmed, problem = runners.execute(runner, python, trees['before'], unknown, Path(scratch)/'before.xml', max(left, CONFIRM))
                left -= time.perf_counter() - began
                states['before'].update(confirmed)
                remembered['before'].update({ident: state for ident, state in confirmed.items() if state != 'unrunnable'})
                if problem:
                    gaps.append(f'shadow run (before the change, {runner.name}): {problem}')
            done = start + len(chunk)
        if done < len(ids) and not aborted:
            gaps.append(f'shadow run stopped at its {budget} s budget: {done} of {len(ids)} candidates run')
    ran_after = sum(state != 'unrunnable' for state in states['after'].values())
    if key and memo:
        if ran_after >= CHUNK and after_seconds > 0:
            remembered['rate'] = round(ran_after / after_seconds / max(1, parts if runner.parallel else 1), 1)
        try:
            Path(memo).parent.mkdir(parents=True, exist_ok=True)
            Path(memo).write_text(json.dumps(remembered))
        except OSError:
            pass
    rows = []
    for ident in ids:
        before, after = states['before'].get(ident), states['after'].get(ident)
        if before and after and before != after:
            rows.append(dict(id=ident, before=before, after=after, label='executed'))
    unrunnable = sorted(ident for ident, state in states['after'].items() if state == 'unrunnable')
    if unrunnable:
        gaps.append(f'{len(unrunnable)} candidate(s) could not be run by {runner.name} and are not covered by this result')
    info = dict(ran=sum(state != 'unrunnable' for state in states['after'].values()), candidates=len(ids), unrunnable=len(unrunnable), outcomes=states['after'], seconds=round(time.perf_counter() - started, 1), runner=runner.name)
    if aborted:
        info['aborted'] = aborted
    return rows, info, gaps
