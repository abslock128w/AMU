"""Operator policy cannot be expanded by a tool's repo parameter or consent."""

import json
import os
import threading
from pathlib import Path

import pytest

from amu import mcp_access, mcp_server


@pytest.mark.parametrize("values", [None, [], (), ["relative"], [""], [True], ["/missing-amu-repo"]])
def test_invalid_operator_authority_fails_closed(values):
    with pytest.raises(mcp_access.AccessError):
        mcp_access.MCPAccessPolicy(values)


def test_authority_is_bounded_and_deduplicated(tmp_path):
    with pytest.raises(mcp_access.AccessError):
        mcp_access.MCPAccessPolicy([str(tmp_path)] * 17)
    policy = mcp_access.MCPAccessPolicy([str(tmp_path), str(tmp_path)])
    assert len(policy.grants) == 1
    assert policy.repository(str(tmp_path)) == str(tmp_path.resolve())


def test_exact_root_not_parent_child_or_symlink_escape(tmp_path):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    policy = mcp_access.MCPAccessPolicy([str(root)])
    for value in (tmp_path, child, root / "escape"):
        with pytest.raises(mcp_access.AccessError, match="not authorized"):
            policy.repository(str(value))


def test_directory_replacement_or_new_symlink_invalidates_authority(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    policy = mcp_access.MCPAccessPolicy([str(root)])
    root.rename(tmp_path / "previous-root")
    root.mkdir()
    with pytest.raises(mcp_access.AccessError, match="identity changed"):
        policy.repository(str(root))
    root.rmdir()
    root.symlink_to(tmp_path / "previous-root", target_is_directory=True)
    with pytest.raises(mcp_access.AccessError):
        policy.repository(str(root))


def test_models_and_client_secrets_are_not_forwarded(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                 "CONTROL_PLANE_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "PASSWORD",
                 "GOOGLE_APPLICATION_CREDENTIALS", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("AMU_DISABLE_MODELS", "0")
    environment = mcp_access.child_environment()
    assert environment["AMU_DISABLE_MODELS"] == "1"
    assert "secret-value" not in environment.values()
    assert environment.get("PATH") == os.environ.get("PATH")


@pytest.mark.parametrize("arguments", [{"query": "😀" * 17000}, {"query": "x" * 65537},
                                     {"query": "\ud800"}, {"value": float("inf")}])
def test_invalid_or_oversized_arguments_are_rejected(arguments):
    with pytest.raises(mcp_access.AccessError):
        mcp_access.check_arguments(arguments)


def test_policy_rejections_never_dispatch_and_do_not_log_private_values(tmp_path, capsys):
    pytest.importorskip("pydantic")
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    calls = []

    def amu_search(query: str, *, repo: str) -> str:
        calls.append(repo)
        return "{}"

    wrapped = mcp_server._strict_mcp_tool(amu_search, mcp_access.MCPAccessPolicy([str(allowed)]))
    refused = json.loads(wrapped("private-query", repo=str(outside)))
    assert refused["error_kind"] == "repository_not_allowed" and not calls
    assert "private-query" not in capsys.readouterr().err
    assert json.loads(wrapped("private-query", repo=str(allowed))) == {}
    log = capsys.readouterr().err
    assert "private-query" not in log and str(allowed) not in log


def test_concurrency_busy_limit_exception_cleanup_and_context_isolation(tmp_path):
    pytest.importorskip("pydantic")
    policy = mcp_access.MCPAccessPolicy([str(tmp_path)], enable_actions=True)
    started, release = threading.Event(), threading.Event()
    environments = []

    def amu_search(query: str, *, repo: str) -> str:
        environments.append(mcp_server._CHILD_ENV.get())
        if query == "wait":
            started.set()
            assert release.wait(5)
        if query == "raise":
            raise RuntimeError("test handler failure")
        return "{}"

    wrapped = mcp_server._strict_mcp_tool(amu_search, policy)
    thread = threading.Thread(target=wrapped, args=("wait",), kwargs={"repo": str(tmp_path)})
    thread.start()
    assert started.wait(5)
    try:
        assert json.loads(wrapped("second", repo=str(tmp_path)))["error_kind"] == "server_busy"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(environments) == 1
    assert json.loads(wrapped("raise", repo=str(tmp_path)))["error_kind"] == "tool_exception"
    assert wrapped("after", repo=str(tmp_path)) == "{}"
    assert all(environment["AMU_DISABLE_MODELS"] == "1" for environment in environments)
    assert mcp_server._CHILD_ENV.get() is None


def test_runtime_policy_forwards_sanitized_environment_only_for_server_calls(tmp_path, monkeypatch):
    pytest.importorskip("pydantic")
    captured = []

    def run(*a, **kw):
        from types import SimpleNamespace
        captured.append(kw)
        return SimpleNamespace(stdout=b"{}", stderr=b"", returncode=0, error=None)

    monkeypatch.setattr(mcp_server.process, "run", run)
    mcp_server.amu_docs_reviews(repo=str(tmp_path))
    assert "env" not in captured[-1]
    wrapped = mcp_server._strict_mcp_tool(mcp_server.amu_docs_reviews, mcp_access.MCPAccessPolicy([str(tmp_path)]))
    wrapped(repo=str(tmp_path))
    assert captured[-1]["env"]["AMU_DISABLE_MODELS"] == "1"
    assert captured[-1]["cwd"] == str(Path(tmp_path).resolve())


def test_large_tool_result_is_withheld_not_partially_returned(tmp_path, monkeypatch):
    pytest.importorskip("pydantic")
    monkeypatch.setattr(mcp_server, "MCP_RESPONSE_LIMIT", 1024, raising=False)

    def amu_search(query: str, *, repo: str) -> str:
        return json.dumps({"data": "test-private-value" * 100})

    wrapped = mcp_server._strict_mcp_tool(amu_search, mcp_access.MCPAccessPolicy([str(tmp_path)]))
    result = wrapped("query", repo=str(tmp_path))
    assert json.loads(result)["error_kind"] == "response_limit"
    assert "test-private-value" not in result
    assert json.loads(result)["graph_evidence"]["approvable"] is False


def test_utf8_payload_below_old_cap_cannot_overflow_escaped_duplicated_wire(tmp_path):
    pytest.importorskip("pydantic")

    def amu_search(query: str, *, repo: str) -> str:
        return json.dumps({"evidence": "世界" * 12_000}, ensure_ascii=False)

    raw = amu_search("query", repo=str(tmp_path))
    assert len(raw.encode("utf-8")) < mcp_server.MCP_RESPONSE_LIMIT
    wrapped = mcp_server._strict_mcp_tool(amu_search, mcp_access.MCPAccessPolicy([str(tmp_path)]))
    result = json.loads(wrapped("query", repo=str(tmp_path)))
    assert result["error_kind"] == "response_limit" and result["graph_evidence"]["approvable"] is False


def test_handler_exception_is_a_safe_structured_failure(tmp_path, capsys):
    pytest.importorskip("pydantic")

    def amu_search(query: str, *, repo: str) -> str:
        raise RuntimeError("test-private-exception")

    wrapped = mcp_server._strict_mcp_tool(amu_search, mcp_access.MCPAccessPolicy([str(tmp_path)]))
    result = wrapped("query", repo=str(tmp_path))
    assert json.loads(result)["error_kind"] == "tool_exception"
    assert "test-private-exception" not in result + capsys.readouterr().err
