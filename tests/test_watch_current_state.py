import sys
from types import SimpleNamespace

from amu import state, watch


def test_watcher_reloads_contract_and_map_and_prefers_native(tmp_path, monkeypatch):
    repo = str(tmp_path)
    state.write_json("contract.json", {"phases": [{"n": 2, "files": ["a.py"]}]}, repo)
    state.write_json("map.json", {"nodes": [{"id": "old", "path": "a.py", "confidence": "sound"}]}, repo)
    options = {}
    def events(root, **kwargs):
        options.update(kwargs)
        state.write_json("contract.json", {"phases": [{"n": 1, "files": ["a.py"]}]}, repo)
        state.write_json("map.json", {"nodes": [{"id": "new", "path": "a.py", "confidence": "sound"}]}, repo)
        yield {(2, str(tmp_path / "a.py"))}
    monkeypatch.setitem(sys.modules, "watchfiles", SimpleNamespace(watch=events))
    results = []
    watch.run(repo, 1, lambda *args: results.append(args))
    assert results[0][1]["nodes"] == {"new": "editing"}
    assert options.get("force_polling") is not True


def test_poll_snapshot_tracks_control_files_without_state_feedback(tmp_path):
    state.write_json("contract.json", {}, str(tmp_path))
    state.write_json("map.json", {}, str(tmp_path))
    state.write_json("state.json", {}, str(tmp_path))
    paths = watch._snapshot(tmp_path)
    assert str(tmp_path / ".amu/contract.json") in paths
    assert str(tmp_path / ".amu/map.json") in paths
    assert str(tmp_path / ".amu/state.json") not in paths
