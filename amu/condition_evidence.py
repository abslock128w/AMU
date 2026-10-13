"""Bounded literal argument/condition relevance. Never evaluate target functions.

A match is source evidence on one condition side, not a test run, changed
output oracle, feasible-path proof or demonstrated regression detection.
"""

from __future__ import annotations

import ast
import operator

from .change_analysis import _bindings, _location

UNKNOWN = object()
COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def literal_expression(node, env, *, builtin_types=False, depth=0):
    if depth > 16:
        return UNKNOWN
    if isinstance(node, ast.Constant) and type(node.value) in {str, bytes, int, float, bool, type(None)}:
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, UNKNOWN)
    if isinstance(node, ast.Subscript):
        value = literal_expression(node.value, env, depth=depth + 1)
        key = literal_expression(node.slice, env, depth=depth + 1)
        if value is UNKNOWN or key is UNKNOWN or type(value) not in {dict, list, tuple, str, bytes}:
            return UNKNOWN
        try:
            return value[key]
        except (KeyError, IndexError, TypeError):
            return UNKNOWN
    if isinstance(node, ast.UnaryOp):
        value = literal_expression(node.operand, env, depth=depth + 1)
        if value is UNKNOWN:
            return UNKNOWN
        if isinstance(node.op, ast.Not):
            return not value
        if isinstance(node.op, ast.USub) and type(value) in {int, float}:
            return -value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [literal_expression(v, env, depth=depth + 1) for v in node.elts]
        return UNKNOWN if any(v is UNKNOWN for v in values) else values
    if isinstance(node, ast.Dict):
        keys = [literal_expression(k, env, depth=depth + 1) for k in node.keys]
        values = [literal_expression(v, env, depth=depth + 1) for v in node.values]
        if any(v is UNKNOWN for v in keys + values):
            return UNKNOWN
        try:
            return dict(zip(keys, values))
        except TypeError:
            return UNKNOWN
    if isinstance(node, ast.BoolOp):
        values = [literal_expression(v, env, builtin_types=builtin_types, depth=depth + 1) for v in node.values]
        if any(v is UNKNOWN for v in values):
            return UNKNOWN
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        values = [literal_expression(n, env, depth=depth + 1) for n in [node.left, *node.comparators]]
        if any(v is UNKNOWN for v in values):
            return UNKNOWN
        try:
            return all(COMPARE[type(op)](a, b) for op, a, b in zip(node.ops, values, values[1:]))
        except (TypeError, KeyError):
            return UNKNOWN
    if (
        builtin_types
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
        and len(node.args) == 2
        and not node.keywords
    ):
        value = literal_expression(node.args[0], env, depth=depth + 1)
        kind = node.args[1]
        types = {"str": str, "bytes": bytes, "int": int, "float": float, "bool": bool}
        if value is not UNKNOWN and isinstance(kind, ast.Name) and kind.id in types:
            return isinstance(value, types[kind.id])
    return UNKNOWN


def parameter_cases(index, test_id):
    from .value_flow import _marker_literal

    ident = test_id.replace("::", "#", 1)
    item = index.functions.get(ident) or index.test_observers.get(ident)
    if item is None:
        return []
    node = item[2]
    cases = [{}]
    for d in node.decorator_list:
        if not isinstance(d, ast.Call) or len(d.args) != 2 or not isinstance(d.func, ast.Attribute):
            return []
        try:
            root = d.func.value.value.id
            names = _marker_literal(d.args[0], pytest_root=root)
            names = [n.strip() for n in names.split(",")] if isinstance(names, str) else names
            rows = _marker_literal(d.args[1], pytest_root=root)
        except (ValueError, AttributeError, TypeError):
            return []
        following = []
        for prior in cases:
            for row in rows:
                values = [row] if len(names) == 1 else row
                if not isinstance(values, list) or len(values) != len(names):
                    return []
                following.append({**prior, **dict(zip(names, values))})
                if len(following) > 64:
                    return []
        cases = following
    return cases


def bind(node, call, env, *, bound_receiver=False):
    params = [*node.args.posonlyargs, *node.args.args]
    values = ([UNKNOWN] if bound_receiver else []) + [literal_expression(a, env) for a in call.args]
    if len(values) > len(params) or any(isinstance(a, ast.Starred) for a in call.args):
        return None
    result = {p.arg: v for p, v in zip(params, values)}
    for keyword in call.keywords:
        if keyword.arg is None:
            extra = literal_expression(keyword.value, env)
            if type(extra) is not dict or any(type(k) is not str for k in extra):
                return None
        else:
            extra = {keyword.arg: literal_expression(keyword.value, env)}
        for name, value in extra.items():
            if name in result or name not in {p.arg for p in [*params, *node.args.kwonlyargs]}:
                return None
            result[name] = value
    defaults = (
        dict(zip([p.arg for p in params][-len(node.args.defaults) :], node.args.defaults)) if node.args.defaults else {}
    )
    defaults.update({p.arg: d for p, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None})
    for p in [*params, *node.args.kwonlyargs]:
        if p.arg not in result:
            result[p.arg] = literal_expression(defaults[p.arg], {}) if p.arg in defaults else UNKNOWN
    return result


def return_on_selected_side(statements, env):
    for statement in statements:
        if isinstance(statement, ast.Return):
            return True
        if isinstance(statement, ast.Raise):
            return False
        if isinstance(statement, ast.If):
            outcome = literal_expression(statement.test, env, builtin_types=True)
            if outcome is UNKNOWN:
                return False
            if return_on_selected_side(statement.body if outcome else statement.orelse, env):
                return True
            continue
        if isinstance(statement, (ast.For, ast.While, ast.Try, ast.With)):
            return False
    return False


def constraints_match(constraints, env):
    for guard in constraints:
        if guard.get("kind") in {"is-none", "not-none"}:
            value = env.get(guard.get("variable"), UNKNOWN)
            if value is UNKNOWN:
                return False
            if guard["kind"] == "is-none" and value is not None or guard["kind"] == "not-none" and value is None:
                return False
            continue
        expression = guard.get("expression")
        if not expression:
            return False
        try:
            test = ast.parse(expression, mode="eval").body
        except SyntaxError:
            return False
        value = literal_expression(test, env, builtin_types=True)
        if value is UNKNOWN:
            return False
        expected = guard.get("branch")
        if expected == "then" and not value or expected == "else" and value:
            return False
    return True


def _match_path(assertion, before_index, after_index):
    source = assertion["entity_id"]
    old = before_index.functions.get(source)
    new = after_index.functions.get(source)
    if old is None or new is None:
        return None
    previous = {ast.dump(n.test, include_attributes=False) for n in ast.walk(old[2]) if isinstance(n, ast.If)}
    conditions = [
        n
        for n in ast.walk(new[2])
        if isinstance(n, ast.If)
        and ast.dump(n.test, include_attributes=False) not in previous
        and any(isinstance(x, ast.Return) for x in ast.walk(n))
    ]
    if not conditions:
        return None
    calls = [s for s in assertion["path"] if s.get("role") == "resolved_call" and s.get("callee_id")]
    for case_index, case in enumerate(parameter_cases(before_index, assertion["test_id"])):
        env = case
        for step in calls:
            callee = before_index.functions.get(step["callee_id"])
            if callee is None:
                break
            try:
                call = ast.parse(step["expression"], mode="eval").body
            except (SyntaxError, ValueError):
                break
            if not isinstance(call, ast.Call):
                break
            if not constraints_match(step.get("constraints", []), env):
                break
            env = bind(callee[2], call, env, bound_receiver=bool(step.get("receiver")))
            if env is None:
                break
            if step["callee_id"] != source:
                continue
            for condition in conditions:
                shadows = _bindings(new[2]) | set(after_index.tables.get(new[0], {}))
                known_types = not ({"isinstance", "str", "bytes", "int", "float", "bool"} & shadows)
                outcome = literal_expression(condition.test, env, builtin_types=known_types)
                if type(outcome) is not bool:
                    continue
                branch = condition.body if outcome else condition.orelse
                if not return_on_selected_side(branch, env):
                    continue
                return {
                    "kind": "literal-parameter-condition",
                    "entity_id": source,
                    "condition": ast.unparse(condition.test),
                    "condition_location": _location(condition.test),
                    "condition_phase": "after",
                    "outcome": outcome,
                    "baseline_parameter_case": case_index,
                    "bindings": {k: repr(v) for k, v in env.items() if v is not UNKNOWN},
                    "authorship": "baseline source metadata",
                    "static_only": True,
                    "path_feasibility_proven": False,
                    "expectation_source": "existing baseline assertion; no candidate-derived business oracle",
                }
    return None


def match_assertion(assertion, before_index, after_index):
    for path in [assertion["path"], *assertion.get("alternative_paths", [])]:
        result = _match_path({**assertion, "path": path}, before_index, after_index)
        if result:
            result["matched_path"] = path
            return result
    return None
