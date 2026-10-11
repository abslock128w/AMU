"""Exact application, source/test bindings and shared bounded evidence retrieval."""

import difflib
import json
from dataclasses import replace

import pytest

from amu.patch import MAX_PATCH_BYTES, PatchError, apply_file, parse_diff
from amu.services import impact, impact_report


def diff(before, after, path="cache.py"):
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                       fromfile="a/" + path, tofile="b/" + path))


def test_multifile_application_is_exact_and_preserves_unicode():
    before = "def f():\n    return 'α'\n"
    after = "def f():\n    return 'β'\n"
    patch = diff(before, after) + diff("x = 1\n", "x = 2\n", "other.py")
    files = parse_diff(patch)
    assert len(files) == 2
    assert apply_file(files[0], before) == after
    assert apply_file(files[1], "x = 1\n") == "x = 2\n"
    with pytest.raises(PatchError, match="context"):
        apply_file(files[0], before.replace("α", "γ"))


def test_zero_context_and_explicit_no_final_newline():
    patch = "--- a/cache.py\n+++ b/cache.py\n@@ -1 +1 @@\n-x = 1\n\\ No newline at end of file\n+x = 2\n\\ No newline at end of file\n"
    assert apply_file(parse_diff(patch)[0], "x = 1") == "x = 2"
    insertion = "--- a/cache.py\n+++ b/cache.py\n@@ -1,0 +2 @@\n+y = 2\n"
    assert apply_file(parse_diff(insertion)[0], "x = 1\n") == "x = 1\ny = 2\n"


def test_add_and_delete():
    added = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    file = parse_diff(added)[0]
    assert apply_file(file, None) == "x = 1\n"
    with pytest.raises(PatchError, match="absent"):
        apply_file(file, "")
    removed = "--- a/new.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    assert apply_file(parse_diff(removed)[0], "x = 1\n") is None


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", ".git/config.py", ".amu/state.py", ".entire/cache.py", "a/../b.py", "a\\b.py", "file.txt"])
def test_unsafe_and_unsupported_paths(path):
    with pytest.raises(PatchError):
        parse_diff(diff("x=1\n", "x=2\n", path))


@pytest.mark.parametrize("mutation", [
    lambda p: p.replace("@@ -1 +1 @@", "@@ -1,2 +1 @@"),
    lambda p: p.replace("@@ -1 +1 @@", "@@ -0 +1 @@"),
    lambda p: p + p,
    lambda p: p.replace("+++ b/cache.py", "+++ b/renamed.py"),
    lambda p: "diff --git a/cache.py b/cache.py\nold mode 100644\nnew mode 100755\n" + p,
    lambda p: "GIT binary patch\n" + p,
    lambda p: p.replace("\n", "\r\n"),
    lambda p: p.replace("-x=1\n", "-x=1"),
])
def test_ambiguous_forms_rejected(mutation):
    with pytest.raises(PatchError):
        parse_diff(mutation(diff("x=1\n", "x=2\n")))


def test_overlapping_and_incorrect_new_positions_rejected():
    base = "x=1\ny=2\n"
    overlap = "--- a/cache.py\n+++ b/cache.py\n@@ -1 +1 @@\n-x=1\n+x=2\n@@ -1 +2 @@\n-x=1\n+x=3\n"
    with pytest.raises(PatchError, match="overlap"):
        apply_file(parse_diff(overlap)[0], base)
    wrong = diff(base, "x=3\ny=2\n").replace("+1,2", "+2,2")
    with pytest.raises(PatchError, match="position"):
        apply_file(parse_diff(wrong)[0], base)


def test_limits_and_scalar_validation_before_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem touched")
    monkeypatch.setattr(impact, "_repo", forbidden)
    valid = impact.PatchRequest(diff("x=1\n", "x=2\n"), base="worktree")
    invalid = [replace(valid, patch="x" * (MAX_PATCH_BYTES + 1)), replace(valid, depth=True),
               replace(valid, include_graph=1), replace(valid, page_size=True), replace(valid, section=[]),
               replace(valid, cursor="invalid"), replace(valid, base="HEAD~1")]
    for request in invalid:
        with pytest.raises(impact.ImpactInputError):
            impact.execute_patch("/no/repo", request)


def test_static_report_bound_to_real_test_source_and_exact_candidate(tmp_path):
    before = "class Cache:\n    _cache = {}\n    @classmethod\n    def lookup(cls, value):\n        return cls._cache.get(value)\n"
    after = before.replace("_cache = {}", "_cache = None")
    source = tmp_path / "cache.py"
    source.write_text(before)
    tests = tmp_path / "test_cache.py"
    tests.write_text("from cache import Cache\n\ndef test_lookup():\n    assert Cache.lookup(1) is None\n")
    request = impact.PatchRequest(diff(before, after), base="worktree", section="recommended_checks")
    report = impact.execute_patch(str(tmp_path), request)
    assert source.read_text() == before
    assert not (tmp_path / ".amu").exists()
    assert report["assessment"]["executed"] is False
    assert report["assessment"]["regression_proven"] is False
    assert report["request"]["patch_sha256"]
    assert "patch" not in report["request"]
    assert report["details"]["items"][0]["test_id"] == "test_cache.py::test_lookup"
    assert report["identity"]["files"][0]["baseline_sha256"] != report["identity"]["files"][0]["candidate_sha256"]
    assert report["identity"]["test_sources"][0]["sha256"]
    cursor = report["retrieval"]["findings"]["cursor"]
    old = impact.execute_patch(str(tmp_path), replace(request, section="findings", cursor=cursor))
    assert old["report_hash"] == report["report_hash"]
    tests.write_text(tests.read_text() + "\n# evidence changed\n")
    with pytest.raises(impact.ImpactInputError, match="stale"):
        impact.execute_patch(str(tmp_path), replace(request, section="findings", cursor=cursor))


def test_reject_symlink_and_stale_base(tmp_path):
    source = tmp_path / "cache.py"
    source.symlink_to(tmp_path / "missing")
    request = impact.PatchRequest(diff("x=1\n", "x=2\n"), base="worktree")
    with pytest.raises(impact.ImpactInputError, match="symbolic"):
        impact.execute_patch(str(tmp_path), request)
    with pytest.raises(impact.ImpactInputError, match="current Git HEAD"):
        impact.execute_patch(str(tmp_path), replace(request, base="f" * 40))


def test_patch_paging_lossless_and_protocol_bounded():
    request = {"patch": "+x", "base": "worktree", "depth": 1, "include_graph": False,
               "section": "findings", "cursor": None, "page_size": 3}
    huge = {"source": "α" * 40000}
    groups = {name: [] for name in impact_report.PATCH_SECTIONS}
    groups["findings"] = [huge, {"source": "last"}]
    fragments, pages = [], 0
    while True:
        report = impact_report.build_patch_packet(request, identity={"source": "hash"}, groups=groups,
                                                  limitations=[], max_bytes=64 * 1024, max_rows=100)
        assert len(json.dumps(report).encode()) < 64 * 1024
        fragments.extend(report["details"]["items"])
        pages += 1
        if report["page"]["next_cursor"] is None:
            break
        request["cursor"] = report["page"]["next_cursor"]
    assert pages > 1
    import base64
    body = b"".join(base64.b64decode(row["evidence_fragment"]["data"]) for row in fragments[:-1])
    assert json.loads(body) == huge
    assert fragments[-1] == {"source": "last"}
    with pytest.raises(impact_report.ReportError):
        impact_report.decode_cursor(report["retrieval"]["findings"]["cursor"])


def test_test_selection_uses_declarations_and_unshadowed_source_references():
    import ast

    from amu.plan import patch_test_recommendations

    text = ("from cache import Cache as Alias\n\n"
            "def test_selected():\n    assert Alias.lookup(1)\n\n"
            "def test_shadowed(Alias):\n    assert Alias.lookup(1)\n\n"
            "def test_unrelated():\n    assert 1\n\n"
            "class Helper:\n    def test_not_collected(self):\n        assert Alias.lookup(1)\n")
    result = patch_test_recommendations([{"path": "tests/test_cache.py", "tree": ast.parse(text), "sha256": "0" * 64}],
                                        {"cache.py": {"Cache.lookup"}})
    assert [row["test_id"] for row in result] == ["tests/test_cache.py::test_selected"]
    assert result[0]["coverage_proven"] is False
    assert result[0]["entities"] == ["cache.py#Cache.lookup"]


def test_empty_and_hostile_hunk_numbers_rejected():
    for patch in ("--- a/cache.py\n+++ b/cache.py\n@@ -1,0 +1,0 @@\n",
                  "--- a/cache.py\n+++ b/cache.py\n@@ -" + "9" * 5000 + " +1 @@\n-x\n+y\n"):
        with pytest.raises(PatchError):
            parse_diff(patch)


def test_source_and_test_inventory_races_fail_closed(tmp_path, monkeypatch):
    before, after = "def f():\n    return 1\n", "def f():\n    return 2\n"
    source = tmp_path / "cache.py"
    source.write_text(before)
    request = impact.PatchRequest(diff(before, after), base="worktree")
    original = impact._source
    calls = 0

    def racing(root, relative, **kwargs):
        nonlocal calls
        result = original(root, relative, **kwargs)
        if relative == "cache.py":
            calls += 1
            if calls == 1:
                source.write_text(after)
        return result
    monkeypatch.setattr(impact, "_source", racing)
    with pytest.raises(impact.ImpactInputError, match="changed during inspection"):
        impact.execute_patch(str(tmp_path), request)
    monkeypatch.setattr(impact, "_source", original)
    source.write_text(before)
    inventory = impact._test_inventory
    scans = 0

    def adding_test(root):
        nonlocal scans
        scans += 1
        if scans == 2:
            (tmp_path / "test_new.py").write_text("def test_new():\n    assert True\n")
        return inventory(root)
    monkeypatch.setattr(impact, "_test_inventory", adding_test)
    with pytest.raises(impact.ImpactInputError, match="test inventory changed"):
        impact.execute_patch(str(tmp_path), request)


def test_encoding_cookie_addition_symlinks_and_no_graph_default(tmp_path, monkeypatch):
    before = "# coding: latin-1\nx = 'é'\n"
    (tmp_path / "cache.py").write_text(before)
    with pytest.raises(impact.ImpactInputError, match="UTF-8 Python"):
        impact.execute_patch(str(tmp_path), impact.PatchRequest(diff(before, before.replace("é", "è")), base="worktree"))
    added = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    (tmp_path / "new.py").symlink_to(tmp_path / "missing")
    with pytest.raises(impact.ImpactInputError, match="already exists"):
        impact.execute_patch(str(tmp_path), impact.PatchRequest(added, base="worktree"))
    (tmp_path / "new.py").unlink()
    (tmp_path / "cache.py").unlink()
    monkeypatch.setattr(impact.mapping, "inspect", lambda *a, **k: pytest.fail("provider unexpectedly queried"))
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(added, base="worktree"))
    assert report["identity"]["files"][0]["baseline_sha256"] is None
    assert not (tmp_path / "new.py").exists()


def test_optional_graph_timestamp_does_not_stale_cursor(tmp_path, monkeypatch):
    before, after = "def f():\n    return 1\n", "def f():\n    return 2\n"
    (tmp_path / "cache.py").write_text(before)
    at = ["one"]
    monkeypatch.setattr(impact.mapping, "worktree_fingerprint", lambda *a, **k: {"head": "no-git"})
    monkeypatch.setattr(impact.graph_refresh, "relation_capture_matches", lambda *a: True)
    monkeypatch.setattr(impact.mapping, "inspect", lambda *a, **k: {
        "map_hash": "fixed", "graph_evidence": {"at": at[0], "approvable": False}, "nodes": [], "resolution": {}})
    request = impact.PatchRequest(diff(before, after), base="worktree", include_graph=True)
    report = impact.execute_patch(str(tmp_path), request)
    at[0] = "two"
    second = impact.execute_patch(str(tmp_path), replace(request, section="findings", cursor=report["retrieval"]["findings"]["cursor"]))
    assert second["report_hash"] == report["report_hash"]
