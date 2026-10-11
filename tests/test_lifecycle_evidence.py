"""A phase needs positive, current check evidence, never an absence of errors."""

from copy import deepcopy

import pytest

from amu import lifecycle, state


@pytest.fixture
def contract():
    return {
        "map_hash": "map-a", "base_sha": "head-a", "created_at": "approval-a",
        "phases": [
            {"n": 1, "kind": "additive", "nodes": [], "files": ["a.py"], "tests": []},
            {"n": 2, "kind": "leaves", "nodes": [], "files": ["b.py"], "tests": []},
        ],
    }


@pytest.fixture
def source():
    return {"head": "head-a", "digest": "worktree-a", "config_hash": "config-a", "build_required": False, "error": None}


def _record(contract, source, phase=None):
    return {
        "version": 1, "event": "check", "phase": phase,
        "covered_phases": [p["n"] for p in contract["phases"] if phase is None or p["n"] <= phase],
        "contract_hash": state.stable_hash(contract), "passed": True,
        "source": deepcopy(source), "source_stable": True,
        "tests": {"status": "passed", "passed": True, "returncode": 0, "command": "pytest"},
        "build": {"status": "not_configured", "command": None},
        "violations": [],
    }


def test_empty_history_record_cannot_green_empty_phase(contract):
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [{}], "nodes": {}}))


@pytest.mark.parametrize("field", [
    "version", "event", "phase", "covered_phases", "contract_hash", "passed", "source", "source_stable",
    "tests", "build", "violations",
])
def test_missing_proof_fields_never_pass(contract, source, field):
    record = _record(contract, source)
    del record[field]
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [record]}, source))


@pytest.mark.parametrize("field,value", [
    ("version", True), ("event", "watch"), ("phase", True), ("phase", "1"),
    ("covered_phases", [1]), ("contract_hash", "other-contract"), ("passed", 1),
    ("source_stable", False), ("violations", [{}]), ("violations", ["oops"]),
    ("violations", [{"severity": "error"}]), ("violations", [{"severity": "ERROR"}]),
    ("tests", {"status": "skipped"}), ("tests", {"status": "passed", "passed": True}),
    ("build", None), ("build", {"status": "skipped"}),
])
def test_malformed_or_failed_proof_never_passes(contract, source, field, value):
    record = _record(contract, source)
    record[field] = value
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [record]}, source))


@pytest.mark.parametrize("field,value", [("head", "head-b"), ("digest", "edited"), ("config_hash", "config-b"), ("error", "unreadable")])
def test_source_or_configuration_change_invalidates_green(contract, source, field, value):
    record = _record(contract, source)
    current = {**source, field: value}
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [record]}, current))


def test_current_complete_evidence_can_green_all_phases(contract, source):
    assert all(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [_record(contract, source)]}, source))


def test_approval_changes_invalidate_evidence_even_with_same_map(contract, source):
    record = _record(contract, source)
    contract["phases"][0]["tests"] = ["test_new.py"]
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [record]}, source))


@pytest.mark.parametrize("bad", [{}, None, "invalid"])
def test_latest_malformed_record_cannot_fall_back_to_old_green(contract, source, bad):
    assert not any(p["green"] for p in lifecycle.phase_statuses(contract, {"history": [_record(contract, source), bad]}, source))


def test_cumulative_check_refreshes_predecessors_but_not_later_phases(contract, source):
    phases = lifecycle.phase_statuses(contract, {"history": [_record(contract, source, phase=1)]}, source)
    assert [p["green"] for p in phases] == [True, False]


def test_historical_milestone_allows_rechecking_later_phase_but_is_not_current_green(contract, source):
    record = _record(contract, source, phase=1)
    current = {**source, "digest": "later-phase-edit"}
    current_state = {"history": [record]}
    assert lifecycle.earlier_incomplete(contract, current_state, 2, current) == []
    assert lifecycle.incomplete_phases(contract, current_state, current)
    assert lifecycle.earlier_incomplete(contract, current_state, 2, {**current, "head": "other-revision"})


def test_node_drift_still_blocks_green(contract, source):
    contract["phases"][0]["nodes"] = ["n1"]
    current = {"history": [_record(contract, source)], "nodes": {"n1": "drift"}}
    assert not lifecycle.phase_statuses(contract, current, source)[0]["green"]
