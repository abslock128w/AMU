"""Fail-closed persistence and whole read/modify/write regression checks."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from amu import state, workspace


@pytest.mark.parametrize("payload", ["{broken", "[]", '{"nodes": []}'])
def test_invalid_state_is_not_missing_or_replaced(tmp_path, payload):
    path = state.amu_dir(str(tmp_path)) / "state.json"
    path.write_text(payload)
    with pytest.raises(state.SchemaError):
        state.read_json("state.json", str(tmp_path), default={})
    with pytest.raises(state.SchemaError):
        state.update_json("state.json", str(tmp_path), {}, lambda _: {"nodes": {}})
    assert path.read_text() == payload


def test_missing_state_still_uses_default(tmp_path):
    assert state.read_json("contract.json", str(tmp_path), default={}) == {}


def test_state_update_has_no_lost_thread_updates(tmp_path):
    def increment(_):
        state.update_json("counter.json", str(tmp_path), {"n": 0}, lambda value: {"n": value["n"] + 1})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(40)))
    assert state.read_json("counter.json", str(tmp_path)) == {"n": 40}


def test_workspace_corruption_is_not_reset():
    path = workspace.home() / "workspace.json"
    path.write_text("{broken")
    with pytest.raises(workspace.WorkspaceError, match="workspace"):
        workspace.load()
    assert path.read_text() == "{broken"


def test_workspace_legacy_migrates_and_future_version_is_rejected():
    path = workspace.home() / "workspace.json"
    path.write_text(json.dumps({"active": None, "repos": []}))
    assert workspace.load()["schema_version"] == 1
    path.write_text(json.dumps({"active": None, "repos": [], "schema_version": 999}))
    with pytest.raises(workspace.WorkspaceError, match="version"):
        workspace.load()


def test_workspace_update_serializes_concurrent_additions():
    def add_entry(index):
        def add(ws):
            ws["repos"].append({"name": str(index), "path": f"/test/{index}"})
            return ws
        workspace.update(add)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add_entry, range(40)))
    assert len(workspace.load()["repos"]) == 40


def test_cli_reports_corrupt_state_as_structured_error(tmp_path):
    from typer.testing import CliRunner

    from amu.cli import app
    path = state.amu_dir(str(tmp_path)) / "map.json"
    path.write_text("{broken")
    result = CliRunner().invoke(app, ["plan", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "state_unreadable"
    assert path.read_text() == "{broken"
