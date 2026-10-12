"""Bounded source-only Python return/exception flow, independent of provider edges.

No modules are imported. Summaries are request-local and recomputed for both
source snapshots; they are never persisted across dependency changes.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .change_analysis import _bindings, _dynamic_namespace, _location
from .outbound import redact

VERSION = "python-value-flow-v2"
MAX_FUNCTIONS = 1024
MAX_STEPS = 500_000
MAX_HOPS = 3
MAX_ROWS = 1024
MAX_UNKNOWN = 200
MAX_IMPORT_HOPS = 8
MAX_TEST_OBSERVERS = 512
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
EXCEPTIONS = frozenset(
    name for name, obj in vars(builtins).items() if isinstance(obj, type) and issubclass(obj, BaseException)
)


def _selector(value):
    return hashlib.sha256((type(value).__name__ + ":" + repr(value)).encode()).hexdigest()


def _marker_literal(node, depth=0):
    """Bounded literal marker metadata, including literal string concatenation.

    This never evaluates a call, name, attribute, comprehension or target code.
    """
    if depth > 20:
        raise ValueError("marker literal depth")
    if isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool, type(None)):
        if isinstance(node.value, str) and len(node.value) > 8192:
            raise ValueError("marker string limit")
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) <= 64:
        return [_marker_literal(v, depth + 1) for v in node.elts]
    if isinstance(node, ast.Dict) and len(node.keys) <= 64:
        return {_marker_literal(k, depth + 1): _marker_literal(v, depth + 1) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.BinOp):
        left, right = _marker_literal(node.left, depth + 1), _marker_literal(node.right, depth + 1)
        if isinstance(node.op, ast.Add) and type(left) is str and type(right) is str and len(left) + len(right) <= 8192:
            return left + right
        if (
            isinstance(node.op, ast.Mult)
            and type(left) is str
            and type(right) is int
            and 0 <= right <= 8192
            and len(left) * right <= 8192
        ):
            return left * right
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in (int, float)
    ):
        return -node.operand.value
    raise ValueError("unsupported marker metadata")


def _parameterized_test(node, table, class_bindings=()):
    if not node.name.startswith("test_") or not node.decorator_list:
        return False
    try:
        for decorator in node.decorator_list:
            func = decorator.func if isinstance(decorator, ast.Call) else None
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "parametrize"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "mark"
                and isinstance(func.value.value, ast.Name)
                and table.get(func.value.value.id) == ("module", "pytest")
                and func.value.value.id not in class_bindings
                and len(decorator.args) == 2
                and sum(1 for _ in ast.walk(decorator)) <= 1024
            ):
                return False
            names, values = (_marker_literal(arg) for arg in decorator.args)
            names = [p.strip() for p in names.split(",")] if isinstance(names, str) else names
            if not (
                isinstance(names, list)
                and names
                and all(isinstance(p, str) and p.isidentifier() for p in names)
                and isinstance(values, list)
                and len(values) <= 64
            ):
                return False
            for keyword in decorator.keywords:
                value = _marker_literal(keyword.value)
                if keyword.arg is None or keyword.arg == "indirect" and value is not False:
                    return False
        return True
    except (ValueError, TypeError, RecursionError, MemoryError):
        return False


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str

    @property
    def id(self):
        return f"{self.path}#{self.name}" if self.path != "builtins" else f"builtins.{self.name}"


class Resolver:
    """Resolve unique source layouts, explicit exports and stable aliases."""

    def __init__(self, sources, syntax_facts=None):
        self.sources = sources
        self.tables = {}
        self.unknowns = []
        self._unknown_keys = set()
        self._unknown_limit_reported = False
        self._syntax_facts = syntax_facts if syntax_facts is not None else {}
        self.functions = {}
        self.test_observers = {}
        self.modules = {}
        self.steps = 0
        self.exhausted = False
        for path, source in sorted(sources.items()):
            module = path.removeprefix("src/").removesuffix(".py").removesuffix("/__init__").replace("/", ".")
            self.modules.setdefault(module, []).append(path)
            table = {}
            tree = source["tree"]
            dynamic = _dynamic_namespace(tree) or any(
                isinstance(n, ast.ImportFrom)
                and any(a.name == "*" for a in n.names)
                or isinstance(n, FUNCTIONS)
                and n.name == "__getattr__"
                for n in tree.body
            )
            if path.endswith("/__init__.py"):
                dynamic |= any(
                    not (
                        isinstance(n, (ast.Import, ast.ImportFrom, ast.Pass))
                        or isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str)
                        or isinstance(n, (ast.Assign, ast.AnnAssign))
                        and isinstance(n.value, (ast.Constant, ast.Name, ast.Attribute))
                    )
                    for n in tree.body
                )
                if any(isinstance(n, ast.Call) for n in ast.walk(tree)):
                    dynamic = True
                if dynamic:
                    self.unknown(path, tree, "executable or dynamic package initializer leaves exports unresolved")
            for node in tree.body:
                pairs = []
                if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    pairs = [(node.name, ("definition", node))]
                elif isinstance(node, ast.Import):
                    pairs = [
                        (a.asname or a.name.split(".")[0], ("module", a.name if a.asname else a.name.split(".")[0]))
                        for a in node.names
                    ]
                elif isinstance(node, ast.ImportFrom):
                    module = self.relative_module(path, node.module, node.level)
                    pairs = [(a.asname or a.name, ("import", module, a.name)) for a in node.names if a.name != "*"]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    pairs = [
                        (n.id, ("alias", node.value)) for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)
                    ]
                else:
                    pairs = [(n, ("unknown",)) for n in _bindings(ast.Module(body=[node], type_ignores=[]))]
                for name, fact in pairs:
                    table[name] = ("unknown",) if name in table or dynamic else fact
            # Attribute mutation of imported objects invalidates that root too.
            for node in tree.body:
                if isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                    continue
                for use in ast.walk(node):
                    if isinstance(use, ast.Attribute) and isinstance(use.ctx, (ast.Store, ast.Del)):
                        root = use.value
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if isinstance(root, ast.Name):
                            table[root.id] = ("unknown",)
            self.tables[path] = table
        for path, table in self.tables.items():
            for name, fact in table.items():
                if fact[0] == "definition" and isinstance(fact[1], ast.FunctionDef) and not fact[1].decorator_list:
                    if len(self.functions) >= MAX_FUNCTIONS:
                        self.unknown(path, fact[1], "function limit reached; further functions not analyzed")
                        continue
                    self.functions[f"{path}#{name}"] = (path, name, fact[1])
                elif fact[0] == "definition" and isinstance(fact[1], FUNCTIONS) and fact[1].decorator_list:
                    if _parameterized_test(fact[1], table):
                        self.add_test_observer(path, name, fact[1])
                    else:
                        self.unknown(path, fact[1], "decorated function behavior unresolved")
                elif fact[0] == "definition" and isinstance(fact[1], ast.ClassDef):
                    owner = fact[1]
                    counts = {}
                    for child in owner.body:
                        if isinstance(child, FUNCTIONS):
                            counts[child.name] = counts.get(child.name, 0) + 1
                    for child in owner.body:
                        if not isinstance(child, ast.FunctionDef) or counts[child.name] != 1:
                            continue
                        if child.decorator_list:
                            if (
                                owner.name.startswith("Test")
                                and not owner.decorator_list
                                and _parameterized_test(child, table, _bindings(owner))
                            ):
                                self.add_test_observer(path, owner.name + "::" + child.name, child)
                            else:
                                self.unknown(
                                    path, child, "decorated member body not composed; descriptor dispatch unresolved"
                                )
                            continue
                        if len(self.functions) >= MAX_FUNCTIONS:
                            self.unknown(path, child, "function limit reached; further methods not analyzed")
                            if (
                                owner.name.startswith("Test")
                                and not owner.decorator_list
                                and child.name.startswith("test_")
                            ):
                                self.add_test_observer(path, owner.name + "::" + child.name, child)
                            continue
                        name = owner.name + ("::" if owner.name.startswith("Test") else ".") + child.name
                        self.functions[f"{path}#{name}"] = (path, name, child)

    def add_test_observer(self, path, name, node):
        from .plan import _is_test_path

        if not _is_test_path(path):
            self.unknown(path, node, "parameterized declaration outside baseline test scope remains unresolved")
        elif len(self.test_observers) >= MAX_TEST_OBSERVERS:
            self.unknown(path, node, "test observer limit reached; further result assertions unresolved")
        elif sum(1 for _ in ast.walk(node)) > 2048:
            self.unknown(path, node, "test observer node limit reached; result assertions unresolved")
        else:
            self.test_observers[f"{path}#{name}"] = (path, name, node)

    @staticmethod
    def relative_module(path, module, level):
        if not level:
            return module or ""
        parts = list(PurePosixPath(path.removeprefix("src/")).parent.parts)
        if level > len(parts):
            return ""
        return ".".join(parts[: len(parts) - level + 1] + ((module or "").split(".") if module else []))

    def lexical_facts(self, node):
        """Reuse immutable AST facts within this request, never dependency facts."""
        if node not in self._syntax_facts:
            self._syntax_facts[node] = (frozenset(_bindings(node)), _dynamic_namespace(node))
        return self._syntax_facts[node]

    def unknown(self, path, node, reason):
        row = {"path": path, "location": _location(node), "reason": reason, "analyzer": VERSION}
        key = (path, tuple(row["location"].values()), reason)
        if key in self._unknown_keys:
            return
        if len(self.unknowns) < MAX_UNKNOWN:
            self._unknown_keys.add(key)
            self.unknowns.append(row)
        elif not self._unknown_limit_reported:
            self._unknown_limit_reported = True
            self.unknowns[-1] = {
                **row,
                "reason": "uncertainty limit reached; additional unresolved boundaries omitted",
            }

    def module(self, name):
        paths = self.modules.get(name, [])
        return Symbol(paths[0], "", "module") if len(paths) == 1 else None

    def export(self, path, name, seen=()):
        key = (path, name)
        if key in seen or len(seen) >= MAX_IMPORT_HOPS:
            return None
        fact = self.tables.get(path, {}).get(name)
        if fact is None:
            if path.endswith("/__init__.py"):
                if any(f[0] == "unknown" for f in self.tables.get(path, {}).values()):
                    return None
                module = path.removeprefix("src/").removesuffix("/__init__.py").replace("/", ".")
                return self.module(module + "." + name)
            return Symbol("builtins", name, "exception") if name in EXCEPTIONS else None
        if fact[0] == "definition":
            node = fact[1]
            if node.decorator_list:
                return None
            return Symbol(path, name, "class" if isinstance(node, ast.ClassDef) else "function")
        if fact[0] == "module":
            return self.module(fact[1])
        if fact[0] == "import":
            target = self.module(fact[1])
            return self.export(target.path, fact[2], (*seen, key)) if target else None
        if fact[0] == "alias" and fact[1] is not None:
            return self.expr(path, fact[1], {}, set(), (*seen, key))
        return None

    def expr(self, path, node, env, local, seen=()):
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            if isinstance(value, Symbol):
                return value
            if node.id in local:
                return None
            return self.export(path, node.id, seen)
        if isinstance(node, ast.Attribute):
            root = self.expr(path, node.value, env, local, seen)
            if root and root.kind == "module":
                return self.export(root.path, node.attr, seen)
        return None

    def exception(self, path, node, env, local, seen=()):
        symbol = self.expr(path, node.func if isinstance(node, ast.Call) else node, env, local)
        if symbol and symbol.kind == "exception":
            return symbol.id
        if symbol and symbol.kind == "class":
            if symbol.id in seen or len(seen) >= MAX_IMPORT_HOPS:
                return None
            fact = self.tables[symbol.path].get(symbol.name)
            bases = fact[1].bases
            if len(bases) == 1 and self.exception(symbol.path, bases[0], {}, set(), (*seen, symbol.id)):
                return symbol.id
        return None


@dataclass
class Value:
    # Every origin has a located call/return path. Parameter origins are local
    # placeholders and only composed when the exact argument is established.
    origins: dict = field(default_factory=dict)
    none: bool = False
    parameters: set = field(default_factory=set)


def union(values):
    result = Value()
    for value in values:
        if isinstance(value, Value):
            result.origins.update(value.origins)
            result.none |= value.none
            result.parameters.update(value.parameters)
    return result


class Scan:
    def __init__(self, resolver, function_id, summaries, phase, emit):
        self.r, self.id, self.summaries, self.phase, self.emit = resolver, function_id, summaries, phase, emit
        self.path, self.name, self.node = resolver.functions.get(function_id) or resolver.test_observers[function_id]
        self.local = set(resolver.lexical_facts(self.node)[0])
        self.env = {
            a.arg: Value(parameters={i}) for i, a in enumerate([*self.node.args.posonlyargs, *self.node.args.args])
        }
        self.returns = Value()
        self.raises = set()
        self.exception_origins = {}
        self.handled_values = {}
        self.handler_value = Value()
        self.records = []
        self.assertions = []
        self.calls = set()
        self.incomplete = False
        self.stopped = False

    def unknown(self, path, node, reason):
        self.incomplete = True
        self.r.unknown(path, node, reason)

    def step(self, node):
        self.r.steps += 1
        if self.r.steps > MAX_STEPS:
            self.r.exhausted = True
            self.incomplete = True
            self.unknown(self.path, node, "analysis step limit reached; remaining flow not analyzed")
            return False
        return True

    def evidence(self, node, role):
        return {
            "path": self.path,
            "phase": self.phase,
            "location": _location(node),
            "source_sha256": self.r.sources[self.path]["sha256"],
            "role": role,
            "expression": redact(ast.unparse(node))[:240],
        }

    def record(self, value, node, operation, constraints):
        if not self.emit or not isinstance(value, Value):
            return
        for origin, path in value.origins.items():
            row = {
                "source_id": origin,
                "consumer_id": self.id,
                "operation": operation,
                "phase": self.phase,
                "source_relationship": True,
                "value_may_reach_consumer": operation not in {"call", "exception"},
                "constraints": constraints,
                "path": [*path, self.evidence(node, operation)],
                "observed_execution": False,
                "regression_reproduced": False,
                "provenance": "source:python-ast:" + VERSION,
            }
            if row not in self.records:
                self.records.append(row)
            if operation == "assertion" and (self.name.startswith("test_") or "::test_" in self.name):
                self.assertions.append(
                    {
                        "test_id": self.path + "::" + self.name,
                        "entity_id": origin,
                        "assertion": self.evidence(node, "assertion"),
                        "path": row["path"],
                        "constraints": constraints,
                        "observed_execution": False,
                        "coverage_proven": False,
                        "phase": self.phase,
                    }
                )

    def expr(self, node, constraints, handlers):
        if node is None or not self.step(node):
            return Value()
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            return self.r.expr(self.path, node, self.env, self.local) or Value()
        if isinstance(node, ast.Constant):
            return Value(none=node.value is None)
        if isinstance(node, ast.Call):
            symbol = self.r.expr(self.path, node.func, self.env, self.local)
            args = [self.expr(arg, constraints, handlers) for arg in node.args]
            keyword_args = [self.expr(k.value, constraints, handlers) for k in node.keywords]
            if self.stopped:
                return Value()
            if any(isinstance(value, Symbol) for value in [*args, *keyword_args]):
                self.stopped = True
                self.unknown(
                    self.path, node, "callable/module binding escaped to a call; subsequent alias identity unresolved"
                )
                return Value()
            if symbol and symbol.kind == "function" and symbol.id in self.r.functions:
                self.calls.add(symbol.id)
                call_step = self.evidence(node, "resolved_call")
                value = Value({symbol.id: [call_step]})
                summary = self.summaries.get(symbol.id, {})
                if symbol.id == self.id:
                    self.incomplete = True
                    self.unknown(self.path, node, "call cycle stops summary composition")
                for origin, steps in summary.get("return_sources", {}).items():
                    if sum(step.get("role") == "resolved_call" for step in steps) < MAX_HOPS and origin != self.id:
                        value.origins[origin] = [call_step, *steps]
                    else:
                        self.incomplete = True
                        self.unknown(self.path, node, "summary hop limit or cycle reached")
                if not node.keywords and not any(isinstance(a, ast.Starred) for a in node.args):
                    for i in summary.get("return_parameters", []):
                        if i < len(args) and isinstance(args[i], Value):
                            for origin, steps in args[i].origins.items():
                                value.origins[origin] = [*steps, call_step]
                            value.parameters.update(args[i].parameters)
                elif summary.get("return_parameters"):
                    self.incomplete = True
                    self.unknown(self.path, node, "keyword/starred argument forwarding is outside the supported subset")
                value.none = summary.get("may_return_none", False)
                self.record(Value({symbol.id: [call_step]}), node, "call", constraints)
                for exc, paths in summary.get("exceptions", {}).items():
                    active = next((h for h in handlers if h["exception_id"] == exc), None)
                    route = [active] if active else []
                    if not active:
                        self.raises.add(exc)
                        origins = self.exception_origins.setdefault(exc, {})
                        origins[symbol.id] = [call_step]
                        for origin, steps in paths.items():
                            if (
                                sum(step.get("role") == "resolved_call" for step in steps) < MAX_HOPS
                                and origin != self.id
                            ):
                                origins[origin] = [call_step, *steps]
                            else:
                                self.incomplete = True
                                self.unknown(self.path, node, "exception summary hop limit or cycle reached")
                    exception_value = Value({symbol.id: [call_step]})
                    for origin, steps in paths.items():
                        if sum(step.get("role") == "resolved_call" for step in steps) < MAX_HOPS:
                            exception_value.origins[origin] = [call_step, *steps]
                    if active:
                        key = active["location"]["line"]
                        transformed = Value(
                            {origin: [*steps, active] for origin, steps in exception_value.origins.items()}
                        )
                        self.handled_values[key] = union([self.handled_values.get(key, Value()), transformed])
                    self.record(exception_value, node, "exception", [*constraints, *route])
                if summary.get("incomplete"):
                    self.incomplete = True
                return value
            # A known builtin may consume or transform data, but arbitrary
            # call semantics do not establish an observing result.
            flows = union([*args, *keyword_args])
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            if isinstance(receiver, ast.Name) and isinstance(self.env.get(receiver.id), Value):
                flows = union([flows, self.env[receiver.id]])
            if flows.origins:
                self.unknown(self.path, node, "unsupported call or mutation stops value propagation")
                for name, saved in list(self.env.items()):
                    if isinstance(saved, Value) and set(saved.origins) & set(flows.origins):
                        self.env[name] = Value()
            # Property/method dispatch deliberately remains a boundary.
            if not symbol or symbol.kind != "function" or symbol.id not in self.r.functions:
                self.unknown(
                    self.path, node, "call target unresolved: dynamic dispatch, shadowing or unsupported binding"
                )
            return Value()
        if isinstance(node, ast.Subscript):
            value = self.expr(node.value, constraints, handlers)
            self.expr(node.slice, constraints, handlers)
            self.record(value, node, "index", constraints)
            if isinstance(value, Value):
                step = self.evidence(node, "index")
                if isinstance(node.slice, ast.Constant) and type(node.slice.value) in (str, int):
                    step["selector"] = _selector(node.slice.value)
                return Value(
                    {origin: [*steps, step] for origin, steps in value.origins.items()},
                    parameters=set(value.parameters),
                )
            return Value()
        if isinstance(node, ast.Attribute):
            symbol = self.r.expr(self.path, node, self.env, self.local)
            if symbol:
                return symbol
            value = self.expr(node.value, constraints, handlers)
            self.record(value, node, "attribute", constraints)
            # Descriptor dispatch can produce an unrelated value.
            if isinstance(value, Value) and value.origins:
                self.unknown(self.path, node, "attribute access exists; descriptor result identity unresolved")
            return Value()
        if isinstance(node, (ast.Compare, ast.BinOp, ast.UnaryOp, ast.Tuple, ast.List, ast.Set, ast.Dict)):
            # Containers preserve dependency, comparisons observe their input.
            value = union(
                [
                    self.expr(child, constraints, handlers)
                    for child in ast.iter_child_nodes(node)
                    if isinstance(child, ast.expr)
                ]
            )
            if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)) and value.origins:
                self.unknown(self.path, node, "container element aliasing stops value propagation")
                return Value()
            value.none = False
            return value
        self.unknown(self.path, node, "unsupported expression stops value propagation")
        return Value()

    def guard(self, test):
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and len(test.ops) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
            and isinstance(test.ops[0], (ast.Is, ast.IsNot))
        ):
            none = isinstance(test.ops[0], ast.Is)

            def condition(is_none):
                return {
                    "kind": "is-none" if is_none else "not-none",
                    "variable": test.left.id,
                    **self.evidence(test, "guard"),
                }

            return condition(none), condition(not none)
        return None

    def assign(self, target, value):
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self.assign(t, Value())
            if isinstance(value, Value) and value.origins:
                self.unknown(self.path, target, "destructured result fields are outside the supported subset")
        else:
            self.incomplete = True
            self.unknown(self.path, target, "mutation/state write stops alias and state reasoning")
            root = target
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name):
                self.local.add(root.id)
                self.env[root.id] = Value()
            self.env = {k: v if isinstance(v, Symbol) else Value() for k, v in self.env.items()}

    def block(self, statements, constraints=(), handlers=()):
        constraints = list(constraints)
        for node in statements:
            if self.stopped:
                return False
            if not self.step(node):
                return False
            if isinstance(node, ast.Return):
                value = self.expr(node.value, constraints, handlers) if node.value is not None else Value(none=True)
                # A literal handler result is constrained by the established
                # exception source, unlike an unrelated later return/raise.
                if self.handler_value.origins:
                    value = union([value, self.handler_value])
                self.returns = union([self.returns, value])
                self.record(value, node, "return", constraints)
                return True
            if isinstance(node, ast.Raise):
                exc = self.r.exception(self.path, node.exc, self.env, self.local) if node.exc else None
                if exc:
                    if not any(h["exception_id"] == exc for h in handlers):
                        self.raises.add(exc)
                        self.exception_origins.setdefault(exc, {})[self.id] = [self.evidence(node, "explicit_raise")]
                else:
                    self.incomplete = True
                    self.unknown(self.path, node, "raised exception identity unresolved")
                return True
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = self.expr(node.value, constraints, handlers)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    self.assign(target, value)
            elif isinstance(node, ast.Expr):
                self.expr(node.value, constraints, handlers)
            elif isinstance(node, ast.Assert):
                value = self.expr(node.test, constraints, handlers)
                self.record(value, node, "assertion", constraints)
            elif isinstance(node, ast.If):
                self.expr(node.test, constraints, handlers)
                guard = self.guard(node.test)
                if guard is None:
                    self.unknown(
                        self.path,
                        node.test,
                        "conditional reachability unresolved; only explicit None guards are interpreted",
                    )
                    guard = (
                        {"kind": "unresolved-condition", "branch": "then", **self.evidence(node.test, "condition")},
                        {"kind": "unresolved-condition", "branch": "else", **self.evidence(node.test, "condition")},
                    )
                original = dict(self.env)
                left_done = self.block(node.body, [*constraints, *([guard[0]] if guard else [])], handlers)
                if self.stopped:
                    return False
                left = dict(self.env)
                self.env = dict(original)
                right_done = self.block(node.orelse, [*constraints, *([guard[1]] if guard else [])], handlers)
                if self.stopped:
                    return False
                right = dict(self.env)
                if left_done and right_done:
                    return True
                if left_done:
                    self.env = right
                    if guard:
                        constraints.append(guard[1])
                elif right_done:
                    self.env = left
                    if guard:
                        constraints.append(guard[0])
                else:
                    self.env = {k: v for k, v in left.items() if k in right and v == right[k]}
                    if any(
                        isinstance(v, Value) and v.origins for k, v in {**left, **right}.items() if k not in self.env
                    ):
                        self.unknown(self.path, node, "branch-dependent result binding is unresolved after the join")
                for key in self.local:
                    self.env.setdefault(key, Value())
            elif isinstance(node, ast.Try) and not node.finalbody:
                active = []
                for handler in node.handlers:
                    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                    for item in types:
                        exc = self.r.exception(self.path, item, self.env, self.local) if item else None
                        if exc:
                            active.append(
                                {"kind": "exception-handler", "exception_id": exc, **self.evidence(handler, "handler")}
                            )
                        else:
                            self.incomplete = True
                            self.unknown(self.path, handler, "exception handler identity unresolved")
                original = dict(self.env)
                body_done = self.block(node.body, constraints, [*active, *handlers])
                if self.stopped:
                    return False
                else_done = self.block(node.orelse, constraints, handlers) if not body_done else False
                if self.stopped:
                    return False
                # No general merge or assumed handler reachability.
                self.env = dict(original)
                handler_done = []
                previous_handler_value = self.handler_value
                for handler in node.handlers:
                    if handler.name:
                        self.env[handler.name] = Value()
                    self.handler_value = self.handled_values.get(handler.lineno, Value())
                    handler_constraint = next((h for h in active if h["location"]["line"] == handler.lineno), None)
                    handler_done.append(
                        self.block(
                            handler.body,
                            [*constraints, *([handler_constraint] if handler_constraint else [])],
                            handlers,
                        )
                    )
                    if self.stopped:
                        return False
                self.handler_value = previous_handler_value
                self.env = {k: v if isinstance(v, Symbol) else Value() for k, v in original.items()}
                if (body_done or else_done) and all(handler_done):
                    return True
            elif isinstance(node, ast.For):
                value = self.expr(node.iter, constraints, handlers)
                self.record(value, node.iter, "iteration", constraints)
                # One abstract iteration, then discard modified bindings.
                original = dict(self.env)
                self.assign(node.target, value)
                self.block(node.body, constraints, handlers)
                if self.stopped:
                    return False
                self.env = {k: v for k, v in original.items() if self.env.get(k) == v}
                self.block(node.orelse, constraints, handlers)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # Function-local imports are deliberately outside this subset.
                self.unknown(self.path, node, "function-local import binding unresolved")
            elif isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
                if not isinstance(node, ast.Pass):
                    self.incomplete = True
                    self.env = {k: Value() for k in self.env}
                    self.unknown(self.path, node, "global/nonlocal mutation stops local reasoning")
                    self.stopped = True
                    return False
            elif isinstance(node, (*FUNCTIONS, ast.ClassDef)):
                self.env[node.name] = Value()
                self.unknown(self.path, node, "nested definition is a separate unsupported scope")
            else:
                self.incomplete = True
                self.unknown(self.path, node, "unsupported control flow stops reasoning for this function")
                self.stopped = True
                return False
        return False

    def run(self):
        if self.r.lexical_facts(self.node)[1]:
            self.incomplete = True
            self.unknown(self.path, self.node, "dynamic namespace stops function analysis")
        else:
            if not self.block(self.node.body) and not self.stopped:
                self.returns.none = True
        exceptions = self.exception_origins
        # Only a straight literal-container return establishes unchanged fields.
        # Dynamic construction, multiple returns and preceding behavior do not.
        body = [
            n
            for n in self.node.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))
        ]
        shape = None
        if len(body) == 1 and isinstance(body[0], ast.Return):
            result = body[0].value
            pairs = (
                list(enumerate(result.elts))
                if isinstance(result, (ast.List, ast.Tuple))
                else (
                    [
                        (k.value, v)
                        for k, v in zip(result.keys, result.values)
                        if isinstance(k, ast.Constant) and isinstance(k.value, (str, int))
                    ]
                    if isinstance(result, ast.Dict)
                    else []
                )
            )
            if (
                pairs
                and len(pairs) <= 64
                and all(isinstance(v, ast.Constant) and type(k) in (str, int) for k, v in pairs)
            ):
                shape = {_selector(k): hashlib.sha256(ast.dump(v).encode()).hexdigest() for k, v in pairs}
        return {
            "return_sources": self.returns.origins,
            "return_parameters": sorted(self.returns.parameters),
            "may_return_none": self.returns.none,
            "exceptions": exceptions,
            "incomplete": self.incomplete,
            "literal_return_fields": shape,
        }


def analyze_repository(before, after, changed_ids):
    """Compare request-local summaries and return located source evidence."""
    phases = {}
    syntax_facts = {}
    for phase, sources in [("before", before), ("after", after)]:
        resolver = Resolver(sources, syntax_facts)
        summaries = {}
        # Finite composition: a pass consumes only the preceding snapshot.
        for _ in range(MAX_HOPS):
            next_summaries = {}
            for ident in resolver.functions:
                next_summaries[ident] = Scan(resolver, ident, summaries, phase, False).run()
                if resolver.exhausted:
                    break
            summaries = next_summaries
            if resolver.exhausted:
                break
        relations = []
        assertions = []
        for ident in resolver.functions:
            scan = Scan(resolver, ident, summaries, phase, True)
            scan.run()
            relations.extend(
                r for r in scan.records if r["source_id"] in changed_ids and r["consumer_id"] != r["source_id"]
            )
            assertions.extend(r for r in scan.assertions if r["entity_id"] in changed_ids)
            if len(relations) > MAX_ROWS or len(assertions) > MAX_ROWS:
                resolver.unknown(scan.path, scan.node, "relationship/assertion output limit reached")
                break
            if resolver.exhausted:
                break
        # Extra test bodies never displace the established function-summary
        # universe. Their known parametrization metadata is not composed as a
        # callable wrapper, and no runtime/input reachability is claimed.
        for ident in resolver.test_observers:
            scan = Scan(resolver, ident, summaries, phase, True)
            scan.run()
            assertions.extend(r for r in scan.assertions if r["entity_id"] in changed_ids)
            if len(assertions) > MAX_ROWS:
                resolver.unknown(scan.path, scan.node, "assertion output limit reached; further observers unresolved")
                break
            if resolver.exhausted:
                break
        phases[phase] = {
            "summaries": summaries,
            "relationships": relations[:MAX_ROWS],
            "assertions": assertions[:MAX_ROWS],
            "uncertainties": resolver.unknowns,
            "steps": resolver.steps,
        }
    deltas = []
    for ident in sorted(changed_ids):
        old = phases["before"]["summaries"].get(ident)
        new = phases["after"]["summaries"].get(ident)
        if old != new:
            deltas.append({"entity_id": ident, "before": old, "after": new})
    assertions = []
    unrelated_assertions = []
    for row in phases["before"]["assertions"]:
        old = phases["before"]["summaries"].get(row["entity_id"], {})
        new = phases["after"]["summaries"].get(row["entity_id"], {})
        previous, current = old.get("literal_return_fields"), new.get("literal_return_fields")
        selector = next((s.get("selector") for s in row["path"] if s.get("role") == "index"), None)
        if (
            selector is not None
            and previous
            and current
            and selector in previous
            and previous.get(selector) == current.get(selector)
        ):
            unrelated_assertions.append(row)
        else:
            assertions.append(row)
    return {
        "version": VERSION,
        "summary_changes": deltas,
        "relationships": [*phases["before"]["relationships"], *phases["after"]["relationships"]],
        "assertions": assertions,
        "unrelated_assertions": unrelated_assertions,
        "uncertainties": [{**r, "phase": p} for p, data in phases.items() for r in data["uncertainties"]],
        "limits": {
            "functions": MAX_FUNCTIONS,
            "steps_per_phase": MAX_STEPS,
            "summary_hops": MAX_HOPS,
            "relationships_per_phase": MAX_ROWS,
            "import_hops": MAX_IMPORT_HOPS,
            "test_observers": MAX_TEST_OBSERVERS,
        },
        "steps": {p: data["steps"] for p, data in phases.items()},
    }


def merge_test_evidence(checks, flow, tests, obligations):
    """Attach only located baseline assertions on the related returned value."""
    import shlex

    by_id = {row["test_id"]: row for row in checks}
    excluded = {(r["test_id"], r["entity_id"]) for r in flow.get("unrelated_assertions", [])} - {
        (r["test_id"], r["entity_id"]) for r in flow["assertions"]
    }
    for test_id, entity_id in excluded:
        row = by_id.get(test_id)
        if row:
            for association in row["associations"]:
                if association["entity_id"] == entity_id:
                    for evidence in association.get("obligation_evidence", []):
                        evidence.update(
                            level="broad",
                            assertion_evidence=None,
                            reason="This assertion selects a literal return field unchanged by the patch.",
                        )
            row["behavioral_rank"] = min(
                (
                    {"input-and-assertion": 0, "assertion": 1, "input": 2}.get(e.get("level"), 3)
                    for a in row["associations"]
                    for e in a.get("obligation_evidence", [])
                ),
                default=4,
            )
    sources = {row["path"]: row for row in tests}
    retained = {}
    for assertion in flow["assertions"]:
        test_id = assertion["test_id"]
        path = test_id.split("::", 1)[0]
        if path not in sources:
            continue
        key = (test_id, assertion["entity_id"])
        if key in retained:
            retained[key]["omitted_assertion_examples"] = retained[key].get("omitted_assertion_examples", 0) + 1
            continue
        row = by_id.get(test_id)
        if row is None:
            row = {
                "test_id": test_id,
                "path": path,
                "source_sha256": sources[path]["sha256"],
                "relationship": "resolved-value-flow",
                "coverage_proven": False,
                "test_source_phase": "before",
                "entities": [],
                "associations": [],
                "behavioral_rank": 3,
                "evidence": {
                    "reference_line": assertion["assertion"]["location"]["line"],
                    "reference": assertion["assertion"]["expression"],
                },
                "verification": "python -m pytest " + shlex.quote(test_id),
                "unknown": "Static result observation does not establish execution or detection of this regression.",
            }
            by_id[test_id] = row
        entity = assertion["entity_id"]
        evidence = [
            {
                "obligation_id": o["id"],
                "level": "assertion",
                "input_evidence": None,
                "assertion_evidence": assertion["assertion"],
                "reason": "A resolved return-value path reaches this baseline assertion; changed input reachability and execution remain unverified.",
            }
            for o in obligations
            if o.get("entity_id") == entity
            and (
                (o.get("observable") or {}).get("kind")
                in {"return", "return-or-state", "predicate-result", "key-or-index-result", "argument-binding"}
                or (o.get("observable") or {}).get("kind") in {"exception", "exception-or-handler-result"}
                and any(s.get("role") == "handler" for s in assertion["path"])
            )
        ][:64]
        association = {
            "entity_id": entity,
            "relationship": "resolved-value-flow",
            "resolution": "resolved",
            "path": assertion["path"],
            "constraints": assertion["constraints"],
            "obligation_evidence": evidence,
            "limitation": "No runtime observation or demonstrated detection of a specific regression.",
            "evidence_category": "related-result-assertion",
            "result_assertion": {
                "assertion": assertion["assertion"],
                "constraints": assertion["constraints"],
                "source_phase": "before",
                "changed_behavior_established": False,
                "reaching_input_proven": False,
                "observed_execution": False,
                "regression_detection_demonstrated": False,
            },
        }
        retained[key] = association
        if association not in row["associations"]:
            row["associations"].append(association)
        row["entities"] = sorted(set(row["entities"]) | {entity})
        if evidence:
            row["behavioral_rank"] = min(row.get("behavioral_rank", 4), 1)
    for row in by_id.values():
        broad = all(a["relationship"].startswith("broad-") for a in row["associations"])
        observed_result = any(a.get("result_assertion") for a in row["associations"])
        row["result_assertion_rank"] = 1 if observed_result else 4
        row["verification_category"] = (
            "broad-source-reference"
            if broad
            else "related-result-assertion"
            if observed_result or row.get("behavioral_rank", 4) <= 1
            else "resolved-source-path"
        )
        if broad:
            row["verification_gap"] = "No member-specific call or assertion on the changed behavior was established."
        elif row.get("behavioral_rank", 4) > 1:
            row["verification_gap"] = (
                "A located result assertion exists, but no supported changed-operation obligation and reaching input were established."
                if observed_result
                else "No assertion observing the related result or identified exception was established for this source path."
            )
    return sorted(by_id.values(), key=lambda r: r["test_id"])
