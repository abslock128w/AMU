"""The reviewed contract is independent of observed SDK/registration output."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from amu import mcp_manifest


def test_reviewed_manifest_has_four_full_schemas_and_conservative_annotations():
    manifest = mcp_manifest.inspection_manifest()
    assert manifest["manifest_version"] == "amu-inspection-v1"
    assert {row["name"] for row in manifest["tools"]} == {"amu_search", "amu_graph", "amu_impact", "amu_docs_reviews"}
    for row in manifest["tools"]:
        assert "repo" in row["inputSchema"]["required"]
        assert row["outputSchema"] == {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}
        assert row["annotations"] == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
    impact = next(row for row in manifest["tools"] if row["name"] == "amu_impact")
    assert impact["inputSchema"]["properties"]["page_size"] == {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}
    assert set(impact["inputSchema"]["properties"]["section"]["enum"]) == set(mcp_manifest.IMPACT_SECTIONS)


def test_expected_manifest_does_not_depend_on_runtime_registration(monkeypatch):
    from amu import mcp_server

    original = mcp_manifest.inspection_manifest()
    monkeypatch.setattr(mcp_server, "TOOLS", {})
    assert mcp_manifest.inspection_manifest() == original
    changed = mcp_manifest.inspection_manifest()
    changed["tools"][0]["inputSchema"]["required"].clear()
    assert mcp_manifest.inspection_manifest() == original


@pytest.mark.parametrize("defect", ["missing_tool", "action_tool", "required_repo", "input_type", "output_type", "annotation", "annotation_type", "page_bound"])
def test_full_semantic_parity_detects_each_contract_change(defect):
    observed = copy.deepcopy(mcp_manifest.inspection_manifest()["tools"])
    row = next(row for row in observed if row["name"] == "amu_impact")
    if defect == "missing_tool":
        observed.remove(row)
    elif defect == "action_tool":
        observed.append({**copy.deepcopy(row), "name": "amu_plan"})
    elif defect == "required_repo":
        row["inputSchema"]["required"].remove("repo")
    elif defect == "input_type":
        row["inputSchema"]["properties"]["line"]["type"] = "boolean"
    elif defect == "output_type":
        row["outputSchema"]["properties"]["result"]["type"] = "object"
    elif defect == "annotation":
        row["annotations"]["readOnlyHint"] = True
    elif defect == "annotation_type":
        row["annotations"]["readOnlyHint"] = 0  # Python equality is insufficient: 0 == False.
    else:
        row["inputSchema"]["properties"]["page_size"]["maximum"] = 101
    result = mcp_manifest.compare_tools(observed)
    assert result["match"] is False
    assert result["observed_sha256"] != result["expected_sha256"]


def test_titles_and_unordered_schema_lists_are_normalized_only():
    observed = mcp_manifest.inspection_manifest()["tools"]
    for tool in observed:
        tool["inputSchema"]["title"] = "SDK generated title"
        tool["inputSchema"]["required"].reverse()
        tool["outputSchema"]["title"] = "SDK return type title"
        tool["annotations"]["title"] = None
    assert mcp_manifest.compare_tools(list(reversed(observed)))["match"] is True


def test_schema_property_named_title_and_title_in_default_are_not_removed():
    schema = {"type": "object", "title": "presentation", "properties": {
        "title": {"type": "string", "default": {"title": "business data"}},
    }}
    row = {"name": "example", "inputSchema": schema, "outputSchema": None, "annotations": {}}
    actual = mcp_manifest.normalize_tools([row])[0]["inputSchema"]
    assert "title" not in actual
    assert actual["properties"]["title"]["default"] == {"title": "business data"}


@pytest.mark.parametrize("observed", [None, {}, [None], [{"name": "x", "inputSchema": True}],
                                      [{"name": "x", "inputSchema": {}}] * 2, [{"name": "x", "inputSchema": {}, "annotations": 1}]])
def test_invalid_manifests_fail_closed(observed):
    assert mcp_manifest.compare_tools(observed)["error_kind"] == "manifest_invalid"


def test_build_identity_hashes_actual_bytes_without_exposing_source(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 'fake-private-literal'\n")
    first = mcp_manifest.package_build(tmp_path)
    (tmp_path / "a.py").write_text("VALUE = 'changed-private-literal'\n")
    second = mcp_manifest.package_build(tmp_path)
    assert first["complete"] and second["complete"] and first["sha256"] != second["sha256"]
    assert "private-literal" not in json.dumps(first)


def test_package_links_and_oversized_sources_are_unknown_not_partial_identity(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n")
    (tmp_path / "alias.py").symlink_to(source)
    assert mcp_manifest.package_build(tmp_path)["sha256"] is None
    (tmp_path / "alias.py").unlink()
    monkeypatch.setattr(mcp_manifest, "_MAX_FILE_BYTES", 2)
    assert mcp_manifest.package_build(tmp_path)["sha256"] is None


def test_package_root_alias_cannot_claim_a_complete_build_identity(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "a.py").write_text("VALUE = 1\n")
    alias = tmp_path / "alias"
    alias.symlink_to(package, target_is_directory=True)
    assert mcp_manifest.package_build(alias)["complete"] is False


def test_package_enumeration_failure_cannot_claim_a_partial_build_identity(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("VALUE = 1\n")

    def failed_walk(*args, **kwargs):
        yield str(tmp_path), [], ["a.py"]
        kwargs["onerror"](PermissionError("fixture unreadable package subtree"))

    monkeypatch.setattr(mcp_manifest.os, "walk", failed_walk)
    assert mcp_manifest.package_build(tmp_path)["complete"] is False


def test_package_file_added_during_capture_cannot_claim_a_stable_identity(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("VALUE = 1\n")
    real_open = mcp_manifest.os.open
    added = False

    def open_and_add(path, *args, **kwargs):
        nonlocal added
        if not added:
            (tmp_path / "b.py").write_text("VALUE = 2\n")
            added = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(mcp_manifest.os, "open", open_and_add)
    assert mcp_manifest.package_build(tmp_path)["complete"] is False


def test_previously_hashed_file_changed_during_capture_is_not_a_complete_identity(tmp_path, monkeypatch):
    first = tmp_path / "a.py"
    first.write_text("VALUE = 1\n")
    (tmp_path / "b.py").write_text("VALUE = 2\n")
    real_open = mcp_manifest.os.open

    def open_and_change(path, *args, **kwargs):
        if Path(path).name == "b.py":
            first.write_text("VALUE = 3\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(mcp_manifest.os, "open", open_and_change)
    assert mcp_manifest.package_build(tmp_path)["complete"] is False


@pytest.mark.parametrize("text", ["plain text", '"\\\n' * 100, "世界" * 100, "🙂" * 100, "\x00" * 100])
def test_wire_estimate_includes_duplicates_unicode_and_bounded_request_id(text):
    actual = {"jsonrpc": "2.0", "id": "fixture", "result": {
        "content": [{"type": "text", "text": text}], "structuredContent": {"result": text}, "isError": False,
    }}
    encoded = json.dumps(actual, ensure_ascii=True).encode("utf-8")
    assert mcp_manifest.tool_result_bytes(text) >= len(encoded) + 1024


def test_runtime_and_public_identity_have_build_manifest_not_host_paths():
    runtime = mcp_manifest.runtime_identity()
    public = mcp_manifest.public_identity()
    assert runtime["build"]["complete"] and len(runtime["build_sha256"]) == 64
    assert len(public["manifest_sha256"]) == 64
    assert Path(runtime["module"]).is_file()
    assert "module" not in public and "executable" not in public


def test_base_mcp_diagnostic_imports_do_not_require_optional_sdk_or_pydantic():
    repo = str(Path(__file__).resolve().parents[1])
    script = """
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class NoOptionalSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mcp', 'pydantic'}:
            raise ImportError('optional SDK must not be imported by base diagnostics')
sys.meta_path.insert(0, NoOptionalSDK())
from amu import mcp_server, mcp_manifest, mcp_verification
assert not any(name.split('.')[0] in {'mcp', 'pydantic'} for name in sys.modules)
identity = mcp_manifest.runtime_identity()
assert identity['build']['complete'] and identity['build_sha256']
print(json.dumps({'manifest_version': identity['manifest_version']}))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, repo], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout) == {"manifest_version": "amu-inspection-v1"}


def test_actual_sdk_output_matches_independent_full_manifest():
    pytest.importorskip("mcp")
    import asyncio

    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    from amu import mcp_server

    async def run():
        server = MCPServer("manifest-regression", version="test")
        for row in mcp_manifest.inspection_manifest()["tools"]:
            server.tool(name=row["name"], annotations=ToolAnnotations.model_validate(row["annotations"]))(mcp_server._strict_mcp_tool(mcp_server.TOOLS[row["name"]][0]))
        observed = [tool.model_dump(by_alias=True) for tool in await server.list_tools()]
        assert mcp_manifest.compare_tools(observed)["match"] is True

    asyncio.run(run())
