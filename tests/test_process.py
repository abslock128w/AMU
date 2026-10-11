"""Actual local processes exercise bounded IO, not mocked command output."""

from __future__ import annotations

import math
import os
import sys
import time

import pytest

from amu import process


def _run(source: str, **kwargs):
    options = {"timeout": 3, "stdout_limit": 131_072, "stderr_limit": 131_072}
    options.update(kwargs)
    return process.run([sys.executable, "-c", source], **options)


def test_exact_boundaries_are_allowed():
    result = _run("import os; os.write(1, b'x' * 100); os.write(2, b'y' * 101)", stdout_limit=100, stderr_limit=101)
    assert result.error is None
    assert result.returncode == 0
    assert result.stdout == b"x" * 100
    assert result.stderr == b"y" * 101
    assert (result.stdout_bytes, result.stderr_bytes) == (100, 101)


@pytest.mark.parametrize("descriptor", [1, 2])
def test_overflow_terminates_process_before_exit_and_withholds_all_output(descriptor):
    started = time.monotonic()
    result = _run(
        f"import os,time; os.write(1,b'{{}}'); os.write({descriptor}, b'x' * 10001); time.sleep(10)",
        stdout_limit=10_000,
        stderr_limit=10_000,
    )
    assert time.monotonic() - started < 2
    assert (result.returncode, result.error) == (125, "output_limit")
    assert result.stdout == result.stderr == b""
    assert (result.stdout_bytes if descriptor == 1 else result.stderr_bytes) > 10_000


def test_simultaneous_streams_do_not_deadlock():
    result = _run("import os; [(os.write(1,b'o'*4096),os.write(2,b'e'*4096)) for _ in range(32)]")
    assert result.error is None
    assert result.stdout == b"o" * 131_072
    assert result.stderr == b"e" * 131_072


def test_limits_count_bytes_not_unicode_characters():
    result = _run("import os; os.write(1, '\u20ac'.encode() * 4)", stdout_limit=11)
    assert result.error == "output_limit"
    assert result.stdout_bytes == 12
    assert result.stdout == b""


def test_timeout_with_partial_json_withholds_output():
    started = time.monotonic()
    result = _run("import os,time; os.write(1, b'{\"partial\":'); time.sleep(10)", timeout=0.2)
    assert time.monotonic() - started < 2
    assert (result.returncode, result.error) == (124, "timeout")
    assert result.stdout == result.stderr == b""
    assert result.stdout_bytes == 11


def test_command_exit_is_not_runner_failure():
    result = _run("import os; os.write(2,b'failed'); raise SystemExit(7)")
    assert result.returncode == 7
    assert result.error is None
    assert result.stderr == b"failed"


def test_missing_executable():
    result = process.run(["/amu-test/no-such-program"], timeout=1, stdout_limit=10, stderr_limit=10)
    assert (result.returncode, result.error) == (127, "missing")
    assert result.stdout == result.stderr == b""


@pytest.mark.parametrize("timeout", [0, -1, True, None, math.inf, math.nan, 10**1000])
def test_invalid_timeouts_rejected_before_launch(timeout):
    with pytest.raises(ValueError):
        _run("raise RuntimeError('must not run')", timeout=timeout)


@pytest.mark.parametrize("limit", [0, -1, True, None, 1.5])
@pytest.mark.parametrize("name", ["stdout_limit", "stderr_limit"])
def test_invalid_limits_rejected_before_launch(name, limit):
    with pytest.raises(ValueError):
        _run("raise RuntimeError('must not run')", **{name: limit})


def test_streaming_sink_does_not_capture_output():
    chunks = []
    result = _run("import os; os.write(1,b'x'*100000)", stdout_sink=chunks.append, capture_stdout=False)
    assert result.error is None
    assert result.stdout == b""
    assert b"".join(chunks) == b"x" * 100_000
    assert max(map(len, chunks)) <= 65_536
    assert result.stdout_bytes == 100_000


def test_overflow_is_checked_before_sink_receives_chunk():
    chunks = []
    result = _run("import os; os.write(1,b'x'*100)", stdout_limit=10, stdout_sink=chunks.append, capture_stdout=False)
    assert result.error == "output_limit"
    assert sum(map(len, chunks)) <= 10


def test_sink_exception_is_io_failure():
    def fail(chunk):
        raise OSError("disk full")

    result = _run("import os,time; os.write(1,b'hello'); time.sleep(10)", stdout_sink=fail, capture_stdout=False)
    assert (result.returncode, result.error) == (126, "io")
    assert result.stdout == result.stderr == b""
    assert result.stdout_bytes == 5


@pytest.mark.skipif(os.name != "posix", reason="POSIX inherited pipes and process groups")
def test_descendant_held_pipes_obey_original_deadline(tmp_path):
    marker = tmp_path / "descendant-alive"
    source = (
        "import os,time,pathlib; child=os.fork(); "
        "\nif child == 0:\n time.sleep(.5); pathlib.Path(" + repr(str(marker)) + ").write_text('alive'); os._exit(0)"
        "\nos._exit(0)"
    )
    started = time.monotonic()
    result = _run(source, timeout=0.2)
    assert time.monotonic() - started < 2
    assert result.error == "timeout"
    time.sleep(0.4)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("exception", [OSError("write failed"), KeyboardInterrupt()])
def test_sink_failure_or_interruption_tears_down_grandchild(tmp_path, exception):
    marker = tmp_path / "grandchild-alive"
    source = (
        "import os,time,pathlib; child=os.fork(); "
        "\nif child == 0:\n time.sleep(.5); pathlib.Path(" + repr(str(marker)) + ").write_text('alive'); os._exit(0)"
        "\nos.write(1,b'ready'); time.sleep(10)"
    )

    def fail(chunk):
        raise exception

    if isinstance(exception, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            _run(source, stdout_sink=fail, capture_stdout=False)
    else:
        assert _run(source, stdout_sink=fail, capture_stdout=False).error == "io"
    time.sleep(0.6)
    assert not marker.exists()


def test_stdin_is_closed_and_explicit_cwd_and_env_are_used(tmp_path):
    result = _run(
        "import os,sys; assert sys.stdin.buffer.read()==b''; print(os.getcwd()); print(os.environ['AMU_PROCESS_TEST'])",
        cwd=str(tmp_path),
        env={**os.environ, "AMU_PROCESS_TEST": "configured"},
    )
    assert result.error is None
    assert result.stdout.decode().splitlines() == [str(tmp_path), "configured"]


@pytest.mark.parametrize("argv", [[], ["python", 1], "python", ["python\x00bad"]])
def test_invalid_argv_is_rejected(argv):
    with pytest.raises(ValueError):
        process.run(argv, timeout=1, stdout_limit=10, stderr_limit=10)


def test_deadline_still_applies_after_child_closes_both_pipes():
    started = time.monotonic()
    result = _run("import os,time; os.close(1); os.close(2); time.sleep(10)", timeout=0.2)
    assert time.monotonic() - started < 2
    assert result.error == "timeout"


@pytest.mark.parametrize("overflow", [False, True])
def test_portable_bounded_queue_backend(monkeypatch, overflow):
    monkeypatch.setattr(process, "_SELECTABLE_PIPES", False)
    result = _run(
        "import os; [(os.write(1,b'o'*4096),os.write(2,b'e'*4096)) for _ in range(32)]",
        stdout_limit=100 if overflow else 131_072,
    )
    if overflow:
        assert result.error == "output_limit"
        assert result.stdout == result.stderr == b""
    else:
        assert result.error is None
        assert result.stdout == b"o" * 131_072
        assert result.stderr == b"e" * 131_072


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_overflow_tears_down_grandchild(tmp_path):
    marker = tmp_path / "overflow-grandchild-alive"
    source = (
        "import os,time,pathlib; child=os.fork(); "
        "\nif child == 0:\n time.sleep(.5); pathlib.Path(" + repr(str(marker)) + ").write_text('alive'); os._exit(0)"
        "\nos.write(1,b'x'*100); time.sleep(10)"
    )
    result = _run(source, stdout_limit=10)
    assert result.error == "output_limit"
    time.sleep(0.6)
    assert not marker.exists()


def test_pipe_read_failure_withholds_output_and_tears_down(monkeypatch):
    def fail(child, output, deadline):
        output.accept("stdout", b"partial")
        raise OSError("pipe read failed")

    monkeypatch.setattr(process, "_drain_posix", fail)
    monkeypatch.setattr(process, "_SELECTABLE_PIPES", True)
    started = time.monotonic()
    result = _run("import time; time.sleep(10)")
    assert time.monotonic() - started < 2
    assert (result.returncode, result.error) == (126, "io")
    assert result.stdout == result.stderr == b""
    assert result.stdout_bytes == 7
