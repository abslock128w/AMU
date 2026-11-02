"""Execution records from a side's own test run (Python 3.12 and later): which watched lines each test ran, and each
call into a watched function with a digest of what went in and what came out.

The hook is a ``sitecustomize`` on ``AMU_TRUST_PATH``, so every interpreter the run starts loads it, whatever the runner.
It uses one ``sys.monitoring`` tool id:

- ``PY_START`` on every code object once. A code object outside the watched files is switched off for the run (DISABLE).
  One in a watched file gets local ``LINE`` events when it holds a watched line, and local ``PY_RETURN`` when it is a
  watched function; ``PY_START`` stays on only for watched functions.
- ``LINE`` records (test, file, line) and returns DISABLE; at every test boundary ``restart_events()`` switches the lines
  back on, so each watched line costs one event per test.
- ``PY_UNWIND`` (global: it cannot be set per code object) closes a watched call that ended in an exception.

A call's input is fingerprinted when it starts, from its arguments' structure and state (``__dict__`` and ``__slots__``
read with ``object.__getattribute__``, never ``repr`` or ``__eq__`` of a project object); its output is the fingerprint of
the return value or the exception, and of the arguments when it ended. Both sides are recorded the same way, in their
own runs, on objects their own code built: two calls are compared only when their inputs' fingerprints are equal.

The test a record belongs to is the pytest node id (a plugin named in ``PYTEST_PLUGINS``) or the unittest id
(``TestCase.run`` wrapped); code that runs outside any test, such as a module body at collection, is ``<import>``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

IMPORT = '<import>'
CALLS = 20          # calls recorded per (test, function)
PER_FUNCTION = 4000  # calls recorded per function per process
PREVIEW = 300        # characters kept of an input or output shown in a report

HOOK = r'''
import os as _os, sys as _sys
_spec_path = _os.environ.get("AMU_REC_SPEC")
if _spec_path and _sys.version_info >= (3, 12):
    try:
        import atexit, hashlib, json, re, threading, types
        with open(_spec_path) as _h:
            _spec = json.load(_h)
        _root = _os.path.realpath(_spec["root"])
        _m = _sys.monitoring
        _tool = next(i for i in (5, 4, 3) if _m.get_tool(i) is None)
        _m.use_tool_id(_tool, "amu-record")
        _E = _m.events
        _lines = {_os.path.realpath(_os.path.join(_root, f)): set(v) for f, v in _spec["lines"].items()}
        _targets = {(_os.path.realpath(_os.path.join(_root, f)), q): f + "#" + q for f, q in _spec["targets"]}
        _files = set(_lines) | {k[0] for k in _targets}
        _rel = {p: _os.path.relpath(p, _root) for p in _files}
        _limit, _per = _spec.get("calls", 20), _spec.get("per_function", 4000)
        _keep = _spec.get("preview", 300)
        _out = _os.path.join(_spec["out"], "%d.jsonl" % _os.getpid())
        _state = {"test": _os.environ.get("AMU_TEST_CONTEXT") or "<import>", "pytest": False}
        _codes = {}
        _hits = {}
        _counts = {}
        _totals = {}
        _buffer = []
        _local = threading.local()
        _ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")
        _GEN = 0x20 | 0x80 | 0x100 | 0x200

        def _flush():
            if _hits:
                _buffer.append(json.dumps(dict(k="cov", t=_state["test"], f={_rel[p]: sorted(v) for p, v in _hits.items()})))
                _hits.clear()
            if _buffer:
                try:
                    with open(_out, "a") as handle:
                        handle.write("\n".join(_buffer) + "\n")
                except Exception:
                    pass
                del _buffer[:]

        def _begin(test):
            _flush()
            _state["test"] = test
            _counts.clear()
            _m.restart_events()

        def _fp(value, depth=0, seen=()):
            kind = type(value)
            if value is None or kind in (bool, int, float, complex):
                return repr(value)
            if kind in (str, bytes):
                return repr(value) if len(value) <= 2000 else repr(value[:2000]) + "+%d" % len(value)
            if depth > 4 or id(value) in seen:
                return "<" + kind.__qualname__ + ">"
            seen = (*seen, id(value))
            if kind in (list, tuple):
                items = [_fp(v, depth + 1, seen) for v in value[:100]]
                return [kind.__name__, items, len(value)]
            if kind in (set, frozenset):
                items = sorted((_fp(v, depth + 1, seen) for v in list(value)[:100]), key=repr)
                return [kind.__name__, items, len(value)]
            if kind is dict:
                pairs = [[_fp(k, depth + 1, seen), _fp(v, depth + 1, seen)] for k, v in list(value.items())[:100]]
                return ["dict", pairs, len(value)]
            if isinstance(value, type):
                return "type:" + getattr(value, "__module__", "?") + "." + getattr(value, "__qualname__", "?")
            if isinstance(value, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
                return "fn:" + str(getattr(value, "__module__", "?")) + "." + str(getattr(value, "__qualname__", "?"))
            if isinstance(value, types.ModuleType):
                return "module:" + value.__name__
            name = getattr(kind, "__module__", "?") + "." + kind.__qualname__
            state = None
            try:
                found = object.__getattribute__(value, "__dict__")
                if type(found) is dict:
                    state = {k: v for k, v in found.items()}
            except Exception:
                pass
            slots = []
            for klass in kind.__mro__:
                names = klass.__dict__.get("__slots__", ())
                for slot in ((names,) if isinstance(names, str) else names):
                    if slot in ("__dict__", "__weakref__"):
                        continue
                    try:
                        slots.append([slot, _fp(object.__getattribute__(value, slot), depth + 1, seen)])
                    except AttributeError:
                        slots.append([slot, "<unset>"])
                    except Exception as exc:
                        slots.append([slot, "<" + type(exc).__name__ + ">"])
            if state is not None or slots:
                return [name, _fp(state, depth + 1, seen) if state is not None else None, slots]
            try:
                text = repr(value)
            except Exception as exc:
                text = "<repr failed: " + type(exc).__name__ + ">"
            return name + ":" + _ADDRESS.sub(" at 0x?", text)[:2000]

        def _pack(value):
            text = json.dumps(value, default=str)
            return hashlib.sha1(text.encode()).hexdigest()[:16], text[:_keep]

        def _arguments(code, frame):
            count = code.co_argcount + code.co_kwonlyargcount
            names = list(code.co_varnames[:count])
            if code.co_flags & 0x04:
                names.append(code.co_varnames[count])
                count += 1
            if code.co_flags & 0x08:
                names.append(code.co_varnames[count])
            return [frame.f_locals.get(n) for n in names]

        def _start(code, offset):
            known = _codes.get(code)
            if known is None:
                name = code.co_filename
                path = _os.path.realpath(name) if name and not name.startswith("<") else ""
                if path not in _files:
                    known = False
                else:
                    own = {line for _, _, line in code.co_lines() if line} & _lines.get(path, set())
                    target = _targets.get((path, code.co_qualname))
                    if target and code.co_flags & _GEN:
                        _buffer.append(json.dumps(dict(k="skip", fn=target, why="generator or coroutine")))
                        target = None
                    events = (_E.LINE if own else 0) | (_E.PY_RETURN if target else 0)
                    if events:
                        _m.set_local_events(_tool, code, events)
                    known = (path, target) if (own or target) else False
                _codes[code] = known
            if not known or not known[1]:
                return _m.DISABLE
            target = known[1]
            if getattr(_local, "busy", False):
                return None  # a project function the fingerprint itself reached: neither recorded nor closed
            key = (_state["test"], target)
            n = _counts.get(key, 0)
            _counts[key] = n + 1
            stack = getattr(_local, "stack", None)
            if stack is None:
                stack = _local.stack = []
            if n >= _limit or _totals.get(target, 0) >= _per:
                stack.append((code, None))
                return None
            _totals[target] = _totals.get(target, 0) + 1
            _local.busy = True
            try:
                args = _arguments(code, _sys._getframe(1))
                digest, shown = _pack(_fp(args))
            except Exception as exc:
                args, digest, shown = None, None, "<" + type(exc).__name__ + ">"
            finally:
                _local.busy = False
            stack.append((code, dict(k="call", t=_state["test"], fn=target, n=n, i=digest, ishow=shown, args=args)))
            return None

        def _end(code, result):
            stack = getattr(_local, "stack", None)
            while stack:
                top, record = stack.pop()
                if top is code:
                    break
            else:
                return
            if record is None:
                return
            _local.busy = True
            try:
                after = _fp(record.pop("args")) if record.get("args") is not None else None
                out, shown = _pack([result, after])
                record.update(o=out, oshow=_pack(result)[1])
            except Exception as exc:
                record.pop("args", None)
                record.update(o=None, oshow="<" + type(exc).__name__ + ">")
            finally:
                _local.busy = False
            _buffer.append(json.dumps(record))
            if len(_buffer) > 500:
                _flush()

        def _return(code, offset, value):
            if _codes.get(code) and not getattr(_local, "busy", False):
                _end(code, ["return", _fp(value)])

        def _unwind(code, offset, exc):
            known = _codes.get(code)
            if known and known[1] and not getattr(_local, "busy", False):
                text = _ADDRESS.sub(" at 0x?", str(exc))[:300]
                _end(code, ["raise", type(exc).__qualname__, text])

        def _line(code, line):
            known = _codes.get(code)
            if known and line in _lines.get(known[0], ()):
                _hits.setdefault(known[0], set()).add(line)
            return _m.DISABLE

        _m.register_callback(_tool, _E.PY_START, _start)
        _m.register_callback(_tool, _E.PY_RETURN, _return)
        _m.register_callback(_tool, _E.PY_UNWIND, _unwind)
        _m.register_callback(_tool, _E.LINE, _line)
        _m.set_events(_tool, _E.PY_START | _E.PY_UNWIND)
        atexit.register(_flush)
        _sys.modules["amu_record_state"] = types.SimpleNamespace(begin=_begin, state=_state, flush=_flush)

        try:
            import unittest
            _run_test = unittest.TestCase.run

            def _test_run(self, result=None):
                if _state["pytest"]:
                    return _run_test(self, result)
                _begin(self.id())
                try:
                    return _run_test(self, result)
                finally:
                    _begin("<import>")
            unittest.TestCase.run = _test_run
        except Exception:
            pass
    except Exception:
        pass
_here = _os.path.dirname(_os.path.abspath(__file__))
_kept = list(_sys.path)
_self = _sys.modules.get("sitecustomize")
try:
    _sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or ".") != _here]
    del _sys.modules["sitecustomize"]
    import sitecustomize  # noqa: F401  the environment's own, if any
except Exception:
    pass
finally:
    _sys.path[:] = _kept
    if "sitecustomize" not in _sys.modules and _self is not None:
        _sys.modules["sitecustomize"] = _self
'''

PLUGIN = r'''"""AMU's record plugin: names each pytest test to the record hook. PYTEST_DONT_REWRITE"""
import os
import sys

import pytest


def _name(item):
    state = sys.modules.get("amu_record_state")
    root = os.environ.get("AMU_REC_ROOT") or str(item.config.rootpath)
    try:
        path = os.path.relpath(os.path.realpath(str(item.path)), os.path.realpath(root))
    except (ValueError, AttributeError):
        path = item.nodeid.split("::", 1)[0]
    rest = item.nodeid.split("::", 1)[1] if "::" in item.nodeid else item.name
    return state, path.replace(os.sep, "/") + "::" + rest


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    state, name = _name(item)
    if state is not None:
        state.state["pytest"] = True
        state.begin(name)


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    state = sys.modules.get("amu_record_state")
    if state is not None:
        state.begin("<import>")
'''

PARAMS = re.compile(r'\[.*\]$')


def supported(python):
    """Whether ``python`` has sys.monitoring (3.12 or later)."""
    import subprocess
    try:
        done = subprocess.run([python, '-c', 'import sys; print(sys.version_info >= (3, 12))'], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.stdout.strip() == 'True'


def prepare(folder, tree, lines, targets, extra_plugins=None):
    import os
    extra_plugins = os.environ.get('PYTEST_PLUGINS') if extra_plugins is None else extra_plugins
    """Write the hook, its plugin and the spec into ``folder``. ``lines``: {path: [line, ...]} relative to ``tree``;
    ``targets``: [(path, qualified name)]. Returns the environment a run needs."""
    folder = Path(folder)
    (folder/'hook').mkdir(parents=True, exist_ok=True)
    (folder/'out').mkdir(exist_ok=True)
    (folder/'hook'/'sitecustomize.py').write_text(HOOK)
    (folder/'hook'/'amu_record_plugin.py').write_text(PLUGIN)
    spec = dict(root=str(tree), lines={p: sorted(set(v)) for p, v in lines.items()}, targets=[list(t) for t in targets],
                out=str(folder/'out'), calls=CALLS, per_function=PER_FUNCTION, preview=PREVIEW)
    (folder/'spec.json').write_text(json.dumps(spec))
    plugins = ','.join(filter(None, [extra_plugins, 'amu_record_plugin']))
    return dict(AMU_TRUST_PATH=str(folder/'hook'), AMU_REC_SPEC=str(folder/'spec.json'), AMU_REC_ROOT=str(tree),
                PYTEST_PLUGINS=plugins)


def identity(raw, tree, cwd=None):
    """AMU's id (path::Class::test) for a recorded test name: a pytest node id without its parameters, or a dotted
    unittest id resolved to its file under ``cwd`` (the runner's directory) or ``tree``."""
    if raw == IMPORT or not raw:
        return IMPORT
    if '::' in raw:
        path, *rest = raw.split('::')
        return '::'.join([path, *rest[:-1], PARAMS.sub('', rest[-1])]) if rest else path
    parts = raw.split(' ')[0].split('.')
    for base in [b for b in (cwd, tree) if b is not None]:
        for cut in range(len(parts) - 1, 0, -1):
            file = Path(base)/('/'.join(parts[:cut]) + '.py')
            if file.is_file():
                try:
                    path = file.resolve().relative_to(Path(tree).resolve()).as_posix()
                except ValueError:
                    path = '/'.join(parts[:cut]) + '.py'
                return '::'.join([path, *parts[cut:]])
    return raw


def read(folder, tree, cwd=None):
    """What a run recorded: ({test: {path: {line}}}, [call], {function: why not recorded})."""
    coverage, calls, skipped = {}, [], {}
    names = {}
    for part in sorted((Path(folder)/'out').glob('*.jsonl')):
        try:
            text = part.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a line cut short when the run was stopped
            raw = event.get('t', IMPORT)
            test = names.get(raw)
            if test is None:
                test = names[raw] = identity(raw, tree, cwd)
            kind = event.get('k')
            if kind == 'cov':
                into = coverage.setdefault(test, {})
                for path, found in event.get('f', {}).items():
                    into.setdefault(path, set()).update(found)
            elif kind == 'call':
                calls.append(dict(test=test, function=event['fn'], n=event['n'], input=event.get('i'),
                                  output=event.get('o'), input_shown=event.get('ishow', ''), output_shown=event.get('oshow', '')))
            elif kind == 'skip':
                skipped[event['fn']] = event['why']
    return coverage, calls, skipped


def tests_on(coverage, path, lines):
    """Tests that ran any of ``lines`` of ``path``; ``<import>`` stands for code run outside any test."""
    lines = set(lines)
    return {test for test, files in coverage.items() if files.get(path, set()) & lines}


def pair(before, after):
    """Calls recorded on each side, paired by (test, function, call index). Returns (rows, counts): a row for each pair
    whose inputs are equal and whose outputs differ. A call with no partner, or whose inputs differ, is counted, never
    compared."""
    left = {(c['test'], c['function'], c['n']): c for c in before if c['test'] != IMPORT}
    right = {(c['test'], c['function'], c['n']): c for c in after if c['test'] != IMPORT}
    rows, counts = [], dict(paired=0, compared=0, inputs_differ=0, unpaired=0, unreadable=0)
    for key in sorted(set(left) | set(right)):
        old, new = left.get(key), right.get(key)
        if old is None or new is None:
            counts['unpaired'] += 1
            continue
        counts['paired'] += 1
        if not old['input'] or not new['input'] or old['output'] is None or new['output'] is None:
            counts['unreadable'] += 1
            continue
        if old['input'] != new['input']:
            counts['inputs_differ'] += 1
            continue
        counts['compared'] += 1
        if old['output'] != new['output']:
            rows.append(dict(test=key[0], function=key[1], n=key[2], input=old['input_shown'],
                             before=old['output_shown'], after=new['output_shown'], digest=(old['output'], new['output'])))
    return rows, counts
