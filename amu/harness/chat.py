"""Interactive assistant for the REPL: a tool-using loop over read-only graph tools and amu commands.
amu never writes source code, so the assistant has no write tool. Facts come from `entire graph` output the
tools return; the model can widen a map or ask, never enlarge a contract or promote confidence."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from amu import entire, graph_refresh, graphview, lifecycle, outbound, process, state, unknowns
from amu.harness import context, providers, schema, validation
from amu.harness import model as modelmod
from amu.harness.prompts import CLOSING, PREAMBLE
from amu.harness.roles import ToolNotGranted, make_role

ASSISTANT_SYSTEM = PREAMBLE.replace(
    'You answer with exactly one JSON object of the shape {"decision": str, "confidence": float, "escalate": bool, "evidence": [str], "next": str}.',
    "You are the interactive assistant (role: assistant). Answer in short plain prose with counts and buckets; cite files as path:line "
    "and symbols verbatim as the tools return them. Prefer one tool call that answers the question over several. When the user "
    "describes a change, run amu_map and then propose the exact next amu command.") + "\n\n" + CLOSING + "\n" + outbound.UNTRUSTED

CLI_STDOUT_LIMIT = 16 * 1024 * 1024
CLI_STDERR_LIMIT = 64 * 1024


def _cli(repo: str, *args: str) -> str:
    command = [sys.executable, "-I", "-m", "amu.cli", *args, "--json", "--repo", repo]
    try:
        result = process.run(command, cwd=repo, timeout=600,
                             stdout_limit=CLI_STDOUT_LIMIT, stderr_limit=CLI_STDERR_LIMIT)
    except (OSError, ValueError):
        return json.dumps({"error": "could not start amu CLI", "exit": 3})
    if result.error:
        reason = {
            "timeout": "amu CLI timed out",
            "output_limit": "amu CLI exceeded the captured-output byte limit; narrow the target",
            "missing": "could not start amu CLI",
            "io": "amu CLI output could not be safely captured",
        }.get(result.error, "amu CLI output could not be safely captured")
        return json.dumps({"error": reason, "exit": result.returncode, "error_kind": result.error,
                           "graph_evidence": {"approvable": False, "reasons": [reason]}})
    stderr = result.stderr.decode("utf-8", errors="replace").strip()[-800:]
    try:
        stdout = result.stdout.decode("utf-8").strip()
        payload = json.loads(stdout) if stdout else None
    except (ValueError, UnicodeError, RecursionError):
        return json.dumps({"error": "amu CLI returned invalid JSON", "exit": result.returncode or 1})
    if not stdout:
        return json.dumps({"error": stderr or f"amu CLI exited with status {result.returncode}",
                           "exit": result.returncode or 1})
    if result.returncode:
        if isinstance(payload, dict):
            payload.setdefault("exit", result.returncode)
            payload.setdefault("error", stderr or f"amu CLI exited with status {result.returncode}")
            return json.dumps(payload, ensure_ascii=False)
        return json.dumps({"error": stderr or f"amu CLI exited with status {result.returncode}",
                           "exit": result.returncode})
    return stdout


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Keep critical tool metadata readable without letting one field eat context."""
    if isinstance(value, str):
        return value if len(value) <= 400 else f"{value[:397]}..."
    if depth >= 3:
        return str(value)[:400]
    if isinstance(value, dict):
        return {str(key)[:80]: _compact_value(item, depth=depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [_compact_value(item, depth=depth + 1) for item in value[:20]]
    return value


def _graph_evidence_summary(value: Any) -> dict:
    """Preserve the safety verdict before any optional graph rows."""
    raw = value if isinstance(value, dict) else {}
    return {
        key: raw[key]
        for key in ("approvable", "stage", "target", "reasons", "contradicted_python_calls", "edge_baseline_errors")
        if key in raw
    }


def _bounded_tool_json(value: object, limit: int) -> str:
    """Return valid bounded JSON without dropping a graph safety verdict.

    Raw string slicing can cut a JSON object in half—and, worse, tends to cut
    the evidence and warnings appended after a long list of graph rows.  Keep
    the verdict first, then add as many optional rows as fit.
    """
    if not isinstance(value, dict):
        if context.estimate(value) <= limit:
            return context.encode(value)
        value = {"error": "tool result exceeded the context limit; narrow the target"}
    try:
        return context.encode(context.ranked_slice(value, budget_bytes=limit))
    except context.ContextLimitError as exc:
        return context.encode({"truncated": True, "error": str(exc), "warnings": [str(exc)],
                               "graph_evidence": {"approvable": False, "reasons": [str(exc)]}})


def _map_context_status(repo: str, amu_map: object, contract: object, current_state: object) -> dict:
    """Describe whether saved map data is safe to present as the current pre-edit baseline.

    The chat model receives this context before it can decide which tool to
    call.  A stale ``.amu/map.json`` must therefore not look like a current
    green graph merely because it once passed map collection.  An approved
    contract also ends the pre-approval map stage: it is useful inspection
    context, but its old ``next: amu plan --approve`` cue is no longer safe.
    """
    if not isinstance(amu_map, dict):
        return {"status": "absent", "reasons": ["no saved map is available"]}

    reasons: list[str] = []
    evidence = amu_map.get("graph_evidence")
    map_hash = amu_map.get("map_hash")
    if not isinstance(map_hash, str) or not map_hash:
        reasons.append("saved map has no map hash")
    elif not isinstance(evidence, dict):
        reasons.append("saved map has no verified graph evidence")
    elif evidence.get("map_hash") != map_hash:
        reasons.append("saved map graph evidence does not match its map hash")
    else:
        try:
            freshness_problem = graph_refresh.map_current_capture_reason(repo, evidence)
        except Exception as exc:  # Fail closed if a malformed local artifact defeats inspection.
            freshness_problem = f"could not validate saved map freshness: {type(exc).__name__}"
        if freshness_problem:
            reasons.append(freshness_problem)
        if evidence.get("approvable") is not True:
            reasons.append("saved map graph evidence is not approval-ready")

    active_contract = isinstance(contract, dict) and bool(contract)
    matching_active_contract = False
    incomplete: list[dict] = []
    if active_contract:
        if contract.get("map_hash") != map_hash:
            reasons.append("approved contract does not match the saved map")
        else:
            matching_active_contract = True
            try:
                incomplete = lifecycle.incomplete_phases(
                    contract, current_state if isinstance(current_state, dict) else {}
                )
            except Exception as exc:  # A malformed state must never be treated as a green lifecycle.
                reasons.append(f"could not validate approved-contract lifecycle: {type(exc).__name__}")

    if reasons:
        status = "inspection_only"
    elif matching_active_contract:
        status = "active_contract"
    else:
        status = "current"
    if status == "active_contract":
        # A plan is already active. The map may still help investigate a node,
        # but it is not a new approval candidate and must not carry its
        # original plan-approval instruction into model context.
        reasons.append("an approved contract is active; use refresh/check rather than re-approving this map")
    return {
        "status": status,
        "reasons": reasons,
        "incomplete_phases": [{"n": row.get("n"), "kind": row.get("kind")} for row in incomplete],
    }


def _map_context_summary(repo: str, amu_map: object, contract: object, current_state: object) -> tuple[dict | None, dict]:
    """Return a bounded, lifecycle-labelled map projection for model-visible state."""
    status = _map_context_status(repo, amu_map, contract, current_state)
    if not isinstance(amu_map, dict):
        return None, status
    evidence = _graph_evidence_summary(amu_map.get("graph_evidence"))
    if status["status"] != "current" and evidence.get("approvable") is True:
        evidence["approvable"] = False
        evidence["context_note"] = "not current approval evidence in this chat context"
    summary = {
        "status": status["status"],
        "safety": status,
        "root": _compact_value(amu_map.get("root")),
        "resolution": _compact_value(amu_map.get("resolution")),
        "graph_evidence": evidence,
    }
    if status["status"] == "current" and isinstance(amu_map.get("next"), str):
        summary["next"] = amu_map["next"]
    return summary, status


def _contract_context_summary(contract: object, status: dict) -> dict | None:
    """Keep active lifecycle state visible without presenting it as a fresh map approval."""
    if not isinstance(contract, dict) or not contract:
        return None
    phases = contract.get("phases") if isinstance(contract.get("phases"), list) else []
    return {
        "status": status["status"],
        "targets": _compact_value(contract.get("targets", [])),
        "phases": [
            (row.get("n"), row.get("kind"), len(row.get("files", [])) if isinstance(row.get("files"), list) else 0)
            for row in phases
            if isinstance(row, dict)
        ],
        "incomplete_phases": status.get("incomplete_phases", []),
    }


def tool_definitions() -> list[dict]:
    """Stable order (cache-friendly). All tools are read-only against the repo."""
    s = lambda **props: {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}  # noqa: E731
    return [
        {"name": "graph_search", "description": "Find code for a plain-language question via `entire graph search`; returns ranked hits with the signals that ranked them. Use this before naming any symbol.", "input_schema": s(query={"type": "string"}), "strict": True},
        {"name": "graph_def", "description": "Locate definition candidates with file provenance; use read_lines to inspect the exact declaration under repository outbound policy.", "input_schema": s(symbol={"type": "string"}), "strict": True},
        {"name": "amu_map", "description": "Blast map for `path#symbol` (or a file path) under a change class: sound/guessed/unknown nodes with reason + verify.", "input_schema": s(target={"type": "string"}, change={"type": "string", "enum": ["signature", "remove", "rename", "move", "body"]}), "strict": True},
        {"name": "amu_graph", "description": "Source-checked relation graph candidate for `path#symbol` from `entire graph impact` (callers, callees, type consumers, data flows, co-change). Source-proven docstring-only Python CALLS edges are excluded and disclosed; use amu_map and refresh before approving edits.", "input_schema": s(symbol={"type": "string"}), "strict": True},
        {"name": "amu_where", "description": "Definition site, feature, and whether a symbol is frozen in the current contract.", "input_schema": s(symbol={"type": "string"}), "strict": True},
        {"name": "amu_state", "description": "Saved map, contract phases, and node states from .amu/, labelled current, active-contract, or inspection-only after a local freshness check.", "input_schema": s(), "strict": True},
        {"name": "read_lines", "description": "Read up to 120 lines of a repo file (read-only). Anything read here is heuristic, never sound.", "input_schema": s(path={"type": "string"}, start={"type": "integer"}, end={"type": "integer"}), "strict": True},
        {"name": "answer_unknown", "description": "Save a model-authored suggestion for an unknown node. It remains unconfirmed until a human reviews it using amu answer --confirm. Never promotes confidence or clears a blocker.", "input_schema": s(node={"type": "string"}, answer={"type": "string"}), "strict": True},
    ]


class Assistant:
    def __init__(self, repo: str, out: Callable[[str], None] | None = None, stream: bool = True, client=None):
        self.repo = repo
        self.out = out or (lambda t: print(t, end="", flush=True))
        self.stream = stream
        self.role = make_role("assistant")
        self.cred = modelmod.credential()
        self.model = modelmod.model_id(repo)
        self.effort = state.config(repo).get("effort", "medium")
        self.messages: list[dict] = []
        self._turn_start: list[dict] = []
        self._compaction_start = 0
        self.stats = {"requests": 0, "input_bytes": 0, "output_bytes": 0, "compacted_turns": 0,
                      "provider_input_tokens": 0, "provider_output_tokens": 0, "usage_reported_requests": 0}
        self._client = client

    def session_status(self) -> dict:
        bounds = context.limits(state.config(self.repo))
        return {"usage": dict(self.stats), "limits": bounds,
                "remaining": {"requests": max(0, bounds["session_requests"] - self.stats["requests"]),
                              "input_bytes": max(0, bounds["session_input_bytes"] - self.stats["input_bytes"])}}

    # ----- tools ---------------------------------------------------------
    def _state_payload(self) -> dict:
        """Expose saved workflow state without letting stale map claims look current."""
        amu_map = state.read_json("map.json", self.repo)
        contract = state.read_json("contract.json", self.repo)
        current_state = state.read_json("state.json", self.repo, default={})
        map_summary, map_status = _map_context_summary(self.repo, amu_map, contract, current_state)
        raw_nodes = amu_map.get("nodes", []) if isinstance(amu_map, dict) else []
        nodes: list[dict] = []
        if isinstance(raw_nodes, list):
            for raw_node in raw_nodes:
                if not isinstance(raw_node, dict):
                    continue
                node = dict(raw_node)
                if map_status["status"] != "current" and node.get("confidence") == "sound":
                    node["confidence"] = "unknown"
                    original_reason = str(node.get("reason") or "resolved graph relation")
                    node["reason"] = f"inspection-only: {original_reason}"
                nodes.append(node)
        return {
            "map": map_summary,
            "nodes": nodes,
            "contract": _contract_context_summary(contract, map_status),
            "state": current_state,
        }

    def _tool(self, name: str, inp: dict) -> str:
        definition = next((d for d in tool_definitions() if d["name"] == name), None)
        if definition is None:
            raise ToolNotGranted(name)
        inp = schema.tool_input(inp, definition["input_schema"])
        fn = {
            "graph_search": lambda: self._search(inp["query"]),
            "graph_def": lambda: self._search(inp["symbol"], definitions=True),
            "amu_map": lambda: self._map(inp["target"], inp["change"]),
            "amu_graph": lambda: self._graph(inp["symbol"]),
            "amu_where": lambda: _cli(self.repo, "where", inp["symbol"]),
            "amu_state": lambda: _bounded_tool_json(self._state_payload(), 12000),
            "read_lines": lambda: self._read(inp["path"], inp["start"], inp["end"]),
            "answer_unknown": lambda: self._answer(inp["node"], inp["answer"]),
        }.get(name)
        if fn is None:
            raise ToolNotGranted(name)
        return outbound.Policy(self.repo, [self.cred.get("api_key")]).clean(self.role.call(f"chat.{name}", fn))

    def _search(self, query: str, *, definitions: bool = False) -> str:
        raw = entire.search(self.repo, query)
        payload = {key: value for key, value in raw.items() if key in {
            "results", "verify_command", "coverage_note", "error", "error_kind", "rc",
            "warnings", "partial_failures", "completeness",
        }}
        if definitions:
            payload["candidates"] = payload.pop("results", [])
            payload["note"] = "definition candidates only; inspect the exact path with read_lines; unstructured definition output is withheld"
        if raw.get("error"):
            payload["graph_evidence"] = {"approvable": False, "reasons": [str(raw["error"])]}
        if raw.get("truncated"):
            # Context ranking has its own truncation flag. Preserve the
            # provider's distinct incompleteness even when no rows are omitted.
            payload["provider_truncated"] = True
        return _bounded_tool_json(payload, 6000 if definitions else 12000)

    def _graph(self, symbol: str) -> str:
        path, _, name = symbol.rpartition("#")
        capture_before = graphview.live_worktree_capture(self.repo)
        impact = entire.impact_json(self.repo, name, 2, file=path or None)
        payload = graphview.payload_from_live_impact(
            self.repo, name, impact, target=(symbol if path else None), capture_before=capture_before
        )
        return _bounded_tool_json(payload, 12000)

    def _map(self, target: str, change: str) -> str:
        """Use the CLI map gate; the assistant must not create a weaker map."""
        if "#" in target:
            args = ("map", "--symbol", target, "--change", change)
        else:
            args = ("map", "--file", target, "--change", change)
        raw = _cli(self.repo, *args)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:14000]
        if not isinstance(payload, dict):
            return raw[:14000]
        return _bounded_tool_json(payload, 14000)

    def _read(self, path: str, start: int, end: int) -> str:
        return outbound.Policy(self.repo, [self.cred.get("api_key")]).read(path, start, end)

    def _answer(self, node: str, answer: str) -> str:
        try:
            record = unknowns.record_answer(self.repo, node, answer, who="model")
        except (ValueError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({
            **record, "recorded": node, "note": "model suggestion; human confirmation required; confidence unchanged",
            "next": f"review with amu answer, then amu answer --confirm {record['id']}",
        })

    # ----- loop ----------------------------------------------------------
    def _context(self) -> str:
        memory = state.read_json("memory.json", self.repo, default={"entries": []})
        entries = memory.get("entries", []) if isinstance(memory, dict) else []
        mem = [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
        mem = outbound.Policy(self.repo, [self.cred.get("api_key")]).clean(mem)
        current = self._state_payload()
        packet = outbound.Policy(self.repo, [self.cred.get("api_key")]).clean({
                "repo": state.repo_name(self.repo),
                "memory": mem,
                "current_map": current["map"],
                "nodes": current["nodes"],
                "contract": current["contract"],
            })
        budget = context.limits(state.config(self.repo))["context_bytes"]
        return context.encode(context.ranked_slice(packet, budget_bytes=min(12000, budget // 3)))

    def _prepare_request(self, tools, messages=None):
        """Drop complete old conversations only; the active tool chain stays intact."""
        raw = self.messages if messages is None else messages
        policy = outbound.Policy(self.repo, [self.cred.get("api_key")])
        if not policy.enabled:
            raise outbound.PolicyError("model requests disabled by repository outbound policy")
        bounds = context.limits(state.config(self.repo))
        cap = min(bounds["context_bytes"], policy.max_payload)
        starts = [i for i, item in enumerate(raw) if item.get("role") == "user" and isinstance(item.get("content"), str)]
        starts = [0, *[i for i in starts if i > 0]]
        fresh = self._context()
        for start in starts:
            selected = raw[start:]
            supplied = [dict(item) for item in selected]
            omitted = sum(item.get("role") == "user" and isinstance(item.get("content"), str) for item in raw[:start])
            notice = (f"\n{self.stats['compacted_turns'] + omitted} earlier completed turns were omitted to fit the context limit. "
                      "Use current graph/state tools for facts; omitted history is unavailable.\n") if self.stats["compacted_turns"] + omitted else ""
            for item in supplied:
                if item.get("role") == "user" and isinstance(item.get("content"), str):
                    question = item["content"]
                    if question.startswith("<context>") and "</context>" in question:
                        question = question.split("</context>", 1)[1].lstrip()
                    item["content"] = f"<context>{fresh}</context>{notice}\n\n{question}"
                    break
            kwargs = policy.clean(dict(model=self.model, max_tokens=16000,
                system=[{"type": "text", "text": ASSISTANT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=tools, messages=supplied, **modelmod.request_kwargs(self.cred, self.effort)))
            if context.estimate(kwargs) <= cap:
                return policy.prepare(kwargs), selected, omitted, bounds
        raise context.ContextLimitError("active question/tool chain exceeds the context limit; narrow the request")

    def _request(self, tools, messages=None):
        return self._prepare_request(tools, messages)[0]

    def preview(self, text: str) -> dict:
        messages = [*self.messages, {"role": "user", "content": text}]
        return self._request(tool_definitions(), messages)

    def _call(self, client, tools):
        kwargs, selected, omitted, bounds = self._prepare_request(tools)
        size = context.estimate(kwargs)
        if self.stats["requests"] >= bounds["session_requests"]:
            raise context.ContextLimitError("session request limit reached; start a new session after reviewing the limits")
        if self.stats["input_bytes"] + size > bounds["session_input_bytes"]:
            raise context.ContextLimitError("session input limit reached; start a new session after reviewing the limits")
        self.stats["requests"] += 1
        self.stats["input_bytes"] += size
        self.stats["compacted_turns"] += omitted
        self.messages = list(selected)
        if not self.stream or not hasattr(client.messages, "stream"):
            msg = client.messages.create(**kwargs)
            if self.stream:  # non-streaming provider: print the text once
                validation.message(msg)
                self.out("".join(getattr(b, "text", "") for b in msg.content if b.type == "text"))
            return msg
        with client.messages.stream(**kwargs) as s:
            emitted_bytes = 0
            for ev in s:
                if ev.type == "content_block_delta" and getattr(ev.delta, "type", "") == "text_delta":
                    emitted_bytes += len(ev.delta.text.encode("utf-8"))
                    if emitted_bytes > 65536:
                        raise validation.ResponseError("stream text exceeded the response limit")
                    self.out(ev.delta.text)
            return s.get_final_message()

    def _fail(self, text: str) -> str:
        text = outbound.redact(text, [self.cred.get("api_key")])
        self.messages = list(self._turn_start)
        self.stats["compacted_turns"] = self._compaction_start
        self.out(f"✗ {text}\n")
        return text

    def ask(self, text: str, max_turns: int = 8) -> str:
        self._turn_start = list(self.messages)
        self._compaction_start = self.stats["compacted_turns"]
        try:
            if not outbound.Policy(self.repo).enabled:
                return self._fail("model requests disabled by repository outbound policy")
        except outbound.PolicyError as exc:
            return self._fail(str(exc))
        if not modelmod.available():
            return "manual mode: no credential — run `amu key set [--provider …]` (or export ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_BASE_URL) to make the session interactive"
        try:
            client = self._client or modelmod.client()
        except ImportError:
            return self._fail("model client is unavailable; install the provider SDK or choose an OpenAI-compatible provider")
        try:
            if not outbound.Policy(self.repo).enabled:
                return self._fail("model requests disabled by repository outbound policy")
        except (outbound.PolicyError, context.ContextLimitError) as exc:
            return self._fail(str(exc))
        self.messages.append({"role": "user", "content": text})
        tools = tool_definitions()
        final = ""
        for _ in range(max_turns):
            try:
                msg = self._call(client, tools)
                validation.message(msg)
            except KeyboardInterrupt:
                self.messages = list(self._turn_start)
                self.stats["compacted_turns"] = self._compaction_start
                raise
            except (outbound.PolicyError, context.ContextLimitError, validation.ResponseError) as exc:
                return self._fail(str(exc))
            except providers.ProviderError as exc:
                hint = {"authentication": "credential rejected; run /key add",
                        "invalid_request": "check the model and endpoint settings",
                        "rate_limit": "provider rate limit; try later",
                        "timeout": "provider request timed out",
                        "response_too_large": "provider response exceeded the byte limit",
                        "invalid_response": "provider returned an invalid response"}.get(exc.category, "provider unreachable or request failed")
                return self._fail(f"model call failed: {hint}; remote diagnostics withheld")
            except Exception as exc:  # Provider SDKs are optional; do not import one for another provider family.
                if self.cred.get("family") == "anthropic":
                    try:
                        import anthropic
                    except ImportError:
                        return self._fail("Anthropic SDK is not installed; install it before using an Anthropic-family provider")
                    if isinstance(exc, anthropic.AuthenticationError):
                        return self._fail("credential rejected (401) — the key is wrong or revoked: type /key add to replace it")
                    if isinstance(exc, (anthropic.APIStatusError, anthropic.APIConnectionError)):
                        msg = "provider request failed; remote diagnostics withheld"
                        hint = " — the key was rejected: /key add to replace it" if "401" in msg else (" — check the model id: /key add" if "404" in msg or "400" in msg else " — endpoint unreachable?")
                        return self._fail(f"model call failed: {type(exc).__name__}: {msg}{hint}")
                return self._fail(f"model call failed: {type(exc).__name__}; diagnostics withheld")
            self.messages.append({"role": "assistant", "content": msg.content})
            self.stats["output_bytes"] += context.estimate(msg.content)
            usage = getattr(msg, "usage", None)
            reported = False
            for field in ("input_tokens", "output_tokens"):
                count = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
                if type(count) is int and count >= 0:
                    reported = True
                    self.stats[f"provider_{field}"] += count
            if reported:
                self.stats["usage_reported_requests"] += 1
            text_out = "".join(b.text for b in msg.content if b.type == "text")
            final = text_out or final
            if msg.stop_reason == "refusal":
                return final or "the model declined this request"
            if msg.stop_reason == "max_tokens":
                return self._fail("model response reached its output limit; narrow the request")
            if msg.stop_reason != "tool_use":
                break
            results = []
            for b in msg.content:
                if b.type != "tool_use":
                    continue
                self.out(f"\n  ⚙ {b.name} {json.dumps(b.input)[:100]}\n")
                try:
                    content = self._tool(b.name, b.input)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": content})
                except ToolNotGranted as exc:
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"tool not granted: {exc}", "is_error": True})
                except Exception as exc:  # noqa: BLE001 — a failed tool is reported to the model, never crashes the REPL
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"{type(exc).__name__}: {exc}", "is_error": True})
            self.messages.append({"role": "user", "content": results})
        else:
            return self._fail("model tool-round limit reached; narrow the request and try again")
        return final
