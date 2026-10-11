"""Concrete diff adapters retain inspection, bounds and explicit repository authority."""

import inspect
import json
import os
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from amu import cli, mcp_access, mcp_manifest, mcp_server, router
from amu.patch import MAX_PATCH_BYTES
from amu.services import impact

PATCH = "--- a/api.py\n+++ b/api.py\n@@ -1,2 +1,2 @@\n def public(value):\n-    return value\n+    return value + 1\n"


def _packet():
    return {"operation": "concrete-patch-analysis", "status": "inspectable", "patch_sha256": "a" * 64,
            "verification_status": "not_executed", "page": {"section": "changes", "returned": 1}}


def test_cli_patch_delegates_once_without_saving_or_executing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(impact, "execute_patch", lambda *args: calls.append(args) or _packet())
    monkeypatch.setattr(cli.state, "write_json", lambda *_args, **_kwargs: pytest.fail("inspection must not save state"))
    result = CliRunner().invoke(cli.app, ["analyze-patch", f"--patch={PATCH}", "--base", "worktree",
                                       "--depth", "2", "--include-graph", "--section", "findings",
                                       "--page-size", "3", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _packet()
    assert calls == [(str(tmp_path), impact.PatchRequest(PATCH, "worktree", 2, True, "findings", None, 3))]
    assert not (tmp_path / ".amu").exists()


def test_cli_patch_file_reads_regular_utf8_without_applying(monkeypatch, tmp_path):
    path = tmp_path.resolve() / "candidate.diff"
    path.write_text(PATCH, encoding="utf-8")
    calls = []
    monkeypatch.setattr(impact, "execute_patch", lambda *args: calls.append(args) or _packet())
    result = CliRunner().invoke(cli.app, ["analyze-patch", "--patch-file", str(path),
                                       "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert calls[0][1].patch == PATCH
    assert calls[0][1].section == "summary"
    assert path.read_text() == PATCH
    assert not (tmp_path / "api.py").exists()


@pytest.mark.parametrize("options", [[], ["--patch=x", "--patch-file=unused"]])
def test_cli_patch_requires_exactly_one_input(monkeypatch, tmp_path, options):
    monkeypatch.setattr(impact, "execute_patch", lambda *_args: pytest.fail("ambiguous input must not dispatch"))
    result = CliRunner().invoke(cli.app, ["analyze-patch", *options, "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 2
    assert "exactly one" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "parent_symlink", "fifo", "utf8", "oversized"])
def test_patch_file_refuses_unbounded_or_aliased_input(tmp_path, kind):
    root = tmp_path.resolve()
    path = root / "candidate.diff"
    if kind == "directory":
        path.mkdir()
    elif kind in {"symlink", "parent_symlink"}:
        real = root / "real"
        real.mkdir()
        (real / "candidate.diff").write_text(PATCH)
        if kind == "symlink":
            path.symlink_to(real / "candidate.diff")
        else:
            path = root / "alias" / "candidate.diff"
            (root / "alias").symlink_to(real, target_is_directory=True)
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("named pipes unavailable")
        os.mkfifo(path)
    elif kind == "utf8":
        path.write_bytes(b"\xff")
    elif kind == "oversized":
        path.write_bytes(b"x" * (MAX_PATCH_BYTES + 1))
    with pytest.raises(impact.ImpactInputError):
        cli._read_patch_file(str(path))


def test_patch_file_mutation_during_capture_is_refused(monkeypatch, tmp_path):
    path = tmp_path.resolve() / "candidate.diff"
    path.write_text(PATCH)
    original = os.read
    changed = False

    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed:
            changed = True
            path.write_text(PATCH + "changed")
        return data

    monkeypatch.setattr(cli.os, "read", read)
    with pytest.raises(impact.ImpactInputError, match="changed"):
        cli._read_patch_file(str(path))


def test_cli_preserves_patch_input_error(monkeypatch, tmp_path):
    def refused(*_args):
        raise impact.ImpactInputError("stale candidate source")

    monkeypatch.setattr(impact, "execute_patch", refused)
    result = CliRunner().invoke(cli.app, ["analyze-patch", f"--patch={PATCH}", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stdout) == {"error": "stale candidate source", "exit": 2}


def test_mcp_patch_forwards_literal_as_assigned_option_once(monkeypatch, tmp_path):
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(stdout=json.dumps(_packet()).encode(), stderr=b"", returncode=0, error=None)

    monkeypatch.setattr(mcp_server, "_cli_process", run)
    assert json.loads(mcp_server.amu_analyze_patch(PATCH, base="worktree", include_graph=True,
                                                 section="findings", page_size=3, repo=str(tmp_path))) == _packet()
    command, options = calls[0]
    assert command[4:] == ["analyze-patch", f"--patch={PATCH}", "--base=worktree", "--depth", "1",
                          "--section=findings", "--page-size", "3", "--include-graph",
                          "--repo", str(tmp_path), "--json"]
    assert (options["timeout"], options["stdout_limit"], options["stderr_limit"]) == (600, 16 * 1024 * 1024, 64 * 1024)
    assert len(calls) == 1


@pytest.mark.parametrize("options", [{"patch": "invalid"}, {"depth": True}, {"include_graph": 1},
                                     {"page_size": True}, {"page_size": 101}, {"section": "impact"},
                                     {"cursor": "bad!"}, {"base": "other-branch"}])
def test_mcp_patch_rejects_invalid_input_before_cli(monkeypatch, options):
    monkeypatch.setattr(mcp_server, "_cli", lambda *_args, **_kwargs: pytest.fail("invalid input dispatched"))
    result = json.loads(mcp_server.amu_analyze_patch(repo="/unused", **{"patch": PATCH, **options}))
    assert result["exit"] == 2


def test_patch_policy_opt_in_is_independent_of_actions(tmp_path):
    for actions in (False, True):
        disabled = mcp_access.MCPAccessPolicy([str(tmp_path)], enable_actions=actions)
        assert not disabled.allows_tool("amu_analyze_patch")
        enabled = mcp_access.MCPAccessPolicy([str(tmp_path)], enable_actions=actions, enable_patch_analysis=True)
        assert enabled.allows_tool("amu_analyze_patch")
        assert enabled.allows_tool("amu_check") is actions
    assert "repo" in inspect.signature(mcp_server.amu_analyze_patch).parameters
    assert "patch_file" not in inspect.signature(mcp_server.amu_analyze_patch).parameters


def test_patch_policy_denies_before_read_and_sanitizes_environment(tmp_path, monkeypatch, capsys):
    pytest.importorskip("pydantic")
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    calls = []

    def amu_analyze_patch(patch: str, *, repo: str) -> str:
        calls.append((repo, mcp_server._CHILD_ENV.get()))
        return "{}"

    disabled = mcp_server._strict_mcp_tool(amu_analyze_patch, mcp_access.MCPAccessPolicy([str(root)]))
    assert json.loads(disabled("private diff", repo=str(root)))["error_kind"] == "patch_analysis_disabled"
    policy = mcp_access.MCPAccessPolicy([str(root)], enable_patch_analysis=True)
    wrapped = mcp_server._strict_mcp_tool(amu_analyze_patch, policy)
    assert json.loads(wrapped("private diff", repo=str(outside)))["error_kind"] == "repository_not_allowed"
    assert not calls
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    assert json.loads(wrapped(PATCH, repo=str(root))) == {}
    assert calls[0][1]["AMU_DISABLE_MODELS"] == "1"
    assert "OPENAI_API_KEY" not in calls[0][1]
    assert "private diff" not in capsys.readouterr().err


def test_default_manifest_identity_is_unchanged_and_v3_patch_contract_is_additive():
    original = mcp_manifest.inspection_manifest()
    assert original["manifest_version"] == "amu-inspection-v1"
    assert original["sha256"] == "794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761"
    extended = mcp_manifest.inspection_manifest(enable_patch_analysis=True)
    assert extended["manifest_version"] == "amu-inspection-v5"
    assert {tool["name"] for tool in extended["tools"]} == mcp_access.PATCH_INSPECTION_TOOLS
    assert [tool for tool in extended["tools"] if tool["name"] != "amu_analyze_patch"] == original["tools"]
    patch_tool = next(tool for tool in extended["tools"] if tool["name"] == "amu_analyze_patch")
    assert patch_tool["inputSchema"]["properties"]["section"]["default"] == "summary"
    assert not mcp_manifest.compare_tools(extended["tools"])["match"]
    assert mcp_manifest.compare_tools(extended["tools"], enable_patch_analysis=True)["match"]


@pytest.mark.parametrize("actions,patch_enabled", [(False, False), (True, False), (False, True), (True, True)])
def test_actual_sdk_profile_registration_and_identity(tmp_path, monkeypatch, actions, patch_enabled):
    pytest.importorskip("mcp")
    import asyncio

    from amu import mcp_transport

    servers = []
    monkeypatch.setattr(mcp_transport, "run_stdio", lambda server, **kwargs: servers.append((server, kwargs)))
    arguments = ["--allow-repo", str(tmp_path)]
    if actions:
        arguments.append("--enable-actions")
    if patch_enabled:
        arguments.append("--enable-patch-analysis")
    mcp_server.main(arguments)
    server, metadata = servers[0]
    observed = [tool.model_dump(by_alias=True) for tool in asyncio.run(server.list_tools())]
    selected = mcp_manifest.inspection_manifest(enable_patch_analysis=patch_enabled)
    inspection = [tool for tool in observed if tool["name"] in {row["name"] for row in selected["tools"]}]
    assert mcp_manifest.compare_tools(inspection, enable_patch_analysis=patch_enabled)["match"]
    assert ("amu_analyze_patch" in {tool["name"] for tool in observed}) is patch_enabled
    identity = mcp_manifest.public_identity(enable_patch_analysis=patch_enabled)
    assert metadata["version"] == mcp_manifest.server_version(identity)
    assert "candidate code" not in json.dumps(selected["tools"])  # schemas carry no source payload.


def test_interactive_patch_command_uses_shared_cli_parser():
    for text in ("analyze-patch --patch-file change.diff", "/analyze-patch --patch-file change.diff"):
        result = router.route(text, lambda _query: pytest.fail("command must not search"))
        assert result["status"] == "command" and result["intent"] == "analyze-patch"
