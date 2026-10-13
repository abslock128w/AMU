"""Shadow execution: run candidate tests against the code before and after the change, and compare.

Only runs when the caller asks for it. It executes the repository's own tests with the
interpreter the caller names, so it belongs to the developer working on their own tree.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import runners
from .patch import applied, candidate, original

CHUNK = 40
CONFIRM = 20  # seconds always allowed for confirming a failure on the unchanged code
IGNORED = ('.git', '.amu', '.amu-index', '.venv', 'venv', 'node_modules', '__pycache__', '.tox', '.nox',
           '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build', '*.egg-info', '.coverage', 'coverage.json')


def run(root, edits, ids, python=None, budget=60, runner=None):
    """Outcomes of ``ids`` before and after the change. Returns (rows, info, gaps)."""
    python, gaps, started = python or sys.executable, [], time.perf_counter()
    runner = runners.get(root, runner)
    if not ids:
        return [], dict(ran=0, seconds=0.0, runner=runner.name), gaps
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
        left, done = float(budget), 0
        for start in range(0, len(ids), CHUNK):
            chunk = ids[start:start + CHUNK]
            if left <= 1:
                break
            began = time.perf_counter()
            found, problem = runners.execute(runner, python, trees['after'], chunk, Path(scratch)/'after.xml', max(left, 1))
            left -= time.perf_counter() - began
            states['after'].update(found)
            if problem and problem != 'timed out':
                gaps.append(f'shadow run (after the change, {runner.name}): {problem}')
                break
            if problem == 'timed out':
                break
            failing = [ident for ident in chunk if found.get(ident) == 'failed']
            if failing:
                began = time.perf_counter()
                confirmed, problem = runners.execute(runner, python, trees['before'], failing, Path(scratch)/'before.xml', max(left, CONFIRM))
                left -= time.perf_counter() - began
                states['before'].update(confirmed)
                if problem:
                    gaps.append(f'shadow run (before the change, {runner.name}): {problem}')
            done = start + len(chunk)
        if done < len(ids):
            gaps.append(f'shadow run stopped at its {budget} s budget: {done} of {len(ids)} candidates run')
    rows = []
    for ident in ids:
        before, after = states['before'].get(ident), states['after'].get(ident)
        if before and after and before != after:
            rows.append(dict(id=ident, before=before, after=after, label='executed'))
    info = dict(ran=len(states['after']), seconds=round(time.perf_counter() - started, 1), runner=runner.name)
    return rows, info, gaps
