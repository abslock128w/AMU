"""MCP command-contract tests.

These tests exercise the server without starting an MCP stdio process. The
important boundary is the CLI invocation: every repository tool must use an
explicit safe repository directory, and the post-edit tool must use the
guarded CLI refresh rather than query a stale graph.
"""

from __future__ import annotations

import inspect
import json
import sys
from types import SimpleNamespace

import pytest

from amu import mcp_server


def _result(stdout: str = "{}", stderr: str = "", returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout.encode(), stderr=stderr.encode(), returncode=returncode, error=None)


def _repo(tmp_path) -> str:
    repo = tmp_path / "repo"
    repo.mkdir()
    return str(repo.resolve())


def test_tools_expose_the_graph_guided_agent_workflow():
    assert {
        "amu_search",
        "amu_graph",
        "amu_impact",
        "amu_map",
        "amu_map_target",
        "amu_refresh_graph",
        "amu_docs",
        "amu_check",
        "amu_done",
    } <= set(mcp_server.TOOLS)


def test_impact_forwards_paging_and_dash_prefixed_text_as_assigned_options(monkeypatch):
    from amu.services import impact_report

    calls = []
    cursor = impact_report.cursor("a" * 64, "coverage", 0)
    monkeypatch.setattr(mcp_server, "_cli", lambda *args, **kwargs: calls.append((args, kwargs)) or "{}")
    mcp_server.amu_impact("-api.py", 2, "--validate=before-return", section="coverage",
                          cursor=cursor, page_size=3, repo="/repo")
    assert calls == [(("impact", "--file=-api.py", "--line", "2", "--proposed-change=--validate=before-return",
                      "--depth", "2", "--change", "body", "--section=coverage", f"--cursor={cursor}",
                      "--page-size", "3"), {"repo": "/repo"})]


@pytest.mark.parametrize("options", [
    {"page_size": True}, {"page_size": "2"}, {"page_size": 2.0}, {"page_size": 0}, {"page_size": 101},
    {"section": "invented"}, {"section": True}, {"cursor": True}, {"cursor": ""},
    {"cursor": "é" * 513},
])
def test_impact_invalid_paging_fails_before_cli_or_repository_access(monkeypatch, options):
    monkeypatch.setattr(mcp_server, "_cli", lambda *args, **kwargs: pytest.fail("invalid paging must not dispatch"))
    result = json.loads(mcp_server.amu_impact("api.py", 2, "change validation", repo="/unused", **options))
    assert result["exit"] == 2


@pytest.mark.parametrize("value", [1, "true", None])
def test_plan_requires_a_boolean_opt_in_before_dispatch(monkeypatch, value):
    monkeypatch.setattr(mcp_server, "_cli", lambda *a, **k: pytest.fail("CLI must not run"))
    result = json.loads(mcp_server.amu_plan("n1", approve=value, repo="/unused"))
    assert result["exit"] == 2 and "approve must be a boolean" in result["error"]


def test_plan_forwards_explicit_document_scope_without_automatic_expansion(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_cli", lambda *a, **k: calls.append((a, k)) or "{}")
    mcp_server.amu_plan("a.py#public:signature", docs_files=["docs/guide.md", "docs/api.rst"], repo="/repo")
    assert calls == [(("plan", "--targets", "a.py#public:signature", "--docs-file", "docs/guide.md",
                      "--docs-file", "docs/api.rst"), {"repo": "/repo"})]


@pytest.mark.parametrize("files", ["docs/guide.md", [True], [1], [""], ["docs/a.md"] * 101])
def test_plan_rejects_malformed_document_scope_before_dispatch(monkeypatch, files):
    monkeypatch.setattr(mcp_server, "_cli", lambda *a, **k: pytest.fail("CLI must not run"))
    result = json.loads(mcp_server.amu_plan("n1", docs_files=files, repo="/unused"))
    assert result["exit"] == 2 and "docs_files must be an array" in result["error"]


def test_repository_tools_require_repo_in_their_mcp_signatures():
    repository_tools = (
        mcp_server.amu_search,
        mcp_server.amu_graph,
        mcp_server.amu_impact,
        mcp_server.amu_map,
        mcp_server.amu_map_target,
        mcp_server.amu_refresh_graph,
        mcp_server.amu_docs,
        mcp_server.amu_plan,
        mcp_server.amu_check,
        mcp_server.amu_done,
        mcp_server.amu_verify,
    )

    for tool in repository_tools:
        repo = inspect.signature(tool).parameters["repo"]
        assert repo.default is inspect.Parameter.empty
        assert repo.kind is inspect.Parameter.KEYWORD_ONLY


def test_omitted_repo_fails_before_any_cli_subprocess(monkeypatch):
    monkeypatch.setattr(
        mcp_server.process,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CLI must not run")),
    )
    calls_without_repo = (
        lambda: mcp_server.amu_search("find array"),
        lambda: mcp_server.amu_graph("numpy/core.py#array"),
        lambda: mcp_server.amu_impact("numpy/core.py", 1, "change validation"),
        lambda: mcp_server.amu_map("numpy/core.py"),
        lambda: mcp_server.amu_map_target("numpy/core.py#array"),
        lambda: mcp_server.amu_refresh_graph("numpy/core.py#array"),
        lambda: mcp_server.amu_docs(),
        lambda: mcp_server.amu_plan("n1"),
        lambda: mcp_server.amu_check(),
        lambda: mcp_server.amu_done(),
        lambda: mcp_server.amu_verify("n1"),
    )

    for call in calls_without_repo:
        with pytest.raises(TypeError, match="repo"):
            call()


def test_null_or_unsafe_repo_is_rejected_before_any_cli_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_server.process,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CLI must not run")),
    )
    calls_with_null_repo = (
        lambda: mcp_server.amu_search("find array", repo=None),
        lambda: mcp_server.amu_graph("numpy/core.py#array", repo=None),
        lambda: mcp_server.amu_impact("numpy/core.py", 1, "change validation", repo=None),
        lambda: mcp_server.amu_map("numpy/core.py", repo=None),
        lambda: mcp_server.amu_map_target("numpy/core.py#array", repo=None),
        lambda: mcp_server.amu_refresh_graph("numpy/core.py#array", repo=None),
        lambda: mcp_server.amu_docs(repo=None),
        lambda: mcp_server.amu_plan("n1", repo=None),
        lambda: mcp_server.amu_check(repo=None),
        lambda: mcp_server.amu_done(repo=None),
        lambda: mcp_server.amu_verify("n1", repo=None),
    )

    for call in calls_with_null_repo:
        result = json.loads(call())
        assert result["exit"] == 2
        assert result["error"].startswith("repo is required")

    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("not a repository")
    for invalid in ("relative/repo", str(tmp_path / "missing"), str(not_a_directory)):
        result = json.loads(mcp_server.amu_search("find array", repo=invalid))
        assert result["exit"] == 2
        assert result["error"].startswith("repo must be")


def test_search_graph_map_and_docs_use_json_cli_commands(monkeypatch, tmp_path):
    calls = []
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert timeout == 600
        assert cwd == repo
        assert (stdout_limit, stderr_limit) == (16 * 1024 * 1024, 64 * 1024)
        calls.append(command)
        return _result('{"ok": true}')

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    assert json.loads(mcp_server.amu_search("where is array created?", repo=repo)) == {"ok": True}
    assert json.loads(mcp_server.amu_graph("numpy/core.py#array", depth=3, relation="CALLS", repo=repo)) == {"ok": True}
    # The original tool shape remains valid for existing MCP clients.
    assert json.loads(mcp_server.amu_map("numpy/core.py", "body", repo=repo)) == {"ok": True}
    assert json.loads(mcp_server.amu_map_target("numpy/core.py#array", "signature", depth=4, repo=repo)) == {"ok": True}
    assert json.loads(mcp_server.amu_docs(repo=repo)) == {"ok": True}

    base = [sys.executable, "-I", "-m", "amu.cli"]
    assert calls == [
        [*base, "find", "where is array created?", "--no-history", "--repo", repo, "--json"],
        [*base, "graph", "numpy/core.py#array", "--depth", "3", "--relation", "CALLS", "--repo", repo, "--json"],
        [*base, "map", "--file", "numpy/core.py", "--change", "body", "--depth", "2", "--repo", repo, "--json"],
        [*base, "map", "--symbol", "numpy/core.py#array", "--change", "signature", "--depth", "4", "--repo", repo, "--json"],
        [*base, "docs", "--mode", "flag", "--repo", repo, "--json"],
    ]


def test_legacy_map_can_accept_a_target_keyword_without_breaking_file_callers(monkeypatch, tmp_path):
    calls = []
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert timeout == 600
        assert cwd == repo
        calls.append(command)
        return _result('{"mapped": true}')

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    result = json.loads(mcp_server.amu_map(target="numpy/core.py#array", repo=repo))

    assert result == {"mapped": True}
    assert calls == [
        [
            sys.executable,
            "-I",
            "-m",
            "amu.cli",
            "map",
            "--symbol",
            "numpy/core.py#array",
            "--change",
            "signature",
            "--depth",
            "2",
            "--repo",
            repo,
            "--json",
        ]
    ]


def test_refresh_graph_delegates_to_the_guarded_cli_refresh_once(monkeypatch, tmp_path):
    calls = []
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert timeout == 600
        assert cwd == repo
        calls.append(command)
        return _result('{"target": "numpy/core.py#array", "complete": true}')

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    result = json.loads(mcp_server.amu_refresh_graph("numpy/core.py#array", depth=3, repo=repo))

    assert result == {"target": "numpy/core.py#array", "complete": True}
    # The guarded CLI operation is the only post-edit graph call, so MCP cannot
    # graph before a refresh or continue with an old graph after one fails.
    assert calls == [
        [
            sys.executable,
            "-I",
            "-m",
            "amu.cli",
            "refresh",
            "--symbol",
            "numpy/core.py#array",
            "--depth",
            "3",
            "--repo",
            repo,
            "--json",
        ]
    ]


def test_refresh_graph_forwards_the_explicit_new_target_for_a_move(monkeypatch, tmp_path):
    calls = []
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert cwd == repo and timeout == 600
        calls.append(command)
        return _result('{"complete": true}')

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    result = json.loads(
        mcp_server.amu_refresh_graph(
            "numpy/old.py#array",
            change="move",
            new_target="numpy/new.py#array",
            repo=repo,
        )
    )

    assert result == {"complete": True}
    assert calls[0][4:] == [
        "refresh",
        "--symbol",
        "numpy/old.py#array",
        "--depth",
        "2",
        "--change",
        "move",
        "--new-symbol",
        "numpy/new.py#array",
        "--repo",
        repo,
        "--json",
    ]


def test_refresh_graph_returns_the_structured_cli_failure_without_fallback_query(monkeypatch, tmp_path):
    calls = []
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert timeout == 600
        assert cwd == repo
        calls.append(command)
        return _result('{"error": "index is not ready", "exit": 3}', "graph unavailable", 3)

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    result = json.loads(mcp_server.amu_refresh_graph("numpy/core.py#array", repo=repo))

    assert result == {"error": "index is not ready", "exit": 3}
    assert [command[4] for command in calls] == ["refresh"]


def test_malformed_or_empty_cli_stdout_becomes_json_error(monkeypatch, tmp_path):
    results = iter([_result("not json"), _result("", "entire graph missing", 3)])
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert timeout == 600
        assert cwd == repo
        return next(results)

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    malformed = json.loads(mcp_server.amu_check(repo=repo))
    empty = json.loads(mcp_server.amu_done(repo=repo))

    assert malformed["error"] == "amu CLI returned invalid JSON"
    assert malformed["exit"] == 1
    assert malformed["stdout"] == "not json"
    assert empty == {"error": "entire graph missing", "exit": 3}


def test_cli_timeout_becomes_json_error(monkeypatch, tmp_path):
    repo = _repo(tmp_path)

    def fake_run(command, *, cwd, stdout_limit, stderr_limit, timeout):
        assert cwd == repo
        return SimpleNamespace(returncode=124, error="timeout", stdout=b"", stderr=b"")

    monkeypatch.setattr(mcp_server.process, "run", fake_run)

    result = json.loads(mcp_server.amu_graph("numpy/core.py#array", repo=repo))

    assert result["error"] == "amu CLI timed out"
    assert result["exit"] == 124


def test_empty_targets_return_json_input_errors_without_spawning_cli(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    monkeypatch.setattr(mcp_server.process, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    assert json.loads(mcp_server.amu_search("  ", repo=repo))["exit"] == 2
    assert json.loads(mcp_server.amu_graph("  ", repo=repo))["exit"] == 2
    assert json.loads(mcp_server.amu_map(target="  ", repo=repo))["exit"] == 2
    assert json.loads(mcp_server.amu_refresh_graph("  ", repo=repo))["exit"] == 2
    assert json.loads(mcp_server.amu_refresh_graph("a.py#old", new_target=" ", repo=repo))["exit"] == 2
