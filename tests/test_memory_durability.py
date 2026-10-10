import json

from amu import memory, state


def _seed(tmp_path):
    repo = str(tmp_path)
    state.write_json("feature_map.json", {"Feature": ["src/**"]}, repo)
    memory.refresh(repo, files=("CLAUDE.md",))
    return repo, state.read_json("memory.json", repo)["entries"][0]["id"]


def test_forget_survives_refresh_and_migrates_legacy_history(tmp_path):
    repo, eid = _seed(tmp_path)
    assert memory.forget(eid, repo)
    memory.refresh(repo, files=("CLAUDE.md",))
    saved = state.read_json("memory.json", repo)
    assert not saved["entries"]
    assert eid in saved["tombstones"]
    saved.pop("tombstones")
    saved.pop("schema_version", None)
    state.write_json("memory.json", saved, repo)
    assert not memory.collect(repo)


def test_refresh_preserves_creation_and_pin_without_timestamp_churn(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "now_iso", lambda: "2020-01-01T00:00:00Z")
    repo, eid = _seed(tmp_path)
    assert memory.pin(eid, repo)
    memory.refresh(repo, files=("CLAUDE.md",))
    original = (tmp_path / "CLAUDE.md").read_bytes()
    monkeypatch.setattr(state, "now_iso", lambda: "2025-01-01T00:00:00Z")
    result = memory.refresh(repo, files=("CLAUDE.md",))
    entry = state.read_json("memory.json", repo)["entries"][0]
    assert entry["created"] == "2020-01-01T00:00:00Z"
    assert entry["pinned"] is True
    assert result["changed_files"] == []
    assert (tmp_path / "CLAUDE.md").read_bytes() == original


def test_stale_evidence_does_not_become_fresh_on_second_refresh(tmp_path):
    repo = str(tmp_path)
    state.write_json("map.json", {"nodes": [{"id": "n1", "path": "x.py", "confidence": "unknown", "reason": "r", "verify": "v"}]}, repo)
    (tmp_path / "x.py").write_text("a")
    memory.refresh(repo, files=())
    (tmp_path / "x.py").write_text("b")
    memory.refresh(repo, files=())
    memory.refresh(repo, files=())
    assert state.read_json("memory.json", repo)["entries"][0].get("stale")


def test_memory_future_schema_is_not_overwritten(tmp_path):
    import pytest
    repo = str(tmp_path)
    path = state.amu_dir(repo) / "memory.json"
    payload = json.dumps({"schema_version": 999, "entries": [], "history": []})
    path.write_text(payload)
    with pytest.raises(state.SchemaError):
        memory.refresh(repo, files=())
    assert path.read_text() == payload
