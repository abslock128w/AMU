import pytest

from amu import state
from amu.harness.orchestrator import Orchestrator
from amu.harness.roles import Budget, ToolNotGranted, WriteRefused, checker_decide, make_role

RED = {"violations": [{"kind": "PHASE_DRIFT", "symbol": "a.py", "detail": "d", "severity": "ERROR"}]}
RETRYABLE = {"violations": [{"kind": "TEST_FAILURE", "symbol": "tests", "detail": "d", "severity": "ERROR"}]}


def test_role_cannot_call_ungranted_tool():
    with pytest.raises(ToolNotGranted):
        make_role("sweeper").call("files.write_allowed", lambda: None)


def test_builder_write_guard(tmp_path):
    r = make_role("builder", {"a.py"})
    with pytest.raises(WriteRefused):
        r.write_path("b.py", "x", str(tmp_path))
    assert r.write_path("a.py", "x", str(tmp_path)).read_text() == "x"


def test_builder_refuses_an_unknown_file_even_when_its_path_is_in_scope(tmp_path):
    r = make_role("builder", {"unknown.py"}, {"unknown.py"})

    with pytest.raises(WriteRefused, match="requires a human answer"):
        r.write_path("unknown.py", "x", str(tmp_path))

    assert not (tmp_path / "unknown.py").exists()


def test_builder_refuses_an_allowed_symlink_that_leads_outside_the_repo(tmp_path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("original\n")
    (tmp_path / "escape.py").symlink_to(outside)
    r = make_role("builder", {"escape.py"})

    with pytest.raises(WriteRefused, match="outside the repository"):
        r.write_path("escape.py", "changed\n", str(tmp_path))

    assert outside.read_text() == "original\n"


def test_builder_refuses_an_allowed_symlink_to_a_different_in_repo_file(tmp_path):
    protected = tmp_path / "protected.py"
    protected.write_text("original\n")
    (tmp_path / "allowed.py").symlink_to(protected)
    r = make_role("builder", {"allowed.py"})

    with pytest.raises(WriteRefused, match="different file"):
        r.write_path("allowed.py", "changed\n", str(tmp_path))

    assert protected.read_text() == "original\n"


def test_checker_cannot_downgrade_blocking():
    assert checker_decide(RED, Budget())["decision"] != "proceed"


def test_third_retry_escalates():
    b = Budget()
    decisions = [checker_decide(RETRYABLE, b)["decision"] for _ in range(3)]
    assert decisions[-1] == "escalate" and checker_decide(RETRYABLE, b)["escalate"] is True


@pytest.mark.parametrize("kind,severity", [("FROZEN_SIGNATURE", "ERROR"), ("UNDECLARED_REMOVAL", "ERROR"), ("UNKNOWN_TOUCHED", "ERROR")])
def test_checker_escalates_protected_contract_and_unknown_nodes(kind, severity):
    result = {"violations": [{"kind": kind, "symbol": "src.py#work", "detail": "fixture", "severity": severity}]}

    decision = checker_decide(result, Budget())

    assert decision["decision"] == "escalate" and decision["escalate"] is True


def test_checker_budget_persists_across_cli_style_orchestrator_instances(tmp_path):
    drift = {"violations": [{"kind": "PHASE_DRIFT", "symbol": "src.py", "detail": "fixture", "severity": "ERROR"}]}

    decisions = [Orchestrator(str(tmp_path), task="map-hash").on_check(drift)["decision"] for _ in range(5)]

    assert decisions == ["widen", "widen", "retry", "retry", "escalate"]
    budget = state.read_json("run/budget.json", str(tmp_path))
    assert budget["task"] == "map-hash"
    assert budget["used"] == {"retry": 2, "widen": 2, "sweep": 0}
    assert len(list((tmp_path / ".amu" / "run").glob("checker-*.json"))) == 5
