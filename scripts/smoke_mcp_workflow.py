"""Opt-in installed-wheel workflow through the actual MCP stdio tools.

The fixture/operator setup is local CLI; every change-workflow operation below
uses MCP. No live model calls, external account connection or source imports.
"""

import argparse
import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def main():
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entire", required=True)
    parser.add_argument("--graph-plugin", required=True)
    args = parser.parse_args()
    assert sys.flags.isolated, "invoke with installed Python -I"
    helper_path = Path(__file__).resolve().with_name("smoke_pilot_installed.py")
    spec = importlib.util.spec_from_file_location("amu_installed_fixture", helper_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    report = {"complete": False, "client": "pinned MCP SDK stdio; not a live AI client", "steps": [], "failures": []}
    fixture_root = None

    def wire(value):
        return value.model_dump(by_alias=True)

    try:
        with tempfile.TemporaryDirectory(prefix="amu-mcp-workflow-") as directory:
            fixture_root = Path(directory)
            drill = fixture.Drill(Path(directory), fixture._binary(args.entire), fixture._binary(args.graph_plugin), 60, 300)
            drill.setup()
            report["engine"] = drill.report["engine"]
            report["application"] = drill.report["application"]
            report["isolation"] = drill.report["isolation"]
            params = StdioServerParameters(command=sys.executable,
                args=["-I", "-m", "amu.mcp_server", "--allow-repo", str(drill.repo), "--enable-actions"],
                cwd=str(drill.repo), env=drill.env)

            async def probe():
                with anyio.fail_after(180):
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=60) as session:
                            await session.initialize()

                            async def call(name, **arguments):
                                result = wire(await session.call_tool(name, {"repo": str(drill.repo), **arguments}))
                                assert not result.get("isError"), name
                                texts = [row["text"] for row in result["content"] if row.get("type") == "text"]
                                assert len(texts) == 1, name
                                payload = json.loads(texts[0])
                                assert not payload.get("error"), (name, payload.get("error"))
                                report["steps"].append({"tool": name, "status": "passed"})
                                return payload

                            mapped = await call("amu_map_target", target=fixture.TARGET, change="signature")
                            assert mapped["graph_evidence"]["approvable"] is True
                            callers = {edge["related"] for edge in mapped["graph_evidence"]["edge_baselines"][fixture.TARGET]
                                       if edge["relation"] == "CALLS" and edge["direction"] == "in" and edge["depth"] == 1}
                            assert {"pilotpkg/api.py#same_file_caller", "pilotpkg/caller.py#cross_file_caller"} <= callers
                            planned = await call("amu_plan", targets=fixture.TARGET + ":signature",
                                                 docs_files=["docs/guide.md"], approve=True)
                            assert planned["contract"] and planned["docs_files"] == ["docs/guide.md"]
                            drill.write("pilotpkg/api.py", fixture.EDITED_SOURCE)
                            refreshed = await call("amu_refresh_graph", target=fixture.TARGET, change="signature")
                            assert refreshed["complete"] is True
                            for phase in planned["phases"]:
                                checked = await call("amu_check", phase=phase["n"])
                                assert checked["passed"] is True
                                assert checked["tests"]["evidence"]["passing_tests_verified"] is True
                            assert (await call("amu_done"))["complete"] is True
                            listing = await call("amu_docs")
                            report["documentation_listing_shape"] = {
                                "keys": sorted(listing),
                                "candidates": [{"keys": sorted(row), "id": row.get("id"),
                                                "candidate": row.get("candidate"), "targets": row.get("targets")}
                                               for row in listing.get("candidates", [])[:3]],
                            }
                            candidate = next(row for row in listing["candidates"]
                                             if row.get("candidate") == fixture.TARGET
                                             and any(target.get("file") == "docs/guide.md"
                                                     for target in row.get("targets", [])))
                            prepared = await call("amu_docs_prepare", candidate=candidate["id"])
                            info = prepared["prepared"]
                            draft = Path(info["file"]).resolve()
                            assert draft.is_relative_to(drill.repo) and draft.is_file()
                            draft.write_text("Use target(value, offset=0). Offset is an optional keyword-only addition.\n"
                                             "Reviewed reference: `pilotpkg/api.py#target`.\n", encoding="utf-8")
                            await call("amu_docs_reviews", preview=info["review_id"])
                            applied = await call("amu_docs_apply_review", candidate=candidate["id"], confirm=True)
                            assert candidate["id"] in applied["applied"]
                            assert any(save["graph_refreshed"] is True for save in applied["saves"])
                            # Existing phase4 semantics recheck cumulative scope,
                            # including the explicitly approved final-phase docs.
                            current = await call("amu_check", phase=4)
                            assert current["passed"] is True
                            assert (await call("amu_done"))["complete"] is True
                            journals = await call("amu_docs_saves")
                            saved = next(row for row in journals["saves"] if row["status"] == "matches_saved")
                            undo = await call("amu_docs_saves", undo=saved["save_id"], confirm=True)
                            assert undo["saved"] is True
                            report["complete"] = True
                            report["reviewed_documentation_current_completion"] = True
                            report["same_and_cross_file_callers"] = sorted(callers)

            asyncio.run(probe())
    except Exception as exc:
        report["complete"] = False
        def causes(error):
            nested = getattr(error, "exceptions", None)
            return [row for child in nested for row in causes(child)] if nested else [
                {"type": type(error).__name__, "error": str(error)[:2000]}]
        report["failures"].extend(causes(exc))
    report["temporary_fixture_removed_after_run"] = fixture_root is not None and not fixture_root.exists()
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
