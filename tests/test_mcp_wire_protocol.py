"""Independent size/identifier checks on bytes from the actual stdio pipe.

The SDK client is deliberately not used to parse/re-serialize acceptance
measurements. ``AMU_WIRE_TEST_PYTHON`` selects a frozen installed candidate for
independent red/green evidence without changing the test assertions.
"""

import json
import os
import sys

import pytest

pytest.importorskip("mcp")
from scripts.smoke_mcp_wire import RawSession, assert_bounded, assert_error, request


@pytest.fixture
def wire_root(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "api.py").write_text("def public(value):\n    return value\n", encoding="utf-8")
    return root


def _session(root, **kwargs):
    return RawSession(os.environ.get("AMU_WIRE_TEST_PYTHON", sys.executable), root, **kwargs)


def _impact(root):
    return {"name": "amu_impact", "arguments": {"repo": str(root), "file": "api.py", "line": 2,
            "proposed_change": "validate value before returning"}}


def test_original_150000_byte_identifier_never_emits_oversized_wire_frame(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call("x" * 150_000, "tools/call", _impact(wire_root))
        assert_bounded(frame)
        # A transport rejection is not a rewritten/correlated response for the
        # inadmissible ID. It must explicitly explain ID rejection with null ID.
        assert packet.get("id") is None and "error" in packet
        assert "id" in packet["error"]["message"].lower()
        assert packet["error"]["data"]["error_kind"] == "request_id_limit"
        assert packet["error"]["data"]["dispatched"] is False
        frame, resumed = session.call("after-id-rejection", "tools/list", {})
        assert_bounded(frame)
        assert resumed["id"] == "after-id-rejection" and "result" in resumed
        assert not (wire_root / ".amu").exists()


def test_exact_original_audit_invalid_cursor_and_150000_byte_identifier(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        params = _impact(wire_root)
        params["arguments"]["cursor"] = "invalid!"
        frame, packet = session.call("r" * 150_000, "tools/call", params)
        assert_bounded(frame)
        assert packet.get("id") is None and packet["error"]["data"]["dispatched"] is False
        assert packet["error"]["data"]["error_kind"] == "request_id_limit"


@pytest.mark.parametrize("identifier", ["x" * 1024, "雪" * 100, "😀" * 256,
                                        "\n\u0000\\\"" * 100, "\u0000" * 1024, 0, -1, 10**100, 10**1023])
def test_admitted_identifier_is_echoed_exactly_and_all_raw_frames_are_bounded(wire_root, identifier):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call(identifier, "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root)},
        })
        assert_bounded(frame)
        assert packet["id"] == identifier and type(packet["id"]) is type(identifier)
        assert json.loads(packet["result"]["content"][0]["text"]) == {"reviews": []}
        assert not (wire_root / ".amu").exists()


@pytest.mark.parametrize("identifier", ["x" * 1025, "😀" * 257, "\u0000" * 1025, 10**1024])
def test_identifier_admission_limits_do_not_truncate_or_coerce(wire_root, identifier):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call(identifier, "tools/list", {})
        assert_bounded(frame)
        assert packet.get("id") is None and "error" in packet
        assert packet["error"]["data"]["error_kind"] == "request_id_limit"
        assert packet["error"]["data"]["max_id_bytes"] == 1024
        assert packet["error"]["data"]["dispatched"] is False
        frame, followup = session.call("after-id-limit", "tools/list", {})
        assert_bounded(frame)
        assert followup["id"] == "after-id-limit" and "result" in followup


def test_non_utf8_string_identifier_is_rejected_without_replacement_or_dispatch(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call("\ud800", "tools/list", {})
        assert_bounded(frame)
        assert packet.get("id") is None and "error" in packet
        assert packet["error"]["data"]["dispatched"] is False
        frame, followup = session.call("after-unicode-rejection", "tools/list", {})
        assert_bounded(frame)
        assert followup["id"] == "after-unicode-rejection" and "result" in followup


@pytest.mark.parametrize("invalid_arguments", [
    {"line": "private-input-marker-" * 5500},
    {"cursor": "private-cursor-marker-" * 5500},
    {"line": {"private-input-marker": ["雪" * 25_000]}},
])
def test_sdk_generated_validation_errors_are_bounded_and_followup_remains_correlated(wire_root, invalid_arguments):
    with _session(wire_root) as session:
        session.initialize()
        arguments = {**_impact(wire_root)["arguments"], **invalid_arguments}
        frame, packet = session.call("sdk-invalid", "tools/call", {"name": "amu_impact", "arguments": arguments})
        assert_bounded(frame)
        assert packet["id"] == "sdk-invalid"
        assert_error(packet)
        frame, followup = session.call("followup", "tools/list", {})
        assert_bounded(frame)
        assert followup["id"] == "followup" and "result" in followup
        assert not (wire_root / ".amu").exists()


@pytest.mark.parametrize("params", [
    {"name": "private-unknown-tool-" * 5500, "arguments": {}},
    {"name": "private-unknown-tool-" * 10_000, "arguments": {}},
    {"name": "amu_impact", "arguments": "private-wrong-params-" * 5500},
])
def test_sdk_protocol_errors_cannot_bypass_response_size_boundary(wire_root, params):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call("protocol-error", "tools/call", params)
        assert_bounded(frame)
        assert packet["id"] == "protocol-error"
        assert_error(packet)


def test_oversized_sdk_unknown_method_error_is_explicitly_withheld_not_a_partial_success(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        frame, packet = session.call("unknown-method", "private-unknown-method-" * 7000, {})
        assert_bounded(frame)
        assert packet["id"] == "unknown-method" and "error" in packet
        assert packet["error"]["data"]["error_kind"] == "response_limit"
        assert packet["error"]["data"]["evidence_withheld"] is True
        assert packet["error"]["data"]["original_response_bytes"] > 131_072
        assert b"private-unknown-method" not in frame
        frame, followup = session.call("after-error-limit", "tools/list", {})
        assert_bounded(frame)
        assert followup["id"] == "after-error-limit" and "result" in followup


def test_exceptional_tool_path_has_bounded_response_and_no_exception_value(wire_root):
    # Fault injection creates a genuine application exception inside the
    # actual SDK server. No fake transport or response is substituted.
    code = """import sys
from amu import mcp_server
def fail(*args, **kwargs):
    raise RuntimeError('PRIVATE_EXCEPTION_VALUE_' * 10000)
mcp_server._cli_process = fail
mcp_server.main(['--allow-repo', sys.argv[1]])
"""
    with _session(wire_root, code=code) as session:
        session.initialize()
        frame, packet = session.call("exception", "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root)},
        })
        assert_bounded(frame)
        assert packet["id"] == "exception"
        assert b"PRIVATE_EXCEPTION_VALUE" not in frame
        payload = json.loads(packet["result"]["content"][0]["text"])
        assert payload["error_kind"] == "tool_exception" and payload["exit"] == 3
        assert payload["side_effects"] == "unknown"


def test_actual_sdk_serializer_failure_yields_same_id_bounded_explicit_error(wire_root):
    # Exercise the final transport serializer, not a mocked bounded_frame.
    code = """import sys
from mcp.types import JSONRPCResponse
from amu import mcp_server
original = JSONRPCResponse.model_dump_json
def serialize(self, *args, **kwargs):
    if self.id == 'serializer-fault':
        raise RuntimeError('PRIVATE_SERIALIZER_EXCEPTION_' * 10000)
    return original(self, *args, **kwargs)
JSONRPCResponse.model_dump_json = serialize
mcp_server.main(['--allow-repo', sys.argv[1]])
"""
    with _session(wire_root, code=code) as session:
        session.initialize()
        frame, packet = session.call("serializer-fault", "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root)},
        })
        assert_bounded(frame)
        assert packet["id"] == "serializer-fault" and "error" in packet
        assert b"PRIVATE_SERIALIZER_EXCEPTION" not in frame
        assert packet["error"]["data"]["error_kind"] == "response_limit"
        assert packet["error"]["data"]["evidence_withheld"] is True
        assert packet["error"]["data"]["serialization_failed"] is True
        assert packet["error"]["data"]["original_response_bytes"] is None
        assert packet["error"]["data"]["side_effects"] == "unknown"
        frame, followup = session.call("after-serializer-fault", "tools/list", {})
        assert_bounded(frame)
        assert followup["id"] == "after-serializer-fault" and "result" in followup


def test_buffered_stray_stdout_is_not_flushed_to_wire_during_shutdown(wire_root):
    # A buffered write is intentionally not flushed while fd 1 is diverted.
    # Restoring fd 1 before draining that buffer would leak a non-protocol tail.
    code = """import sys
from amu import mcp_server
original = mcp_server._cli_process
def stray(*args, **kwargs):
    sys.stdout.write('BUFFERED_STRAY_HANDLER_OUTPUT')
    return original(*args, **kwargs)
mcp_server._cli_process = stray
mcp_server.main(['--allow-repo', sys.argv[1]])
"""
    with _session(wire_root, code=code) as session:
        session.initialize()
        frame, packet = session.call("stray-output", "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root)},
        })
        assert_bounded(frame)
        assert b"BUFFERED_STRAY_HANDLER_OUTPUT" not in frame
        assert packet["id"] == "stray-output"
        assert json.loads(packet["result"]["content"][0]["text"]) == {"reviews": []}
    assert session.process.returncode == 0
    assert session.trailing_stdout() == b""
    assert b"BUFFERED_STRAY_HANDLER_OUTPUT" in session.stderr


def test_failed_buffer_flush_closes_without_restoring_stdout_to_protocol_wire(wire_root):
    code = """import sys
from amu import mcp_server
class FailFirstFlush:
    def __init__(self, underlying):
        self.underlying = underlying
        self.failed = False
    def write(self, value):
        return self.underlying.write(value)
    def flush(self):
        if not self.failed:
            self.failed = True
            raise OSError('injected buffered-output flush failure')
        return self.underlying.flush()
    def __getattr__(self, name):
        return getattr(self.underlying, name)
sys.stdout = FailFirstFlush(sys.stdout)
original = mcp_server._cli_process
def stray(*args, **kwargs):
    sys.stdout.write('FAILED_FLUSH_RETAINED_HANDLER_OUTPUT')
    return original(*args, **kwargs)
mcp_server._cli_process = stray
mcp_server.main(['--allow-repo', sys.argv[1]])
"""
    with _session(wire_root, code=code) as session:
        session.initialize()
        frame, packet = session.call("failed-flush", "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root)},
        })
        assert_bounded(frame)
        assert packet["id"] == "failed-flush"
        assert json.loads(packet["result"]["content"][0]["text"]) == {"reviews": []}
    assert session.process.returncode != 0, "unflushable stdout must close the server explicitly"
    assert session.trailing_stdout() == b"", "retained output leaked after failed shutdown flush"


def test_duplicate_identifier_members_are_explicitly_rejected_not_silently_rewritten(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        session.send_bytes(b'{"jsonrpc":"2.0","id":"first","id":"second","method":"tools/list","params":{}}\n')
        frame, packet = session.receive()
        assert_bounded(frame)
        assert packet.get("id") is None and "error" in packet
        assert "duplicate" in packet["error"]["message"].lower()


def test_duplicate_repository_argument_is_rejected_without_dispatch(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        root = json.dumps(str(wire_root)).encode("utf-8")
        packet = (b'{"jsonrpc":"2.0","id":"duplicate-repo","method":"tools/call","params":'
                  b'{"name":"amu_docs_reviews","arguments":{"repo":' + root + b',"repo":' + root + b'}}}\n')
        session.send_bytes(packet)
        frame, response = session.receive()
        assert_bounded(frame)
        assert response.get("id") is None and response["error"]["data"]["dispatched"] is False
        assert response["error"]["data"]["error_kind"] == "duplicate_json_member"
        assert not (wire_root / ".amu").exists()


def test_oversized_incoming_frame_is_explicitly_rejected_then_drained_to_next_frame(wire_root):
    with _session(wire_root) as session:
        session.initialize()
        session.send(request("too-large-frame", "tools/call", {
            "name": "amu_docs_reviews", "arguments": {"repo": str(wire_root), "oversized": "x" * 300_000},
        }))
        frame, packet = session.receive()
        assert_bounded(frame)
        assert packet.get("id") is None and packet["error"]["data"]["dispatched"] is False
        assert packet["error"]["data"]["error_kind"] == "request_frame_limit"
        assert packet["error"]["data"]["max_frame_bytes"] == 262_144
        frame, packet = session.call("after-frame-drain", "tools/list", {})
        assert_bounded(frame)
        assert packet["id"] == "after-frame-drain" and "result" in packet


@pytest.mark.parametrize("packet", [
    b'{"jsonrpc":"2.0","id":true,"method":"tools/list","params":{}}\n',
    b'{"jsonrpc":"2.0","id":1.5,"method":"tools/list","params":{}}\n',
    json.dumps([request("batch", "tools/list", {})]).encode("utf-8") + b"\n",
    b'{"jsonrpc":"2.0","id":"bad-json","method":\n',
])
def test_invalid_or_unsupported_protocol_frames_receive_bounded_explicit_error(wire_root, packet):
    with _session(wire_root) as session:
        session.initialize()
        session.send_bytes(packet)
        frame, error = session.receive()
        assert_bounded(frame)
        assert error.get("id") is None and "error" in error


def test_exact_response_allowance_and_one_byte_overflow_at_actual_pipe(wire_root):
    # Inject sized SDK response payloads after handler budgeting, retaining the
    # genuine SDK serializer and AMU transport. Measure only the emitted pipe
    # bytes; do not reconstruct the result or call the codec directly.
    code = """import sys
from mcp.types import JSONRPCResponse
from amu import mcp_server
original = JSONRPCResponse.model_dump_json
def serialize(self, *args, **kwargs):
    target = {'exact-response': 131072, 'oversized-response': 131073}.get(self.id)
    if target is None:
        return original(self, *args, **kwargs)
    empty = self.model_copy(update={'result': {'blob': ''}})
    overhead = len(original(empty, *args, **kwargs).encode('utf-8')) + 1
    sized = self.model_copy(update={'result': {'blob': 'x' * (target - overhead)}})
    return original(sized, *args, **kwargs)
JSONRPCResponse.model_dump_json = serialize
mcp_server.main(['--allow-repo', sys.argv[1]])
"""
    with _session(wire_root, code=code) as session:
        session.initialize()
        frame, exact = session.call('exact-response', 'tools/list', {})
        assert len(frame) == 131_072 and frame.endswith(b'\n')
        assert exact['id'] == 'exact-response' and exact['result']['blob']
        frame, overflow = session.call('oversized-response', 'tools/list', {})
        assert_bounded(frame)
        assert overflow['id'] == 'oversized-response' and 'result' not in overflow
        assert overflow['error']['data']['error_kind'] == 'response_limit'
        assert overflow['error']['data']['original_response_bytes'] == 131_073
        assert overflow['error']['data']['evidence_withheld'] is True
        frame, followup = session.call('after-one-byte-overflow', 'tools/list', {})
        assert_bounded(frame)
        assert followup['id'] == 'after-one-byte-overflow' and 'result' in followup
        assert not (wire_root / '.amu').exists()
