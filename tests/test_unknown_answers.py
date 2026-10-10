"""Unknown answers must match current evidence and preserve their real author."""

import json
import subprocess
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from amu import cli, memory, state
from amu.harness import chat, model
from amu.harness.orchestrator import Orchestrator
from tests.test_lifecycle_gates import _map


@pytest.fixture
def mapped_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "src.py").write_text("def work_1():\n    return 1\n")
    (tmp_path / "consumer.py").write_text("def work_2():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"],
        cwd=tmp_path, check=True,
    )
    repo = str(tmp_path)
    amu_map = _map()
    amu_map["nodes"] = amu_map["nodes"][:2]
    for n in amu_map["nodes"]:
        n.update(confidence="unknown", risk="unknown", action="ask", reason="dynamic dispatch needs review")
    amu_map["resolution"].update(sound=0, guessed=0, unknown=2)
    capture = {**state.worktree_fingerprint(repo), "head": state.git_head(repo)}
    amu_map["graph_evidence"]["worktree"].update(before=capture, after=deepcopy(capture))
    state.write_json("map.json", amu_map, repo)
    monkeypatch.setattr(model, "available", lambda: False)
    return repo, amu_map


def _decision(repo, amu_map):
    return Orchestrator(repo, task=amu_map["map_hash"]).plan_decision(amu_map)


def test_unrelated_legacy_answer_cannot_clear_unknowns(mapped_repo):
    repo, amu_map = mapped_repo
    state.write_json("memory.json", {"entries": [{"kind": "answered_unknown", "text": "old question → old answer"}]}, repo)
    result = _decision(repo, amu_map)
    assert result["decision"] == "ask"
    assert set(result["evidence"]) >= {"n1", "n2"}


def test_model_cannot_override_unanswered_unknowns(mapped_repo, monkeypatch):
    repo, amu_map = mapped_repo
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "ask_json", lambda *_: {
        "decision": "proceed", "confidence": 1.0, "escalate": False, "evidence": [], "next": "approve",
    })
    assert _decision(repo, amu_map)["decision"] == "ask"


def test_chat_cannot_claim_human_authorship(mapped_repo):
    repo, _ = mapped_repo
    assistant = chat.Assistant(repo, stream=False)
    with pytest.raises(ValueError, match="declared fields"):
        assistant._tool("answer_unknown", {
            "node": "n1", "answer": "Only the documented adapter invokes this.", "who": "human", "confirmed": True,
        })
    result = json.loads(assistant._tool("answer_unknown", {
        "node": "n1", "answer": "Only the documented adapter invokes this.",
    }))
    assert result["who"] == "model"
    assert result["confirmed"] is False
    assert not list((state.amu_dir(repo) / "run").glob("human-*.json"))
    assert _decision(repo, state.read_json("map.json", repo))["decision"] == "ask"


def test_each_question_requires_its_own_current_confirmation(mapped_repo):
    from amu import unknowns

    repo, amu_map = mapped_repo
    unknowns.record_answer(repo, "n1", "Reviewed the adapter.", who="human")
    decision = _decision(repo, amu_map)
    assert decision["decision"] == "ask" and decision["evidence"] == ["n2"]
    proposal = unknowns.record_answer(repo, "n2", "Reviewed the consumer.", who="model")
    assert _decision(repo, amu_map)["decision"] == "ask"
    confirmed = unknowns.confirm_answer(repo, proposal["id"])
    assert confirmed["who"] == "model" and confirmed["confirmed_by"] == "human"
    assert _decision(repo, amu_map)["decision"] == "proceed"
    assert all(n["confidence"] == "unknown" for n in state.read_json("map.json", repo)["nodes"])


@pytest.mark.parametrize("change", ["source", "head", "node", "evidence", "map"])
def test_changed_context_expires_answers_and_refuses_stale_confirmation(mapped_repo, change):
    from amu import unknowns

    repo, amu_map = mapped_repo
    proposal = unknowns.record_answer(repo, "n1", "Draft answer.", who="model")
    unknowns.record_answer(repo, "n2", "Reviewed.", who="human")
    if change == "source":
        from pathlib import Path
        Path(repo, "src.py").write_text("def work_1():\n    return 3\n")
    elif change == "head":
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "new HEAD"],
            cwd=repo, check=True,
        )
    elif change == "node":
        amu_map["nodes"][0]["symbol"] = "different_symbol_using_same_node_id"
    elif change == "evidence":
        amu_map["nodes"][0]["reason"] = "different unresolved relationship"
    else:
        amu_map["map_hash"] = "new-map"
        amu_map["graph_evidence"]["map_hash"] = "new-map"
    state.write_json("map.json", amu_map, repo)
    with pytest.raises(ValueError, match="stale|changed|different"):
        unknowns.confirm_answer(repo, proposal["id"])
    assert _decision(repo, amu_map)["decision"] == "ask"


def test_confirmed_answers_do_not_override_missing_graph_evidence(mapped_repo, monkeypatch):
    from amu import unknowns

    repo, amu_map = mapped_repo
    amu_map["graph_evidence"]["approvable"] = False
    amu_map["graph_evidence"]["reasons"] = ["unresolved target"]
    state.write_json("map.json", amu_map, repo)
    for node in ("n1", "n2"):
        unknowns.record_answer(repo, node, "Reviewed.", who="human")
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "ask_json", lambda *_: {"decision": "proceed"})
    assert _decision(repo, amu_map)["decision"] == "ask"


def test_cli_review_then_confirm_keeps_model_attribution(mapped_repo):
    repo, _ = mapped_repo
    proposal = json.loads(chat.Assistant(repo, stream=False)._answer("n1", "Review this adapter."))
    runner = CliRunner()
    listing = runner.invoke(cli.app, ["answer", "--json", "--repo", repo])
    assert listing.exit_code == 0, listing.output
    assert json.loads(listing.stdout)["answers"][0]["answer"] == "Review this adapter."
    result = runner.invoke(cli.app, ["answer", "--confirm", proposal["id"], "--json", "--repo", repo])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["who"] == "model"
    assert json.loads(result.stdout)["confirmed_by"] == "human"


def test_memory_keeps_model_suggestions_and_legacy_answers_out_of_human_decisions(mapped_repo):
    repo, _ = mapped_repo
    chat.Assistant(repo, stream=False)._answer("n1", "Proposed explanation.")
    state.write_json("run/human-legacy.json", {"answers": [{"question": "old question", "answer": "old answer", "who": "human"}]}, repo)
    entries = memory.collect(repo)
    assert any(e["kind"] == "answer_suggestion" and "Proposed explanation" in e["text"] for e in entries)
    assert all(e.get("stale") for e in entries if "old answer" in e["text"])
    assert not any(e["kind"] == "answered_unknown" and not e.get("stale") for e in entries)


@pytest.mark.parametrize("change", ["source", "map"])
def test_context_change_during_model_decision_cannot_proceed(mapped_repo, monkeypatch, change):
    from pathlib import Path

    from amu import unknowns

    repo, amu_map = mapped_repo
    for node in ("n1", "n2"):
        unknowns.record_answer(repo, node, "Reviewed.", who="human")

    def response(*args):
        if change == "source":
            Path(repo, "src.py").write_text("def work_1():\n    return 99\n")
        else:
            replacement = deepcopy(amu_map)
            replacement["nodes"][0]["reason"] = "new question"
            state.write_json("map.json", replacement, repo)
        return {"decision": "proceed"}

    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "ask_json", response)
    assert _decision(repo, amu_map)["decision"] == "ask"


def test_cli_human_answer_only_clears_its_question(mapped_repo):
    from amu import unknowns

    repo, _ = mapped_repo
    result = CliRunner().invoke(cli.app, ["answer", "--node", "n1", "--text", "Reviewed adapter", "--json", "--repo", repo])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["who"] == "human"
    assert unknowns.review(repo)["unanswered"] == ["n2"]
    entries = memory.collect(repo)
    assert any(e["kind"] == "answered_unknown" and not e.get("stale") for e in entries)


@pytest.mark.parametrize("node,answer", [("missing", "Reviewed"), ("n1", ""), ("n1", "key sk-ant-abc123456789")])
def test_invalid_answers_are_not_saved(mapped_repo, node, answer):
    from amu import unknowns

    repo, _ = mapped_repo
    with pytest.raises(ValueError):
        unknowns.record_answer(repo, node, answer, who="human")
    assert unknowns.review(repo)["answers"] == []
