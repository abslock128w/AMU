"""Local, fail-closed validation at model-controlled boundaries."""

import math


def planner_reply(value: object) -> dict:
    fields = {"decision", "confidence", "escalate", "evidence", "next"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("planner response must contain exactly the required fields")
    if value["decision"] not in ("proceed", "narrow", "ask", "manual"):
        raise ValueError("unknown planner decision")
    confidence = value["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a finite number between zero and one")
    if type(value["escalate"]) is not bool:
        raise ValueError("escalate must be a boolean")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 100 or any(not isinstance(x, str) or len(x) > 4000 for x in evidence):
        raise ValueError("evidence must be a bounded list of strings")
    if not isinstance(value["next"], str) or not value["next"].strip() or len(value["next"]) > 4000:
        raise ValueError("next must be a nonempty bounded string")
    if value["decision"] == "proceed" and value["escalate"]:
        raise ValueError("proceed cannot also request escalation")
    return value


def tool_input(value: object, schema: dict) -> dict:
    props = schema["properties"]
    if not isinstance(value, dict) or set(value) != set(props):
        raise ValueError("tool input must contain exactly its declared fields")
    for name, rule in props.items():
        item = value[name]
        if rule["type"] == "string" and (not isinstance(item, str) or not item.strip() or len(item) > 16000):
            raise ValueError(f"{name} must be a nonempty bounded string")
        if rule["type"] == "integer" and (type(item) is not int or item < 1):
            raise ValueError(f"{name} must be a positive integer")
        if "enum" in rule and item not in rule["enum"]:
            raise ValueError(f"{name} is not a supported value")
    if "start" in value and "end" in value and value["end"] < value["start"]:
        raise ValueError("end must not precede start")
    return value
