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
        states = {}
        for side in ('after', 'before'):
            states[side], problem = runners.execute(runner, python, trees[side], ids, Path(scratch)/(side + '.xml'), budget)
            if problem:
                gaps.append(f'shadow run ({side} the change, {runner.name}): {problem}')
    rows = []
    for ident in ids:
        before, after = states['before'].get(ident), states['after'].get(ident)
        if before and after and before != after:
            rows.append(dict(id=ident, before=before, after=after, label='executed'))
    info = dict(ran=len(set(states['after']) | set(states['before'])), seconds=round(time.perf_counter() - started, 1), runner=runner.name)
    return rows, info, gaps
