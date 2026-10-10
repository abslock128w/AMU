"""Boundary validation for commands that establish or consume a scope contract."""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli, entire, state


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["map"], "exactly one"),
        (["map", "--file", "a.py", "--symbol", "a.py#f"], "exactly one"),
        (["map", "--file", "../outside.py"], "repository-relative"),
        (["map", "--symbol", "not-a-symbol"], "form path#name"),
        (["map", "--file", "a.py", "--change", "typo"], "invalid --change"),
        (["map", "--file", "a.py", "--depth", "0"], "at least 1"),
    ],
)
def test_map_rejects_ambiguous_or_unsafe_inputs(tmp_path, args, message):
    result = CliRunner().invoke(cli.app, [*args, "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert message in json.loads(result.stdout)["error"]


def test_map_rejects_symlink_path_that_escapes_the_repo(tmp_path):
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    result = CliRunner().invoke(cli.app, ["map", "--file", "escape/x.py", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "repository-relative" in json.loads(result.stdout)["error"]


def test_map_accepts_a_well_formed_repo_relative_symbol(fake_entire, tmp_path):
    _init_repo(tmp_path)
    result = CliRunner().invoke(
        cli.app,
        ["map", "--symbol", "amu/classify.py#classify_consumers", "--json", "--repo", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["root"]["file"] == "amu/classify.py"


def test_map_fails_closed_when_symbol_enumeration_fails(fake_entire, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.setattr(cli.entire, "symbols_result", lambda *_args, **_kwargs: ([], "graph cache unavailable"))

    result = CliRunner().invoke(cli.app, ["map", "--file", "empty.py", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 3
    assert "symbols failed: graph cache unavailable" in json.loads(result.stdout)["error"]
    assert not (tmp_path / ".amu" / "map.json").exists()


def test_map_keeps_a_legitimate_zero_symbol_file_nonblocking(fake_entire, tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "empty.py").write_text("# intentionally no symbols\n")
    monkeypatch.setattr(cli.entire, "symbols_result", lambda *_args, **_kwargs: ([], None))

    result = CliRunner().invoke(cli.app, ["map", "--file", "empty.py", "--json", "--repo", str(tmp_path)])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["root"]["exports"] == 0
    assert payload["nodes"] == []
    assert payload["resolution"]["unknown"] == 0


def test_symbol_adapter_preserves_a_subprocess_error(monkeypatch):
    monkeypatch.setattr(entire, "_run", lambda *_args, **_kwargs: (1, "", "graph cache unavailable"))

    rows, error = entire.symbols_result(".")

    assert rows == []
    assert error == "graph cache unavailable"


def test_docs_rejects_an_unknown_mode(tmp_path):
    result = CliRunner().invoke(cli.app, ["docs", "--mode", "publish", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "invalid docs mode" in json.loads(result.stdout)["error"]


def test_plan_rejects_an_unknown_change_class(tmp_path):
    state.write_json("map.json", {"root": {"file": "a.py"}, "change": "signature", "nodes": [], "map_hash": "h"}, str(tmp_path))

    result = CliRunner().invoke(cli.app, ["plan", "--targets", "a.py#f:typo", "--json", "--repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "invalid target change class" in json.loads(result.stdout)["error"]
