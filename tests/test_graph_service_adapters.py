"""Actual interactive map dispatch and repository normalization use core."""

import json

import pytest
from typer.testing import CliRunner

from amu import cli, state
from tests.test_graph_service_parity import _Adapter, _repo, _setup_provider
from tests.test_repl_dispatch import _session


@pytest.fixture
def provider(monkeypatch):
    return _setup_provider(monkeypatch)


@pytest.mark.parametrize("argument,file,depth,change", [
    ("target.py", "target.py", 2, "signature"),
    ("target.py#target --depth 3 --change body", None, 3, "body"),
    ("--symbol target.py#target --depth=1", None, 1, "signature"),
    ('"file with space.py" --change body', "file with space.py", 2, "body"),
])
def test_actual_map_session_matches_cli_parser(monkeypatch, tmp_path, provider, argument, file, depth, change):
    repo = _repo(tmp_path)
    if file == "file with space.py":
        (repo / file).write_text("def target(): return 1\n")
        provider["symbols"][0]["file_path"] = file
        provider["impact"]["focus"]["file_path"] = file
    expected = _Adapter("cli", repo, monkeypatch).map(file=file, depth=depth, change=change)[1]
    outputs = []
    monkeypatch.setattr(cli, "_out", lambda out, *_args: outputs.append(out))
    monkeypatch.setattr(cli.render, "render_map", lambda _value: None)
    _session(monkeypatch, repo, [f"/map {argument}"])
    assert outputs == [expected]
    assert state.read_json("map.json", str(repo)) == expected


def test_invalid_map_options_keep_session_alive_and_preserve_prior_map(monkeypatch, tmp_path, provider, capsys):
    repo = _repo(tmp_path)
    _Adapter("cli", repo, monkeypatch).map()
    previous = (repo / ".amu/map.json").read_bytes()
    _session(monkeypatch, repo, ["/map --symbol target.py#target --bogus", "/help"])
    output = capsys.readouterr()
    assert "No such option" in output.err and "AMU command guide" in output.err
    assert (repo / ".amu/map.json").read_bytes() == previous


@pytest.mark.parametrize("command,args", [("map", ["--symbol", "target.py#target"]), ("sync", [])])
def test_graph_commands_retain_explicit_relative_repo(monkeypatch, tmp_path, provider, command, args):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo.parent)
    result = CliRunner().invoke(cli.app, [command, *args, "--repo", repo.name, "--json"])
    assert result.exit_code == 0, result.output
    assert not json.loads(result.stdout).get("error")
