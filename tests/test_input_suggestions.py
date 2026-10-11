"""Static probe derivation, abstention, selection, and retrieval invariants."""

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from amu.change_analysis import analyze_python_change
from amu.services import impact
from tests.test_patch_actionable import diff

CASES = json.loads((Path(__file__).parent / "fixtures" / "input_suggestions_v8.json").read_text())["cases"]


def _identity(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_predeclared_probe_classes_and_abstentions(case):
    analysis = analyze_python_change("sample.py", case["before"], case["after"])
    suggestions = [probe for row in analysis["obligations"] for probe in row.get("input_suggestions", [])]
    if case["family"] is None:
        assert suggestions == []
        return
    assert suggestions and {probe["family"] for probe in suggestions} == {case["family"]}
    assert all(probe["expected_result"] == "unknown" for probe in suggestions)
    assert all(probe["reachability"]["status"] == "unestablished" for probe in suggestions)
    assert all(probe["source_spans"][phase]["ast_hash"] for probe in suggestions for phase in ("before", "after"))
    if "required_distinguishing" in case:
        actual = {_identity(probe["value"]) for probe in suggestions if probe["distinguishes_expressions"]}
        assert {_identity(value) for value in case["required_distinguishing"]} <= actual
        assert {_identity(value) for value in case.get("required_adjacent", [])} <= {
            _identity(probe["value"]) for probe in suggestions}
        assert not ({_identity(value) for value in case.get("forbidden_values", [])} & {
            _identity(probe["value"]) for probe in suggestions})
    if "required_key_states" in case:
        assert set(case["required_key_states"]) <= {probe["key_state"] for probe in suggestions}
        assert all(probe["invocation"] is None for probe in suggestions)
    if "status" in case:
        assert {probe["probe_status"] for probe in suggestions} == {case["status"]}
    if case.get("reachability"):
        assert all(probe["reachability"]["reason"] == "earlier-return" for probe in suggestions)


def _items(repo, request):
    cursor = None
    entries = []
    while True:
        packet = impact.execute_patch(str(repo), replace(request, section="obligations", cursor=cursor, page_size=100))
        entries.extend(packet["details"]["items"])
        cursor = packet["page"]["next_cursor"]
        if cursor is None:
            break
    complete = []
    fragments = {}
    for entry in entries:
        if "evidence_fragment" not in entry:
            complete.append(entry)
            continue
        fragment = entry["evidence_fragment"]
        fragments.setdefault(fragment["item_sha256"], []).append(fragment)
    for digest, parts in fragments.items():
        parts.sort(key=lambda part: part["fragment_index"])
        assert len(parts) == parts[0]["fragment_count"]
        body = b"".join(base64.b64decode(part["data"]) for part in parts)
        assert hashlib.sha256(body).hexdigest() == digest
        complete.append(json.loads(body))
    return complete, packet


def _repo(tmp_path, before, after, symbol):
    (tmp_path / "sample.py").write_text(before)
    (tmp_path / "test_existing.py").write_text(
        f"import pytest\nfrom sample import {symbol}\n\ndef test_smoke():\n    assert callable({symbol})\n")
    return impact.PatchRequest(diff(before, after, "sample.py"), base="worktree")


def test_selected_probe_reaches_existing_draft_without_retyping_input(tmp_path):
    case = next(row for row in CASES if row["name"] == "inclusive-boundary")
    request = _repo(tmp_path, case["before"], case["after"], "gate")
    summary = impact.execute_patch(str(tmp_path), request)
    assert summary["schema_version"] == 8
    assert summary["summary"]["input_suggestion_counts"]["retained"] >= 3
    preview = summary["summary"]["input_suggestions"][0]
    assert preview["value"] == 10 and preview["details_section"] == "obligations"
    obligation = next(row for row in _items(tmp_path, request)[0] if row["id"] == preview["obligation_id"])
    chosen = next(row for row in obligation["input_suggestions"] if row["id"] == preview["id"])
    assert chosen["invocation"] == {"args": [], "kwargs": {"n": 10}}
    assert chosen["local_observation"]["before"] is False
    assert chosen["local_observation"]["after"] is True
    specification = {"obligation_id": obligation["id"], "suggestion_id": chosen["id"],
                     "invocation": {"kind": "direct-return"},
                     "expectation": {"basis": "user-supplied-task-requirement",
                                     "reference": "A window of ten is included in the accepted range.",
                                     "predicate": "equals", "value": True},
                     "intent": "bug-fix"}
    selected_request = replace(request, check_spec=specification)
    selected = next(row for row in _items(tmp_path, selected_request)[0] if row["id"] == obligation["id"])
    proposal = selected["proposed_check"]
    assert proposal["selected_input_suggestion"]["id"] == chosen["id"]
    assert proposal["draft_status"] == "review-required" and proposal["execution_status"] == "not-run"
    assert proposal["draft"]["source"].endswith("assert gate(n=10) == True\n")
    assert proposal["draft"]["identity"]["suggestion_id"] == chosen["id"]
    assert proposal["check_specification"]["expectation_source"] == specification["expectation"]["reference"]
    changed = replace(request, patch=diff(case["before"], case["after"].replace("<= 10", "<= 11"), "sample.py"),
                      check_spec=specification)
    with pytest.raises(impact.ImpactInputError, match="does not identify|stale"):
        impact.execute_patch(str(tmp_path), changed)
    altered = replace(selected_request, check_spec={**specification,
                      "expectation": {**specification["expectation"], "value": False}})
    page = impact.execute_patch(str(tmp_path), replace(selected_request, section="obligations", page_size=1))
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(altered, section="obligations", page_size=1,
                                                  cursor=page["page"]["next_cursor"]))


def test_mapping_probe_remains_partial_and_cannot_select_a_draft(tmp_path):
    case = next(row for row in CASES if row["name"] == "mapping-missing-vs-none")
    request = _repo(tmp_path, case["before"], case["after"], "lookup")
    obligation = next(row for row in _items(tmp_path, request)[0] if row.get("input_suggestions"))
    absent = next(row for row in obligation["input_suggestions"] if row["key_state"] == "absent")
    present_none = next(row for row in obligation["input_suggestions"] if row["key_state"] == "present-none")
    assert absent["local_observation"]["before"] == "raises KeyError"
    assert absent["local_observation"]["after"] == "returns None"
    assert present_none["distinguishes_expressions"] is False
    selected = {"obligation_id": obligation["id"], "suggestion_id": absent["id"],
                "invocation": {"kind": "direct-return"},
                "expectation": {"basis": "user-supplied-task-requirement", "reference": "Missing item returns None.",
                                "predicate": "equals", "value": None}}
    with pytest.raises(impact.ImpactInputError, match="not a complete scalar"):
        impact.execute_patch(str(tmp_path), replace(request, check_spec=selected))


def test_unrelated_patch_change_invalidates_selected_suggestion(tmp_path):
    case = next(row for row in CASES if row["name"] == "inclusive-boundary")
    request = _repo(tmp_path, case["before"], case["after"], "gate")
    (tmp_path / "README.md").write_text("# Before\n")
    preview = impact.execute_patch(str(tmp_path), request)["summary"]["input_suggestions"][0]
    specification = {"obligation_id": preview["obligation_id"], "suggestion_id": preview["id"],
                     "invocation": {"kind": "direct-return"}}
    changed = replace(request, patch=request.patch + diff("# Before\n", "# After\n", "README.md"),
                      check_spec=specification)
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), changed)


def test_configuration_change_invalidates_selected_suggestion(tmp_path):
    case = next(row for row in CASES if row["name"] == "inclusive-boundary")
    request = _repo(tmp_path, case["before"], case["after"], "gate")
    configuration = tmp_path / "pyproject.toml"
    configuration.write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    preview = impact.execute_patch(str(tmp_path), request)["summary"]["input_suggestions"][0]
    specification = {"obligation_id": preview["obligation_id"], "suggestion_id": preview["id"],
                     "invocation": {"kind": "direct-return"}}
    configuration.write_text('[tool.pytest.ini_options]\ntestpaths = ["tests", "checks"]\n')
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(request, check_spec=specification))


def test_report_budget_keeps_complete_retrieval_and_counts(tmp_path):
    old = "".join(f"def gate_{index}(n: int):\n    return n < 3\n\n" for index in range(12))
    new = "".join(f"def gate_{index}(n: int):\n    return n < 5\n\n" for index in range(12))
    request = _repo(tmp_path, old, new, "gate_0")
    summary = impact.execute_patch(str(tmp_path), request)
    counts = summary["summary"]["input_suggestion_counts"]
    assert counts == {"retained": 40, "omitted": 20, "per_report_limit": 40}
    rows, packet = _items(tmp_path, request)
    assert sum(len(row.get("input_suggestions", [])) for row in rows) == 40
    assert sum(row.get("omitted_input_suggestions", 0) for row in rows) == 20
    assert packet["report_hash"] == summary["report_hash"]


def test_suggestion_bound_spec_rejects_literal_argument_override():
    from amu.check_drafts import CheckSpecError, validate_check_spec

    spec = {"obligation_id": "sample.py#gate:obligation:123", "suggestion_id": "sample.py#gate:obligation:123:input:456",
            "invocation": {"kind": "direct-return", "args": [999], "kwargs": {}}}
    with pytest.raises(CheckSpecError, match="only kind"):
        validate_check_spec(spec)


def test_declared_mapping_scalar_is_only_a_partial_type_intent_probe():
    before = "def lookup(data: dict[str, int]):\n    return data['item']\n"
    after = "def lookup(data: dict[str, int]):\n    return data.get('item')\n"
    probes = [probe for row in analyze_python_change("lookup.py", before, after)["obligations"]
              for probe in row.get("input_suggestions", [])]
    scalar = next(probe for probe in probes if probe["key_state"] == "present-scalar")
    assert scalar["value"] == {"item": 1}
    assert scalar["applicability"] == "declared-type-intent"
    assert scalar["probe_status"] == "partial-invocation" and scalar["invocation"] is None
    assert scalar["expected_result"] == "unknown"


def test_boolean_parameter_and_module_rebinding_abstain_while_method_stays_expression_level():
    before = "def gate(n: bool):\n    return n < 1\n"
    after = "def gate(n: bool):\n    return n <= 1\n"
    assert not any(row.get("input_suggestions") for row in analyze_python_change("gate.py", before, after)["obligations"])
    before = "def gate(n):\n    return n < 1\n\ngate = object()\n"
    after = "def gate(n):\n    return n <= 1\n\ngate = object()\n"
    probes = [probe for row in analyze_python_change("gate.py", before, after)["obligations"]
              for probe in row.get("input_suggestions", [])]
    assert probes == []
    before = "class Panel:\n    def gate(self, n):\n        return n < 1\n"
    after = "class Panel:\n    def gate(self, n):\n        return n <= 1\n"
    probes = [probe for row in analyze_python_change("gate.py", before, after)["obligations"]
              for probe in row.get("input_suggestions", [])]
    assert probes and all(probe["probe_status"] == "expression-probe" for probe in probes)
    assert all(probe["invocation"] is None for probe in probes)


def test_per_obligation_budget_counts_omitted_neighbors():
    before = "def gate(n: int):\n    return n < -10\n"
    after = "def gate(n: int):\n    return n < 10\n"
    obligation = next(row for row in analyze_python_change("gate.py", before, after)["obligations"]
                      if row.get("input_suggestions"))
    assert len(obligation["input_suggestions"]) == 5
    assert obligation["omitted_input_suggestions"] == 1
    assert sum(probe["distinguishes_expressions"] for probe in obligation["input_suggestions"]) == 3


def test_nonparameter_name_is_not_promoted_to_input_and_literal_defaults_bind():
    before = "limit = 10\ndef gate(n):\n    return limit < 3\n"
    after = "limit = 10\ndef gate(n):\n    return limit <= 3\n"
    assert not any(row.get("input_suggestions") for row in analyze_python_change("gate.py", before, after)["obligations"])
    before = "def gate(n, offset=-1, scale=1.5):\n    return n < 3\n"
    after = "def gate(n, offset=-1, scale=1.5):\n    return n <= 3\n"
    probes = [probe for row in analyze_python_change("gate.py", before, after)["obligations"]
              for probe in row.get("input_suggestions", [])]
    assert probes and all(probe["probe_status"] == "complete-source-bound-invocation" for probe in probes)


def test_returned_scalar_is_not_a_truthiness_condition():
    before = "def choose(x):\n    return x is None\n"
    after = "def choose(x):\n    return x\n"
    assert not any(row.get("input_suggestions") for row in analyze_python_change("choose.py", before, after)["obligations"])


def test_long_signature_preview_preserves_full_signature_identity():
    import ast

    signature = "n, " + ", ".join(f"option_{i}=1" for i in range(40))
    before = f"def gate({signature}):\n    return n < 3\n"
    after = f"def gate({signature}):\n    return n <= 3\n"
    probe = next(probe for row in analyze_python_change("gate.py", before, after)["obligations"]
                 for probe in row.get("input_suggestions", []))
    for phase, source in (("before", before), ("after", after)):
        text = ast.unparse(ast.parse(source).body[0].args)
        identity = probe["binding"]["signature_details"][phase]
        assert identity == {"truncated": True, "sha256": hashlib.sha256(text.encode()).hexdigest()}
