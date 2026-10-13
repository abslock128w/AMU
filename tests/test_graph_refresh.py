"""Deterministic edge-delta coverage for the post-edit graph refresh boundary."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from amu import cli, graph_refresh, graphview, state

TARGET = "pkg/target.py#target"


def _stable_map_worktree_capture() -> dict:
    return {
        "stable": True,
        "before": {"digest": "pre-edit-worktree", "files": [], "error": None, "head": "pre-edit-head"},
        "after": {"digest": "pre-edit-worktree", "files": [], "error": None, "head": "pre-edit-head"},
        "pre_edit_graph_files": [],
    }


def _saved_pre_edit_map() -> dict:
    """A prior map: one caller stays, one caller was removed by the edit."""
    return {
        "root": {"file": "pkg/target.py"},
        "depth": 2,
        "map_hash": "before-edit",
        "graph_evidence": {
            "queried_symbols": ["target"],
            "unresolved_focus": [],
            "map_hash": "before-edit",
            "worktree": _stable_map_worktree_capture(),
            "impact_coverage": {
                TARGET: {
                    "target": TARGET,
                    "depth": 2,
                    "focus": {
                        "resolved": True,
                        "file_path": "pkg/target.py",
                        "qualified_name": "target",
                        "name": "target",
                    },
                    "sections": {
                        "callers": {"total": 2, "captured": 2},
                        "callees": {"total": 0, "captured": 0},
                        "type_consumers": {"total": 0, "captured": 0},
                        "data_flows": {"total": 0, "captured": 0},
                    },
                }
            },
            "edge_baselines": {
                TARGET: [
                    {
                        "target": TARGET,
                        "related": "pkg/caller.py#removed_caller",
                        "relation": "CALLS",
                        "direction": "in",
                        "depth": 1,
                    },
                    {
                        "target": TARGET,
                        "related": "pkg/stable.py#still_calls",
                        "relation": "CALLS",
                        "direction": "in",
                        "depth": 1,
                    },
                ]
            },
        },
        "ripples": [
            {
                "confidence": "sound",
                "source": {"path": "pkg/target.py", "symbol": "target"},
                "endpoint": {"path": "pkg/caller.py", "symbol": "removed_caller"},
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            },
            {
                "confidence": "sound",
                "source": {"path": "pkg/target.py", "symbol": "target"},
                "endpoint": {"path": "pkg/stable.py", "symbol": "still_calls"},
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            },
            # A guessed co-change is deliberately not a semantic-edge baseline.
            {
                "confidence": "guessed",
                "source": {"path": "pkg/target.py", "symbol": "target"},
                "endpoint": {"path": "tests/test_target.py", "symbol": "test_target"},
                "relation": "CO_CHANGE",
                "direction": "side",
                "depth": 3,
            },
        ],
    }


def _post_edit_graph() -> dict:
    """Live graph after an edit: one edge disappeared and a cross-file one appeared."""
    return {
        "focus": "target-id",
        "nodes": [
            {"id": "target-id", "path": "pkg/target.py", "name": "target"},
            {"id": "stable-id", "path": "pkg/stable.py", "name": "still_calls"},
            {"id": "added-id", "path": "pkg/new_caller.py", "name": "newly_added_caller"},
        ],
        "edges": [
            {
                "from": "stable-id",
                "to": "target-id",
                "relation": "CALLS",
                "direction": "in",
                "section": "callers",
                "depth": 1,
            },
            {
                "from": "added-id",
                "to": "target-id",
                "relation": "CALLS",
                "direction": "in",
                "section": "callers",
                "depth": 1,
            },
        ],
    }


def test_post_edit_cross_file_edge_delta_surfaces_added_removed_and_unchanged_relationships():
    baseline = graph_refresh.map_baseline(TARGET, 2, _saved_pre_edit_map())
    after = graph_refresh.from_graph_payload(TARGET, _post_edit_graph())

    result = graph_refresh.delta(baseline.edges, after)

    assert baseline.available is True
    assert baseline.reason is None
    assert result == {
        "added": [
            {
                "provenance": "unknown", "confidence": "sound",
                "target": TARGET,
                "related": "pkg/new_caller.py#newly_added_caller",
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            }
        ],
        "removed": [
            {
                "provenance": "unknown", "confidence": "sound",
                "target": TARGET,
                "related": "pkg/caller.py#removed_caller",
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            }
        ],
        "unchanged": 1,
    }


def test_unrelated_saved_map_is_not_treated_as_an_empty_pre_edit_baseline():
    baseline = graph_refresh.map_baseline(
        TARGET,
        2,
        {**_saved_pre_edit_map(), "root": {"file": "pkg/other.py"}},
    )

    assert baseline.available is False
    assert baseline.edges == []
    assert baseline.reason == "saved map is for pkg/other.py, not pkg/target.py"


def test_baseline_requires_the_exact_symbol_that_was_mapped_not_just_the_same_file():
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["queried_symbols"] = ["different_target"]

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.edges == []
    assert baseline.reason == "saved map did not query the exact target pkg/target.py#target"


def test_baseline_requires_the_same_depth_as_the_saved_map():
    baseline = graph_refresh.map_baseline(TARGET, 1, _saved_pre_edit_map())

    assert baseline.available is False
    assert baseline.edges == []
    assert baseline.reason == "saved map was made at depth 2; refresh requested depth 1"


def test_baseline_rejects_old_maps_without_raw_impact_coverage():
    amu_map = _saved_pre_edit_map()
    del amu_map["graph_evidence"]["impact_coverage"]

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.reason == "saved map has no raw-impact coverage; create a new map before comparing edges"


def test_baseline_rejects_a_map_without_a_stable_worktree_capture():
    amu_map = _saved_pre_edit_map()
    del amu_map["graph_evidence"]["worktree"]

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.reason == "saved map has no stable worktree capture; create a new map before comparing edges"


@pytest.mark.parametrize(
    ("capture", "reason"),
    [
        ({"stable": False}, "saved map worktree capture is not marked stable; create a new map before comparing edges"),
        (
            {
                "stable": True,
                "before": {"digest": "before", "head": "same"},
                "after": {"digest": "after", "head": "same"},
            },
            "saved map worktree capture spans different worktree states; create a new map before comparing edges",
        ),
        (
            {
                "stable": True,
                "before": {"digest": "same", "head": "before"},
                "after": {"digest": "same", "head": "after"},
            },
            "saved map worktree capture spans different Git revisions; create a new map before comparing edges",
        ),
    ],
)
def test_baseline_rejects_an_unstable_or_revision_changed_map_capture(capture, reason):
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["worktree"] = capture

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.reason == reason


def test_approval_refuses_a_map_without_a_stable_worktree_capture(tmp_path):
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["approvable"] = True
    del amu_map["graph_evidence"]["worktree"]

    assert cli._map_approval_problem(str(tmp_path), amu_map) == "map has no stable worktree capture; re-run amu map before approving"


def test_approval_refuses_a_map_after_the_repository_advanced(monkeypatch, tmp_path):
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["approvable"] = True
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: {"digest": "pre-edit-worktree", "files": [], "error": None})
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "intervening-commit")

    reason = cli._map_approval_problem(str(tmp_path), amu_map)

    assert reason == "saved map is for a different Git revision than the current repository; re-run amu map before approving"


def test_approval_refuses_a_map_after_the_editable_worktree_changed(monkeypatch, tmp_path):
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["approvable"] = True
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: {"digest": "different-worktree", "files": ["pkg/caller.py"], "error": None})
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "pre-edit-head")

    reason = cli._map_approval_problem(str(tmp_path), amu_map)

    assert reason == "saved map is for a different editable worktree than the current repository; re-run amu map before approving"


def test_refresh_gate_blocks_a_graph_edit_outside_the_exact_target_coverage(monkeypatch):
    contract = {
        "targets": [{"symbol": TARGET, "change_class": "body"}],
        "map_hash": "before-edit",
    }
    saved = {
        "targets": {
            TARGET: {
                "complete": True,
                "change": "body",
                "baseline": {"map_hash": "before-edit", "depth": 2},
                "sync": {"sha": "head"},
                "worktree": {"digest": "current"},
            }
        }
    }

    def read(name, _repo, default=None):
        if name == "refresh-state.json":
            return saved
        if name == "map.json":
            return {"depth": 2}
        return default

    monkeypatch.setattr(cli.state, "read_json", read)
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: {"digest": "current", "files": [], "error": None})
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "head")

    violations = cli._refresh_gate_violations("repo", contract, {"pkg/target.py", "pkg/caller.py"})

    assert [violation.symbol for violation in violations] == ["pkg/caller.py"]
    assert "outside the exact mapped targets" in violations[0].detail


def test_refresh_gate_accepts_a_completed_move_destination_as_exact_coverage(monkeypatch):
    old_target = "pkg/old.py#old"
    contract = {
        "targets": [{"symbol": old_target, "change_class": "move"}],
        "map_hash": "before-edit",
    }
    saved = {
        "targets": {
            old_target: {
                "complete": True,
                "change": "move",
                "live_target": "pkg/new.py#new",
                "baseline": {"map_hash": "before-edit", "depth": 2},
                "sync": {"sha": "head"},
                "worktree": {"digest": "current"},
            }
        }
    }

    def read(name, _repo, default=None):
        if name == "refresh-state.json":
            return saved
        if name == "map.json":
            return {"depth": 2}
        return default

    monkeypatch.setattr(cli.state, "read_json", read)
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: {"digest": "current", "files": [], "error": None})
    monkeypatch.setattr(cli.state, "git_head", lambda _repo: "head")

    assert cli._refresh_gate_violations("repo", contract, {"pkg/old.py", "pkg/new.py"}) == []


def test_baseline_rejects_a_map_taken_after_graph_relevant_uncommitted_edits():
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["worktree"]["pre_edit_graph_files"] = ["pkg/target.py"]

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.reason == "saved map began with graph-relevant uncommitted changes; create a new map before comparing edges"


def test_baseline_rejects_a_pre_edit_section_that_was_truncated_by_the_provider():
    amu_map = _saved_pre_edit_map()
    amu_map["graph_evidence"]["impact_coverage"][TARGET]["sections"]["callers"] = {
        "total": 51,
        "captured": 50,
    }

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline.available is False
    assert baseline.reason == "saved map callers response was truncated (50 of 51 edges captured)"


def test_baseline_rejects_a_legacy_map_that_only_has_display_ripples():
    amu_map = _saved_pre_edit_map()
    del amu_map["graph_evidence"]["edge_baselines"]

    baseline = graph_refresh.map_baseline(TARGET, 2, amu_map)

    assert baseline == graph_refresh.EdgeEvidence(
        False,
        [],
        "saved map has no complete semantic-edge baseline; create a new map",
    )


def test_graph_delta_ignores_malformed_or_nonsemantic_edges_instead_of_inventing_relations():
    graph = _post_edit_graph()
    graph["edges"].extend(
        [
            {"from": "added-id", "to": "target-id", "section": "siblings", "direction": "side", "depth": 1},
            {"from": "missing", "to": "target-id", "relation": "CALLS", "section": "callers", "direction": "in", "depth": 1},
        ]
    )

    edges = graph_refresh.from_graph_payload(TARGET, graph)

    assert [(edge["related"], edge["relation"]) for edge in edges] == [
        ("pkg/new_caller.py#newly_added_caller", "CALLS"),
        ("pkg/stable.py#still_calls", "CALLS"),
    ]


def _live_impact_after_edit() -> dict:
    """Provider-shaped data used to prove the command reads current relationships."""
    return {
        "focus": {
            "id": "target-id",
            "name": "target",
            "qualified_name": "target",
            "kind": "function",
            "file_path": "pkg/target.py",
        },
        "depth": 2,
        "callers": {
            "total": 2,
            "entries": [
                {
                    "endpoint": {
                        "id": "stable-id",
                        "name": "still_calls",
                        "qualified_name": "still_calls",
                        "kind": "function",
                        "file_path": "pkg/stable.py",
                    },
                    "relation": "CALLS",
                    "depth": 1,
                },
                {
                    "endpoint": {
                        "id": "added-id",
                        "name": "newly_added_caller",
                        "qualified_name": "newly_added_caller",
                        "kind": "function",
                        "file_path": "pkg/new_caller.py",
                    },
                    "relation": "CALLS",
                    "depth": 1,
                },
            ],
        },
        "callees": {"total": 0, "entries": []},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
    }


def test_live_edge_evidence_requires_an_exact_focus_and_complete_sections():
    wrong_focus = _live_impact_after_edit()
    wrong_focus["focus"] = {**wrong_focus["focus"], "file_path": "pkg/other.py"}
    wrong = graph_refresh.live_edges(TARGET, wrong_focus)

    truncated = _live_impact_after_edit()
    truncated["callers"] = {**truncated["callers"], "total": 3}
    incomplete = graph_refresh.live_edges(TARGET, truncated)

    assert wrong == graph_refresh.EdgeEvidence(
        False,
        [],
        "graph resolved pkg/other.py, not pkg/target.py",
    )
    assert incomplete == graph_refresh.EdgeEvidence(
        False,
        [],
        "live graph callers response is truncated (2 of 3 edges returned)",
    )


def test_live_edges_preserves_a_real_outbound_data_flow_direction():
    """Entire may report a DATA_FLOWS relationship leaving the focus node."""
    impact = _live_impact_after_edit()
    impact["data_flows"] = {
        "total": 1,
        "entries": [
            {
                "endpoint": {
                    "id": "flow-id",
                    "name": "flow_consumer",
                    "qualified_name": "flow_consumer",
                    "file_path": "pkg/flow.py",
                },
                "relation": "DATA_FLOWS",
                "direction": "out",
                "depth": 1,
            }
        ],
    }

    evidence = graph_refresh.live_edges(TARGET, impact)

    assert evidence.available is True
    assert {
        "target": TARGET,
        "related": "pkg/flow.py#flow_consumer",
        "provenance": "unknown", "confidence": "sound",
        "relation": "DATA_FLOWS",
        "direction": "out",
        "depth": 1,
    } in evidence.edges


def test_live_edges_preserves_an_explicit_outbound_type_use():
    """A focus function can use an annotated type rather than be its consumer."""
    impact = _live_impact_after_edit()
    impact["type_consumers"] = {
        "total": 1,
        "entries": [{
            "endpoint": {"id": "type-id", "name": "Request", "qualified_name": "Request",
                         "file_path": "pkg/request.py"},
            "relation": "PARAM_TYPE", "direction": "out", "depth": 1,
        }],
    }
    evidence = graph_refresh.live_edges(TARGET, impact)
    assert evidence.available
    assert any(row["related"] == "pkg/request.py#Request" and row["direction"] == "out"
               and row["relation"] == "PARAM_TYPE" for row in evidence.edges)


@pytest.mark.parametrize("direction", [None, "side"])
def test_live_edges_refuses_ambiguous_type_use_direction(direction):
    impact = _live_impact_after_edit()
    entry = {"endpoint": {"id": "type-id", "name": "Request", "qualified_name": "Request",
                          "file_path": "pkg/request.py"},
             "relation": "PARAM_TYPE", "depth": 1}
    if direction is not None:
        entry["direction"] = direction
    impact["type_consumers"] = {"total": 1, "entries": [entry]}
    evidence = graph_refresh.live_edges(TARGET, impact)
    assert not evidence.available
    assert "type_consumers edge 1" in evidence.reason


def test_map_baselines_normalize_an_omitted_caller_direction_without_dropping_the_edge():
    impact = _live_impact_after_edit()
    assert "direction" not in impact["callers"]["entries"][0]

    baselines, errors = graph_refresh.edge_baselines_from_impacts("pkg/target.py", {"target": impact}, 2)

    assert errors == {}
    assert baselines[TARGET][0]["direction"] == "in"
    assert len(baselines[TARGET]) == 2


@pytest.mark.parametrize(
    ("raw_direction", "reason"),
    [
        (None, "live graph data_flows edge 1 has no direction; expected one of in, out"),
        ("side", "live graph data_flows edge 1 has direction side, expected one of in, out"),
    ],
)
def test_live_edges_rejects_an_ambiguous_or_unknown_data_flow_direction(raw_direction, reason):
    """Do not turn a directionless or invented flow into trusted delta evidence."""
    impact = _live_impact_after_edit()
    flow = {
        "endpoint": {
            "id": "flow-id",
            "name": "flow_consumer",
            "qualified_name": "flow_consumer",
            "file_path": "pkg/flow.py",
        },
        "relation": "DATA_FLOWS",
        "depth": 1,
    }
    if raw_direction is not None:
        flow["direction"] = raw_direction
    impact["data_flows"] = {"total": 1, "entries": [flow]}

    evidence = graph_refresh.live_edges(TARGET, impact)

    assert evidence == graph_refresh.EdgeEvidence(False, [], reason)


def test_depth_distinct_live_edges_survive_graph_conversion_and_are_not_false_removals():
    impact = _live_impact_after_edit()
    depth_one = impact["callers"]["entries"][0]
    depth_two = {**depth_one, "depth": 2}
    impact["callers"] = {"total": 2, "entries": [depth_one, depth_two]}

    graph = graphview.payload_from_impact("target", impact)
    live = graph_refresh.live_edges(TARGET, impact)
    through_graphview = graph_refresh.from_graph_payload(TARGET, graph)

    before = [
        {
            "target": TARGET,
            "related": "pkg/stable.py#still_calls",
            "relation": "CALLS",
            "direction": "in",
            "depth": 1,
        },
        {
            "target": TARGET,
            "related": "pkg/stable.py#still_calls",
            "relation": "CALLS",
            "direction": "in",
            "depth": 2,
        },
    ]

    assert live.available is True
    assert len(graph["edges"]) == 2
    assert through_graphview == live.edges
    assert graph_refresh.delta(before, through_graphview) == {"added": [], "removed": [], "unchanged": 2}


def test_refresh_indexes_and_snapshots_before_reading_live_edges_then_surfaces_edge_changes(tmp_path, monkeypatch):
    """The post-edit command must not query a stale graph before syncing evidence."""
    state.write_json("map.json", {**_saved_pre_edit_map(), "map_hash": "before-edit"}, str(tmp_path))
    events: list[str] = []

    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(
        cli.state,
        "worktree_fingerprint",
        lambda _repo: {"digest": "stable-worktree", "files": [], "error": None},
    )

    def index(repo):
        assert repo == str(tmp_path)
        events.append("index")
        return {"counts": {"files": 3, "symbols": 4, "relations": {"CALLS": 2}}, "partial_failures": []}

    def snapshot(repo, out_path, *args, **kwargs):
        assert repo == str(tmp_path)
        assert events == ["index"]
        assert kwargs.get("worktree") is True
        events.append("snapshot")
        return {"lines": 9, "path": out_path}

    def impact(repo, symbol, depth, **kwargs):
        assert repo == str(tmp_path)
        assert symbol == "target"
        assert depth == 2
        assert kwargs.get("file") == "pkg/target.py"
        assert kwargs.get("head", False) is False
        assert events == ["index", "snapshot"]
        events.append("live-impact")
        return _live_impact_after_edit()

    monkeypatch.setattr(cli.entire, "index", index)
    monkeypatch.setattr(cli.entire, "snapshot", snapshot)
    monkeypatch.setattr(cli.entire, "impact_json", impact)
    # This fixture exercises snapshot/query ordering, not source parsing. A
    # complete source proof is supplied explicitly so missing fixture files
    # cannot be mistaken for approval-grade graph evidence.
    monkeypatch.setattr(cli.source_evidence, "filter_python_call_evidence", lambda _repo, rows: (rows, [], []))

    result = CliRunner().invoke(
        cli.app,
        [
            "refresh",
            "--symbol",
            TARGET,
            "--depth",
            "2",
            "--change",
            "body",
            "--json",
            "--repo",
            str(tmp_path),
        ],
    )

    payload = json.loads(result.stdout)
    persisted = state.read_json("refresh-state.json", str(tmp_path))
    assert result.exit_code == 0
    assert events == ["index", "snapshot", "live-impact"]
    assert payload["complete"] is True
    assert payload["sync"]["snapshot_ref"] == "worktree"
    assert payload["baseline"] == {"available": True, "edge_count": 2, "map_hash": "before-edit", "depth": 2, "reason": None}
    assert payload["edge_delta"] == {
        "added": [
            {
                "provenance": "unknown", "confidence": "sound",
                "target": TARGET,
                "related": "pkg/new_caller.py#newly_added_caller",
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            }
        ],
        "removed": [
            {
                "provenance": "unknown", "confidence": "sound",
                "target": TARGET,
                "related": "pkg/caller.py#removed_caller",
                "relation": "CALLS",
                "direction": "in",
                "depth": 1,
            }
        ],
        "unchanged": 1,
    }
    assert persisted["last"] == TARGET
    assert persisted["targets"][TARGET]["graph"] == payload["graph"]


def test_refresh_fails_closed_without_a_live_query_when_sync_is_not_ready(tmp_path, monkeypatch):
    """Never pass a stale relation graph to an agent if its refresh failed."""
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(
        cli.entire,
        "index",
        lambda _repo: {"error": "semantic index unavailable", "counts": {"relations": {}}},
    )
    monkeypatch.setattr(cli.entire, "snapshot", lambda *_args, **_kwargs: pytest.fail("unsafe index must not snapshot"))
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: pytest.fail("failed sync must not query impact"))

    result = CliRunner().invoke(
        cli.app,
        ["refresh", "--symbol", TARGET, "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    persisted = state.read_json("refresh-state.json", str(tmp_path))
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert payload["graph"] is None
    assert "semantic index unavailable" in payload["error"]
    assert persisted["targets"][TARGET]["complete"] is False


def _patch_refresh_sync(monkeypatch, tmp_path, impact, *, fingerprints=None):
    """Provide a complete graph provider boundary for refresh command tests."""
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_available", lambda: True)
    monkeypatch.setattr(
        cli.entire,
        "index",
        lambda _repo: {"counts": {"files": 3, "symbols": 4, "relations": {"CALLS": 2}}, "partial_failures": []},
    )
    monkeypatch.setattr(cli.entire, "snapshot", lambda _repo, out_path, **_kwargs: {"lines": 4, "path": out_path})
    monkeypatch.setattr(cli.entire, "impact_json", lambda *_args, **_kwargs: impact)
    rows = iter(fingerprints or [{"digest": "stable", "files": [], "error": None}] * 3)
    monkeypatch.setattr(cli.state, "worktree_fingerprint", lambda _repo: next(rows))


def test_refresh_rejects_a_worktree_that_changes_between_snapshot_and_live_query(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    _patch_refresh_sync(
        monkeypatch,
        tmp_path,
        _live_impact_after_edit(),
        fingerprints=[
            {"digest": "before", "files": ["pkg/caller.py"], "error": None},
            {"digest": "after", "files": ["pkg/caller.py"], "error": None},
        ],
    )

    result = CliRunner().invoke(cli.app, ["refresh", "--symbol", TARGET, "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert payload["graph"] is None
    assert "worktree changed while graph refresh was running" in payload["error"]


def test_refresh_rejects_partial_live_graph_evidence_before_claiming_removed_edges(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    impact = _live_impact_after_edit()
    impact["partial_failures"] = [{"code": "E_PARSE_ERROR", "file_path": "pkg/missing.py"}]
    _patch_refresh_sync(monkeypatch, tmp_path, impact)

    result = CliRunner().invoke(cli.app, ["refresh", "--symbol", TARGET, "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert payload["edge_delta"] == {"added": [], "removed": [], "unchanged": 0}
    assert "partial failure" in payload["error"]


def test_refresh_blocks_a_source_contradicted_call_instead_of_reporting_it_as_added(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    _patch_refresh_sync(monkeypatch, tmp_path, _live_impact_after_edit())
    contradiction = {
        "target": "target",
        "section": "callers",
        "source_path": "pkg/new_caller.py",
        "endpoint": "newly_added_caller",
        "line": 1,
    }
    monkeypatch.setattr(
        cli.source_evidence,
        "filter_python_call_evidence",
        lambda _repo, rows: (rows, [contradiction], []),
    )

    result = CliRunner().invoke(cli.app, ["refresh", "--symbol", TARGET, "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["complete"] is False
    assert payload["graph_evidence"]["contradicted_python_calls"] == [contradiction]
    assert "source AST contradicted" in payload["error"]


def test_remove_refresh_reports_all_trusted_old_edges_as_removed_after_source_verified_deletion(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "target.py").write_text("def replacement():\n    return 1\n")
    (package / "caller.py").write_text("def removed_caller():\n    return 0\n")
    (package / "stable.py").write_text("def still_calls():\n    return 0\n")
    _patch_refresh_sync(monkeypatch, tmp_path, {"focus": {}, "callers": {}, "callees": {}})

    result = CliRunner().invoke(
        cli.app,
        ["refresh", "--symbol", TARGET, "--change", "remove", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["complete"] is True
    assert payload["graph"]["status"] == "removed"
    assert payload["edge_delta"]["added"] == []
    assert {edge["related"] for edge in payload["edge_delta"]["removed"]} == {
        "pkg/caller.py#removed_caller",
        "pkg/stable.py#still_calls",
    }


def test_remove_refresh_refuses_to_claim_an_old_caller_was_removed_when_it_still_references_target(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "target.py").write_text("def replacement():\n    return 1\n")
    (package / "caller.py").write_text("def removed_caller():\n    return target()\n")
    (package / "stable.py").write_text("def still_calls():\n    return 0\n")
    _patch_refresh_sync(monkeypatch, tmp_path, {"focus": {}, "callers": {}, "callees": {}})

    result = CliRunner().invoke(
        cli.app,
        ["refresh", "--symbol", TARGET, "--change", "remove", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["complete"] is False
    assert payload["edge_delta"] == {"added": [], "removed": [], "unchanged": 0}
    assert payload["old_relation_evidence"]["approvable"] is False
    assert payload["old_relation_evidence"]["issues"][0]["related"] == "pkg/caller.py#removed_caller"


def test_remove_refresh_refuses_a_renamed_old_consumer_that_can_hide_a_stale_reference(tmp_path, monkeypatch):
    """A vanished endpoint may have moved/renamed rather than disappeared."""
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "target.py").write_text("def replacement():\n    return 1\n")
    (package / "caller.py").write_text("def renamed_caller():\n    return target()\n")
    (package / "stable.py").write_text("def still_calls():\n    return 0\n")
    _patch_refresh_sync(monkeypatch, tmp_path, {"focus": {}, "callers": {}, "callees": {}})

    result = CliRunner().invoke(
        cli.app,
        ["refresh", "--symbol", TARGET, "--change", "remove", "--json", "--repo", str(tmp_path)],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["complete"] is False
    issue = next(item for item in payload["old_relation_evidence"]["issues"] if item["related"] == "pkg/caller.py#removed_caller")
    assert issue["verdict"] == "endpoint_absent"
    assert "renamed or moved" in issue["reason"]


def test_rename_refresh_compares_new_live_edges_against_the_old_target_identity(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    renamed = "pkg/target.py#renamed_target"
    impact = _live_impact_after_edit()
    impact["focus"] = {**impact["focus"], "id": "renamed-id", "name": "renamed_target", "qualified_name": "renamed_target"}
    _patch_refresh_sync(monkeypatch, tmp_path, impact)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "target.py").write_text("def renamed_target():\n    return 1\n")
    (package / "caller.py").write_text("from .target import renamed_target\n\ndef removed_caller():\n    return renamed_target()\n")
    (package / "stable.py").write_text("from .target import renamed_target\n\ndef still_calls():\n    return renamed_target()\n")

    result = CliRunner().invoke(
        cli.app,
        [
            "refresh",
            "--symbol",
            TARGET,
            "--change",
            "rename",
            "--new-symbol",
            renamed,
            "--json",
            "--repo",
            str(tmp_path),
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["live_target"] == renamed
    assert payload["complete"] is True
    assert all(edge["target"] == TARGET for edge in payload["edge_delta"]["added"])
    assert all(edge["target"] == TARGET for edge in payload["edge_delta"]["removed"])


def test_rename_refresh_refuses_to_hide_an_old_caller_that_still_names_the_old_target(tmp_path, monkeypatch):
    state.write_json("map.json", _saved_pre_edit_map(), str(tmp_path))
    renamed = "pkg/target.py#renamed_target"
    impact = _live_impact_after_edit()
    impact["focus"] = {**impact["focus"], "id": "renamed-id", "name": "renamed_target", "qualified_name": "renamed_target"}
    _patch_refresh_sync(monkeypatch, tmp_path, impact)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "target.py").write_text("def renamed_target():\n    return 1\n")
    (package / "caller.py").write_text("def removed_caller():\n    return target()\n")
    (package / "stable.py").write_text("def still_calls():\n    return renamed_target()\n")

    result = CliRunner().invoke(
        cli.app,
        [
            "refresh",
            "--symbol",
            TARGET,
            "--change",
            "rename",
            "--new-symbol",
            renamed,
            "--json",
            "--repo",
            str(tmp_path),
        ],
    )

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert payload["complete"] is False
    assert payload["edge_delta"] == {"added": [], "removed": [], "unchanged": 0}
    assert payload["old_relation_evidence"]["issues"][0]["related"] == "pkg/caller.py#removed_caller"
