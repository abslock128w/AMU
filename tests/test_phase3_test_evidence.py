"""Bounded baseline test behavior evidence stays separate from runtime coverage."""

import ast

from amu.change_analysis import analyze_python_change
from amu.plan import patch_test_recommendations


def _recommend(before, after, tests, obligations, path="calc.py"):
    analysis = analyze_python_change(path, before, after)
    rows = patch_test_recommendations(
        [{"path": "tests/test_calc.py", "tree": ast.parse(tests), "sha256": "a" * 64}],
        {path: {item["entity_id"].split("#", 1)[1] for item in obligations}},
        declarations=analysis["recommendation_entities"],
        state_dependencies=analysis["state_dependencies"],
        source_trees={path: {"tree": ast.parse(before), "sha256": "b" * 64}},
        obligations=obligations,
    )
    return {row["test_id"].split("::")[-1]: row for row in rows}


def _boundary(entity_id="calc.py#score"):
    return {"id": "boundary", "entity_id": entity_id, "category": "branch",
            "condition": {"kind": "comparison-boundary", "parameter": "value",
                          "before_operator": "<", "after_operator": "<=",
                          "boundary": 0, "target_value": 0},
            "observable": {"kind": "return", "detail": "returned value"}}


def _evidence(row, entity_id="calc.py#score"):
    association = next(item for item in row["associations"] if item["entity_id"] == entity_id)
    return association["obligation_evidence"][0]


def test_equality_boundary_input_and_result_assertion_outrank_broad_reference():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    after = before.replace("value < 0", "value <= 0")
    tests = """from calc import score

def test_equal():
    result = score(0)
    assert result == 2

def test_positive():
    assert score(3) == 2

def test_call_only():
    score(0)

def test_reference_only():
    assert callable(score)
"""
    rows = _recommend(before, after, tests, [_boundary()])
    assert set(rows) == {"test_equal", "test_positive", "test_call_only", "test_reference_only"}
    assert rows["test_equal"]["behavioral_rank"] == 0
    assert _evidence(rows["test_equal"])["input_evidence"]["condition"] == "value == 0"
    assert _evidence(rows["test_equal"])["assertion_evidence"]["observes"] == "result local result"
    assert _evidence(rows["test_positive"])["level"] == "assertion"
    assert _evidence(rows["test_call_only"])["level"] == "input"
    assert _evidence(rows["test_reference_only"])["level"] == "broad"
    assert all(row["coverage_proven"] is False and row["test_source_phase"] == "before" for row in rows.values())


def test_literal_parameter_cases_and_dynamic_case_abstention():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = """import pytest
from calc import score

@pytest.mark.parametrize("value", [-1, 0, 1])
def test_cases(value):
    assert score(value) in (1, 2)

@pytest.mark.parametrize("value", build_cases())
def test_dynamic(value):
    assert score(value) in (1, 2)
"""
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert _evidence(rows["test_cases"])["level"] == "input-and-assertion"
    assert _evidence(rows["test_cases"])["input_evidence"]["parameter_case"] == 1
    assert _evidence(rows["test_dynamic"])["level"] == "assertion"


def test_exception_check_observes_raised_result_without_claiming_input_match():
    before = "def parse(value):\n    raise ValueError('bad')\n"
    after = before.replace("ValueError", "TypeError")
    obligation = {"id": "exception", "entity_id": "calc.py#parse", "category": "exception",
                  "condition": {"kind": "exception-handler", "exception": "ValueError"},
                  "observable": {"kind": "exception", "detail": "raised exception"}}
    tests = """import pytest
from calc import parse

def test_raises():
    with pytest.raises(ValueError):
        parse("bad")
"""
    rows = _recommend(before, after, tests, [obligation])
    evidence = _evidence(rows["test_raises"], "calc.py#parse")
    assert evidence["level"] == "assertion"
    assert evidence["assertion_evidence"]["expression"] == "pytest.raises(ValueError)"


def test_constructed_instance_method_and_self_state_are_distinct_from_sibling():
    before = """class Quota:
    ceiling = 8
    floor = 2

    def upper(self):
        return self.ceiling

    def lower(self):
        return self.floor
"""
    after = before.replace("ceiling = 8", "ceiling = 9")
    obligation = {"id": "state", "entity_id": "calc.py#Quota.ceiling", "category": "state_declaration",
                  "condition": None, "observable": {"kind": "state", "detail": "ceiling"}}
    tests = """from calc import Quota

def test_upper():
    quota = Quota()
    assert quota.upper() == 8

def test_lower():
    quota = Quota()
    assert quota.lower() == 2

def test_dynamic(factory):
    quota = factory()
    assert quota.upper() == 8
"""
    rows = _recommend(before, after, tests, [obligation])
    upper = rows["test_upper"]
    state_paths = [item for item in upper["associations"] if item["entity_id"] == "calc.py#Quota.ceiling"]
    assert len(state_paths) == 1
    assert state_paths[0]["relationship"] == "constructed-instance-state-path"
    assert state_paths[0]["path"][-1]["consumer_id"] == "calc.py#Quota.upper"
    assert _evidence(upper, "calc.py#Quota.ceiling")["level"] == "assertion"
    assert not any(item["entity_id"] == "calc.py#Quota.ceiling" for item in rows["test_lower"]["associations"])
    assert "test_dynamic" not in rows


def test_input_and_assertion_must_belong_to_the_same_call():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = "from calc import score\n\ndef test_split():\n    score(0)\n    assert score(3) == 2\n"
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert _evidence(rows["test_split"])["level"] != "input-and-assertion"


def test_actual_exception_obligation_connects_to_pytest_check():
    before = "def parse(value):\n    raise ValueError('bad')\n"
    after = before.replace("ValueError", "TypeError")
    analysis = analyze_python_change("calc.py", before, after)
    tests = "import pytest\nfrom calc import parse\n\ndef test_raises():\n    with pytest.raises(ValueError):\n        parse('bad')\n"
    rows = _recommend(before, after, tests, analysis["obligations"])
    assert _evidence(rows["test_raises"], "calc.py#parse")["level"] == "assertion"


def test_changed_guarded_return_matches_only_supported_input_path():
    before = "def score(value):\n    if value < 0:\n        return None\n    return value\n"
    after = before.replace("return None", "return []")
    analysis = analyze_python_change("calc.py", before, after)
    tests = "from calc import score\n\ndef test_negative():\n    assert score(-1) is None\n\ndef test_positive():\n    assert score(2) == 2\n"
    rows = _recommend(before, after, tests, analysis["obligations"])
    assert _evidence(rows["test_negative"])["level"] == "input-and-assertion"
    assert _evidence(rows["test_positive"])["level"] == "assertion"


def test_parameter_rebinding_indirection_and_starred_arguments_abstain():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = """import pytest
from calc import score

@pytest.mark.parametrize('value', [0])
def test_rebound(value):
    value = 3
    assert score(value) == 2

@pytest.mark.parametrize('value', [0], indirect=True)
def test_indirect(value):
    assert score(value) == 2

@pytest.mark.parametrize('value', [(0,)])
def test_tuple_case(value):
    assert score(value) == 2

def test_starred(args):
    assert score(*args, 0) == 2
"""
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert all(_evidence(row)["level"] != "input-and-assertion" for row in rows.values())


def test_discarded_result_and_mock_decorator_do_not_prove_observation():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = """from calc import score
from unittest.mock import patch

def test_discarded():
    assert (score(0), True)[1]

@patch('calc.score', return_value=2)
def test_mocked(mock_score):
    assert score(0) == 2
"""
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert _evidence(rows["test_discarded"])["level"] == "input"
    assert _evidence(rows["test_mocked"])["level"] == "unresolved"


def test_method_shadowing_keeps_only_broad_class_candidate():
    before = "class Quota:\n    def __init__(self):\n        self.score = lambda value: 2\n    def score(self, value):\n        return 1 if value < 0 else 2\n"
    tests = "from calc import Quota\n\ndef test_shadow():\n    quota = Quota()\n    assert quota.score(0) == 2\n"
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary("calc.py#Quota.score")])
    assert not any(item["entity_id"] == "calc.py#Quota.score" and item["resolution"] == "resolved"
                   for item in rows["test_shadow"]["associations"])


def test_unbound_method_arguments_keep_explicit_receiver():
    before = "class Quota:\n    def score(self, value, *rest):\n        return 1 if value < 0 else 2\n"
    tests = "from calc import Quota\n\ndef test_unbound():\n    assert Quota.score(0, 3) == 2\n"
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary("calc.py#Quota.score")])
    assert _evidence(rows["test_unbound"], "calc.py#Quota.score")["input_evidence"] is None


def test_test_assertion_text_preserves_redaction():
    import json

    token = "sk-syntheticReviewValue12345"
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = f"from calc import score\n\ndef test_secret():\n    assert score(0) == '{token}'\n"
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert token not in json.dumps(rows)


def test_result_rebinding_and_imported_member_override_abstain():
    before = "def score(value):\n    return 1 if value < 0 else 2\n"
    tests = """import calc
from calc import score

def test_rebound_result():
    result = score(0)
    def result():
        return 3
    assert result is not None

def test_module_override():
    calc.score = lambda value: 2
    assert calc.score(0) == 2
"""
    rows = _recommend(before, before.replace("value < 0", "value <= 0"), tests, [_boundary()])
    assert _evidence(rows["test_rebound_result"])["assertion_evidence"] is None
    assert _evidence(rows["test_module_override"])["level"] == "broad"


def test_omitted_default_argument_ranks_ahead_of_explicit_argument():
    before = "def greeting(name, prefix='Hello'):\n    return prefix + ', ' + name\n"
    after = before.replace("'Hello'", "'Welcome'")
    analysis = analyze_python_change("calc.py", before, after)
    tests = "from calc import greeting\n\ndef test_omitted():\n    assert greeting('Ada') == 'Hello, Ada'\n\ndef test_explicit():\n    assert greeting('Ada', 'Hi') == 'Hi, Ada'\n"
    rows = _recommend(before, after, tests, analysis["obligations"])
    assert _evidence(rows["test_omitted"], "calc.py#greeting")["level"] == "input-and-assertion"
    assert _evidence(rows["test_explicit"], "calc.py#greeting")["level"] == "assertion"
