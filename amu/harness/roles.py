"""Roles with fixed tool sets. A role literally cannot call a tool outside its set."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class ToolNotGranted(PermissionError):
    pass


class WriteRefused(PermissionError):
    pass


TOOL_MATRIX = {
    "router": {"graph.search", "graph.def", "memory.read"},
    "mapper": {"graph.impact", "graph.neighbors", "graph.def", "memory.read"},
    "planner": {"map.read", "memory.read", "plan.write"},
    "builder": {"map.read", "contract.read", "skills.read", "files.write_allowed", "tests.run"},
    "checker": {"check.read", "graph.diff", "tests.run", "memory.read"},
    "sweeper": {"graph.diff", "graph.impact", "contract.read", "memory.read"},
    "doc_agent": {"graph.diff", "docs.read", "docs.draft"},
    "memory": {"state.read", "contract.read", "run.read", "memory.write"},
    # interactive assistant: read-only graph + amu commands; no source writes, no contract writes
    "assistant": {"chat.graph_search", "chat.graph_def", "chat.amu_map", "chat.amu_graph", "chat.amu_where", "chat.amu_state", "chat.read_lines", "chat.answer_unknown"},
}


@dataclass(frozen=True)
class Role:
    name: str
    tools: frozenset = field(default_factory=frozenset)
    allowed_files: frozenset = field(default_factory=frozenset)
    unknown_files: frozenset = field(default_factory=frozenset)

    def call(self, tool: str, fn, *args, **kwargs):
        if tool not in self.tools:
            raise ToolNotGranted(f"role {self.name} has no tool {tool}")
        return fn(*args, **kwargs)

    def write_path(self, path: str, content: str, repo: str = ".") -> Path:
        if "files.write_allowed" not in self.tools:
            raise ToolNotGranted(f"role {self.name} cannot write files")
        if path not in self.allowed_files:
            raise WriteRefused(f"{path} is outside the contract's allowed_files")
        if path in self.unknown_files:
            raise WriteRefused(f"{path} is an unknown graph region and requires a human answer before editing")
        raw_path = Path(path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise WriteRefused(f"{path} is not a repository-relative file")
        root = Path(repo).resolve()
        try:
            p = (root / raw_path).resolve()
            resolved_relative = p.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            raise WriteRefused(f"{path} resolves outside the repository") from None
        # Staying under the repository is necessary but not sufficient: an
        # allowed symlink could still redirect a write into a different,
        # unapproved in-repository file.  The resolved target must remain the
        # exact contracted relative path as well.
        if resolved_relative != raw_path:
            raise WriteRefused(f"{path} resolves to a different file than the contracted path")
        p.write_text(content)
        return p


def make_role(name: str, allowed_files: set | None = None, unknown_files: set | None = None) -> Role:
    return Role(name, frozenset(TOOL_MATRIX[name]), frozenset(allowed_files or ()), frozenset(unknown_files or ()))


class Budget:
    """Checker budget: max 2 retries, max 2 widenings, 1 sweep; the 3rd retry escalates."""

    def __init__(self, retries: int = 2, widenings: int = 2, sweeps: int = 1, used: dict | None = None):
        self.max = {"retry": retries, "widen": widenings, "sweep": sweeps}
        if any(type(value) is not int or value < 0 for value in self.max.values()):
            raise ValueError("budget limits must be nonnegative integers")
        raw_used = {} if used is None else used
        if not isinstance(raw_used, dict) or set(raw_used) - self.max.keys():
            raise ValueError("budget used must be an object with retry, widen, or sweep counters")
        self.used = {kind: raw_used.get(kind, 0) for kind in self.max}
        if any(type(value) is not int or value < 0 or value > self.max[kind]
               for kind, value in self.used.items()):
            raise ValueError("budget counters must be nonnegative integers within their limits")

    def spend(self, kind: str) -> bool:
        if self.used[kind] >= self.max[kind]:
            return False
        self.used[kind] += 1
        return True

    def as_str(self) -> str:
        return " ".join(f"{k} {v}/{self.max[k]}" for k, v in self.used.items())

    def to_dict(self) -> dict:
        return {"max": dict(self.max), "used": dict(self.used)}


def checker_decide(check_result: dict, budget: Budget) -> dict:
    """Deterministic checker: blocking violations cannot be downgraded; budget exhaustion escalates."""
    violations = check_result.get("violations", [])
    blocking = [v for v in violations if v["severity"] == "ERROR"]
    escalate_kinds = {"FROZEN_SIGNATURE", "UNDECLARED_REMOVAL", "UNKNOWN_TOUCHED"}
    protected = [v for v in violations if v["kind"] in escalate_kinds]
    if protected:
        return {"decision": "escalate", "confidence": 0.9, "escalate": True, "evidence": [v["symbol"] for v in protected],
                "next": "human: review the protected contract or unknown-node violation before continuing"}
    if not blocking:
        return {"decision": "proceed", "confidence": 1.0, "escalate": False, "evidence": [], "next": "amu check --phase next"}
    kinds = {v["kind"] for v in blocking}
    if {"OUT_OF_SCOPE", "PHASE_DRIFT"} & kinds and budget.spend("widen"):
        return {"decision": "widen", "confidence": 0.6, "escalate": False, "evidence": [v["symbol"] for v in blocking], "next": "amu map --depth 2 then amu plan --approve"}
    if budget.spend("retry"):
        return {"decision": "retry", "confidence": 0.5, "escalate": False, "evidence": [v["symbol"] for v in blocking], "next": "revert the drifted file and re-run amu check"}
    return {"decision": "escalate", "confidence": 0.9, "escalate": True, "evidence": [v["symbol"] for v in blocking],
            "next": "human: the same blocking violation persisted after the retry budget"}
