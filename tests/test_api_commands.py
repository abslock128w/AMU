"""Exercise the registered commands and interactive parser boundaries."""

import json

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from amu import api_manifest, benchmark, cli, router
from tests.test_repl_dispatch import _session


def _write_json(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def _edge():
    return {"source": "same_file.py#public", "relation": "CALLS", "target": "same_file.py#helper"}


def test_api_manifest_is_registered_independently_of_model_preview(monkeypatch, tmp_path):
    registered = get_command(cli.app).commands
    assert "api-manifest" in registered
    assert registered["model-preview"].callback.__name__ == "model_preview_cmd"
    calls = []

    def report(repo, file, base):
        calls.append((repo, file, base))
        return {"changes": [], "unknowns": ["coverage is partial"], "complete": False}

    monkeypatch.setattr(api_manifest, "report", report)
    result = CliRunner().invoke(cli.app, [
        "api-manifest", "--file", "api space.py", "--base", "HEAD~1", "--repo", str(tmp_path), "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["complete"] is False
    assert calls == [(str(tmp_path), "api space.py", "HEAD~1")]


def test_default_public_api_benchmark_runs_packaged_fixtures_without_repo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["benchmark", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["counts"]["tp"] > 0
    assert report["fixture_version"]
    assert report["tool_version"]
    assert any("synthetic" in limitation for limitation in report["limitations"])


def test_benchmark_human_output_is_a_score_summary_not_entire_fixture_sources():
    result = CliRunner().invoke(cli.app, ["benchmark"])
    assert result.exit_code == 0, result.output[:2000]
    assert "public-api-v1" in result.stdout
    assert "TP" in result.stdout and "precision" in result.stdout
    assert "synthetic" in result.stdout.lower()
    assert "signature_fingerprint" not in result.stdout
    assert len(result.stdout) < 3000


def test_relation_benchmark_accepts_list_without_implying_completeness(tmp_path):
    path = _write_json(tmp_path / "edges.json", [])
    result = CliRunner().invoke(cli.app, ["benchmark", "--relations", path, "--json"])
    assert result.exit_code == 0, result.output
    raw = json.loads(result.stdout)["reports"]["raw_provider"]
    assert raw["counts"]["observed"] == 0
    assert raw["counts"]["tn"] == 0
    assert raw["metrics"]["precision"] is None


def test_relation_benchmark_envelope_preserves_coverage_and_abstentions(monkeypatch, tmp_path):
    path = _write_json(tmp_path / "edges.json", {
        "edges": [], "completeness": {"complete": False}, "abstentions": [_edge()],
    })
    calls = []

    def run(observed, **kwargs):
        calls.append((observed, kwargs))
        return {"reports": {}}

    monkeypatch.setattr(benchmark, "run_relations", run)
    result = CliRunner().invoke(cli.app, ["benchmark", "--relations", path, "--json"])
    assert result.exit_code == 0, result.output
    assert calls[0][0] == []
    assert calls[0][1]["completeness"] == {"complete": False}
    assert calls[0][1]["abstentions"] == [_edge()]


def test_relation_benchmark_validation_has_independent_completeness(tmp_path):
    raw = _write_json(tmp_path / "raw.json", {"edges": [], "completeness": True})
    validated = _write_json(tmp_path / "validated.json", [])
    result = CliRunner().invoke(cli.app, [
        "benchmark", "--relations", raw, "--validated", validated, "--json",
    ])
    assert result.exit_code == 0, result.output
    reports = json.loads(result.stdout)["reports"]
    assert reports["raw_provider"]["counts"]["tn"] > 0
    assert reports["amu_source_validated"]["counts"]["tn"] == 0
    assert reports["amu_source_validated"]["completeness"] is None


def test_relation_benchmark_validation_preserves_own_envelope_metadata(monkeypatch, tmp_path):
    raw = _write_json(tmp_path / "raw.json", {"edges": [], "completeness": True})
    validated = _write_json(tmp_path / "validated.json", {
        "edges": [], "completeness": False, "abstentions": [_edge()],
    })
    calls = []
    monkeypatch.setattr(benchmark, "run_relations", lambda *args, **kwargs: calls.append((args, kwargs)) or {})
    result = CliRunner().invoke(cli.app, [
        "benchmark", "--relations", raw, "--validated", validated, "--json",
    ])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["completeness"] is True
    assert calls[0][1]["validation_completeness"] is False
    assert calls[0][1]["validation_abstentions"] == [_edge()]


@pytest.mark.parametrize("option", ["--relations", "--validated", "--corpus"])
def test_relation_benchmark_rejects_explicit_blank_paths(option):
    result = CliRunner().invoke(cli.app, ["benchmark", option, "", "--json"])
    assert result.exit_code == 2, result.output
    assert "must not be empty" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("value", [
    None, "not an edge list", {}, {"edges": "not a list"}, {"edges": [], "unknown": True},
    {"edges": [], "completeness": "yes"}, {"edges": [], "completeness": {"complete": "yes"}},
    {"edges": [], "abstentions": "not a list"}, [1], [{"source": "missing-fields"}],
])
def test_relation_benchmark_rejects_malformed_input_as_json(tmp_path, value):
    path = _write_json(tmp_path / "edges.json", value)
    result = CliRunner().invoke(cli.app, ["benchmark", "--relations", path, "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["exit"] == 2
    assert "error" in json.loads(result.stdout)


def test_relation_benchmark_requires_raw_input_for_validation(tmp_path):
    path = _write_json(tmp_path / "edges.json", [])
    result = CliRunner().invoke(cli.app, ["benchmark", "--validated", path, "--json"])
    assert result.exit_code == 2
    assert "requires --relations" in json.loads(result.stdout)["error"]


def test_relation_benchmark_rejects_missing_or_symlink_input(tmp_path):
    actual = tmp_path / "actual.json"
    actual.write_text("[]")
    link = tmp_path / "link.json"
    link.symlink_to(actual)
    for path in (tmp_path / "missing.json", link):
        result = CliRunner().invoke(cli.app, ["benchmark", "--relations", str(path), "--json"])
        assert result.exit_code == 2, result.output
        assert "error" in json.loads(result.stdout)


@pytest.mark.parametrize("prefix", ["/", ""])
def test_new_commands_route_without_symbol_lookup(prefix):
    def unexpected(_):
        pytest.fail("a command must not search symbols")

    assert router.route(f"{prefix}api-manifest --file api.py", unexpected)["intent"] == "api-manifest"
    assert router.route(f"{prefix}benchmark --json", unexpected)["intent"] == "benchmark"


def test_new_commands_repl_shares_cli_parser_and_keeps_session_alive(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(api_manifest, "report", lambda *args: calls.append(args) or {"changes": [], "unknowns": []})
    monkeypatch.setattr(benchmark, "run_public_api", lambda **_kwargs: {"fixture_version": "test"})
    _session(monkeypatch, tmp_path, [
        '/api-manifest --file "api space.py" --json', "/benchmark --json", "/api-manifest", "/help",
    ])
    output = capsys.readouterr()
    assert calls == [(str(tmp_path), "api space.py", "HEAD")]
    assert '"fixture_version": "test"' in output.out
    assert "Missing option" in output.err
    assert "AMU command guide" in output.err
