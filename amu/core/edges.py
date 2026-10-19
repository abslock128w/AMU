"""First-hit caller-to-callee edges per test, recorded with sys.monitoring (Python 3.12 and later).

One tool id and two events: PY_START (a Python function begins, however it was reached: descriptors, getattr, signal
receivers, decorators, plugins) and CALL (a call site calls a Python function). Each callback records its edge and
returns DISABLE, so each code location costs one event per test; at every test boundary
``sys.monitoring.restart_events()`` switches them back on. Only first-party code is kept: files under the root.

The hook loads in every interpreter the suite starts (a .pth file in the project's site-packages for the duration of
the run, as the coverage recorder does), so code a test runs in a child process belongs to that test: the child
inherits the running test's name through the environment.

With ``AMU_EDGES_EXC`` set, which AMU does only when it reruns a flipped test, RAISE, RERAISE and PY_UNWIND are on too:
they give each exception's path from where it was raised to where it stopped. These events cannot be switched off per
location before Python 3.15, so they never run during a whole-suite recording.

Below 3.12 the hook does nothing and ``record`` says so: the coverage recorder (observe) is the fallback there.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

from . import runners
from .index import build

FORMAT = 'amu-edges-1'  # edges are [caller, callee, direct]; caller -1 when no first-party frame called
HOOK_FILES = ('amu_edges_hook.py', 'zzz_amu_edges.pth')  # what ``record`` puts in the interpreter's site-packages

HOOK = r'''"""AMU's edge hook, loaded through a .pth file and named to pytest with -p. PYTEST_DONT_REWRITE: it is imported before
pytest starts, and asking pytest to rewrite it would raise a warning that -W error turns into an abort."""
import os, sys
_out = os.environ.get("AMU_EDGES_OUT")
_root = os.environ.get("AMU_EDGES_ROOT")
_AMU_EDGES = None
if _out and _root and sys.version_info >= (3, 12):
    try:
        import atexit, json
        _m = sys.monitoring
        _tool = next(i for i in (3, 4, 5) if _m.get_tool(i) is None)
        _m.use_tool_id(_tool, "amu-edges")
        _prefix = os.path.realpath(_root) + os.sep
        _ids, _names, _own, _edges, _chains = {}, [], {}, {}, {}
        _state = {"test": os.environ.get("AMU_TEST_CONTEXT") or ""}

        def _mine(code):
            known = _own.get(code)
            if known is None:
                name = code.co_filename
                known = _own[code] = bool(name) and not name.startswith("<") and os.path.realpath(name).startswith(_prefix)
            return known

        def _id(code):
            found = _ids.get(code)
            if found is None:
                found = _ids[code] = len(_names)
                last = max((line for _, _, line in code.co_lines() if line), default=code.co_firstlineno)
                _names.append([os.path.relpath(os.path.realpath(code.co_filename), _root).replace(os.sep, "/"),
                               code.co_qualname, code.co_firstlineno, last])
            return found

        def _add(caller, callee, direct):
            _edges.setdefault(_state["test"], set()).add((_id(caller) if caller is not None else -1, _id(callee), int(direct)))

        import types as _types
        _function, _method = _types.FunctionType, _types.MethodType

        # A callback must never raise into the code it observes, and must never touch the objects it sees beyond their
        # exact type: getattr on a mock call, a lazy object or a proxy runs their code (a mock.call's __code__ is
        # another, unhashable, call object).
        def _start(code, offset):
            try:
                if _mine(code):
                    # The nearest first-party frame below: direct when it is the immediate caller, else reached
                    # through library code (a framework, a descriptor protocol in C, a signal dispatcher).
                    back = sys._getframe(1).f_back
                    direct = back is not None and _mine(back.f_code)
                    while back is not None and not _mine(back.f_code):
                        back = back.f_back
                    _add(back.f_code if back is not None else None, code, direct)
            except Exception:
                pass
            return _m.DISABLE

        def _call(code, offset, target, arg0):
            try:
                if _mine(code):
                    kind = type(target)
                    if kind is _method:
                        target = target.__func__
                        kind = type(target)
                    if kind is _function and _mine(target.__code__):
                        _add(code, target.__code__, True)
            except Exception:
                pass
            return _m.DISABLE

        events = _m.events.PY_START | _m.events.CALL
        _m.register_callback(_tool, _m.events.PY_START, _start)
        _m.register_callback(_tool, _m.events.CALL, _call)
        if os.environ.get("AMU_EDGES_EXC"):
            def _raise(code, offset, exc):
                try:
                    if _mine(code):
                        chain = _chains.setdefault(id(exc), [type(exc).__name__, []])
                        if not chain[1] or chain[1][-1] != _id(code):
                            chain[1].append(_id(code))
                except Exception:
                    pass

            for event in (_m.events.RAISE, _m.events.RERAISE, _m.events.PY_UNWIND):
                _m.register_callback(_tool, event, _raise)
            events |= _m.events.RAISE | _m.events.RERAISE | _m.events.PY_UNWIND
        _m.set_events(_tool, events)

        def _begin(name):
            _state["test"] = name
            os.environ["AMU_TEST_CONTEXT"] = name
            _m.restart_events()

        def _end():
            _state["test"] = ""
            os.environ.pop("AMU_TEST_CONTEXT", None)

        def _save():
            try:
                with open(os.path.join(_out, "%d.json" % os.getpid()), "w") as handle:
                    json.dump(dict(names=_names, edges={t: sorted(e) for t, e in _edges.items()},
                                   chains=[c for c in _chains.values() if len(c[1]) > 1]), handle)
            except Exception:
                pass
        atexit.register(_save)
        try:
            import unittest
            _run = unittest.TestCase.run

            def run(self, result=None):
                _begin(self.id())
                try:
                    return _run(self, result)
                finally:
                    _end()
            unittest.TestCase.run = run
        except Exception:
            pass
        _AMU_EDGES = (_begin, _end)
    except Exception:
        _AMU_EDGES = None


def pytest_runtest_setup(item):
    if _AMU_EDGES:
        path, _, rest = item.nodeid.partition("::")
        _AMU_EDGES[0](".".join([path[:-3].replace("/", "."), *rest.split("[")[0].split("::")]))


def pytest_runtest_teardown(item, nextitem):
    if _AMU_EDGES:
        _AMU_EDGES[1]()
'''

SUITE = {'pytest': lambda py: [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'amu_edges_hook'],
         'unittest': lambda py: [py, '-m', 'unittest', 'discover'],
         'django-runtests': lambda py: [py, 'runtests.py', '--parallel', '1', '--noinput'],
         'django-manage': lambda py: [py, 'manage.py', 'test', '--noinput', '--parallel', '1']}


def supported(python):
    try:
        done = subprocess.run([python, '-c', 'import sys; print(sys.version_info >= (3, 12))'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.stdout.strip() == 'True'


def merge(folder):
    """One process record per file in ``folder`` merged into (names, {test: {(caller, callee)}}, chains), with code
    identities shared across processes: (path, qualname, first line, last line)."""
    table, edges, chains = {}, {}, []
    for part in sorted(Path(folder).glob('*.json')):
        try:
            data = json.loads(part.read_text())
        except (OSError, ValueError):
            continue
        local = [table.setdefault(tuple(name), len(table)) for name in data.get('names', [])]
        for test, pairs in data.get('edges', {}).items():
            bucket = edges.setdefault(test, set())
            for caller, callee, *kind in pairs:
                bucket.add((local[caller] if caller >= 0 else -1, local[callee], kind[0] if kind else 1))
        for kind, path in data.get('chains', []):
            chains.append([kind, [local[i] for i in path]])
    names = [list(name) for name, _ in sorted(table.items(), key=lambda item: item[1])]
    return names, edges, chains


def record(root, python=None, runner=None, index_dir=None, timeout=7200, labels=(), plain=False):
    """Run the suite with the edge hook (or, with ``plain``, without any hook, for timing) and store the recording
    beside the index. Returns an info dict; problems are in ``gaps``."""
    root = Path(root).resolve()
    python = python or runners.interpreter(root)
    chosen = runners.get(root, runner)
    info = dict(runner=chosen.name, python=python, gaps=[], mode='plain' if plain else 'edges')
    if not plain and not supported(python):
        info['gaps'].append(f'{python} is older than 3.12: no sys.monitoring; use --observe (coverage contexts) instead')
        return info
    try:
        purelib = Path(subprocess.check_output([python, '-c', 'import sysconfig; print(sysconfig.get_paths()["purelib"])'],
                                               stderr=subprocess.DEVNULL, timeout=60).decode().strip())
    except (OSError, subprocess.SubprocessError):
        info['gaps'].append(f'{python} could not be asked for its site-packages')
        return info
    with tempfile.TemporaryDirectory(prefix='amu-edges-') as scratch:
        out = Path(scratch)/'out'
        out.mkdir()
        installed = []
        try:
            # The hook is needed by pytest (-p amu_edges_hook) even in a plain run, where it records nothing.
            module, pth = HOOK_FILES
            (purelib/module).write_text(HOOK)
            installed.append(purelib/module)
            if not plain:
                (purelib/pth).write_text('import amu_edges_hook\n')
                installed.append(purelib/pth)
        except OSError:
            for path in installed:
                path.unlink(missing_ok=True)
            info['gaps'].append('could not install the edge hook in the interpreter; nothing recorded')
            return info
        env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=os.pathsep.join(filter(None, [str(root/'src'), str(root), os.environ.get('PYTHONPATH')])))
        if not plain:
            env.update(AMU_EDGES_OUT=str(out), AMU_EDGES_ROOT=str(root))
        command = [*SUITE[chosen.name](python), *labels]
        started = time.perf_counter()
        try:
            done = subprocess.run(command, cwd=chosen.cwd(root), env=env, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            output = (done.stderr + done.stdout).decode(errors='replace').strip().splitlines()
            tail = output[-1:] or ['']
            info.update(exit=done.returncode, suite=tail[0][:160],
                        failures=[line[:200] for line in output if line.startswith(('FAIL:', 'ERROR:', 'FAILED ', 'ERROR '))][:50],
                        summary=[line[:200] for line in output if line.startswith(('Ran ', 'FAILED (', 'OK'))][-3:])
        except subprocess.TimeoutExpired:
            info['gaps'].append(f'the suite did not finish in {timeout} s; what ran is recorded')
        finally:
            for path in installed:
                path.unlink(missing_ok=True)
        info.update(command=' '.join(command), seconds=round(time.perf_counter() - started, 1),
                    processes=len(list(out.glob('*.json'))))
        if plain:
            return info
        names, edges, _ = merge(out)
        info.update(store(root, names, edges, index_dir, info))
    return info


def directory(cache):
    return Path(cache)/'edges'


def store(root, names, edges, index_dir, info):
    _, hashes, _, _, cache = build(root, index_dir)
    out = directory(cache)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    tests = sorted(t for t in edges if t)
    payload = dict(format=FORMAT, recorded_at=time.time(), runner=info.get('runner'), command=info.get('command'),
                   seconds=info.get('seconds'), suite=info.get('suite'), names=names, tests=tests,
                   edges=[sorted(edges[t]) for t in tests], outside=sorted(edges.get('', ())),
                   hashes={name[0]: hashes.get(name[0]) for name in names})
    with gzip.open(out/'edges.json.gz', 'wt') as handle:
        json.dump(payload, handle, separators=(',', ':'))
    return dict(tests=len(tests), codes=len(names), edges=sum(len(e) for e in edges.values()), stored=str(out))


def load(cache):
    try:
        with gzip.open(directory(cache)/'edges.json.gz', 'rt') as handle:
            data = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    return data if data.get('format') == FORMAT else None


def record_id(name):
    """AMU's identity for a recorded code object: ``path#qual`` with nested-scope markers dropped; module code is the
    path itself; a lambda or comprehension belongs to the function that holds it."""
    path, qual = name[0], name[1]
    parts = [p for p in qual.split('.') if p != '<locals>']
    while parts and parts[-1].startswith('<'):
        parts.pop()
    return path + ('#' + '.'.join(parts) if parts else '')


def holding(names, changed):
    """Indexes of the recorded code objects that hold a changed line, the narrowest for each line: a module's or class
    body's range spans its functions, and the function is what changed."""
    found = set()
    for path, lines in changed.items():
        here = [(i, name) for i, name in enumerate(names) if name[0] == path]
        for line in lines:
            around = [(name[3] - name[2], i) for i, name in here if name[2] <= line <= name[3]]
            if around:
                found.add(min(around)[1])
    return found


def entering(data, changed):
    """{test label: changed record ids its run entered}. ``changed`` is {path: lines} on the recorded tree."""
    hit = holding(data['names'], changed)
    found = {}
    for test, pairs in zip(data['tests'], data['edges']):
        reached = {record_id(data['names'][pair[1]]) for pair in pairs if pair[1] in hit}
        if reached:
            found[test] = reached
    return found


def tests_entering(data, symbols):
    """Labels of the tests whose recorded calls entered any of ``symbols`` (AMU record ids), matched by name, so a file
    edited since the recording still matches function by function."""
    wanted = {i for i, name in enumerate(data['names']) if record_id(name) in symbols}
    return {test for test, pairs in zip(data['tests'], data['edges']) if any(pair[1] in wanted for pair in pairs)}


def path_to(names, pairs, start_path, changed, test_name=None, limit=8):
    """Shortest call path, as record ids, from the test (its function when ``test_name`` is given, else any code in its
    file) to code holding a changed line."""
    graph = {}
    for caller, callee, *_ in pairs:
        if caller >= 0:
            graph.setdefault(caller, set()).add(callee)
    targets = holding(names, changed)
    starts = [i for i, name in enumerate(names) if name[0] == start_path
              and (test_name is None or name[1].split('.')[-1] == test_name)]
    if not starts:
        starts = [i for i, name in enumerate(names) if name[0] == start_path]
    seen, queue = {i: None for i in starts}, deque(starts)
    while queue:
        node = queue.popleft()
        if node in targets:
            path = []
            while node is not None:
                path.append(record_id(names[node]))
                node = seen[node]
            return list(dict.fromkeys(reversed(path)))[-limit:]
        for nxt in sorted(graph.get(node, ())):
            if nxt not in seen:
                seen[nxt] = node
                queue.append(nxt)
    return None


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog='amu edges', description='Record first-hit call edges per test (Python 3.12+).')
    p.add_argument('--repo', default='.')
    p.add_argument('--python')
    p.add_argument('--runner')
    p.add_argument('--plain', action='store_true', help='run the suite with no hook, for timing')
    a = p.parse_args(argv)
    print(json.dumps(record(a.repo, a.python, a.runner, plain=a.plain)))


if __name__ == '__main__':
    _main(sys.argv[1:])
