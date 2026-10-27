"""Bare amu again on every save (``amu watch``): its summary line and its flips, each time a Python file changes.

One process stays up, so what a run reads afresh each time is kept in memory instead: the index's last snapshot
(index.MEMORY) and the copy of the tree the code before the change runs in (shadow.MIRROR), which is brought up to date
file by file. On Django that takes the time from a save to the first test run under 2 s once the first run has built
the index. Nothing is written to the tree; the copy lives in a temporary folder that goes when the watch stops.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

WATCHED = ('.py', '.pyi')
SKIPPED = ('.amu', '.git', '__pycache__', '.venv', 'venv', '.tox', '.nox', 'node_modules')


def watched(path):
    parts = Path(path).parts
    return str(path).endswith(WATCHED) and not any(part in SKIPPED for part in parts)


def lines(report, since_save=None):
    """What a run prints: the summary line, with the time from the save to the first test run, then one line per flip."""
    summary = report.get('summary') or report.get('error') or 'no report'
    timing = report.get('seconds_before_running')
    if timing is not None:
        timing += since_save or 0.0
        summary += f' [{timing:.1f} s from the save to the first test run]'
    out = [summary]
    for row in report.get('predicted_failures') or []:
        out.append(f"  flip: {row['id'] if isinstance(row, dict) else row}")
    return out


def watch(root, show, python=None, budget=60, runner=None, stop_after=None, debounce_ms=50, stop=None):
    """Run bare amu now and after every batch of Python file changes under ``root``, until Ctrl-C (or ``stop_after``
    seconds, or ``stop``, a threading.Event, being set); ``show(lines)`` prints each result."""
    from watchfiles import watch as changes

    from . import analyze, index, shadow
    root = Path(root).resolve()
    mirror = Path(tempfile.mkdtemp(prefix='amu-watch-'))
    index.MEMORY, shadow.MIRROR = True, mirror
    started = time.monotonic()

    def once(paths=(), changed=None):
        stamps = [os.stat(p).st_mtime for p in paths if os.path.exists(p)]
        since_save = max(0.0, time.time() - max(stamps)) if stamps else None
        shadow.MIRROR_CHANGED = changed
        show(lines(analyze(root, python=python, budget=budget, runner=runner), since_save))
    try:
        once()
        if stop is None and stop_after is not None:
            import threading
            stop = threading.Event()
        if stop_after is not None:
            import threading
            threading.Timer(max(0.0, stop_after - (time.monotonic() - started)), stop.set).start()
        # Every file change is seen, so the copy of the tree follows it; a run starts only for Python files.
        pending = set()
        for batch in changes(root, watch_filter=lambda change, path: not any(part in SKIPPED for part in Path(path).parts),
                             debounce=debounce_ms, step=25, stop_event=stop, raise_interrupt=False):
            pending |= {str(Path(path).relative_to(root)) for _, path in batch}
            python_files = [path for _, path in batch if watched(path)]
            if python_files:
                once(python_files, pending)
                pending = set()
    finally:
        index.MEMORY, shadow.MIRROR, shadow.MIRROR_CHANGED = False, None, None
        shadow._mirror_state.update(synced=False, written=set())
        index._held.clear()
        index._complete.clear()
        shutil.rmtree(mirror, ignore_errors=True)
