"""Nested shape checks for saved records; old partial records remain inspectable.

This is structural validation, not lifecycle proof. Completion requires its
existing source, scope and successful-run checks independently.
"""

from __future__ import annotations

import math


class InvalidState(ValueError):
    pass


def _error(field: str):
    raise InvalidState(f"invalid {field} field; restore or repair this state file")


def fields(value: dict, rules: dict, prefix: str = ""):
    for key, expected in rules.items():
        if key in value and type(value[key]) not in (expected if isinstance(expected, tuple) else (expected,)):
            _error(prefix + key)


def strings(value, field):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _error(field)


def rows(value, field, check):
    if not isinstance(value, list):
        _error(field)
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            _error(f"{field}[{index}]")
        check(item, f"{field}[{index}].")


def violation(value, prefix):
    fields(value, {"kind": str, "symbol": str, "detail": str, "severity": str}, prefix)
    if "severity" in value and value["severity"] not in {"ERROR", "WARNING"}:
        _error(prefix + "severity")


def node(value, prefix):
    fields(value, {"id": str, "path": str, "symbol": (str, type(None)), "confidence": str,
                   "risk": str, "feature": str, "order": int, "action": str,
                   "via": (str, type(None)), "reason": str, "verify": str}, prefix)
    for key, choices in (("confidence", {"sound", "guessed", "unknown"}),
                         ("risk", {"will_break", "might_break", "unknown", "none"})):
        if key in value and value[key] not in choices:
            _error(prefix + key)


def phase(value, prefix):
    fields(value, {"n": int, "kind": str, "nodes": list, "files": list, "tests": list,
                   "reason": str, "verify": str, "open_questions": list}, prefix)
    if "n" in value and value["n"] < 1:
        _error(prefix + "n")
    for key in ("nodes", "files", "tests", "open_questions"):
        if key in value:
            strings(value[key], prefix + key)


def history(value, prefix):
    fields(value, {"passed": bool, "source_stable": bool, "version": int,
                   "event": str, "phase": (int, type(None)), "violations": list}, prefix)
    if "violations" in value:
        rows(value["violations"], prefix + "violations", violation)


def memory(value, prefix):
    fields(value, {"id": str, "kind": str, "text": str, "evidence": list, "sha": str,
                   "file_hash": str, "created": str, "last_seen": str, "pinned": bool}, prefix)
    if "evidence" in value:
        strings(value["evidence"], prefix + "evidence")


def answer(value, prefix):
    fields(value, {"id": str, "node": str, "answer": str, "who": str, "confirmed": bool,
                   "binding": dict, "version": int}, prefix)


def refresh(value, prefix):
    fields(value, {"complete": bool, "target": str, "live_target": str, "change": str,
                   "baseline": dict, "sync": (dict, type(None)), "worktree": (dict, type(None)),
                   "edge_delta": dict}, prefix)


def check(name: str, value: dict):
    if name == "map.json":
        fields(value, {"root": dict, "nodes": list, "ripples": list, "features": dict,
                       "resolution": dict, "graph_evidence": dict, "depth": int, "map_hash": str})
        if "nodes" in value:
            rows(value["nodes"], "nodes", node)
        if "ripples" in value:
            rows(value["ripples"], "ripples", lambda *_: None)
    elif name == "contract.json":
        fields(value, {"targets": list, "phases": list, "allowed_files": list, "frozen_signatures": list})
        if "targets" in value:
            rows(value["targets"], "targets", lambda item, p: fields(item, {"symbol": str, "change_class": str}, p))
        if "phases" in value:
            rows(value["phases"], "phases", phase)
        for key in ("allowed_files", "frozen_signatures"):
            if key in value:
                strings(value[key], key)
    elif name == "state.json":
        if "nodes" in value and any(not isinstance(k, str) or v not in {"planned", "editing", "green", "red", "drift"}
                                    for k, v in value["nodes"].items()):
            _error("nodes")
        for key, validator in (("history", history), ("violations", violation)):
            if key in value:
                rows(value[key], key, validator)
    elif name == "memory.json":
        for key in ("entries", "history"):
            if key in value:
                rows(value[key], key, memory)
    elif name == "unknown-answers.json" and "answers" in value:
        rows(value["answers"], "answers", answer)
    elif name == "refresh-state.json":
        fields(value, {"targets": dict})
        for record in value.get("targets", {}).values():
            if not isinstance(record, dict):
                _error("targets record")
            refresh(record, "targets record.")
    elif name == "sync-state.json":
        fields(value, {"readiness": dict, "worktree": dict, "sha": str, "code_cache_reused": bool})
    elif name == "config.json":
        fields(value, {"docs": dict, "outbound": dict, "web": dict, "model_limits": dict,
                       "test_cmd": str, "build_cmd": str, "docs_dir": str, "model": str})
        if "docs" in value:
            fields(value["docs"], {"mode": str}, "docs.")
    elif name == "nav.json" and "stack" in value:
        rows(value["stack"], "stack", lambda item, p: fields(item, {"kind": str, "target": str, "at": str}, p))


def generated(kind: str, value: dict):
    names = {"map": "map.json", "contract": "contract.json", "state": "state.json", "memory_entry": ""}
    if kind in names:
        check(names[kind], value)
    if kind == "node":
        node(value, "node.")
    if kind == "handoff":
        fields(value, {"decision": str, "confidence": (int, float), "escalate": bool, "evidence": list, "next": str})
        number = value["confidence"]
        if not math.isfinite(number) or not 0 <= number <= 1:
            _error("confidence")
        strings(value["evidence"], "evidence")
    if kind == "memory_entry":
        memory(value, "memory entry.")
