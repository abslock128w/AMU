"""Inspection search must not replace or create navigation workflow state."""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from amu import cli, entire, mcp_server, state


def _search_evidence(monkeypatch):
    monkeypatch.setattr(entire, "available", lambda: True)
    monkeypatch.setattr(entire, "graph_available", lambda: True)
    monkeypatch.setattr(entire, "search", lambda repo, query: {"results": [{
        "rank": 1, "file_path": "api.py", "start_line": 1,
        "symbol_name": "public", "kind": "function", "signals": ["fixture"],
    }]})


def _files(repo):
    root = repo / ".amu"
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("existing_history", [False, True])
def test_cli_no_history_preserves_navigation_and_lock_state(tmp_path, monkeypatch, existing_history):
    _search_evidence(monkeypatch)
    if existing_history:
        state.write_json("nav.json", {"stack": [{"kind": "find", "target": "earlier", "at": "2026-10-10T00:00:00Z"}]}, str(tmp_path))
    before = _files(tmp_path)
    result = CliRunner().invoke(cli.app, ["find", "validation", "--no-history", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["hits"][0]["symbol_name"] == "public"
    assert _files(tmp_path) == before
    if not existing_history:
        assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("existing_history", [False, True])
def test_mcp_search_real_cli_bridge_preserves_navigation(tmp_path, monkeypatch, existing_history):
    _search_evidence(monkeypatch)
    if existing_history:
        state.write_json("nav.json", {"stack": [{"kind": "find", "target": "earlier", "at": "2026-10-10T00:00:00Z"}]}, str(tmp_path))
    before = _files(tmp_path)

    def cli_process(command, **options):
        assert command[:4] == [mcp_server.sys.executable, "-I", "-m", "amu.cli"]
        assert options["cwd"] == str(tmp_path)
        result = CliRunner().invoke(cli.app, command[4:])
        return SimpleNamespace(stdout=result.stdout.encode("utf-8"), stderr=result.stderr.encode("utf-8"),
                               returncode=result.exit_code, error=None)

    monkeypatch.setattr(mcp_server, "_cli_process", cli_process)
    result = json.loads(mcp_server.amu_search("validation", repo=str(tmp_path)))
    assert result["hits"][0]["symbol_name"] == "public", result
    assert _files(tmp_path) == before
    if not existing_history:
        assert not (tmp_path / ".amu").exists()


def test_normal_cli_search_still_records_navigation_history(tmp_path, monkeypatch):
    _search_evidence(monkeypatch)
    result = CliRunner().invoke(cli.app, ["find", "validation", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    history = state.read_json("nav.json", str(tmp_path))
    assert history["stack"][0]["kind"] == "find" and history["stack"][0]["target"] == "validation"


def test_mcp_search_always_forwards_explicit_no_history(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_cli", lambda *args, **options: calls.append((args, options)) or "{}")
    assert mcp_server.amu_search("validation", repo="/repo") == "{}"
    assert calls == [(("find", "validation", "--no-history"), {"repo": "/repo"})]
