"""The recorders' hook files in an interpreter's site-packages (edges, observe).

A recorder puts its hook there for the length of its suite run, so that every interpreter the suite starts loads it,
and takes it out at the end; on SIGTERM, SIGINT and SIGHUP too. SIGKILL cannot be caught: a recording killed that way
leaves its hook, and every later test process in that interpreter loads it. So a recorder writes an owner marker first,
with its pid, and holds a lock on it until the hook is out again. The lock dies with the process however it ends, which
a pid alone does not show once the pid is reused. ``sweep``, run before AMU runs tests with an interpreter, removes the
hook files whose recorder is gone and says so.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

try:
    import fcntl
except ImportError:  # not POSIX: no lock, so a marker counts as a recorder that runs
    fcntl = None

SIGNALS = ('SIGTERM', 'SIGINT', 'SIGHUP')


def site_packages(python):
    try:
        return Path(subprocess.check_output([python, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
                                            stderr=subprocess.DEVNULL, timeout=60).decode().strip())
    except (OSError, subprocess.SubprocessError):
        return None


def owner(files):
    """The owner marker's name for a recorder's hook files: its module's, as .owner."""
    return Path(next(iter(files))).stem + '.owner'


def running(marker):
    """Whether the recorder that wrote ``marker`` still runs: it holds the marker's lock."""
    try:
        with open(marker) as handle:
            if fcntl is None:
                return True
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        pass
    return False


def install(purelib, files):
    """Write ``files`` ({name: text}) into ``purelib`` after their owner marker, and return the function that takes
    them out again. Until it is called, SIGTERM, SIGINT and SIGHUP take them out and exit. Raises OSError when they
    cannot be written or when a recorder that still runs holds the marker."""
    purelib = Path(purelib)
    marker = purelib/owner(files)
    handle = open(marker, 'a+')
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.truncate(0)
        handle.write(f'{os.getpid()}\n')
        handle.flush()
    except OSError:
        handle.close()
        raise
    written, previous = [], {}

    def remove():
        for path in written:
            path.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        handle.close()
        while previous:
            number, handler = previous.popitem()
            signal.signal(number, handler if handler is not None else signal.SIG_DFL)

    def stop(number, frame):
        remove()
        raise SystemExit(128 + number)

    try:
        for name, text in files.items():
            written.append(purelib/name)
            (purelib/name).write_text(text)
    except OSError:
        remove()
        raise
    # Signal handlers can only be set from the main thread; elsewhere the sweep is what covers a stop.
    if threading.current_thread() is threading.main_thread():
        for name in SIGNALS:
            number = getattr(signal, name, None)
            # A signal the process was started to ignore (nohup) stays ignored.
            if number is not None and signal.getsignal(number) is not signal.SIG_IGN:
                previous[number] = signal.signal(number, stop)
    return remove


def sweep(python):
    """Remove the recorders' hook files left in ``python``'s site-packages by a recording that was stopped before it
    could (SIGKILL). Those of a recorder that still runs are left alone. Returns the gaps: one per recorder found."""
    from . import edges, observe  # both use this module
    purelib = site_packages(python)
    if purelib is None:
        return []
    gaps = []
    for files in (edges.HOOK_FILES, observe.HOOK_FILES):
        marker = purelib/owner(files)
        if running(marker):
            continue
        removed, kept = [], []
        for name in files:
            try:
                (purelib/name).unlink()
                removed.append(name)
            except FileNotFoundError:
                pass
            except OSError:
                kept.append(name)
        if not kept:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
        if removed:
            gaps.append(f"removed a stale recording hook ({', '.join(removed)}) from {purelib}: an earlier recording was "
                        f"stopped before it could remove it")
        if kept:
            gaps.append(f"a stale recording hook ({', '.join(kept)}) in {purelib} could not be removed: every test process "
                        f"this interpreter starts loads it")
    return gaps
