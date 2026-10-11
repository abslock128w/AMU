"""Lifecycle gates: phase order, persisted builder briefs, and completion evidence."""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, lifecycle, state
from tests.test_lifecycle_evidence import _record


def _map() -> dict:
    nodes = []
    for number, path in enumerate(("src.py", "consumer.py", "interior.py"), start=1):
        nodes.append(
            {
                "id": f"n{number}",
                "path": path,
                "symbol": f"work_{number}",
                "confidence": "sound",
                "risk": "will_break" if number == 1 else "might_break",
                "feature": "Core",
                "order": number,
                "action": "edit",
                "via": f"CALLS:{path}:1",
                "reason": "resolved fixture edge",
                "verify": f"amu verify --node n{number}",
                "evidence": ["fixture"],
            }
        )
    return {
        "root": {"file": "src.py", "exports": 1, "internal": 0, "feature": "Core", "feature_source": "fixture"},
        "change": "signature",
        "depth": 2,
        "nodes": nodes,
        "ripples": [],
        "features": {"Core": 3},
        "radius": {"files": 3, "packages": 1, "exports": 1, "consumers": 2, "tests": 0},
        "resolution": {"sound": 3, "guessed": 0, "unknown": 0, "qualifier": "fixture"},
        "next": "amu plan --approve",
        "map_hash": "lifecycle-map-hash",
        "graph_evidence": {
            "approvable": True,
            "map_hash": "lifecycle-map-hash",
            "reasons": [],
            "queried_symbols": ["work_1"],
            "unresolved_focus": [],
            "impact_coverage": {
                "src.py#work_1": {
                    "target": "src.py#work_1",
                    "depth": 2,
                    "focus": {
                        "resolved": True,
                        "file_path": "src.py",
                        "qualified_name": "work_1",
                        "name": "work_1",
                    },
                    "sections": {
                        "callers": {"total": 0, "captured": 0},
                        "callees": {"total": 0, "captured": 0},
                        "type_consumers": {"total": 0, "captured": 0},
                        "data_flows": {"total": 0, "captured": 0},
                    },
                }
            },
            "edge_baselines": {"src.py#work_1": []},
            "worktree": {
                "stable": True,
                "before": {"digest": "fixture-worktree", "head": "fixture-head", "error": None},
                "after": {"digest": "fixture-worktree", "head": "fixture-head", "error": None},
                "pre_edit_graph_files": [],
            },
        },
    }


def _contract() -> dict:
    phases = [
        {"n": 1, "kind": "additive", "nodes": ["n1"], "files": ["src.py"], "tests": [], "reason": "fixture", "verify": "amu check --phase 1"},
        {"n": 2, "kind": "leaves", "nodes": ["n2"], "files": ["consumer.py"], "tests": [], "reason": "fixture", "verify": "amu check --phase 2"},
        {"n": 3, "kind": "interior", "nodes": ["n3"], "files": ["interior.py"], "tests": [], "reason": "fixture", "verify": "amu check --phase 3"},
    ]
    return {
        "targets": [{"symbol": "src.py#work_1", "change_class": "signature"}],
        "base_sha": "no-git",
        "map_hash": "lifecycle-map-hash",
        "phases": phases,
        "frozen_signatures": [],
        "allowed_files": ["src.py", "consumer.py", "interior.py"],
        "created_at": "now",
    }


def _write_approved_lifecycle(repo, *, checked_phases: list[int] | None = None, node_states: dict | None = None) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.test", "commit", "--allow-empty", "-qm", "baseline"],
        cwd=repo, check=True,
    )
    state.write_json("map.json", _map(), str(repo))
    state.write_json("contract.json", _contract(), str(repo))
    state.write_json(
        "state.json",
        {
            "nodes": node_states or {"n1": "planned", "n2": "planned", "n3": "planned"},
            "violations": [],
            "history": [_record(_contract(), lifecycle.capture_source(str(repo)), n) for n in checked_phases or []],
            "updated_at": "now",
        },
        str(repo),
    )


def test_later_phase_is_blocked_until_earlier_phase_has_green_evidence(tmp_path):
    _write_approved_lifecycle(tmp_path)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "2", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["incomplete_phases"] == [{"n": 1, "kind": "additive", "green": False, "source": "missing"}]
    assert payload["next"] == "amu check --phase 1"


def test_green_node_colours_without_a_recorded_check_cannot_advance_a_phase(tmp_path):
    _write_approved_lifecycle(
        tmp_path,
        node_states={"n1": "green", "n2": "planned", "n3": "planned"},
    )

    result = CliRunner().invoke(cli.app, ["check", "--phase", "2", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["incomplete_phases"] == [{"n": 1, "kind": "additive", "green": False, "source": "missing"}]


def test_green_prior_phase_allows_next_phase_and_records_the_next_builder_brief(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(
        tmp_path,
        checked_phases=[1],
        node_states={"n1": "green", "n2": "planned", "n3": "planned"},
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "2", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["passed"] is True
    assert payload["builder"]["role"] == "builder"
    assert payload["builder"]["phase"]["n"] == 3
    assert (tmp_path / ".amu" / "run" / "builder-2.json").exists()


def test_approved_plan_records_the_first_builder_brief(tmp_path, monkeypatch):
    # This fixture is intentionally state-only; live-map/current-worktree
    # matching has dedicated integration coverage.
    monkeypatch.setattr(cli.graph_refresh, "map_current_capture_reason", lambda *_args: None)
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "fixture-head")
    state.write_json("map.json", _map(), str(tmp_path))

    result = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["builder"]["role"] == "builder"
    assert payload["builder"]["phase"]["n"] == 1
    assert (tmp_path / ".amu" / "run" / "builder-2.json").exists()


def test_plan_refuses_an_exact_target_that_the_map_never_queried(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.graph_refresh, "map_current_capture_reason", lambda *_args: None)
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "fixture-head")
    state.write_json("map.json", _map(), str(tmp_path))

    result = CliRunner().invoke(
        cli.app,
        [
            "plan",
            "--approve",
            "--targets",
            "other.py#unmapped:signature",
            "--json",
            "--repo",
            str(tmp_path),
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert "outside mapped file" in payload["approval_refused"]
    assert not (tmp_path / ".amu" / "contract.json").exists()


def test_plan_rechecks_the_map_before_persisting_when_the_worktree_changes(tmp_path, monkeypatch):
    current = {"digest": "fixture-worktree"}
    state.write_json("map.json", _map(), str(tmp_path))
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": current["digest"], "files": [], "error": None},
    )
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "fixture-head")
    real_problem = cli._map_approval_problem
    calls = 0

    def mutate_after_initial_approval(repo, amu_map):
        nonlocal calls
        calls += 1
        result = real_problem(repo, amu_map)
        if calls == 1:
            current["digest"] = "edited-after-first-approval"
        return result

    monkeypatch.setattr(cli, "_map_approval_problem", mutate_after_initial_approval)

    result = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert calls == 2
    assert "different editable worktree" in payload["approval_refused"]
    assert not (tmp_path / ".amu" / "contract.json").exists()


def test_done_blocks_before_completion_work_when_any_planned_phase_is_incomplete(tmp_path):
    _write_approved_lifecycle(
        tmp_path,
        checked_phases=[1],
        node_states={"n1": "green", "n2": "planned", "n3": "planned"},
    )

    result = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert [phase["n"] for phase in payload["incomplete_phases"]] == [2, 3]
    assert payload["next"] == "amu check"
    assert not (tmp_path / ".amu" / "report.json").exists()


def test_done_accepts_all_green_phase_history_and_records_lifecycle_status(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write_approved_lifecycle(
        tmp_path,
        checked_phases=[1, 2, 3],
        node_states={"n1": "green", "n2": "green", "n3": "green"},
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})

    result = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["lifecycle"] == {
        "required": True,
        "passed": True,
        "phases": [
            {"n": 1, "kind": "additive", "green": True, "source": "history"},
            {"n": 2, "kind": "leaves", "green": True, "source": "history"},
            {"n": 3, "kind": "interior", "green": True, "source": "history"},
        ],
    }


def _successful_runners(monkeypatch):
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_a, **_k: {
        "command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed",
    })
    monkeypatch.setattr(cli, "_run_build", lambda *_a, **_k: None)


def _check(repo, *args):
    return CliRunner().invoke(cli.app, ["check", *args, "--json", "--repo", str(repo)])


def test_completed_check_persists_explicit_bound_evidence(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    result = _check(tmp_path, "--phase", "1")
    assert result.exit_code == 0, result.output
    saved = state.read_json("state.json", str(tmp_path))
    assert len(saved["history"]) == 1  # pending attempt is replaced, not duplicated
    record = saved["history"][0]
    assert record["event"] == "check" and record["version"] == 1 and record["passed"] is True
    assert record["source_stable"] is True
    assert record["contract_hash"] == state.stable_hash(_contract())
    assert record["source"] == lifecycle.capture_source(str(tmp_path))
    assert record["tests"]["status"] == "passed" and record["tests"]["returncode"] == 0
    assert record["build"] == {"status": "not_configured", "command": None}
    assert [p["green"] for p in lifecycle.phase_statuses(_contract(), saved, record["source"])] == [True, False, False]


def test_later_phase_rechecks_cumulative_tests_on_edited_source(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    contract = _contract()
    contract["phases"][0]["tests"] = ["test_a.py"]
    contract["phases"][1]["tests"] = ["test_b.py", "test_a.py"]
    state.write_json("contract.json", contract, str(tmp_path))
    assert _check(tmp_path, "--phase", "1").exit_code == 0
    (tmp_path / "consumer.py").write_text("value = 2\n")
    # Refresh behavior has dedicated tests; here isolate cumulative check evidence.
    monkeypatch.setattr(cli, "_refresh_gate_violations", lambda *_: [])
    calls = []

    def tests(_repo, targets, _cfg):
        calls.append(targets)
        return {"command": "pytest test_a.py test_b.py", "returncode": 0, "passed": True, "summary": "2 passed"}

    monkeypatch.setattr(cli, "_run_tests", tests)
    assert not any(p["green"] for p in lifecycle.phase_statuses(
        contract, state.read_json("state.json", str(tmp_path)), lifecycle.capture_source(str(tmp_path)),
    ))
    result = _check(tmp_path, "--phase", "2")
    assert result.exit_code == 0, result.output
    assert calls == [["test_a.py", "test_b.py"]]
    saved = state.read_json("state.json", str(tmp_path))
    assert saved["history"][-1]["covered_phases"] == [1, 2]
    assert [p["green"] for p in lifecycle.phase_statuses(contract, saved, lifecycle.capture_source(str(tmp_path)))] == [True, True, False]


@pytest.mark.parametrize("mutation", ["source", "head", "config", "contract"])
def test_changes_during_check_never_record_success(fake_entire, tmp_path, monkeypatch, mutation):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)

    def tests(*_args):
        if mutation == "source":
            (tmp_path / "src.py").write_text("changed_during_tests = True\n")
        elif mutation == "head":
            subprocess.run(
                ["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.test", "commit", "--allow-empty", "-qm", "new revision"],
                cwd=tmp_path, check=True,
            )
        elif mutation == "config":
            state.write_json("config.json", {**state.config(str(tmp_path)), "test_cmd": "pytest -x"}, str(tmp_path))
        else:
            state.write_json("contract.json", {**_contract(), "created_at": "new approval"}, str(tmp_path))
        return {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"}

    monkeypatch.setattr(cli, "_run_tests", tests)
    result = _check(tmp_path, "--phase", "1")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert any(v["kind"] == "CHECK_EVIDENCE_INVALID" for v in payload["violations"])
    saved = state.read_json("state.json", str(tmp_path))
    assert saved["history"][-1]["passed"] is False
    assert not any(p["green"] for p in lifecycle.phase_statuses(_contract(), saved, lifecycle.capture_source(str(tmp_path))))


def test_skipped_tests_are_explicit_and_do_not_advance(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    monkeypatch.setattr(cli, "_run_tests", lambda *_: pytest.fail("--no-tests must not execute tests"))
    result = _check(tmp_path, "--phase", "1", "--no-tests")
    assert result.exit_code == 1
    assert "diagnostic check cannot advance" in result.stdout
    saved = state.read_json("state.json", str(tmp_path))
    assert saved["history"][-1]["tests"] == {"status": "skipped", "command": None}
    assert _check(tmp_path, "--phase", "2").exit_code == 2


def test_missing_configured_build_outcome_is_not_success(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    state.write_json("config.json", {**state.config(str(tmp_path)), "build_cmd": "python -m compileall ."}, str(tmp_path))
    result = _check(tmp_path, "--phase", "1")
    assert result.exit_code == 1
    assert "configured build returned no valid successful outcome" in result.stdout


def test_tool_failure_revokes_previous_success_without_falling_back(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    assert _check(tmp_path).exit_code == 0
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: None)
    assert _check(tmp_path).exit_code == 3
    saved = state.read_json("state.json", str(tmp_path))
    assert len(saved["history"]) == 2
    assert saved["history"][-1]["passed"] is False
    assert saved["history"][-1]["tests"]["status"] == "not_run"
    done = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])
    assert done.exit_code == 1
    assert len(json.loads(done.stdout)["incomplete_phases"]) == 3


def test_done_rejects_edits_after_a_real_successful_check(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    _successful_runners(monkeypatch)
    assert _check(tmp_path).exit_code == 0
    (tmp_path / "README.md").write_text("An edit after the check.\n")
    result = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["next"] == "amu check"
    assert not (tmp_path / ".amu/report.json").exists()
