"""Bounded, source-grounded check proposals. This module never imports targets."""

import ast
import hashlib
import json
import keyword
import math
from pathlib import PurePosixPath

MAX_CHECK_SPEC_BYTES = 4096
MAX_MAPPING_KEY_BYTES = 128
SUPPORTED_EXCEPTIONS = frozenset({
    "ValueError", "TypeError", "KeyError", "IndexError", "LookupError", "OverflowError",
    "ZeroDivisionError", "RuntimeError", "NotImplementedError", "FileNotFoundError",
})


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
    if not isinstance(value.get("obligation_id"), str) or not value["obligation_id"] or len(value["obligation_id"]) > 400:
        raise CheckSpecError("check_spec obligation_id must be bounded text")
    invocation = value.get("invocation")
    if isinstance(invocation, dict) and invocation.get("kind") == "ast-boundary":
        raise CheckSpecError("ast-boundary drafting was retired in schema 7; schema-6 callback evidence remains historical")
    if not isinstance(invocation, dict) or not isinstance(invocation.get("kind"), str) or invocation.get("kind") not in {
            "direct-return", "mapping-field", "expected-exception"}:
        raise CheckSpecError("check_spec invocation kind must be direct-return, mapping-field or expected-exception")
    kind = invocation["kind"]
    required = {"kind", "args", "kwargs"} | ({"key", "operator"} if kind == "mapping-field" else set())
    if set(invocation) != required or not isinstance(invocation["args"], list) or len(invocation["args"]) > 6:
        raise CheckSpecError(f"{kind} requires up to six literal positional arguments and only its documented fields")
    if not isinstance(invocation["kwargs"], dict) or len(invocation["kwargs"]) > 6:
        raise CheckSpecError(f"{kind} keyword arguments must be bounded")
    if not all(_scalar(item) for item in invocation["args"]) or not all(
            isinstance(key, str) and key.isidentifier() and not keyword.iskeyword(key) and _scalar(item)
            for key, item in invocation["kwargs"].items()):
        raise CheckSpecError(f"{kind} arguments must be scalar literals")
    if kind == "mapping-field":
        key = invocation["key"]
        if not isinstance(key, str) or not key or len(key.encode()) > MAX_MAPPING_KEY_BYTES or any(
                ord(char) < 32 or ord(char) == 127 for char in key):
            raise CheckSpecError("mapping-field key must be nonempty literal text of at most 128 bytes")
        if invocation["operator"] != "getitem":
            raise CheckSpecError("mapping-field supports only getitem indexing; key presence and .get semantics are unsupported")
    expectation = value.get("expectation")
    if expectation is not None:
        fields = {"basis", "reference", "predicate", "value"} | ({"match"} if kind == "expected-exception" else set())
        if not isinstance(expectation, dict) or set(expectation) != fields:
            raise CheckSpecError("expectation requires basis, reference, predicate, value and match for exceptions only")
        if not isinstance(expectation["basis"], str) or expectation["basis"] not in {
                "user-supplied-task-requirement", "user-designated-baseline-preservation"}:
            raise CheckSpecError("expectation basis must be an explicit user-supplied requirement or preservation designation")
        if not isinstance(expectation["reference"], str) or not 1 <= len(expectation["reference"].encode()) <= 500:
            raise CheckSpecError("expectation reference must identify the exact requirement in bounded text")
        if kind == "expected-exception":
            if (expectation["predicate"] != "raises" or not isinstance(expectation["value"], str)
                    or expectation["value"] not in SUPPORTED_EXCEPTIONS):
                raise CheckSpecError("expected-exception requires a supported specific built-in exception type; Exception is too broad")
            if not isinstance(expectation["match"], str) or expectation["match"] not in {"subclass", "exact"}:
                raise CheckSpecError("expected-exception match must explicitly be subclass or exact")
        elif expectation["predicate"] != "equals" or not _scalar(expectation["value"]):
            raise CheckSpecError(f"{kind} requires an equals predicate with a bounded scalar expected value")
    if not isinstance(value.get("intent", "inspection"), str) or value.get("intent", "inspection") not in {
            "inspection", "bug-fix", "preservation"}:
        raise CheckSpecError("check_spec intent is unsupported")


def _functions(tree, name):
    return [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]


def _import_supported(path, name, tests):
    module = ".".join(PurePosixPath(path).with_suffix("").parts)
    parent, _, stem = module.rpartition(".")
    for source in tests:
        matched, ambiguous = False, False
        for node in source["tree"].body:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                for alias in node.names:
                    if ((node.module == module and alias.name == name)
                            or (parent and node.module == parent and alias.name == stem)):
                        matched = True
                    elif (alias.asname or alias.name) == name or alias.name == "*":
                        ambiguous = True
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module:
                        matched = True
                    elif (alias.asname or alias.name.split(".")[0]) == name:
                        ambiguous = True
        if matched and not ambiguous:
            return source["path"]
    return None


def _pytest_supported(tests):
    return next((source["path"] for source in tests if any(
        isinstance(node, ast.Import) and any(alias.name == "pytest" for alias in node.names)
        for node in source["tree"].body) and any(
        isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in source["tree"].body)), None)


def _stable_target(tree, name):
    """Require one plain definition and no other visible module-level binding."""
    definitions = _functions(tree, name)
    if len(definitions) != 1:
        return False
    for node in tree.body:
        if node is definitions[0]:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # Local names inside unrelated definitions do not rebind this module target.
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                return False
        elif any(isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del))
                 and child.id == name for child in ast.walk(node)):
            return False
        elif any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                 and child.func.id in {"exec", "eval", "globals", "locals"} for child in ast.walk(node)):
            return False
    return True


def _direct_call(old, new, name, invocation):
    """Build one literal invocation only when it binds both static signatures."""
    if old.decorator_list or new.decorator_list or old.args.vararg or old.args.kwarg or new.args.vararg or new.args.kwarg:
        return None, "Decorators or variable argument binding make invocation uncertain."
    if old.args.posonlyargs or new.args.posonlyargs:
        return None, "Positional-only binding is outside these draft templates."
    parameters = [arg.arg for arg in old.args.args]
    kwonly = [arg.arg for arg in old.args.kwonlyargs]
    if parameters != [arg.arg for arg in new.args.args] or kwonly != [arg.arg for arg in new.args.kwonlyargs]:
        return None, "The call signature changed across the patch."
    supplied = parameters[:len(invocation["args"])] + list(invocation["kwargs"])
    if (len(set(supplied)) != len(supplied) or len(invocation["args"]) > len(parameters)
            or any(key not in parameters + kwonly for key in invocation["kwargs"])):
        return None, "Arguments do not bind unambiguously to the baseline signature."
    for function in (old, new):
        required = (parameters[:len(parameters) - len(function.args.defaults)]
                    + [arg.arg for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
                       if default is None])
        if any(parameter not in supplied for parameter in required):
            return None, "Required arguments are missing in at least one source version."
    args = [_literal(item) for item in invocation["args"]]
    args += [f"{key}={_literal(value)}" for key, value in invocation["kwargs"].items()]
    return f"{name}({', '.join(args)})", None


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
            "authorship": {"discovered": ["changed production source and its identity"],
                           "supplied": [], "deterministic": [],
                           "unresolved": ["expected result", "runtime reachability and behavior"]},
            "draft": None, "draft_status": "unavailable", "draft_reason": "Invocation and reviewed expectation details are needed.",
            "review_status": "required", "execution_status": "not-run"}
    if request_spec is None or request_spec["obligation_id"] != obligation["id"]:
        return spec
    invocation = request_spec["invocation"]
    expectation = request_spec.get("expectation")
    spec["check_specification"].update(status="specified" if expectation else "incomplete", input=invocation,
        expected_result=expectation["value"] if expectation else None,
        expectation_basis=expectation["basis"] if expectation else "unresolved",
        expectation_source=expectation["reference"] if expectation else None)
    spec["authorship"]["supplied"].append("literal call arguments and observation kind")
    if invocation["kind"] == "mapping-field":
        spec["authorship"]["supplied"].append("mapping key and getitem operator")
        spec["authorship"]["unresolved"].append("whether the function returns a mapping for this input")
    if expectation is not None:
        spec["authorship"]["supplied"].append("expected value, basis, and exact reference")
        spec["authorship"]["unresolved"].remove("expected result")
    if invocation["kind"] == "mapping-field":
        spec["check_specification"]["observable"] = {
            "kind": "returned-mapping-field", "key": invocation["key"], "operator": "getitem",
            "missing_key": "raises KeyError", "present_none": "compares as None",
            "source": "supplied observation requirement; return type not established"}
    elif invocation["kind"] == "expected-exception":
        spec["check_specification"]["observable"] = {
            "kind": "direct-call-exception", "match": expectation["match"] if expectation else None,
            "source": "supplied observation requirement; exception occurrence not established"}
    if expectation is None:
        spec["draft_reason"] = "Expected result is unresolved; inspect the observable before writing an assertion."
        return spec
    if obligation.get("source_role", {}).get("role") != "production":
        spec["draft_reason"] = "Only production obligations support generated test drafts."
        return spec
    if not path.endswith(".py") or not name.isidentifier() or keyword.iskeyword(name):
        spec["draft_reason"] = "A direct top-level Python import target is unavailable."
        return spec
    if baseline_tree is None or candidate_tree is None or not _stable_target(baseline_tree, name) or not _stable_target(candidate_tree, name):
        spec["draft_reason"] = "The target is not an unambiguous stable function in both source versions."
        return spec
    old, new = _functions(baseline_tree, name)[0], _functions(candidate_tree, name)[0]
    import_example = _import_supported(path, name, tests)
    framework_example = _pytest_supported(tests)
    if not import_example or not framework_example:
        spec["draft_reason"] = "An unambiguous baseline import path or plain pytest function convention was not established."
        return spec
    module = ".".join(PurePosixPath(path).with_suffix("").parts)
    call, reason = _direct_call(old, new, name, invocation)
    if reason:
        spec["draft_reason"] = reason
        return spec
    kind = invocation["kind"]
    if kind == "direct-return":
        body = f"    assert {call} == {_literal(expectation['value'])}\n"
        observable = "Returned scalar value"
    elif kind == "mapping-field":
        body = (f"    result = {call}\n"
                f"    assert result[{_literal(invocation['key'])}] == {_literal(expectation['value'])}\n")
        observable = "Value at the supplied top-level mapping key, using getitem indexing"
    elif expectation["match"] == "subclass":
        body = f"    with pytest.raises({expectation['value']}):\n        {call}\n"
        observable = "A raised built-in exception, accepting subclasses"
    else:
        body = (f"    with pytest.raises({expectation['value']}) as caught:\n"
                f"        {call}\n"
                f"    assert type(caught.value) is {expectation['value']}\n")
        observable = "A raised built-in exception of the exact specified type"
    imports = ("import pytest\n" if kind == "expected-exception" else "") + f"from {module} import {name}\n"
    code = imports + "\ndef test_proposed_check():\n" + body
    ast.parse(code)
    basis = {"patch_sha256": patch_sha256, "source": file_identity,
             "configuration_sha256": config_sha256,
             "expectation_sha256": hashlib.sha256(_encoded(expectation)).hexdigest(),
             "invocation_sha256": hashlib.sha256(_encoded(invocation)).hexdigest()}
    spec["check_specification"].update(setup="Pure literal input; no test fixtures or cleanup.",
        observable=observable if kind == "direct-return" else spec["check_specification"]["observable"],
        framework="plain pytest function", import_evidence=import_example, framework_evidence=framework_example)
    spec["authorship"]["discovered"] += ["plain target signature in both source versions",
                                           "baseline import-path evidence", "plain pytest convention"]
    spec["authorship"]["deterministic"] += ["module import path from source path",
                                              "literal assertion template and draft identity hashes"]
    spec.update(draft_status="review-required", draft_reason=None,
                draft={"filename_suggestion": "test_proposed_check.py", "source": code,
                       "sha256": hashlib.sha256(code.encode()).hexdigest(), "identity": basis,
                       "limitations": "Syntax and source paths do not prove that imports, setup, or the intended behavior run correctly."})
    return spec
