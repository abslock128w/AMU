"""A check draft needs an explicit expectation and a source-grounded invocation."""

import ast
import hashlib
from dataclasses import replace

import pytest

from amu.services import impact
from tests.test_patch_actionable import diff


def _case(tmp_path, *, old_result="value + 1", new_result="value + 2", tests=None):
    old = f"def calculate(value):\n    return {old_result}\n\ndef unrelated(value):\n    return value\n"
    new = old.replace(old_result, new_result)
    (tmp_path / "core.py").write_text(old)
    if tests is not None:
        (tmp_path / "test_core.py").write_text(tests)
    request = impact.PatchRequest(diff(old, new, "core.py"), base="worktree")
    first = impact.execute_patch(str(tmp_path), request)
    assert first["assessment"]["executed"] is False
    detail = impact.execute_patch(str(tmp_path), replace(request, section="obligations", page_size=100))
    obligation = next(row for row in detail["details"]["items"] if row.get("entity_id") == "core.py#calculate")
    return request, obligation


def _spec(obligation_id, *, value, basis="user-supplied-task-requirement", args=None):
    return {"obligation_id": obligation_id,
            "invocation": {"kind": "direct-return", "args": [1] if args is None else args, "kwargs": {}},
            "expectation": {"basis": basis, "reference": "The explicitly designated result for calculate(1).",
                            "predicate": "equals", "value": value},
            "intent": "bug-fix" if basis == "user-supplied-task-requirement" else "preservation"}


def _proposal(tmp_path, request, spec):
    report = impact.execute_patch(str(tmp_path), replace(request, check_spec=spec))
    detail = impact.execute_patch(str(tmp_path), replace(request, section="obligations", page_size=100,
                                                    check_spec=spec))
    proposal = next(row["proposed_check"] for row in detail["details"]["items"]
                    if row.get("id") == spec["obligation_id"])
    return report, proposal


def test_explicit_expectation_produces_reviewable_direct_return_draft(tmp_path):
    tests = "import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert callable(calculate)\n"
    request, obligation = _case(tmp_path, tests=tests)
    spec = _spec(obligation["id"], value=3)
    report, proposal = _proposal(tmp_path, request, spec)
    assert proposal["evidence_gap"]["status"] == "no-matching-assertion-established"
    assert proposal["evidence_gap"]["inventory_complete"] is True
    assert proposal["check_specification"]["input"] == spec["invocation"]
    assert proposal["check_specification"]["expected_result"] == 3
    assert proposal["check_specification"]["expectation_source"] == spec["expectation"]["reference"]
    assert proposal["check_specification"]["import_evidence"] == "test_core.py"
    assert proposal["draft_status"] == "review-required"
    assert proposal["review_status"] == "required"
    assert proposal["execution_status"] == "not-run"
    source = proposal["draft"]["source"]
    assert source == "from core import calculate\n\ndef test_proposed_check():\n    assert calculate(1) == 3\n"
    assert hashlib.sha256(source.encode()).hexdigest() == proposal["draft"]["sha256"]
    ast.parse(source)
    assert report["summary"]["proposed_checks"][0]["draft_sha256"] == proposal["draft"]["sha256"]
    assert report["request"]["check_spec_sha256"]
    assert "check_spec" not in report["request"]


def test_unresolved_expectation_never_becomes_an_assertion(tmp_path):
    request, obligation = _case(tmp_path, tests="import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert True\n")
    spec = _spec(obligation["id"], value=3)
    del spec["expectation"]
    _, proposal = _proposal(tmp_path, request, spec)
    assert proposal["check_specification"]["expected_result"] is None
    assert proposal["check_specification"]["expectation_basis"] == "unresolved"
    assert proposal["draft"] is None
    assert proposal["draft_status"] == "unavailable"
    assert "unresolved" in proposal["draft_reason"]


def test_bug_fix_does_not_preserve_baseline_result_and_protected_result_can_be_violated(tmp_path):
    tests = "import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert callable(calculate)\n"
    request, obligation = _case(tmp_path, tests=tests)
    bug_fix = _spec(obligation["id"], value=3)
    _, fixed = _proposal(tmp_path, request, bug_fix)
    preserved = _spec(obligation["id"], value=2, basis="user-designated-baseline-preservation")
    _, protected = _proposal(tmp_path, request, preserved)
    assert "calculate(1) == 3" in fixed["draft"]["source"]
    assert "calculate(1) == 2" in protected["draft"]["source"]
    assert fixed["draft"]["identity"]["expectation_sha256"] != protected["draft"]["identity"]["expectation_sha256"]
    assert protected["check_specification"]["expectation_basis"] == "user-designated-baseline-preservation"
    assert fixed["execution_status"] == protected["execution_status"] == "not-run"


def test_unrelated_assertion_does_not_establish_the_missing_check(tmp_path):
    tests = ("import pytest\nfrom core import calculate, unrelated\n\n"
             "def test_other():\n    a = calculate(1)\n    b = unrelated(9)\n    assert b == 9\n")
    request, obligation = _case(tmp_path, tests=tests)
    spec = _spec(obligation["id"], value=3)
    _, proposal = _proposal(tmp_path, request, spec)
    assert proposal["evidence_gap"]["status"] == "no-matching-assertion-established"
    assert proposal["draft_status"] == "review-required"
    assert proposal["draft"]["source"].count("calculate(1)") == 1
    assert "unrelated" not in proposal["draft"]["source"]


def test_missing_import_or_pytest_convention_prevents_runnable_draft(tmp_path):
    request, obligation = _case(tmp_path)
    spec = _spec(obligation["id"], value=3)
    _, proposal = _proposal(tmp_path, request, spec)
    assert proposal["draft"] is None
    assert "import path" in proposal["draft_reason"]
    (tmp_path / "test_core.py").write_text("from core import calculate\n\ndef test_smoke():\n    assert True\n")
    _, proposal = _proposal(tmp_path, request, spec)
    assert proposal["draft"] is None
    assert "pytest" in proposal["draft_reason"]


def test_source_and_expectation_changes_invalidate_draft_and_cursor(tmp_path):
    tests = "import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert True\n"
    request, obligation = _case(tmp_path, tests=tests)
    spec = _spec(obligation["id"], value=3)
    first, proposal = _proposal(tmp_path, request, spec)
    changed_spec = _spec(obligation["id"], value=4)
    changed, altered = _proposal(tmp_path, request, changed_spec)
    assert first["report_hash"] != changed["report_hash"]
    assert proposal["draft"]["sha256"] != altered["draft"]["sha256"]
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(request, section="obligations", check_spec=changed_spec,
            cursor=first["retrieval"]["obligations"]["cursor"]))
    old = (tmp_path / "core.py").read_text()
    source = old.replace("return value + 1", "return value + 10")
    (tmp_path / "core.py").write_text(source)
    revised = replace(request, patch=diff(source, source.replace("+ 10", "+ 2"), "core.py"))
    revised_detail = impact.execute_patch(str(tmp_path), replace(revised, section="obligations", page_size=100))
    revised_id = next(row["id"] for row in revised_detail["details"]["items"]
                      if row.get("entity_id") == "core.py#calculate")
    revised = replace(revised, check_spec=_spec(revised_id, value=3))
    newer = impact.execute_patch(str(tmp_path), revised)
    assert first["report_hash"] != newer["report_hash"]
    _, revised_proposal = _proposal(tmp_path, replace(revised, check_spec=None), revised.check_spec)
    assert proposal["draft"]["identity"]["source"] != revised_proposal["draft"]["identity"]["source"]
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(revised, section="obligations",
            cursor=first["retrieval"]["obligations"]["cursor"]))


def test_inspection_does_not_import_execute_or_write_target(tmp_path):
    old = "raise RuntimeError('imported')\ndef calculate(value):\n    return value + 1\n"
    new = old.replace("+ 1", "+ 2")
    (tmp_path / "core.py").write_text(old)
    (tmp_path / "test_core.py").write_text("import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert True\n")
    request = impact.PatchRequest(diff(old, new, "core.py"), base="worktree")
    report = impact.execute_patch(str(tmp_path), request)
    assert report["assessment"]["executed"] is False
    assert (tmp_path / "core.py").read_text() == old
    assert sorted(path.name for path in tmp_path.iterdir()) == ["core.py", "test_core.py"]


def test_complete_retrieval_contains_specification_and_identical_draft(tmp_path):
    tests = "import pytest\nfrom core import calculate\n\ndef test_smoke():\n    assert True\n"
    request, obligation = _case(tmp_path, tests=tests)
    spec = _spec(obligation["id"], value=3)
    summary, proposal = _proposal(tmp_path, request, spec)
    seen = []
    cursor = None
    while True:
        page = impact.execute_patch(str(tmp_path), replace(request, check_spec=spec, section="obligations",
            cursor=cursor, page_size=1))
        assert page["report_hash"] == summary["report_hash"]
        seen.extend(page["details"]["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == summary["retrieval"]["obligations"]["logical_items"]
    assert next(row["proposed_check"] for row in seen if row["id"] == spec["obligation_id"]) == proposal


def test_check_spec_is_bounded_data_before_repo_access(tmp_path):
    request = impact.PatchRequest("not a patch", base="worktree")
    invalid = {"obligation_id": "x", "invocation": {"kind": "direct-return", "args": [object()], "kwargs": {}}}
    with pytest.raises(impact.ImpactInputError, match="check_spec"):
        impact.execute_patch(str(tmp_path / "missing"), replace(request, check_spec=invalid))
