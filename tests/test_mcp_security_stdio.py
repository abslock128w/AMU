"""Exercise repository authority and inspection defaults over the real wire."""

import asyncio
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("mcp")
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(result):
    return json.loads(result.model_dump(by_alias=True)["content"][0]["text"])


def test_real_stdio_blocks_other_repositories_and_hides_actions_by_default(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    child = allowed / "child"
    child.mkdir()
    (allowed / "api.py").write_text("def public(value):\n    return value\n")
    env = {**os.environ, "AMU_HOME": str(tmp_path / "home")}
    params = StdioServerParameters(command=sys.executable,
                                  args=["-I", "-m", "amu.mcp_server", "--allow-repo", str(allowed)], env=env)

    async def probe():
        with anyio.fail_after(20):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=10) as session:
                    hello = (await session.initialize()).model_dump(by_alias=True)
                    assert "amu_impact" in hello["instructions"] and "never guarantees" in hello["instructions"]
                    denied = await session.call_tool("amu_docs_reviews", {"repo": str(outside)})
                    assert _payload(denied)["error_kind"] == "repository_not_allowed"
                    assert not (outside / ".amu").exists()
                    queue = await session.call_tool("amu_docs_reviews", {"repo": str(allowed)})
                    assert _payload(queue) == {"reviews": []}
                    tools = {tool["name"]: tool for tool in (await session.list_tools()).model_dump(by_alias=True)["tools"]}
                    assert tools.keys() == {"amu_search", "amu_graph", "amu_impact", "amu_docs_reviews"}
                    impact_schema = tools["amu_impact"]["inputSchema"]
                    assert {"file", "line", "proposed_change", "repo"} <= set(impact_schema["required"])
                    assert tools["amu_impact"]["annotations"]["destructiveHint"] is False
                    for other_repo in (outside, child):
                        refused = await session.call_tool("amu_impact", {
                            "repo": str(other_repo), "file": "api.py", "line": 2, "proposed_change": "change validation",
                        })
                        assert _payload(refused)["error_kind"] == "repository_not_allowed"
                    for field, value in (("line", True), ("line", "2"), ("line", 2.0),
                                         ("depth", True), ("depth", "2"), ("depth", 2.0), ("proposed_change", 1)):
                        rejected = await session.call_tool("amu_impact", {
                            "repo": str(allowed), "file": "api.py", "line": 2,
                            "proposed_change": "change validation", field: value,
                        })
                        assert rejected.model_dump(by_alias=True)["isError"], (field, value)
                    required = await session.call_tool("amu_impact", {"repo": str(allowed), "file": "api.py", "line": 2})
                    assert required.model_dump(by_alias=True)["isError"]
                    invalid_bounds = await session.call_tool("amu_impact", {
                        "repo": str(allowed), "file": "api.py", "line": 0, "proposed_change": "change validation",
                    })
                    assert _payload(invalid_bounds)["exit"] == 2
                    inspected = await session.call_tool("amu_impact", {
                        "repo": str(allowed), "file": "api.py", "line": 2,
                        "proposed_change": "return value only after validation",
                    })
                    packet = _payload(inspected)
                    assert packet["operation"] == "proposed-change-impact"
                    assert packet["assessment"]["safe_to_change"] is None
                    for state_file in ("map.json", "contract.json", "nav.json", "run-history.json"):
                        assert not (allowed / ".amu" / state_file).exists()
                    blocked = await session.call_tool("amu_docs_apply_review", {
                        "repo": str(allowed), "candidate": "d1", "confirm": True,
                    })
                    assert blocked.model_dump(by_alias=True)["isError"]

    asyncio.run(probe())


def test_server_without_explicit_repository_authority_fails_closed():
    result = subprocess.run([sys.executable, "-I", "-m", "amu.mcp_server"], input="", text=True,
                            capture_output=True, timeout=10)
    assert result.returncode == 2
    assert "--allow-repo" in result.stderr and not result.stdout
