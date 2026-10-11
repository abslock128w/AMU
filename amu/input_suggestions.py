"""Small, non-executing input probes attached to changed Python obligations.

These rules reason about built-in scalar predicates only. They do not infer a
function contract, execute source, or claim that the changed line is reached.
"""

from __future__ import annotations

import ast
import hashlib
import json

from amu.check_drafts import _direct_call, _scalar, _stable_target
from amu.outbound import redact

MAX_PER_OBLIGATION = 5
MAX_PER_REPORT = 40
MAX_ABS_BOUNDARY = 1_000_000_000
MAX_KEY_BYTES = 128

_RELATION = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
_REVERSED = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}


def _int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int):
        return -node.operand.value
    return None


def _comparison(node: ast.AST):
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    relation = _RELATION.get(type(node.ops[0]))
    if relation is None:
        return None
    if isinstance(node.left, ast.Name):
        parameter, boundary = node.left.id, _int(node.comparators[0])
    elif isinstance(node.comparators[0], ast.Name):
        parameter, boundary = node.comparators[0].id, _int(node.left)
        relation = _REVERSED[relation]
    else:
        return None
    if boundary is None or abs(boundary) > MAX_ABS_BOUNDARY:
        return None
    return parameter, relation, boundary


def _predicate(relation: str, value: int, boundary: int) -> bool:
    if relation == "<":
        return value < boundary
    if relation == "<=":
        return value <= boundary
    if relation == ">":
        return value > boundary
    return value >= boundary


def _none_condition(node: ast.AST):
    if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Constant) and left.value is None:
            left, right = right, left
        if isinstance(left, ast.Name) and isinstance(right, ast.Constant) and right.value is None:
            if isinstance(node.ops[0], ast.Is):
                return left.id, "is-none"
            if isinstance(node.ops[0], ast.IsNot):
                return left.id, "is-not-none"
    if isinstance(node, ast.Name):
        return node.id, "truthy"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name):
        return node.operand.id, "falsey"
    return None


def _truth(condition: str, value) -> bool:
    if condition == "is-none":
        return value is None
    if condition == "is-not-none":
        return value is not None
    if condition == "truthy":
        return value is not None and value != 0 and value != "" and value is not False
    return value is None or value == 0 or value == "" or value is False


def _mapping_access(node: ast.AST):
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        key, kind, name = node.slice, "getitem", node.value.id
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and isinstance(node.func.value, ast.Name) and node.func.attr == "get"
          and not node.keywords and len(node.args) in {1, 2}
          and (len(node.args) == 1 or isinstance(node.args[1], ast.Constant) and node.args[1].value is None)):
        key, kind, name = node.args[0], "get", node.func.value.id
    else:
        return None
    if (not isinstance(key, ast.Constant) or type(key.value) is not str or not key.value
            or len(key.value.encode()) > MAX_KEY_BYTES or redact(key.value) != key.value
            or any(ord(char) < 32 or ord(char) == 127 for char in key.value)):
        return None
    return name, key.value, kind


def _annotation_types(node: ast.AST | None) -> set[str]:
    """Recognize only direct scalar annotations; no import/alias resolution."""
    if isinstance(node, ast.Name) and node.id in {"int", "str", "bool", "None"}:
        return {node.id}
    if isinstance(node, ast.Constant) and node.value is None:
        return {"None"}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _annotation_types(node.left), _annotation_types(node.right)
        return left | right if left and right else set()
    return set()


def _dict_value_type(function: ast.AST | None, parameter: str) -> str | None:
    args = _parameters(function)
    annotation = next((arg.annotation for arg in args or [] if arg.arg == parameter), None)
    if (isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "dict" and isinstance(annotation.slice, ast.Tuple)
            and len(annotation.slice.elts) == 2
            and isinstance(annotation.slice.elts[0], ast.Name) and annotation.slice.elts[0].id == "str"
            and isinstance(annotation.slice.elts[1], ast.Name)
            and annotation.slice.elts[1].id in {"int", "str", "bool"}):
        return annotation.slice.elts[1].id
    return None


def _signature(function: ast.AST | None) -> str | None:
    if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    return ast.unparse(function.args)[:300]


def _parameters(function: ast.AST | None):
    if not isinstance(function, ast.FunctionDef):
        return None
    args = function.args
    if function.decorator_list or args.posonlyargs or args.vararg or args.kwarg:
        return None
    return [*args.args, *args.kwonlyargs]


def _scalar_default(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return _scalar(node.value)
    return (isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant) and type(node.operand.value) in {int, float}
            and _scalar(node.operand.value))


def _call_readiness(old: ast.AST | None, new: ast.AST | None, parameter: str, value):
    old_params, new_params = _parameters(old), _parameters(new)
    signatures = {"before": _signature(old), "after": _signature(new)}
    if old_params is None or new_params is None:
        return "expression-probe", None, signatures, ["A plain direct function target in both versions was not established."]
    old_names, new_names = [arg.arg for arg in old_params], [arg.arg for arg in new_params]
    if parameter not in old_names or parameter not in new_names:
        return "expression-probe", None, signatures, ["The changed name is not a parameter in both source versions."]
    if old_names != new_names:
        return "partial-invocation", None, signatures, ["Baseline and candidate call signatures differ."]
    unresolved = []
    for function in (old, new):
        args = function.args
        required_pos = [arg.arg for arg in args.args[:len(args.args) - len(args.defaults)]]
        required_kw = [arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults) if default is None]
        for name in [*required_pos, *required_kw]:
            if name != parameter and f"Required argument {name} is unresolved." not in unresolved:
                unresolved.append(f"Required argument {name} is unresolved.")
        # Omitted values are accepted only when both signatures contain the
        # same bounded scalar literal default; arbitrary default expressions
        # can execute at import time and are not treated as supported setup.
        defaults = [*([None] * (len(args.args) - len(args.defaults))), *args.defaults]
        defaults += args.kw_defaults
        for arg, default in zip([*args.args, *args.kwonlyargs], defaults):
            if arg.arg != parameter and default is not None and not _scalar_default(default):
                note = f"Default for {arg.arg} is not a supported scalar literal."
                if note not in unresolved:
                    unresolved.append(note)
    if unresolved:
        return "partial-invocation", None, signatures, unresolved
    invocation = {"args": [], "kwargs": {parameter: value}}
    _, reason = _direct_call(old, new, old.name, invocation)
    if reason:
        return "partial-invocation", None, signatures, [reason]
    return "complete-source-bound-invocation", invocation, signatures, []


def _earlier_return(phase, function, expression) -> bool:
    if not isinstance(function, ast.FunctionDef) or expression is None:
        return False
    line = getattr(expression, "lineno", 0)
    for statement in function.body:
        if getattr(statement, "lineno", 0) >= line:
            break
        if any(isinstance(node, ast.Return) for node in ast.walk(statement)):
            return True
    return False


def derive(obligation: dict, old_phase, new_phase, entity: str, before: ast.AST | None,
           after: ast.AST | None, *, rebound_names: set[str] | None = None) -> tuple[list[dict], int]:
    """Derive at most five probes and count candidates omitted by that budget."""
    if before is None or after is None:
        return [], 0
    if obligation["category"] == "return":
        if not isinstance(before, ast.Return) or not isinstance(after, ast.Return):
            return [], 0
        before, after = before.value, after.value
        # A bare returned name is a scalar result, not a condition evaluated
        # for truthiness. Only a branch guard gives that projection semantics.
        if isinstance(before, ast.Name) or isinstance(after, ast.Name):
            return [], 0
        # The branch event already owns a direct comparison returned by both versions.
        if isinstance(before, ast.Compare) and isinstance(after, ast.Compare):
            return [], 0
    if before is None or after is None:
        return [], 0
    old_function = old_phase.entities.get(entity, {}).get("nodes", [None])[0]
    new_function = new_phase.entities.get(entity, {}).get("nodes", [None])[0]
    candidates = []
    old_cmp, new_cmp = _comparison(before), _comparison(after)
    if old_cmp and new_cmp and old_cmp[0] == new_cmp[0]:
        parameter, old_op, old_boundary = old_cmp
        _, new_op, new_boundary = new_cmp
        annotations = []
        for function in (old_function, new_function):
            args = _parameters(function)
            annotation = next((arg.annotation for arg in args or [] if arg.arg == parameter), None)
            annotations.append(_annotation_types(annotation))
        declared = annotations[0] & annotations[1] if all(annotations) else set()
        if declared and "int" not in declared:
            return [], 0
        rationales = {}
        for phase, boundary in (("baseline", old_boundary), ("candidate", new_boundary)):
            for offset in (-1, 0, 1):
                value = boundary + offset
                rationales.setdefault(value, []).append(f"{phase} boundary {boundary}{offset:+d}")
        for value, sources in rationales.items():
            old_result, new_result = _predicate(old_op, value, old_boundary), _predicate(new_op, value, new_boundary)
            candidates.append((0 if old_result != new_result else 1,
                {"family": "integer-boundary", "parameter": parameter, "value": value,
                 "derivation": f"Built-in integer comparison {old_op} {old_boundary} → {new_op} {new_boundary}; "
                               + ", ".join(sources),
                 "local_observation": {"before": old_result, "after": new_result,
                                       "basis": "built-in integer relation, conditional on an int input"},
                 "distinguishes_expressions": old_result != new_result,
                 "applicability": "declared-type-intent" if "int" in declared else "type-uncertain-exploratory",
                 "assumptions": ["Input is a built-in int, not bool or an overloaded numeric type."]}))
    else:
        old_none, new_none = _none_condition(before), _none_condition(after)
        if (old_none and new_none and old_none[0] == new_none[0]
                and (old_none[1] in {"is-none", "is-not-none"}) != (new_none[1] in {"is-none", "is-not-none"})):
            parameter = old_none[0]
            annotations = []
            for function in (old_function, new_function):
                args = _parameters(function)
                annotation = next((arg.annotation for arg in args or [] if arg.arg == parameter), None)
                annotations.append(_annotation_types(annotation))
            declared = annotations[0] & annotations[1] if all(annotations) else set()
            for value, label, kind in ((None, "None", "None"), (0, "zero", "int"),
                                       (False, "False", "bool"), ("", "empty string", "str")):
                if declared and kind not in declared:
                    continue
                old_result, new_result = _truth(old_none[1], value), _truth(new_none[1], value)
                if not (old_result != new_result or value is None):
                    continue
                candidates.append((0 if old_result != new_result else 2,
                    {"family": "none-truthiness", "parameter": parameter, "value": value,
                     "derivation": f"{old_none[1]} → {new_none[1]}; {label} has built-in false-like semantics.",
                     "local_observation": {"before": old_result, "after": new_result,
                                           "basis": "built-in scalar None identity/truthiness"},
                     "distinguishes_expressions": old_result != new_result,
                     "applicability": "declared-type-intent" if kind in declared else "type-uncertain-exploratory",
                     "assumptions": ["The API accepts this scalar input; static annotation is intent, not runtime proof."]}))
        else:
            old_map, new_map = _mapping_access(before), _mapping_access(after)
            if (old_map and new_map and old_map[:2] == new_map[:2] and old_map[2] != new_map[2]):
                parameter, key = old_map[:2]
                value_type = _dict_value_type(old_function, parameter)
                if value_type != _dict_value_type(new_function, parameter):
                    value_type = None
                representative = {"int": 1, "str": "x", "bool": True}.get(value_type)
                states = [("absent", {}), ("present-none", {key: None})]
                if representative is not None:
                    states.append(("present-scalar", {key: representative}))
                for state, value in states:
                    before_result = "raises KeyError" if state == "absent" and old_map[2] == "getitem" else "returns None"
                    after_result = "raises KeyError" if state == "absent" and new_map[2] == "getitem" else "returns None"
                    if state == "present-scalar":
                        before_result = after_result = "returns the supplied scalar"
                    access_change = (f"Direct {parameter}[{key!r}] → {parameter}.get({key!r})"
                                     if old_map[2] == "getitem" else
                                     f"Direct {parameter}.get({key!r}) → {parameter}[{key!r}]")
                    derivation = access_change + f"; probe key state {state}."
                    if state == "present-scalar":
                        derivation += f" Representative {representative!r} follows declared dict[str, {value_type}] intent."
                    candidates.append((0 if before_result != after_result else 1,
                        {"family": "mapping-key-presence", "parameter": parameter, "value": value,
                         "mapping_key": key, "key_state": state,
                         "derivation": derivation,
                         "local_observation": {"before": before_result, "after": after_result,
                                               "basis": "built-in dict with the stated literal key"},
                         "distinguishes_expressions": before_result != after_result,
                         "applicability": "declared-type-intent" if state == "present-scalar" else "type-uncertain-exploratory",
                         "assumptions": ["Input is a built-in dict with exactly the stated key presence; other required fields and API setup are unresolved."]}))
    if not candidates or candidates[0][1]["parameter"] in (rebound_names or set()):
        return [], 0
    parameter = candidates[0][1]["parameter"]
    for function in (old_function, new_function):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return [], 0
        names = {arg.arg for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
        if parameter not in names:
            return [], 0
    candidates.sort(key=lambda pair: (pair[0], str(pair[1]["value"]), pair[1].get("key_state", "")))
    source_spans = {}
    for label, phase, expression in (("before", old_phase, before), ("after", new_phase, after)):
        evidence = phase.evidence(expression, "input_expression")
        source_spans[label] = {"path": evidence["path"], "location": evidence["location"],
                               "ast_hash": evidence["ast_hash"]}
    stable_target = ("." not in entity and _stable_target(old_phase.tree, entity)
                     and _stable_target(new_phase.tree, entity))
    signature_details = {}
    for label, function in (("before", old_function), ("after", new_function)):
        text = ast.unparse(function.args)
        if len(text) > 300:
            signature_details[label] = {"truncated": True, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    rows = []
    for priority, candidate in candidates[:MAX_PER_OBLIGATION]:
        parameter, value = candidate["parameter"], candidate["value"]
        status, invocation, signatures, missing = _call_readiness(old_function, new_function, parameter, value)
        if candidate["family"] == "mapping-key-presence":
            status, invocation = "partial-invocation", None
            missing = [*missing, "The existing draft templates do not accept literal mapping arguments or key-presence observations."]
        if "." in entity:
            status, invocation = "expression-probe", None
            missing.append("Method receiver and construction are unresolved.")
        elif status == "complete-source-bound-invocation" and not stable_target:
            status, invocation = "expression-probe", None
            missing.append("The module-level target is rebound or dynamically resolved.")
        earlier_return = _earlier_return(old_phase, old_function, before) or _earlier_return(new_phase, new_function, after)
        if earlier_return:
            missing.append("An earlier return may bypass the changed expression; reachability is unestablished.")
        missing.append("Target import and runtime setup require review; none was inferred.")
        candidate.update({
            "probe_status": status, "priority": "distinguishing" if priority == 0 else "preservation-or-exploratory",
            "source_spans": source_spans, "binding": {"parameter": parameter,
                "before_signature": signatures["before"], "after_signature": signatures["after"]},
            "invocation": invocation, "unresolved": missing,
            "reachability": {"status": "unestablished", "reason":
                             "earlier-return" if earlier_return else "static-only"},
            "observable": {"local": "changed expression result or exception",
                           "whole_function": "see obligation.observable"},
            "expected_result": "unknown",
        })
        identity = {"obligation": obligation["id"], "family": candidate["family"],
                    "parameter": parameter, "value": value, "spans": source_spans}
        candidate["id"] = obligation["id"] + ":input:" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]
        if signature_details:
            candidate["binding"]["signature_details"] = signature_details
        rows.append(candidate)
    return rows, max(0, len(candidates) - MAX_PER_OBLIGATION)
