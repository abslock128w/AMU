"""Fixture CLI adapters preserve explicit execution and named-case boundaries."""

import json

import pytest
from typer.testing import CliRunner

from amu import cli, fixture_verification, process


def _forbid_process(monkeypatch):
    monkeypatch.setattr(process, "run", lambda *_args, **_kwargs: pytest.fail("execution was not requested"))


@pytest.mark.parametrize("case", [None, "wrong-key"])
def test_example_fixture_delegates_export_without_execution(monkeypatch, tmp_path, case):
    _forbid_process(monkeypatch)
    output = str(tmp_path / "exported")
    calls = []
    packet = {"status": "exported", "output": output, "execution_performed": False}
    monkeypatch.setattr(fixture_verification, "export_fixture", lambda *args: calls.append(args) or packet)
    options = ["--case", case] if case else []
    result = CliRunner().invoke(cli.app, ["example-fixture", "--output", output, *options, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == packet
    assert calls == [(output, case)]


def test_example_fixture_exports_real_reviewable_files_without_execution(monkeypatch, tmp_path):
    _forbid_process(monkeypatch)
    output = tmp_path / "exported"
    result = CliRunner().invoke(cli.app, ["example-fixture", "--output", str(output), "--case", "corrected", "--json"])
    assert result.exit_code == 0, result.output
    packet = json.loads(result.stdout)
    assert packet["cases"] == ["corrected"] and packet["execution_performed"] is False
    assert (output / "cache_case.py").is_file()
    assert [path.name for path in (output / "patches").iterdir()] == ["corrected.diff"]


def test_verify_fixture_default_is_nonexecuting_and_returns_exit_two(monkeypatch):
    _forbid_process(monkeypatch)
    result = CliRunner().invoke(cli.app, ["verify-fixture", "--case", "wrong-key", "--json"])
    assert result.exit_code == 2, result.output
    packet = json.loads(result.stdout)
    assert packet["execution_requested"] is False and packet["execution_performed"] is False
    assert packet["status"] == "execution_authorization_required"


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_verify_fixture_forwards_explicit_opt_in_and_result_exit(monkeypatch, exit_code):
    calls = []
    packet = {"case": "corrected", "status": "passed", "execution_performed": True, "exit_code": exit_code}
    monkeypatch.setattr(fixture_verification, "verify_fixture", lambda *args: calls.append(args) or packet)
    result = CliRunner().invoke(cli.app, ["verify-fixture", "--case", "corrected", "--execute-reviewed-fixture", "--json"])
    assert result.exit_code == exit_code, result.output
    assert json.loads(result.stdout) == packet
    assert calls == [("corrected", True)]


@pytest.mark.parametrize("command", [
    ["example-fixture", "--output", "relative"],
    ["example-fixture", "--output", "/unused", "--case", "/tmp/program.py"],
    ["verify-fixture", "--case", "/tmp/program.py", "--execute-reviewed-fixture"],
])
def test_fixture_commands_reject_arbitrary_inputs_without_execution(monkeypatch, command):
    _forbid_process(monkeypatch)
    result = CliRunner().invoke(cli.app, [*command, "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["exit"] == 2
