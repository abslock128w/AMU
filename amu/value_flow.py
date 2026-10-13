"""Bounded source-only Python return/exception flow, independent of provider edges.

No modules are imported. Summaries are request-local and recomputed for both
source snapshots; they are never persisted across dependency changes.
"""

from __future__ import annotations

import ast
import builtins
import copy
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .change_analysis import _bindings as _bindings
from .change_analysis import _dynamic_namespace as _dynamic_namespace
from .change_analysis import _location
from .outbound import redact
from .semantic import Instance, SemanticIndex, Symbol

VERSION = "python-value-flow-v3"
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


def _marker_literal(node, depth=0, pytest_root="pytest"):
    """Bounded literal marker metadata, including literal string concatenation.

    This never evaluates a call, name, attribute, comprehension or target code.
    """
    if depth > 20:
        raise ValueError("marker literal depth")
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes, int, float, bool, type(None)):
        if isinstance(node.value, str) and len(node.value) > 8192:
            raise ValueError("marker string limit")
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and len(node.elts) <= 64:
        return [_marker_literal(v, depth + 1, pytest_root) for v in node.elts]
    if isinstance(node, ast.Dict) and len(node.keys) <= 64:
        return {
            _marker_literal(k, depth + 1, pytest_root): _marker_literal(v, depth + 1, pytest_root)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.BinOp):
        left, right = (
            _marker_literal(node.left, depth + 1, pytest_root),
            _marker_literal(node.right, depth + 1, pytest_root),
        )
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
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "param"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == pytest_root
        and len(node.args) <= 32
        and all(k.arg == "id" for k in node.keywords)
    ):
        for k in node.keywords:
            _marker_literal(k.value, depth + 1, pytest_root)
        values = [_marker_literal(a, depth + 1, pytest_root) for a in node.args]
        return values[0] if len(values) == 1 else values
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
            names, values = (_marker_literal(arg, pytest_root=func.value.value.id) for arg in decorator.args)
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


class Resolver(SemanticIndex):
    """Compatibility entry point backed by shared identities."""

    def __init__(self, sources, syntax_facts=None, provider_candidates=()):
        super().__init__(
            sources,
            syntax_facts,
            parameterized=_parameterized_test,
            function_limit=MAX_FUNCTIONS,
            observer_limit=MAX_TEST_OBSERVERS,
            provider_candidates=provider_candidates,
        )


@dataclass
class Value:
    # Every origin has a located call/return path. Parameter origins are local
    # placeholders and only composed when the exact argument is established.
    origins: dict = field(default_factory=dict)
    none: bool = False
    parameters: set = field(default_factory=set)
    fields: dict = field(default_factory=dict)
    shape: str | None = None
    literal: str | None = None
    field_parameters: set = field(default_factory=set)
    complete_shape: bool = False
    alternative_paths: dict = field(default_factory=dict)


def union(values):
    result = Value()
    for value in values:
        if isinstance(value, Value):
            for origin, path in value.origins.items():
                candidates = [*result.alternative_paths.get(origin, []), *value.alternative_paths.get(origin, [])]
                previous = result.origins.get(origin)
                if previous is not None and previous != path:
                    candidates.extend([previous, path])
                unique = []
                for candidate in candidates:
                    if candidate not in unique:
                        unique.append(candidate)
                if unique:
                    result.alternative_paths[origin] = unique[:4]
                result.origins[origin] = path
            result.none |= value.none
            result.parameters.update(value.parameters)
            result.field_parameters.update(value.field_parameters)
    return result


def packed(value):
    if not isinstance(value, Value):
        return None
    return {
        "origins": value.origins,
        "none": value.none,
        "parameters": sorted(value.parameters),
        "fields": {k: packed(v) for k, v in value.fields.items()},
        "shape": value.shape,
        "literal": value.literal,
        "field_parameters": sorted(value.field_parameters),
        "complete_shape": value.complete_shape,
        "alternative_paths": value.alternative_paths,
    }


def unpacked(row):
    if not row:
        return Value()
    return Value(
        row["origins"],
        row["none"],
        set(row["parameters"]),
        {k: unpacked(v) for k, v in row.get("fields", {}).items()},
        row.get("shape"),
        row.get("literal"),
        set(tuple(x) for x in row.get("field_parameters", [])),
        row.get("complete_shape", False),
        row.get("alternative_paths", {}),
    )


def literal_value(value):
    if type(value) in {str, bytes, int, float, bool, type(None)}:
        return Value(none=value is None, shape=type(value).__name__, literal=repr(value))
    if type(value) in {dict, list, tuple} and len(value) <= 64:
        pairs = value.items() if type(value) is dict else enumerate(value)
        fields = {_selector(k): literal_value(v) for k, v in pairs}
        return Value(fields=fields, shape=type(value).__name__, complete_shape=True)
    return Value()


class Scan:
    def __init__(self, resolver, function_id, summaries, phase, emit):
        self.r, self.id, self.summaries, self.phase, self.emit = resolver, function_id, summaries, phase, emit
        self.path, self.name, self.node = resolver.functions.get(function_id) or resolver.test_observers[function_id]
        self.local = set(resolver.lexical_facts(self.node)[0])
        self.env = {
            a.arg: Value(parameters={i}) for i, a in enumerate([*self.node.args.posonlyargs, *self.node.args.args])
        }
        owner = resolver.owners.get(function_id)
        if owner and self.node.args.args and not resolver.is_static_method(self.node, resolver.tables[self.path]):
            self.env[self.node.args.args[0].arg] = Instance(owner, "parameter:self", exact=False)
        self.returns = Value()
        self.return_instances = []
        self.return_count = 0
        self.return_shapes = set()
        self.binding_invalid = False
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
                        "alternative_paths": [
                            [*path, self.evidence(node, "assertion")]
                            for path in value.alternative_paths.get(origin, [])
                        ],
                        "constraints": constraints,
                        "observed_execution": False,
                        "coverage_proven": False,
                        "phase": self.phase,
                    }
                )

    def selected(self, value, key, node):
        if not isinstance(value, Value):
            return Value()
        selector = _selector(key)
        step = self.evidence(node, "index")
        step["selector"] = selector
        if value.shape in {"list", "tuple", "dict"} and not value.complete_shape:
            self.unknown(self.path, node, "branch-dependent container slot identity is unresolved")
            return Value()
        if value.fields and selector in value.fields:
            result = copy.deepcopy(value.fields[selector])
            result.origins = {o: [*path, step] for o, path in result.origins.items()}
            result.alternative_paths = {
                o: [[*path, step] for path in paths] for o, paths in result.alternative_paths.items()
            }
            return result
        return Value({o: [*path, step] for o, path in value.origins.items()}, parameters=set(value.parameters))

    def substitute(self, value, args, step):
        if not isinstance(value, Value):
            return Value()
        result = copy.deepcopy(value)
        for i in result.parameters:
            if i < len(args) and isinstance(args[i], Value):
                result.origins.update({o: [*path, step] for o, path in args[i].origins.items()})
                if args[i].literal is not None:
                    result.literal = args[i].literal
        for i, name in result.field_parameters:
            if i < len(args) and isinstance(args[i], Instance) and not args[i].escaped:
                field = args[i].fields.get(name)
                if isinstance(field, Value):
                    result.origins.update({o: [*path, step] for o, path in field.origins.items()})
                    result.literal = field.literal
        result.fields = {k: self.substitute(v, args, step) for k, v in result.fields.items()}
        return result

    def arguments(self, symbol, node, args, keyword_args, receiver):
        target = self.r.functions[symbol.id][2]
        params = [*target.args.posonlyargs, *target.args.args, *target.args.kwonlyargs]
        self.binding_invalid = False
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            self.unknown(self.path, node, "starred argument binding unresolved")
            return []
        values = ([receiver] if symbol.kind in {"method", "property"} and isinstance(receiver, Instance) else []) + args
        if len(values) > len(params) and target.args.vararg is None:
            self.binding_invalid = True
            self.unknown(self.path, node, "argument arity does not match runtime implementation")
            return []
        bound = {p.arg: v for p, v in zip(params, values)}
        for keyword, value in zip(node.keywords, keyword_args):
            if keyword.arg in bound or keyword.arg not in {p.arg for p in params}:
                self.binding_invalid = True
                self.unknown(self.path, node, "duplicate or unsupported keyword argument binding")
                return []
            bound[keyword.arg] = value
        required = [a.arg for a in [*target.args.posonlyargs, *target.args.args]][
            : len(target.args.posonlyargs) + len(target.args.args) - len(target.args.defaults)
        ]
        required += [a.arg for a, d in zip(target.args.kwonlyargs, target.args.kw_defaults) if d is None]
        if any(name not in bound for name in required):
            self.binding_invalid = True
            self.unknown(self.path, node, "required argument binding absent; invocation unsupported")
            return []
        defaults = (
            dict(zip([a.arg for a in params][-len(target.args.defaults) :], target.args.defaults))
            if target.args.defaults
            else {}
        )
        return [
            bound.get(
                p.arg,
                Value(literal=repr(defaults[p.arg].value))
                if p.arg in defaults and isinstance(defaults[p.arg], ast.Constant)
                else Value(),
            )
            for p in params
        ]

    def escape(self, value):
        if isinstance(value, Instance):
            value.escaped = True
            value.fields.clear()

    def expr(self, node, constraints, handlers):
        if node is None or not self.step(node):
            return Value()
        if isinstance(node, ast.Name):
            if node.id in self.env:
                return self.env[node.id]
            symbol = self.r.expr(self.path, node, self.env, self.local)
            if symbol:
                return symbol
            fact = self.r.tables.get(self.path, {}).get(node.id)
            if (
                node.id not in self.local
                and fact
                and fact[0] == "alias"
                and isinstance(fact[1], (ast.Constant, ast.Dict, ast.Tuple, ast.List))
            ):
                if not any(
                    isinstance(n, ast.Subscript)
                    and isinstance(n.ctx, (ast.Store, ast.Del))
                    and isinstance(n.value, ast.Name)
                    and n.value.id == node.id
                    or isinstance(n, ast.Call)
                    and (
                        any(isinstance(a, ast.Name) and a.id == node.id for a in n.args)
                        or isinstance(n.func, ast.Attribute)
                        and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == node.id
                    )
                    for n in ast.walk(self.r.sources[self.path]["tree"])
                ):
                    try:
                        return literal_value(ast.literal_eval(fact[1]))
                    except (ValueError, TypeError, MemoryError, RecursionError):
                        pass
            return Value()
        if isinstance(node, ast.Constant):
            return Value(none=node.value is None, shape=type(node.value).__name__, literal=repr(node.value))
        if isinstance(node, ast.Call):
            symbol = self.r.expr(self.path, node.func, self.env, self.local)
            receiver = (
                self.expr(node.func.value, constraints, handlers) if isinstance(node.func, ast.Attribute) else None
            )
            if isinstance(receiver, Instance) and not receiver.escaped:
                symbol = self.r.member(receiver.type, node.func.attr)
            evaluated_receiver = receiver
            args = [self.expr(arg, constraints, handlers) for arg in node.args]
            keyword_args = [self.expr(k.value, constraints, handlers) for k in node.keywords]
            if self.stopped:
                return Value()
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "str"
                and node.func.id not in self.local
                and node.func.id not in self.r.tables[self.path]
                and len(args) == 1
                and not node.keywords
                and isinstance(args[0], Value)
            ):
                result = copy.deepcopy(args[0])
                result.shape = "str"
                step = self.evidence(node, "scalar_operation")
                step["model"] = "builtin-str-contract-v1"
                result.origins = {o: [*path, step] for o, path in result.origins.items()}
                return result
            if isinstance(evaluated_receiver, Value) and isinstance(node.func, ast.Attribute):
                method = node.func.attr
                if (
                    evaluated_receiver.shape == "list"
                    and method == "append"
                    and len(args) == 1
                    and isinstance(args[0], Value)
                    and not node.keywords
                ):
                    if len(evaluated_receiver.fields) < 64:
                        if evaluated_receiver.complete_shape:
                            evaluated_receiver.fields[_selector(len(evaluated_receiver.fields))] = copy.deepcopy(
                                args[0]
                            )
                        evaluated_receiver.origins.update(args[0].origins)
                        evaluated_receiver.parameters.update(args[0].parameters)
                        evaluated_receiver.field_parameters.update(args[0].field_parameters)
                        self.record(args[0], node, "container_append", constraints)
                    else:
                        self.unknown(self.path, node, "container field budget reached")
                    return Value(none=True)
                if (
                    evaluated_receiver.shape == "str"
                    and method in {"join", "format", "strip", "lstrip", "rstrip", "lower", "upper", "replace"}
                    and all(isinstance(v, Value) for v in [*args, *keyword_args])
                ):
                    result = union([evaluated_receiver, *args, *keyword_args])
                    result.shape = "str"
                    step = self.evidence(node, "scalar_operation")
                    step["model"] = "python-str-readonly-v1"
                    result.origins = {o: [*path, step] for o, path in result.origins.items()}
                    return result
            if any(isinstance(value, Symbol) for value in [*args, *keyword_args]):
                self.stopped = True
                self.unknown(
                    self.path, node, "callable/module binding escaped to a call; subsequent alias identity unresolved"
                )
                return Value()
            if symbol and symbol.kind == "class" and symbol.id in self.r.classes:
                owner = self.r.classes[symbol.id]
                if (
                    owner.decorator_list
                    or owner.keywords
                    or any(
                        isinstance(n, FUNCTIONS) and n.name in {"__new__", "__getattribute__", "__getattr__"}
                        for n in owner.body
                    )
                ):
                    self.unknown(
                        self.path, node, "constructed receiver has unsupported allocation/descriptor semantics"
                    )
                    return Value()
                obj = Instance(symbol, f"{self.id}:{node.lineno}:{node.col_offset}")
                init = self.r.member(symbol, "__init__")
                if init and init.id in self.r.functions:
                    bound = self.arguments(init, node, args, keyword_args, obj)
                    if self.binding_invalid:
                        return Value()
                    step = self.evidence(node, "constructor")
                    if self.summaries.get(init.id, {}).get("receiver_invalidated"):
                        obj.escaped = True
                    for key, row in self.summaries.get(init.id, {}).get("state_effects", {}).items():
                        obj.fields[key] = self.substitute(unpacked(row), bound, step)
                return obj
            if (
                symbol
                and symbol.kind in {"function", "method", "static_method", "property"}
                and symbol.id in self.r.functions
            ):
                self.calls.add(symbol.id)
                call_step = self.evidence(node, "resolved_call")
                call_step["callee_id"] = symbol.id
                call_step["constraints"] = list(constraints)
                if isinstance(receiver, Instance):
                    call_step["receiver"] = {
                        "type_id": receiver.type.id,
                        "allocation": receiver.allocation,
                        "status": "constructed" if receiver.exact else "declaring-type-assumption",
                    }
                matches = self.r.provider_matches(symbol.id, self.id)
                if matches:
                    call_step["provider_candidates"] = [
                        {
                            "source_id": m["source_id"],
                            "consumer_id": m["consumer_id"],
                            "status": "source-validated-candidate",
                        }
                        for m in matches
                    ]
                bound = self.arguments(symbol, node, args, keyword_args, receiver)
                if self.binding_invalid:
                    self.record(Value({symbol.id: [call_step]}), node, "call", constraints)
                    return Value()
                call_step["literal_arguments"] = {
                    str(i): v.literal for i, v in enumerate(bound) if isinstance(v, Value) and v.literal is not None
                }
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
                if bound:
                    for i in summary.get("return_parameters", []):
                        if i < len(bound) and isinstance(bound[i], Value):
                            for origin, steps in bound[i].origins.items():
                                value.origins[origin] = [*steps, call_step]
                            value.parameters.update(bound[i].parameters)
                elif summary.get("return_parameters"):
                    self.incomplete = True
                    self.unknown(self.path, node, "keyword/starred argument forwarding is outside the supported subset")
                for key, row in summary.get("return_fields", {}).items():
                    field = self.substitute(unpacked(row), bound, call_step)
                    field.origins[symbol.id] = [call_step]
                    field.origins.update(value.origins)
                    value.fields[key] = field
                value.shape = summary.get("return_shape")
                value.complete_shape = summary.get("return_shape_complete", False)
                for i, name in summary.get("return_field_parameters", []):
                    if i < len(bound) and isinstance(bound[i], Instance) and name in bound[i].fields:
                        saved = bound[i].fields[name]
                        if isinstance(saved, Value):
                            value.origins.update(saved.origins)
                            value.shape = saved.shape
                            value.literal = saved.literal
                            value.fields = copy.deepcopy(saved.fields)
                            value.complete_shape = saved.complete_shape
                if isinstance(receiver, Instance) and summary.get("receiver_invalidated"):
                    self.escape(receiver)
                if isinstance(receiver, Instance) and not receiver.escaped:
                    for key, row in summary.get("state_effects", {}).items():
                        receiver.fields[key] = self.substitute(unpacked(row), bound, call_step)
                for origin, alternatives in summary.get("return_alternative_paths", {}).items():
                    value.alternative_paths[origin] = [
                        [call_step, *path]
                        for path in alternatives
                        if sum(s.get("role") == "resolved_call" for s in path) < MAX_HOPS
                    ][:4]
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
                returned = summary.get("return_instances", [])
                if len(returned) == 1:
                    row = returned[0]
                    cls_path, cls_name = row["type_id"].split("#", 1)
                    return Instance(
                        Symbol(cls_path, cls_name, "class"),
                        f"{self.id}:{node.lineno}",
                        {k: self.substitute(unpacked(v), bound, call_step) for k, v in row["fields"].items()},
                    )
                return value
            for arg in [*args, *keyword_args]:
                self.escape(arg)
            # A known builtin may consume or transform data, but arbitrary
            # call semantics do not establish an observing result.
            flows = union([*args, *keyword_args, evaluated_receiver])
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            receiver_value = self.env.get(receiver.id) if isinstance(receiver, ast.Name) else None
            if isinstance(receiver_value, Value):
                flows = union([flows, receiver_value])
            if flows.origins:
                self.record(flows, node, "argument-to-unresolved-call", constraints)
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
                if isinstance(node.slice, ast.Constant) and type(node.slice.value) in (str, int):
                    return self.selected(value, node.slice.value, node)
                step = self.evidence(node, "index")
                return Value(
                    {origin: [*steps, step] for origin, steps in value.origins.items()},
                    parameters=set(value.parameters),
                )
            return Value()
        if isinstance(node, ast.Attribute):
            receiver = self.r.expr(self.path, node.value, self.env, self.local)
            symbol = self.r.expr(self.path, node, self.env, self.local)
            if symbol and symbol.kind == "property" and isinstance(receiver, Instance):
                call = ast.copy_location(ast.Call(func=node, args=[], keywords=[]), node)
                return self.expr(call, constraints, handlers)
            if symbol:
                return symbol
            if isinstance(receiver, Instance) and not receiver.escaped:
                saved = receiver.fields.get(node.attr)
                if isinstance(saved, Value):
                    step = self.evidence(node, "state_read")
                    step["field"] = node.attr
                    return Value(
                        {o: [*path, step] for o, path in saved.origins.items()},
                        parameters=set(saved.parameters),
                        fields=copy.deepcopy(saved.fields),
                        shape=saved.shape,
                        literal=saved.literal,
                    )
                if not receiver.exact:
                    return Value(field_parameters={(0, node.attr)})
            value = self.expr(node.value, constraints, handlers)
            self.record(value, node, "attribute", constraints)
            if isinstance(value, Value) and value.origins:
                self.unknown(self.path, node, "attribute access exists; descriptor result identity unresolved")
            return Value()
        if isinstance(node, ast.JoinedStr):
            values = [
                self.expr(n.value, constraints, handlers) for n in node.values if isinstance(n, ast.FormattedValue)
            ]
            if any(isinstance(v, (Symbol, Instance)) for v in values):
                self.unknown(self.path, node, "opaque __format__ dispatch is unresolved")
                return Value()
            value = union(values)
            value.shape = "str"
            step = self.evidence(node, "scalar_operation")
            step["model"] = "python-fstring-v1"
            value.origins = {o: [*path, step] for o, path in value.origins.items()}
            return value
        if isinstance(node, (ast.Tuple, ast.List, ast.Dict)):
            if isinstance(node, ast.Dict):
                if any(not isinstance(k, ast.Constant) or type(k.value) not in (str, int) for k in node.keys):
                    self.unknown(self.path, node, "dynamic dictionary shape is unresolved")
                    return Value()
                fields = {
                    _selector(k.value): self.expr(v, constraints, handlers) for k, v in zip(node.keys, node.values)
                }
                shape = "dict"
            else:
                fields = {_selector(i): self.expr(v, constraints, handlers) for i, v in enumerate(node.elts)}
                shape = "tuple" if isinstance(node, ast.Tuple) else "list"
            if len(fields) > 64 or any(not isinstance(v, Value) for v in fields.values()):
                self.unknown(self.path, node, "container identity/field limit unresolved")
                return Value()
            result = union(fields.values())
            result.fields = fields
            result.shape = shape
            result.complete_shape = True
            result.none = False
            return result
        if isinstance(node, (ast.Compare, ast.BinOp, ast.UnaryOp)):
            children = [
                self.expr(child, constraints, handlers)
                for child in ast.iter_child_nodes(node)
                if isinstance(child, ast.expr)
            ]
            value = union(children)
            value.none = False
            if isinstance(node, ast.BinOp) and len(children) == 2 and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                left, right = children
                if (
                    isinstance(left, Value)
                    and isinstance(right, Value)
                    and left.shape == right.shape
                    and left.shape in {"int", "float"}
                ):
                    value.shape = left.shape
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
            if isinstance(value, Value) and value.shape in {"tuple", "list"} and len(value.fields) == len(target.elts):
                for i, t in enumerate(target.elts):
                    self.assign(t, self.selected(value, i, target))
            else:
                for t in target.elts:
                    self.assign(t, Value())
                self.unknown(self.path, target, "destructured result shape is unresolved")
        elif isinstance(target, ast.Attribute):
            receiver = self.r.expr(self.path, target.value, self.env, self.local)
            if target.attr in {"__class__", "__dict__"}:
                self.escape(receiver)
                self.unknown(self.path, target, "receiver class/namespace mutation invalidates identity")
                return
            setter = (
                self.r.members.get((receiver.type.id, target.attr + "@setter"))
                if isinstance(receiver, Instance) and not receiver.escaped
                else None
            )
            if setter is not None and setter.id in self.summaries:
                summary = self.summaries[setter.id]
                step = self.evidence(target, "property_set")
                step["entity_id"] = setter.id
                self.calls.add(setter.id)
                self.record(Value({setter.id: [step]}), target, "call", [])
                if summary.get("receiver_invalidated"):
                    self.escape(receiver)
                else:
                    for key, row in summary.get("state_effects", {}).items():
                        saved = self.substitute(unpacked(row), [receiver, value], step)
                        receiver.fields[key] = saved
                return
            if (
                isinstance(receiver, Instance)
                and not receiver.escaped
                and not self.r.member(receiver.type, target.attr)
                and self.r.ordinary_field(receiver.type, target.attr)
            ):
                if isinstance(value, Value):
                    saved = copy.deepcopy(value)
                    step = self.evidence(target, "state_write")
                    step["field"] = target.attr
                    saved.origins = {o: [*path, step] for o, path in saved.origins.items()}
                    if receiver.allocation == "parameter:self":
                        saved.origins[self.id] = [step]
                    receiver.fields[target.attr] = saved
                    self.record(saved, target, "state_write", [])
                else:
                    receiver.fields.pop(target.attr, None)
            else:
                self.escape(receiver)
                if isinstance(receiver, Symbol):
                    root = target.value
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name):
                        self.local.add(root.id)
                        self.env[root.id] = Value()
                    for key, saved in list(self.env.items()):
                        if saved == receiver:
                            self.env[key] = Value()
                self.unknown(self.path, target, "unsupported descriptor/state write invalidates receiver")
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
                self.return_count += 1
                value = self.expr(node.value, constraints, handlers) if node.value is not None else Value(none=True)
                # A literal handler result is constrained by the established
                # exception source, unlike an unrelated later return/raise.
                if self.handler_value.origins:
                    value = union([value, self.handler_value])
                self.return_shapes.add(
                    value.shape if isinstance(value, Value) else "instance" if isinstance(value, Instance) else None
                )
                if isinstance(value, Instance) and not value.escaped:
                    self.return_instances.append(
                        {"type_id": value.type.id, "fields": {k: packed(v) for k, v in value.fields.items()}}
                    )
                if isinstance(value, Value):
                    previous = self.returns
                    self.returns = union([previous, value])
                    if not previous.origins and not previous.fields and not previous.parameters:
                        self.returns.fields = copy.deepcopy(value.fields)
                        self.returns.complete_shape = value.complete_shape
                        self.returns.shape = value.shape
                    elif previous.fields == value.fields:
                        self.returns.fields = copy.deepcopy(value.fields)
                        self.returns.complete_shape = value.complete_shape
                        self.returns.shape = value.shape
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
                if isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
                    if self.block(node.body if node.test.value else node.orelse, constraints, handlers):
                        return True
                    continue
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
                original = copy.deepcopy(self.env)
                left_done = self.block(node.body, [*constraints, *([guard[0]] if guard else [])], handlers)
                if self.stopped:
                    return False
                left = copy.deepcopy(self.env)
                self.env = copy.deepcopy(original)
                right_done = self.block(node.orelse, [*constraints, *([guard[1]] if guard else [])], handlers)
                if self.stopped:
                    return False
                right = copy.deepcopy(self.env)
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
                    for key in left.keys() & right.keys():
                        a, b = left[key], right[key]
                        if (
                            isinstance(a, Value)
                            and isinstance(b, Value)
                            and a.shape == b.shape
                            and a.shape in {"list", "tuple", "dict"}
                        ):
                            joined = union([a, b])
                            joined.shape = a.shape
                            # Whole collection dependence survives; differing slot
                            # identities remain unresolved rather than one guessed value.
                            joined.fields = {
                                slot: v for slot, v in a.fields.items() if slot in b.fields and v == b.fields[slot]
                            }
                            self.env[key] = joined
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
                original = copy.deepcopy(self.env)
                body_done = self.block(node.body, constraints, [*active, *handlers])
                if self.stopped:
                    return False
                else_done = self.block(node.orelse, constraints, handlers) if not body_done else False
                if self.stopped:
                    return False
                # The normal continuation is distinct from terminating handlers.
                normal_env = copy.deepcopy(self.env)
                self.env = copy.deepcopy(original)
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
                self.env = (
                    normal_env
                    if handler_done and all(handler_done) and not (body_done or else_done)
                    else {k: v if isinstance(v, Symbol) else Value() for k, v in original.items()}
                )
                if (body_done or else_done) and all(handler_done):
                    return True
            elif isinstance(node, ast.For):
                value = self.expr(node.iter, constraints, handlers)
                self.record(value, node.iter, "iteration", constraints)
                # One abstract iteration, then discard modified bindings.
                original = copy.deepcopy(self.env)
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
            "receiver_invalidated": any(
                obj.escaped
                for obj in self.env.values()
                if isinstance(obj, Instance) and obj.allocation == "parameter:self"
            ),
            "literal_state_fields": {
                k: hashlib.sha256(v.literal.encode()).hexdigest()
                for obj in self.env.values()
                if isinstance(obj, Instance) and obj.allocation == "parameter:self" and not obj.escaped
                for k, v in obj.fields.items()
                if isinstance(v, Value) and v.literal is not None
            },
            "return_fields": {k: packed(v) for k, v in self.returns.fields.items()},
            "return_shape_complete": self.returns.complete_shape,
            "return_shape": next(iter(self.return_shapes))
            if len(self.return_shapes) == 1 and not self.stopped
            else None,
            "return_instances": self.return_instances[:2]
            if len(self.return_instances) == self.return_count and not self.returns.none and not self.stopped
            else [],
            "possible_return_instance_types": sorted({r["type_id"] for r in self.return_instances}),
            "state_effects": {
                k: packed(v)
                for obj in self.env.values()
                if isinstance(obj, Instance) and obj.allocation == "parameter:self" and not obj.escaped
                for k, v in obj.fields.items()
            },
            "return_sources": self.returns.origins,
            "return_alternative_paths": self.returns.alternative_paths,
            "return_parameters": sorted(self.returns.parameters),
            "return_field_parameters": sorted(self.returns.field_parameters),
            "may_return_none": self.returns.none,
            "exceptions": exceptions,
            "incomplete": self.incomplete,
            "literal_return_fields": shape,
        }


def analyze_repository(before, after, changed_ids, *, provider_candidates=()):
    """Compare request-local summaries and return located source evidence."""
    phases = {}
    syntax_facts = {}
    indexes = {}
    for phase, sources in [("before", before), ("after", after)]:
        resolver = Resolver(sources, syntax_facts, provider_candidates)
        indexes[phase] = resolver
        summaries = {}
        # Dependency-driven fixed point. Each update consumes immutable preceding
        # summaries; cycles/hop/resource limits remain explicit, not success.
        dependents = defaultdict(set)
        pending = deque(resolver.functions)
        queued = set(pending)
        updates = defaultdict(int)
        while pending and not resolver.exhausted:
            ident = pending.popleft()
            queued.discard(ident)
            scan = Scan(resolver, ident, summaries, phase, False)
            summary = scan.run()
            for callee in scan.calls:
                dependents[callee].add(ident)
            if summaries.get(ident) != summary:
                summaries[ident] = summary
                updates[ident] += 1
                if updates[ident] >= 8:
                    summaries[ident]["incomplete"] = True
                    resolver.unknown(
                        scan.path, scan.node, "summary convergence limit reached; cyclic/expanding effects unresolved"
                    )
                    continue
                for caller in sorted(dependents[ident]):
                    if caller not in queued and updates[caller] < 8:
                        pending.append(caller)
                        queued.add(caller)
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
        state_field = next((step.get("field") for step in row["path"] if step.get("role") == "state_read"), None)
        prior_state, current_state = old.get("literal_state_fields", {}), new.get("literal_state_fields", {})
        if (
            state_field in prior_state
            and state_field in current_state
            and prior_state[state_field] == current_state[state_field]
        ):
            unrelated_assertions.append(row)
            continue
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
    from .condition_evidence import match_assertion

    for assertion in assertions:
        assertion["changed_condition"] = match_assertion(assertion, indexes["before"], indexes["after"])
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
            "summary_updates_per_function": 8,
            "alternative_paths_per_source": 4,
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
        condition = assertion.get("changed_condition")
        evidence = [
            {
                "obligation_id": o["id"],
                "level": "input-and-assertion" if condition else "assertion",
                "input_evidence": condition,
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
                "changed_condition_related": bool(condition),
                "changed_condition": condition,
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
            row["behavioral_rank"] = min(row.get("behavioral_rank", 4), 0 if condition else 1)
    for row in by_id.values():
        broad = all(a["relationship"].startswith("broad-") for a in row["associations"])
        observed_result = any(a.get("result_assertion") for a in row["associations"])
        row["result_assertion_rank"] = 1 if observed_result else 4
        row["primary_verification"] = not broad
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
