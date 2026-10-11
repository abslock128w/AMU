"""Transport-independent graph mapping and its persistence boundaries."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from amu.services import mapping


def _impact(name="target", path="module.py"):
    return {
        "focus": {"id": f"id:{path}#{name}", "name": name, "qualified_name": name, "file_path": path},
        "callers": {"total": 0, "entries": []},
        "callees": {"total": 0, "entries": []},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
        "stats": {"completeness_level": "complete"},
    }


@pytest.fixture
def provider(monkeypatch):
    rows = [
        {"name": "target", "qualified_name": "target", "file_path": "module.py", "kind": "function"},
        {"name": "_helper", "file_path": "module.py", "kind": "function"},
        {"record_type": "file", "name": "other", "file_path": "module.py", "kind": "function"},
        {"name": "foreign", "file_path": "other.py", "kind": "function"},
    ]
    queries = []
    monkeypatch.setattr(mapping.entire, "symbols_result", lambda repo: (rows, None))
    monkeypatch.setattr(mapping.entire, "capabilities", lambda repo: {})
    monkeypatch.setattr(mapping.graph_refresh, "graph_capture", lambda repo: {"digest": "stable", "files": [], "error": None})
    monkeypatch.setattr(mapping.state, "git_head", lambda repo: "head")
    monkeypatch.setattr(mapping.state, "now_iso", lambda: "2026-10-09T00:00:00Z")
    monkeypatch.setattr(mapping.source_evidence, "filter_python_call_evidence", lambda repo, impacts: (impacts, [], []))
    monkeypatch.setattr(mapping.workspace, "touch", lambda *args, **kwargs: None)

    def impact(repo, name, depth, **kwargs):
        queries.append((repo, name, depth, kwargs))
        return _impact(name, kwargs.get("file") or "module.py")

    monkeypatch.setattr(mapping.entire, "impact_json", impact)
    return rows, queries


def test_request_is_immutable():
    request = mapping.MapRequest(file="module.py")
    with pytest.raises(FrozenInstanceError):
        request.depth = 3


@pytest.mark.parametrize("map_request", [
    None,
    mapping.MapRequest(),
    mapping.MapRequest(file="module.py", symbol="module.py#target"),
    mapping.MapRequest(file=""), mapping.MapRequest(file=" "), mapping.MapRequest(file=5),
    mapping.MapRequest(symbol=""), mapping.MapRequest(symbol="module.py#"),
    mapping.MapRequest(symbol="#target"), mapping.MapRequest(symbol="module.py#target#extra"),
    mapping.MapRequest(file="../module.py"), mapping.MapRequest(file="/module.py"),
    mapping.MapRequest(file="module\x00.py"), mapping.MapRequest(symbol="../module.py#target"),
    mapping.MapRequest(file="module.py", depth=True), mapping.MapRequest(file="module.py", depth="2"),
    mapping.MapRequest(file="module.py", depth=0), mapping.MapRequest(file="module.py", depth=-1),
    mapping.MapRequest(file="module.py", change="invented"), mapping.MapRequest(file="module.py", change=[]),
])
def test_invalid_request_has_no_provider_queries_or_state_writes(tmp_path, provider, map_request):
    with pytest.raises(mapping.MapInputError):
        mapping.execute(str(tmp_path), map_request)
    assert not provider[1]
    assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("repo", [".", "", " ", None, 5, "/path/that/does/not/exist"])
def test_repo_must_be_explicit_absolute_existing_directory(tmp_path, provider, repo):
    with pytest.raises(mapping.MapInputError):
        mapping.execute(repo, mapping.MapRequest(file="module.py"))
    assert not provider[1]


def test_outside_symlink_cannot_be_mapped(tmp_path, provider):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def target(): pass\n")
    (root / "module.py").symlink_to(outside)
    with pytest.raises(mapping.MapInputError, match="repository-relative"):
        mapping.execute(str(root), mapping.MapRequest(file="module.py"))
    assert not provider[1]
    assert not (root / ".amu").exists()


def test_file_map_preserves_baselines_capture_navigation_and_progress(tmp_path, provider):
    progress = []
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"), progress=progress.append)
    assert provider[1] == [(str(tmp_path), "target", 2, {"file": "module.py", "head": False})]
    assert result["root"]["internal"] == 1
    assert result["graph_evidence"]["approvable"] is True
    assert result["graph_evidence"]["edge_baselines"] == {"module.py#target": []}
    assert result["graph_evidence"]["impact_coverage"]["module.py#target"]["focus"]["resolved"] is True
    assert result["graph_evidence"]["baseline_ready"] is True
    assert progress == ["resolving…", "resolving callers of target · 0 edges"]
    assert json.loads((tmp_path / ".amu" / "map.json").read_text()) == result
    assert mapping.state.read_json("nav.json", str(tmp_path))["stack"][-1]["target"] == "module.py#target"


def test_progress_never_changes_evidence_hash(tmp_path, provider):
    first = mapping.execute(str(tmp_path), mapping.MapRequest(symbol="module.py#target"))
    second = mapping.execute(str(tmp_path), mapping.MapRequest(symbol="module.py#target"), progress=lambda value: None)
    assert first["map_hash"] == second["map_hash"]


def test_legacy_bare_symbol_remains_an_inspection_query(tmp_path, provider):
    result = mapping.execute(str(tmp_path), mapping.MapRequest(symbol="target"))
    assert provider[1][0][-1] == {"file": None, "head": False}
    assert result["root"]["file"] == "module.py"
    assert result["graph_evidence"]["edge_baselines"] == {"module.py#target": []}


@pytest.mark.parametrize("focus", [None, {}])
def test_legacy_symbol_without_focus_is_an_explicit_unknown(tmp_path, provider, monkeypatch, focus):
    monkeypatch.setattr(mapping.entire, "impact_json", lambda *args, **kwargs: {**_impact(), "focus": focus})
    result = mapping.execute(str(tmp_path), mapping.MapRequest(symbol="target"))
    assert result["root"]["file"] == "?"
    assert result["graph_evidence"]["approvable"] is False
    assert result["nodes"][0]["confidence"] == "unknown"


@pytest.mark.parametrize("path", ["/outside.py", "../outside.py", "source\x00.py", 42])
def test_legacy_inferred_file_cannot_escape_repo(tmp_path, provider, monkeypatch, path):
    monkeypatch.setattr(mapping.entire, "impact_json", lambda *args, **kwargs: _impact(path=path))
    with pytest.raises(mapping.GraphDataError, match="unsafe inferred target path"):
        mapping.execute(str(tmp_path), mapping.MapRequest(symbol="target"))
    assert not (tmp_path / ".amu" / "map.json").exists()


def test_inventory_failure_does_not_replace_previous_map(tmp_path, provider, monkeypatch):
    previous = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    monkeypatch.setattr(mapping.entire, "symbols_result", lambda repo: ([], "invalid symbol inventory"))
    with pytest.raises(mapping.GraphDataError, match="entire graph symbols failed: invalid symbol inventory"):
        mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert mapping.state.read_json("map.json", str(tmp_path)) == previous


@pytest.mark.parametrize("mutation", ["head", "worktree"])
def test_mid_collection_mutation_preserves_prior_map_and_history(tmp_path, provider, monkeypatch, mutation):
    previous = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    nav = mapping.state.read_json("nav.json", str(tmp_path))
    if mutation == "head":
        calls = iter(["head", "new-head"])
        monkeypatch.setattr(mapping.state, "git_head", lambda repo: next(calls))
    else:
        calls = iter(["before", "after"])
        monkeypatch.setattr(mapping.graph_refresh, "graph_capture", lambda repo: {"digest": next(calls), "files": [], "error": None})
    with pytest.raises(mapping.GraphDataError, match="changed while graph map was running; no map was saved"):
        mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert mapping.state.read_json("map.json", str(tmp_path)) == previous
    assert mapping.state.read_json("nav.json", str(tmp_path)) == nav


def test_failed_worktree_capture_stops_before_provider_calls(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(mapping.graph_refresh, "graph_capture", lambda repo: {"digest": None, "error": "read denied"})
    with pytest.raises(mapping.GraphDataError, match="could not fingerprint worktree before graph map: read denied"):
        mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert not provider[1]
    assert not (tmp_path / ".amu").exists()


def test_capability_error_is_explicit_and_unapprovable(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(mapping.entire, "capabilities", lambda repo: {"error": "timeout", "rc": 124})
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert result["graph_evidence"]["approvable"] is False
    assert result["graph_evidence"]["capabilities_error"]["error"] == "timeout"


def test_unresolved_focus_stays_unknown(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(mapping.entire, "impact_json", lambda *args, **kwargs: {**_impact(), "focus": {}})
    result = mapping.execute(str(tmp_path), mapping.MapRequest(symbol="module.py#target"))
    assert result["graph_evidence"]["approvable"] is False
    assert result["graph_evidence"]["unresolved_focus"] == ["target"]
    assert result["nodes"][0]["confidence"] == "unknown"


def test_missing_file_inventory_is_not_a_sound_empty_graph(tmp_path, provider):
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="missing.py"))
    assert result["graph_evidence"]["approvable"] is False
    assert result["graph_evidence"]["queried_symbols"] == []
    assert not provider[1]


def test_source_uncertainty_is_disclosed_without_inventing_a_blocker(tmp_path, provider, monkeypatch):
    uncertain = [{"target": "target", "evidence": "source unavailable"}]
    monkeypatch.setattr(mapping.source_evidence, "filter_python_call_evidence", lambda repo, impacts: (impacts, [], uncertain))
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert result["graph_evidence"]["approvable"] is True
    assert result["graph_evidence"]["unverified_python_calls"] == uncertain
    assert result["resolution"]["source_uncertainties"] == uncertain


def test_source_contradictions_remain_an_approval_blocker(tmp_path, provider, monkeypatch):
    contradiction = [{"target": "target", "evidence": "docstring-only-call"}]
    monkeypatch.setattr(mapping.source_evidence, "filter_python_call_evidence", lambda repo, impacts: (impacts, contradiction, []))
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert result["graph_evidence"]["approvable"] is False
    assert result["graph_evidence"]["contradicted_python_calls"] == contradiction
    assert result["resolution"]["source_contradictions"] == contradiction


def test_dirty_code_inputs_are_inspectable_but_not_a_pre_edit_baseline(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(mapping.graph_refresh, "graph_capture", lambda repo: {
        "digest": "dirty", "error": None, "files": ["docs/guide.md", "docs/example.py", "uv.lock", ".amu/map.json"],
    })
    result = mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    assert result["graph_evidence"]["pre_edit_graph_files"] == ["docs/example.py", "uv.lock"]
    assert result["graph_evidence"]["baseline_ready"] is False


def test_navigation_history_keeps_existing_bounded_stack(tmp_path, provider):
    mapping.state.write_json("nav.json", {"stack": [{"kind": "find", "target": str(n), "at": "old"} for n in range(25)]}, str(tmp_path))
    mapping.execute(str(tmp_path), mapping.MapRequest(file="module.py"))
    nav = mapping.state.read_json("nav.json", str(tmp_path))
    assert len(nav["stack"]) == 20
    assert nav["stack"][0]["target"] == "6"
    assert nav["stack"][-1] == {"kind": "map", "target": "module.py#target", "at": "2026-10-09T00:00:00Z"}
