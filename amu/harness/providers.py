"""Bring-your-own-provider clients.

Anthropic-family providers use their official SDK clients. OpenAI-compatible
providers and the native Gemini Developer API use small stdlib adapters that
present the same ``.messages.create(...)`` surface to the chat loop.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from types import SimpleNamespace as NS

from amu.keys import models_disabled, validate_endpoint

REQUEST_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ProviderError(RuntimeError):
    """A safe diagnostic plus metadata for the caller's bounded retry policy.

    Transport code never retries. ``retryable`` describes connection failures,
    rate limits and server failures; it does not promise a retry will succeed.
    """

    def __init__(self, message: str, *, category: str = "unknown", retryable: bool = False,
                 status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status_code = status_code


def _http_error(status: int) -> ProviderError:
    category = ("authentication" if status in {401, 403} else
                "rate_limit" if status == 429 else
                "timeout" if status == 408 else
                "server" if 500 <= status <= 599 else "invalid_request")
    return ProviderError(f"provider HTTP {status}; response body withheld", category=category,
                         retryable=category in {"rate_limit", "timeout", "server"}, status_code=status)


def _bounded_timeout(timeout) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        value = 0
    if not math.isfinite(value) or value <= 0:
        raise ProviderError("provider timeout must be positive", category="invalid_request")
    return min(value, REQUEST_TIMEOUT_SECONDS)


def _response_too_large() -> ProviderError:
    return ProviderError("provider response exceeded the byte limit; response body withheld",
                         category="response_too_large")


def _sdk_http_client(*, transport=None):
    """Bound decoded SDK response bytes while retaining incremental SSE reads.

    HTTPX is an SDK dependency, imported only for Anthropic-family providers.
    Its timeouts bound each connect/read/write operation, not the total length
    of a streaming conversation. The response ceiling also bounds streamed data.
    """
    import httpx2 as httpx

    class BoundedStream(httpx.SyncByteStream):
        def __init__(self, response):
            self.response = response

        def __iter__(self):
            total = 0
            try:
                for chunk in self.response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise _response_too_large()
                    yield chunk
            finally:
                self.response.close()

        def close(self):
            self.response.close()

    class BoundedClient(httpx.Client):
        def send(self, request, *, stream=False, **kwargs):
            response = super().send(request, stream=True, **kwargs)
            if response.status_code >= 400:
                response.close()
                raise _http_error(response.status_code)
            # The inner response performs content decoding. The outer response
            # must not decode it again (gzip output is subject to the same cap).
            headers = response.headers.copy()
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            bounded = httpx.Response(response.status_code, headers=headers,
                                     stream=BoundedStream(response), request=response.request,
                                     extensions=response.extensions, history=response.history)
            if not stream:
                try:
                    bounded.read()
                except Exception:
                    bounded.close()
                    raise
            return bounded

    return BoundedClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=transport)


def _sdk_error(exc: Exception, sdk) -> ProviderError:
    # The SDK wraps HTTP-client failures in APIConnectionError. Preserve our
    # original limit/status category rather than misclassifying it as retryable.
    cause = exc
    for _ in range(4):
        if isinstance(cause, ProviderError):
            return cause
        cause = cause.__cause__
        if cause is None:
            break
    if isinstance(exc, getattr(sdk, "APIStatusError", ())):
        return _http_error(exc.status_code)
    if isinstance(exc, getattr(sdk, "APITimeoutError", ())):
        return ProviderError("provider request timed out; remote diagnostics withheld",
                             category="timeout", retryable=True)
    if isinstance(exc, getattr(sdk, "APIConnectionError", ())):
        return ProviderError("provider unreachable; remote diagnostics withheld",
                             category="connection", retryable=True)
    return ProviderError("provider response failed; remote diagnostics withheld", category="invalid_response")


class _SDKMessages:
    def __init__(self, messages, sdk):
        self._messages = messages
        self._sdk = sdk

    def __getattr__(self, name):
        return getattr(self._messages, name)

    def create(self, **kwargs):
        kwargs["timeout"] = _bounded_timeout(kwargs.get("timeout", REQUEST_TIMEOUT_SECONDS))
        try:
            return self._messages.create(**kwargs)
        except Exception as exc:
            raise _sdk_error(exc, self._sdk) from None

    @contextmanager
    def stream(self, **kwargs):
        kwargs["timeout"] = _bounded_timeout(kwargs.get("timeout", REQUEST_TIMEOUT_SECONDS))
        try:
            with self._messages.stream(**kwargs) as stream:
                yield stream
        except Exception as exc:
            raise _sdk_error(exc, self._sdk) from None


def make_client(cred: dict):
    if models_disabled():
        raise ProviderError("model requests disabled by AMU_DISABLE_MODELS=1", category="disabled")
    validate_endpoint(cred.get("base_url"))
    fam, prov = cred.get("family"), cred.get("provider")
    if fam == "gemini":
        return GeminiClient(cred["base_url"], cred.get("api_key"))
    if fam == "openai":
        return OpenAICompatClient(cred["base_url"], cred.get("api_key"), cred.get("provider", "openai-compatible"))
    import anthropic
    http_client = _sdk_http_client()
    kw = {"timeout": REQUEST_TIMEOUT_SECONDS, "max_retries": 0, "http_client": http_client}
    try:
        if prov == "bedrock":
            client = anthropic.AnthropicBedrockMantle(aws_region=cred["region"], **kw)
        elif prov == "vertex":
            client = anthropic.AnthropicVertex(project_id=cred["project_id"], region=cred["region"], **kw)
        elif prov == "foundry":
            client = anthropic.AnthropicFoundry(api_key=cred["api_key"], resource=cred["resource"], **kw)
        else:
            if cred.get("api_key"):
                kw["api_key"] = cred["api_key"]
            if cred.get("base_url"):
                kw["base_url"] = cred["base_url"]
            client = anthropic.Anthropic(**kw)
    except Exception:
        if hasattr(http_client, "close"):
            http_client.close()
        raise
    client.messages = _SDKMessages(client.messages, anthropic)
    return client


def supports_anthropic_params(cred: dict) -> bool:
    return cred.get("family") == "anthropic"


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter (Ollama, LM Studio, OpenRouter, vLLM, …)
# ---------------------------------------------------------------------------

def _post_json(url: str, body: dict, headers: dict, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    validate_endpoint(url)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_bounded_timeout(timeout)) as r:
            raw = r.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise _response_too_large()
            try:
                result = json.loads(raw.decode())
            except (ValueError, RecursionError):
                raise ProviderError("provider returned invalid JSON; response body withheld",
                                    category="invalid_response") from None
            if not isinstance(result, dict):
                raise ProviderError("provider response must be a JSON object; response body withheld",
                                    category="invalid_response")
            return result
    except urllib.error.HTTPError as exc:
        exc.close()
        raise _http_error(exc.code) from None
    except TimeoutError:
        raise ProviderError("provider request timed out; remote diagnostics withheld",
                            category="timeout", retryable=True) from None
    except (urllib.error.URLError, OSError) as exc:
        timed_out = isinstance(getattr(exc, "reason", None), TimeoutError)
        category = "timeout" if timed_out else "connection"
        raise ProviderError(f"provider {category} failure; remote diagnostics withheld",
                            category=category, retryable=True) from None


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}} for t in tools or []]


def to_openai_messages(system, messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    sys_text = system if isinstance(system, str) else "".join(b.get("text", "") for b in (system or []))
    if sys_text:
        out.append({"role": "system", "content": sys_text})
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text")
            calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}} for b in content if getattr(b, "type", "") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
                elif isinstance(b, dict) and b.get("type") == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def from_openai_response(resp: dict):
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []
    if msg.get("content"):
        blocks.append(NS(type="text", text=msg["content"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_raw": fn.get("arguments")}
        blocks.append(NS(type="tool_use", id=tc.get("id") or f"call_{len(blocks)}", name=fn.get("name"), input=args))
    finish = choice.get("finish_reason")
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else ("max_tokens" if finish == "length" else ("refusal" if finish == "content_filter" else "end_turn"))
    return NS(content=blocks, stop_reason=stop, model=resp.get("model"), usage=resp.get("usage"))


class _Messages:
    def __init__(self, base_url: str, api_key: str | None, provider: str):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if provider == "openrouter":
            self.headers["HTTP-Referer"] = "https://github.com/fayasfarmisofficial-cyber/TWC-Thugs"

    def create(self, *, model: str, max_tokens: int, messages: list[dict], system=None, tools=None, **_ignored):
        body = {"model": model, "max_tokens": max_tokens, "messages": to_openai_messages(system, messages)}
        if tools:
            body["tools"] = to_openai_tools(tools)
        return from_openai_response(_post_json(self.url, body, self.headers))


class OpenAICompatClient:
    """Duck-types the slice of anthropic.Anthropic the harness uses: client.messages.create(...). No streaming."""

    def __init__(self, base_url: str, api_key: str | None, provider: str = "openai-compatible"):
        self.messages = _Messages(base_url, api_key, provider)
        self.provider = provider


# ---------------------------------------------------------------------------
# Native Gemini Developer API adapter
# ---------------------------------------------------------------------------


def _text_from_system(system) -> str:
    if isinstance(system, str):
        return system
    return "".join(block.get("text", "") for block in (system or []) if isinstance(block, dict))


def _gemini_schema(value):
    """Translate the small JSON-Schema subset AMU uses to Gemini's Schema.

    Gemini accepts OpenAPI-style schemas for function declarations but rejects
    JSON-Schema-only ``additionalProperties`` on some model versions.  AMU's
    tools are closed objects already, so omitting that hint does not expand
    what the local tool dispatcher can execute.
    """
    if isinstance(value, list):
        return [_gemini_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: _gemini_schema(item) for key, item in value.items() if key != "additionalProperties"}


def to_gemini_tools(tools: list[dict]) -> list[dict]:
    declarations = [
        {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": _gemini_schema(tool["input_schema"]),
        }
        for tool in (tools or [])
    ]
    return [{"functionDeclarations": declarations}] if declarations else []


def _tool_names(messages: list[dict]) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "assistant" or isinstance(message.get("content"), str):
            continue
        for block in message.get("content") or []:
            if getattr(block, "type", "") == "tool_use" and getattr(block, "id", None):
                names[block.id] = block.name
    return names


def _function_response(content, is_error: bool) -> dict:
    """Gemini requires an object as ``functionResponse.response``."""
    try:
        result = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        result = content
    response = {"result": result}
    if is_error:
        response["error"] = True
    return response


def to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Convert the Anthropic-shaped chat history to native Gemini contents."""
    out: list[dict] = []
    names = _tool_names(messages)
    for message in messages:
        role, content = message["role"], message["content"]
        if isinstance(content, str):
            out.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
            continue
        parts: list[dict] = []
        if role == "assistant":
            for block in content:
                if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                    parts.append({"text": block.text})
                elif getattr(block, "type", "") == "tool_use":
                    call = {"name": block.name, "args": block.input}
                    if getattr(block, "id", None):
                        call["id"] = block.id
                    parts.append({"functionCall": call})
            if parts:
                out.append({"role": "model", "parts": parts})
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                call_id = block.get("tool_use_id")
                response = {
                    "name": names.get(call_id, "unknown_tool"),
                    "response": _function_response(block.get("content", ""), bool(block.get("is_error"))),
                }
                if call_id:
                    response["id"] = call_id
                parts.append({"functionResponse": response})
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append({"text": block["text"]})
        if parts:
            out.append({"role": "user", "parts": parts})
    return out


def from_gemini_response(resp: dict):
    candidates = resp.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    blocks = []
    for index, part in enumerate((candidate.get("content") or {}).get("parts") or []):
        if part.get("text"):
            blocks.append(NS(type="text", text=part["text"]))
        call = part.get("functionCall") or {}
        if call:
            args = call.get("args") or {}
            if not isinstance(args, dict):
                args = {"_raw": args}
            blocks.append(NS(type="tool_use", id=call.get("id") or f"call_{index}", name=call.get("name"), input=args))
    finish = candidate.get("finishReason")
    blocked = bool((resp.get("promptFeedback") or {}).get("blockReason"))
    if any(block.type == "tool_use" for block in blocks):
        stop = "tool_use"
    elif finish == "MAX_TOKENS":
        stop = "max_tokens"
    elif blocked or finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
        stop = "refusal"
    else:
        stop = "end_turn"
    return NS(content=blocks, stop_reason=stop, model=resp.get("modelVersion"), usage=resp.get("usageMetadata"))


class _GeminiMessages:
    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"x-goog-api-key": api_key} if api_key else {}

    def create(self, *, model: str, max_tokens: int, messages: list[dict], system=None, tools=None, **_ignored):
        model_id = str(model).removeprefix("models/")
        url = f"{self.base_url}/{urllib.parse.quote(model_id, safe='-._')}:generateContent"
        body = {
            "contents": to_gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        system_text = _text_from_system(system)
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        gemini_tools = to_gemini_tools(tools)
        if gemini_tools:
            body["tools"] = gemini_tools
        return from_gemini_response(_post_json(url, body, self.headers))


class GeminiClient:
    """Native Gemini Developer API client with the harness' message surface."""

    def __init__(self, base_url: str, api_key: str | None):
        self.messages = _GeminiMessages(base_url, api_key)
