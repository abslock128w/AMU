"""Resource failures must never become empty graph evidence or successful refreshes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amu import cli, entire, graph, graph_refresh, state
from tests.test_docs import _committed_repo
from tests.test_graph_refresh import TARGET, _saved_pre_edit_map


@pytest.fixture
def bounded_provider(tmp_path, monkeypatch):
    """A real subprocess fixture; output limits cannot be bypassed by a mock."""
    binary_dir = tmp_path / "provider-bin"
    binary_dir.mkdir()
    binary = binary_dir / "entire"
    binary.write_text(
        f"#!{sys.executable}\n"
        + '''import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["AMU_RESOURCE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
mode = os.environ.get("AMU_RESOURCE_MODE", "")
command = args[1] if args[:1] == ["graph"] else args[0]
padding = "X" * 2048 if command == mode else ""
if mode == "stderr":
    print("X" * 2048, file=sys.stderr)
if command == "version":
    print("resource-fixture-1")
elif command == "index":
    print(json.dumps({"counts": {"files": 1, "symbols": 1, "relations": {"CALLS": 1}}, "padding": padding}))
elif command == "snapshot":
    print(json.dumps({"record_type": "header", "padding": padding}))
elif command == "symbols":
    print(json.dumps({"record_type": "symbol", "kind": "function", "name": "target", "qualified_name": "target", "file_path": "pkg/target.py", "padding": padding}))
elif command == "capabilities":
    print(json.dumps({"out_of_coverage": [], "unresolved_patterns": [], "padding": padding}))
elif command == "impact":
    result = {"focus": {"id": "target", "name": "target", "qualified_name": "target", "file_path": "pkg/target.py"}, "completeness_scope": {"level": "complete"}, "padding": padding}
    for section in ("callers", "callees", "type_consumers", "data_flows"):
        result[section] = {"total": 0, "entries": []}
    print(json.dumps(result))
elif command == "diff":
    print(json.dumps({"files": [], "padding": padding}))
elif command == "neighbors":
    print(json.dumps({"matches": [], "padding": padding}))
else:
    print("unsupported resource fixture command", file=sys.stderr)
    sys.exit(2)
'''
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", os.pathsep.join((str(binary_dir), os.environ["PATH"])))
    log = tmp_path / "provider-calls.ndjson"
    monkeypatch.setenv("AMU_RESOURCE_LOG", str(log))
    monkeypatch.setenv("AMU_RESOURCE_MODE", "")
    # Raising=False makes the pre-fix wrapper demonstrably ignore these caps.
    for key in ("MAX_QUERY_BYTES", "MAX_SYMBOL_BYTES", "MAX_SNAPSHOT_BYTES", "MAX_STDERR_BYTES"):
        monkeypatch.setattr(entire, key, 1024, raising=False)
    monkeypatch.setattr(entire, "MAX_RECORD_BYTES", 1024, raising=False)
    monkeypatch.setattr(cli.memory, "refresh", lambda _repo: {"entries": 0, "changed_files": []})
    return log


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _old_snapshot(repo: Path) -> tuple[Path, bytes]:
    path = state.amu_dir(str(repo)) / "snapshot.ndjson"
    old = b'{"record_type":"header","revision":"previous"}\n'
    path.write_bytes(old)
    return path, old


def _assert_resource_error(error: object) -> None:
    assert isinstance(error, str) and error
    assert any(word in error.lower() for word in ("limit", "exceed", "large", "bound")), error


def test_query_limit_is_an_explicit_failure_not_a_truncated_json_object(bounded_provider, monkeypatch):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "index")

    result = entire.index(".")

    _assert_resource_error(result.get("error"))
    assert result["rc"] == 125
    assert "counts" not in result


def test_capability_limit_is_not_hidden_by_conservative_fallback(bounded_provider, monkeypatch):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "capabilities")
    result = graph.capabilities(".")
    _assert_resource_error(result.get("error"))
    assert result["rc"] == 125 and result["capabilities_status"] == "unavailable"


def test_map_with_failed_capabilities_cannot_be_approved(bounded_provider, tmp_path, monkeypatch):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    monkeypatch.setenv("AMU_RESOURCE_MODE", "capabilities")
    runner = CliRunner()
    mapped = runner.invoke(cli.app, ["map", "--symbol", "pkg/target.py#target", "--json", "--repo", str(repo)])
    assert mapped.exit_code == 0, mapped.output
    evidence = json.loads(mapped.stdout)["graph_evidence"]
    assert evidence["approvable"] is False
    assert "capabilit" in " ".join(evidence["reasons"])
    approved = runner.invoke(cli.app, ["plan", "--targets", "pkg/target.py#target:body", "--approve", "--json", "--repo", str(repo)])
    assert approved.exit_code != 0 and not (repo / ".amu" / "contract.json").exists()


def test_stderr_limit_discards_otherwise_successful_json_evidence(bounded_provider, monkeypatch):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "stderr")

    result = entire.index(".")

    _assert_resource_error(result.get("error"))
    assert result["rc"] == 125
    assert "counts" not in result


@pytest.mark.parametrize("limit_kind", ("total", "record"))
def test_symbol_limit_is_not_successful_empty_inventory(bounded_provider, monkeypatch, limit_kind):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "symbols")
    if limit_kind == "record":
        monkeypatch.setattr(entire, "MAX_SYMBOL_BYTES", 8192)

    rows, error = entire.symbols_result(".")

    assert rows == []
    _assert_resource_error(error)


@pytest.mark.parametrize("command", ("open", "where", "tree"))
def test_readonly_inventory_resource_failure_cannot_report_empty_exports_or_missing_symbol(
    bounded_provider, tmp_path, monkeypatch, command
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    monkeypatch.setenv("AMU_RESOURCE_MODE", "symbols")
    args = [command]
    if command == "open":
        args.append("pkg/target.py")
    elif command == "where":
        args.append(TARGET)

    result = CliRunner().invoke(cli.app, [*args, "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    _assert_resource_error(payload["error"])
    assert "symbols" not in payload
    assert "definitions" not in payload
    assert "files" not in payload
    assert payload.get("status") != "not_found"


def test_tree_without_provider_preserves_offline_filesystem_inspection(tmp_path, monkeypatch):
    (tmp_path / "target.py").write_text("def target(): return 1\n")
    monkeypatch.setattr(entire, "available", lambda: False)
    monkeypatch.setattr(entire, "symbols_result", lambda *_args: pytest.fail("offline tree must not query provider"))

    result = CliRunner().invoke(cli.app, ["tree", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 0
    assert "target.py" in json.loads(result.stdout)["files"]


def test_verify_fallback_resource_failure_retains_unknown_count_and_error(
    bounded_provider, tmp_path, monkeypatch
):
    state.write_json("map.json", {"nodes": [{"id": "n1", "path": "pkg/target.py", "symbol": "target",
                                             "confidence": "unknown", "verify": "ask a human"}]}, str(tmp_path))
    monkeypatch.setenv("AMU_RESOURCE_MODE", "neighbors")

    result = CliRunner().invoke(cli.app, ["verify", "--node", "n1", "--attempt-fallback", "--json", "--repo", str(tmp_path)])

    fallback = json.loads(result.stdout)["fallback"]
    assert result.exit_code == 0
    assert fallback["neighbors_matches"] is None
    _assert_resource_error(fallback["error"])
    assert fallback["rc"] == 125


def test_verify_fallback_can_report_genuine_successful_zero_neighbors(bounded_provider, tmp_path):
    state.write_json("map.json", {"nodes": [{"id": "n1", "path": "pkg/target.py", "symbol": "target",
                                             "confidence": "unknown", "verify": "ask a human"}]}, str(tmp_path))

    result = CliRunner().invoke(cli.app, ["verify", "--node", "n1", "--attempt-fallback", "--json", "--repo", str(tmp_path)])

    fallback = json.loads(result.stdout)["fallback"]
    assert result.exit_code == 0
    assert fallback["neighbors_matches"] == 0
    assert not fallback.get("error")


@pytest.mark.parametrize("limit_kind", ("total", "record"))
def test_snapshot_limits_preserve_prior_snapshot_and_remove_incomplete_temporary_file(
    bounded_provider, tmp_path, monkeypatch, limit_kind
):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "snapshot")
    if limit_kind == "record":
        monkeypatch.setattr(entire, "MAX_SNAPSHOT_BYTES", 8192)
    destination, old = _old_snapshot(tmp_path)

    result = entire.snapshot(str(tmp_path), str(destination), worktree=True)

    _assert_resource_error(result.get("error"))
    assert destination.read_bytes() == old
    assert not list(destination.parent.glob(".snapshot.ndjson.*"))


def test_init_stops_on_snapshot_resource_failure_before_creating_config(
    bounded_provider, tmp_path, monkeypatch
):
    monkeypatch.setenv("AMU_RESOURCE_MODE", "snapshot")
    destination, old = _old_snapshot(tmp_path)

    result = CliRunner().invoke(cli.app, ["init", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    _assert_resource_error(payload["error"])
    assert destination.read_bytes() == old
    assert not (tmp_path / ".amu/config.json").exists()


def test_map_symbol_resource_failure_cannot_replace_previous_map_or_approve(
    bounded_provider, tmp_path, monkeypatch
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    monkeypatch.setenv("AMU_RESOURCE_MODE", "symbols")
    previous = {"map_hash": "previous", "sentinel": "unchanged"}
    state.write_json("map.json", previous, str(repo))

    mapped = CliRunner().invoke(cli.app, ["map", "--symbol", TARGET, "--json", "--repo", str(repo)])

    assert mapped.exit_code == 3
    _assert_resource_error(json.loads(mapped.stdout)["error"])
    assert state.read_json("map.json", str(repo)) == previous
    assert not (repo / ".amu/contract.json").exists()


def test_map_impact_resource_failure_remains_unknown_and_blocks_approval(
    bounded_provider, tmp_path, monkeypatch
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    monkeypatch.setenv("AMU_RESOURCE_MODE", "impact")
    mapped = CliRunner().invoke(cli.app, ["map", "--symbol", TARGET, "--json", "--repo", str(repo)])
    planned = CliRunner().invoke(cli.app, ["plan", "--approve", "--json", "--repo", str(repo)])

    assert mapped.exit_code in (0, 3)
    payload = json.loads(mapped.stdout)
    if mapped.exit_code == 0:
        assert payload["graph_evidence"]["approvable"] is False
        assert payload["graph_evidence"]["unresolved_focus"] == ["target"]
        assert not payload["graph_evidence"]["isolated_targets"]
    else:
        _assert_resource_error(payload["error"])
    assert planned.exit_code != 0
    assert not (repo / ".amu/contract.json").exists()


def test_refresh_impact_resource_failure_cannot_claim_removed_relationships(
    bounded_provider, tmp_path, monkeypatch
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    state.write_json("map.json", _saved_pre_edit_map(), str(repo))
    monkeypatch.setenv("AMU_RESOURCE_MODE", "impact")

    result = CliRunner().invoke(cli.app, ["refresh", "--symbol", TARGET, "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert payload["sync"]["error"] is None
    assert payload["complete"] is False
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["graph"] is None
    assert payload["edge_delta"] == {"added": [], "removed": [], "unchanged": 0}
    assert any(call[:2] == ["graph", "impact"] for call in _calls(bounded_provider))


@pytest.mark.parametrize("command", ("sync", "done", "refresh"))
def test_snapshot_resource_failure_propagates_through_completion_commands(
    bounded_provider, tmp_path, monkeypatch, command
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n"})
    monkeypatch.setenv("AMU_RESOURCE_MODE", "snapshot")
    destination, old = _old_snapshot(repo)
    if command == "refresh":
        state.write_json("map.json", _saved_pre_edit_map(), str(repo))
        args = [command, "--symbol", TARGET]
    else:
        state.write_json("map.json", {"map_hash": "report-only-map", "depth": 2, "nodes": []}, str(repo))
        args = [command]
        if command == "done":
            state.write_json("contract.json", {"targets": [], "base_sha": state.git_head(str(repo)), "map_hash": "report-only-map",
                             "phases": [], "frozen_signatures": [], "allowed_files": [], "created_at": "fixture"}, str(repo))

    result = CliRunner().invoke(cli.app, [*args, "--json", "--repo", str(repo)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 3
    assert destination.read_bytes() == old
    sync = state.read_json("sync-state.json", str(repo))
    _assert_resource_error(sync["error"])
    assert sync["code_cache_reused"] is False
    if command == "sync":
        _assert_resource_error(payload["error"])
    else:
        assert payload["complete"] is False
    if command == "refresh":
        _assert_resource_error(payload["error"])
        assert payload["graph"] is None
        assert payload["edge_delta"] == {"added": [], "removed": [], "unchanged": 0}
        assert not any(call[:2] == ["graph", "impact"] for call in _calls(bounded_provider))
        assert state.read_json("refresh-state.json", str(repo))["targets"][TARGET]["complete"] is False


def test_failed_snapshot_retains_last_successful_metadata_and_cannot_be_reused_as_code_cache(
    bounded_provider, tmp_path, monkeypatch
):
    repo = _committed_repo(tmp_path, {"pkg/target.py": "def target(): return 1\n", "docs/api.md": "# API\nOriginal.\n"})
    state.write_json("map.json", {"depth": 2}, str(repo))
    first = cli._sync_state(str(repo))
    assert not first["error"]
    original_hash = graph_refresh.snapshot_hash(str(repo))
    (repo / "pkg/target.py").write_text("def target(): return 2\n")
    monkeypatch.setenv("AMU_RESOURCE_MODE", "snapshot")

    failed = cli._sync_state(str(repo))

    _assert_resource_error(failed["error"])
    assert graph_refresh.snapshot_hash(str(repo)) == original_hash
    prior = failed.get("prior_snapshot")
    assert isinstance(prior, dict), "a failed attempt must not associate old snapshot bytes with new source metadata"
    for key in ("sha", "worktree", "snapshot_hash", "snapshot_lines", "provider_version", "graph_depth", "snapshot_ref"):
        assert prior[key] == first[key]
    (repo / "docs/api.md").write_text("# API\nChanged prose after failed graph capture.\n")
    before = len(_calls(bounded_provider))
    still_failed = cli._sync_state(str(repo), allow_doc_cache=True)
    _assert_resource_error(still_failed["error"])
    assert still_failed["code_cache_reused"] is False
    assert any(call[:2] == ["graph", "snapshot"] for call in _calls(bounded_provider)[before:])
