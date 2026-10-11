"""Transport limits and error categories without calling a real provider."""

import gzip
import io
import json
import sys
import urllib.error
from types import SimpleNamespace as NS

import pytest

from amu.harness import providers


class Response(io.BytesIO):
    headers = {}


def test_json_transport_uses_short_timeout_and_bounded_read(monkeypatch):
    class Observed(Response):
        def read(self, size=-1):
            assert 0 < size <= providers.MAX_RESPONSE_BYTES + 1
            return super().read(size)

    seen = []

    def open_response(_request, timeout):
        seen.append(timeout)
        return Observed(b'{"ok": true}')

    monkeypatch.setattr(providers.urllib.request, "urlopen", open_response)
    assert providers._post_json("https://provider.invalid/v1", {}, {}) == {"ok": True}
    assert seen == [60]


def test_json_transport_rejects_oversized_response_and_closes_it(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 16)
    response = Response(b'{"secret": "REMOTE_SECRET_NEVER_ECHO"}')
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *_a, **_k: response)
    with pytest.raises(providers.ProviderError) as caught:
        providers._post_json("https://provider.invalid/v1", {}, {})
    assert caught.value.category == "response_too_large"
    assert not caught.value.retryable
    assert response.closed
    assert "REMOTE_SECRET" not in str(caught.value)


@pytest.mark.parametrize("status, category, retryable", [
    (400, "invalid_request", False), (401, "authentication", False),
    (403, "authentication", False), (404, "invalid_request", False),
    (408, "timeout", True), (429, "rate_limit", True),
    (500, "server", True), (503, "server", True),
])
def test_http_status_is_safe_typed_and_not_retried(monkeypatch, status, category, retryable):
    calls = []
    response = io.BytesIO(b"REMOTE_SECRET")

    def fail(*_args, **_kwargs):
        calls.append(True)
        raise urllib.error.HTTPError("https://provider.invalid?secret=URL_SECRET", status,
                                     "SECRET_DIAGNOSTIC", {}, response)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fail)
    with pytest.raises(providers.ProviderError) as caught:
        providers._post_json("https://provider.invalid/v1", {}, {})
    error = caught.value
    assert error.status_code == status
    assert error.category == category and error.retryable is retryable
    assert str(status) in str(error) and "SECRET" not in str(error)
    assert response.closed and len(calls) == 1


@pytest.mark.parametrize("failure, category", [
    (TimeoutError("REMOTE_SECRET"), "timeout"),
    (urllib.error.URLError("REMOTE_SECRET"), "connection"),
])
def test_connection_failures_are_retryable_without_diagnostics(monkeypatch, failure, category):
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        lambda *_a, **_k: (_ for _ in ()).throw(failure))
    with pytest.raises(providers.ProviderError) as caught:
        providers._post_json("https://provider.invalid/v1", {}, {})
    assert caught.value.category == category and caught.value.retryable
    assert "REMOTE_SECRET" not in str(caught.value)


@pytest.mark.parametrize("body", [b"not JSON REMOTE_SECRET", b"\xff", b"[]", b"null"])
def test_bad_provider_response_is_nonretryable_and_withheld(monkeypatch, body):
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *_a, **_k: Response(body))
    with pytest.raises(providers.ProviderError) as caught:
        providers._post_json("https://provider.invalid/v1", {}, {})
    assert caught.value.category == "invalid_response" and not caught.value.retryable
    assert "REMOTE_SECRET" not in str(caught.value)


def test_transport_timeout_cannot_be_increased(monkeypatch):
    seen = []
    monkeypatch.setattr(providers.urllib.request, "urlopen",
                        lambda _r, timeout: (seen.append(timeout) or Response(b"{}")))
    providers._post_json("https://provider.invalid/v1", {}, {}, timeout=600)
    providers._post_json("https://provider.invalid/v1", {}, {}, timeout=3)
    assert seen == [60, 3]


def test_sdk_clients_disable_hidden_retries_and_use_bounded_transport(monkeypatch):
    calls = []
    marker = object()
    sdk = NS()
    for name in ("Anthropic", "AnthropicBedrockMantle", "AnthropicVertex", "AnthropicFoundry"):
        def make(_name=name, **kwargs):
            calls.append((_name, kwargs))
            return NS(messages=NS(create=lambda **_kw: NS(content=[])))
        setattr(sdk, name, make)
    monkeypatch.setitem(sys.modules, "anthropic", sdk)
    monkeypatch.setattr(providers, "_sdk_http_client", lambda: marker)
    credentials = [
        {"family": "anthropic", "provider": "anthropic", "api_key": "SECRET"},
        {"family": "anthropic", "provider": "bedrock", "region": "us-east-1"},
        {"family": "anthropic", "provider": "vertex", "region": "us-east-1", "project_id": "p"},
        {"family": "anthropic", "provider": "foundry", "resource": "r", "api_key": "SECRET"},
    ]
    for credential in credentials:
        providers.make_client(credential)
    assert len(calls) == 4
    for _name, kwargs in calls:
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 60
        assert kwargs["http_client"] is marker


def test_sdk_connection_and_status_errors_are_normalized(monkeypatch):
    class APIConnectionError(Exception):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code
            super().__init__("REMOTE_SECRET")

    sdk = NS(APIConnectionError=APIConnectionError, APITimeoutError=APITimeoutError,
             APIStatusError=APIStatusError)
    for failure, category, retryable in [
        (APIConnectionError("REMOTE_SECRET"), "connection", True),
        (APITimeoutError("REMOTE_SECRET"), "timeout", True),
        (APIStatusError(429), "rate_limit", True),
        (APIStatusError(401), "authentication", False),
    ]:
        messages = NS(create=lambda **_kw: (_ for _ in ()).throw(failure))
        proxy = providers._SDKMessages(messages, sdk)
        with pytest.raises(providers.ProviderError) as caught:
            proxy.create(model="m")
        assert caught.value.category == category and caught.value.retryable is retryable
        assert "SECRET" not in str(caught.value)


def test_sdk_interruptions_pass_through():
    messages = NS(create=lambda **_kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        providers._SDKMessages(messages, NS()).create(model="m")


@pytest.mark.parametrize("compressed", [False, True])
def test_sdk_response_ceiling_applies_before_parsing_and_to_decoded_bytes(monkeypatch, compressed):
    httpx = pytest.importorskip("httpx2")
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 128)
    payload = json.dumps({"data": "SECRET" * 100}).encode()
    wire = gzip.compress(payload) if compressed else payload

    class Stream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield wire

        def close(self):
            self.closed = True

    stream = Stream()
    headers = {"Content-Encoding": "gzip"} if compressed else {}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream))
    with providers._sdk_http_client(transport=transport) as client:
        with pytest.raises(providers.ProviderError) as caught:
            client.post("https://provider.invalid/v1", json={})
    assert caught.value.category == "response_too_large"
    assert not caught.value.retryable and stream.closed


def test_sdk_transport_preserves_incremental_streaming(monkeypatch):
    httpx = pytest.importorskip("httpx2")
    consumed = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for value in [b"event: first\n\n", b"event: second\n\n"]:
                consumed.append(value)
                yield value

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    with providers._sdk_http_client(transport=transport) as client:
        with client.stream("POST", "https://provider.invalid/v1", json={}) as response:
            assert consumed == []
            iterator = response.iter_bytes()
            assert next(iterator) == b"event: first\n\n"
            assert len(consumed) == 1
            assert list(iterator) == [b"event: second\n\n"]


def test_real_sdk_mock_response_and_limit_are_compatible(monkeypatch):
    pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    response = {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "mock",
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
        "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 1},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    original_factory = providers._sdk_http_client
    monkeypatch.setattr(providers, "_sdk_http_client", lambda: original_factory(transport=transport))
    with providers.make_client({"provider": "anthropic", "family": "anthropic", "api_key": "mock-secret"}) as client:
        message = client.messages.create(model="mock", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
        assert message.content[0].text == "ok"
        assert message.usage.input_tokens == 3
        monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 8)
        with pytest.raises(providers.ProviderError) as caught:
            client.messages.create(model="mock", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
        assert caught.value.category == "response_too_large" and not caught.value.retryable


def test_sdk_stream_iteration_failure_is_normalized_and_closed(monkeypatch):
    pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 32)

    class Stream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b"event: message_start\ndata: " + b"X" * 100 + b"\n\n"

        def close(self):
            self.closed = True

    stream = Stream()
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=stream))
    original_factory = providers._sdk_http_client
    monkeypatch.setattr(providers, "_sdk_http_client", lambda: original_factory(transport=transport))
    with providers.make_client({"provider": "anthropic", "family": "anthropic", "api_key": "mock-secret"}) as client:
        with pytest.raises(providers.ProviderError) as caught:
            with client.messages.stream(model="mock", max_tokens=10,
                                        messages=[{"role": "user", "content": "hi"}]) as events:
                list(events)
    assert caught.value.category == "response_too_large"
    assert not caught.value.retryable and stream.closed


def test_real_sdk_http_failure_preserves_status_and_has_no_retry(monkeypatch):
    pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    calls = []

    def fail(request):
        calls.append(True)
        return httpx.Response(429, text="REMOTE_SECRET", headers={"retry-after": "1"})

    transport = httpx.MockTransport(fail)
    original_factory = providers._sdk_http_client
    monkeypatch.setattr(providers, "_sdk_http_client", lambda: original_factory(transport=transport))
    with providers.make_client({"provider": "anthropic", "family": "anthropic", "api_key": "mock-secret"}) as client:
        with pytest.raises(providers.ProviderError) as caught:
            client.messages.create(model="mock", max_tokens=10, messages=[{"role": "user", "content": "hi"}])
    assert caught.value.status_code == 429 and caught.value.category == "rate_limit"
    assert caught.value.retryable and len(calls) == 1
    assert "SECRET" not in str(caught.value)
