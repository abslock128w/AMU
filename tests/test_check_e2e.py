"""End-to-end guardrail checks for the CLI's phase gate and test runner."""

from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from amu import cli, state


def _write_approved_state(repo) -> None:
    state.write_json(
        "map.json",
        {
            "root": {"file": "src.py", "exports": 1, "internal": 0, "feature": "Core", "feature_source": "derived"},
            "change": "signature",
            "depth": 2,
            "nodes": [{"id": "n1", "path": "src.py", "symbol": "work", "confidence": "sound", "risk": "will_break", "feature": "Core", "order": 1, "action": "edit", "via": "CALLS:src.py:1", "reason": "resolved", "verify": "amu verify --node n1", "evidence": ["impact:callers"]}],
            "ripples": [],
            "features": {"Core": 1},
            "radius": {"files": 1, "packages": 1, "exports": 1, "consumers": 1, "tests": 0},
            "resolution": {"sound": 1, "guessed": 0, "unknown": 0, "qualifier": "fixture"},
            "next": "amu plan --approve",
            "map_hash": "map-hash",
        },
        str(repo),
    )
    state.write_json(
        "contract.json",
        {
            "targets": [{"symbol": "src.py#work", "change_class": "signature"}],
            "base_sha": "no-git",
            "map_hash": "map-hash",
            "phases": [{"n": 1, "kind": "additive", "nodes": ["n1"], "files": ["src.py"], "tests": []}],
            "frozen_signatures": [],
            "allowed_files": ["src.py"],
            "created_at": "now",
        },
        str(repo),
    )


def _commit_source(repo) -> None:
    (repo / "src.py").write_text("def work(value):\n    return value\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "src.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=amu@example.test", "-c", "user.name=AMU", "commit", "-qm", "baseline"],
        cwd=repo,
        check=True,
    )


def test_test_runner_uses_an_argument_vector_not_a_shell(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return cli.process.Result(0, b"1 passed\n", b"", None, 9, 0)

    monkeypatch.setattr(cli.process, "run", fake_run)

    result = cli._run_tests(str(tmp_path), [], {"test_cmd": "pytest -q; touch should-not-run"})

    assert result["passed"] is False
    assert "shell control character" in result["summary"]
    assert calls == []


def test_test_runner_rejects_phase_targets_outside_the_repo(tmp_path):
    result = cli._run_tests(str(tmp_path), ["../../outside.py::test_x"], {"test_cmd": "pytest -q"})

    assert result["passed"] is False
    assert "outside the repository" in result["summary"]


def test_check_blocks_and_marks_state_red_when_tests_fail(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 1, "passed": False, "summary": "1 failed"})

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["passed"] is False
    assert any(v["kind"] == "TEST_FAILURE" for v in payload["violations"])
    assert state.read_json("state.json", str(tmp_path))["nodes"]["n1"] == "red"


def test_check_blocks_and_marks_state_red_when_the_build_fails(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: {"command": "python -m build", "returncode": 1, "passed": False, "summary": "build failed"})

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["build"]["passed"] is False
    assert any(v["kind"] == "BUILD_FAILURE" for v in payload["violations"])
    assert state.read_json("state.json", str(tmp_path))["nodes"]["n1"] == "red"


def test_build_runner_rejects_shell_control_syntax(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.process, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")))

    result = cli._run_build(str(tmp_path), {"build_cmd": "python -m build; touch should-not-run"})

    assert result["passed"] is False
    assert "shell control character" in result["summary"]


def test_python_project_commands_use_the_running_interpreter(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'fixture'\nversion = '0.0.0'\n")

    detected = state.detect_commands(str(tmp_path))

    assert detected["test_cmd"].endswith(" -m pytest -q")
    assert detected["build_cmd"].endswith(" -m compileall -q .")
    assert detected["test_cmd"].split(" -m ", 1)[0]


def test_check_rejects_unknown_phase_with_exit_code_two(tmp_path):
    _write_approved_state(tmp_path)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "0", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"].startswith("invalid phase 0")


def test_approved_contract_check_fails_closed_without_entire_graph(tmp_path):
    _write_approved_state(tmp_path)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 3
    assert "graph plugin missing" in json.loads(result.stdout)["error"]


def test_approved_contract_check_fails_closed_when_graph_diff_fails(fake_entire, tmp_path, monkeypatch):
    _write_approved_state(tmp_path)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 3
    assert "graph diff failed" in json.loads(result.stdout)["error"]


def test_init_fails_closed_when_graph_indexing_returns_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: {"error": "index unavailable"})

    result = CliRunner().invoke(cli.app, ["init", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 3
    assert "index unavailable" in json.loads(result.stdout)["error"]


def test_sync_returns_tooling_exit_when_graph_indexing_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: {"error": "index unavailable"})

    result = CliRunner().invoke(cli.app, ["sync", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["error"] == "index unavailable"


def test_sync_returns_tooling_exit_when_graph_snapshotting_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: {"counts": {"files": 1, "relations": 1}})
    monkeypatch.setattr(cli.entire, "snapshot", lambda _repo, _path, worktree=False: {"error": "snapshot unavailable"})

    result = CliRunner().invoke(cli.app, ["sync", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["error"] == "snapshot unavailable"
    assert payload["snapshot_lines"] is None


def test_check_enforces_graph_diff_files_even_when_git_has_no_worktree_change(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert "amu/classify.py" in payload["changed_files"]
    assert any(v["kind"] == "OUT_OF_SCOPE" and v["symbol"] == "amu/classify.py" for v in payload["violations"])


def test_check_blocks_uncommitted_change_in_a_file_with_a_frozen_signature(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    contract = state.read_json("contract.json", str(tmp_path))
    contract["frozen_signatures"] = ["src.py#work"]
    state.write_json("contract.json", contract, str(tmp_path))
    (tmp_path / "src.py").write_text("def work(value, depth=2):\n    return value\n")
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    violation = next(v for v in payload["violations"] if v["kind"] == "UNVERIFIABLE_WORKTREE_SEMANTICS")
    assert result.exit_code == 1
    assert violation["symbol"] == "src.py"
    assert "commit the intended change" in violation["detail"]
    assert payload["delta_brief"]["verify"].startswith("commit the intended change, run amu sync")
    assert "src.py" in payload["worktree_changed_files"]


def test_check_requires_a_fresh_graph_refresh_for_an_uncommitted_non_frozen_code_change(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    (tmp_path / "src.py").write_text("def work(value, depth=2):\n    return value\n")
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert not any(v["kind"] == "UNVERIFIABLE_WORKTREE_SEMANTICS" for v in payload["violations"])
    refresh = next(v for v in payload["violations"] if v["kind"] == "GRAPH_REFRESH_REQUIRED")
    assert refresh["symbol"] == "src.py#work"
    assert payload["next"].startswith("amu refresh --symbol src.py#work")


def test_check_accepts_a_successful_refresh_for_the_exact_current_worktree(fake_entire, tmp_path, monkeypatch):
    """A real refresh record clears the gate only while its content stays current."""
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    (tmp_path / "src.py").write_text("def work(value, depth=2):\n    return value\n")
    fingerprint = state.worktree_fingerprint(str(tmp_path))
    state.write_json(
        "refresh-state.json",
        {
            "targets": {
                "src.py#work": {
                    "complete": True,
                    "change": "signature",
                    "baseline": {"map_hash": "map-hash", "depth": 2},
                    "sync": {"sha": state.git_head(str(tmp_path))},
                    "worktree": fingerprint,
                }
            }
        },
        str(tmp_path),
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["passed"] is True
    assert payload["refresh_required"] == []


def test_check_rejects_a_refresh_record_with_the_wrong_change_class_or_map(fake_entire, tmp_path, monkeypatch):
    """A complete record cannot be replayed for a different approved edit."""
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    (tmp_path / "src.py").write_text("def work(value, depth=2):\n    return value\n")
    state.write_json(
        "refresh-state.json",
        {
            "targets": {
                "src.py#work": {
                    "complete": True,
                    "change": "remove",
                    "baseline": {"map_hash": "other-map", "depth": 1},
                    "sync": {"sha": state.git_head(str(tmp_path))},
                    "worktree": state.worktree_fingerprint(str(tmp_path)),
                }
            }
        },
        str(tmp_path),
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    violation = next(v for v in payload["violations"] if v["kind"] == "GRAPH_REFRESH_REQUIRED")
    assert "different change class" in violation["detail"]


def test_check_rejects_a_refresh_record_from_a_different_map_depth(fake_entire, tmp_path, monkeypatch):
    _commit_source(tmp_path)
    _write_approved_state(tmp_path)
    (tmp_path / "src.py").write_text("def work(value, depth=2):\n    return value\n")
    state.write_json(
        "refresh-state.json",
        {
            "targets": {
                "src.py#work": {
                    "complete": True,
                    "change": "signature",
                    "baseline": {"map_hash": "map-hash", "depth": 1},
                    "sync": {"sha": state.git_head(str(tmp_path))},
                    "worktree": state.worktree_fingerprint(str(tmp_path)),
                }
            }
        },
        str(tmp_path),
    )
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_tests", lambda *_args, **_kwargs: {"command": "pytest", "returncode": 0, "passed": True, "summary": "1 passed"})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(cli.app, ["check", "--phase", "1", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    violation = next(v for v in payload["violations"] if v["kind"] == "GRAPH_REFRESH_REQUIRED")
    assert "different map or graph depth" in violation["detail"]


def test_plan_does_not_approve_a_scope_that_the_planner_asks_to_resolve(tmp_path):
    _write_approved_state(tmp_path)
    amu_map = state.read_json("map.json", str(tmp_path))
    amu_map["nodes"][0]["confidence"] = "unknown"
    amu_map["nodes"][0]["risk"] = "unknown"
    amu_map["nodes"][0]["action"] = "ask"
    amu_map["nodes"][0]["via"] = None
    amu_map["resolution"] = {"sound": 0, "guessed": 0, "unknown": 1, "qualifier": "fixture unknown"}
    state.write_json("map.json", amu_map, str(tmp_path), kind="map")
    (tmp_path / ".amu" / "contract.json").unlink()

    result = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["planner"]["decision"] == "ask"
    assert payload["approval_refused"]
    assert not (tmp_path / ".amu" / "contract.json").exists()


def test_verify_preserves_python_module_pytest_command(tmp_path, monkeypatch):
    _write_approved_state(tmp_path)
    amu_map = state.read_json("map.json", str(tmp_path))
    amu_map["nodes"][0]["verify"] = "python -m pytest -q tests/test_src.py::test_work"
    state.write_json("map.json", amu_map, str(tmp_path), kind="map")
    state.write_json("config.json", {**state.config(str(tmp_path)), "test_cmd": "python -m pytest -q"}, str(tmp_path))
    called = []
    monkeypatch.setattr(cli, "_run_tests", lambda repo, tests, cfg: called.append((repo, tests, cfg)) or {"passed": True, "summary": "1 passed"})

    result = CliRunner().invoke(cli.app, ["verify", "--node", "n1", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert called[0][1] == ["tests/test_src.py::test_work"]
