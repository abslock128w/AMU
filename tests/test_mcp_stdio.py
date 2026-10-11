"""Exercise the actual optional MCP stdio server, not just Python wrappers."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(result):
    texts = [item["text"] for item in result.model_dump(by_alias=True)["content"] if item.get("type") == "text"]
    assert len(texts) == 1
    return json.loads(texts[0])


def test_real_stdio_initialize_list_safe_calls_and_confirmation_guards(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    document = repo / "docs" / "guide.md"
    document.write_text("# Guide\n\nOriginal prose.\n")
    env = dict(os.environ)
    env["AMU_HOME"] = str(tmp_path / "home")
    for name in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "AMU_PROVIDER"):
        env.pop(name, None)
    server = StdioServerParameters(command=sys.executable, args=["-I", "-m", "amu.mcp_server",
                                   "--allow-repo", str(repo), "--enable-actions"],
                                   cwd=str(Path(__file__).resolve().parents[1]), env=env)

    async def probe():
        with anyio.fail_after(20):
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=10) as session:
                    initialized = await session.initialize()
                    hello = initialized.model_dump(by_alias=True)
                    assert hello["serverInfo"]["name"] and hello["protocolVersion"]
                    listed = await session.list_tools()
                    tools = {tool["name"]: tool for tool in listed.model_dump(by_alias=True)["tools"]}
                    assert {"amu_map_target", "amu_refresh_graph", "amu_impact", "amu_docs_reviews", "amu_docs_apply_review"} <= tools.keys()
                    assert all("repo" in tool["inputSchema"].get("required", []) for tool in tools.values())
                    queue = await session.call_tool("amu_docs_reviews", {"repo": str(repo)})
                    assert not queue.model_dump(by_alias=True).get("isError") and _payload(queue) == {"reviews": []}
                    relative = await session.call_tool("amu_docs_reviews", {"repo": "."})
                    assert _payload(relative)["exit"] == 2
                    for value in (False, 1, "true"):
                        rejected = await session.call_tool("amu_docs_apply_review", {
                            "repo": str(repo), "candidate": "d1", "confirm": value,
                        })
                        if not rejected.model_dump(by_alias=True).get("isError"):
                            data = _payload(rejected)
                            assert data["exit"] == 2 and "explicit user approval" in data["error"]
                    # Consent and integer guards must see JSON types, not the
                    # SDK's default bool/int coercions. Never execute writes.
                    for tool, args in (
                        ("amu_docs_discard", {"review_id": "missing", "confirm": 1}),
                        ("amu_docs_saves", {"undo": "missing", "confirm": "true"}),
                        ("amu_plan", {"targets": "n1", "approve": 1}),
                        ("amu_plan", {"targets": "n1", "docs_files": [1]}),
                        ("amu_graph", {"target": "api.py#public", "depth": True}),
                        ("amu_impact", {"file": "api.py", "line": True, "proposed_change": "change validation"}),
                        ("amu_impact", {"file": "api.py", "line": 1, "depth": "2", "proposed_change": "change validation"}),
                        ("amu_check", {"phase": "1"}),
                        ("amu_docs_reviews", {"repo": True}),
                        ("amu_docs_reviews", {}),
                    ):
                        arguments = {"repo": str(repo), **args}
                        if tool == "amu_docs_reviews" and not args:
                            arguments = {}
                        rejected = await session.call_tool(tool, arguments)
                        assert rejected.model_dump(by_alias=True).get("isError"), (tool, arguments)
                    for index in (True, "1", 1.0):
                        wrong_index = await session.call_tool("amu_docs_prepare", {
                            "repo": str(repo), "candidate": "d1", "target_index": index,
                        })
                        assert wrong_index.model_dump(by_alias=True).get("isError")
                    # A real JSON true reaches the application guard unchanged.
                    # An invalid candidate makes this a no-write positive probe.
                    confirmed = await session.call_tool("amu_docs_apply_review", {
                        "repo": str(repo), "candidate": "invalid", "confirm": True,
                    })
                    assert not confirmed.model_dump(by_alias=True).get("isError")
                    assert "positive target_index" in _payload(confirmed)["error"]
                    assert document.read_text() == "# Guide\n\nOriginal prose.\n"
                    assert not (repo / ".amu" / "doc-saves").exists()

    asyncio.run(probe())
