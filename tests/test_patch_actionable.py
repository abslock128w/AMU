"""Mixed patch coverage, bounded source paths, and compact lossless reporting."""

import difflib
import hashlib
import json
from dataclasses import replace

import pytest

from amu.patch import PatchError, parse_diff
from amu.services import impact, impact_report


def diff(before, after, path):
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True) if before is not None else [],
        after.splitlines(keepends=True) if after is not None else [],
        fromfile="a/" + path if before is not None else "/dev/null",
        tofile="b/" + path if after is not None else "/dev/null",
    ))


def test_mixed_patch_records_complete_identity_and_content_only_docs(tmp_path):
    before = "def calculate(value):\n    return value + 1\n"
    after = "raise RuntimeError('candidate executed')\n" + before.replace("+ 1", "+ 2")
    prose_before = "# Guide\n\nThe old behavior.\n"
    prose_after = prose_before.replace("old", "new")
    (tmp_path / "engine.py").write_text(before)
    (tmp_path / "GUIDE.md").write_text(prose_before)
    patch = diff(before, after, "engine.py") + diff(prose_before, prose_after, "GUIDE.md")

    request = impact.PatchRequest(patch, base="worktree")
    report = impact.execute_patch(str(tmp_path), request)

    assert report["schema_version"] == 3
    assert report["request"]["patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert {row["path"]: row["analysis"] for row in report["identity"]["file_coverage"]} == {
        "engine.py": "python-syntax", "GUIDE.md": "content-only"}
    assert "Python behavioral analysis does not cover" in next(
        row["limitation"] for row in report["identity"]["file_coverage"] if row["path"] == "GUIDE.md")
    assert {row["path"] for row in report["summary"]["changed_files"]} == {"engine.py", "GUIDE.md"}
    assert report["assessment"]["counts"]["changes"] >= 2
    changes = impact.execute_patch(str(tmp_path), replace(request, section="changes"))
    assert any(row.get("path") == "GUIDE.md" and row.get("analysis") == "content-only"
               for row in changes["details"]["items"])
    assert changes["report_hash"] == report["report_hash"]
    assert (tmp_path / "engine.py").read_text() == before
    assert (tmp_path / "GUIDE.md").read_text() == prose_before
    assert not (tmp_path / ".amu").exists()
    assert report["assessment"]["executed"] is False


@pytest.mark.parametrize("contents", [b"# Guide\n\xff", b"# Guide\x00\n", b"# Guide\r\n"])
def test_markdown_source_must_be_ordinary_utf8_lf_text(tmp_path, contents):
    (tmp_path / "GUIDE.md").write_bytes(contents)
    patch = diff("# Guide\n", "# Updated\n", "GUIDE.md")
    with pytest.raises(impact.ImpactInputError, match="Markdown"):
        impact.execute_patch(str(tmp_path), impact.PatchRequest(patch, base="worktree"))


def test_markdown_hunks_and_path_protections_remain_exact(tmp_path):
    (tmp_path / "GUIDE.md").write_text("# Actual\n")
    stale = diff("# Stale\n", "# New\n", "GUIDE.md")
    with pytest.raises(impact.ImpactInputError, match="context"):
        impact.execute_patch(str(tmp_path), impact.PatchRequest(stale, base="worktree"))
    (tmp_path / "GUIDE.md").unlink()
    (tmp_path / "GUIDE.md").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(impact.ImpactInputError, match="symbolic"):
        impact.execute_patch(str(tmp_path), impact.PatchRequest(stale, base="worktree"))
    with pytest.raises(PatchError):
        parse_diff(diff("x\n", "y\n", "../escape.md"))
    with pytest.raises(PatchError):
        parse_diff("diff --git a/GUIDE.md b/GUIDE.md\nold mode 100644\nnew mode 100755\n" + stale)
    with pytest.raises(PatchError):
        parse_diff(diff("x\n", "y\n", "script.txt"))
    with pytest.raises(PatchError):
        parse_diff(diff("x\n", "y\n", "executable.mdx"))


def test_markdown_addition_and_deletion_are_content_only_and_read_only(tmp_path):
    (tmp_path / "old.markdown").write_text("# Old\n")
    patch = diff("# Old\n", None, "old.markdown") + diff(None, "# New\n", "new.md")
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(patch, base="worktree"))
    assert [(row["path"], row["operation"]) for row in report["identity"]["files"]] == [
        ("old.markdown", "delete"), ("new.md", "add")]
    assert all(row["analysis"] == "content-only" for row in report["identity"]["file_coverage"])
    assert report["assessment"]["counts"]["recommended_checks"] == 0
    assert (tmp_path / "old.markdown").read_text() == "# Old\n"
    assert not (tmp_path / "new.md").exists()


def _package_case(tmp_path, *, rebound=False, dynamic=False, package_override=None):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(package_override if package_override is not None else '"""Package."""\n')
    source = (
        "def _core(value):\n    return value + 1\n\n"
        "def public(value):\n"
        + ("    _core = lambda item: item\n" if rebound else "")
        + ("    globals()['_core'] = lambda item: item\n" if dynamic else "")
        + "    return _core(value)\n\n"
        "def unrelated(value):\n    return value\n"
    )
    (package / "worker.py").write_text(source)
    tests = (
        "from pkg import worker\n\n"
        "def test_public():\n    assert worker.public(2)\n\n"
        "def test_unrelated():\n    assert worker.unrelated(2)\n\n"
        "def test_loaded_only():\n    assert callable(worker.public)\n\n"
        "def test_shadowed(worker):\n    assert worker.public(2)\n"
    )
    (tmp_path / "test_worker.py").write_text(tests)
    candidate_tests = tests + "\ndef test_candidate_only():\n    assert worker._core(2)\n"
    patch = (diff(source, source.replace("value + 1", "value + 2"), "pkg/worker.py")
             + diff(tests, candidate_tests, "test_worker.py"))
    return impact.PatchRequest(patch, base="worktree", section="recommended_checks")


def test_verified_package_submodule_and_direct_source_call_recommend_existing_test(tmp_path):
    request = _package_case(tmp_path)
    report = impact.execute_patch(str(tmp_path), request)
    rows = report["details"]["items"]
    assert [row["test_id"] for row in rows] == ["test_worker.py::test_public"]
    row = rows[0]
    assert row["coverage_proven"] is False
    assert row["test_source_phase"] == "before"
    association = next(a for a in row["associations"] if a["entity_id"] == "pkg/worker.py#_core")
    assert association["relationship"] == "source-call-path"
    assert association["resolution"] == "resolved"
    assert [step["role"] for step in association["path"]] == [
        "test_reference", "source_declaration", "source_call", "source_declaration"]
    assert association["path"][0]["location"]["line"] == 4
    assert association["path"][2]["location"]["line"] == 5
    assert "assert" in association["limitation"]
    assert report["identity"]["resolution_sources"][0]["path"] == "pkg/__init__.py"
    assert report["identity"]["candidate_test_files"] == ["test_worker.py"]
    assert "test_candidate_only" not in json.dumps(report)
    assert (tmp_path / "test_worker.py").read_text().count("def test_") == 4


def test_literal_package_version_preserves_supported_submodule_path(tmp_path):
    request = _package_case(tmp_path, package_override='__version__ = "1.0"\n')
    report = impact.execute_patch(str(tmp_path), request)
    assert [row["test_id"] for row in report["details"]["items"]] == ["test_worker.py::test_public"]
    assert report["identity"]["recommendation_scope"]["unresolved_package_imports"] == []
    assert report["identity"]["candidate_test_files"] == ["test_worker.py"]
    assert "test_candidate_only" not in json.dumps(report)


def test_literal_binding_of_imported_name_stays_unresolved(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "1.0"\n')
    before = "def run():\n    return 1\n"
    (package / "__version__.py").write_text(before)
    (tmp_path / "test_version.py").write_text(
        "from pkg import __version__\n\ndef test_run():\n    assert __version__.run()\n")
    patch = diff(before, before.replace("return 1", "return 2"), "pkg/__version__.py")
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(
        patch, base="worktree", section="recommended_checks"))
    assert report["details"]["items"] == []
    assert report["identity"]["recommendation_scope"]["unresolved_package_imports"][0]["reason"] == (
        "package initializer binds the imported submodule name")


def test_candidate_added_tests_are_separate_from_baseline_recommendations(tmp_path):
    request = _package_case(tmp_path)
    added = "from pkg import worker\n\ndef test_new():\n    assert worker.public(2)\n"
    request = replace(request, patch=request.patch + diff(None, added, "test_new.py"))
    report = impact.execute_patch(str(tmp_path), request)
    assert [row["test_id"] for row in report["details"]["items"]] == ["test_worker.py::test_public"]
    assert report["identity"]["candidate_existing_test_files"] == ["test_worker.py"]
    assert report["identity"]["candidate_added_test_files"] == ["test_new.py"]
    summary = impact.execute_patch(str(tmp_path), replace(request, section="summary"))["summary"]
    assert summary["candidate_added_test_files"] == ["test_new.py"]
    assert "excluded from baseline discovery" in summary["recommendation_search"]


def test_empty_recommendation_explains_unresolved_and_limited_search(tmp_path, monkeypatch):
    request = _package_case(tmp_path, package_override="worker = object()\n")
    summary = impact.execute_patch(str(tmp_path), replace(request, section="summary"))["summary"]
    assert summary["existing_checks"] == []
    assert "No candidates found within the completed bounded search" in summary["recommendation_search"]
    assert "relationship(s) remain unresolved" in summary["recommendation_search"]
    monkeypatch.setattr(impact, "MAX_TEST_FILES", 0)
    limited = impact.execute_patch(str(tmp_path), replace(request, section="summary"))
    assert limited["identity"]["test_inventory_complete"] is False
    assert "Search incomplete" in limited["summary"]["recommendation_search"]
    assert "test inventory was incomplete" in limited["summary"]["recommendation_search"]


def test_unparseable_baseline_test_is_reported_as_unavailable(tmp_path):
    request = _package_case(tmp_path, package_override="worker = object()\n")
    (tmp_path / "test_unparseable.py").write_text("def test_bad(:\n")
    detailed = impact.execute_patch(str(tmp_path), request)
    unavailable = detailed["identity"]["recommendation_scope"]["unavailable_baseline_tests"]
    assert [row["path"] for row in unavailable] == ["test_unparseable.py"]
    summary = impact.execute_patch(str(tmp_path), replace(request, section="summary"))["summary"]
    assert "Search incomplete" in summary["recommendation_search"]
    assert "1 baseline test file(s) could not be parsed" in summary["recommendation_search"]


@pytest.mark.parametrize("rebound,dynamic,package_override", [
    (True, False, None),
    (False, True, None),
    (False, False, "worker = object()\n"),
    (False, False, '__version__ = compute()\n'),
])
def test_ambiguous_source_or_package_binding_does_not_create_specific_path(tmp_path, rebound, dynamic, package_override):
    request = _package_case(tmp_path, rebound=rebound, dynamic=dynamic, package_override=package_override)
    report = impact.execute_patch(str(tmp_path), request)
    assert report["assessment"]["counts"]["recommended_checks"] == 0
    summary = impact.execute_patch(str(tmp_path), replace(request, section="summary"))["summary"]
    assert "No supported" in summary["recommendation_search"]


@pytest.mark.parametrize("competitor,changed", [
    ("src/pkg/worker.py", False),
    ("src/pkg/worker.py", True),
    ("pkg/worker/__init__.py", False),
])
def test_competing_package_module_paths_do_not_claim_a_specific_test_path(tmp_path, competitor, changed):
    request = _package_case(tmp_path)
    path = tmp_path / competitor
    path.parent.mkdir(parents=True, exist_ok=True)
    before = "def run():\n    return 1\n"
    path.write_text(before)
    if changed:
        request = replace(request, patch=request.patch + diff(
            before, before.replace("return 1", "return 2"), competitor))
    report = impact.execute_patch(str(tmp_path), request)
    assert report["assessment"]["counts"]["recommended_checks"] == 0
    assert report["identity"]["recommendation_scope"]["unresolved_package_imports"]
    assert not report["details"]["items"]


def test_new_competing_package_module_invalidates_existing_check_cursor(tmp_path):
    request = _package_case(tmp_path)
    report = impact.execute_patch(str(tmp_path), request)
    assert "src/pkg/worker.py" in report["identity"]["resolution_absences"]
    path = tmp_path / "src" / "pkg" / "worker.py"
    path.parent.mkdir(parents=True)
    path.write_text("def run():\n    return 1\n")
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(
            request, cursor=report["retrieval"]["recommended_checks"]["cursor"]))


def test_competing_package_module_created_during_inspection_refuses_report(tmp_path, monkeypatch):
    request = _package_case(tmp_path)
    original = impact._source_path_present_or_unsafe
    created = False

    def check_and_create(root, relative):
        nonlocal created
        present = original(root, relative)
        if relative == "src/pkg/worker.py" and not created:
            created = True
            path = tmp_path / relative
            path.parent.mkdir(parents=True)
            path.write_text("def run():\n    return 1\n")
        return present

    monkeypatch.setattr(impact, "_source_path_present_or_unsafe", check_and_create)
    with pytest.raises(impact.ImpactInputError, match="competing package source path changed"):
        impact.execute_patch(str(tmp_path), request)


def test_package_initializer_source_change_invalidates_evidence_cursor(tmp_path):
    request = _package_case(tmp_path)
    report = impact.execute_patch(str(tmp_path), request)
    cursor = report["retrieval"]["recommended_checks"]["cursor"]
    (tmp_path / "pkg" / "__init__.py").write_text('"""Changed package docstring."""\n')
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(request, cursor=cursor))


def test_package_initializer_is_bound_once_for_multiple_affected_modules(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('"""Package."""\n')
    before = "def run(value):\n    return value + 1\n"
    for name in ("one", "two"):
        (package / f"{name}.py").write_text(before)
    (tmp_path / "test_modules.py").write_text(
        "from pkg import one, two\n\ndef test_one():\n    assert one.run(1)\n"
        "\ndef test_two():\n    assert two.run(1)\n")
    patch = "".join(diff(before, before.replace("+ 1", "+ 2"), f"pkg/{name}.py")
                    for name in ("one", "two"))
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(
        patch, base="worktree", section="recommended_checks"))
    assert [row["test_id"] for row in report["details"]["items"]] == [
        "test_modules.py::test_one", "test_modules.py::test_two"]
    assert [row["path"] for row in report["identity"]["resolution_sources"]] == ["pkg/__init__.py"]


def test_package_initializer_race_refuses_completed_report(tmp_path, monkeypatch):
    request = _package_case(tmp_path)
    original = impact._source
    raced = False

    def read_and_change(root, relative, **kwargs):
        nonlocal raced
        result = original(root, relative, **kwargs)
        if relative == "pkg/__init__.py" and not raced:
            raced = True
            (tmp_path / relative).write_text('"""Changed after read."""\n')
        return result

    monkeypatch.setattr(impact, "_source", read_and_change)
    with pytest.raises(impact.ImpactInputError, match="changed during inspection"):
        impact.execute_patch(str(tmp_path), request)


def _report_groups():
    groups = {name: [] for name in impact_report.PATCH_SECTIONS}
    groups["changes"] = [
        {"id": "src/api.py#run", "path": "src/api.py", "kind": "function", "change": "modified"},
        {"id": "src/api.py#_NOTE", "path": "src/api.py", "kind": "state", "change": "modified"},
    ]
    groups["findings"] = [
        {"entity_id": "src/api.py#_NOTE", "category": "state_declaration", "observation": "state declaration syntax changed"},
        {"entity_id": "src/api.py#run", "category": "call", "observation": "call syntax changed",
         "recommended_test_ids": ["test_api.py::test_run"]},
    ]
    groups["state_dependencies"] = [
        {"state_id": "src/api.py#_NOTE", "consumer_id": "src/api.py#run", "access": "read", "phase": phase,
         "expression_hash": "a" * 64, "location": {"line": line, "column": 2}}
        for phase, line in (("before", 5), ("after", 6))
    ]
    groups["uncertainties"] = [
        {"path": "src/api.py", "reason": "dynamic target unresolved", "phase": phase,
         "location": {"line": line, "column": 2}}
        for phase, line in (("before", 4), ("before", 5), ("after", 4), ("after", 5))
    ] + [{"path": "tests/test_api.py", "reason": "dynamic target unresolved", "phase": "after",
          "location": {"line": 2}},
         {"path": "src/api.py", "reason": "new uncertainty", "phase": "after", "location": {"line": 8}}]
    return groups


def test_summary_groups_repetition_ranks_edits_and_keeps_every_raw_item():
    request = {"patch": "+x", "base": "worktree", "depth": 1, "include_graph": False,
               "section": "summary", "cursor": None, "page_size": 2}
    identity = {"files": [{"path": "src/api.py", "operation": "modify"}], "test_sources": []}
    groups = _report_groups()
    packet = impact_report.build_patch_packet(request, identity=identity, groups=groups,
                                              limitations=[], max_bytes=64 * 1024, max_rows=100)
    summary = packet["summary"]
    assert summary["findings"][0]["entity_id"] == "src/api.py#run"
    assert summary["uncertainty_group_count"] == 2
    repeated = summary["uncertainty_groups"][0]
    assert repeated["reason"] == "dynamic target unresolved"
    assert repeated["count"] == 5
    assert repeated["phase_counts"] == {"before": 2, "after": 3}
    assert repeated["path_counts"] == {"src/api.py": 4, "tests/test_api.py": 1}
    assert {sample["phase"] for sample in repeated["samples"]} == {"before", "after"}
    assert summary["state_relationships"][0]["phases"] == ["before", "after"]
    assert summary["state_relationships"][0]["count"] == 2
    assert packet["retrieval"]["uncertainties"]["logical_items"] == 6
    assert packet["truncated"] is True
    assert len(json.dumps(packet, ensure_ascii=False).encode()) < 12 * 1024

    seen, cursor = [], None
    while True:
        page = impact_report.build_patch_packet({**request, "section": "uncertainties", "cursor": cursor},
                                                identity=identity, groups=groups, limitations=[],
                                                max_bytes=64 * 1024, max_rows=100)
        seen.extend(page["details"]["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert seen == groups["uncertainties"]
    assert impact_report.build_patch_packet(request, identity=identity, groups={
        **groups, "findings": list(reversed(groups["findings"]))}, limitations=[],
        max_bytes=64 * 1024, max_rows=100)["summary"]["findings"] == summary["findings"]
    assert impact_report.build_patch_packet(request, identity=identity, groups={
        **groups, "uncertainties": list(reversed(groups["uncertainties"]))}, limitations=[],
        max_bytes=64 * 1024, max_rows=100)["summary"]["uncertainty_groups"] == summary["uncertainty_groups"]
    changed = {**groups, "uncertainties": [*groups["uncertainties"], {"reason": "additional evidence"}]}
    with pytest.raises(impact_report.ReportError, match="stale"):
        impact_report.build_patch_packet({**request, "section": "uncertainties",
                                          "cursor": packet["retrieval"]["uncertainties"]["cursor"]},
                                         identity=identity, groups=changed, limitations=[],
                                         max_bytes=64 * 1024, max_rows=100)


def test_summary_counts_groups_beyond_preview_without_losing_retrieval():
    request = {"patch": "+x", "section": "summary", "cursor": None, "page_size": 2}
    groups = {name: [] for name in impact_report.PATCH_SECTIONS}
    groups["uncertainties"] = [{"reason": f"reason {i}", "phase": "after", "location": {"line": i + 1}}
                               for i in range(9)]
    packet = impact_report.build_patch_packet(request, identity={"test_sources": []}, groups=groups,
                                              limitations=[], max_bytes=64 * 1024, max_rows=100)
    summary = packet["summary"]
    assert summary["uncertainty_group_count"] == 9
    assert summary["omitted_uncertainty_groups"] == 3
    assert summary["omitted_uncertainty_occurrences"] == 3
    assert sum(row["count"] for row in summary["uncertainty_groups"] +
               summary["other_uncertainty_group_counts"]) == 9
    assert packet["retrieval"]["uncertainties"]["logical_items"] == 9
