"""No live providers: exercise fail-closed schemas and bounded context/session state."""

import json
from types import SimpleNamespace as NS

import pytest

from amu.harness import chat, model
from amu.harness.orchestrator import Orchestrator

VALID = {"decision": "proceed", "confidence": 0.6, "escalate": False, "evidence": ["caller checked"], "next": "review"}


def response(value):
    return NS(content=[NS(type="text", text=json.dumps(value))], stop_reason="end_turn")


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai"})


@pytest.mark.parametrize("value", [[], [VALID], {"decision": "proceed"}, {**VALID, "confidence": True},
    {**VALID, "confidence": float("nan")}, {**VALID, "decision": "approved"}, {**VALID, "escalate": "false"},
    {**VALID, "evidence": "trust me"}, {**VALID, "extra": "untrusted"}])
def test_invalid_model_json_fails_closed(tmp_path, monkeypatch, fake_model, value):
    calls = []
    monkeypatch.setattr(model, "client", lambda: NS(messages=NS(create=lambda **kw: calls.append(kw) or response(value))))
    result = model.ask_json("system", "question", repo=str(tmp_path))
    assert result["decision"] == "manual" and result["escalate"] is True
    assert len(calls) == 2


def test_planner_does_not_replace_invalid_model_output_with_proceed(tmp_path, monkeypatch, fake_model):
    monkeypatch.setattr(model, "ask_json", lambda *_: {"decision": "manual", "confidence": 0.0,
        "escalate": True, "evidence": [], "next": "invalid response"})
    result = Orchestrator(str(tmp_path)).plan_decision({"nodes": [], "resolution": {"sound": 1, "guessed": 0}, "next": "approve"})
    assert result["decision"] == "manual" and result["escalate"] is True


def test_malformed_tool_arguments_never_dispatch(tmp_path, monkeypatch, fake_model):
    called = []
    monkeypatch.setattr(chat.entire, "search", lambda *_: called.append(True) or {})
    messages = [NS(content=[NS(type="tool_use", id="c1", name="graph_search", input={"query": ["bad"]})], stop_reason="tool_use"), response(VALID)]
    assistant = chat.Assistant(str(tmp_path), out=lambda _: None, stream=False,
        client=NS(messages=NS(create=lambda **_: messages.pop(0))))
    assistant.ask("hello")
    assert not called
    assert assistant.messages[2]["content"][0]["is_error"] is True


def test_failed_turn_preserves_previous_completed_history(tmp_path, fake_model):
    def fail(**_):
        raise RuntimeError("private provider text")

    assistant = chat.Assistant(str(tmp_path), out=lambda _: None, stream=False, client=NS(messages=NS(create=fail)))
    prior = [{"role": "user", "content": "previous question"},
             {"role": "assistant", "content": [NS(type="text", text="previous answer")]}]
    assistant.messages = list(prior)
    assert "failed" in assistant.ask("new question")
    assert assistant.messages == prior


def test_failed_tool_round_restores_entire_prior_turn(tmp_path, fake_model):
    calls = [NS(content=[NS(type="tool_use", id="t1", name="amu_state", input={})], stop_reason="tool_use")]
    def create(**_):
        if calls:
            return calls.pop()
        raise RuntimeError("failed")
    assistant = chat.Assistant(str(tmp_path), out=lambda _: None, stream=False, client=NS(messages=NS(create=create)))
    prior = [{"role": "user", "content": "old"}, {"role": "assistant", "content": [NS(type="text", text="answer")]}]
    assistant.messages = list(prior)
    assistant.ask("new")
    assert assistant.messages == prior


def test_exhausted_tool_round_is_not_left_as_an_unfinished_conversation(tmp_path, fake_model):
    message = NS(content=[NS(type="tool_use", id="t1", name="amu_state", input={})], stop_reason="tool_use")
    assistant = chat.Assistant(str(tmp_path), out=lambda _: None, stream=False, client=NS(messages=NS(create=lambda **_: message)))
    assert "limit" in assistant.ask("new", max_turns=1)
    assert assistant.messages == []
