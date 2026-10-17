"""Test runners AMU can drive: how to name a test for each, how to run a batch, how to read the outcome.

AMU's own test identity is ``path/to/file.py::Class::test_name``. Each runner turns that into the
label its command line accepts, so a row in the packet can be pasted into the project's runner.
"""
from __future__ import annotations

import json
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
DESELECTED = re.compile(r'\b\d+ deselected\b')


def abort_reason(output, tree):
    """The runner's own last exception line, with the deepest frame inside the tree when there is one."""
    # pytest prints the underlying exception indented after "E", below its own summary line.
    lines = [re.sub(r'^E\s+', '', line.strip()) for line in output.strip().splitlines()]
    exception = next((line for line in reversed(lines) if EXCEPTION.match(line)), None)
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

    extra = ()  # arguments added to every run, such as the ordering seed (trust.ordering)
    durations = None  # {id: seconds} measured by the runs of this instance, where the runner reports them

    def command(self, python, labels, report):
        return [python, '-m', 'pytest', *labels, '-q', '-p', 'no:cacheprovider', *self.extra, '--junitxml=' + str(report)]

    def paste(self, ident):
        return 'python -m pytest ' + self.label(ident)

    def selected(self, python, tree, ids, env, timeout):
        """The ids pytest still selects under the project's own configuration (a marker filter in addopts deselects
        tests even when they are named); None when that cannot be read."""
        try:
            done = subprocess.run([python, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider', *self.extra,
                                   *[self.label(i) for i in ids]],
                                  cwd=self.cwd(tree), env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except (subprocess.TimeoutExpired, OSError):
            return None
        return {re.sub(r'\[.*\]$', '', line.strip()) for line in done.stdout.decode(errors='replace').splitlines() if '::' in line}

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
            try:
                took = float(case.get('time') or 0)
            except ValueError:
                took = 0.0
            if self.durations is None:
                self.durations = {}
            self.durations[ident] = round(self.durations.get(ident, 0.0) + took, 3) if ident in found else round(took, 3)
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


# Plugins never loaded for a comparison: they reorder, rerun, select or decorate tests, and the comparison does that
# itself (pytest-randomly is loaded again with a fixed seed when present: trust.ordering).
SKIPPED_PLUGINS = ('xdist', 'testmon', 'pytest_cov', 'rerunfailures', 'pytest_rerunfailures', 'flaky', 'sugar', 'pytest_sugar',
                   'pytest_html', 'picked', 'pytest_picked', 'randomly', 'pytest_randomly', 'pytest_randomly.', 'random_order',
                   'pytest_random_order', 'instafail', 'pytest_instafail')
_PLUGINS = {}


def plugins(python):
    """The pytest plugins installed for ``python`` (entry point group pytest11), as importable module names."""
    if python not in _PLUGINS:
        code = ('import importlib.metadata as m, json; '
                'print(json.dumps(sorted({(e.name, e.value.split(":")[0]) for e in m.entry_points(group="pytest11")})))')
        try:
            done = subprocess.run([python, '-c', code], capture_output=True, text=True, timeout=60)
            _PLUGINS[python] = [tuple(item) for item in json.loads(done.stdout)]
        except (OSError, subprocess.TimeoutExpired, ValueError):
            _PLUGINS[python] = None
    return _PLUGINS[python]


def plugin_args(runner, python):
    """With plugin autoload off (environment), the installed plugins a comparison keeps, each named with -p. Empty
    when the list cannot be read; autoload then stays on."""
    if runner.name != 'pytest':
        return []
    found = plugins(python)
    if found is None:
        return []
    runner.autoload_off = True
    kept = [module for name, module in found
            if not any(name == skip or module == skip or module.startswith(skip.rstrip('.') + '.') for skip in SKIPPED_PLUGINS)]
    if any(module.split('.')[0] == 'pytest_randomly' for _, module in found):
        kept.append('pytest_randomly')
    return [arg for module in kept for arg in ('-p', module)]


def environment(tree, extra_env=None, autoload=True):
    """The environment a test run gets: the tree importable, no bytecode written, a fixed hash seed so both sides
    iterate sets and dicts of strings alike. ``AMU_TRUST_PATH`` in ``extra_env`` goes first on the path (trust)."""
    extra = dict(extra_env or {})
    first = extra.get('AMU_TRUST_PATH')
    if not autoload:
        extra.setdefault('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
    # The project's tests never see AMU's model credentials: the change under test is someone else's code.
    inherited = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}
    return dict(inherited, PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', **extra,
                PYTHONPATH=os.pathsep.join(filter(None, [first, str(tree/'src'), str(tree), os.environ.get('PYTHONPATH')])))


def sequence(runner, python, tree, ids, timeout):
    """Each label's outcomes in the order a unittest-style runner reported them: {id: [state, ...]}. A label given
    twice runs twice in the same process."""
    try:
        done = subprocess.run(runner.command(python, [runner.label(i) for i in ids], None), cwd=runner.cwd(tree),
                              env=environment(tree, autoload=not getattr(runner, 'autoload_off', False)), capture_output=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    by_label = {runner.label(ident): ident for ident in ids}
    found, pending = {}, None
    for line in (done.stderr + done.stdout).decode(errors='replace').splitlines():
        match = VERBOSE.match(line)
        if match:
            pending = match[2]
        state = STATE.search(line)
        if state and pending:
            value = 'passed' if state[1] == 'ok' else 'skipped' if state[1] in ('skipped', 'expected failure') else 'failed'
            if pending in by_label:
                found.setdefault(by_label[pending], []).append(value)
            pending = None
    return found


def execute(runner, python, tree, ids, report, timeout, extra_env=None, extra_args=None):
    """Run ``ids`` in ``tree``. Returns ({id: state}, problem or None)."""
    usable = [ident for ident in ids if not runner.needs_class or ident.count('::') >= 2]
    if not usable:
        return {}, None
    env = environment(tree, extra_env, autoload=not getattr(runner, 'autoload_off', False))
    found, problem = {}, None
    # One batch first. A single label the runner cannot load can abort the whole batch and leave it with
    # no outcomes, so a batch that comes back short is split in two and retried until the labels that
    # cannot be run stand alone. Those are returned as 'unrunnable': never silently as "did not fail".
    batches, splits = [usable], 0
    while batches:
        batch = batches.pop(0)
        try:
            command = runner.command(python, [runner.label(i) for i in batch], report)
            if extra_args:
                command = [*command[:3], *extra_args, *command[3:]] if runner.name == 'pytest' else command
            done = subprocess.run(command, cwd=runner.cwd(tree), env=env,
                                  capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return found, 'timed out'
        except OSError as exc:
            return found, f'could not start the test runner: {exc.strerror}'
        output = (done.stderr + done.stdout).decode(errors='replace')
        got = runner.outcomes(tree, output, report, batch)
        found.update(got)
        missing = [i for i in batch if i not in got]
        if missing and runner.name == 'pytest' and DESELECTED.search(output):
            # Deselected by the project's configuration, not lost to an abort: skipped, and not split to find out.
            kept = runner.selected(python, tree, missing, env, timeout)
            if kept is not None:
                found.update({ident: 'skipped' for ident in missing if ident not in kept})
                missing = [ident for ident in missing if ident in kept]
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
    """Whether the tree visibly has tests to run: a runner entry point or at least one test module, in a top-level
    tests directory or in a package's own (sympy/core/tests/test_basic.py). Hidden directories are not searched."""
    root = Path(root)
    if (root/'tests'/'runtests.py').is_file() or any(root.glob('tests/**/test*.py')) or any(root.glob('test_*.py')):
        return True
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
        if 'tests.py' in files or (Path(folder).name == 'tests' and any(f.startswith('test_') and f.endswith('.py') for f in files)):
            return True
    return False


def interpreter(root):
    """The project's own interpreter when one is visible, else the one running AMU."""
    import sys
    root = Path(root)
    for candidate in filter(None, [os.environ.get('VIRTUAL_ENV') and Path(os.environ['VIRTUAL_ENV'])/'bin'/'python',
                                   root/'.venv'/'bin'/'python', root/'venv'/'bin'/'python']):
        if Path(candidate).is_file():
            return str(candidate)
    return sys.executable
