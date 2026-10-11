"""S1→S4 loop with budgets. Every handoff is one JSON object written to .amu/run/<role>-<n>.json; nothing passes by prose."""

from __future__ import annotations

import hashlib
from pathlib import Path

from amu import state, unknowns
from amu.harness import context, model, schema
from amu.harness.prompts import system_prompt
from amu.harness.roles import TOOL_MATRIX, Budget, checker_decide, make_role

_ABSENT = object()


def _budget_record(value: object, task: str | None = None) -> Budget:
    """Saved counters are a guardrail: malformed state cannot reset them."""
    if (not isinstance(value, dict) or not isinstance(value.get("task"), str)
            or not value["task"] or (task is not None and value["task"] != task)):
        raise state.StateReadError("checker budget: invalid task identity; restore or repair this state file")
    expected = Budget().max
    if (not isinstance(value.get("max"), dict) or set(value["max"]) != set(expected)
            or any(type(value["max"][key]) is not int or value["max"][key] != limit
                   for key, limit in expected.items())
            or not isinstance(value.get("used"), dict) or set(value["used"]) != set(expected)):
        raise state.StateReadError("checker budget: invalid limits or counters; restore or repair this state file")
    try:
        return Budget(used=value["used"])
    except ValueError as exc:
        raise state.StateReadError(f"checker budget: {exc}; restore or repair this state file") from exc


def _budget_name(task: str) -> str:
    return f"run/budgets/{hashlib.sha256(task.encode('utf-8')).hexdigest()}.json"


class Orchestrator:
    def __init__(self, repo: str = ".", task: str = "t1"):
        if not isinstance(task, str) or not task:
            raise ValueError("task must be a nonempty string")
        self.repo = repo
        self.task = task
        self.run_dir = state.amu_dir(repo) / "run"
        self.run_dir.mkdir(exist_ok=True)
        # All role artifacts and budgets share one lock. Per-instance snapshots
        # are only for display; each write reloads authoritative state.
        self.transaction = self.run_dir / "transaction"
        with state._json_write_lock(self.transaction):
            self.n = self._last_number_locked()
            self.budget = self._load_budget_locked()
        self.memory = state.read_json("memory.json", repo, default={"entries": []})

    def reset_budget(self) -> None:
        """Explicitly reset this task when its new contract is approved."""
        with state._json_write_lock(self.transaction):
            self._load_budget_locked()
            self.budget = Budget()
            self._save_budget_locked()

    def _load_budget_locked(self) -> Budget:
        # Migrate the old shared file before another task replaces its mirror.
        # The task-specific file becomes authoritative once present. A failed
        # mirror write must not undo an already persisted spend.
        legacy = state.read_json("run/budget.json", self.repo, default=_ABSENT)
        if legacy is not _ABSENT:
            _budget_record(legacy)
            legacy_name = _budget_name(legacy["task"])
            migrated = state.read_json(legacy_name, self.repo, default=_ABSENT)
            if migrated is _ABSENT:
                state._write_json_atomic(state.amu_dir(self.repo) / legacy_name, legacy)
            else:
                _budget_record(migrated, legacy["task"])
        saved = state.read_json(_budget_name(self.task), self.repo, default=_ABSENT)
        return Budget() if saved is _ABSENT else _budget_record(saved, self.task)

    def _save_budget_locked(self) -> None:
        record = {"task": self.task, **self.budget.to_dict()}
        _budget_record(record, self.task)
        state._write_json_atomic(state.amu_dir(self.repo) / _budget_name(self.task), record)
        state._write_json_atomic(self.run_dir / "budget.json", record)

    def _last_number_locked(self) -> int:
        sequence = state.read_json("run/sequence.json", self.repo, default=_ABSENT)
        last = 0
        if sequence is not _ABSENT:
            if (not isinstance(sequence, dict) or type(sequence.get("schema_version")) is not int
                    or sequence["schema_version"] != 1 or type(sequence.get("last")) is not int
                    or sequence["last"] < 0):
                raise state.StateReadError("run sequence: invalid counter; restore or repair this state file")
            last = sequence["last"]
        # Include pre-counter artifacts and files from older clients. No role
        # may reuse another role's ID, even after a removed artifact or crash.
        for path in self.run_dir.glob("*-*.json"):
            suffix = path.stem.rsplit("-", 1)[-1]
            if suffix.isascii() and suffix.isdigit():
                last = max(last, int(suffix))
        return last

    def handoff(self, role: str, payload: dict) -> dict:
        with state._json_write_lock(self.transaction):
            return self._handoff_locked(role, payload)

    def _handoff_locked(self, role: str, payload: dict) -> dict:
        if role not in TOOL_MATRIX:
            raise ValueError("unknown handoff role")
        payload = {**{"decision": "", "confidence": 0.0, "escalate": False, "evidence": [], "next": ""}, **payload, "task": self.task, "role": role}
        state.validate("handoff", payload)
        self.n = self._last_number_locked() + 1
        # Reserve before writing: an interrupted handoff leaves a gap rather
        # than allowing its identifier to refer to a later, different decision.
        state._write_json_atomic(self.run_dir / "sequence.json", {"schema_version": 1, "last": self.n})
        artifact = self.run_dir / f"{role}-{self.n}.json"
        if artifact.exists() or artifact.is_symlink():
            raise state.StateReadError("run artifact already exists; refusing to overwrite a handoff")
        state._write_json_atomic(artifact, payload)
        return payload

    def answered(self, amu_map: dict) -> dict[str, dict]:
        return unknowns.review(self.repo, amu_map)["confirmed"]

    def _unknown_gate(self, amu_map: dict) -> dict | None:
        nodes = [n for n in amu_map.get("nodes", []) if n.get("confidence") == "unknown"]
        count = amu_map.get("resolution", {}).get("unknown", 0)
        evidence = amu_map.get("graph_evidence")
        graph_blocked = isinstance(evidence, dict) and evidence.get("approvable") is not True
        if not nodes and not count and not graph_blocked:
            return None
        review = unknowns.review(self.repo, amu_map)
        unresolved = review["unanswered"]
        if unresolved or count != len(nodes) or review["binding_error"] or graph_blocked:
            reasons = list(unresolved)
            if count != len(nodes):
                reasons.append("unknown count does not match the available questions; re-map")
            if review["binding_error"]:
                reasons.append(review["binding_error"])
            if graph_blocked:
                reasons.append("graph evidence is incomplete; human answers cannot repair it")
            return {
                "decision": "ask", "confidence": 0.5, "escalate": True, "evidence": reasons,
                "next": "run amu answer to review unanswered questions; repair stale or incomplete graph evidence with amu map",
            }
        return None

    def plan_decision(self, amu_map: dict) -> dict:
        res = amu_map["resolution"]
        hot = [e["text"] for e in self.memory.get("entries", []) if e["kind"] == "hot_spot"]
        blocked = self._unknown_gate(amu_map)
        if blocked:
            return self.handoff("planner", blocked)
        d = {"decision": "proceed", "confidence": 0.8 if not hot else 0.7, "escalate": False,
             "evidence": [f"sound {res['sound']}", f"guessed {res['guessed']}"] + hot[:3], "next": amu_map["next"]}
        if model.available():
            try:
                prompt = system_prompt("planner")
                packet = model.planner_packet(prompt, {"map": amu_map, "hot_spots": hot}, self.repo)
                m = model.ask_json(prompt, packet, model.model_id(self.repo), self.repo)
            except context.ContextLimitError as exc:
                m = model._manual(str(exc))
            try:
                d = schema.planner_reply(m)
            except (ValueError, TypeError):
                d = model._manual("invalid planner response; review by hand")
            # A provider call can outlive a source edit. Revalidate the answers
            # after it returns before accepting any proposed decision.
            d = self._unknown_gate(amu_map) or d
        return self.handoff("planner", d)

    def phase_brief(self, contract: dict, phase: int, amu_map: dict) -> dict:
        p = next((candidate for candidate in contract.get("phases", []) if candidate.get("n") == phase), None)
        if p is None:
            raise ValueError(f"phase {phase} is not in the approved contract")
        unknown_files = {n["path"] for n in amu_map["nodes"] if n["confidence"] == "unknown"}
        role = make_role("builder", set(p["files"]), unknown_files)
        nodes = [n for n in amu_map["nodes"] if n["id"] in p["nodes"]]
        brief = {"decision": "brief", "confidence": 1.0, "escalate": not model.available(), "evidence": p["nodes"],
                 "next": p["verify"], "phase": p, "nodes": nodes, "frozen_signatures": contract["frozen_signatures"],
                 "allowed_files": sorted(role.allowed_files), "mode": "model" if model.available() else "manual",
                 "skills": [Path(s).parent.name for s in (state.amu_dir(self.repo) / "skills").glob("*/SKILL.md")]}
        return self.handoff("builder", brief)

    def brief_markdown(self, brief: dict) -> str:
        p = brief["phase"]
        lines = [f"## amu phase {p['n']} · {p['kind']} (TWC Thugs)", f"reason: {p['reason']}", f"allowed files: {', '.join(brief['allowed_files']) or '—'}",
                 f"frozen signatures: {', '.join(brief['frozen_signatures']) or '—'}", "nodes:"]
        lines += [f"- {n['id']} {n['path']}#{n['symbol']} · {n['confidence']} · {n['risk']} · verify: {n['verify']}" for n in brief["nodes"]]
        lines.append(f"when done: `{p['verify']}`")
        return "\n".join(lines)

    def on_check(self, check_result: dict) -> dict:
        with state._json_write_lock(self.transaction):
            self.budget = self._load_budget_locked()
            decision = checker_decide(check_result, self.budget)
            self._save_budget_locked()
            return self._handoff_locked("checker", decision)
