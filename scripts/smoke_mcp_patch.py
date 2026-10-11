"""Check an installed inspection-only v1/v6 MCP server in a disposable fixture.

Run with the candidate installation's Python -I and --python /absolute/python.
Only operator setup edits the tiny fixture; no candidate or check is executed.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REVIEWED_V1_SHA256 = "794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761"
SOURCE = "def first(value):\n    return value\n\ndef second(value):\n    return value\n"
CANDIDATE = SOURCE.replace("return value", "return value + 1")
PATCH = "".join(difflib.unified_diff(SOURCE.splitlines(keepends=True), CANDIDATE.splitlines(keepends=True),
                                   fromfile="a/api.py", tofile="b/api.py"))
TEST_SOURCE = ('from api import first\n\nraise AssertionError("inspection must never execute test modules")\n'
               '\ndef test_first():\n    assert first(1) == 1\n')


def _capture(repo: Path) -> dict:
    rows = {}
    for path in sorted(repo.rglob("*")):
        if ".git" in path.relative_to(repo).parts:
            continue
        assert not path.is_symlink(), path
        rows[path.relative_to(repo).as_posix()] = (
            "directory" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        )
    return rows


def main() -> int:
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    import amu
    from amu import mcp_access, mcp_manifest, mcp_verification
    from amu.patch import MAX_PATCH_BYTES

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="absolute Python executable for the installed candidate")
    args = parser.parse_args()
    assert sys.flags.isolated, "invoke this smoke script with candidate Python -I"
    assert Path(amu.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), amu.__file__
    assert amu.__version__ == importlib.metadata.version("amu"), "package version mismatch"
    assert mcp_manifest.inspection_manifest()["sha256"] == REVIEWED_V1_SHA256, "v1 manifest changed"
    python = Path(args.python)
    assert python.is_absolute() and python.is_file() and os.access(python, os.X_OK), "select an absolute Python executable"
    git = shutil.which("git")
    assert git, "git is required for the disposable fixture"
    report = {"schema_version": 1, "complete": False, "steps": [], "failures": [],
              "scope": "installed v1/v6 inspection-only SDK stdio in a tiny disposable fixture",
              "models_used": False, "candidate_executed": False, "checks_executed": False,
              "live_configuration_changed": False}
    old_environment = dict(os.environ)
    fixture_root = None

    def wire(result):
        return result.model_dump(by_alias=True)

    def payload(result):
        data = wire(result)
        assert not data.get("isError"), data
        texts = [item["text"] for item in data["content"] if item.get("type") == "text"]
        assert len(texts) == 1, data
        assert mcp_manifest.tool_result_bytes(texts[0]) <= mcp_manifest.MCP_WIRE_LIMIT
        assert len(json.dumps(data, ensure_ascii=True).encode("utf-8")) <= mcp_manifest.MCP_WIRE_LIMIT
        value = json.loads(texts[0])
        assert isinstance(value, dict), value
        return value

    try:
        with tempfile.TemporaryDirectory(prefix="amu-installed-patch-") as directory:
            fixture_root = root = Path(directory).resolve()
            home, bin_dir = root / "home", root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            (bin_dir / "git").symlink_to(git)
            env = {"PATH": str(bin_dir), "HOME": str(home), "AMU_HOME": str(home / ".amu"),
                   "AMU_DISABLE_MODELS": "1", "NO_COLOR": "1", "TERM": "dumb", "LANG": "en_US.UTF-8"}
            # SDK stdio also merges inherited defaults; remove that source.
            os.environ.clear()
            os.environ.update(env)
            repo, outside = root / "repo", root / "outside"
            repo.mkdir()
            outside.mkdir()
            child = repo / "child"
            child.mkdir()
            source, tests = repo / "api.py", repo / "test_api.py"
            source.write_text(SOURCE, encoding="utf-8")
            tests.write_text(TEST_SOURCE, encoding="utf-8")
            subprocess.run([git, "init", "-q", str(repo)], env=env, check=True, capture_output=True, timeout=10)
            subprocess.run([git, "-C", str(repo), "add", "api.py", "test_api.py"], env=env,
                           check=True, capture_output=True, timeout=10)
            subprocess.run([git, "-C", str(repo), "-c", "user.name=AMU fixture", "-c",
                            "user.email=fixture@amu.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
                           env=env, check=True, capture_output=True, timeout=10)
            baseline = _capture(repo)
            identities = {}
            for enabled in (False, True):
                diagnostic = mcp_verification.probe(str(python), str(repo), enable_patch_analysis=enabled)
                assert diagnostic["status"] == "matched" and diagnostic["manifest"]["match"], diagnostic
                name = "v6" if enabled else "v1"
                identities[name] = diagnostic["protocol"]["reported_identity"]
                report[name] = {"manifest_sha256": diagnostic["manifest"]["expected_sha256"],
                                "build_sha256": diagnostic["runtime"]["build_sha256"],
                                "tools": diagnostic["protocol"]["tool_names"]}

            async def probe():
                for enabled in (False, True):
                    name = "v6" if enabled else "v1"
                    arguments = ["-I", "-B", "-m", "amu.mcp_server", "--allow-repo", str(repo)]
                    if enabled:
                        arguments.append("--enable-patch-analysis")
                    params = StdioServerParameters(command=str(python), args=arguments, cwd=str(root), env=env)
                    with anyio.fail_after(45):
                        async with stdio_client(params) as (read, write):
                            async with ClientSession(read, write, read_timeout_seconds=10) as session:
                                hello = wire(await session.initialize())
                                identity = mcp_verification._initialize_identity(hello)
                                assert identity == identities[name], hello
                                assert hello["serverInfo"]["version"] == mcp_manifest.server_version(identity)
                                listed = wire(await session.list_tools())["tools"]
                                assert mcp_manifest.compare_tools(listed, enable_patch_analysis=enabled)["match"], listed
                                expected = mcp_access.PATCH_INSPECTION_TOOLS if enabled else mcp_access.INSPECTION_TOOLS
                                assert {tool["name"] for tool in listed} == expected, listed
                                assert not {"example-fixture", "verify-fixture", "amu_verify_fixture"} & expected
                                for tool in ("amu_docs_apply_review", "amu_map_target", "amu_refresh_graph"):
                                    rejected = wire(await session.call_tool(tool, {"repo": str(repo)}))
                                    assert rejected.get("isError"), rejected
                                report["steps"].append({"profile": name, "check": "manifest_identity_and_actions_denied", "status": "passed"})
                                if not enabled:
                                    rejected = wire(await session.call_tool("amu_analyze_patch", {"repo": str(repo), "patch": PATCH}))
                                    assert rejected.get("isError"), rejected
                                    continue

                                async def call(**options):
                                    return payload(await session.call_tool("amu_analyze_patch", {"repo": str(repo), "patch": PATCH, **options}))

                                summary = await call(page_size=1)
                                assert summary["schema_version"] == 6 and summary["page"]["section"] == "summary", summary
                                assert summary["summary"]["obligations"] and summary["retrieval"]["obligations"]["cursor"], summary
                                obligations = await call(section="obligations", page_size=1)
                                assert obligations["assessment"]["executed"] is False and obligations["details"]["items"], obligations
                                assert obligations["report_hash"] == summary["report_hash"], obligations
                                assert summary["summary"]["changed_files"] and summary["retrieval"]["changes"]["cursor"], summary
                                first = await call(section="changes", page_size=1)
                                assert first["operation"] == "concrete-patch-impact", first
                                assert first["assessment"]["executed"] is False and first["assessment"]["safe_to_change"] is None, first
                                assert first["page"]["section"] == "changes" and first["page"]["next_cursor"], first
                                assert first["report_hash"] == summary["report_hash"], first
                                second = await call(section="changes", page_size=1, cursor=first["page"]["next_cursor"])
                                assert second["report_hash"] == first["report_hash"] and second["page"]["offset"] == 1, second
                                full = await call(section="changes", page_size=100)
                                assert full["report_hash"] == first["report_hash"]
                                assert full["details"]["items"] == first["details"]["items"] + second["details"]["items"]
                                checks = await call(section="recommended_checks", cursor=first["retrieval"]["recommended_checks"]["cursor"])
                                assert checks["assessment"]["executed"] is False and checks["report_hash"] == first["report_hash"], checks
                                assert checks["details"]["items"], checks
                                for changed in ({"patch": 7}, {"include_graph": 1}, {"depth": True}, {"depth": "1"},
                                                {"page_size": True}, {"section": "impact"}, {"cursor": "x" * 1025}):
                                    rejected = wire(await session.call_tool("amu_analyze_patch", {"repo": str(repo), "patch": PATCH, **changed}))
                                    assert rejected.get("isError"), (changed, rejected)
                                for changed in ({"depth": 0}, {"patch": "x" * (MAX_PATCH_BYTES + 1)},
                                                {"patch": PATCH.replace("a/api.py", "a/../api.py").replace("b/api.py", "b/../api.py")}):
                                    denied = await call(**changed)
                                    assert denied["exit"] == 2 and denied.get("error"), denied
                                argument_limit = await call(patch="x" * (mcp_access.MAX_ARGUMENT_BYTES + 1))
                                assert argument_limit["error_kind"] == "argument_limit", argument_limit
                                for denied_repo in (str(outside), str(child), "."):
                                    denied = await call(repo=denied_repo)
                                    assert denied["exit"] == 2 and denied["error_kind"] in {"repository_not_allowed", "invalid_repository"}, denied
                                alias = root / "repo-alias"
                                alias.symlink_to(repo, target_is_directory=True)
                                canonical = await call(repo=str(alias), page_size=1)
                                assert canonical["report_hash"] == first["report_hash"], canonical
                                external = outside / "api.py"
                                external.write_text(SOURCE, encoding="utf-8")
                                linked = repo / "linked.py"
                                linked.symlink_to(external)
                                try:
                                    denied = await call(patch=PATCH.replace("api.py", "linked.py"))
                                    assert denied["exit"] == 2 and denied.get("error"), denied
                                    assert external.read_text(encoding="utf-8") == SOURCE
                                finally:
                                    linked.unlink()
                                # MCP accepts literal diffs only, never paths to diff files.
                                rejected = wire(await session.call_tool("amu_analyze_patch", {"repo": str(repo), "patch_file": str(root / "candidate.diff")}))
                                assert rejected.get("isError"), rejected
                                assert _capture(repo) == baseline, "inspection changed fixture inputs or managed state"
                                report["steps"].append({"profile": name, "check": "literal_patch_paging_strict_inputs_limits_repository_boundary", "status": "passed"})
                                tests.write_text(TEST_SOURCE + "\n# operator changed test evidence\n", encoding="utf-8")
                                edited = _capture(repo)
                                stale = await call(section="changes", page_size=1, cursor=first["page"]["next_cursor"])
                                assert stale["exit"] == 2 and "stale" in stale["error"], stale
                                fresh = await call(section="changes", page_size=1)
                                assert fresh["report_hash"] != first["report_hash"] and fresh["assessment"]["executed"] is False, fresh
                                assert _capture(repo) == edited, "inspection changed operator-edited fixture"
                                assert not (repo / ".amu").exists() and not (outside / ".amu").exists()
                                report["steps"].append({"profile": name, "check": "stale_cursor_rejected_and_source_tests_state_preserved", "status": "passed"})

            asyncio.run(probe())
        report["complete"] = True
    except Exception as exc:
        def causes(error):
            children = getattr(error, "exceptions", None)
            return [row for child in children for row in causes(child)] if children else [
                {"type": type(error).__name__, "error": str(error)[:3000]}]
        report["failures"].extend(causes(exc))
    finally:
        os.environ.clear()
        os.environ.update(old_environment)
    report["temporary_fixture_removed_after_run"] = fixture_root is not None and not fixture_root.exists()
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
