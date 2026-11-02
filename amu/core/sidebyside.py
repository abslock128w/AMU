"""Side-by-side execution of changed functions on captured inputs (Python 3.12 and later).

While candidate tests run on the code before the change, a sys.monitoring hook watches the changed functions. When one
starts, its arguments are snapshotted with pickle (a deep copy that also bounds size), and the function is executed
right there, in the test's own process and state, twice in its version before the change and twice in its version
after, each time on a fresh copy of the arguments. Compared: the return value (or the exception raised), the arguments
afterwards (mutation), what was printed, and the sequence of calls made from first-party code to code outside it. A
difference is kept only when both versions repeat themselves exactly; one that does not is nondeterministic and dropped.

Both versions are compiled from source the same way (decorators left off, so a wrapper's own behaviour is not compared),
in the module's globals, with ``__class__`` bound for ``super()`` and their own name looked up in those globals, as in the
module (recursion reaches the version swapped in for the run); a decorated module function's own name is the copy itself,
so its recursion is undecorated too. Each is named as the source names it, as is everything defined in it. Then the
test's own call goes on as usual. A function that is nested, a coroutine, a generator on one side only, or a method using
a private name that a class body would mangle is not compared, and is counted as such; so is a function in a test file
(the tests, not the code they test), and one whose code is the same once its docstring is set aside (only the docstring,
comments or layout changed).

This runs the project's code on inputs it was really called with; it is a run of its own on the code before the change,
separate from the comparison of test outcomes, whose results it does not touch.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import textwrap
from pathlib import Path

from . import replay, runners

KIND = 'behaviour differs on captured input'
CALLS = 5          # calls captured per argument shape of a changed function, per process
SHAPES = 8         # argument shapes sampled per changed function, per process
LISTED = 100       # captured calls named in the report
PRIVATE = re.compile(r'(?<![\w.])__[A-Za-z]\w*(?<!__)\b')

HOOK = r'''
import os as _os, sys as _sys
_spec_path = _os.environ.get("AMU_SBS_SPEC")
if _spec_path and _sys.version_info >= (3, 12):
    try:
        import contextlib, copy, inspect, io, json, pickle, re, textwrap, threading, types
        with open(_spec_path) as _h:
            _spec = json.load(_h)
        _out = _os.environ["AMU_SBS_OUT"]
        _root = _os.path.realpath(_spec["root"]) + _os.sep
        _m = _sys.monitoring
        _tool = next(i for i in (3, 4, 5) if _m.get_tool(i) is None)
        _m.use_tool_id(_tool, "amu-side-by-side")
        _want = {(_os.path.realpath(t["file"]), t["qual"]): t for t in _spec["targets"]}
        _files = {k[0] for k in _want}
        _keys, _built, _count = {}, {}, {}
        _local = threading.local()
        _journal = _os.path.join(_out, "%d.jsonl" % _os.getpid())

        def _emit(kind, **data):
            """One line per event, written at once: a run stopped at its time limit keeps what it compared."""
            try:
                with open(_journal, "a") as handle:
                    handle.write(json.dumps(dict(data, kind=kind)) + "\n")
            except Exception:
                pass
        _limit = _spec.get("calls", 5)
        _shapes = _spec.get("shapes", 8)
        _taken = {}
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

        def _requalified(code, wrapper, owner):
            """``code`` with each qualified name in it (functions, generators, coroutines, classes) that starts with
            ``wrapper`` starting with ``owner`` instead: what is built inside a wrapper is named as in the source
            (sympy #30715: a generator's outcomes differed only in "__amu_outer__.<locals>.")."""
            consts = tuple(_requalified(c, wrapper, owner) if type(c) is types.CodeType
                           else owner + c[len(wrapper):] if type(c) is str and c.startswith(wrapper) else c
                           for c in code.co_consts)  # a class body stores its __qualname__ as a constant
            name = code.co_qualname
            name = owner + name[len(wrapper):] if name.startswith(wrapper) else name
            return code.replace(co_consts=consts, co_qualname=name)

        def _build(target, which, globs):
            source = target.get(which)
            if source is None:
                return None
            parts = target["qual"].split(".")
            # Its own name is a global, as in its module (a function recursing, a method calling the module function it
            # is named after), not a cell of the wrapper holding the owner. A module function whose name holds something
            # else (a decorator's wrapper, a cache) reaches the copy itself, so its recursion is undecorated on both sides:
            # the as-changed run swaps such a wrapper for the bare copy.
            own = globs.get(target["name"])
            itself = len(parts) == 1 and not (type(own) is types.FunctionType and _want.get(_key(own.__code__)) is target)
            if which == "after":
                _prepare_swaps()
                _swapped(True)
            template = ("def __amu_outer__():\n" + ("" if itself else "    global " + target["name"] + "\n")
                        + "    __class__ = None\n" + textwrap.indent(source, "    ") + "\n    return " + target["name"] + "\n")
            space = dict(globs)
            try:
                code = _requalified(compile(template, target["file"], "exec"), "__amu_outer__.<locals>.",
                                    target["qual"][:-len(target["name"])])
                if not itself:
                    # The global declaration names the copy, and what it defines, as a module function's.
                    code = _requalified(code, target["name"] + ".", target["qual"] + ".")
                exec(code, space)
            finally:
                if which == "after":
                    _swapped(False)
            made = space["__amu_outer__"]()
            code = made.__code__.replace(co_qualname=target["qual"])
            _keys[code] = False  # named as the function it copies, and never taken for it
            owner = None
            if len(parts) > 1:
                owner = globs.get(parts[0])
                for part in parts[1:-1]:
                    owner = vars(owner).get(part) if isinstance(owner, type) else None
            cells = tuple(types.CellType(owner) for name in code.co_freevars)
            fn = types.FunctionType(code, globs, made.__name__, made.__defaults__, cells or None)
            fn.__kwdefaults__ = made.__kwdefaults__
            for name, cell in zip(code.co_freevars, cells):
                if name == target["name"]:
                    cell.cell_contents = fn
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

        _modules, _swaps, _MISSING = {}, [], object()
        _ready = set()  # additions (by index) and targets (by id) prepared; one whose module was not loaded yet waits

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
                if found:
                    _modules[path] = found  # a module not loaded yet is looked for again next time
            return found

        def _owner(module, parts):
            owner = module
            for part in parts:
                owner = vars(owner).get(part) if isinstance(owner, type) or owner is module else None
                if owner is None:
                    return None
            return owner

        def _rebind(value, cls):
            """A member built outside its class, with super() and __class__ pointing at the real class."""
            kind = type(value)
            if kind is types.FunctionType:
                if "__class__" not in value.__code__.co_freevars:
                    return value
                cells = tuple(types.CellType(cls) if name == "__class__" else cell
                              for name, cell in zip(value.__code__.co_freevars, value.__closure__ or ()))
                fn = types.FunctionType(value.__code__, value.__globals__, value.__name__, value.__defaults__, cells)
                fn.__kwdefaults__, fn.__dict__ = value.__kwdefaults__, dict(value.__dict__)
                return fn
            if kind in (staticmethod, classmethod):
                return kind(_rebind(value.__func__, cls))
            if kind is property:
                return property(*(_rebind(f, cls) if f is not None else None for f in (value.fget, value.fset, value.fdel)), value.__doc__)
            return value

        def _prepare_swaps():
            """What the as-changed runs swap in, as (owner, name, as changed or _MISSING, as it was or _MISSING): the
            top-level statements and class members the change adds, the class members it removes, and every changed
            function that can be found in its module or class. A changed function must see the rest of the change, or
            the comparison mixes versions."""
            additions = list(enumerate(_spec.get("additions", [])))
            waiting = [("a", i, a["file"]) for i, a in additions if ("a", i) not in _ready] + \
                      [("t", t["id"], t["file"]) for t in _spec["targets"] if ("t", t["id"]) not in _ready]
            if _swaps and not any(_module_for(_os.path.realpath(file)) for _, _, file in waiting):
                return _swaps
            # Modules the change adds as new files: loaded from their changed source and put in sys.modules and on their
            # package for the as-changed runs, so changed code importing them finds them.
            for _ in range(2):
                for added in _spec.get("modules", []):
                    name = added["name"]
                    if any(item and item[0] is _sys.modules and item[1] == name for item in _swaps):
                        continue
                    module = types.ModuleType(name)
                    module.__file__ = added["file"]
                    module.__package__ = name if added["file"].endswith("__init__.py") else name.rpartition(".")[0]
                    previous = _sys.modules.get(name, _MISSING)
                    _sys.modules[name] = module
                    try:
                        exec(compile(added["source"], added["file"], "exec"), vars(module))
                    except Exception:
                        if previous is _MISSING:
                            _sys.modules.pop(name, None)
                        else:
                            _sys.modules[name] = previous
                        continue
                    _swaps.append((_sys.modules, name, module, previous))
                    parent, _, leaf = name.rpartition(".")
                    if parent and parent in _sys.modules:
                        holder = _sys.modules[parent]
                        _swaps.append((holder, leaf, module, vars(holder).get(leaf, _MISSING)))
                        setattr(holder, leaf, module)
            # Additions are applied as they are prepared and stay applied until all are, so an import in one file can
            # see a helper the change adds to another; one that fails is retried after the others.
            # Prepared once their module is loaded: preparing at a process's first comparison would leave out a module
            # imported later (Django #22026: an import the change adds to query.py, missing as a NameError).
            pending = [(i, a) for i, a in additions if ("a", i) not in _ready and _module_for(_os.path.realpath(a["file"]))]
            _ready.update(("a", i) for i, _ in pending)
            pending = [a for _, a in pending]
            for _ in range(3):
                failed = []
                for addition in pending:
                    module = _module_for(_os.path.realpath(addition["file"]))
                    if not module:
                        continue
                    if "removed" in addition:
                        # Members the change deletes from a class come off it, so what it inherits shows through.
                        owner = _owner(module, addition["owner"].split("."))
                        for name in addition["removed"] if isinstance(owner, type) else ():
                            original = vars(owner).get(name, _MISSING)
                            if original is not _MISSING:
                                try:
                                    delattr(owner, name)
                                except Exception:
                                    continue
                                _swaps.append((owner, name, _MISSING, original))
                        continue
                    space = vars(module)
                    before = dict(space)
                    try:
                        if addition["owner"]:
                            owner = _owner(module, addition["owner"].split("."))
                            if not isinstance(owner, type):
                                continue
                            leaf = addition["owner"].rsplit(".", 1)[-1]  # the class's own name, so private names mangle as in it
                            body = compile("class " + leaf + ":\n" + textwrap.indent(addition["source"], "    "), addition["file"], "exec")
                            exec(_requalified(body, leaf + ".", addition["owner"] + "."), space)
                            built = space.pop(leaf)
                            if leaf in before:
                                space[leaf] = before[leaf]
                            members = {k: v for k, v in vars(built).items()
                                       if k not in ("__module__", "__qualname__", "__dict__", "__weakref__", "__doc__", "__firstlineno__", "__static_attributes__")}
                            for name, value in members.items():
                                item = (owner, name, _rebind(value, owner), vars(owner).get(name, _MISSING))
                                _swaps.append(item)
                                setattr(owner, name, item[2])
                        else:
                            exec(compile(addition["source"], addition["file"], "exec"), space)
                            for name, value in list(space.items()):
                                if before.get(name, _MISSING) is not value:
                                    _swaps.append((module, name, value, before.get(name, _MISSING)))
                    except Exception:
                        failed.append(addition)
                        for name in [n for n in space if n not in before]:
                            del space[name]
                        space.update(before)
                if not failed or len(failed) == len(pending):
                    break
                pending = failed
            _swapped(False)
            for target in _spec["targets"]:
                if ("t", target["id"]) in _ready:
                    continue
                module = _module_for(_os.path.realpath(target["file"]))
                if not module:
                    continue
                _ready.add(("t", target["id"]))
                owner = _owner(module, target["qual"].split(".")[:-1])
                if owner is None:
                    continue
                original = vars(owner).get(target["name"])
                kind = type(original)
                if kind not in (types.FunctionType, staticmethod, classmethod):
                    continue
                _swaps.append(None)  # mark prepared before building, which applies what is prepared so far
                try:
                    changed = _build(target, "after", vars(module))
                finally:
                    _swaps.pop()
                if changed is None:
                    continue
                _swaps.append((owner, target["name"], changed if kind is types.FunctionType else kind(changed), original))
                if owner is module and kind is types.FunctionType:
                    # `from module import f` elsewhere holds the old function under its own name: rebind those copies too
                    # (first-party modules only), or old callers reach it as it was.
                    for other in list(_sys.modules.values()):
                        try:
                            name = object.__getattribute__(other, "__file__")
                            space = object.__getattribute__(other, "__dict__")
                        except Exception:
                            continue
                        if other is module or not isinstance(name, str) or not _os.path.realpath(name).startswith(_root):
                            continue
                        for alias, value in list(space.items()):
                            if value is original:
                                _swaps.append((other, alias, changed, original))
            _swaps.append(None)  # prepared, even if empty
            return _swaps

        def _swapped(on):
            for item in (_swaps if on else reversed(_swaps)):
                if item is None:
                    continue
                owner, name, changed, original = item
                try:
                    if isinstance(owner, dict):  # sys.modules
                        if on:
                            owner[name] = changed
                        elif original is _MISSING:
                            owner.pop(name, None)
                        else:
                            owner[name] = original
                    elif on and changed is _MISSING:
                        delattr(owner, name)
                    elif on:
                        setattr(owner, name, changed)
                    elif original is _MISSING:
                        delattr(owner, name)
                    else:
                        setattr(owner, name, original)
                except Exception:
                    pass

        def _thaw(blob):
            return pickle.loads(blob[1]) if blob[0] == "pickle" else copy.deepcopy(blob[1]) if blob[0] == "copy" else blob[1]

        def _run(fn, blob, generator, changed=False, rebind=None):
            pos, kw = _thaw(blob)
            if rebind is not None:
                pos, kw = rebind(pos, kw)
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
            if rebind is None:
                layout = [pos, kw]
            elif getattr(rebind, "keyed", True):
                layout = rebind.back(kw)  # passed by name: every value is in kw
            else:
                layout = rebind.back((pos, kw))
            return dict(result=result, args_after=_fp(layout), stdout=out.getvalue()[:2000], calls=calls)

        def _arguments(code, frame):
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
            return pos, kw

        def _one(value):
            """One argument's shape: its type, None, emptiness, and a length bucket (0, 1, 2-10, 11-100, more)."""
            if value is None:
                return "None"
            kind = type(value).__qualname__
            if isinstance(value, bool):
                return kind + ":" + str(value)
            if isinstance(value, (int, float)):
                return kind + (":0" if value == 0 else ":-" if value < 0 else ":+")
            try:
                size = len(value)
            except Exception:
                return kind
            return kind + ":" + ("empty" if size == 0 else "1" if size == 1 else "2-10" if size <= 10 else
                                 "11-100" if size <= 100 else "100+")

        def _shape(pos, kw):
            try:
                return ",".join([_one(v) for v in pos] + [k + "=" + _one(v) for k, v in sorted(kw.items())])
            except Exception:
                return "?"

        def _rebinder(target, head, pos, kw):
            """None when the positional parameters are the same on both sides. Otherwise a function that passes the
            captured arguments to the as-changed version by name, or False when that cannot work: extra positional
            values (*args), positional-only parameters, or a parameter the as-changed version requires that the call
            never gave."""
            before, after = target.get("params_before"), target.get("params_after")
            if before is None or after is None or before == after:
                return None
            if len(before) == len(after) and all(b == a or (b not in after and a not in before) for b, a in zip(before, after)):
                return None  # only renamed in place: a positional call still lands on the same parameters
            if len(pos) > len(before) or target.get("positional_only"):
                return False
            named = dict(zip(before, pos))
            named.update(kw)
            try:
                inspect.signature(head).bind(**named)
            except TypeError:
                return False

            count, keys = len(pos), list(kw)

            def rebind(_pos, _kw):
                values = dict(zip(before, _pos))
                values.update(_kw)
                return [], values
            # The arguments afterwards are compared in the captured layout, positions then keywords, as on the other side.
            rebind.back = lambda values: [[values.get(name) for name in before[:count]], {k: values.get(k) for k in keys}]
            return rebind

        def _with_defaults(target, frame, pos, kw, rebind):
            """When the change alters a parameter's default and the captured value equals the old default, the as-changed
            runs get the new default: arguments are captured after defaults are bound, so a call that left the parameter
            out looks like one that passed the old value (Flask #5898: redirect's code went from 302 to 303, unseen).
            Returns (rebind, [names assumed left at their default])."""
            changed = target.get("defaults") or {}
            if not changed:
                return rebind, []
            names = target.get("params_before") or []
            subs = {}
            for name, (was, now) in changed.items():
                try:
                    old_value = eval(was, frame.f_globals)
                    new_value = eval(now, frame.f_globals)
                except Exception:
                    continue
                if name in kw:
                    value = kw[name]
                elif name in names[:len(pos)]:
                    value = pos[names.index(name)]
                else:
                    continue
                try:
                    if type(value) is type(old_value) and value == old_value:
                        subs[name] = (value, new_value)
                except Exception:
                    continue
            if not subs:
                return rebind, []
            inner = rebind

            def substituted(_pos, _kw):
                p, k = inner(_pos, _kw) if inner is not None else (list(_pos), dict(_kw))
                for name, (_, new_value) in subs.items():
                    if name in k:
                        k[name] = new_value
                    elif name in names[:len(p)]:
                        p[names.index(name)] = new_value
                return p, k

            def back(values):
                layout = inner.back(values) if inner is not None else [list(values[0]), dict(values[1])]
                for name, (captured, _) in subs.items():
                    if name in layout[1]:
                        layout[1][name] = captured
                    elif name in names[:len(layout[0])]:
                        layout[0][names.index(name)] = captured
                return layout
            substituted.back = back
            substituted.keyed = inner is not None
            return substituted, sorted(subs)

        def _compare(target, code, frame, pos, kw, shape):
            flags = code.co_flags
            try:
                blob = ("pickle", pickle.dumps((pos, kw), protocol=4))
                if len(blob[1]) > _bytes:
                    _emit("skipped", target=target["id"], reason="arguments over the size bound")
                    return
            except Exception:
                # Lambdas, local classes and the like do not pickle; a deep copy shares functions and copies the rest.
                try:
                    blob = ("copy", copy.deepcopy((pos, kw)))
                except Exception:
                    # Objects holding locks, modules or connections copy neither way. This run's test outcomes are not
                    # used, so the versions run on the live arguments; the comparison stands only if no run changed them.
                    blob = ("live", (pos, kw))
            fns = _built.get(target["id"])
            if fns is None:
                fns = _built[target["id"]] = (_build(target, "before", frame.f_globals), _build(target, "after", frame.f_globals))
            base, head = fns
            rebind = _rebinder(target, head, pos, kw)
            if rebind is not False:
                rebind, assumed = _with_defaults(target, frame, pos, kw, rebind)
            if rebind is False:
                # The change renamed, reordered or inserted positional parameters, and the captured call cannot be given
                # to the as-changed version by name (Flask #5818: a new leading ctx parameter took the exception).
                _emit("misfit", target=target["id"])
                return
            generator = bool(flags & 0x20)
            untouched = _fp([pos, kw]) if blob[0] == "live" else None
            runs = [_run(base, blob, generator), _run(base, blob, generator),
                    _run(head, blob, generator, True, rebind), _run(head, blob, generator, True, rebind)]
            if untouched is not None and any(run["args_after"] != untouched for run in runs):
                _emit("skipped", target=target["id"], reason="changes arguments that cannot be copied; not compared on them")
                return
            stable = runs[0] == runs[1] and runs[2] == runs[3]
            differs = [field for field in ("result", "args_after", "stdout", "calls") if runs[0][field] != runs[2][field]]
            try:
                shown = repr(_thaw(blob))
            except Exception:
                shown = "<input>"
            if assumed:
                shown += " [as changed: " + ", ".join(assumed) + " left at the new default, assuming the caller did not pass it]"
            _emit("result", **dict(target=target["id"], shape=shape, copied=blob[0] != "live", test=_os.environ.get("AMU_TEST_CONTEXT") or _os.environ.get("PYTEST_CURRENT_TEST", ""),
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
                _emit("skipped", target=target["id"], reason="coroutine or async generator")
                return _m.DISABLE
            # Sampled by argument shape, not first come: up to _limit calls of each shape, up to _shapes shapes. A run's
            # first five calls are often alike; the edge case a change is about is another shape (None, empty, zero).
            frame = _sys._getframe(1)
            try:
                pos, kw = _arguments(code, frame)
            except Exception:
                return None
            shape = _shape(pos, kw)
            shapes = _taken.setdefault(target["id"], {})
            seen = shapes.get(shape, 0)
            if seen >= _limit or (shape not in shapes and len(shapes) >= _shapes):
                if len(shapes) >= _shapes and all(n >= _limit for n in shapes.values()):
                    return _m.DISABLE
                return None
            shapes[shape] = seen + 1
            _count[target["id"]] = _count.get(target["id"], 0) + 1
            _emit("captured", target=target["id"], shape=shape)
            _local.busy, _local.calls = True, None
            _m.set_events(_tool, _m.events.PY_START | _m.events.CALL)
            try:
                _compare(target, code, frame, pos, kw, shape)
            except BaseException as exc:
                _emit("skipped", target=target["id"], reason="could not be compared: " + type(exc).__name__ + ": " + str(exc)[:160])
            finally:
                _m.set_events(_tool, _m.events.PY_START)
                _local.busy = False
            return None

        _m.register_callback(_tool, _m.events.PY_START, _start)
        _m.register_callback(_tool, _m.events.CALL, _external)
        _m.set_events(_tool, _m.events.PY_START)

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
        # The environment has none of its own: the import that loaded this file expects to find it here.
        _sys.modules["sitecustomize"] = _self
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


def _code(node):
    """The def statement as it runs: its syntax tree without decorators (never compared) and without the docstring."""
    node = copy.copy(node)
    node.decorator_list = []
    if ast.get_docstring(node, clean=False) is not None:
        node.body = node.body[1:]
    return ast.dump(node)


def targets(functions, before_src, after_src):
    """The spec entries for changed functions that exist on both sides and can be compared, and {id: reason} for those
    that cannot. ``functions`` are analyze's changed_functions rows (file, symbol). A function in a test file, or one
    whose code is the same once its docstring is set aside, is not a target either; the generated tier gets these
    entries too."""
    found, skipped = [], {}
    for row in functions:
        path, qual = row['file'], row['symbol']
        ident = f'{path}#{qual}'
        if replay.is_test_file(path):
            skipped[ident] = 'in a test file'
            continue
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
        if _code(old) == _code(new):
            skipped[ident] = 'only its docstring, comments or layout changed'
            continue
        if '.' in qual and (PRIVATE.search(old_text) or PRIVATE.search(new_text)):
            skipped[ident] = 'uses a name a class body would mangle'
            continue
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(old)) != any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(new)):
            skipped[ident] = 'a generator on one side only'
            continue
        def positional(node):
            return [a.arg for a in [*node.args.posonlyargs, *node.args.args]]
        found.append(dict(id=ident, file=path, qual=qual, name=qual.rsplit('.', 1)[-1], before=old_text, after=new_text,
                          signature_changed=ast.dump(old.args) != ast.dump(new.args),
                          params_before=positional(old), params_after=positional(new),
                          positional_only=bool(new.args.posonlyargs), defaults=_changed_defaults(old, new, before, after)))
    return found, skipped


def _defaults(node, text):
    """{parameter: source of its default} for a def statement."""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    found = {a.arg: ast.get_source_segment(text, d) for a, d in zip(positional[len(positional) - len(args.defaults):], args.defaults)}
    found.update({a.arg: ast.get_source_segment(text, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None})
    return found


def _changed_defaults(old, new, before, after):
    """{parameter: [default before, default after]} for parameters on both sides whose default the change alters."""
    was, now = _defaults(old, before), _defaults(new, after)
    return {name: [was[name], now[name]] for name in was.keys() & now.keys() if was[name] and now[name] and was[name] != now[name]}


def _defined(statement):
    names = set()
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(statement.name)
    elif isinstance(statement, (ast.Import, ast.ImportFrom)):
        names.update((a.asname or a.name.split('.')[0]) for a in statement.names)
    elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target]):
            names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
    return names


def _binds(body):
    """Names a class body binds, counting those bound anywhere inside its if, try, with and for blocks."""
    names = set()
    for statement in body:
        nodes = [statement] if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else ast.walk(statement)
        for node in nodes:
            if isinstance(node, ast.stmt):
                names |= _defined(node)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
    return names


def additions(before, after, path=''):
    """What the change adds next to its changed functions, for the as-changed runs: top-level statements of ``after`` that
    define a name ``before`` does not (owner ''), and members of a class on both sides that only ``after`` defines (owner
    the class's qualified name), in file order, as {file, owner, source}. Then, per class on both sides, the members only
    ``before`` defines, as {file, owner, removed}: left on the class, one would shadow what it now inherits."""
    try:
        old, new = ast.parse(before), ast.parse(after)
    except (SyntaxError, ValueError):
        return []
    lines, found = after.splitlines(), []

    def segment(statement):
        start = min([statement.lineno, *(d.lineno for d in getattr(statement, 'decorator_list', []))])
        return textwrap.dedent('\n'.join(lines[start - 1:statement.end_lineno])) + '\n'

    def walk(old_body, new_body, owner, decorated=False, rebased=False):
        known = set().union(*(_defined(s) for s in old_body)) if old_body else set()
        classes = {s.name: s for s in old_body if isinstance(s, ast.ClassDef)}
        for statement in new_body:
            names = _defined(statement)
            if isinstance(statement, ast.ClassDef) and statement.name in classes:
                was = classes[statement.name]
                # A class whose bases change cannot be given the head's MRO: deleting a member would expose the old bases.
                bases = [ast.dump(e) for e in was.bases + was.keywords] != [ast.dump(e) for e in statement.bases + statement.keywords]
                walk(was.body, statement.body, (owner + '.' if owner else '') + statement.name, bool(statement.decorator_list),
                     bases)
            elif names and not names <= known:
                found.append(dict(file=path, owner=owner, source=segment(statement)))
        # A class decorator (dataclass, total_ordering) may make again a dunder the change deletes: those stay.
        # A class attribute the change moves onto the instance stays: objects built before the change lack the new one.
        selfs = {n.attr for s in new_body for n in ast.walk(s) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                 and isinstance(n.value, ast.Name) and n.value.id == 'self'}
        gone = ({n for n in known - _binds(new_body) - selfs if not (decorated and n[:2] == n[-2:] == '__')}
                if owner and not rebased else ())
        if gone:
            found.append(dict(file=path, owner=owner, removed=sorted(gone)))
    walk(old.body, new.body, '')
    return found


def support(scratch, tree, spec_targets, added=(), modules=()):
    folder = Path(scratch)/'side-by-side'
    (folder/'out').mkdir(parents=True, exist_ok=True)
    (folder/'hook').mkdir(exist_ok=True)
    (folder/'hook'/'sitecustomize.py').write_text(HOOK)
    rows = [dict(t, file=str(Path(tree)/t['file'])) for t in spec_targets]
    extra = [dict(a, file=str(Path(tree)/a['file'])) for a in added]
    new = [dict(m, file=str(Path(tree)/m['file'])) for m in modules]
    (folder/'spec.json').write_text(json.dumps(dict(root=str(tree), targets=rows, additions=extra, modules=new, calls=CALLS,
                                                    shapes=SHAPES)))
    return folder


def fresh_attributes(before, after):
    """(class, attribute) pairs the change starts setting when an object is built, in one file: ``self.name = ...``
    inside ``__init__``, ``__new__`` or ``__post_init__`` of a class (or of a base class defined in the same file) in
    ``after`` and not in that class in ``before``. Every object of that class the code after the change builds has them;
    one the code before it built does not. Per class, not per file: another class of the file already setting the name
    before the change says nothing about this one (Flask #5812: the old ``RequestContext`` set
    ``_after_request_functions``; the new ``AppContext`` does). (An attribute set by some other method is left out: an
    object built after the change can lack it too, so missing it may be a real regression.)"""
    def names(text):
        try:
            tree = ast.parse(text or '')
        except (SyntaxError, ValueError):
            return {}
        own, bases = {}, {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]
            found = own.setdefault(node.name, set())
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in ('__init__', '__new__', '__post_init__'):
                    for inner in ast.walk(item):
                        if isinstance(inner, ast.Attribute) and isinstance(inner.ctx, ast.Store) and isinstance(inner.value, ast.Name) \
                                and inner.value.id in ('self', 'cls', 'obj', 'instance'):
                            found.add(inner.attr)

        def every(name, seen=()):
            if name in seen or name not in own:
                return set()
            return own[name].union(*(every(b, (*seen, name)) for b in bases.get(name, ())))
        return {name: every(name) for name in own}
    old, new = names(before), names(after)
    return {(cls, attr) for cls, attrs in new.items() for attr in attrs - old.get(cls, set())}


ATTRIBUTE = re.compile(r"'(\w+)' object has no attribute '(\w+)'")


def run(runner, python, before_tree, ids, spec_targets, skipped, scratch, timeout, added=(), modules=(), prefer=(),
        fresh=()):
    """Run ``ids`` on the code before the change with the hook. Returns (info, differences, gaps). Tests in ``prefer``
    (seen running a changed line) run first, so theirs are the calls a full shape keeps."""
    gaps = []
    first = set(prefer)
    ids = sorted(ids, key=lambda i: not any(i == p or i.startswith(p + '::') for p in first)) if first else list(ids)
    if not spec_targets:
        return dict(targets=0, skipped=skipped), [], gaps
    folder = support(scratch, before_tree, spec_targets, added, modules)
    env = dict(AMU_TRUST_PATH=str(folder/'hook'), AMU_SBS_SPEC=str(folder/'spec.json'), AMU_SBS_OUT=str(folder/'out'))
    _, problem = runners.execute(runner, python, before_tree, ids, Path(scratch)/'side-by-side.xml', timeout, extra_env=env)
    if problem and problem != 'timed out':
        gaps.append(f'side-by-side run ({runner.name}): {problem}')
    elif problem:
        gaps.append('side-by-side run stopped at its time limit; calls captured until then are compared')
    stopped = problem == 'timed out'
    results, captured, misfit_calls = [], {}, 0
    for part in sorted((folder/'out').glob('*.jsonl')):
        try:
            lines = part.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a line cut short by the time limit
            kind = event.pop('kind', None)
            if kind == 'result':
                results.append(event)
            elif kind == 'skipped':
                skipped[event['target']] = event['reason']
            elif kind == 'captured':
                captured[event['target']] = captured.get(event['target'], 0) + 1
            elif kind == 'misfit':
                misfit_calls += 1
    # An object built by the code before the change lacks state a changed method now gives it. When one changed method
    # of a class changes the object's state, an AttributeError from another changed method of that class says the input
    # was built by the old code, not that the method behaves differently: set aside, and counted.
    stateful = {row['target'].rsplit('.', 1)[0] for row in results
                if row['stable'] and 'args_after' in row['differs'] and '.' in row['target'].split('#', 1)[1]}
    # A call captured from the old code does not fit a function whose parameters the change altered: the as-changed
    # version refuses it at the call. That is a different signature, not a behaviour seen on the input: set aside.
    reshaped = {t['id'] for t in spec_targets if t.get('signature_changed')}
    seen, differences, built_before, misfit = set(), [], 0, misfit_calls
    for row in results:
        after = row['after']['result']
        if (row['target'] in reshaped and after[0] == 'raise' and after[1] == 'TypeError'
                and after[2].startswith(row['target'].rsplit('#', 1)[1] + '()') and 'argument' in after[2]):
            misfit += 1
            continue
        missing = ATTRIBUTE.search(after[2]) if after[0] == 'raise' and after[1] == 'AttributeError' else None
        if row['stable'] and row['differs'] and missing and (missing[1], missing[2]) in fresh and row['before']['result'][:2] != after[:2]:
            # The changed code reads an attribute the change starts setting, on an object the code before it built
            # (Flask #5812: 'AppContext' object has no attribute '_request'): the input, not the behaviour.
            built_before += 1
            continue
        if (row['stable'] and row['differs'] and row['target'].rsplit('.', 1)[0] in stateful and 'args_after' not in row['differs']
                and after[0] == 'raise' and after[1] == 'AttributeError' and row['before']['result'][0] != 'raise'):
            built_before += 1
            continue
        if row['stable'] and row['differs']:
            key = (row['target'], row['input'])
            if key not in seen:
                seen.add(key)
                differences.append(dict(row, label=KIND))
    info = dict(targets=len(spec_targets), compared_calls=len(results), functions_called=len(captured), called=sorted(captured),
                unstable=sum(not r['stable'] for r in results), differences=len(differences), skipped=skipped,
                input_built_before_the_change=built_before, calls_not_fitting_a_changed_signature=misfit)
    # Which calls were compared: per target, its argument shape, the input and the test it came from.
    info['captured'] = [dict(target=r['target'], shape=r.get('shape', '?'), input=str(r.get('input', ''))[:200],
                             test=str(r.get('test', ''))[:200]) for r in results[:LISTED]]
    if len(results) > LISTED:
        info['captured_not_listed'] = len(results) - LISTED
    if stopped:
        info['stopped'] = True  # calls after the time limit were not seen: none captured does not mean none made
    if not results:
        gaps.append(f'side-by-side: none of the {len(spec_targets)} comparable changed function(s) was called by the tests run on the code before the change')
    return info, differences, gaps
