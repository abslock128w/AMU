"""Shared graph sync decisions retain evidence and fail-closed cache guards."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from amu import state
from amu.services import graph_sync as service
from tests.test_docs import _committed_repo, _git

READY_INDEX = {"counts": {"files": 2, "relations": {"CALLS": 1}}}


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    events = []
    worktree = {"head": "fixture-head", "digest": "fixture-worktree", "files": ["api.py"], "error": None}
    monkeypatch.setattr(service.entire, "graph_version", lambda: events.append(("version",)) or "fixture-provider")
    monkeypatch.setattr(service.entire, "available", lambda: True)
    monkeypatch.setattr(service.entire, "index", lambda repo: events.append(("index", repo)) or READY_INDEX)
    monkeypatch.setattr(service.state, "git_head", lambda repo: "fixture-head")
    monkeypatch.setattr(service.state, "now_iso", lambda: "fixture-time")
    monkeypatch.setattr(service.graph_refresh, "graph_capture", lambda repo: events.append(("capture", repo)) or worktree)
    monkeypatch.setattr(service.graph_refresh, "snapshot_hash", lambda repo: "fixture-hash")

    def snapshot(repo, output, *, worktree=False):
        events.append(("snapshot", repo, output, worktree))
        return {"lines": 4}

    monkeypatch.setattr(service.entire, "snapshot", snapshot)
    state.write_json("map.json", {"depth": 3}, str(tmp_path))
    return {"repo": str(tmp_path), "events": events, "worktree": worktree}


def test_success_payload_preserves_head_index_and_worktree_snapshot(workflow, capsys):
    repo = workflow["repo"]
    result = service.execute(repo)
    readiness = state.read_json("graph-readiness.json", repo)
    assert result == {
        "sha": "fixture-head", "index_ref": "HEAD", "snapshot_ref": "worktree",
        "counts": READY_INDEX["counts"], "snapshot_lines": 4, "worktree": workflow["worktree"],
        "provider_version": "fixture-provider", "graph_depth": 3, "snapshot_hash": "fixture-hash",
        "code_cache_reused": False, "readiness": readiness, "at": "fixture-time", "error": None,
    }
    assert readiness["ready"] is True and readiness["sha"] == "fixture-head"
    assert readiness["at"] == "fixture-time" and readiness["stage"] == "index"
    assert workflow["events"] == [("version",), ("index", repo), ("capture", repo),
                                  ("snapshot", repo, str(Path(repo) / ".amu/snapshot.ndjson"), True)]
    assert state.read_json("sync-state.json", repo) == result
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("repo", [None, False, "", " ", ".", "relative/path", Path("/tmp")])
@pytest.mark.parametrize("operation", ["execute", "record_index_readiness"])
def test_repository_must_be_explicit_before_any_provider_or_state_access(repo, operation, monkeypatch):
    monkeypatch.setattr(service.entire, "graph_version", lambda: pytest.fail("invalid repo must not query graph"))
    monkeypatch.setattr(service.state, "git_head", lambda repo: pytest.fail("invalid repo must not query state"))
    with pytest.raises(service.GraphSyncError, match="explicit absolute") as caught:
        getattr(service, operation)(repo) if operation == "execute" else getattr(service, operation)(repo, READY_INDEX)
    assert caught.value.exit_code == 2
    assert caught.value.payload == {"error": str(caught.value), "exit": 2}


@pytest.mark.parametrize("operation", ["execute", "record_index_readiness"])
def test_missing_or_file_repository_is_rejected_before_writes(tmp_path, operation):
    file = tmp_path / "plain-file"
    file.write_text("not a directory")
    for path in (file, tmp_path / "missing"):
        with pytest.raises(service.GraphSyncError, match="existing directory"):
            getattr(service, operation)(str(path)) if operation == "execute" else getattr(service, operation)(str(path), READY_INDEX)
    assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("value", [None, 0, 1, "true", "", [], {}, 0.0])
def test_cache_flag_is_a_strict_boolean_before_any_access(tmp_path, monkeypatch, value):
    monkeypatch.setattr(service.entire, "graph_version", lambda: pytest.fail("invalid flag must not query graph"))
    monkeypatch.setattr(service.state, "read_json", lambda *args, **kwargs: pytest.fail("invalid flag must not read state"))
    with pytest.raises(service.GraphSyncError, match="boolean"):
        service.execute(str(tmp_path), allow_doc_cache=value)
    assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("operation", ["execute", "record_index_readiness"])
def test_absolute_repository_alias_is_canonicalized(workflow, tmp_path, operation):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    if operation == "execute":
        service.execute(str(alias))
        assert ("index", str(tmp_path)) in workflow["events"]
        assert ("capture", str(tmp_path)) in workflow["events"]
    else:
        result = service.record_index_readiness(str(alias), READY_INDEX)
        assert result["ready"] is True
        assert state.read_json("graph-readiness.json", str(tmp_path)) == result


@pytest.mark.parametrize("index", [None, {}, {"error": "index failed"},
                                 {"counts": {"relations": {"DEFINES": 2, "CONTAINS": 2}}}])
def test_readiness_helper_retains_unready_diagnostics(workflow, index):
    result = service.record_index_readiness(workflow["repo"], index)
    assert result["ready"] is False and result["reasons"]
    assert state.read_json("graph-readiness.json", workflow["repo"]) == result


@pytest.mark.parametrize("index", [{"error": "index failed"}, {},
                                 {"counts": {"relations": {"DEFINES": 2, "CONTAINS": 2}}}])
def test_failed_index_skips_snapshot_and_records_unavailable_attempt(workflow, monkeypatch, index):
    monkeypatch.setattr(service.entire, "index", lambda repo: index)
    monkeypatch.setattr(service.entire, "snapshot", lambda *args, **kwargs: pytest.fail("unready index must not snapshot"))
    result = service.execute(workflow["repo"])
    assert result["readiness"]["ready"] is False and result["error"]
    assert result["snapshot_ref"] == "unavailable"
    assert result["snapshot_lines"] is None and result["snapshot_hash"] is None
    assert result["index_ref"] == "HEAD" and result["code_cache_reused"] is False


def test_missing_provider_is_an_explicit_failed_attempt(workflow, monkeypatch):
    monkeypatch.setattr(service.entire, "available", lambda: False)
    monkeypatch.setattr(service.entire, "index", lambda repo: pytest.fail("missing provider must not index"))
    monkeypatch.setattr(service.entire, "snapshot", lambda *args, **kwargs: pytest.fail("missing provider must not snapshot"))
    result = service.execute(workflow["repo"])
    assert result["error"] == "entire missing"
    assert result["snapshot_ref"] == "unavailable" and result["snapshot_hash"] is None


def test_failed_snapshot_preserves_prior_success_metadata_across_retries(workflow, monkeypatch):
    first = service.execute(workflow["repo"])
    failure = {"kind": "output_limit", "message": "bounded graph output exceeded limit"}
    monkeypatch.setattr(service.entire, "snapshot", lambda *args, **kwargs: {"error": failure, "lines": 1})
    failed = service.execute(workflow["repo"])
    assert failed["error"] == failure
    assert failed["snapshot_ref"] == "unavailable" and failed["snapshot_hash"] is None
    assert failed["snapshot_lines"] is None and failed["code_cache_reused"] is False
    keys = ("sha", "worktree", "snapshot_hash", "snapshot_lines", "provider_version", "graph_depth", "snapshot_ref", "at")
    assert failed["prior_snapshot"] == {key: first[key] for key in keys}
    retry = service.execute(workflow["repo"], allow_doc_cache=True)
    assert retry["prior_snapshot"] == failed["prior_snapshot"]
    assert retry["code_cache_reused"] is False and retry["error"] == failure


@pytest.mark.parametrize("filename", ["map.json", "sync-state.json"])
def test_corrupt_saved_state_is_not_silently_defaulted(workflow, monkeypatch, filename):
    path = Path(workflow["repo"]) / ".amu" / filename
    path.write_text("{invalid-json")
    monkeypatch.setattr(service.entire, "index", lambda repo: pytest.fail("unreadable state must stop before index"))
    with pytest.raises(state.StateReadError, match=filename):
        service.execute(workflow["repo"])
    assert path.read_text() == "{invalid-json"
    assert not (path.parent / "sync-state.json").exists() if filename == "map.json" else True
    assert not (path.parent / "graph-readiness.json").exists()


@pytest.fixture
def cached(tmp_path, monkeypatch):
    repo = _committed_repo(tmp_path, {"api.py": "def f(): return 1\n", "docs/api.md": "# API\nOriginal.\n"})
    state.write_json("map.json", {"depth": 2}, str(repo))
    calls = {"index": 0, "snapshot": 0}
    monkeypatch.setattr(service.entire, "available", lambda: True)
    monkeypatch.setattr(service.entire, "graph_version", lambda: "fixture-provider")

    def index(repo):
        calls["index"] += 1
        return READY_INDEX

    def snapshot(repo, output, *, worktree=False):
        calls["snapshot"] += 1
        assert worktree is True
        Path(output).write_text('{"record_type":"file","path":"api.py"}\n')
        return {"lines": 1}

    monkeypatch.setattr(service.entire, "index", index)
    monkeypatch.setattr(service.entire, "snapshot", snapshot)
    first = service.execute(str(repo))
    (repo / "docs/api.md").write_text("# API\nUpdated prose.\n")
    return repo, calls, first


def test_documentation_only_reuse_preserves_snapshot_capture_and_exposes_current_freshness(cached):
    repo, calls, first = cached
    result = service.execute(str(repo), allow_doc_cache=True)
    assert calls == {"index": 1, "snapshot": 1}
    assert result["code_cache_reused"] is True and result["snapshot_ref"] == "code-worktree-cache"
    assert result["worktree"] == first["worktree"] and result["snapshot_hash"] == first["snapshot_hash"]
    assert result["current_freshness"]["code"]["digest"] == first["worktree"]["freshness"]["code"]["digest"]
    assert result["current_freshness"]["docs"]["digest"] != first["worktree"]["freshness"]["docs"]["digest"]
    assert result["reused_at"] and "earlier prose revision" in result["note"]


@pytest.mark.parametrize("change", ["source", "config", "version", "missing_version", "empty_version", "depth",
                                   "snapshot", "missing_snapshot", "head", "readiness", "failed_sync",
                                   "index_ref", "snapshot_ref", "legacy_capture", "invalid_capture", "unchanged_full"])
def test_documentation_cache_requires_every_existing_guard(cached, monkeypatch, change):
    repo, calls, first = cached
    saved = copy.deepcopy(first)
    if change == "source":
        (repo / "api.py").write_text("def f(): return 2\n")
    elif change == "config":
        state.write_json("config.json", {"docs_dir": "manual"}, str(repo))
    elif change in {"version", "missing_version", "empty_version"}:
        monkeypatch.setattr(service.entire, "graph_version", lambda: {"version": "other-provider", "missing_version": "missing", "empty_version": ""}[change])
    elif change == "depth":
        state.write_json("map.json", {"depth": 3}, str(repo))
    elif change == "snapshot":
        (repo / ".amu/snapshot.ndjson").write_text("different snapshot\n")
    elif change == "missing_snapshot":
        (repo / ".amu/snapshot.ndjson").unlink()
    elif change == "head":
        _git(repo, "add", "docs/api.md")
        _git(repo, "commit", "-qm", "new head")
    elif change == "readiness":
        saved["readiness"]["ready"] = False
    elif change == "failed_sync":
        saved["error"] = "last attempt failed"
    elif change in {"index_ref", "snapshot_ref"}:
        saved[change] = "unavailable"
    elif change == "legacy_capture":
        saved["worktree"].pop("freshness")
    elif change == "invalid_capture":
        saved["worktree"]["freshness"]["schema_version"] = 999
    else:
        (repo / "docs/api.md").write_text("# API\nOriginal.\n")
    state.write_json("sync-state.json", saved, str(repo))
    result = service.execute(str(repo), allow_doc_cache=True)
    assert calls == {"index": 2, "snapshot": 2}
    assert result["code_cache_reused"] is False and result["snapshot_ref"] == "worktree"


def test_explicit_sync_always_rebuilds_current_worktree_even_for_prose_only_changes(cached):
    repo, calls, first = cached
    result = service.execute(str(repo))
    assert calls == {"index": 2, "snapshot": 2}
    assert result["code_cache_reused"] is False
    assert result["worktree"]["digest"] != first["worktree"]["digest"]


def test_core_has_no_presentation_or_entrypoint_imports():
    tree = ast.parse(Path(service.__file__).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
            imports.extend(f"{node.module}.{item.name}" for item in node.names)
    assert not any(name in {"typer", "rich", "amu.cli", "amu.brand"} or name.startswith(("typer.", "rich.")) for name in imports)
