import hashlib
import json
import multiprocessing

import pytest

from amu import state
from amu.harness.orchestrator import Orchestrator
from amu.harness.roles import Budget

RETRYABLE = {"violations": [{"kind": "TEST_FAILURE", "symbol": "tests", "severity": "ERROR"}]}


def _concurrent_handoffs(repo, barrier, output):
    try:
        orchestrator = Orchestrator(repo, task="parallel")
        barrier.wait(timeout=15)
        for index in range(4):
            orchestrator.handoff("planner", {"decision": "proceed", "evidence": [f"{multiprocessing.current_process().name}:{index}"]})
        output.put(None)
    except BaseException as exc:
        output.put(repr(exc))


def _concurrent_checks(repo, barrier, output):
    try:
        orchestrator = Orchestrator(repo, task="parallel")
        barrier.wait(timeout=15)
        output.put(orchestrator.on_check(RETRYABLE)["decision"])
    except BaseException as exc:
        output.put(repr(exc))


def _run_processes(tmp_path, worker, count=4):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(count)
    output = context.Queue()
    processes = [context.Process(target=worker, args=(str(tmp_path), barrier, output)) for _ in range(count)]
    for process in processes:
        process.start()
    try:
        results = [output.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        return results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        output.close()


def test_stale_orchestrators_never_overwrite_handoffs(tmp_path):
    first = Orchestrator(str(tmp_path))
    stale = Orchestrator(str(tmp_path))
    first.handoff("planner", {"decision": "proceed", "evidence": ["first"]})
    stale.handoff("planner", {"decision": "ask", "evidence": ["second"]})
    files = sorted((tmp_path / ".amu/run").glob("planner-*.json"))
    assert len(files) == 2
    assert [json.loads(path.read_text())["evidence"] for path in files] == [["first"], ["second"]]


def test_handoff_number_is_shared_across_roles(tmp_path):
    first = Orchestrator(str(tmp_path))
    stale = Orchestrator(str(tmp_path))
    first.handoff("planner", {"decision": "proceed"})
    stale.handoff("builder", {"decision": "brief"})
    assert (tmp_path / ".amu/run/planner-1.json").exists()
    assert (tmp_path / ".amu/run/builder-2.json").exists()


def test_parallel_processes_preserve_all_handoffs(tmp_path):
    assert _run_processes(tmp_path, _concurrent_handoffs) == [None] * 4
    files = list((tmp_path / ".amu/run").glob("planner-*.json"))
    assert len(files) == 16
    assert {int(path.stem.rsplit("-", 1)[1]) for path in files} == set(range(1, 17))
    assert len({json.loads(path.read_text())["evidence"][0] for path in files}) == 16


def test_stale_checker_instances_cannot_reuse_retry_budget(tmp_path):
    instances = [Orchestrator(str(tmp_path), task="same") for _ in range(3)]
    assert [instance.on_check(RETRYABLE)["decision"] for instance in instances] == ["retry", "retry", "escalate"]


def test_parallel_processes_spend_each_retry_once(tmp_path):
    decisions = _run_processes(tmp_path, _concurrent_checks)
    assert sorted(decisions) == ["escalate", "escalate", "retry", "retry"]
    assert state.read_json("run/budget.json", str(tmp_path))["used"]["retry"] == 2
    assert len(list((tmp_path / ".amu/run").glob("checker-*.json"))) == 4


def test_switching_tasks_and_resetting_one_task_preserves_other_budget(tmp_path):
    task_a = Orchestrator(str(tmp_path), task="task-a")
    assert task_a.on_check(RETRYABLE)["decision"] == "retry"
    assert task_a.on_check(RETRYABLE)["decision"] == "retry"
    task_b = Orchestrator(str(tmp_path), task="task-b")
    assert task_b.on_check(RETRYABLE)["decision"] == "retry"
    task_b.reset_budget()
    assert Orchestrator(str(tmp_path), task="task-a").on_check(RETRYABLE)["decision"] == "escalate"
    assert task_b.on_check(RETRYABLE)["decision"] == "retry"


def test_legacy_budget_is_preserved_before_another_task_writes_mirror(tmp_path):
    state.write_json("run/budget.json", {"task": "older", "max": {"retry": 2, "widen": 2, "sweep": 1}, "used": {"retry": 2, "widen": 0, "sweep": 0}}, str(tmp_path))
    Orchestrator(str(tmp_path), task="new").on_check(RETRYABLE)
    assert Orchestrator(str(tmp_path), task="older").on_check(RETRYABLE)["decision"] == "escalate"


@pytest.mark.parametrize("used", [False, [], "2", {"retry": True}, {"retry": "1"}, {"retry": -1}])
def test_budget_rejects_malformed_counters(used):
    with pytest.raises(ValueError):
        Budget(used=used)


@pytest.mark.parametrize("saved", [None, [], {}, {"task": "same", "used": {"retry": "2"}}, {"task": "same", "used": {"retry": False}}, {"task": "same", "used": {"retry": -1}}])
def test_existing_invalid_budget_cannot_become_a_fresh_budget(tmp_path, saved):
    state.write_json("run/budget.json", saved, str(tmp_path))
    with pytest.raises(state.SchemaError):
        Orchestrator(str(tmp_path), task="same")


def test_invalid_per_task_budget_cannot_be_ignored(tmp_path):
    digest = hashlib.sha256(b"same").hexdigest()
    state.write_json(f"run/budgets/{digest}.json", {"task": "other", "used": {}, "max": {}}, str(tmp_path))
    with pytest.raises(state.SchemaError):
        Orchestrator(str(tmp_path), task="same")


def test_interrupted_handoff_reservation_is_never_reused(tmp_path, monkeypatch):
    orchestrator = Orchestrator(str(tmp_path))
    original = state._write_json_atomic

    def interrupt(path, obj):
        if path.name == "planner-1.json":
            raise OSError("interrupted artifact write")
        return original(path, obj)

    monkeypatch.setattr(state, "_write_json_atomic", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        orchestrator.handoff("planner", {"decision": "proceed"})
    monkeypatch.setattr(state, "_write_json_atomic", original)
    Orchestrator(str(tmp_path)).handoff("planner", {"decision": "proceed"})
    assert not (tmp_path / ".amu/run/planner-1.json").exists()
    assert (tmp_path / ".amu/run/planner-2.json").exists()


def test_budget_spend_survives_failed_checker_artifact(tmp_path, monkeypatch):
    orchestrator = Orchestrator(str(tmp_path), task="same")
    original = state._write_json_atomic

    def interrupt(path, obj):
        if path.name.startswith("checker-"):
            raise OSError("interrupted artifact write")
        return original(path, obj)

    monkeypatch.setattr(state, "_write_json_atomic", interrupt)
    with pytest.raises(OSError):
        orchestrator.on_check(RETRYABLE)
    monkeypatch.setattr(state, "_write_json_atomic", original)
    assert orchestrator.on_check(RETRYABLE)["decision"] == "retry"
    assert orchestrator.on_check(RETRYABLE)["decision"] == "escalate"


@pytest.mark.parametrize("counter", [None, [], {}, {"schema_version": 1, "last": True}, {"schema_version": 1, "last": "1"}, {"schema_version": 1, "last": -1}])
def test_existing_invalid_sequence_cannot_reset_numbering(tmp_path, counter):
    state.write_json("run/sequence.json", counter, str(tmp_path))
    with pytest.raises(state.SchemaError):
        Orchestrator(str(tmp_path)).handoff("planner", {"decision": "proceed"})


def test_handoff_role_cannot_escape_the_run_directory(tmp_path):
    with pytest.raises(ValueError):
        Orchestrator(str(tmp_path)).handoff("../escape", {"decision": "proceed"})
