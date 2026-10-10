"""Bounded AMU stdio codec around the public MCP stream runner.

The SDK's tool wrappers cannot bound identifiers or SDK-generated messages.
Admit identifiers from the original JSON before SDK validation, and serialize
every outbound SDK message exactly once before writing any bytes. No response
identifier is truncated or replaced. A rejected, uncorrelatable frame receives
an explicit ``id: null`` protocol error; an admitted identifier is preserved.

This transport currently requires POSIX descriptors. It refuses unsupported
platforms rather than falling back to an unbounded SDK transport. MCP/AnyIO
remain optional: dependency-free imports do not import either package.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import asynccontextmanager, contextmanager

from amu.mcp_manifest import MCP_WIRE_LIMIT

MAX_ID_BYTES = 1024
MAX_REQUEST_FRAME_BYTES = 256 * 1024
RESPONSE_LIMIT_CODE = -32010
_CHUNK_BYTES = 64 * 1024
_CLAIM_LOCK = threading.Lock()
_WIRE_CLAIMED = False


class TransportFailure(RuntimeError):
    """The connection cannot continue without violating its wire contract."""


class _DuplicateMember(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise _DuplicateMember("duplicate JSON object member")
        result[name] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-JSON numeric constant")


def admitted_id(value: object) -> bool:
    """Test the original identifier without coercion or altered correlation."""
    try:
        if type(value) is str:
            return len(value.encode("utf-8")) <= MAX_ID_BYTES
        if type(value) is int:
            return len(str(value).encode("ascii")) <= MAX_ID_BYTES
    except (UnicodeError, ValueError, OverflowError):
        return False
    return False


def _error(request_id, code: int, message: str, **data):
    from mcp import types

    return types.JSONRPCError(jsonrpc="2.0", id=request_id,
                              error=types.ErrorData(code=code, message=message, data=data))


def decode_frame(frame: bytes):
    """Return an SDK message or an explicit transport rejection, never both.

    ``frame`` includes its newline when present. SDK validation receives the
    original bytes only after duplicate-member and identifier admission, so
    its parser cannot silently coerce an identifier or choose a duplicate.
    Rejections contain neither rejected input nor its identifier.
    """
    if len(frame) > MAX_REQUEST_FRAME_BYTES:
        return None, _error(None, -32600, "Request frame exceeds the transport allowance; it was not dispatched.",
                            error_kind="request_frame_limit", dispatched=False,
                            max_frame_bytes=MAX_REQUEST_FRAME_BYTES)
    try:
        original = json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except _DuplicateMember:
        return None, _error(None, -32600, "Duplicate JSON object members are ambiguous; the frame was not dispatched.",
                            error_kind="duplicate_json_member", dispatched=False)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return None, _error(None, -32700, "Invalid JSON frame; it was not dispatched.",
                            error_kind="parse_error", dispatched=False)
    if not isinstance(original, dict):
        return None, _error(None, -32600, "A single JSON-RPC object is required; the frame was not dispatched.",
                            error_kind="invalid_request", dispatched=False)
    if "id" in original:
        request_id = original["id"]
        # A JSON-RPC error may legitimately have a null correlation ID; a
        # request/success response may not. Do not conflate bool with int.
        null_error = request_id is None and "error" in original and "method" not in original
        if not null_error and not admitted_id(request_id):
            kind = "request_id_limit" if type(request_id) in (str, int) else "request_id_invalid"
            return None, _error(None, -32600,
                                "The original request identifier was not admitted or echoed; retry with a bounded string or integer ID.",
                                error_kind=kind, dispatched=False, max_id_bytes=MAX_ID_BYTES)
    try:
        from mcp import types
        from pydantic import TypeAdapter

        return TypeAdapter(types.JSONRPCMessage).validate_json(frame), None
    except Exception:
        # Pydantic/SDK exception text can include rejected values. It must
        # never become an unbounded or credential-bearing wire diagnostic.
        return None, _error(None, -32600, "Invalid JSON-RPC envelope; it was not dispatched.",
                            error_kind="invalid_request", dispatched=False)


def _serialized(message) -> bytes:
    return (message.model_dump_json(by_alias=True, exclude_unset=True) + "\n").encode("utf-8")


def bounded_frame(message) -> bytes:
    """Serialize the exact complete outgoing frame, with a bounded failure.

    An oversized/erroring response becomes an explicit error on its original
    admitted ID, not a partially successful result. No server request or
    notification can be rewritten as a response: those paths fail closed.
    """
    from mcp import types

    response = isinstance(message, (types.JSONRPCResponse, types.JSONRPCError))
    request_id = getattr(message, "id", None)
    if request_id is not None and not admitted_id(request_id):
        raise TransportFailure("outgoing identifier violates admission; connection closed")
    try:
        frame = _serialized(message)
    except Exception:
        frame = None
    if frame is not None and len(frame) <= MCP_WIRE_LIMIT:
        return frame
    if not response:
        raise TransportFailure("outgoing non-response could not fit the wire allowance; connection closed")
    details = {
        "error_kind": "response_limit", "evidence_withheld": True,
        "original_response_bytes": len(frame) if frame is not None else None,
        "original_response_type": "error" if isinstance(message, types.JSONRPCError) else "result",
        "side_effects": "unknown",
        "retry": "Inspect state before repeating an action. Narrow the request; use a smaller amu_impact page_size and its evidence cursors.",
    }
    if isinstance(message, types.JSONRPCError):
        details["original_error_code"] = message.error.code
    if frame is None:
        details["serialization_failed"] = True
    replacement = _error(request_id, RESPONSE_LIMIT_CODE,
                         "The full response was withheld because it could not satisfy the wire allowance; no partial result was returned.",
                         **details)
    try:
        replacement_frame = _serialized(replacement)
    except Exception as exc:
        raise TransportFailure("bounded error could not be serialized; connection closed") from exc
    if len(replacement_frame) > MCP_WIRE_LIMIT:
        raise TransportFailure("bounded error violates the wire allowance; connection closed")
    return replacement_frame


@contextmanager
def _wire_descriptors():
    """Pin the wire and divert stray handler/child standard IO off it.

    Nonblocking descriptor IO has no background reader threads to outlive
    cancellation and accidentally consume a recycled descriptor. Restore both
    descriptors and their original flags even if startup or the codec fails.
    """
    if os.name != "posix":
        raise TransportFailure("bounded stdio currently requires a POSIX platform")
    global _WIRE_CLAIMED
    with _CLAIM_LOCK:
        if _WIRE_CLAIMED:
            raise TransportFailure("another bounded stdio transport already owns the wire")
        _WIRE_CLAIMED = True
    descriptors = []
    blocking = []
    diverted = []
    try:
        try:
            for standard in (0, 1):
                duplicate = os.dup(standard)
                if duplicate <= 2:
                    os.close(duplicate)
                    raise TransportFailure("standard wire descriptors are unavailable")
                descriptors.append(duplicate)
                blocking.append(None)
                blocking[-1] = os.get_blocking(duplicate)
            null_input = os.open(os.devnull, os.O_RDONLY)
            try:
                os.dup2(null_input, 0)
                diverted.append(0)
            finally:
                os.close(null_input)
            os.dup2(2, 1)
            diverted.append(1)
            for descriptor in descriptors:
                os.set_blocking(descriptor, False)
        except (OSError, ValueError) as exc:
            raise TransportFailure("bounded stdio descriptors were unavailable") from exc
        yield tuple(descriptors)
    finally:
        restoration_failed = False
        stdout_flushed = True
        if 1 in diverted:
            try:
                # Handler output may remain in Python's text buffer. Flush it
                # while fd 1 is still diverted, never after restoring the wire.
                sys.stdout.flush()
            except Exception:
                restoration_failed = True
                stdout_flushed = False
        for standard, descriptor, was_blocking in zip((0, 1), descriptors, blocking):
            try:
                if was_blocking is not None:
                    os.set_blocking(descriptor, was_blocking)
                # A failed flush can leave non-protocol text buffered. Keep
                # stdout diverted on that exceptional path; a later/exit
                # flush must never regain access to the protocol pipe.
                if standard in diverted and (standard != 1 or stdout_flushed):
                    os.dup2(descriptor, standard)
            except OSError:
                restoration_failed = True
            finally:
                os.close(descriptor)
        with _CLAIM_LOCK:
            # An incompletely restored connection cannot be claimed again.
            _WIRE_CLAIMED = restoration_failed
        if restoration_failed:
            raise TransportFailure("standard wire descriptors could not be restored")


async def _frames(descriptor):
    import anyio

    buffered = bytearray()
    discarded = False
    while True:
        await anyio.wait_readable(descriptor)
        try:
            chunk = os.read(descriptor, _CHUNK_BYTES)
        except BlockingIOError:
            continue
        if not chunk:
            if discarded:
                yield None
            elif buffered:
                yield bytes(buffered)
            return
        for part in chunk.splitlines(keepends=True):
            complete = part.endswith(b"\n")
            if not discarded:
                if len(buffered) + len(part) > MAX_REQUEST_FRAME_BYTES:
                    buffered.clear()
                    discarded = True
                else:
                    buffered.extend(part)
            if complete:
                yield None if discarded else bytes(buffered)
                buffered.clear()
                discarded = False


async def _write_frame(descriptor, frame: bytes):
    import anyio

    # This is the last boundary before the first byte reaches stdout.
    if len(frame) > MCP_WIRE_LIMIT:
        raise TransportFailure("unbounded frame refused before writing")
    remaining = memoryview(frame)
    while remaining:
        await anyio.wait_writable(descriptor)
        try:
            written = os.write(descriptor, remaining)
        except BlockingIOError:
            continue
        if written <= 0:
            raise TransportFailure("wire closed during frame write")
        remaining = remaining[written:]


@asynccontextmanager
async def bounded_stdio():
    """Public SDK SessionMessage streams with bounded framing/admission."""
    import anyio
    from mcp.shared.message import SessionMessage

    with _wire_descriptors() as (input_fd, output_fd):
        incoming_send, incoming = anyio.create_memory_object_stream(0)
        outgoing, outgoing_receive = anyio.create_memory_object_stream(0)

        async def read_wire():
            async with incoming_send:
                async for frame in _frames(input_fd):
                    if frame is None:
                        message, rejection = decode_frame(b" " * (MAX_REQUEST_FRAME_BYTES + 1))
                    else:
                        message, rejection = decode_frame(frame)
                    if rejection is not None:
                        await outgoing.send(SessionMessage(rejection))
                    else:
                        await incoming_send.send(SessionMessage(message))

        async def write_wire():
            async with outgoing_receive:
                async for item in outgoing_receive:
                    await _write_frame(output_fd, bounded_frame(item.message))

        # Unexpected IO/serialization failures propagate out of the task group
        # and terminate the connection. No unsafe unbounded fallback is used.
        async with anyio.create_task_group() as group:
            group.start_soon(read_wire)
            group.start_soon(write_wire)
            try:
                yield incoming, outgoing
            finally:
                group.cancel_scope.cancel()


def run_stdio(tool_server, *, name: str, version: str, instructions: str) -> None:
    """Serve registered tools via only the SDK's public stream/handler APIs."""
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import MCPError

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=await tool_server.list_tools())

    async def call_tool(_context, params):
        try:
            return await tool_server.call_tool(params.name, params.arguments or {})
        except MCPError:
            raise
        except Exception as exc:
            # Preserve the SDK's tool-error shape and validation semantics;
            # the final serializer bounds SDK-generated exception text too.
            return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], isError=True)

    server = Server(name, version=version, instructions=instructions,
                    on_list_tools=list_tools, on_call_tool=call_tool)

    async def serve():
        async with bounded_stdio() as (incoming, outgoing):
            await server.run(incoming, outgoing, server.create_initialization_options())

    anyio.run(serve)
