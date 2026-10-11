"""Codec tests supplement, rather than replace, raw installed-wire tests."""

import json

import pytest

pytest.importorskip("mcp")
import anyio
from mcp import types

from amu import mcp_transport


def test_exact_wire_allowance_includes_newline_and_never_returns_partial_success():
    empty = types.JSONRPCResponse(jsonrpc="2.0", id="original", result={"blob": ""})
    overhead = len(mcp_transport.bounded_frame(empty))
    blob = "x" * (131_072 - overhead)
    complete = types.JSONRPCResponse(jsonrpc="2.0", id="original", result={"blob": blob})
    frame = mcp_transport.bounded_frame(complete)
    assert len(frame) == 131_072 and frame.endswith(b"\n")
    assert json.loads(frame)["result"]["blob"] == blob
    oversized = types.JSONRPCResponse(jsonrpc="2.0", id="original", result={"blob": blob + "x"})
    failure_frame = mcp_transport.bounded_frame(oversized)
    failure = json.loads(failure_frame)
    assert len(failure_frame) <= 131_072 and failure["id"] == "original"
    assert "result" not in failure
    assert failure["error"]["code"] == -32010
    assert failure["error"]["data"]["evidence_withheld"] is True
    assert failure["error"]["data"]["original_response_bytes"] == 131_073
    assert "page_size" in failure["error"]["data"]["retry"]


@pytest.mark.parametrize("message", [
    types.JSONRPCRequest(jsonrpc="2.0", id="request", method="server-request", params={"blob": "x" * 131_072}),
    types.JSONRPCNotification(jsonrpc="2.0", method="notification", params={"blob": "x" * 131_072}),
])
def test_oversized_server_request_or_notification_is_not_forged_into_response(message):
    with pytest.raises(mcp_transport.TransportFailure, match="non-response"):
        mcp_transport.bounded_frame(message)


def test_an_outgoing_unadmitted_identifier_is_never_truncated_or_rewritten():
    message = types.JSONRPCResponse(jsonrpc="2.0", id="x" * 1025, result={})
    with pytest.raises(mcp_transport.TransportFailure, match="identifier"):
        mcp_transport.bounded_frame(message)


def test_serialization_failure_is_bounded_without_echoing_exception_values(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_EXCEPTION_VALUE" * 100_000)

    monkeypatch.setattr(types.JSONRPCResponse, "model_dump_json", fail)
    response = types.JSONRPCResponse(jsonrpc="2.0", id=123, result={})
    frame = mcp_transport.bounded_frame(response)
    error = json.loads(frame)
    assert len(frame) <= 131_072 and error["id"] == 123
    assert b"PRIVATE_EXCEPTION_VALUE" not in frame
    assert error["error"]["data"]["serialization_failed"] is True
    assert error["error"]["data"]["original_response_bytes"] is None


def test_failed_error_serialization_closes_instead_of_using_unbounded_fallback(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("private")

    monkeypatch.setattr(types.JSONRPCError, "model_dump_json", fail)
    error = types.JSONRPCError(jsonrpc="2.0", id=123, error={"code": -32603, "message": "failed"})
    with pytest.raises(mcp_transport.TransportFailure, match="bounded error could not be serialized"):
        mcp_transport.bounded_frame(error)


def test_decode_is_strict_about_ids_without_sdk_coercion():
    class IntegerSubclass(int):
        pass

    for value in (True, False, 1.0, None, IntegerSubclass(1), "\ud800"):
        assert mcp_transport.admitted_id(value) is False
    for value in (0, -1, 10**1023, "\u0000" * 1024, "😀" * 256):
        assert mcp_transport.admitted_id(value) is True


def test_parse_rejection_does_not_echo_raw_credentials_or_dispatch():
    for frame in (b'{"secret":"PRIVATE_TOKEN",', b'\xffPRIVATE_TOKEN\n',
                  b'{"jsonrpc":"2.0","id":"request","params":{"secret":NaN}}\n'):
        message, rejection = mcp_transport.decode_frame(frame)
        assert message is None
        encoded = mcp_transport.bounded_frame(rejection)
        assert b"PRIVATE_TOKEN" not in encoded
        assert json.loads(encoded)["error"]["data"]["dispatched"] is False


def test_chunked_reader_discards_oversized_frame_and_preserves_next_frame(monkeypatch):
    # The chunks deliberately bisect identifiers, CRLF and the frame limit.
    chunks = iter([b'x' * 200_000, b'x' * 62_145, b'\r', b'\n{"id":', b'"next"}\n', b''])

    async def readable(_fd):
        await anyio.lowlevel.checkpoint()

    monkeypatch.setattr(anyio, "wait_readable", readable)
    monkeypatch.setattr(mcp_transport.os, "read", lambda *_args: next(chunks))

    async def collect():
        return [frame async for frame in mcp_transport._frames(99)]

    assert anyio.run(collect) == [None, b'{"id":"next"}\n']


def test_final_write_checks_bytes_before_any_output(monkeypatch):
    writes = []
    monkeypatch.setattr(mcp_transport.os, "write", lambda *args: writes.append(args))
    with pytest.raises(mcp_transport.TransportFailure, match="before writing"):
        anyio.run(mcp_transport._write_frame, 99, b'x' * 131_073)
    assert writes == []


def _fake_descriptors(monkeypatch, fail_get_blocking=False):
    duplicates = iter([30, 31])
    closed, restored, flags = [], [], []
    monkeypatch.setattr(mcp_transport.os, "dup", lambda _fd: next(duplicates))
    monkeypatch.setattr(mcp_transport.os, "open", lambda *_args: 32)
    monkeypatch.setattr(mcp_transport.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(mcp_transport.os, "dup2", lambda source, target: restored.append((source, target)))
    monkeypatch.setattr(mcp_transport.os, "set_blocking", lambda fd, flag: flags.append((fd, flag)))

    def get_blocking(fd):
        if fail_get_blocking and fd == 31:
            raise OSError("descriptor unavailable")
        return True

    monkeypatch.setattr(mcp_transport.os, "get_blocking", get_blocking)
    return closed, restored, flags


def test_owned_wire_descriptors_are_restored_after_handler_failure(monkeypatch):
    closed, restored, flags = _fake_descriptors(monkeypatch)
    with pytest.raises(ValueError, match="handler failed"):
        with mcp_transport._wire_descriptors() as wire:
            assert wire == (30, 31)
            raise ValueError("handler failed")
    assert closed == [32, 30, 31]
    assert restored == [(32, 0), (2, 1), (30, 0), (31, 1)]
    assert flags == [(30, False), (31, False), (30, True), (31, True)]


def test_partially_claimed_descriptors_are_closed_when_flag_capture_fails(monkeypatch):
    closed, restored, _flags = _fake_descriptors(monkeypatch, fail_get_blocking=True)
    with pytest.raises(mcp_transport.TransportFailure, match="descriptors were unavailable"):
        with mcp_transport._wire_descriptors():
            pytest.fail("a partially captured wire must not start")
    assert closed == [30, 31] and restored == []


def test_second_transport_cannot_claim_diverted_or_restore_another_wire(monkeypatch):
    _fake_descriptors(monkeypatch)
    with mcp_transport._wire_descriptors():
        with pytest.raises(mcp_transport.TransportFailure, match="already owns the wire"):
            with mcp_transport._wire_descriptors():
                pytest.fail("another active transport must not seize the wire")
    assert mcp_transport._WIRE_CLAIMED is False


def test_failed_buffer_flush_never_restores_stdout_to_the_protocol_wire(monkeypatch):
    _closed, restored, _flags = _fake_descriptors(monkeypatch)
    monkeypatch.setattr(mcp_transport, "_WIRE_CLAIMED", False)

    class FailedBuffer:
        def flush(self):
            raise OSError("buffered handler output could not flush")

    monkeypatch.setattr(mcp_transport.sys, "stdout", FailedBuffer())
    with pytest.raises(mcp_transport.TransportFailure, match="could not be restored"):
        with mcp_transport._wire_descriptors():
            pass
    assert (30, 0) in restored and (31, 1) not in restored
    assert mcp_transport._WIRE_CLAIMED is True
