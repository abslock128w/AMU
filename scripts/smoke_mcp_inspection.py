"""Opt-in real-provider inspection of a freshly installed wheel over MCP.

Only a disposable trusted Git fixture is initialized/edited by the operator
setup. The server stays inspection-only, with no models or inherited credentials.
This is not authenticated ChatGPT/Claude or NumPy acceptance evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path


def _capture(repo: Path) -> dict:
    """Inventory fixture inputs and AMU state, excluding native/Git caches."""
    result = {}
    paths = [repo / "pyproject.toml", repo / ".gitignore"]
    for name in ("pilotpkg", "docs", ".amu", "tests"):
        paths.extend([repo / name, *(repo / name).rglob("*")])
    for path in sorted(paths):
        info = path.lstat()
        assert not path.is_symlink() and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)), path
        row = {"kind": "directory" if path.is_dir() else "file", "mode": stat.S_IMODE(info.st_mode)}
        if path.is_file():
            assert info.st_size <= 2 * 1024 * 1024, path
            row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        result[path.relative_to(repo).as_posix()] = row
    return result


def main() -> int:
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from amu import mcp_manifest, mcp_verification

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entire", required=True)
    parser.add_argument("--graph-plugin", required=True)
    args = parser.parse_args()
    assert sys.flags.isolated, "run with candidate Python -I"
    helper_path = Path(__file__).resolve().with_name("smoke_pilot_installed.py")
    spec = importlib.util.spec_from_file_location("amu_inspection_fixture", helper_path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    report = {"schema_version": 1, "complete": False, "steps": [], "failures": [],
              "scope": "installed SDK inspection-only server and tiny trusted fixture; not a live AI client or NumPy",
              "models_used": False, "live_configuration_changed": False}
    fixture_root = None
    try:
        with tempfile.TemporaryDirectory(prefix="amu-mcp-inspection-") as directory:
            fixture_root = Path(directory)
            drill = fixture.Drill(Path(directory), fixture._binary(args.entire), fixture._binary(args.graph_plugin), 60, 300)
            drill.env["AMU_DISABLE_MODELS"] = "1"
            # SDK stdio_client merges inherited defaults; remove them too.
            os.environ.clear()
            os.environ.update(drill.env)
            drill.setup()
            report.update(engine=drill.report["engine"], application=mcp_manifest.runtime_identity(),
                          isolation=drill.report["isolation"], setup_steps=drill.report["steps"])
            baseline = _capture(drill.repo)
            diagnostic = drill.cli("installed-doctor-and-protocol", "doctor", "--mcp", expected=3)
            health = next(row for row in diagnostic["checks"] if row["name"] == "graph_health")
            assert health["ready"] is True and health["status"] == "passed", diagnostic
            assert diagnostic["mcp"]["manifest"]["match"] is True, diagnostic
            assert diagnostic["repository"]["category"] == "GRAPH_FRESHNESS_UNKNOWN", diagnostic
            assert diagnostic["ready"] is False and diagnostic["exit"] == 3, diagnostic
            assert _capture(drill.repo) == baseline, "doctor changed fixture inputs or managed state"
            report["doctor"] = diagnostic
            params = StdioServerParameters(command=sys.executable,
                args=["-I", "-m", "amu.mcp_server", "--allow-repo", str(drill.repo)],
                cwd=str(drill.repo), env=drill.env)

            async def probe():
                with anyio.fail_after(240):
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=60) as session:
                            hello = (await session.initialize()).model_dump(by_alias=True)
                            identity = mcp_verification._initialize_identity(hello)
                            assert identity and identity["build_sha256"] == report["application"]["build_sha256"], hello
                            report["initialize"] = {"server_info": hello["serverInfo"], "identity": identity}
                            listed = (await session.list_tools()).model_dump(by_alias=True)
                            report["manifest"] = mcp_manifest.compare_tools(listed["tools"])
                            assert report["manifest"]["match"], listed

                            async def call(name, *, expect_error=False, **arguments):
                                result = (await session.call_tool(name, {"repo": str(drill.repo), **arguments})).model_dump(by_alias=True)
                                assert not result.get("isError"), result
                                texts = [item["text"] for item in result["content"] if item.get("type") == "text"]
                                assert len(texts) == 1, result
                                assert result.get("structuredContent") == {"result": texts[0]}, result
                                assert mcp_manifest.tool_result_bytes(texts[0]) <= mcp_manifest.MCP_WIRE_LIMIT
                                assert len(json.dumps(result).encode("utf-8")) + 8192 <= mcp_manifest.MCP_WIRE_LIMIT
                                payload = json.loads(texts[0])
                                assert bool(payload.get("error")) is expect_error, payload
                                report["steps"].append({"tool": name, "status": "passed",
                                                        "expected_error": expect_error,
                                                        "sdk_result_bytes": len(json.dumps(result).encode("utf-8"))})
                                return payload

                            search = await call("amu_search", query="target")
                            assert search.get("hits"), search
                            report["search_hits"] = len(search["hits"])
                            graph = await call("amu_graph", target=fixture.TARGET, relation="CALLS")
                            assert graph.get("nodes"), graph
                            report["graph_warnings"] = graph.get("warnings", [])
                            isolated = await call("amu_graph", target="pilotpkg/api.py#isolated", relation="CALLS")
                            report["isolated_observation"] = {key: isolated.get(key) for key in ("warnings", "resolution", "graph_evidence")}
                            reviews = await call("amu_docs_reviews")
                            report["review_queue_keys"] = sorted(reviews)

                            arguments = {"file": "pilotpkg/api.py", "line": 2,
                                         "proposed_change": "Change the return value", "page_size": 1}
                            first = await call("amu_impact", **arguments)
                            assert first["assessment"]["safe_to_change"] is None
                            hashes, rows = {first["report_hash"]}, list(first["impact"]["rows"])
                            cursor = first["page"]["next_cursor"]
                            for _ in range(128):
                                if cursor is None:
                                    break
                                page = await call("amu_impact", **arguments, cursor=cursor)
                                hashes.add(page["report_hash"])
                                assert page["page"]["offset"] == len(rows)
                                rows.extend(page["impact"]["rows"])
                                cursor = page["page"]["next_cursor"]
                            assert cursor is None and len(hashes) == 1
                            assert len(rows) == first["retrieval"]["impact"]["logical_items"]
                            complete = await call("amu_impact", **{**arguments, "page_size": 100})
                            assert complete["report_hash"] == first["report_hash"]
                            assert complete["page"]["next_cursor"] is None and complete["impact"]["rows"] == rows
                            coverage = await call("amu_impact", **{**arguments, "page_size": 100}, section="coverage",
                                                  cursor=first["retrieval"]["coverage"]["cursor"])
                            assert coverage["report_hash"] == first["report_hash"]
                            assert coverage["page"]["next_cursor"] is None
                            details = {row["field"]: row["value"] for row in coverage["details"]["items"]}
                            edges = details["edge_baselines"][fixture.TARGET]
                            callers = {edge["related"] for edge in edges if edge["direction"] == "in"
                                       and edge["relation"] == "CALLS" and edge["depth"] == 1}
                            assert {"pilotpkg/api.py#same_file_caller", "pilotpkg/caller.py#cross_file_caller"} <= callers, edges
                            report["direct_callers"] = sorted(callers)
                            report["impact"] = {"status": first["status"], "report_hash": first["report_hash"],
                                                "rows_received": len(rows), "retrieval": first["retrieval"]}
                            after_inspection = _capture(drill.repo)
                            report["inspection_state_changes"] = {name: {"before": baseline.get(name), "after": after_inspection.get(name)}
                                for name in sorted(baseline.keys() | after_inspection.keys())
                                if baseline.get(name) != after_inspection.get(name)}
                            assert not report["inspection_state_changes"], "inspection changed source/docs/AMU state"
                            # Only the operator fixture edits code, never the tool.
                            drill.write("pilotpkg/api.py", fixture.INITIAL_SOURCE.replace("return target(value)", "return isolated()"))
                            edited_capture = _capture(drill.repo)
                            stale = await call("amu_impact", expect_error=True, **arguments, section="coverage",
                                               cursor=first["retrieval"]["coverage"]["cursor"])
                            assert stale["exit"] == 2 and "stale" in stale["error"], stale
                            report["stale_cursor_error"] = stale
                            updated = await call("amu_impact", **{**arguments, "page_size": 100}, section="coverage")
                            assert updated["report_hash"] != first["report_hash"]
                            updated_details = {row["field"]: row["value"] for row in updated["details"]["items"]}
                            updated_callers = {edge["related"] for edge in updated_details["edge_baselines"][fixture.TARGET]
                                               if edge["direction"] == "in" and edge["relation"] == "CALLS" and edge["depth"] == 1}
                            assert "pilotpkg/api.py#same_file_caller" not in updated_callers, updated_details
                            assert "pilotpkg/caller.py#cross_file_caller" in updated_callers, updated_details
                            report["updated_direct_callers"] = sorted(updated_callers)
                            assert _capture(drill.repo) == edited_capture, "inspection changed edited fixture state"
                            outside = drill.root / "unselected"
                            outside.mkdir()
                            denied = await call("amu_impact", expect_error=True, **{**arguments, "repo": str(outside)})
                            assert denied["error_kind"] == "repository_not_allowed" and not (outside / ".amu").exists()
                            report["authorization_negative"] = denied
                            report["inspection_preserved_source_docs_and_amu_state"] = True
                            for role, binary in (("dispatcher", drill.env["PATH"].split(os.pathsep)[0] + "/entire"),
                                                 ("graph_plugin", drill.env["PATH"].split(os.pathsep)[0] + "/entire-graph")):
                                assert fixture._identity(Path(binary))["sha256"] == report["engine"][role]["sha256"]

            asyncio.run(probe())
        # Teardown and fixture cleanup are part of acceptance, not an epilogue
        # after an already successful exit status.
        report["complete"] = True
    except Exception as exc:
        report["complete"] = False
        def causes(error):
            children = getattr(error, "exceptions", None)
            return [row for child in children for row in causes(child)] if children else [
                {"type": type(error).__name__, "error": str(error)[:3000]}]
        report["failures"].extend(causes(exc))
    report["temporary_fixture_removed_after_run"] = fixture_root is not None and not fixture_root.exists()
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
