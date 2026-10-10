"""Mapping and snapshot evidence agree across the shared service and adapters.

Only the external graph provider is faked. Git, source checking, freshness,
serialization and the CLI parser remain real. MCP retains its bounded process
boundary; the test bridge runs that exact CLI command in-process.
"""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from amu import cli, entire, graph_refresh, mcp_server, state

MAP_ADAPTERS = ["service", "cli", "repl", "mcp"]
SYNC_ADAPTERS = ["service", "cli", "repl"]
TARGET = "target.py#target"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "target.py").write_text(
        "def target():\n    return 1\n\ndef helper():\n    return 2\n", encoding="utf-8")
    (repo / "consumer.py").write_text(
        "from target import target\n\ndef consumer():\n    return target()\n", encoding="utf-8")
    (repo / "docs/guide.md").write_text("# Guide\n\nInitial prose.\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "parity@example.invalid"],
                 ["config", "user.name", "Parity test"], ["add", "."],
                 ["commit", "-qm", "baseline"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


def _commit(repo: Path, path: str) -> None:
    subprocess.run(["git", "add", path], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "fixture update"], cwd=repo,
                   check=True, capture_output=True)


def test_mcp_bridge_does_not_intercept_nested_bounded_git_status(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _setup_provider(monkeypatch)
    real_run = mcp_server.process.run
    commands = []

    def observed_run(command, **options):
        commands.append(command)
        return real_run(command, **options)

    monkeypatch.setattr(mcp_server.process, "run", observed_run)
    code, payload = _Adapter("mcp", repo, monkeypatch).map()
    assert code == 0 and payload["graph_evidence"]["worktree"]["stable"] is True
    assert any(command[0] == "git" and "status" in command for command in commands)


def _impact() -> dict:
    return {
        "focus": {"id": "fixture-target", "name": "target", "qualified_name": "target",
                  "kind": "function", "file_path": "target.py", "start_line": 1, "end_line": 2},
        "callers": {"total": 1, "entries": [
            {"endpoint": {"name": "consumer", "qualified_name": "consumer",
                          "file_path": "consumer.py", "start_line": 3, "end_line": 4},
             "relation": "CALLS", "depth": 1,
             "call_site": {"file_path": "consumer.py", "line": 4}},
        ]},
        **{name: {"total": 0, "entries": []}
           for name in ("callees", "type_consumers", "data_flows")},
        "stats": {"completeness_level": "complete"},
    }


def _setup_provider(monkeypatch):
    """Mutable recorded graph responses, with real source/freshness validation."""
    observed = {
        "impact": _impact(), "inventory_error": None,
        "symbols": [{"record_type": "symbol", "id": "fixture-target", "name": "target",
                     "qualified_name": "target", "file_path": "target.py", "kind": "function"}],
        "capabilities": {"unresolved_patterns": ["reflection", "dynamic_dispatch"]},
        "index": {"counts": {"files": 3, "symbols": 3, "relations": {"CALLS": 1}}},
        "snapshot_error": None, "calls": [],
    }
    monkeypatch.setattr(state, "now_iso", lambda: "2026-10-09T00:00:00Z")
    monkeypatch.setattr(entire, "available", lambda: True)
    monkeypatch.setattr(entire, "graph_available", lambda: True)
    monkeypatch.setattr(entire, "graph_version", lambda: "graph-parity-1")
    monkeypatch.setattr(entire, "symbols_result", lambda _repo: (
        deepcopy(observed["symbols"]), observed["inventory_error"]))
    monkeypatch.setattr(entire, "capabilities", lambda _repo: deepcopy(observed["capabilities"]))

    def impact(repo, symbol, depth, **options):
        observed["calls"].append(("impact", repo, symbol, depth, options))
        return deepcopy(observed["impact"])

    def index(repo):
        observed["calls"].append(("index", repo))
        return deepcopy(observed["index"])

    def snapshot(repo, output, *, worktree=False):
        observed["calls"].append(("snapshot", repo, output, worktree))
        if observed["snapshot_error"]:
            return {"error": observed["snapshot_error"]}
        Path(output).write_text('{"record_type":"file","path":"target.py"}\n', encoding="utf-8")
        return {"lines": 1, "path": output}

    monkeypatch.setattr(entire, "impact_json", impact)
    monkeypatch.setattr(entire, "index", index)
    monkeypatch.setattr(entire, "snapshot", snapshot)
    return observed


@pytest.fixture
def provider(monkeypatch):
    return _setup_provider(monkeypatch)


class _Adapter:
    def __init__(self, kind: str, repo: Path, monkeypatch):
        self.kind, self.repo = kind, str(repo)
        if kind == "mcp":
            def run(command, *, cwd, timeout, stdout_limit, stderr_limit):
                assert command[:4] == [mcp_server.sys.executable, "-I", "-m", "amu.cli"]
                assert cwd == self.repo and timeout == 600
                assert stdout_limit == mcp_server.CLI_STDOUT_LIMIT
                assert stderr_limit == mcp_server.CLI_STDERR_LIMIT
                result = CliRunner().invoke(cli.app, command[4:])
                return SimpleNamespace(stdout=result.stdout.encode(), stderr=result.stderr.encode(),
                                       returncode=result.exit_code, error=None)
            monkeypatch.setattr(mcp_server, "_cli_process", run)

    def map(self, *, symbol=TARGET, file=None, depth=2, change="signature"):
        if self.kind == "service":
            from amu.services import mapping
            try:
                request = mapping.MapRequest(file=file, symbol=None if file else symbol,
                                             depth=depth, change=change)
                return 0, mapping.execute(self.repo, request)
            except mapping.GraphDataError as exc:
                return 3, {"error": str(exc)}
        if self.kind == "mcp":
            raw = mcp_server.amu_map(file=file, target=None if file else symbol,
                                     depth=depth, change=change, repo=self.repo)
            payload = json.loads(raw)
            return payload.get("exit", 0), payload
        option, target = ("--file", file) if file else ("--symbol", symbol)
        return self._command("map", [option, target, "--depth", str(depth), "--change", change])

    def sync(self, *, allow_doc_cache=False):
        if self.kind == "service":
            from amu.services import graph_sync
            payload = graph_sync.execute(self.repo, allow_doc_cache=allow_doc_cache)
            return 3 if payload["error"] else 0, payload
        assert not allow_doc_cache, "only completion opts into the private docs-only cache"
        return self._command("sync", [])

    def _command(self, operation, arguments):
        if self.kind == "cli":
            result = CliRunner().invoke(cli.app, [operation, *arguments, "--repo", self.repo, "--json"])
            assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
            return result.exit_code, json.loads(result.stdout)
        assert self.kind == "repl"
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            cli._repl_cli_command(operation, shlex.join([*arguments, "--json"]), self.repo)
        payload = json.loads(output.getvalue())
        return payload.get("exit", 3 if payload.get("error") else 0), payload


def test_exact_map_payload_is_identical_for_all_adapters(tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    expected_code, expected = _Adapter("service", repo, monkeypatch).map(depth=3, change="body")
    assert expected_code == 0 and expected["graph_evidence"]["approvable"] is True
    assert expected["graph_evidence"]["baseline_ready"] is True
    assert expected["graph_evidence"]["unverified_python_calls"] == []
    assert expected["graph_evidence"]["impact_coverage"][TARGET]["depth"] == 3
    assert expected["resolution"]["limitations"]
    for kind in MAP_ADAPTERS[1:]:
        code, actual = _Adapter(kind, repo, monkeypatch).map(depth=3, change="body")
        assert code == expected_code and actual == expected
        assert state.read_json("map.json", str(repo)) == expected
    assert all(call[4] == {"file": "target.py", "head": False}
               for call in provider["calls"] if call[0] == "impact")


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
def test_empty_file_inventory_is_explicit_unapprovable_inspection(kind, tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    provider["symbols"] = []
    code, payload = _Adapter(kind, repo, monkeypatch).map(file="target.py")
    assert code == 0
    assert payload["nodes"] == [] and payload["graph_evidence"]["queried_symbols"] == []
    assert payload["graph_evidence"]["approvable"] is False
    assert "no live impact queries" in " ".join(payload["graph_evidence"]["reasons"])
    assert not provider["calls"]


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
def test_failed_inventory_preserves_previous_map(kind, tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    assert adapter.map()[0] == 0
    destination = repo / ".amu/map.json"
    previous = destination.read_bytes()
    provider["inventory_error"] = "captured output exceeded limit"
    provider["calls"].clear()
    code, payload = adapter.map()
    assert code == 3 and "symbols failed" in payload["error"]
    assert destination.read_bytes() == previous
    assert not provider["calls"]


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
@pytest.mark.parametrize("defect", ["ambiguous", "wrong_focus", "truncated", "capabilities"])
def test_provider_uncertainty_cannot_be_promoted_to_exact_baseline(
    kind, defect, tmp_path, monkeypatch, provider,
):
    repo = _repo(tmp_path)
    if defect == "ambiguous":
        provider["impact"].update(disambiguation_required=True,
                                  definitions=[{"file_path": "a.py"}, {"file_path": "b.py"}])
    elif defect == "wrong_focus":
        provider["impact"]["focus"]["file_path"] = "other.py"
    elif defect == "truncated":
        provider["impact"]["callers"]["total"] = 2
    else:
        provider["capabilities"] = {"error": "capability output exceeded limit", "error_type": "output_limit"}
    code, payload = _Adapter(kind, repo, monkeypatch).map()
    assert code == 0 and payload["graph_evidence"]["approvable"] is False
    if defect != "capabilities":
        assert graph_refresh.map_baseline(TARGET, 2, payload).available is False
    assert cli._map_approval_problem(str(repo), payload)


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
@pytest.mark.parametrize("evidence", ["contradicted", "uncertain"])
def test_source_evidence_policy_stays_the_same_in_every_adapter(
    kind, evidence, tmp_path, monkeypatch, provider,
):
    repo = _repo(tmp_path)
    source = ('def consumer():\n    """Example only: target()."""\n    return 0\n'
              if evidence == "contradicted" else
              "def consumer(callback):\n    return callback.target()\n")
    (repo / "consumer.py").write_text(source, encoding="utf-8")
    _commit(repo, "consumer.py")
    site = 2
    entry = provider["impact"]["callers"]["entries"][0]
    entry["call_site"]["line"] = site
    entry["endpoint"].update(start_line=1, end_line=3 if evidence == "contradicted" else 2)
    code, payload = _Adapter(kind, repo, monkeypatch).map()
    assert code == 0
    graph_evidence = payload["graph_evidence"]
    if evidence == "contradicted":
        assert graph_evidence["contradicted_python_calls"]
        assert graph_evidence["approvable"] is False
        assert not any(node["path"] == "consumer.py" for node in payload["nodes"])
    else:
        assert graph_evidence["unverified_python_calls"]
        assert graph_evidence["contradicted_python_calls"] == []
        # Preserve the current explicit policy: source uncertainty discloses
        # its limit but does not invalidate a provider-resolved relation.
        assert graph_evidence["approvable"] is True
        assert any(node["path"] == "consumer.py" and node["confidence"] == "sound"
                   for node in payload["nodes"])


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
def test_source_mutation_during_query_never_overwrites_stable_map(kind, tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    assert adapter.map()[0] == 0
    previous = (repo / ".amu/map.json").read_bytes()
    def mutate(*_args, **_kwargs):
        (repo / "target.py").write_text("def target():\n    return 9\n", encoding="utf-8")
        return deepcopy(provider["impact"])
    monkeypatch.setattr(entire, "impact_json", mutate)
    code, payload = adapter.map()
    assert code == 3 and "worktree changed" in payload["error"]
    assert (repo / ".amu/map.json").read_bytes() == previous


@pytest.mark.parametrize("kind", MAP_ADAPTERS)
@pytest.mark.parametrize("path", ["target.py", "docs/example.py", "docs/guide.md"])
def test_dirty_code_is_inspectable_but_only_inert_prose_preserves_baseline(
    kind, path, tmp_path, monkeypatch, provider,
):
    repo = _repo(tmp_path)
    original = (repo / path).read_text() if (repo / path).exists() else "value = 1\n"
    (repo / path).write_text(original + "\n# revised input\n", encoding="utf-8")
    code, payload = _Adapter(kind, repo, monkeypatch).map()
    assert code == 0 and payload["graph_evidence"]["worktree"]["stable"] is True
    prose = path == "docs/guide.md"
    assert payload["graph_evidence"]["baseline_ready"] is prose
    assert bool(cli._map_approval_problem(str(repo), payload)) is not prose


def test_saved_baseline_requires_exact_symbol_and_depth_after_extraction(tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    code, payload = _Adapter("service", repo, monkeypatch).map(depth=3)
    assert code == 0 and graph_refresh.map_baseline(TARGET, 3, payload).available
    assert not graph_refresh.map_baseline(TARGET, 2, payload).available
    assert not graph_refresh.map_baseline("target.py#helper", 3, payload).available
    assert not graph_refresh.map_baseline("consumer.py#target", 3, payload).available


def test_sync_payload_is_identical_across_shared_service_and_parsers(tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    expected_code, expected = _Adapter("service", repo, monkeypatch).sync()
    assert expected_code == 0 and expected["snapshot_hash"]
    for kind in SYNC_ADAPTERS[1:]:
        code, actual = _Adapter(kind, repo, monkeypatch).sync()
        assert code == expected_code and actual == expected
        assert state.read_json("sync-state.json", str(repo)) == expected
    assert expected["index_ref"] == "HEAD" and expected["snapshot_ref"] == "worktree"
    assert expected["code_cache_reused"] is False
    assert all(call[3] is True for call in provider["calls"] if call[0] == "snapshot")


@pytest.mark.parametrize("kind", SYNC_ADAPTERS)
@pytest.mark.parametrize("failure", ["index", "snapshot"])
def test_failed_sync_preserves_prior_snapshot_without_claiming_current_evidence(
    kind, failure, tmp_path, monkeypatch, provider,
):
    repo = _repo(tmp_path)
    adapter = _Adapter(kind, repo, monkeypatch)
    _code, original = adapter.sync()
    old_snapshot = (repo / ".amu/snapshot.ndjson").read_bytes()
    provider["calls"].clear()
    if failure == "index":
        provider["index"] = {"error": "index output exceeded limit"}
    else:
        provider["snapshot_error"] = "snapshot output exceeded limit"
    code, failed = adapter.sync()
    assert code == 3 and "exceeded limit" in failed["error"]
    assert failed["snapshot_ref"] == "unavailable" and failed["snapshot_lines"] is None
    assert failed["snapshot_hash"] is None and failed["code_cache_reused"] is False
    assert failed["prior_snapshot"]["snapshot_hash"] == original["snapshot_hash"]
    assert (repo / ".amu/snapshot.ndjson").read_bytes() == old_snapshot
    assert state.read_json("sync-state.json", str(repo)) == failed
    readiness = state.read_json("graph-readiness.json", str(repo))
    assert readiness["ready"] is (failure == "snapshot")
    if failure == "index":
        assert not any(call[0] == "snapshot" for call in provider["calls"])


@pytest.mark.parametrize("change", ["prose", "source", "executable_doc", "provider", "depth", "snapshot"])
def test_completion_cache_only_reuses_unchanged_code_configuration_and_snapshot(
    change, tmp_path, monkeypatch, provider,
):
    repo = _repo(tmp_path)
    state.write_json("map.json", {"depth": 2}, str(repo))
    adapter = _Adapter("service", repo, monkeypatch)
    assert adapter.sync()[0] == 0
    provider["calls"].clear()
    (repo / "docs/guide.md").write_text("# Guide\n\nNew prose.\n", encoding="utf-8")
    if change == "source":
        (repo / "target.py").write_text("def target():\n    return 3\n", encoding="utf-8")
    elif change == "executable_doc":
        (repo / "docs/example.py").write_text("example = 2\n", encoding="utf-8")
    elif change == "provider":
        monkeypatch.setattr(entire, "graph_version", lambda: "graph-parity-2")
    elif change == "depth":
        state.write_json("map.json", {"depth": 3}, str(repo))
    elif change == "snapshot":
        (repo / ".amu/snapshot.ndjson").write_text("changed\n", encoding="utf-8")
    code, result = adapter.sync(allow_doc_cache=True)
    assert code == 0
    assert result["code_cache_reused"] is (change == "prose")
    if change == "prose":
        assert provider["calls"] == [] and result["snapshot_ref"] == "code-worktree-cache"
        assert result["current_freshness"]["docs"]["digest"] != result["worktree"]["freshness"]["docs"]["digest"]
    else:
        assert [call[0] for call in provider["calls"]] == ["index", "snapshot"]


def test_explicit_sync_after_prose_edit_never_silently_uses_completion_cache(tmp_path, monkeypatch, provider):
    repo = _repo(tmp_path)
    _Adapter("service", repo, monkeypatch).sync()
    (repo / "docs/guide.md").write_text("# Changed prose\n", encoding="utf-8")
    provider["calls"].clear()
    code, result = _Adapter("cli", repo, monkeypatch).sync()
    assert code == 0 and result["code_cache_reused"] is False
    assert [call[0] for call in provider["calls"]] == ["index", "snapshot"]
