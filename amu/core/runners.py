"""Test runners AMU can drive: how to name a test for each, how to run a batch, how to read the outcome.

AMU's own test identity is ``path/to/file.py::Class::test_name``. Each runner turns that into the
label its command line accepts, so a row in the packet can be pasted into the project's runner.
"""
from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

VERBOSE = re.compile(r'^(\w+) \(([\w.]+)\)')
STATE = re.compile(r'\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)')
ORDER = {'failed': 3, 'passed': 2, 'skipped': 1}
MAX_SPLITS = 12  # extra runner starts allowed to isolate labels that abort a batch
EXCEPTION = re.compile(r'^([A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning)|LookupError|KeyError)\b.*')
FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+)')


def abort_reason(output, tree):
    """The runner's own last exception line, with the deepest frame inside the tree when there is one."""
    lines = output.strip().splitlines()
    exception = next((line.strip() for line in reversed(lines) if EXCEPTION.match(line.strip())), None)
    if exception is None:
        return (lines[-1].strip() if lines else 'no output')[:300]
    where = None
    roots = sorted({str(Path(tree)), str(Path(tree).resolve())}, key=len, reverse=True)
    for line in lines:
        frame = FRAME.match(line)
        root = next((r for r in roots if frame and frame[1].startswith(r + '/')), None)
        if root and '/tests/runtests.py' not in frame[1]:
            where = f'{frame[1][len(root) + 1:]}:{frame[2]}'
    return (exception[:300] + (f' (at {where})' if where else ''))


def dotted(ident, strip=''):
    path, *rest = ident.split('::')
    path = path[len(strip):] if strip and path.startswith(strip) else path
    return '.'.join([path[:-3].replace('/', '.'), *rest])


class Runner:
    name = 'pytest'
    needs_class = False
    parallel = False  # whether several processes may run tests in the same tree at once

    def label(self, ident):
        return ident

    def cwd(self, tree):
        return tree

    def command(self, python, labels, report):
        return [python, '-m', 'pytest', *labels, '-q', '-p', 'no:cacheprovider', '--junitxml=' + str(report)]

    def paste(self, ident):
        return 'python -m pytest ' + self.label(ident)

    def outcomes(self, tree, output, report, ids):
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
            if ORDER[state] > ORDER.get(found.get(ident), 0):
                found[ident] = state
        return found


class Unittest(Runner):
    """``python -m unittest`` and the Django runners share verbose output and dotted labels."""
    name = 'unittest'
    needs_class = True
    strip = ''

    def label(self, ident):
        return dotted(ident, self.strip)

    def command(self, python, labels, report):
        return [python, '-m', 'unittest', '-v', *labels]

    def paste(self, ident):
        return 'python -m unittest ' + self.label(ident)

    def outcomes(self, tree, output, report, ids):
        by_label = {self.label(ident): ident for ident in ids}
        found, pending, unloadable = {}, None, []
        for line in output.splitlines():
            match = VERBOSE.match(line)
            if match:
                pending = match[2]
            state = STATE.search(line)
            if state and pending:
                value = 'passed' if state[1] == 'ok' else 'skipped' if state[1] in ('skipped', 'expected failure') else 'failed'
                if '_FailedTest' in pending:
                    # A module or name that cannot be loaded: every requested test under it fails.
                    unloadable.append(pending.rsplit('.', 1)[-1])
                elif pending in by_label:
                    found[by_label[pending]] = value
                pending = None
        for label, ident in by_label.items():
            if ident not in found and any(name in label.split('.') for name in unloadable):
                found[ident] = 'failed'
        return found


class DjangoRuntests(Unittest):
    name = 'django-runtests'
    strip = 'tests/'
    parallel = True  # each process gets its own in-memory test databases

    def cwd(self, tree):
        return tree/'tests'

    def command(self, python, labels, report):
        return [python, 'runtests.py', *labels, '-v', '2', '--parallel', '1', '--noinput']

    def paste(self, ident):
        return 'cd tests && python runtests.py ' + self.label(ident)


class DjangoManage(Unittest):
    name = 'django-manage'

    def command(self, python, labels, report):
        return [python, 'manage.py', 'test', *labels, '-v', '2', '--noinput']

    def paste(self, ident):
        return 'python manage.py test ' + self.label(ident)


RUNNERS = {cls.name: cls for cls in (Runner, Unittest, DjangoRuntests, DjangoManage)}


def detect(root):
    """Pick the runner the tree is set up for. Explicit pytest configuration wins over a manage.py."""
    root = Path(root)
    if (root/'tests'/'runtests.py').is_file():
        return 'django-runtests'
    for name, marker in (('pytest.ini', ''), ('pyproject.toml', '[tool.pytest'), ('tox.ini', '[pytest]'), ('setup.cfg', '[tool:pytest]')):
        try:
            if marker in (root/name).read_text(errors='replace'):
                return 'pytest'
        except OSError:
            pass
    if (root/'manage.py').is_file():
        return 'django-manage'
    if (root/'conftest.py').is_file() or (root/'tests'/'conftest.py').is_file():
        return 'pytest'
    # Module-level test functions and pytest imports only work under pytest.
    samples = [*list(root.glob('tests/**/test*.py'))[:30], *list(root.glob('test*.py'))[:5], *list(root.glob('**/tests.py'))[:10]]
    try:
        texts = [path.read_text(errors='replace') for path in samples]
    except OSError:
        texts = []
    if not texts or any('import pytest' in text or re.search(r'^def test_', text, re.M) for text in texts):
        return 'pytest'
    return 'unittest'


def get(root, name=None):
    return RUNNERS.get(name or detect(root), Runner)()


def execute(runner, python, tree, ids, report, timeout):
    """Run ``ids`` in ``tree``. Returns ({id: state}, problem or None)."""
    usable = [ident for ident in ids if not runner.needs_class or ident.count('::') >= 2]
    if not usable:
        return {}, None
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join(filter(None, [str(tree/'src'), str(tree), os.environ.get('PYTHONPATH')])))
    found, problem = {}, None
    # One batch first. A single label the runner cannot load can abort the whole batch and leave it with
    # no outcomes, so a batch that comes back short is split in two and retried until the labels that
    # cannot be run stand alone. Those are returned as 'unrunnable': never silently as "did not fail".
    batches, splits = [usable], 0
    while batches:
        batch = batches.pop(0)
        try:
            done = subprocess.run(runner.command(python, [runner.label(i) for i in batch], report), cwd=runner.cwd(tree), env=env,
                                  capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return found, 'timed out'
        except OSError as exc:
            return found, f'could not start the test runner: {exc.strerror}'
        output = (done.stderr + done.stdout).decode(errors='replace')
        got = runner.outcomes(tree, output, report, batch)
        found.update(got)
        missing = [i for i in batch if i not in got]
        if not missing:
            continue
        if batch is usable and not got:
            first_error = abort_reason(output, tree)
        elif (not got and splits and len(batch) > 1 and not any(state != 'unrunnable' for state in found.values())
              and abort_reason(output, tree) == locals().get('first_error')):
            # Half a batch failing exactly as the whole batch did: the runner aborts before any test, whatever
            # the labels. Splitting further only restarts it; every remaining candidate is unrunnable.
            for ident in [*missing, *(i for rest in batches for i in rest)]:
                found[ident] = 'unrunnable'
            batches = []
            continue
        if len(missing) == 1 or splits >= MAX_SPLITS:
            for ident in missing:
                found[ident] = 'unrunnable'
            continue
        splits += 1
        half = len(missing) // 2
        batches.extend([missing[:half], missing[half:]])
    if usable and all(found.get(i) == 'unrunnable' for i in usable):
        problem = 'no outcomes read: ' + locals().get('first_error', 'the runner produced no result for any candidate')
    return found, problem


def evident(root):
    """Whether the tree visibly has tests to run: a runner entry point or at least one test module."""
    root = Path(root)
    return ((root/'tests'/'runtests.py').is_file() or any(root.glob('tests/**/test*.py')) or any(root.glob('test_*.py'))
            or any(root.glob('**/tests.py')))


def interpreter(root):
    """The project's own interpreter when one is visible, else the one running AMU."""
    import sys
    root = Path(root)
    for candidate in filter(None, [os.environ.get('VIRTUAL_ENV') and Path(os.environ['VIRTUAL_ENV'])/'bin'/'python',
                                   root/'.venv'/'bin'/'python', root/'venv'/'bin'/'python']):
        if Path(candidate).is_file():
            return str(candidate)
    return sys.executable
