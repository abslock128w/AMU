"""Relevance and resource limits at the actual model request boundaries."""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from amu import state
from amu.harness import chat, context, model
from amu.harness.orchestrator import Orchestrator


def node(name, **extra):
    return {"id": name, "path": f"src/{name}.py", "symbol": name, "confidence": "sound",
            "order": 3, "via": "CALLS:depth3", "reason": "potentially affected", "verify": "run tests",
            "detail": "x" * 500, **extra}


def test_late_direct_call_and_uncertain_boundary_outrank_early_distant_rows():
    packet = {"nodes": [node(str(i)) for i in range(100)] +
              [node("direct", order=1), node("uncertain", confidence="unknown")],
              "graph_evidence": {"approvable": False, "reasons": ["parse failure"]}}
    result = context.ranked_slice(packet, budget_bytes=4000)
    names = [row["id"] for row in result["nodes"]]
    assert names[:2] == ["uncertain", "direct"]
    assert result["graph_evidence"] == packet["graph_evidence"]
    assert result["context_selection"]["groups"][0]["omitted"] > 0
    assert context.estimate(result) <= 4000


def test_mandatory_evidence_is_never_silently_cut():
    with pytest.raises(context.ContextLimitError, match="mandatory"):
        context.ranked_slice({"graph_evidence": {"approvable": False, "reasons": ["x" * 5000]}}, budget_bytes=2000)


def test_planner_sends_ranked_bounded_packet(tmp_path, monkeypatch):
    state.write_json("config.json", {"model_limits": {"context_bytes": 8000}}, str(tmp_path))
    seen = []
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "model_id", lambda *_: "fake")
    monkeypatch.setattr(model, "ask_json", lambda system, user, *_: seen.append(user) or model._manual("review"))
    amu_map = {"nodes": [node(str(i)) for i in range(300)] + [node("direct", order=1)],
               "root": {"file": "src/api.py"}, "resolution": {"sound": 301, "guessed": 0, "unknown": 0}, "next": "review"}
    Orchestrator(str(tmp_path)).plan_decision(amu_map)
    sent = json.loads(seen[0])
    assert len(seen[0].encode()) <= 8000
    assert sent["map"]["nodes"][0]["id"] == "direct"
    assert sent["truncated"] is True


def test_planner_budget_includes_prompt_and_embedded_json_escaping(tmp_path, monkeypatch):
    from amu.harness.prompts import system_prompt

    state.write_json("config.json", {"model_limits": {"context_bytes": 8000}}, str(tmp_path))
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai"})
    monkeypatch.setattr(model, "model_id", lambda *_: "fake")
    packet = {"nodes": [node(str(i), detail='"quoted"\\path' * 100) for i in range(100)]}
    prompt = system_prompt("planner")
    selected = model.planner_packet(prompt, packet, str(tmp_path))
    payload = model._request_payload({"family": "openai"}, prompt, selected, "fake")
    assert context.estimate(payload) <= 8000
    assert json.loads(selected)["context_selection"]["groups"][0]["total"] == 100


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai"})
    monkeypatch.setattr(model, "model_id", lambda *_: "fake")
    monkeypatch.setattr(chat, "tool_definitions", lambda: [])


def assistant(tmp_path, create, **limits):
    state.write_json("config.json", {"model_limits": limits}, str(tmp_path))
    return chat.Assistant(str(tmp_path), out=lambda _: None, stream=False,
                          client=NS(messages=NS(create=create)))


def reply(text="answer"):
    return NS(content=[NS(type="text", text=text)], stop_reason="end_turn")


def test_long_session_compacts_whole_completed_turns_under_request_limit(tmp_path, fake_model):
    calls = []
    a = assistant(tmp_path, lambda **kw: calls.append(kw) or reply("a" * 1500), context_bytes=8000)
    for i in range(8):
        assert a.ask(f"question{i}: " + "q" * 1800) == "a" * 1500
    assert a.stats["compacted_turns"] > 0
    assert all(context.estimate(call) <= 8000 for call in calls)
    assert "question7" in calls[-1]["messages"][-1]["content"]
    assert calls[-1]["messages"][0]["role"] == "user"
    assert a.messages[-1]["role"] == "assistant"


def test_cumulative_request_budget_stops_before_next_network_call(tmp_path, fake_model):
    calls = []
    a = assistant(tmp_path, lambda **kw: calls.append(kw) or reply(), session_requests=1)
    assert a.ask("first") == "answer"
    before = copy.deepcopy(a.messages)
    assert "session request limit" in a.ask("second")
    assert len(calls) == 1 and a.messages == before
    assert a.stats["requests"] == 1


def test_input_budget_and_oversized_question_make_no_network_call(tmp_path, fake_model):
    calls = []
    a = assistant(tmp_path, lambda **kw: calls.append(kw) or reply(), session_input_bytes=1)
    assert "session input limit" in a.ask("question")
    assert not calls
    state.write_json("config.json", {"model_limits": {"context_bytes": 8000}}, str(tmp_path))
    assert "context" in a.ask("q" * 9000)
    assert not calls and a.messages == []


def test_preview_does_not_consume_budget_or_compact_saved_history(tmp_path, fake_model):
    a = assistant(tmp_path, lambda **_: reply(), session_requests=1)
    a.ask("first")
    saved_messages, saved_stats = copy.deepcopy(a.messages), dict(a.stats)
    a.preview("next")
    assert a.messages == saved_messages and a.stats == saved_stats


def test_interruption_rolls_back_active_turn_and_keeps_charged_request(tmp_path, fake_model):
    calls = [reply("previous")]
    def create(**_):
        if calls:
            return calls.pop()
        raise KeyboardInterrupt
    a = assistant(tmp_path, create)
    a.ask("first")
    saved = copy.deepcopy(a.messages)
    with pytest.raises(KeyboardInterrupt):
        a.ask("second")
    assert a.messages == saved
    assert a.stats["requests"] == 2


def test_compaction_never_leaves_orphaned_tool_results(tmp_path, fake_model):
    calls = []
    a = assistant(tmp_path, lambda **kw: calls.append(kw) or reply(), context_bytes=8000)
    a.messages = [
        {"role": "user", "content": "old " + "x" * 1500},
        {"role": "assistant", "content": [NS(type="tool_use", id="old-tool", name="amu_state", input={})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old-tool", "content": "x" * 2500}]},
        {"role": "assistant", "content": [NS(type="text", text="finished " + "x" * 1200)]},
    ]
    assert a.ask("new question " + "x" * 1500) == "answer"
    assert "old-tool" not in context.encode(calls[-1]["messages"])
    assert a.stats["compacted_turns"] == 1


def test_truncated_or_oversized_reply_does_not_complete_a_turn(tmp_path, fake_model):
    for response in [NS(content=[NS(type="text", text="partial")], stop_reason="max_tokens"), reply("x" * 70000)]:
        a = assistant(tmp_path, lambda **_: response)
        assert "limit" in a.ask("question")
        assert not a.messages


def test_nonstreaming_provider_cannot_print_oversized_text_before_validation(tmp_path, fake_model):
    emitted = []
    a = chat.Assistant(str(tmp_path), out=emitted.append, stream=True,
                       client=NS(messages=NS(create=lambda **_: reply("x" * 70000))))
    assert "limit" in a.ask("question")
    assert not any("x" * 1000 in text for text in emitted)


def test_planner_request_limit_precedes_client_initialization(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai"})
    monkeypatch.setattr(model, "client", lambda: pytest.fail("oversized input initialized provider"))
    state.write_json("config.json", {"model_limits": {"context_bytes": 8000}}, str(tmp_path))
    assert model.ask_json("system", "x" * 9000, repo=str(tmp_path))["decision"] == "manual"


@pytest.mark.parametrize("retryable,expected_calls", [(True, 2), (False, 1)])
def test_planner_retries_only_typed_transient_failures(tmp_path, monkeypatch, retryable, expected_calls):
    from amu.harness import providers

    calls = []
    def create(**kw):
        calls.append(kw)
        raise providers.ProviderError("withheld", category="connection", retryable=retryable)
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai"})
    monkeypatch.setattr(model, "client", lambda: NS(messages=NS(create=create)))
    monkeypatch.setattr(model.time, "sleep", lambda _: None)
    assert model.ask_json("system", "question", repo=str(tmp_path))["decision"] == "manual"
    assert len(calls) == expected_calls
