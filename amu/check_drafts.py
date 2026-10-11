"""Bounded, source-grounded check proposals. This module never imports targets."""

import ast
import hashlib
import json
import keyword
import math
from pathlib import PurePosixPath

MAX_CHECK_SPEC_BYTES = 4096
MAX_SOURCE_EXAMPLE_BYTES = 1024


class CheckSpecError(ValueError):
    pass


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _scalar(value):
    return value is None or type(value) in {str, bool, int} or type(value) is float and math.isfinite(value)


def validate_check_spec(value):
    """Reject executable snippets, unbounded data, and implied expectations."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {"obligation_id", "invocation", "expectation", "intent"}:
        raise CheckSpecError("check_spec must have obligation_id, invocation and optional expectation/intent")
    try:
        if len(_encoded(value)) > MAX_CHECK_SPEC_BYTES:
            raise CheckSpecError("check_spec exceeds its 4096-byte limit")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CheckSpecError("check_spec must be bounded JSON data") from exc
    if not isinstance(value.get("obligation_id"), str) or len(value["obligation_id"]) > 400:
        raise CheckSpecError("check_spec obligation_id must be bounded text")
    invocation = value.get("invocation")
    if not isinstance(invocation, dict) or invocation.get("kind") not in {"direct-return", "ast-boundary"}:
        raise CheckSpecError("check_spec invocation kind is unsupported")
    if invocation["kind"] == "direct-return":
        if set(invocation) != {"kind", "args", "kwargs"} or not isinstance(invocation["args"], list) or len(invocation["args"]) > 6:
            raise CheckSpecError("direct-return requires up to six literal positional arguments and keyword arguments")
        if not isinstance(invocation["kwargs"], dict) or len(invocation["kwargs"]) > 6:
            raise CheckSpecError("direct-return keyword arguments must be bounded")
        if not all(_scalar(item) for item in invocation["args"]) or not all(
                isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and _scalar(item)
                for key, item in invocation["kwargs"].items()):
            raise CheckSpecError("direct-return arguments must be scalar literals")
    else:
        source = invocation.get("source_example")
        if set(invocation) != {"kind", "source_example"} or not isinstance(source, str) or not source or len(source.encode()) > MAX_SOURCE_EXAMPLE_BYTES:
            raise CheckSpecError("ast-boundary requires a bounded source_example")
    expectation = value.get("expectation")
    if expectation is not None:
        if not isinstance(expectation, dict) or set(expectation) != {"basis", "reference", "predicate", "value"}:
            raise CheckSpecError("expectation requires basis, reference, predicate and value")
        if expectation["basis"] not in {"user-supplied-task-requirement", "user-designated-baseline-preservation"}:
            raise CheckSpecError("expectation basis must be an explicit user-supplied requirement or preservation designation")
        if not isinstance(expectation["reference"], str) or not 1 <= len(expectation["reference"].encode()) <= 500:
            raise CheckSpecError("expectation reference must identify the exact requirement in bounded text")
        if expectation["predicate"] not in {"equals", "unresolved-boundary"} or not _scalar(expectation["value"]):
            raise CheckSpecError("expectation predicate/value is unsupported")
        if expectation["predicate"] == "unresolved-boundary" and expectation["value"] is not True:
            raise CheckSpecError("unresolved-boundary expectation value must be true")
    if value.get("intent", "inspection") not in {"inspection", "bug-fix", "preservation"}:
        raise CheckSpecError("check_spec intent is unsupported")


def _functions(tree, name):
    return [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]


def _import_supported(path, name, tests):
    module = ".".join(PurePosixPath(path).with_suffix("").parts)
    parent, _, stem = module.rpartition(".")
    for source in tests:
        for node in source["tree"].body:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module == module and any(alias.name == name for alias in node.names):
                    return source["path"]
                if parent and node.module == parent and any(alias.name == stem for alias in node.names):
                    return source["path"]
            elif isinstance(node, ast.Import) and any(alias.name == module for alias in node.names):
                return source["path"]
    return None


def _pytest_supported(tests):
    return next((source["path"] for source in tests if any(
        isinstance(node, ast.Import) and any(alias.name == "pytest" for alias in node.names)
        for node in source["tree"].body) and any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in source["tree"].body)), None)


def _boundary_sample(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    function = tree.body[0]
    if function.decorator_list or function.args.defaults or function.args.kw_defaults or len(function.body) != 1:
        return None
    statement = function.body[0]
    call = statement.value if isinstance(statement, (ast.Return, ast.Expr)) else None
    params = {arg.arg for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]}
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in params
            and len([node for node in ast.walk(function) if isinstance(node, ast.Call)]) == 1):
        return None
    return call.lineno


def _literal(value):
    # JSON scalar spellings are not Python spellings for booleans/null.
    return repr(value)


def propose(obligation, request_spec, *, baseline_tree, candidate_tree, tests, file_identity, patch_sha256,
            config_sha256, search_scope):
    """Return a specification and optional draft; never write or execute either."""
    entity_id = obligation["entity_id"]
    path, _, name = entity_id.partition("#")
    plan = obligation.get("verification_plan") or {}
    gap = {"status": "no-matching-assertion-established" if not any(
        check["level"] in {"input-and-assertion", "assertion"} for check in plan.get("existing_baseline_tests", []))
        else "some-baseline-assertion-evidence",
        "baseline_test_files_inspected": search_scope.get("baseline_test_files_inspected", 0),
        "inventory_complete": search_scope.get("test_inventory_complete", False),
        "unavailable_baseline_test_files": search_scope.get("unavailable_baseline_test_files", 0),
        "source_call_search_limits": search_scope.get("source_call_search_limits", 0),
        "search_complete": bool(search_scope.get("test_inventory_complete", False)
                                and not search_scope.get("unavailable_baseline_test_files", 0)
                                and not search_scope.get("source_call_search_limits", 0)),
        "candidate_count": len(plan.get("existing_baseline_tests", [])),
        "limits": ["Bounded static search cannot prove that no suitable test exists."] + plan.get("unresolved", [])[:2]}
    proposed_input = plan.get("proposed_input")
    spec = {"obligation_id": obligation["id"], "entity_id": entity_id,
            "source": file_identity, "evidence_gap": gap,
            "check_specification": {"status": "incomplete", "setup": None, "input": proposed_input,
                                    "observable": obligation.get("observable"),
                                    "expected_result": None, "expectation_basis": "unresolved",
                                    "expectation_source": None,
                                    "candidate_test_ids": [row["test_id"] for row in plan.get("existing_baseline_tests", [])[:3]],
                                    "omitted_candidate_count": max(0, len(plan.get("existing_baseline_tests", [])) - 3)},
            "draft": None, "draft_status": "unavailable", "draft_reason": "Invocation and reviewed expectation details are needed.",
            "review_status": "required", "execution_status": "not-run"}
    if request_spec is None or request_spec["obligation_id"] != obligation["id"]:
        return spec
    invocation = request_spec["invocation"]
    expectation = request_spec.get("expectation")
    spec["check_specification"].update(status="specified", input=invocation,
        expected_result=expectation["value"] if expectation else None,
        expectation_basis=expectation["basis"] if expectation else "unresolved",
        expectation_source=expectation["reference"] if expectation else None)
    if expectation is None:
        spec["draft_reason"] = "Expected result is unresolved; inspect the observable before writing an assertion."
        return spec
    if obligation.get("source_role", {}).get("role") != "production":
        spec["draft_reason"] = "Only production obligations support generated test drafts."
        return spec
    if not path.endswith(".py") or not name.isidentifier() or keyword.iskeyword(name):
        spec["draft_reason"] = "A direct top-level Python import target is unavailable."
        return spec
    if baseline_tree is None or candidate_tree is None or len(_functions(baseline_tree, name)) != 1 or len(_functions(candidate_tree, name)) != 1:
        spec["draft_reason"] = "The target is not an unambiguous function in both source versions."
        return spec
    old, new = _functions(baseline_tree, name)[0], _functions(candidate_tree, name)[0]
    if old.decorator_list or new.decorator_list or old.args.vararg or old.args.kwarg or new.args.vararg or new.args.kwarg:
        spec["draft_reason"] = "Decorators or variable argument binding make invocation uncertain."
        return spec
    import_example = _import_supported(path, name, tests)
    framework_example = _pytest_supported(tests)
    if not import_example or not framework_example:
        spec["draft_reason"] = "A baseline import path or plain pytest function convention was not established."
        return spec
    module = ".".join(PurePosixPath(path).with_suffix("").parts)
    call = None
    if invocation["kind"] == "direct-return" and expectation["predicate"] == "equals":
        if len(old.args.posonlyargs) or len(new.args.posonlyargs):
            spec["draft_reason"] = "Positional-only binding is outside this first draft template."
            return spec
        parameters = [arg.arg for arg in old.args.args]
        if (parameters != [arg.arg for arg in new.args.args]
                or [arg.arg for arg in old.args.kwonlyargs] != [arg.arg for arg in new.args.kwonlyargs]):
            spec["draft_reason"] = "The call signature changed across the patch."
            return spec
        supplied = parameters[:len(invocation["args"])] + list(invocation["kwargs"])
        if len(set(supplied)) != len(supplied) or len(invocation["args"]) > len(parameters) or any(
                key not in parameters + [arg.arg for arg in old.args.kwonlyargs] for key in invocation["kwargs"]):
            spec["draft_reason"] = "Arguments do not bind unambiguously to the baseline signature."
            return spec
        def required_names(function):
            return (parameters[:len(parameters)-len(function.args.defaults)]
                    + [arg.arg for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
                       if default is None])
        if any(parameter not in supplied for function in (old, new) for parameter in required_names(function)):
            spec["draft_reason"] = "Required arguments are missing."
            return spec
        args = [_literal(item) for item in invocation["args"]] + [f"{key}={_literal(value)}" for key, value in invocation["kwargs"].items()]
        call = f"{name}({', '.join(args)})"
        body = f"    assert {call} == {_literal(expectation['value'])}\n"
    elif invocation["kind"] == "ast-boundary" and expectation["predicate"] == "unresolved-boundary":
        source = invocation["source_example"]
        line = _boundary_sample(source)
        if line is None or len(old.args.args) != 1 or len(new.args.args) != 1:
            spec["draft_reason"] = "The example or AST-node argument is outside the supported boundary shape."
            return spec
        if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and isinstance(node.func.value, ast.Name) and node.func.value.id == "ast" and node.func.attr == "walk"
                   for node in ast.walk(old)):
            spec["draft_reason"] = "The baseline function is not a source AST walker."
            return spec
        body = (f"    node = ast.parse({_literal(source)}).body[0]\n"
                f"    rows = {name}(node)\n"
                f"    assert any(row.get('line') == {line} and 'unresolved' in row.get('reason', '') for row in rows)\n")
    else:
        spec["draft_reason"] = "The requested input and expectation are not a supported pair."
        return spec
    imports = ("import ast\n" if invocation["kind"] == "ast-boundary" else "") + f"from {module} import {name}\n"
    code = imports + "\ndef test_proposed_check():\n" + body
    ast.parse(code)
    basis = {"patch_sha256": patch_sha256, "source": file_identity,
             "configuration_sha256": config_sha256,
             "expectation_sha256": hashlib.sha256(_encoded(expectation)).hexdigest(),
             "invocation_sha256": hashlib.sha256(_encoded(invocation)).hexdigest()}
    spec["check_specification"].update(setup="Pure literal input; no test fixtures or cleanup.",
        observable="Returned value" if invocation["kind"] == "direct-return" else
        f"Boundary row at source example line {line} with an unresolved reason",
        framework="plain pytest function", import_evidence=import_example, framework_evidence=framework_example)
    spec.update(draft_status="review-required", draft_reason=None,
                draft={"filename_suggestion": "test_proposed_check.py", "source": code,
                       "sha256": hashlib.sha256(code.encode()).hexdigest(), "identity": basis,
                       "limitations": "Syntax and source paths do not prove that imports, setup, or the intended behavior run correctly."})
    return spec
