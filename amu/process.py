"""Byte-bounded, shell-free child execution with one absolute IO deadline.

POSIX children start a new session. On runner failure or interruption the whole
process group is killed, including descendants that still hold the output pipes;
the direct child is reaped. Descendants that deliberately escape that group are
outside this guarantee. Windows uses bounded reader queues and terminates the
direct child only: descendant teardown needs a platform-specific job object.

A stdout sink receives bounded chunks *only after* their byte limit check. It
must return promptly: arbitrary Python callbacks cannot safely be preempted.
Sinks can already have received earlier chunks when a later read fails, so callers
must stage their output and publish it only after a successful result. Captured
output is withheld on every runner failure; ordinary nonzero child exits retain
their bounded output and have ``error=None``.
"""

from __future__ import annotations

import math
import os
import queue
import selectors
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

_CHUNK_BYTES = 65_536
_POLL_SECONDS = 0.05
_CLEANUP_SECONDS = 1.0
_SELECTABLE_PIPES = os.name == "posix"
_ERROR_CODES = {"missing": 127, "timeout": 124, "output_limit": 125, "io": 126}


@dataclass(frozen=True, slots=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes
    error: str | None
    stdout_bytes: int
    stderr_bytes: int


class _Failure(Exception):
    def __init__(self, error: str):
        self.error = error


class _Output:
    def __init__(self, stdout_limit: int, stderr_limit: int, stdout_sink, capture_stdout: bool):
        self.limits = {"stdout": stdout_limit, "stderr": stderr_limit}
        self.counts = {"stdout": 0, "stderr": 0}
        self.buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self.sink = stdout_sink
        self.capture_stdout = capture_stdout

    def read_size(self, stream: str) -> int:
        # One sentinel byte distinguishes an exact boundary from overflow.
        return min(_CHUNK_BYTES, self.limits[stream] - self.counts[stream] + 1)

    def accept(self, stream: str, chunk: bytes) -> None:
        self.counts[stream] += len(chunk)
        if self.counts[stream] > self.limits[stream]:
            raise _Failure("output_limit")
        if stream == "stdout" and self.sink is not None:
            try:
                self.sink(chunk)
            except Exception as exc:
                raise _Failure("io") from exc
        if stream == "stderr" or self.capture_stdout:
            self.buffers[stream].extend(chunk)

    def result(self, returncode: int, error: str | None = None) -> Result:
        return Result(
            returncode=returncode,
            stdout=bytes(self.buffers["stdout"]) if error is None else b"",
            stderr=bytes(self.buffers["stderr"]) if error is None else b"",
            error=error,
            stdout_bytes=self.counts["stdout"],
            stderr_bytes=self.counts["stderr"],
        )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Failure("timeout")
    return remaining


def _drain_posix(child: subprocess.Popen, output: _Output, deadline: float) -> None:
    with selectors.DefaultSelector() as selector:
        for name, pipe in (("stdout", child.stdout), ("stderr", child.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            ready = selector.select(min(_POLL_SECONDS, _remaining(deadline)))
            for key, _ in ready:
                _remaining(deadline)
                try:
                    chunk = os.read(key.fd, output.read_size(key.data))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.accept(key.data, chunk)


def _drain_threads(child: subprocess.Popen, output: _Output, deadline: float) -> None:
    """Portable fallback: queue + readers retain at most a few fixed chunks."""
    messages = queue.Queue(maxsize=4)
    stopped = threading.Event()

    def reader(name, pipe):
        def send(value):
            while not stopped.is_set():
                try:
                    messages.put(value, timeout=_POLL_SECONDS)
                    return
                except queue.Full:
                    continue

        try:
            while not stopped.is_set():
                chunk = os.read(pipe.fileno(), _CHUNK_BYTES)
                send((name, chunk, None))
                if not chunk:
                    break
        except (OSError, ValueError) as exc:
            send((name, b"", exc))

    for name, pipe in (("stdout", child.stdout), ("stderr", child.stderr)):
        threading.Thread(target=reader, args=(name, pipe), daemon=True).start()
    closed = set()
    try:
        while len(closed) < 2:
            try:
                name, chunk, error = messages.get(timeout=min(_POLL_SECONDS, _remaining(deadline)))
            except queue.Empty:
                continue
            _remaining(deadline)
            if error is not None:
                raise _Failure("io")
            if not chunk:
                closed.add(name)
            else:
                output.accept(name, chunk)
    finally:
        stopped.set()


def _teardown(child: subprocess.Popen) -> None:
    # A leader can already have exited while its descendants retain the pipes.
    # Do not condition killpg on child.poll(): its session still needs teardown.
    try:
        if os.name == "posix":
            os.killpg(child.pid, signal.SIGKILL)
        else:
            child.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        child.wait(timeout=_CLEANUP_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        # Cleanup is itself bounded; SIGKILL normally makes this unreachable.
        pass


def _validate(argv, timeout, stdout_limit, stderr_limit, env, stdout_sink, capture_stdout):
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise ValueError("argv must be a nonempty list of strings without NUL bytes")
    try:
        valid_timeout = (
            not isinstance(timeout, bool) and isinstance(timeout, (int, float)) and math.isfinite(timeout) and timeout > 0
        )
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ValueError("timeout must be a positive finite number")
    for name, value in (("stdout_limit", stdout_limit), ("stderr_limit", stderr_limit)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer byte limit")
    if env is not None and (
        not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items())
    ):
        raise ValueError("env must be a dictionary of strings")
    if stdout_sink is not None and not callable(stdout_sink):
        raise ValueError("stdout_sink must be callable")
    if type(capture_stdout) is not bool:
        raise ValueError("capture_stdout must be a boolean")


def run(
    argv: list[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    cwd: str | None = None,
    env: dict | None = None,
    stdout_sink: Callable[[bytes], None] | None = None,
    capture_stdout: bool = True,
) -> Result:
    """Run with byte caps and a deadline; invalid options raise ``ValueError``.

    Runner errors are ``missing`` (127), ``timeout`` (124), ``output_limit``
    (125), or ``io`` (126). Byte counts are bytes actually observed, not an
    estimate of all bytes emitted by a child terminated for overflow.
    ``KeyboardInterrupt`` and other non-Exception interruptions propagate only
    after process teardown. ``stdin`` is DEVNULL and ``shell`` is always false.
    """
    _validate(argv, timeout, stdout_limit, stderr_limit, env, stdout_sink, capture_stdout)
    output = _Output(stdout_limit, stderr_limit, stdout_sink, capture_stdout)
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            shell=False,
            bufsize=0,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError:
        return output.result(_ERROR_CODES["missing"], "missing")
    except (OSError, ValueError, TypeError):
        return output.result(_ERROR_CODES["io"], "io")
    try:
        drain = _drain_posix if _SELECTABLE_PIPES else _drain_threads
        drain(child, output, deadline)
        try:
            returncode = child.wait(timeout=_remaining(deadline))
        except subprocess.TimeoutExpired as exc:
            raise _Failure("timeout") from exc
        return output.result(returncode)
    except _Failure as exc:
        _teardown(child)
        return output.result(_ERROR_CODES[exc.error], exc.error)
    except Exception:
        _teardown(child)
        return output.result(_ERROR_CODES["io"], "io")
    except BaseException:
        _teardown(child)
        raise
    finally:
        for pipe in (child.stdout, child.stderr):
            try:
                pipe.close()
            except OSError:
                pass
