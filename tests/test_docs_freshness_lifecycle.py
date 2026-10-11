"""Prose can reuse code relations, but cannot reuse a previous green check."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amu import cli, lifecycle, state
from tests.test_lifecycle_gates import _contract, _map

TARGET = "src.py#work_1"
INPUTS = {
    "src.py": "def work_1():\n    return 1\n",
    "README.md": "# Example project\n",
    "docs/guide.md": "# Guide\n\nOriginal guidance.\n",
    "docs/guide.rst": "Guide\n=====\n\nOriginal guidance.\n",
    "docs/example.py": "example_value = 1\n",
    "docs/conf.py": "project = 'Example'\n",
    "docs/components.mdx": "# Components\n\n<Example />\n",
    "docs/settings.json": '{"enabled": true}\n',
    "uv.lock": "version = 1\n",
    "requirements.txt": "example==1.0\n",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.test", *args],
                   cwd=repo, check=True, capture_output=True)


def _approved_repo(repo: Path) -> dict:
    """An actual committed baseline with a post-approval edit to one exact target."""
    for path, text in INPUTS.items():
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", *INPUTS)
    _git(repo, "commit", "-qm", "baseline")
    fingerprint = state.worktree_fingerprint(str(repo))
    assert fingerprint["error"] is None
    capture = {**fingerprint, "head": state.git_head(str(repo))}
    amu_map = deepcopy(_map())
    amu_map["change"] = "body"
    amu_map["nodes"] = amu_map["nodes"][:1]
    amu_map["graph_evidence"]["worktree"] = {
        "stable": True, "before": capture, "after": capture, "pre_edit_graph_files": [],
    }
    contract = deepcopy(_contract())
    contract["base_sha"] = capture["head"]
    contract["targets"] = [{"symbol": TARGET, "change_class": "body"}]
    contract["phases"] = contract["phases"][:1]
    contract["phases"][0]["files"] = list(INPUTS)
    contract["allowed_files"] = list(INPUTS)
    state.write_json("map.json", amu_map, str(repo))
    state.write_json("contract.json", contract, str(repo))
    state.write_json("state.json", {"nodes": {"n1": "planned"}, "violations": [], "history": []}, str(repo))
    (repo / "src.py").write_text("def work_1():\n    return 2\n", encoding="utf-8")
    return contract


def _provider(monkeypatch) -> None:
    """Fake only the graph provider and runners; freshness reads real Git/content."""
    impact = {
        "focus": {"id": "src-work", "name": "work_1", "qualified_name": "work_1",
                  "kind": "function", "file_path": "src.py"},
        "depth": 2,
        **{name: {"total": 0, "entries": []}
           for name in ("callers", "callees", "type_consumers", "data_flows")},
    }
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: {
        "counts": {"files": len(INPUTS), "symbols": 4, "relations": {"CALLS": 1}}, "partial_failures": [],
    })
    monkeypatch.setattr(cli.entire, "snapshot", lambda _repo, output, **_kwargs: {"lines": 4, "path": output})
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: deepcopy(impact))
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli.entire, "checkpoint_list", lambda: [])
    monkeypatch.setattr(cli, "_run_tests", lambda *_args: {
        "command": "pytest", "passed": True, "returncode": 0, "summary": "1 passed",
    })
    monkeypatch.setattr(cli, "_run_build", lambda *_args: None)
    monkeypatch.setattr(cli, "_sweep", lambda _repo: {"findings": [], "summary": "no findings", "error": None})


def _invoke(repo: Path, command: str, *args: str):
    result = CliRunner().invoke(cli.app, [command, *args, "--json", "--repo", str(repo)])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result, json.loads(result.stdout)


def _refresh(repo: Path) -> dict:
    result, payload = _invoke(repo, "refresh", "--symbol", TARGET, "--change", "body")
    assert result.exit_code == 0, result.stdout
    assert payload["complete"] is True
    return payload


@pytest.mark.parametrize("path", ["docs/guide.md", "docs/guide.rst", "README.md"])
def test_doc_edit_after_complete_refresh_reuses_code_graph_for_real_check(tmp_path, monkeypatch, path):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)
    (tmp_path / path).write_text("# Updated prose\n\nA clearer explanation.\n", encoding="utf-8")

    result, payload = _invoke(tmp_path, "check")

    assert payload["refresh_required"] == []
    assert result.exit_code == 0, result.stdout
    assert payload["passed"] is True


@pytest.mark.parametrize("path", [
    "docs/example.py", "docs/conf.py", "docs/components.mdx", "docs/settings.json", "uv.lock", "requirements.txt",
])
def test_executable_docs_and_configuration_edits_still_require_graph_coverage(tmp_path, monkeypatch, path):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)
    (tmp_path / path).write_text(INPUTS[path] + "\n# Changed input\n", encoding="utf-8")

    result, payload = _invoke(tmp_path, "check")

    assert result.exit_code == 1, result.stdout
    assert any(row["kind"] == "GRAPH_REFRESH_REQUIRED" and row["symbol"] == path
               for row in payload["refresh_required"])
    assert payload["passed"] is False


@pytest.mark.parametrize("path", ["docs/guide.md", "README.md"])
def test_prose_edit_invalidates_green_completion_until_full_check_runs_again(tmp_path, monkeypatch, path):
    contract = _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)
    first, _payload = _invoke(tmp_path, "check", "--phase", "1")
    assert first.exit_code == 0, first.stdout
    prior = lifecycle.capture_source(str(tmp_path))
    (tmp_path / path).write_text("# Documentation changed after tests passed\n", encoding="utf-8")
    current = lifecycle.capture_source(str(tmp_path))
    assert current["digest"] != prior["digest"]
    statuses = lifecycle.phase_statuses(contract, state.read_json("state.json", str(tmp_path)), current)
    assert not any(phase["green"] for phase in statuses)

    blocked, payload = _invoke(tmp_path, "done")
    assert blocked.exit_code == 1, blocked.stdout
    assert payload["next"] == "amu check"
    assert not (tmp_path / ".amu/report.json").exists()

    checked, payload = _invoke(tmp_path, "check")
    assert checked.exit_code == 0, checked.stdout
    assert payload["refresh_required"] == []
    done, payload = _invoke(tmp_path, "done")
    assert done.exit_code == 0, done.stdout
    assert payload["complete"] is True
    assert payload["lifecycle"]["passed"] is True


def test_source_mutation_during_snapshot_refuses_complete_refresh(tmp_path, monkeypatch):
    _approved_repo(tmp_path)
    _provider(monkeypatch)

    def snapshot(_repo, output, **_kwargs):
        (tmp_path / "src.py").write_text("def work_1():\n    return 3\n", encoding="utf-8")
        return {"lines": 4, "path": output}

    monkeypatch.setattr(cli.entire, "snapshot", snapshot)
    result, payload = _invoke(tmp_path, "refresh", "--symbol", TARGET, "--change", "body")
    assert result.exit_code == 3, result.stdout
    assert payload["complete"] is False
    assert payload["graph"] is None
    assert "changed" in payload["error"]
    assert state.read_json("refresh-state.json", str(tmp_path))["targets"][TARGET]["complete"] is False


def test_config_change_invalidates_refreshed_graph_even_when_git_content_is_unchanged(tmp_path, monkeypatch):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)
    before = state.worktree_fingerprint(str(tmp_path))
    state.write_json("config.json", {**state.config(str(tmp_path)), "docs_dir": "manual"}, str(tmp_path))
    assert state.worktree_fingerprint(str(tmp_path))["digest"] == before["digest"]

    result, payload = _invoke(tmp_path, "check")

    assert result.exit_code == 1, result.stdout
    assert any(row["kind"] == "GRAPH_REFRESH_REQUIRED" and row["symbol"] == TARGET
               for row in payload["refresh_required"])


def test_documentation_edit_during_tests_never_records_green_evidence(tmp_path, monkeypatch):
    contract = _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)

    def tests(*_args):
        (tmp_path / "docs/guide.md").write_text("# Changed while tests ran\n", encoding="utf-8")
        return {"command": "pytest", "passed": True, "returncode": 0, "summary": "1 passed"}

    monkeypatch.setattr(cli, "_run_tests", tests)
    result, payload = _invoke(tmp_path, "check")
    assert result.exit_code == 1, result.stdout
    assert any(row["kind"] == "CHECK_EVIDENCE_INVALID" for row in payload["violations"])
    saved = state.read_json("state.json", str(tmp_path))
    assert saved["history"][-1]["passed"] is False
    assert not any(row["green"] for row in lifecycle.phase_statuses(
        contract, saved, lifecycle.capture_source(str(tmp_path)),
    ))


def test_legacy_full_capture_remains_strict_after_prose_edit(tmp_path, monkeypatch):
    contract = _approved_repo(tmp_path)
    _provider(monkeypatch)
    legacy = {
        "complete": True, "change": "body",
        "baseline": {"map_hash": contract["map_hash"], "depth": 2},
        "sync": {"sha": state.git_head(str(tmp_path))},
        "worktree": state.worktree_fingerprint(str(tmp_path)),
    }
    state.write_json("refresh-state.json", {"targets": {TARGET: legacy}}, str(tmp_path))
    (tmp_path / "docs/guide.md").write_text("# Edited after legacy capture\n", encoding="utf-8")

    result, payload = _invoke(tmp_path, "check")

    assert result.exit_code == 1, result.stdout
    assert any(row["kind"] == "GRAPH_REFRESH_REQUIRED" and row["symbol"] == TARGET
               for row in payload["refresh_required"])


def test_new_commit_requires_refresh_even_when_only_prose_was_committed(tmp_path, monkeypatch):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    _refresh(tmp_path)
    (tmp_path / "README.md").write_text("# Committed documentation edit\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "documentation")

    result, payload = _invoke(tmp_path, "check")

    assert result.exit_code == 1, result.stdout
    assert any(row["kind"] == "GRAPH_REFRESH_REQUIRED" and row["symbol"] == TARGET
               for row in payload["refresh_required"])


def test_done_after_checked_prose_edit_does_not_rescan_unchanged_code(tmp_path, monkeypatch):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    calls = {"index": 0, "snapshot": 0}
    monkeypatch.setattr(cli.entire, "graph_version", lambda: "graph-test-1")
    original_index = cli.entire.index
    def index(repo):
        calls["index"] += 1
        return original_index(repo)
    def snapshot(_repo, output, **_kwargs):
        calls["snapshot"] += 1
        Path(output).write_text('{"record_type":"file","path":"src.py"}\n')
        return {"lines": 1, "path": output}
    monkeypatch.setattr(cli.entire, "index", index)
    monkeypatch.setattr(cli.entire, "snapshot", snapshot)
    _refresh(tmp_path)
    (tmp_path / "docs/guide.md").write_text("# Reviewed documentation update\n")
    checked, payload = _invoke(tmp_path, "check")
    assert checked.exit_code == 0, checked.stdout
    assert not payload["refresh_required"]
    done, payload = _invoke(tmp_path, "done")
    assert done.exit_code == 0, done.stdout
    assert calls == {"index": 1, "snapshot": 1}
    assert state.read_json("sync-state.json", str(tmp_path))["code_cache_reused"] is True
    assert payload["snapshot"]["code_cache_reused"] is True
    assert payload["complete"] is True


def test_provider_change_requires_new_relationship_evidence(tmp_path, monkeypatch):
    _approved_repo(tmp_path)
    _provider(monkeypatch)
    monkeypatch.setattr(cli.entire, "graph_version", lambda: "graph-test-1")
    _refresh(tmp_path)
    monkeypatch.setattr(cli.entire, "graph_version", lambda: "graph-test-2")
    checked, payload = _invoke(tmp_path, "check")
    assert checked.exit_code == 1
    assert any(row["kind"] == "GRAPH_REFRESH_REQUIRED" for row in payload["refresh_required"])
