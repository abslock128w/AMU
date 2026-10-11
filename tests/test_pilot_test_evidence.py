"""A successful pytest process must execute a passing test before gating a pilot."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from amu import cli, lifecycle, process, state
from tests.test_lifecycle_gates import _write_approved_lifecycle


def _pytest_command(*args: str) -> str:
    return shlex.join([sys.executable, "-m", "pytest", *args])


@pytest.mark.parametrize("source,args,counts", [
    ("import pytest\ndef test_optional():\n    pytest.skip('not available')\n", ["-q"], {"skipped": 1}),
    ("import pytest\n@pytest.mark.xfail\ndef test_expected_failure():\n    assert False\n", ["-q"], {"xfailed": 1}),
    ("def test_only_collected():\n    assert True\n", ["-q", "--collect-only"], None),
    ("def test_hidden_summary():\n    assert True\n", ["-qq"], None),
])
def test_pytest_zero_exit_without_passing_evidence_is_not_green(tmp_path, source, args, counts):
    (tmp_path / "test_project.py").write_text(source, encoding="utf-8")

    result = cli._run_tests(str(tmp_path), [], {"test_cmd": _pytest_command(*args)})

    assert result["returncode"] == 0, result
    assert result["passed"] is False, result
    assert result["evidence"]["passing_tests_verified"] is False
    assert result["evidence"]["counts"] == counts
    assert "passing" in result["summary"]
    assert "pytest -q" in result["summary"]


@pytest.mark.parametrize("extra_source,counts", [
    ("", {"passed": 1}),
    ("\nimport pytest\ndef test_optional():\n    pytest.skip('not available')\n", {"passed": 1, "skipped": 1}),
])
def test_pytest_executed_pass_can_green_with_optional_skips(tmp_path, extra_source, counts):
    (tmp_path / "test_project.py").write_text(
        "def test_required():\n    assert True\n" + extra_source, encoding="utf-8",
    )

    result = cli._run_tests(str(tmp_path), [], {"test_cmd": _pytest_command("-q")})

    assert result["passed"] is True, result
    assert result["returncode"] == 0
    assert result["evidence"]["counts"] == counts
    assert result["evidence"]["passing_tests_verified"] is True


@pytest.mark.parametrize("stdout,expected", [
    ("the setup passed; no assertions ran\n", None),
    ("1 passed in 0.01s\npost-session diagnostic\n", None),
    ("this is not pytest: 1 passed in 0.01s\n", None),
    ("1 passed, 1 passed in 0.01s\n", None),
    ("\x1b[32m================ 2 passed, 1 warning in 0.01s ================\x1b[0m\n", {"passed": 2, "warnings": 1}),
])
def test_only_an_interpretable_final_pytest_summary_supplies_evidence(tmp_path, monkeypatch, stdout, expected):
    monkeypatch.setattr(cli.process, "run", lambda *_args, **_kwargs: process.Result(
        returncode=0, stdout=stdout.encode("utf-8"), stderr=b"", error=None,
        stdout_bytes=len(stdout.encode("utf-8")), stderr_bytes=0,
    ))

    result = cli._run_tests(str(tmp_path), [], {"test_cmd": _pytest_command("-q")})

    assert result["passed"] is bool(expected)
    assert result["evidence"]["counts"] == expected


def test_pytest_failure_keeps_its_real_returncode_and_counts(tmp_path):
    (tmp_path / "test_project.py").write_text(
        "def test_one_pass():\n    assert True\n\ndef test_failure():\n    assert False\n", encoding="utf-8",
    )

    result = cli._run_tests(str(tmp_path), [], {"test_cmd": _pytest_command("-q")})

    assert result["returncode"] == 1
    assert result["passed"] is False
    assert result["evidence"]["counts"] == {"failed": 1, "passed": 1}
    assert result["evidence"]["passing_tests_verified"] is True


def test_build_and_other_runners_keep_actual_zero_exit_semantics(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.process, "run", lambda *_args, **_kwargs: process.Result(
        returncode=0, stdout=b"finished\n", stderr=b"", error=None,
        stdout_bytes=9, stderr_bytes=0,
    ))

    for result in (
        cli._run_build(str(tmp_path), {"build_cmd": _pytest_command().replace("-m pytest", "-m compileall")}),
        cli._run_tests(str(tmp_path), [], {"test_cmd": "npm test"}),
        cli._run_tests(str(tmp_path), [], {"test_cmd": "go test ./..."}),
    ):
        assert result["passed"] is True
        assert "evidence" not in result


def test_all_skipped_real_pytest_cannot_advance_or_complete(fake_entire, tmp_path, monkeypatch):
    _write_approved_lifecycle(tmp_path)
    (tmp_path / "test_project.py").write_text(
        "import pytest\ndef test_optional():\n    pytest.skip('not available')\n", encoding="utf-8",
    )
    subprocess.run(["git", "add", "test_project.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=AMU Test", "-c", "user.email=amu@example.test", "commit", "-qm", "test fixture"],
        cwd=tmp_path, check=True,
    )
    state.write_json("config.json", {**state.config(str(tmp_path)), "test_cmd": _pytest_command("-q")}, str(tmp_path))
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: {"files": []})
    monkeypatch.setattr(cli, "_run_build", lambda *_args, **_kwargs: None)

    checked = CliRunner().invoke(cli.app, ["check", "--json", "--repo", str(tmp_path)])

    payload = json.loads(checked.stdout)
    assert checked.exit_code == 1, payload
    assert payload["tests"]["returncode"] == 0
    assert payload["tests"]["passed"] is False
    saved = state.read_json("state.json", str(tmp_path))
    assert saved["history"][-1]["tests"]["evidence"]["counts"] == {"skipped": 1}
    assert not any(row["green"] for row in lifecycle.phase_statuses(
        state.read_json("contract.json", str(tmp_path)), saved, lifecycle.capture_source(str(tmp_path)),
    ))
    closed = CliRunner().invoke(cli.app, ["done", "--json", "--repo", str(tmp_path)])
    assert closed.exit_code == 1
    assert "successful current check evidence" in json.loads(closed.stdout)["error"]
