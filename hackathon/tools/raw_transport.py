"""Measure actual installed MCP stdout frames, including JSON-RPC IDs/newlines.

Run with the selected non-editable wheel interpreter's ``-I -B`` flags. This
uses a disposable inspection-only root and no graph provider or credentials.
Unlike an SDK model re-encoding check, each measurement is the original byte
frame read from the real subprocess's pipe. No live deployment is contacted.
"""

import argparse
import hashlib
import importlib.metadata
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

WIRE_LIMIT = 131_072
CAPTURE_LIMIT = 2 * 1024 * 1024
REVIEWED_MANIFEST = "794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761"


def request(identifier, method, params=None):
    packet = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        packet["params"] = params
    return packet


class RawSession:
    """Deadline- and capture-bounded raw stdio session, not an SDK client.

    The larger capture limit deliberately permits recording a regression such
    as the old 150,297-byte response. It is a test-harness ceiling, not a server
    allowance. stderr is continuously drained with bounded retained bytes.
    """

    def __init__(self, python, root, *, code=None, timeout=15):
        self.root = Path(root)
        self.timeout = timeout
        self.frames = []
        self.stderr = bytearray()
        self._queue = queue.Queue()
        self._total = 0
        home = self.root.parent / "wire-home"
        home.mkdir(exist_ok=True)
        env = {"HOME": str(home), "AMU_HOME": str(home / ".amu"),
               "PATH": str(home), "LANG": "en_US.UTF-8", "NO_COLOR": "1", "TERM": "dumb"}
        command = ([str(python), "-I", "-B", "-m", "amu.mcp_server", "--allow-repo", str(self.root)]
                   if code is None else [str(python), "-I", "-B", "-c", code, str(self.root)])
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, cwd=self.root.parent, env=env)
        self._readers = [threading.Thread(target=self._stdout, daemon=True),
                         threading.Thread(target=self._stderr, daemon=True)]
        for reader in self._readers:
            reader.start()

    def _stdout(self):
        try:
            while True:
                frame = self.process.stdout.readline(CAPTURE_LIMIT + 1)
                if not frame:
                    self._queue.put(None)
                    return
                self._total += len(frame)
                if len(frame) > CAPTURE_LIMIT or self._total > CAPTURE_LIMIT:
                    self._queue.put(RuntimeError("raw MCP stdout capture exceeded its test ceiling"))
                    self.process.terminate()
                    return
                self._queue.put(frame)
        except (OSError, ValueError) as exc:
            self._queue.put(exc)

    def _stderr(self):
        try:
            while block := self.process.stderr.read(4096):
                remaining = 65_536 - len(self.stderr)
                if remaining > 0:
                    self.stderr.extend(block[:remaining])
        except (OSError, ValueError):
            pass

    def send(self, packet):
        self.send_bytes(json.dumps(packet, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n")

    def send_bytes(self, frame):
        assert self.process.stdin is not None
        outcome = queue.Queue(maxsize=1)

        def write():
            try:
                self.process.stdin.write(frame)
                self.process.stdin.flush()
                outcome.put(None)
            except (OSError, ValueError) as exc:
                outcome.put(exc)

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            error = outcome.get(timeout=self.timeout)
        except queue.Empty:
            self.process.terminate()
            raise TimeoutError("raw MCP stdin write exceeded its deadline") from None
        if error is not None:
            raise error
        writer.join(timeout=1)

    def receive(self):
        frame = self._queue.get(timeout=self.timeout)
        if frame is None:
            raise AssertionError("MCP exited before the expected response; stderr retained within diagnostic cap")
        if isinstance(frame, Exception):
            raise frame
        assert frame.endswith(b"\n"), "stdio JSON frame is missing its newline"
        assert b"\n" not in frame[:-1], "unescaped newline split an MCP frame"
        packet = json.loads(frame)
        assert isinstance(packet, dict) and packet.get("jsonrpc") == "2.0", "stdout is not JSON-RPC"
        self.frames.append({"bytes": len(frame), "sha256": hashlib.sha256(frame).hexdigest(),
                            "response_kind": "error" if "error" in packet else "result"})
        return frame, packet

    def call(self, identifier, method, params=None):
        self.send(request(identifier, method, params))
        return self.receive()

    def initialize(self):
        from mcp.types import LATEST_PROTOCOL_VERSION

        frame, hello = self.call("initialize", "initialize", {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "amu-raw-wire-verification", "version": "1"},
        })
        assert hello.get("id") == "initialize" and "result" in hello, "initialize failed"
        assert len(frame) <= WIRE_LIMIT
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return hello["result"]

    def close(self):
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for reader in self._readers:
            reader.join(timeout=1)
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()

    def trailing_stdout(self):
        """Return bounded unconsumed wire bytes after shutdown, not decoded data."""
        tail = bytearray()
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return bytes(tail)
            if isinstance(item, Exception):
                raise item
            if item is not None:
                tail.extend(item)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def assert_bounded(frame):
    assert len(frame) <= WIRE_LIMIT, f"actual MCP frame is {len(frame)} bytes; allowance is {WIRE_LIMIT}"


def assert_error(packet):
    if "error" in packet:
        assert isinstance(packet["error"].get("message"), str) and packet["error"]["message"], packet
    else:
        assert packet["result"].get("isError") is True, packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a bounded machine-readable execution receipt")
    parser.add_argument("--expected-build-sha256", help="require the independently recorded candidate payload identity")
    args = parser.parse_args()
    import amu
    from amu import mcp_manifest, mcp_verification

    assert sys.flags.isolated, "run with selected installed interpreter -I -B"
    assert Path(amu.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), "not a non-editable installed package"
    assert amu.__version__ == importlib.metadata.version("amu")
    identity = mcp_manifest.runtime_identity()
    assert identity["build"]["complete"] and identity["build_sha256"]
    assert identity["manifest_sha256"] == REVIEWED_MANIFEST
    if args.expected_build_sha256 is not None:
        assert identity["build_sha256"] == args.expected_build_sha256, "installed payload differs from selected candidate"
    started = time.monotonic()
    cases = []
    with tempfile.TemporaryDirectory(prefix="amu-installed-wire-") as directory:
        root = Path(directory).resolve() / "allowed"
        root.mkdir()
        source = root / "api.py"
        original = "def public(value):\n    return value\n"
        source.write_text(original, encoding="utf-8")
        with RawSession(sys.executable, root) as session:
            hello = session.initialize()
            public_identity = mcp_verification._initialize_identity(hello)
            assert public_identity is not None and public_identity["build_sha256"] == identity["build_sha256"]
            assert public_identity["manifest_sha256"] == REVIEWED_MANIFEST
            frame, listing = session.call("list", "tools/list", {})
            assert_bounded(frame)
            assert mcp_manifest.compare_tools(listing["result"]["tools"])["match"]
            cases.append("four-tool-full-manifest-and-installed-identity")
            for identifier in ("x" * 1024, "boundary-雪-😀-\n-\u0000", 0, -1, 10**100):
                frame, packet = session.call(identifier, "tools/call", {
                    "name": "amu_docs_reviews", "arguments": {"repo": str(root)},
                })
                assert_bounded(frame)
                assert packet["id"] == identifier and type(packet["id"]) is type(identifier)
                assert json.loads(packet["result"]["content"][0]["text"]) == {"reviews": []}
            cases.append("admitted-identifiers-exact-and-bounded")
            frame, inspected = session.call("ordinary-impact", "tools/call", {
                "name": "amu_impact", "arguments": {"repo": str(root), "file": "api.py", "line": 2,
                "proposed_change": "validate value"},
            })
            assert_bounded(frame)
            assert inspected["id"] == "ordinary-impact"
            payload = json.loads(inspected["result"]["content"][0]["text"])
            assert payload["operation"] == "proposed-change-impact" and payload["assessment"]["safe_to_change"] is None
            cases.append("ordinary-impact-is-bounded-honest-inspection")
            # SDK-generated argument diagnostics are outside the old handler's
            # response estimator; the raw frame is the acceptance measurement.
            frame, invalid = session.call("large-sdk-validation", "tools/call", {
                "name": "amu_impact", "arguments": {"repo": str(root), "file": "api.py",
                "line": "private-diagnostic-marker-" * 4000, "proposed_change": "validate value"},
            })
            assert_bounded(frame)
            assert_error(invalid)
            cases.append("large-sdk-generated-validation-error")
            frame, invalid = session.call("large-sdk-method-error", "private-unknown-method-" * 7000, {})
            assert_bounded(frame)
            assert invalid["id"] == "large-sdk-method-error" and "error" in invalid
            assert invalid["error"]["data"]["error_kind"] == "response_limit"
            assert invalid["error"]["data"]["evidence_withheld"] is True
            assert invalid["error"]["data"]["original_response_bytes"] > WIRE_LIMIT
            cases.append("oversized-sdk-error-explicit-evidence-withholding")
            # An existing, disposable ungranted root exercises authorization;
            # a nonexistent path only exercises invalid-repository validation.
            outside = root.parent / "outside"
            outside.mkdir()
            frame, denied = session.call("unauthorized", "tools/call", {
                "name": "amu_docs_reviews", "arguments": {"repo": str(outside)},
            })
            assert_bounded(frame)
            assert json.loads(denied["result"]["content"][0]["text"])["error_kind"] == "repository_not_allowed"
            cases.append("unchanged-explicit-repository-authority")
            frame, rejected = session.call("x" * 150_000, "tools/call", {
                "name": "amu_impact", "arguments": {"repo": str(root), "file": "api.py", "line": 2,
                "proposed_change": "validate value"},
            })
            assert_bounded(frame)
            assert rejected.get("id") is None and "error" in rejected
            assert "id" in rejected["error"]["message"].lower(), rejected
            frame, resumed = session.call("after-id-rejection", "tools/list", {})
            assert_bounded(frame)
            assert resumed["id"] == "after-id-rejection" and "result" in resumed
            cases.append("original-150000-byte-identifier-rejected-before-admission")
            assert source.read_text(encoding="utf-8") == original
            assert not (root / ".amu").exists(), "inspection changed managed state"
            frames = session.frames
        assert session.process.returncode == 0, "selected installed server did not shut down normally"
        assert not session.trailing_stdout(), "unexpected non-protocol output after server shutdown"
    receipt = {"status": "passed", "scope": "new real raw-stdio installed-package execution; not authenticated client",
               "python": sys.version.split()[0], "executable": sys.executable, "package": str(Path(amu.__file__).resolve()),
               "identity": identity, "wire_limit_including_newline": WIRE_LIMIT,
               "expected_build_sha256": args.expected_build_sha256,
               "maximum_frame_bytes": max(frame["bytes"] for frame in frames), "frames": frames, "cases": cases,
               "elapsed_seconds": round(time.monotonic() - started, 3), "server_exit": session.process.returncode,
               "live_deployment_contacted": False}
    print(json.dumps(receipt, sort_keys=True) if args.json else
          f"Installed raw MCP wire smoke passed: {len(frames)} measured frames, maximum {receipt['maximum_frame_bytes']} bytes.")


if __name__ == "__main__":
    main()
