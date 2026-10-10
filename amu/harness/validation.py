"""Closed model/tool schemas. Provider 'strict' flags are not a trust boundary."""

from __future__ import annotations

import math

from amu.harness import context


class ResponseError(ValueError):
    """Safe diagnostic that never embeds model output or tool arguments."""


def decision(value: object) -> dict:
    fields = {"decision", "confidence", "escalate", "evidence", "next"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ResponseError("model response must contain exactly the five decision fields")
    if value["decision"] not in ("proceed", "narrow", "ask", "manual"):
        raise ResponseError("model returned an unsupported decision")
    confidence = value["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ResponseError("model confidence must be a finite number between zero and one")
    if type(value["escalate"]) is not bool:
        raise ResponseError("model escalate must be a boolean")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 100 or any(not isinstance(s, str) or len(s) > 2000 for s in evidence):
        raise ResponseError("model evidence must be a bounded list of strings")
    if not isinstance(value["next"], str) or not value["next"].strip() or len(value["next"]) > 4000:
        raise ResponseError("model next must be a nonempty bounded string")
    if value["decision"] in ("ask", "manual") and value["escalate"] is not True:
        raise ResponseError("ask/manual decisions must escalate")
    return dict(value)


def tool_input(name: str, value: object, definitions: list[dict]) -> None:
    spec = next((item["input_schema"] for item in definitions if item["name"] == name), None)
    if spec is None:
        return  # The role allowlist reports unknown tools without executing them.
    if not isinstance(value, dict) or set(value) != set(spec["properties"]):
        raise ResponseError("tool arguments must match the declared fields exactly")
    for field, rule in spec["properties"].items():
        item = value[field]
        if rule["type"] == "string" and (not isinstance(item, str) or not item.strip() or len(item) > 8000):
            raise ResponseError("tool text arguments must be nonempty bounded strings")
        if rule["type"] == "integer" and (type(item) is not int or not 1 <= item <= 10_000_000):
            raise ResponseError("tool line arguments must be positive bounded integers")
        if "enum" in rule and item not in rule["enum"]:
            raise ResponseError("tool argument is outside the declared choices")
    if name == "read_lines" and value["end"] < value["start"]:
        raise ResponseError("tool end line must not precede its start line")


def message(value: object) -> None:
    content = getattr(value, "content", None)
    if not isinstance(content, list) or len(content) > 32:
        raise ResponseError("provider content must be a bounded list of blocks")
    if context.estimate(content) > 65536:
        raise ResponseError("provider content exceeded the response limit")
    ids = set()
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "text":
            text = getattr(block, "text", None)
            if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
                raise ResponseError("provider text block is invalid or oversized")
        elif kind == "tool_use":
            ident, name = getattr(block, "id", None), getattr(block, "name", None)
            if not isinstance(ident, str) or not ident or len(ident) > 200 or ident in ids:
                raise ResponseError("provider tool call IDs must be present and unique")
            if not isinstance(name, str) or not name or len(name) > 200:
                raise ResponseError("provider tool name is invalid")
            ids.add(ident)
        elif kind not in ("thinking", "redacted_thinking"):
            raise ResponseError("provider returned an unsupported content block")
    stop = getattr(value, "stop_reason", None)
    if stop not in ("end_turn", "tool_use", "refusal", "max_tokens", "stop_sequence"):
        raise ResponseError("provider returned an unsupported stop reason")
    if bool(ids) != (stop == "tool_use"):
        raise ResponseError("provider tool calls do not match its stop reason")
