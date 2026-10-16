"""Side-by-side execution of changed functions on captured inputs (Python 3.12 and later).

While candidate tests run on the code before the change, a sys.monitoring hook watches the changed functions. When one
starts, its arguments are snapshotted with pickle (a deep copy that also bounds size), and the function is executed
right there, in the test's own process and state, twice in its version before the change and twice in its version
after, each time on a fresh copy of the arguments. Compared: the return value (or the exception raised), the arguments
afterwards (mutation), what was printed, and the sequence of calls made from first-party code to code outside it. A
difference is kept only when both versions repeat themselves exactly; one that does not is nondeterministic and dropped.

Both versions are compiled from source the same way (decorators left off, so a wrapper's own behaviour is not compared),
in the module's globals, with ``__class__`` bound for ``super()``. Then the test's own call goes on as usual. A function
that is nested, a generator or coroutine, or uses a private name that a class body would mangle is not compared, and is
counted as such.

This runs the project's code on inputs it was really called with; it is a run of its own on the code before the change,
separate from the comparison of test outcomes, whose results it does not touch.
"""
from __future__ import annotations

import ast
import json
import re
import textwrap
from pathlib import Path

from . import runners

KIND = 'behaviour differs on captured input'
CALLS = 5          # calls captured per changed function, per process
PRIVATE = re.compile(r'(?<![\w.])__[A-Za-z]\w*(?<!__)\b')

HOOK = r'''
import os as _os, sys as _sys
_spec_path = _os.environ.get("AMU_SBS_SPEC")
if _spec_path and _sys.version_info >= (3, 12):
    try:
        import atexit, contextlib, io, json, pickle, re, textwrap, threading, types
        with open(_spec_path) as _h:
            _spec = json.load(_h)
        _out = _os.environ["AMU_SBS_OUT"]
        _root = _os.path.realpath(_spec["root"]) + _os.sep
        _m = _sys.monitoring
        _tool = next(i for i in (3, 4, 5) if _m.get_tool(i) is None)
        _m.use_tool_id(_tool, "amu-side-by-side")
        _want = {(_os.path.realpath(t["file"]), t["qual"]): t for t in _spec["targets"]}
        _files = {k[0] for k in _want}
        _keys, _built, _count, _results, _skipped = {}, {}, {}, [], {}
        _local = threading.local()
        _limit = _spec.get("calls", 5)
        _bytes = _spec.get("bytes", 262144)
        _ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")
        _GEN = 0x20 | 0x80 | 0x100 | 0x200

        def _key(code):
            found = _keys.get(code)
            if found is None:
                name = code.co_filename
                path = _os.path.realpath(name) if name and not name.startswith("<") else ""
                found = _keys[code] = (path, code.co_qualname) if path in _files else False
            return found

        def _fp(value, depth=0, seen=None):
            """A comparable fingerprint: structure for builtin containers and plain objects, never getattr on them."""
            seen = seen if seen is not None else set()
            kind = type(value)
            if value is None or kind in (bool, int, float, complex, str, bytes):
                return repr(value)
            if depth > 4 or id(value) in seen:
                return "<" + kind.__qualname__ + ">"
            seen = seen | {id(value)}
            if kind in (list, tuple, set, frozenset):
                items = [_fp(v, depth + 1, seen) for v in value]
                return [kind.__name__, sorted(items, key=repr) if kind in (set, frozenset) else items]
            if kind is dict:
                return ["dict", sorted(([_fp(k, depth + 1, seen), _fp(v, depth + 1, seen)] for k, v in value.items()), key=repr)]
            try:
                state = object.__getattribute__(value, "__dict__")
            except Exception:
                state = None
            if type(state) is dict and kind.__repr__ is object.__repr__:
                return [kind.__module__ + "." + kind.__qualname__, _fp(state, depth + 1, seen)]
            try:
                text = repr(value)
            except Exception as exc:
                text = "<repr failed: " + type(exc).__name__ + ">"
            return kind.__qualname__ + ":" + _ADDRESS.sub(" at 0x?", text)[:2000]

        def _build(target, which, globs):
            source = target.get(which)
            if source is None:
                return None
            template = ("def __amu_outer__():\n    __class__ = None\n" + textwrap.indent(source, "    ")
                        + "\n    return " + target["name"] + "\n")
            space = dict(globs)
            exec(compile(template, target["file"], "exec"), space)
            made = space["__amu_outer__"]()
            parts = target["qual"].split(".")
            owner = None
            if len(parts) > 1:
                owner = globs.get(parts[0])
                for part in parts[1:-1]:
                    owner = vars(owner).get(part) if isinstance(owner, type) else None
            cells = tuple(types.CellType(owner) for name in made.__code__.co_freevars)
            fn = types.FunctionType(made.__code__, globs, made.__name__, made.__defaults__, cells or None)
            fn.__kwdefaults__ = made.__kwdefaults__
            return fn

        _first = {}

        def _first_party(code):
            found = _first.get(code)
            if found is None:
                name = code.co_filename
                found = _first[code] = bool(name) and not name.startswith("<") and _os.path.realpath(name).startswith(_root)
            return found

        def _external(code, offset, target, arg0):
            """During a comparison: a call from first-party code to anything outside it, by name, never by getattr on
            the callable beyond its exact type."""
            if not getattr(_local, "busy", False) or _local.calls is None or not _first_party(code):
                return None
            kind = type(target)
            if kind is types.MethodType:
                target = target.__func__
                kind = type(target)
            if kind is types.FunctionType:
                if _first_party(target.__code__):
                    return None
                name = (target.__module__ or "") + "." + target.__qualname__
            elif kind is types.BuiltinFunctionType:
                name = (target.__module__ or "builtins") + "." + target.__qualname__
            else:
                name = kind.__module__ + "." + kind.__qualname__
            if len(_local.calls) < 200:
                _local.calls.append(name)
            return None

        _modules, _swaps = {}, []

        def _module_for(path):
            found = _modules.get(path)
            if found is None:
                found = False
                for module in list(_sys.modules.values()):
                    try:
                        name = object.__getattribute__(module, "__file__")
                    except Exception:
                        continue
                    if isinstance(name, str) and _os.path.realpath(name) == path:
                        found = module
                        break
                _modules[path] = found
            return found

        def _prepare_swaps():
            """For every changed function that can be found in its module or class: (owner, name, as changed, as it was).
            A changed function calling another changed one must call it as changed too, or the comparison mixes versions."""
            if _swaps:
                return _swaps
            for target in _spec["targets"]:
                module = _module_for(_os.path.realpath(target["file"]))
                if not module:
                    continue
                owner = module
                for part in target["qual"].split(".")[:-1]:
                    owner = vars(owner).get(part) if isinstance(owner, type) or owner is module else None
                    if owner is None:
                        break
                if owner is None:
                    continue
                original = vars(owner).get(target["name"])
                kind = type(original)
                if kind not in (types.FunctionType, staticmethod, classmethod):
                    continue
                changed = _build(target, "after", vars(module))
                if changed is None:
                    continue
                _swaps.append((owner, target["name"], changed if kind is types.FunctionType else kind(changed), original))
            _swaps.append(None)  # prepared, even if empty
            return _swaps

        def _swapped(on):
            for item in _swaps:
                if item is not None:
                    owner, name, changed, original = item
                    try:
                        setattr(owner, name, changed if on else original)
                    except Exception:
                        pass

        def _run(fn, blob, generator, changed=False):
            pos, kw = pickle.loads(blob)
            out = io.StringIO()
            _local.calls = []
            if changed:
                _prepare_swaps()
                _swapped(True)
            try:
                with contextlib.redirect_stdout(out):
                    try:
                        value = fn(*pos, **kw)
                        if generator:
                            value = list(value)[:1000]
                        result = ["return", _fp(value)]
                    except BaseException as exc:
                        result = ["raise", type(exc).__qualname__, _ADDRESS.sub(" at 0x?", str(exc))[:500]]
            finally:
                if changed:
                    _swapped(False)
                calls, _local.calls = _local.calls, None
            return dict(result=result, args_after=_fp([pos, kw]), stdout=out.getvalue()[:2000], calls=calls)

        def _compare(target, code, frame):
            names = code.co_varnames
            flags = code.co_flags
            n = code.co_argcount
            k = code.co_kwonlyargcount
            local = frame.f_locals
            pos = [local[name] for name in names[:n]]
            kw = {name: local[name] for name in names[n:n + k]}
            extra = n + k
            if flags & 0x04:
                pos += list(local[names[extra]])
                extra += 1
            if flags & 0x08:
                kw.update(local[names[extra]])
            try:
                blob = pickle.dumps((pos, kw), protocol=4)
            except Exception as exc:
                _skipped[target["id"]] = "arguments cannot be copied: " + type(exc).__name__
                return
            if len(blob) > _bytes:
                _skipped[target["id"]] = "arguments over the size bound"
                return
            fns = _built.get(target["id"])
            if fns is None:
                fns = _built[target["id"]] = (_build(target, "before", frame.f_globals), _build(target, "after", frame.f_globals))
            base, head = fns
            generator = bool(flags & 0x20)
            runs = [_run(base, blob, generator), _run(base, blob, generator),
                    _run(head, blob, generator, True), _run(head, blob, generator, True)]
            stable = runs[0] == runs[1] and runs[2] == runs[3]
            differs = [field for field in ("result", "args_after", "stdout", "calls") if runs[0][field] != runs[2][field]]
            try:
                shown = repr(pickle.loads(blob))
            except Exception:
                shown = "<input>"
            _results.append(dict(target=target["id"], test=_os.environ.get("AMU_TEST_CONTEXT") or _os.environ.get("PYTEST_CURRENT_TEST", ""),
                                 input=_ADDRESS.sub(" at 0x?", shown)[:400], stable=stable, differs=differs,
                                 before=runs[0], after=runs[2]))

        def _start(code, offset):
            if getattr(_local, "busy", False):
                return None
            key = _key(code)
            if key is False:
                return _m.DISABLE
            target = _want.get(key)
            if target is None:
                return _m.DISABLE
            if code.co_flags & (0x80 | 0x100 | 0x200):
                _skipped[target["id"]] = "coroutine or async generator"
                return _m.DISABLE
            seen = _count.get(target["id"], 0)
            if seen >= _limit:
                return _m.DISABLE
            _count[target["id"]] = seen + 1
            _local.busy, _local.calls = True, None
            _m.set_events(_tool, _m.events.PY_START | _m.events.CALL)
            try:
                _compare(target, code, _sys._getframe(1))
            except BaseException as exc:
                _skipped[target["id"]] = "could not be compared: " + type(exc).__name__ + ": " + str(exc)[:160]
            finally:
                _m.set_events(_tool, _m.events.PY_START)
                _local.busy = False
            return None

        _m.register_callback(_tool, _m.events.PY_START, _start)
        _m.register_callback(_tool, _m.events.CALL, _external)
        _m.set_events(_tool, _m.events.PY_START)

        def _save():
            try:
                with open(_os.path.join(_out, "%d.json" % _os.getpid()), "w") as handle:
                    json.dump(dict(results=_results, skipped=_skipped, captured=_count), handle)
            except Exception:
                pass
        atexit.register(_save)
        try:
            import unittest
            _run_test = unittest.TestCase.run

            def _test_run(self, result=None):
                _os.environ["AMU_TEST_CONTEXT"] = self.id()
                try:
                    return _run_test(self, result)
                finally:
                    _os.environ.pop("AMU_TEST_CONTEXT", None)
            unittest.TestCase.run = _test_run
        except Exception:
            pass
    except Exception:
        pass
_here = _os.path.dirname(_os.path.abspath(__file__))
_kept = list(_sys.path)
try:
    _sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or ".") != _here]
    del _sys.modules["sitecustomize"]
    import sitecustomize  # noqa: F401  the environment's own, if any
except Exception:
    pass
finally:
    _sys.path[:] = _kept
'''


def _node(text, qual):
    """The def statement of ``qual`` (Class.method or function) in ``text``, or None."""
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError):
        return None
    *owners, name = qual.split('.')
    for owner in owners:
        found = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == owner), None)
        if found is None:
            return None
        body = found.body
    return next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def _source(text, node):
    lines = text.splitlines()
    return textwrap.dedent('\n'.join(lines[node.lineno - 1:node.end_lineno])) + '\n'


def targets(functions, before_src, after_src):
    """The spec entries for changed functions that exist on both sides and can be compared, and {id: reason} for those
    that cannot. ``functions`` are analyze's changed_functions rows (file, symbol)."""
    found, skipped = [], {}
    for row in functions:
        path, qual = row['file'], row['symbol']
        ident = f'{path}#{qual}'
        before, after = before_src.get(path), after_src.get(path)
        old = _node(before, qual) if before else None
        new = _node(after, qual) if after else None
        if old is None or new is None:
            skipped[ident] = 'not a module or class function on both sides (new, removed or nested)'
            continue
        if isinstance(old, ast.AsyncFunctionDef) or isinstance(new, ast.AsyncFunctionDef):
            skipped[ident] = 'coroutine'
            continue
        old_text, new_text = _source(before, old), _source(after, new)
        if old_text == new_text:
            continue  # only its decorators or surroundings changed
        if '.' in qual and (PRIVATE.search(old_text) or PRIVATE.search(new_text)):
            skipped[ident] = 'uses a name a class body would mangle'
            continue
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(old)) != any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(new)):
            skipped[ident] = 'a generator on one side only'
            continue
        found.append(dict(id=ident, file=path, qual=qual, name=qual.rsplit('.', 1)[-1], before=old_text, after=new_text))
    return found, skipped


def support(scratch, tree, spec_targets):
    folder = Path(scratch)/'side-by-side'
    (folder/'out').mkdir(parents=True, exist_ok=True)
    (folder/'hook').mkdir(exist_ok=True)
    (folder/'hook'/'sitecustomize.py').write_text(HOOK)
    rows = [dict(t, file=str(Path(tree)/t['file'])) for t in spec_targets]
    (folder/'spec.json').write_text(json.dumps(dict(root=str(tree), targets=rows, calls=CALLS)))
    return folder


def run(runner, python, before_tree, ids, spec_targets, skipped, scratch, timeout):
    """Run ``ids`` on the code before the change with the hook. Returns (info, differences, gaps)."""
    gaps = []
    if not spec_targets:
        return dict(targets=0, skipped=skipped), [], gaps
    folder = support(scratch, before_tree, spec_targets)
    env = dict(AMU_TRUST_PATH=str(folder/'hook'), AMU_SBS_SPEC=str(folder/'spec.json'), AMU_SBS_OUT=str(folder/'out'))
    _, problem = runners.execute(runner, python, before_tree, ids, Path(scratch)/'side-by-side.xml', timeout, extra_env=env)
    if problem and problem != 'timed out':
        gaps.append(f'side-by-side run ({runner.name}): {problem}')
    elif problem:
        gaps.append('side-by-side run stopped at its time limit; calls captured until then are compared')
    results, captured = [], {}
    for part in sorted((folder/'out').glob('*.json')):
        try:
            data = json.loads(part.read_text())
        except (OSError, ValueError):
            continue
        results += data.get('results', [])
        skipped.update(data.get('skipped', {}))
        for name, count in data.get('captured', {}).items():
            captured[name] = captured.get(name, 0) + count
    seen, differences = set(), []
    for row in results:
        if row['stable'] and row['differs']:
            key = (row['target'], row['input'])
            if key not in seen:
                seen.add(key)
                differences.append(dict(row, label=KIND))
    info = dict(targets=len(spec_targets), compared_calls=len(results), functions_called=len(captured),
                unstable=sum(not r['stable'] for r in results), differences=len(differences), skipped=skipped)
    if not results:
        gaps.append(f'side-by-side: none of the {len(spec_targets)} changed function(s) was called by the tests run on the code before the change')
    return info, differences, gaps
