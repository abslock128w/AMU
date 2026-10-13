"""Shadow execution: run candidate tests against the code before and after the change, and compare.

Only runs when the caller asks for it. It executes the repository's own tests with the
interpreter the caller names, so it belongs to the developer working on their own tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .patch import applied, candidate, original

IGNORED = ('.git', '.amu', '.amu-index', '.venv', 'venv', 'node_modules', '__pycache__', '.tox', '.nox',
           '.mypy_cache', '.pytest_cache', '.ruff_cache', 'dist', 'build', '*.egg-info', '.coverage', 'coverage.json')


def _outcomes(tree, report):
    found = {}
    try:
        cases = list(ET.parse(report).getroot().iter('testcase'))
    except (OSError, ET.ParseError):
        return found
    for case in cases:
        parts = case.get('classname', '').split('.')
        for cut in range(len(parts), 0, -1):
            if (tree/('/'.join(parts[:cut]) + '.py')).is_file():
                break
        else:
            continue
        ident = '::'.join(['/'.join(parts[:cut]) + '.py', *parts[cut:], case.get('name', '').split('[')[0]])
        state = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
        state = 'failed' if state in ('failure', 'error') else state
        # A function fails if any of its parameter instances fails.
        order = {'failed': 3, 'passed': 2, 'skipped': 1}
        if order[state] > order.get(found.get(ident), 0):
            found[ident] = state
    return found


def _pytest(python, tree, ids, report, timeout):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join(filter(None, [str(tree/'src'), str(tree), os.environ.get('PYTHONPATH')])))
    try:
        done = subprocess.run([python, '-m', 'pytest', *ids, '-q', '-p', 'no:cacheprovider', '--junitxml=' + str(report)],
                              cwd=tree, env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 'timed out'
    except OSError as exc:
        return f'could not start pytest: {exc.strerror}'
    if done.returncode not in (0, 1) and not Path(report).exists():
        tail = (done.stderr or done.stdout).decode(errors='replace').strip().splitlines()[-1:]
        return 'pytest did not run: ' + (tail[0][:120] if tail else f'exit {done.returncode}')
    return None


def run(root, edits, ids, python=None, budget=60):
    """Outcomes of ``ids`` before and after the change. Returns (rows, info, gaps)."""
    python, gaps, started = python or sys.executable, [], time.perf_counter()
    if not ids:
        return [], dict(ran=0, seconds=0.0), gaps
    with tempfile.TemporaryDirectory(prefix='amu-shadow-') as scratch:
        other = Path(scratch)/'tree'
        shutil.copytree(root, other, ignore=shutil.ignore_patterns(*IGNORED), symlinks=True)
        on_disk = {}
        for path, edit in edits.items():
            on_disk[path] = applied(root, path, edit)
            source = original(root, path, edit) if on_disk[path] else candidate(root, path, edit)
            if source is None:
                return [], dict(ran=0, seconds=0.0), [f'{path}: other side of the change could not be built; nothing executed']
            (other/path).parent.mkdir(parents=True, exist_ok=True)
            (other/path).write_text(source)
        # Whichever side the working tree holds, the copy holds the other one.
        live_is_after = any(on_disk.values())
        trees = dict(after=root if live_is_after else other, before=other if live_is_after else root)
        states = {}
        for side in ('after', 'before'):
            report = Path(scratch)/(side + '.xml')
            problem = _pytest(python, trees[side], ids, report, budget)
            if problem:
                gaps.append(f'shadow run ({side} the change): {problem}')
            states[side] = _outcomes(trees[side], report)
    rows = []
    for ident in ids:
        before, after = states['before'].get(ident), states['after'].get(ident)
        if before and after and before != after:
            rows.append(dict(id=ident, before=before, after=after, label='executed'))
    info = dict(ran=len(set(states['after']) | set(states['before'])), seconds=round(time.perf_counter() - started, 1))
    return rows, info, gaps
