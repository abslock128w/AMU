"""The assistant and MCP must bound child output before JSON/context handling."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from amu import mcp_server
from amu.harness import chat


@pytest.mark.parametrize("module", [chat, mcp_server])
@pytest.mark.parametrize("stream", [1, 2])
def test_wrapper_uses_bounded_transport_and_withholds_overflow(module, stream, tmp_path, monkeypatch):
    from amu import process

    run = process.run
    seen = []
    monkeypatch.setattr(module, "CLI_STDOUT_LIMIT", 128)
    monkeypatch.setattr(module, "CLI_STDERR_LIMIT", 64)

    def bounded_child(command, **options):
        seen.append((command, options))
        source = f"import os,time; os.write(1,b'{{\"graph_evidence\":{{\"approvable\":true}}}}'); os.write({stream},b'x'*129); time.sleep(10)"
        return run([sys.executable, "-c", source], **{**options, "timeout": 2})

    monkeypatch.setattr(module.process, "run", bounded_child)
    repo = str(tmp_path)
    raw = chat._cli(repo, "map") if module is chat else mcp_server._cli("map", repo=repo)
    payload = json.loads(raw)

    assert seen[0][1] == {"cwd": repo, "timeout": 600, "stdout_limit": 128, "stderr_limit": 64}
    assert payload["exit"] == 125
    assert payload["error_kind"] == "output_limit"
    assert payload["graph_evidence"]["approvable"] is False
    assert "x" * 20 not in raw
    assert "stdout" not in payload and "stderr" not in payload


@pytest.mark.parametrize("module", [chat, mcp_server])
def test_wrapper_withholds_partial_output_on_timeout(module, tmp_path, monkeypatch):
    result = SimpleNamespace(returncode=124, error="timeout", stdout=b'{"approvable":true}', stderr=b"raw-secret")
    monkeypatch.setattr(module.process, "run", lambda *args, **kwargs: result)
    raw = chat._cli(str(tmp_path), "map") if module is chat else mcp_server._cli("map", repo=str(tmp_path))
    payload = json.loads(raw)
    assert payload["exit"] == 124
    assert payload["error"] == "amu CLI timed out"
    assert payload["graph_evidence"]["approvable"] is False
    assert "raw-secret" not in raw


@pytest.mark.parametrize("module", [chat, mcp_server])
def test_wrapper_reports_invalid_utf8_and_decoder_recursion_failure(module, tmp_path, monkeypatch):
    result = SimpleNamespace(returncode=0, error=None, stdout=b"\xff", stderr=b"")
    monkeypatch.setattr(module.process, "run", lambda *args, **kwargs: result)
    raw = chat._cli(str(tmp_path), "map") if module is chat else mcp_server._cli("map", repo=str(tmp_path))
    assert json.loads(raw)["error"] == "amu CLI returned invalid JSON"

    result.stdout = b"{}"
    # Python versions have different decoder nesting implementations. Exercise
    # the error boundary without asserting one interpreter-specific depth cap.
    with monkeypatch.context() as patch:
        patch.setattr(module.json, "loads", lambda *args, **kwargs: (_ for _ in ()).throw(RecursionError()))
        raw = chat._cli(str(tmp_path), "map") if module is chat else mcp_server._cli("map", repo=str(tmp_path))
    assert json.loads(raw)["error"] == "amu CLI returned invalid JSON"


@pytest.mark.parametrize("tool,field", [("graph_def", "symbol"), ("graph_search", "query")])
def test_assistant_search_tools_preserve_provider_errors_and_coverage(tool, field, tmp_path, monkeypatch):
    monkeypatch.setattr(chat.entire, "search", lambda *args: {
        "error": "graph output exceeded byte limit", "rc": 125,
        "warnings": ["evidence incomplete"], "completeness": {"relations": {}},
    })
    payload = json.loads(chat.Assistant(str(tmp_path), stream=False)._tool(tool, {field: "target"}))
    assert payload["error"] == "graph output exceeded byte limit"
    assert payload["rc"] == 125
    assert payload["warnings"] == ["evidence incomplete"]
    assert payload["completeness"] == {"relations": {}}
    assert payload["graph_evidence"]["approvable"] is False
