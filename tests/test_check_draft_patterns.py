"""General literal drafting patterns; these tests inspect drafts but never run them."""

import ast
import base64
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from amu.check_drafts import CheckSpecError, validate_check_spec
from amu.services import impact
from tests.test_patch_actionable import diff

FIXTURES = Path(__file__).parent / "fixtures" / "draft_patterns"


def _complete_obligation_items(tmp_path, request):
    """Schema-8 suggestions can push a selected obligation into lossless fragments."""
    entries, cursor = [], None
    while True:
        page = impact.execute_patch(str(tmp_path), replace(request, section="obligations", cursor=cursor, page_size=100))
        entries.extend(page["details"]["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    rows, fragments = [], {}
    for entry in entries:
        if "evidence_fragment" not in entry:
            rows.append(entry)
        else:
            part = entry["evidence_fragment"]
            fragments.setdefault(part["item_sha256"], []).append(part)
    for digest, parts in fragments.items():
        parts.sort(key=lambda part: part["fragment_index"])
        assert len(parts) == parts[0]["fragment_count"]
        body = b"".join(base64.b64decode(part["data"]) for part in parts)
        assert hashlib.sha256(body).hexdigest() == digest
        rows.append(json.loads(body))
    return rows


def _source(pattern, version):
    relative = "catalog/records.py" if pattern == "mapping" else "filters.py"
    return (FIXTURES / pattern / version / relative).read_text(), relative


def _case(tmp_path, pattern, version):
    old, relative = _source(pattern, "original")
    new, _ = _source(pattern, version)
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(old)
    if pattern == "mapping":
        (destination.parent / "__init__.py").write_text('"""Catalog."""\n')
        symbol = "lookup_record"
    else:
        symbol = "parse_window"
    (tmp_path / "test_existing.py").write_text(
        f"import pytest\nfrom {relative[:-3].replace('/', '.')} import {symbol}\n\n"
        f"def test_smoke():\n    assert callable({symbol})\n")
    request = impact.PatchRequest(diff(old, new, relative), base="worktree")
    summary = impact.execute_patch(str(tmp_path), request)
    entity = f"{relative}#{symbol}"
    obligation = next(row for row in _complete_obligation_items(tmp_path, request) if row["entity_id"] == entity)
    return request, summary, obligation


def _spec(obligation, pattern, *, argument=None, expected=None, match="subclass"):
    if pattern == "mapping":
        return {"obligation_id": obligation["id"],
                "invocation": {"kind": "mapping-field", "args": ["sample" if argument is None else argument],
                               "kwargs": {}, "key": "status", "operator": "getitem"},
                "expectation": {"basis": "user-designated-baseline-preservation",
                                "reference": "The sample record keeps its active status.",
                                "predicate": "equals", "value": "active" if expected is None else expected},
                "intent": "preservation"}
    return {"obligation_id": obligation["id"],
            "invocation": {"kind": "expected-exception", "args": [-1 if argument is None else argument],
                           "kwargs": {}},
            "expectation": {"basis": "user-supplied-task-requirement",
                            "reference": "Negative windows must raise ValueError.",
                            "predicate": "raises", "value": "ValueError" if expected is None else expected,
                            "match": match}, "intent": "bug-fix"}


def _proposal(tmp_path, request, spec):
    report = impact.execute_patch(str(tmp_path), replace(request, check_spec=spec))
    selected = next(row for row in _complete_obligation_items(tmp_path, replace(request, check_spec=spec))
                    if row["id"] == spec["obligation_id"])
    return report, selected["proposed_check"]


@pytest.mark.parametrize("pattern,version", [
    ("mapping", "compatible"), ("mapping", "violating"), ("mapping", "corrected"),
    ("exception", "compatible"), ("exception", "violating"), ("exception", "corrected")])
def test_unrelated_modules_produce_same_protected_draft(tmp_path, pattern, version):
    request, summary, obligation = _case(tmp_path, pattern, version)
    spec = _spec(obligation, pattern)
    report, proposal = _proposal(tmp_path, request, spec)
    assert summary["schema_version"] == report["schema_version"] == 8
    assert all("proposed_check" not in row for row in impact.execute_patch(
        str(tmp_path), replace(request, section="obligations", page_size=100))["details"]["items"])
    assert summary["summary"]["proposed_checks"][0]["draft_status"] == "unavailable"
    assert proposal["draft_status"] == "review-required"
    assert proposal["review_status"] == "required" and proposal["execution_status"] == "not-run"
    assert proposal["check_specification"]["expectation_source"] == spec["expectation"]["reference"]
    assert "expected value, basis, and exact reference" in proposal["authorship"]["supplied"]
    assert "runtime reachability and behavior" in proposal["authorship"]["unresolved"]
    assert report["summary"]["proposed_checks"][0]["expectation_source"] == spec["expectation"]["reference"]
    assert report["summary"]["proposed_checks"][0]["draft_sha256"] == proposal["draft"]["sha256"]
    source = proposal["draft"]["source"]
    assert hashlib.sha256(source.encode()).hexdigest() == proposal["draft"]["sha256"]
    ast.parse(source)
    if pattern == "mapping":
        assert source == ("from catalog.records import lookup_record\n\n"
                          "def test_proposed_check():\n    result = lookup_record('sample')\n"
                          "    assert result['status'] == 'active'\n")
        assert proposal["check_specification"]["observable"]["operator"] == "getitem"
        assert "return type not established" in proposal["check_specification"]["observable"]["source"]
    else:
        assert source == ("import pytest\nfrom filters import parse_window\n\n"
                          "def test_proposed_check():\n    with pytest.raises(ValueError):\n"
                          "        parse_window(-1)\n")
        assert proposal["check_specification"]["observable"]["match"] == "subclass"
        assert "exception occurrence not established" in proposal["check_specification"]["observable"]["source"]


def test_missing_expectation_and_mapping_none_stay_distinct(tmp_path):
    request, _, obligation = _case(tmp_path, "mapping", "compatible")
    spec = _spec(obligation, "mapping", argument="none-status", expected="unused")
    spec["expectation"]["value"] = None
    _, none_proposal = _proposal(tmp_path, request, spec)
    assert "result['status'] == None" in none_proposal["draft"]["source"]
    assert "result.get" not in none_proposal["draft"]["source"]
    missing = copy.deepcopy(spec)
    missing["invocation"]["args"] = ["missing-status"]
    _, missing_proposal = _proposal(tmp_path, request, missing)
    assert "result['status']" in missing_proposal["draft"]["source"]
    del spec["expectation"]
    _, unresolved = _proposal(tmp_path, request, spec)
    assert unresolved["draft"] is None
    assert unresolved["check_specification"]["expected_result"] is None


def test_exception_exact_type_is_explicit(tmp_path):
    request, _, obligation = _case(tmp_path, "exception", "compatible")
    _, proposal = _proposal(tmp_path, request, _spec(obligation, "exception", match="exact"))
    assert "with pytest.raises(ValueError) as caught:" in proposal["draft"]["source"]
    assert "assert type(caught.value) is ValueError" in proposal["draft"]["source"]


@pytest.mark.parametrize("change,reason", [
    (lambda spec: spec["invocation"].update(operator="get"), "getitem"),
    (lambda spec: spec["invocation"].update(key=""), "key"),
    (lambda spec: spec["expectation"].update(value=[]), "scalar"),
    (lambda spec: spec.update(body="assert True"), "check_spec"),
    (lambda spec: spec["invocation"].update(args=[{"expression": "run()"}]), "scalar"),
])
def test_mapping_rejects_unsupported_or_executable_fields(change, reason):
    spec = _spec({"id": "selected"}, "mapping")
    change(spec)
    with pytest.raises(CheckSpecError, match=reason):
        validate_check_spec(spec)


@pytest.mark.parametrize("change,reason", [
    (lambda spec: spec["expectation"].update(value="Exception"), "specific"),
    (lambda spec: spec["expectation"].update(value="CustomError"), "specific"),
    (lambda spec: spec["expectation"].update(match="message"), "match"),
    (lambda spec: spec["expectation"].update(value=[]), "specific"),
    (lambda spec: spec["expectation"].update(message="negative"), "requires"),
])
def test_exception_rejects_unknown_broad_or_unbounded_expectations(change, reason):
    spec = _spec({"id": "selected"}, "exception")
    change(spec)
    with pytest.raises(CheckSpecError, match=reason):
        validate_check_spec(spec)


def test_legacy_callback_has_explicit_versioned_rejection():
    spec = {"obligation_id": "old", "invocation": {"kind": "ast-boundary", "source_example": "pass"}}
    with pytest.raises(CheckSpecError, match="retired in schema 7"):
        validate_check_spec(spec)


@pytest.mark.parametrize("old_replacement,new_replacement,reason", [
    ("\ndef lookup_record(label):\n    return {}\n", "", "unambiguous"),
    ("\nlookup_record = object()\n", "", "unambiguous"),
    ("", "def lookup_record(label, scope):", "signature"),
    ("", "def lookup_record():", "signature"),
])
def test_ambiguous_or_changed_target_has_no_draft(tmp_path, old_replacement, new_replacement, reason):
    old, relative = _source("mapping", "original")
    new, _ = _source("mapping", "compatible")
    old += old_replacement
    if new_replacement:
        new = new.replace("def lookup_record(label):", new_replacement, 1)
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True)
    destination.write_text(old)
    (destination.parent / "__init__.py").write_text('"""Catalog."""\n')
    (tmp_path / "test_existing.py").write_text(
        "import pytest\nfrom catalog.records import lookup_record\n\ndef test_smoke():\n    assert True\n")
    request = impact.PatchRequest(diff(old, new, relative), base="worktree")
    detail = impact.execute_patch(str(tmp_path), replace(request, section="obligations", page_size=100))
    obligation = next(row for row in detail["details"]["items"] if row["entity_id"] == "catalog/records.py#lookup_record")
    _, proposal = _proposal(tmp_path, request, _spec(obligation, "mapping"))
    assert proposal["draft"] is None
    assert reason in proposal["draft_reason"]


def test_literal_string_is_quoted_not_evaluated_and_unrelated_edit_preserves_meaning(tmp_path):
    request, _, obligation = _case(tmp_path, "mapping", "corrected")
    spec = _spec(obligation, "mapping", argument="__import__('os').system('false')")
    _, proposal = _proposal(tmp_path, request, spec)
    source = proposal["draft"]["source"]
    assert "lookup_record(\"__import__('os').system('false')\")" in source
    assert len([node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]) == 1
    original_spec = _spec(obligation, "mapping")
    _, original_proposal = _proposal(tmp_path, request, original_spec)
    assert "unrelated" not in original_proposal["draft"]["source"]


def test_conflicting_import_evidence_and_wrong_literal_binding_are_rejected(tmp_path):
    request, _, obligation = _case(tmp_path, "mapping", "compatible")
    spec = _spec(obligation, "mapping")
    (tmp_path / "test_existing.py").write_text(
        "import pytest\nfrom catalog.records import lookup_record\n"
        "from shadow import lookup_record\n\ndef test_smoke():\n    assert True\n")
    _, ambiguous = _proposal(tmp_path, request, spec)
    assert ambiguous["draft"] is None
    assert "unambiguous baseline import path" in ambiguous["draft_reason"]
    (tmp_path / "test_existing.py").write_text(
        "import pytest\nfrom catalog.records import lookup_record\n\ndef test_smoke():\n    assert True\n")
    spec["invocation"]["args"] = ["sample", "extra"]
    _, unbound = _proposal(tmp_path, request, spec)
    assert unbound["draft"] is None
    assert "bind unambiguously" in unbound["draft_reason"]


@pytest.mark.parametrize("pattern", ["mapping", "exception"])
def test_pattern_expectation_and_source_changes_invalidate_paged_retrieval(tmp_path, pattern):
    request, _, obligation = _case(tmp_path, pattern, "compatible")
    spec = _spec(obligation, pattern)
    first, proposal = _proposal(tmp_path, request, spec)
    changed = copy.deepcopy(spec)
    changed["expectation"]["reference"] += " Explicitly revised."
    newer, revised = _proposal(tmp_path, request, changed)
    assert newer["report_hash"] != first["report_hash"]
    assert revised["draft"]["source"] == proposal["draft"]["source"]
    assert revised["draft"]["identity"]["expectation_sha256"] != proposal["draft"]["identity"]["expectation_sha256"]
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(request, check_spec=changed, section="obligations",
                                                   cursor=first["retrieval"]["obligations"]["cursor"]))
    relative = "catalog/records.py" if pattern == "mapping" else "filters.py"
    original = (tmp_path / relative).read_text()
    (tmp_path / relative).write_text(original + "\n# Source changed after inspection.\n")
    with pytest.raises(impact.ImpactInputError):
        impact.execute_patch(str(tmp_path), replace(request, check_spec=spec, section="obligations",
                                                   cursor=first["retrieval"]["obligations"]["cursor"]))
