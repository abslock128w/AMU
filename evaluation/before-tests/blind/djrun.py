"""Run Django's own tests in a tree and read per-test outcomes; keep a time ledger. Standard library only.

Django's --parallel mode aborts the whole run in this environment when a worker reports a failure (tblib is
not installed, so the traceback cannot be pickled). A full-suite run is therefore split into separate serial
processes over disjoint top-level test packages.
"""
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

LINE = re.compile(r'^(\w+) \(([\w.]+)\)')
STATE = re.compile(r'\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)')
RAN = re.compile(r'^Ran (\d+) tests? in')
# gis_tests aborts the runner when named without a GIS database; runtests.py leaves it out by itself.
SKIP_DIRS = {'data', 'import_error_package', 'test_runner_apps', '__pycache__', 'gis_tests'}


def packages(tree):
    """Top-level test packages, as runtests.py discovers them."""
    return sorted(p.name for p in (Path(tree)/'tests').iterdir() if p.is_dir() and '.' not in p.name and p.name not in SKIP_DIRS)


def split(tree, shards):
    """Packages dealt into ``shards`` groups of similar source size, largest first."""
    sized = sorted(((sum(f.stat().st_size for f in (Path(tree)/'tests'/name).rglob('*.py')), name) for name in packages(tree)), reverse=True)
    groups, totals = [[] for _ in range(shards)], [0] * shards
    for size, name in sized:
        slot = totals.index(min(totals))
        groups[slot].append(name)
        totals[slot] += size
    return [group for group in groups if group]


def parse(text):
    """({dotted test id: passed|failed|skipped}, tests the runner says it ran, modules that failed to import)."""
    found, pending, unloadable, ran = {}, None, set(), None
    for line in text.splitlines():
        match = LINE.match(line)
        if match and '_FailedTest' in match[2]:
            unloadable.add(match[1])
            pending = None
            continue
        if match:
            pending = match[2] if match[2].endswith('.' + match[1]) else match[2] + '.' + match[1]
        state = STATE.search(line)
        if state and pending:
            found[pending] = 'passed' if state[1] == 'ok' else 'failed' if state[1] in ('FAIL', 'ERROR', 'unexpected success') else 'skipped'
            pending = None
        if RAN.match(line):
            ran = int(RAN.match(line)[1])
    return found, ran, sorted(unloadable)


def run(python, tree, labels=(), shards=1, timeout=3600, home=None):
    """Run ``labels`` (all tests when empty). Returns dict(states, output, problems, seconds)."""
    tree = Path(tree)
    home = Path(home or tree.parent/'home')
    home.mkdir(parents=True, exist_ok=True)
    env = dict(PATH='/usr/bin:/bin', HOME=str(home), PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE='1', LANG='en_US.UTF-8')
    groups = [list(labels)] if labels else split(tree, shards)
    started = time.time()
    # Each part writes to its own file: a pipe read in turn would stall the parts not being read.
    logs = [tempfile.TemporaryFile() for _ in groups]
    procs = [subprocess.Popen([str(python), '-B', 'runtests.py', *group, '-v', '2', '--parallel', '1', '--noinput'], cwd=tree/'tests', env=env,
                              stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL) for group, log in zip(groups, logs)]
    states, outputs, problems = {}, [], []
    for number, (proc, log) in enumerate(zip(procs, logs)):
        try:
            proc.wait(timeout=max(1, timeout - (time.time() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            problems.append(f'part {number + 1} of {len(procs)} was stopped when the time ran out')
        log.seek(0)
        text = log.read().decode(errors='replace')
        log.close()
        found, ran, unloadable = parse(text)
        states.update(found)
        outputs.append(text)
        if ran is None and not any('stopped' in p for p in problems):
            problems.append(f'part {number + 1} of {len(procs)} ended without a result (exit {proc.returncode})')
        for module in unloadable:
            problems.append(f'module {module} failed to import')
    return dict(states=states, output='\n'.join(outputs), problems=problems, seconds=round(time.time() - started, 2))


def failures(output, limit=160):
    """The FAIL and ERROR blocks from runner output, cut to ``limit`` lines."""
    lines, keep, inside = output.splitlines(), [], False
    for index, line in enumerate(lines):
        if line.startswith('=' * 20) and index + 1 < len(lines) and lines[index + 1].startswith(('FAIL:', 'ERROR:')):
            inside = True
        elif inside and (RAN.match(line) or line.startswith(('Destroying', 'Ran '))):
            inside = False
        if inside:
            keep.append(line)
    return keep[:limit] + ([f'... {len(keep) - limit} more lines of failure output not shown'] if len(keep) > limit else [])


class Ledger:
    """Seconds of test execution charged to one run, shared by the test command and any automatic check."""

    def __init__(self, path, budget):
        self.path, self.budget = Path(path), budget

    def rows(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def spent(self):
        return sum(row['seconds'] for row in self.rows())

    def left(self):
        return self.budget - self.spent()

    def charge(self, tool, seconds, **detail):
        with open(str(self.path) + '.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = self.rows()
            rows.append(dict(tool=tool, seconds=round(seconds, 2), at=round(time.time(), 1), **detail))
            self.path.write_text(json.dumps(rows, indent=1))
            os.fsync(lock.fileno())
