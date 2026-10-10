"""Line-impact adapters share the service and preserve the MCP authority boundary."""

import inspect
import json
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from amu import cli, mcp_access, mcp_server


def _packet():
    return {
        "schema_version": 1, "operation": "proposed-change-impact", "status": "inspectable",
        "target": {"file": "api.py", "symbol": "public", "range": {"start": 1, "end": 2}},
        "assessment": {"safe_to_change": None, "provider_reported_coverage_complete": True, "reasons": []},
        "impact": {"rows": [], "omitted": 0},
    }


def test_cli_impact_delegates_once_to_shared_service_without_other_actions(monkeypatch, tmp_path):
    from amu.services import impact

    calls = []
    monkeypatch.setattr(impact, "execute", lambda *args: calls.append(args) or _packet())
    monkeypatch.setattr(cli.state, "write_json", lambda *_args, **_kwargs: pytest.fail("adapter must not save state"))
    result = CliRunner().invoke(cli.app, [
        "impact", "--file", "api.py", "--line", "2", "--proposed-change", "raise on invalid input",
        "--depth", "3", "--change", "signature", "--repo", str(tmp_path), "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _packet()
    assert calls == [(str(tmp_path), impact.ImpactRequest("api.py", 2, "raise on invalid input", 3, "signature"))]
    assert not (tmp_path / ".amu").exists()


def test_cli_impact_preserves_typed_input_error(monkeypatch, tmp_path):
    from amu.services import impact

    def refused(*_args):
        raise impact.ImpactInputError("invalid line for source")

    monkeypatch.setattr(impact, "execute", refused)
    result = CliRunner().invoke(cli.app, [
        "impact", "--file", "api.py", "--line", "1", "--proposed-change", "change defaults",
        "--repo", str(tmp_path), "--json",
    ])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"error": "invalid line for source", "exit": 2}


def test_cli_impact_requires_file_line_and_proposed_intent(tmp_path):
    for options in ([], ["--file", "api.py"], ["--file", "api.py", "--line", "1"]):
        result = CliRunner().invoke(cli.app, ["impact", *options, "--repo", str(tmp_path), "--json"])
        assert result.exit_code == 2


def test_human_impact_output_does_not_claim_the_proposal_is_safe(monkeypatch, tmp_path):
    from amu.services import impact

    monkeypatch.setattr(impact, "execute", lambda *_args: _packet())
    result = CliRunner().invoke(cli.app, [
        "impact", "--file", "api.py", "--line", "2", "--proposed-change", "fix input validation",
        "--repo", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert "does not establish that an edit is safe" in result.stdout
    assert "not executed" in result.stdout


def test_impact_mcp_forwards_one_explicit_cli_request(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(_packet()).encode(), stderr=b"", returncode=0, error=None)

    monkeypatch.setattr(mcp_server.process, "run", run)
    proposal = "return '; touch never-executed' after validation"
    result = mcp_server.amu_impact("api space.py", 2, proposal, 3, "signature", repo=str(tmp_path))
    assert json.loads(result) == _packet()
    assert calls[0][0] == [
        sys.executable, "-I", "-m", "amu.cli", "impact", "--file=api space.py", "--line", "2",
        f"--proposed-change={proposal}", "--depth", "3", "--change", "signature", "--repo", str(tmp_path), "--json",
    ]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["stdout_limit"] == mcp_server.CLI_STDOUT_LIMIT


def test_mcp_free_text_and_file_are_not_reinterpreted_as_cli_options(monkeypatch, tmp_path):
    from amu.services import impact

    calls = []
    monkeypatch.setattr(impact, "execute", lambda *args: calls.append(args) or _packet())

    def invoke(*args, repo):
        result = CliRunner().invoke(cli.app, [*args, "--repo", repo, "--json"])
        assert result.exit_code == 0, result.output
        return result.stdout

    monkeypatch.setattr(mcp_server, "_cli", invoke)
    assert json.loads(mcp_server.amu_impact("--option.py", 2, "--help", repo=str(tmp_path))) == _packet()
    assert calls[0][1].file == "--option.py"
    assert calls[0][1].proposed_change == "--help"


@pytest.mark.parametrize("field,value", [
    ("line", True), ("line", False), ("line", "1"), ("line", 1.0), ("line", 0), ("line", -1),
    ("depth", True), ("depth", "2"), ("depth", 2.0), ("depth", 0), ("depth", 4),
    ("file", True), ("file", None), ("file", ""), ("file", "a\x00.py"), ("file", "x" * 4097),
    ("proposed_change", None), ("proposed_change", True), ("proposed_change", " "),
    ("proposed_change", "x\x00y"), ("proposed_change", "😀" * 2049), ("proposed_change", "\ud800"),
    ("change_kind", False), ("change_kind", "invented"),
])
def test_mcp_impact_rejects_malformed_scalars_before_dispatch(monkeypatch, field, value):
    monkeypatch.setattr(mcp_server.process, "run", lambda *_args, **_kwargs: pytest.fail("CLI must not dispatch"))
    arguments = {"file": "api.py", "line": 1, "proposed_change": "change validation", "repo": "/unused"}
    arguments[field] = value
    result = json.loads(mcp_server.amu_impact(**arguments))
    assert result["exit"] == 2


def test_impact_is_inspection_only_with_required_repo_and_proposal():
    assert "amu_impact" in mcp_access.INSPECTION_TOOLS
    assert mcp_server.TOOLS["amu_impact"][0] is mcp_server.amu_impact
    signature = inspect.signature(mcp_server.amu_impact)
    assert signature.parameters["repo"].kind is inspect.Parameter.KEYWORD_ONLY
    for name in ("repo", "file", "line", "proposed_change"):
        assert signature.parameters[name].default is inspect.Parameter.empty


def test_inspection_impact_denies_other_roots_and_sanitizes_child_environment(monkeypatch, tmp_path, capsys):
    pytest.importorskip("pydantic")
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    child = allowed / "child"
    child.mkdir(parents=True)
    outside.mkdir()
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "fake-sensitive-model-key")
    monkeypatch.setenv("AMU_PROVIDER", "openai-compatible")

    def run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout=b"{}", stderr=b"", returncode=0, error=None)

    monkeypatch.setattr(mcp_server.process, "run", run)
    wrapped = mcp_server._strict_mcp_tool(mcp_server.amu_impact, mcp_access.MCPAccessPolicy([str(allowed)]))
    for invalid in (outside, child):
        refused = json.loads(wrapped("api.py", 1, "private proposal", repo=str(invalid)))
        assert refused["error_kind"] == "repository_not_allowed"
    assert not calls
    assert wrapped("api.py", 1, "private proposal", repo=str(allowed)) == "{}"
    assert calls[0]["env"]["AMU_DISABLE_MODELS"] == "1"
    assert "OPENAI_API_KEY" not in calls[0]["env"] and "AMU_PROVIDER" not in calls[0]["env"]
    assert "private proposal" not in capsys.readouterr().err


def test_inspection_impact_withholds_oversized_protocol_result(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    monkeypatch.setattr(mcp_server, "MCP_RESPONSE_LIMIT", 1024)
    monkeypatch.setattr(mcp_server, "_cli", lambda *_args, **_kwargs: json.dumps({"data": "private response" * 100}))
    wrapped = mcp_server._strict_mcp_tool(mcp_server.amu_impact, mcp_access.MCPAccessPolicy([str(tmp_path)]))
    result = wrapped("api.py", 1, "change validation", repo=str(tmp_path))
    assert json.loads(result)["error_kind"] == "response_limit"
    assert "private response" not in result
