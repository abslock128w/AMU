"""Run with an installed wheel's Python -I, after installing constrained MCP.

This probes the real stdio protocol in a disposable repository without a graph
provider, model credentials or permission to save documentation. It deliberately
does not replace the preceding base-package smoke test.
"""

import asyncio
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REVIEWED_INSPECTION_MANIFEST = "794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761"


def main():
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import amu
    from amu import mcp_manifest, mcp_verification

    package = Path(amu.__file__).resolve()
    assert package.is_relative_to(Path(sys.prefix).resolve()), package
    assert amu.__version__ == importlib.metadata.version("anti-messup"), "package version mismatch"
    assert sys.flags.isolated, "invoke this smoke script with Python -I"
    expected = mcp_manifest.inspection_manifest()
    assert expected["sha256"] == REVIEWED_INSPECTION_MANIFEST, "reviewed inspection manifest changed"
    installed_identity = mcp_manifest.runtime_identity()
    assert installed_identity["build"]["complete"] and installed_identity["build_sha256"], installed_identity
    git = shutil.which("git")
    assert git, "git is required for the smoke test"

    def wire(result):
        # MCP 2.x uses snake_case Python attributes; aliases are the JSON wire
        # contract. Do not depend on SDK attribute spellings such as isError.
        return result.model_dump(by_alias=True)

    def payload(result):
        texts = [item["text"] for item in wire(result)["content"] if item.get("type") == "text"]
        assert len(texts) == 1, wire(result)
        assert mcp_manifest.tool_result_bytes(texts[0]) <= mcp_manifest.MCP_WIRE_LIMIT, "oversized serialized tool response"
        assert len(json.dumps(wire(result), ensure_ascii=True).encode("utf-8")) <= mcp_manifest.MCP_WIRE_LIMIT, "oversized MCP SDK envelope"
        data = json.loads(texts[0])
        assert isinstance(data, dict), data
        return data

    with tempfile.TemporaryDirectory(prefix="amu-installed-mcp-") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        home.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(git)
        # stdio_client also reads its default inherited environment. Clear that
        # source as well as supplying env, not merely override a few key names.
        env = {"PATH": str(bin_dir), "HOME": str(home), "AMU_HOME": str(home / ".amu"),
               "NO_COLOR": "1", "TERM": "dumb", "LANG": "en_US.UTF-8"}
        os.environ.clear()
        os.environ.update(env)
        repo = root / "repo"
        repo.mkdir()
        subprocess.run([git, "init", "-q", str(repo)], env=env, cwd=root,
                       check=True, capture_output=True, timeout=10)
        (repo / "docs").mkdir()
        document = repo / "docs" / "guide.md"
        original = "# Guide\n\nOriginal prose.\n"
        document.write_text(original, encoding="utf-8")
        source = repo / "api.py"
        original_source = "def public(value):\n    return value\n"
        source.write_text(original_source, encoding="utf-8")
        outside = root / "outside"
        outside.mkdir()
        child = repo / "child"
        child.mkdir()
        inspection = StdioServerParameters(command=sys.executable, args=["-I", "-m", "amu.mcp_server",
                                           "--allow-repo", str(repo)], cwd=str(root), env=env)
        server = StdioServerParameters(command=sys.executable, args=["-I", "-m", "amu.mcp_server",
                                       "--allow-repo", str(repo), "--enable-actions"],
                                       cwd=str(root), env=env)

        async def probe():
            # Older wheels ignored these startup flags. Prove the installed
            # authority/default discovery, not merely a successful handshake.
            with anyio.fail_after(20):
                async with stdio_client(inspection) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as session:
                        hello = wire(await session.initialize())
                        public_identity = mcp_verification._initialize_identity(hello)
                        assert public_identity is not None, hello
                        assert public_identity["build_sha256"] == installed_identity["build_sha256"], public_identity
                        assert public_identity["manifest_sha256"] == REVIEWED_INSPECTION_MANIFEST, public_identity
                        assert hello["serverInfo"]["version"] == mcp_manifest.server_version(public_identity), hello
                        assert "amu_impact" in hello["instructions"] and "never guarantees" in hello["instructions"], hello
                        tools = {tool["name"]: tool for tool in wire(await session.list_tools())["tools"]}
                        assert mcp_manifest.compare_tools(list(tools.values()))["match"], tools
                        assert tools.keys() == {"amu_search", "amu_graph", "amu_impact", "amu_docs_reviews"}, tools.keys()
                        impact_schema = tools["amu_impact"]["inputSchema"]
                        assert {"file", "line", "proposed_change", "repo"} <= set(impact_schema["required"]), impact_schema
                        assert tools["amu_impact"]["annotations"]["destructiveHint"] is False, tools["amu_impact"]
                        denied = await session.call_tool("amu_docs_reviews", {"repo": str(outside)})
                        assert payload(denied)["error_kind"] == "repository_not_allowed", wire(denied)
                        assert not (outside / ".amu").exists()
                        for selected in (outside, child):
                            denied = await session.call_tool("amu_impact", {
                                "repo": str(selected), "file": "api.py", "line": 2,
                                "proposed_change": "validate input before returning",
                            })
                            assert payload(denied)["error_kind"] == "repository_not_allowed", wire(denied)
                        for field, value in (("line", True), ("line", "2"), ("line", 2.0),
                                             ("depth", True), ("depth", "2"), ("depth", 2.0), ("proposed_change", 1),
                                             ("page_size", True), ("page_size", "2"), ("page_size", 2.0),
                                             ("page_size", 0), ("page_size", 101), ("section", "invented"), ("cursor", 1)):
                            rejected = await session.call_tool("amu_impact", {
                                "repo": str(repo), "file": "api.py", "line": 2,
                                "proposed_change": "validate input before returning", field: value,
                            })
                            assert wire(rejected).get("isError"), (field, value, wire(rejected))
                        required = await session.call_tool("amu_impact", {"repo": str(repo), "file": "api.py", "line": 2})
                        assert wire(required).get("isError"), wire(required)
                        invalid = await session.call_tool("amu_impact", {
                            "repo": str(repo), "file": "api.py", "line": 0,
                            "proposed_change": "validate input before returning",
                        })
                        assert payload(invalid)["exit"] == 2, wire(invalid)
                        inspected = await session.call_tool("amu_impact", {
                            "repo": str(repo), "file": "api.py", "line": 2,
                            "proposed_change": "validate input before returning",
                        })
                        data = payload(inspected)
                        assert data["operation"] == "proposed-change-impact", data
                        assert data["assessment"]["safe_to_change"] is None, data
                        assert len(json.dumps(data).encode("utf-8")) <= 128 * 1024, "unbounded impact response"
                        page = payload(await session.call_tool("amu_impact", {
                            "repo": str(repo), "file": "api.py", "line": 2,
                            "proposed_change": "validate input before returning", "section": "coverage", "page_size": 1,
                        }))
                        assert page["operation"] == "proposed-change-impact", page
                        assert page["assessment"]["safe_to_change"] is None, page
                        for name in ("map.json", "contract.json", "nav.json", "run-history.json"):
                            assert not (repo / ".amu" / name).exists(), name
                        hidden = await session.call_tool("amu_docs_apply_review", {
                            "repo": str(repo), "candidate": "d1", "confirm": True,
                        })
                        assert wire(hidden).get("isError"), wire(hidden)
            # Includes process startup, protocol calls and context-manager
            # shutdown. Per-request deadlines are shorter than the whole probe.
            with anyio.fail_after(20):
                async with stdio_client(server) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as session:
                        hello = wire(await session.initialize())
                        assert hello["serverInfo"]["name"] and hello["protocolVersion"], hello
                        tools = {tool["name"]: tool for tool in wire(await session.list_tools())["tools"]}
                        assert {"amu_map_target", "amu_refresh_graph", "amu_impact", "amu_docs_reviews",
                                "amu_docs_apply_review", "amu_docs_prepare"} <= tools.keys(), tools.keys()
                        assert all("repo" in tool["inputSchema"].get("required", [])
                                   for tool in tools.values()), tools

                        queue = await session.call_tool("amu_docs_reviews", {"repo": str(repo)})
                        assert not wire(queue).get("isError") and payload(queue) == {"reviews": []}, wire(queue)
                        relative = await session.call_tool("amu_docs_reviews", {"repo": "."})
                        assert not wire(relative).get("isError") and payload(relative)["exit"] == 2, wire(relative)
                        missing = await session.call_tool("amu_docs_reviews", {})
                        assert wire(missing).get("isError"), wire(missing)

                        refused = await session.call_tool("amu_docs_apply_review", {
                            "repo": str(repo), "candidate": "d1", "confirm": False,
                        })
                        assert not wire(refused).get("isError"), wire(refused)
                        assert payload(refused)["exit"] == 2 and "explicit user approval" in payload(refused)["error"]
                        for value in (1, "true"):
                            rejected = await session.call_tool("amu_docs_apply_review", {
                                "repo": str(repo), "candidate": "d1", "confirm": value,
                            })
                            assert wire(rejected).get("isError"), (value, wire(rejected))
                        for index in (True, "1", 1.0):
                            rejected = await session.call_tool("amu_docs_prepare", {
                                "repo": str(repo), "candidate": "d1", "target_index": index,
                            })
                            assert wire(rejected).get("isError"), (index, wire(rejected))

                        # Genuine JSON true must reach the application unchanged.
                        # An invalid candidate prevents this consent-positive
                        # probe from executing a save or contacting the graph.
                        confirmed = await session.call_tool("amu_docs_apply_review", {
                            "repo": str(repo), "candidate": "invalid", "confirm": True,
                        })
                        assert not wire(confirmed).get("isError"), wire(confirmed)
                        assert payload(confirmed)["exit"] == 2
                        assert "positive target_index" in payload(confirmed)["error"], payload(confirmed)

            # Reaching here also establishes that session/server shutdown
            # finished under the overall deadline, not merely the last call.
            assert document.read_text(encoding="utf-8") == original
            assert source.read_text(encoding="utf-8") == original_source
            assert not (repo / ".amu" / "doc-saves").exists()

        asyncio.run(probe())
    print("Installed-MCP stdio smoke passed: inspection-only default, operator repository allowlist, "
          "four inspection tools, independent semantic manifest, installed build identity, line-impact inspection, "
          "bounded protocol responses, action opt-in, initialize, listing, authorized calls, "
          "strict line/depth/paging/consent/index inputs, shutdown.")


if __name__ == "__main__":
    main()
