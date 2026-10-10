"""Declared test/build commands cannot turn incomplete output into green evidence."""

from __future__ import annotations

import shlex
import sys

import pytest

from amu import cli


def _declared_runner(tmp_path, source, kind="pytest"):
    executable = tmp_path / ("pytest" if kind == "pytest" else "python-build")
    executable.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    executable.chmod(0o755)
    command = shlex.join([str(executable), "-q"] if kind == "pytest"
                         else [str(executable), "-m", "compileall"])
    if kind == "pytest":
        return lambda: cli._run_tests(str(tmp_path), [], {"test_cmd": command})
    return lambda: cli._run_build(str(tmp_path), {"build_cmd": command})


@pytest.mark.parametrize("kind", ["pytest", "build"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_runner_overflow_rejects_otherwise_successful_summary(tmp_path, monkeypatch, kind, stream):
    monkeypatch.setattr(cli, "RUNNER_STDOUT_LIMIT", 4096)
    monkeypatch.setattr(cli, "RUNNER_STDERR_LIMIT", 4096)
    run = _declared_runner(tmp_path, (
        f"import sys\nsys.{stream}.write('x' * 5120 + '\\n')\n"
        "print('1 passed in 0.01s')\n"
    ), kind)

    result = run()

    assert result["passed"] is False, result
    assert result["returncode"] == 125
    assert "output byte limit" in result["summary"]
    assert result.get("evidence", {}).get("passing_tests_verified") is not True
    assert "1 passed" not in result["summary"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_exact_output_byte_boundary_keeps_complete_passing_evidence(tmp_path, monkeypatch, stream):
    monkeypatch.setattr(cli, "RUNNER_STDOUT_LIMIT", 4096)
    monkeypatch.setattr(cli, "RUNNER_STDERR_LIMIT", 4096)
    summary = "\n1 passed in 0.01s\n"
    output = "x" * (4096 - len(summary)) + summary if stream == "stdout" else "x" * 4096
    source = f"import sys\nsys.{stream}.write({output!r})\n"
    if stream == "stderr":
        source += f"sys.stdout.write({summary!r})\n"

    result = _declared_runner(tmp_path, source)()

    assert result["returncode"] == 0
    assert result["passed"] is True
    assert result["evidence"]["counts"] == {"passed": 1}


@pytest.mark.parametrize("kind", ["pytest", "build"])
def test_timeout_discards_already_printed_successful_summary(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(cli, "RUNNER_TIMEOUT_SECONDS", 0.2)
    run = _declared_runner(tmp_path, (
        "import time\nprint('1 passed in 0.01s', flush=True)\ntime.sleep(5)\n"
    ), kind)

    result = run()

    assert result["returncode"] == 124
    assert result["passed"] is False
    assert "timed out" in result["summary"]
    assert result.get("evidence", {}).get("passing_tests_verified") is not True
    assert "1 passed" not in result["summary"]
