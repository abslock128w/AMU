import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from amu import keys, state
from amu.harness import chat
from amu.harness.roles import ToolNotGranted, make_role


class _Msgs:
    """Scripted fake of client.messages: first turn calls a tool, second turn answers."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def _text(t, stop="end_turn"):
    return NS(content=[NS(type="text", text=t)], stop_reason=stop)


def _tool(name, inp, tid="tu1"):
    return NS(content=[NS(type="tool_use", id=tid, name=name, input=inp)], stop_reason="tool_use")


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    return str(tmp_path)


def test_manual_mode_without_credential(tmp_path):
    a = chat.Assistant(_repo(tmp_path), stream=False, client=NS(messages=_Msgs([])))
    assert "manual mode" in a.ask("hello") and a.messages == []


def test_tool_loop_executes_graph_search_and_returns_text(fake_entire, tmp_path):
    keys.set_key("sk-ant-api03-file-value-000000000000")
    msgs = _Msgs([_tool("graph_search", {"query": "classifier"}), _text("classify_consumers in amu/classify.py:3 has 30 callers.")])
    a = chat.Assistant(_repo(tmp_path), out=lambda t: None, stream=False, client=NS(messages=msgs))
    ans = a.ask("where is the classifier?")
    assert "classify_consumers" in ans
    result = a.messages[2]["content"][0]
    assert result["type"] == "tool_result" and "classify_consumers" in result["content"] and not result.get("is_error")
    assert msgs.calls[0]["model"] == "claude-opus-5" and msgs.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [t["name"] for t in msgs.calls[0]["tools"]] == [t["name"] for t in chat.tool_definitions()]


def test_ungranted_tool_is_reported_not_crashed(fake_entire, tmp_path):
    keys.set_key("sk-ant-api03-file-value-000000000000")
    msgs = _Msgs([_tool("write_file", {"path": "x"}), _text("ok I cannot write.")])
    a = chat.Assistant(_repo(tmp_path), out=lambda t: None, stream=False, client=NS(messages=msgs))
    a.ask("edit the file")
    assert a.messages[2]["content"][0]["is_error"] is True and "not granted" in a.messages[2]["content"][0]["content"]


def test_assistant_role_has_no_write_tool():
    r = make_role("assistant")
    assert not any("write" in t for t in r.tools)
    with pytest.raises(ToolNotGranted):
        r.call("files.write_allowed", lambda: None)


def test_read_lines_stays_inside_repo(tmp_path):
    a = chat.Assistant(_repo(tmp_path), stream=False)
    (tmp_path / "f.py").write_text("a\nb\n")
    assert "heuristic" in a._read("f.py", 1, 2)
    assert "outside" in a._read("../../etc/passwd", 1, 2)


def test_read_lines_rejects_sibling_with_matching_path_prefix(tmp_path):
    repo = tmp_path / "repo"
    sibling = tmp_path / "repo_evil"
    repo.mkdir()
    sibling.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo)
    (sibling / "secret.txt").write_text("do not expose\n")
    a = chat.Assistant(str(repo), stream=False)
    assert "outside" in a._read("../repo_evil/secret.txt", 1, 1)


def test_answer_unknown_requires_current_map_and_rejects_secret(tmp_path):
    repo = _repo(tmp_path)
    state.write_json("map.json", {"nodes": [{"id": "n5", "path": "x.py", "reason": "dyn"}]}, repo)
    a = chat.Assistant(repo, stream=False)
    assert "error" in a._answer("n5", "internal only")
    assert "secret" in a._answer("n5", "key sk-ant-abc123456789")
    from amu import memory
    kinds = {e["kind"] for e in memory.collect(repo)}
    assert "answered_unknown" not in kinds


def test_refusal_stop_reason_handled(fake_entire, tmp_path):
    keys.set_key("sk-ant-api03-file-value-000000000000")
    a = chat.Assistant(_repo(tmp_path), out=lambda t: None, stream=False, client=NS(messages=_Msgs([_text("", stop="refusal")])))
    assert "declined" in a.ask("x")


def test_cli_ask_manual_mode_json(tmp_path):
    import sys
    r = subprocess.run([sys.executable, "-m", "amu.cli", "ask", "hi", "--json"], cwd=_repo(tmp_path), capture_output=True, text=True)
    assert json.loads(r.stdout)["answer"].startswith("manual mode")


def _docstring_only_impact():
    return {
        "focus": {
            "id": "target-id",
            "name": "target",
            "qualified_name": "target",
            "file_path": "sample.py",
            "kind": "function",
            "start_line": 1,
            "end_line": 3,
        },
        "callees": {
            "total": 1,
            "entries": [
                {
                    "endpoint": {"id": "helper-id", "name": "helper", "file_path": "sample.py", "kind": "function"},
                    "relation": "CALLS",
                    "depth": 1,
                }
            ],
        },
    }


def test_assistant_graph_uses_source_checked_live_payload(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        '    """Example only: helper()."""\n'
        "    return 0\n\n"
        "def helper():\n"
        "    return 1\n"
    )
    monkeypatch.setattr(chat.entire, "impact_json", lambda *_args, **_kwargs: _docstring_only_impact())

    payload = json.loads(chat.Assistant(repo, stream=False)._tool("amu_graph", {"symbol": "sample.py#target"}))

    assert payload["edges"] == []
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["source_contradictions"][0]["endpoint"] == "helper"


def test_assistant_map_delegates_to_the_guarded_cli_map(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = []
    monkeypatch.setattr(
        chat,
        "_cli",
        lambda called_repo, *args: seen.append((called_repo, args)) or json.dumps({"nodes": [{"id": str(n)} for n in range(61)]}),
    )
    monkeypatch.setattr(chat.entire, "impact_json", lambda *_args, **_kwargs: pytest.fail("assistant map must use the CLI map gate"))

    payload = json.loads(chat.Assistant(repo, stream=False)._map("sample.py#target", "body"))

    assert seen == [(repo, ("map", "--symbol", "sample.py#target", "--change", "body"))]
    assert len(payload["nodes"]) == 61
    assert payload["truncated"] is False

    chat.Assistant(repo, stream=False)._map("sample.py", "signature")
    assert seen[-1] == (repo, ("map", "--file", "sample.py", "--change", "signature"))


def _large_incomplete_impact():
    entries = [
        {
            "endpoint": {"id": f"helper-{index}", "name": f"helper_{index}", "file_path": "x.py", "kind": "function"},
            "relation": "CALLS",
            "depth": 1,
        }
        for index in range(300)
    ]
    return {
        "focus": {"id": "target", "name": "target", "qualified_name": "target", "file_path": "x.py", "kind": "function"},
        "callers": {"total": 0, "entries": []},
        "callees": {"total": 301, "entries": entries},
        "type_consumers": {"total": 0, "entries": []},
        "data_flows": {"total": 0, "entries": []},
    }


def test_assistant_large_graph_keeps_valid_json_and_safety_verdict(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(chat.entire, "impact_json", lambda *_args, **_kwargs: _large_incomplete_impact())

    raw = chat.Assistant(repo, stream=False)._graph("x.py#target")
    payload = json.loads(raw)

    assert len(raw) <= 12000
    assert payload["truncated"] is True
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["warnings"]


def test_assistant_large_map_keeps_valid_json_and_safety_verdict(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    raw_map = {
        "nodes": [{"id": str(index), "detail": "n" * 200} for index in range(61)],
        "ripples": [{"id": str(index), "detail": "r" * 400} for index in range(200)],
        "graph_evidence": {"approvable": False, "reasons": ["graph response is incomplete"]},
        "warnings": ["do not approve this map"],
    }
    monkeypatch.setattr(chat, "_cli", lambda *_args: json.dumps(raw_map))

    raw = chat.Assistant(repo, stream=False)._map("sample.py#target", "body")
    payload = json.loads(raw)

    assert len(raw) <= 14000
    assert payload["truncated"] is True
    assert payload["graph_evidence"]["approvable"] is False
    assert payload["warnings"] == ["do not approve this map"]


def _committed_repo_with_current_map(tmp_path):
    repo = _repo(tmp_path)
    path = tmp_path / "x.py"
    path.write_text("def target():\n    return 1\n")
    subprocess.run(["git", "add", "x.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "baseline"],
        cwd=repo,
        check=True,
    )
    fingerprint = state.worktree_fingerprint(repo)
    head = state.git_head(repo)
    capture = {
        "stable": True,
        "before": {**fingerprint, "head": head},
        "after": {**fingerprint, "head": head},
        "pre_edit_graph_files": [],
    }
    amu_map = {
        "root": {"file": "x.py"},
        "resolution": {"sound": 1, "guessed": 0, "unknown": 0},
        "next": "amu plan --approve",
        "map_hash": "fresh-map",
        "graph_evidence": {"approvable": True, "map_hash": "fresh-map", "worktree": capture},
        "nodes": [{"id": "n1", "confidence": "sound", "reason": "resolved caller"}],
    }
    state.write_json("map.json", amu_map, repo)
    return repo, path


def test_chat_context_and_state_mark_a_stale_map_inspection_only(tmp_path):
    repo, path = _committed_repo_with_current_map(tmp_path)
    path.write_text("def target():\n    return 2\n")
    assistant = chat.Assistant(repo, stream=False)

    context = json.loads(assistant._context())
    tool_state = json.loads(assistant._tool("amu_state", {}))

    assert context["current_map"]["status"] == "inspection_only"
    assert "next" not in context["current_map"]
    assert context["current_map"]["graph_evidence"]["approvable"] is False
    assert any("different editable worktree" in reason for reason in context["current_map"]["safety"]["reasons"])
    assert tool_state["map"]["status"] == "inspection_only"
    assert "next" not in tool_state["map"]
    assert tool_state["nodes"][0]["confidence"] == "unknown"


def test_chat_context_never_offers_reapproval_when_a_matching_contract_is_active(tmp_path):
    repo, _path = _committed_repo_with_current_map(tmp_path)
    state.write_json(
        "contract.json",
        {
            "map_hash": "fresh-map",
            "targets": [{"symbol": "x.py#target", "change_class": "body"}],
            "phases": [{"n": 1, "kind": "additive", "files": ["x.py"], "nodes": ["n1"]}],
        },
        repo,
    )
    state.write_json("state.json", {"nodes": {"n1": "planned"}, "history": []}, repo)

    context = json.loads(chat.Assistant(repo, stream=False)._context())

    assert context["current_map"]["status"] == "active_contract"
    assert "next" not in context["current_map"]
    assert context["contract"]["status"] == "active_contract"
    assert context["contract"]["incomplete_phases"] == [{"n": 1, "kind": "additive"}]
