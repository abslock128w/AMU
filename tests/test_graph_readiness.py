"""Regression tests for fail-closed graph evidence at approval boundaries."""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, graph_readiness, state, workspace

UNSAFE_INDEX = {
    "counts": {
        "files": 1600,
        "symbols": 28_933,
        "relations": {"DEFINES": 28_933, "CONTAINS": 28_933},
        "completeness_level": "unsafe",
    },
    "partial_failures": 1424,
}

READY_INDEX = {
    "counts": {
        "files": 1,
        "relations": {"CALLS": 1},
    }
}


def _degraded_impact() -> dict:
    return {
        "focus": {"id": "fixture", "name": "classify_consumers", "file_path": "amu/classify.py"},
        "callers": {
            "total": 1,
            "entries": [
                {
                    "endpoint": {"name": "consumer", "qualified_name": "consumer", "file_path": "tests/test_amu.py"},
                    "relation": "CALLS",
                    "depth": 1,
                }
            ],
        },
        "callees": {"total": 0, "entries": []},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
        "completeness_scope": {"level": "degraded"},
    }


def _approve_report_only_lifecycle(repo) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for _ in range(2):
        subprocess.run(
            ["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.invalid",
             "commit", "--allow-empty", "-qm", "fixture baseline"],
            cwd=repo, check=True,
        )
    state.write_json("map.json", {"map_hash": "report-only-map", "depth": 2, "nodes": []}, str(repo))
    state.write_json(
        "contract.json",
        {
            "targets": [],
            "base_sha": "HEAD~1",
            "map_hash": "report-only-map",
            "phases": [],
            "frozen_signatures": [],
            "allowed_files": [],
            "created_at": "fixture",
        },
        str(repo),
    )


def test_index_evidence_rejects_unsafe_structural_only_response():
    evidence = graph_readiness.index_evidence(UNSAFE_INDEX)

    assert evidence["ready"] is False
    assert evidence["partial_failures"] == 1424
    assert any("unsafe" in reason for reason in evidence["reasons"])
    assert any("no semantic relations" in reason for reason in evidence["reasons"])


def test_init_records_unsafe_index_and_stops_before_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: UNSAFE_INDEX)
    monkeypatch.setattr(
        cli.entire, "snapshot", lambda *_args, **_kwargs: pytest.fail("unsafe index must not be snapshotted")
    )

    result = CliRunner().invoke(cli.app, ["init", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    persisted = json.loads((tmp_path / ".amu" / "graph-readiness.json").read_text())
    assert result.exit_code == 3
    assert "not semantically ready" in payload["error"]
    assert persisted["ready"] is False
    assert "unsafe" in " ".join(persisted["reasons"])


def test_workspace_indexing_rejects_and_records_unsafe_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace.entire, "index", lambda _repo: UNSAFE_INDEX)
    monkeypatch.setattr(
        workspace.entire, "snapshot", lambda *_args, **_kwargs: pytest.fail("unsafe index must not be snapshotted")
    )

    with pytest.raises(workspace.WorkspaceError, match="not semantically ready") as exc:
        workspace.index_repo(tmp_path)

    persisted = json.loads((tmp_path / ".amu" / "graph-readiness.json").read_text())
    assert exc.value.code == 3
    assert persisted["ready"] is False


def test_init_snapshots_the_current_worktree(tmp_path, monkeypatch):
    snapshots = []
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: READY_INDEX)
    monkeypatch.setattr(cli.entire, "capabilities", lambda _repo: {})
    monkeypatch.setattr(cli.memory, "refresh", lambda _repo: {"entries": 0, "changed_files": []})

    def snapshot(repo, out_path, worktree=False):
        snapshots.append((repo, out_path, worktree))
        return {"lines": 1, "path": out_path}

    monkeypatch.setattr(cli.entire, "snapshot", snapshot)

    result = CliRunner().invoke(cli.app, ["init", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert snapshots == [(str(tmp_path), str(tmp_path / ".amu" / "snapshot.ndjson"), True)]


@pytest.mark.parametrize("command", ("sync", "done"))
def test_sync_and_done_snapshot_the_current_worktree(command, tmp_path, monkeypatch):
    snapshots = []
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "index", lambda _repo: READY_INDEX)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli.memory, "refresh", lambda _repo: {"entries": 0, "changed_files": []})
    if command == "done":
        _approve_report_only_lifecycle(tmp_path)

    def snapshot(repo, out_path, worktree=False):
        snapshots.append((repo, out_path, worktree))
        return {"lines": 1, "path": out_path}

    monkeypatch.setattr(cli.entire, "snapshot", snapshot)

    result = CliRunner().invoke(cli.app, [command, "--json", "--repo", str(tmp_path)])
    sync_state = json.loads((tmp_path / ".amu" / "sync-state.json").read_text())

    assert result.exit_code == 0
    assert snapshots == [(str(tmp_path), str(tmp_path / ".amu" / "snapshot.ndjson"), True)]
    assert sync_state["index_ref"] == "HEAD"
    assert sync_state["snapshot_ref"] == "worktree"


def test_map_uses_live_impact_not_cached_head(fake_entire, tmp_path, monkeypatch):
    calls = []
    real_impact = cli.entire.impact_json
    worktree = {"digest": "stable-map-worktree", "files": [], "error": None}

    def record_impact(*args, **kwargs):
        calls.append(kwargs.get("head"))
        impact = real_impact(*args, **kwargs)
        # The recorded UI fixture samples its caller list (30 total, 15
        # entries).  This test is about the map's snapshot selection rather
        # than pagination, so give it a complete provider-shaped response.
        for section in ("callers", "callees", "type_consumers", "data_flows"):
            group = impact.get(section)
            if isinstance(group, dict) and isinstance(group.get("entries"), list):
                group["total"] = len(group["entries"])
        return impact

    monkeypatch.setattr(cli.entire, "impact_json", record_impact)
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: dict(worktree))
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "stable-map-head")

    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert calls == [False]
    assert payload["graph_evidence"]["approvable"] is True
    coverage = payload["graph_evidence"]["impact_coverage"]["amu/classify.py#classify_consumers"]
    assert coverage["target"] == "amu/classify.py#classify_consumers"
    assert coverage["depth"] == 2
    assert set(coverage["sections"]) == {"callers", "callees", "type_consumers", "data_flows"}
    assert all(isinstance(section["captured"], int) for section in coverage["sections"].values())
    assert payload["graph_evidence"]["worktree"] == {
        "stable": True,
        "before": {**worktree, "head": "stable-map-head"},
        "after": {**worktree, "head": "stable-map-head"},
        "pre_edit_graph_files": [],
    }


def test_degraded_live_map_stays_visible_but_cannot_be_approved(fake_entire, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: _degraded_impact())
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": "stable-map-worktree", "files": [], "error": None},
    )
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "stable-map-head")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["approvable"] is False
    assert "degraded" in " ".join(map_payload["graph_evidence"]["reasons"])
    assert planned.exit_code == 2
    assert "degraded" in plan_payload["approval_refused"]
    assert not (tmp_path / ".amu" / "contract.json").exists()


def test_wrong_focus_map_stays_visible_but_cannot_be_approved(fake_entire, tmp_path, monkeypatch):
    wrong_focus = {
        "focus": {"id": "wrong", "name": "classify_consumers", "qualified_name": "classify_consumers", "file_path": "other.py"},
        "callers": {
            "total": 1,
            "entries": [
                {
                    "endpoint": {"name": "consumer", "qualified_name": "consumer", "file_path": "consumer.py"},
                    "relation": "CALLS",
                    "depth": 1,
                }
            ],
        },
        "callees": {"total": 0, "entries": []},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
    }
    worktree = {"digest": "stable-map-worktree", "files": [], "error": None}
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: wrong_focus)
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: dict(worktree))
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "stable-map-head")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["approvable"] is False
    assert "graph resolved other.py" in " ".join(map_payload["graph_evidence"]["reasons"])
    assert planned.exit_code == 2
    assert "complete semantic baseline unavailable" in plan_payload["approval_refused"]


def test_plan_cannot_approve_a_map_after_an_intervening_commit(fake_entire, tmp_path, monkeypatch):
    impact = json.loads((fake_entire / "impact.json").read_text())
    for section in ("callers", "callees", "type_consumers", "data_flows"):
        group = impact.get(section)
        if isinstance(group, dict) and isinstance(group.get("entries"), list):
            group["total"] = len(group["entries"])
    current = {"head": "map-commit", "digest": "map-worktree"}
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: impact)
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": current["digest"], "files": [], "error": None},
    )
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: current["head"])

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )
    current["head"] = "intervening-commit"
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    assert mapped.exit_code == 0
    assert planned.exit_code == 2
    assert "different Git revision" in json.loads(planned.stdout)["approval_refused"]
    assert not (tmp_path / ".amu" / "contract.json").exists()


def test_map_from_dirty_graph_code_stays_viewable_but_cannot_be_approved_as_a_pre_edit_baseline(fake_entire, tmp_path, monkeypatch):
    worktree = {"digest": "dirty-map-worktree", "files": ["working.py"], "error": None}
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: dict(worktree))
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "stable-map-head")

    mapped = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(tmp_path)])

    map_payload = json.loads(mapped.stdout)
    plan_payload = json.loads(planned.stdout)
    assert mapped.exit_code == 0
    assert map_payload["graph_evidence"]["baseline_ready"] is False
    assert map_payload["graph_evidence"]["pre_edit_graph_files"] == ["working.py"]
    assert planned.exit_code == 2
    assert "began with graph-relevant uncommitted changes" in plan_payload["approval_refused"]


def test_map_rejects_worktree_drift_without_replacing_the_previous_map(fake_entire, tmp_path, monkeypatch):
    previous = {"previous": "map"}
    state.write_json("map.json", previous, str(tmp_path))
    rows = iter(
        [
            {"digest": "before", "files": ["target.py"], "error": None},
            {"digest": "after", "files": ["target.py"], "error": None},
        ]
    )
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: next(rows))
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "stable-map-head")

    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert "worktree changed while graph map was running" in payload["error"]
    assert state.read_json("map.json", str(tmp_path)) == previous


def test_map_rejects_a_git_revision_change_during_graph_collection(fake_entire, tmp_path, monkeypatch):
    previous = {"previous": "map"}
    state.write_json("map.json", previous, str(tmp_path))
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": "stable", "files": [], "error": None},
    )
    revision = {"head": "before-map"}
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: revision["head"])
    query = cli.entire.impact_json
    def intervening_commit(*args, **kwargs):
        impact = query(*args, **kwargs)
        revision["head"] = "after-map"
        return impact
    monkeypatch.setattr(cli.entire, "impact_json", intervening_commit)

    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert "Git revision changed while graph map was running" in payload["error"]
    assert state.read_json("map.json", str(tmp_path)) == previous


def test_map_rejects_a_worktree_fingerprint_error_without_saving(fake_entire, tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": None, "files": [], "error": "repository is not a Git worktree"},
    )

    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert "could not fingerprint worktree before graph map" in payload["error"]
    assert not (tmp_path / ".amu" / "map.json").exists()
