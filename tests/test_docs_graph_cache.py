"""Prose-only completion can reuse validated source snapshots without reindexing."""

from pathlib import Path

import pytest

from amu import cli, state
from tests.test_docs import _committed_repo


@pytest.fixture
def cached(tmp_path, monkeypatch):
    repo = _committed_repo(tmp_path, {"api.py": "def f(): return 1\n", "docs/api.md": "# API\n\nOriginal.\n"})
    state.write_json("map.json", {"depth": 2}, str(repo))
    calls = {"index": 0, "snapshot": 0}
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "graph_version", lambda: "graph-test-1")
    def index(_):
        calls["index"] += 1
        return {"counts": {"files": 2, "relations": {"CALLS": 1}}}
    def snapshot(_, output, **kwargs):
        calls["snapshot"] += 1
        Path(output).write_text('{"record_type":"file","path":"api.py"}\n')
        return {"lines": 1}
    monkeypatch.setattr(cli.entire, "index", index)
    monkeypatch.setattr(cli.entire, "snapshot", snapshot)
    initial = cli._sync_state(str(repo))
    assert initial["snapshot_hash"] and not initial["error"]
    (repo / "docs/api.md").write_text("# API\n\nUpdated prose.\n")
    return repo, calls


def test_doc_only_completion_reuses_code_snapshot_with_explicit_provenance(cached):
    repo, calls = cached
    result = cli._sync_state(str(repo), allow_doc_cache=True)
    assert calls == {"index": 1, "snapshot": 1}
    assert result["code_cache_reused"] is True
    assert result["snapshot_ref"] == "code-worktree-cache"
    assert result["current_freshness"]["docs"]["digest"] != result["worktree"]["freshness"]["docs"]["digest"]


@pytest.mark.parametrize("change", ["source", "config", "version", "depth", "snapshot", "missing_snapshot"])
def test_cache_requires_exact_source_configuration_provider_and_snapshot(cached, monkeypatch, change):
    repo, calls = cached
    if change == "source":
        (repo / "api.py").write_text("def f(): return 2\n")
    elif change == "config":
        state.write_json("config.json", {"docs_dir": "manual"}, str(repo))
    elif change == "version":
        monkeypatch.setattr(cli.entire, "graph_version", lambda: "graph-test-2")
    elif change == "depth":
        state.write_json("map.json", {"depth": 3}, str(repo))
    elif change == "snapshot":
        (repo / ".amu/snapshot.ndjson").write_text("changed\n")
    else:
        (repo / ".amu/snapshot.ndjson").unlink()
    result = cli._sync_state(str(repo), allow_doc_cache=True)
    assert calls == {"index": 2, "snapshot": 2}
    assert result["code_cache_reused"] is False


def test_explicit_sync_always_requests_current_snapshot(cached):
    repo, calls = cached
    cli._sync_state(str(repo))
    assert calls == {"index": 2, "snapshot": 2}
