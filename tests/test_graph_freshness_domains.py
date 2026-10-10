"""Versioned graph and documentation evidence keep their own revision identity."""

from copy import deepcopy

import pytest

from amu import docgraph, docs, graph_refresh, graphview, state, unknowns
from tests.test_docs import _committed_repo


def mapped(repo):
    capture = graph_refresh.graph_capture(str(repo))
    value = {"map_hash": "mapped", "root": {"file": "api.py"},
             "nodes": [{"id": "n1", "path": "api.py", "symbol": "f", "confidence": "unknown",
                        "reason": "review dynamic use", "verify": "test"}],
             "graph_evidence": {"map_hash": "mapped", "worktree": {
                 "stable": True, "before": capture, "after": capture, "pre_edit_graph_files": [],
             }}}
    state.write_json("map.json", value, str(repo))
    return value


def project(tmp_path):
    return _committed_repo(tmp_path, {"api.py": "def f(): return 1\n", "docs/api.md": "# API\n\nUse f.\n"})


def test_current_map_and_confirmed_code_answer_survive_prose_edit(tmp_path):
    repo = project(tmp_path)
    value = mapped(repo)
    unknowns.record_answer(str(repo), "n1", "Reviewed the current call site.", who="human")
    (repo / "docs/api.md").write_text("# API\n\nA clearer explanation.\n")
    assert graph_refresh.map_current_capture_reason(str(repo), value["graph_evidence"]) is None
    assert "n1" in unknowns.review(str(repo))["confirmed"]


def test_documentation_answer_expires_without_expiring_code_answer(tmp_path):
    repo = project(tmp_path)
    value = mapped(repo)
    value["nodes"].append({"id": "n2", "path": "docs/api.md", "symbol": None, "confidence": "unknown",
                           "reason": "review the documented example", "verify": "read docs"})
    state.write_json("map.json", value, str(repo))
    for node in ("n1", "n2"):
        unknowns.record_answer(str(repo), node, "Reviewed this question.", who="human")
    (repo / "docs/api.md").write_text("# API\n\nA different example.\n")
    review = unknowns.review(str(repo))
    assert set(review["confirmed"]) == {"n1"}
    assert "n2" in review["unanswered"]


@pytest.mark.parametrize("change", ["source", "configuration", "features"])
def test_source_or_analysis_changes_expire_current_map_and_code_answer(tmp_path, change):
    repo = project(tmp_path)
    value = mapped(repo)
    unknowns.record_answer(str(repo), "n1", "Reviewed the current call site.", who="human")
    if change == "source":
        (repo / "api.py").write_text("def f(): return 2\n")
    else:
        state.write_json("config.json" if change == "configuration" else "feature_map.json", {"changed": True}, str(repo))
    assert graph_refresh.map_current_capture_reason(str(repo), value["graph_evidence"])
    assert not unknowns.review(str(repo))["confirmed"]


def test_prose_mutation_during_graph_display_does_not_change_code_capture(tmp_path):
    repo = project(tmp_path)
    before = graphview.live_worktree_capture(str(repo))
    (repo / "docs/api.md").write_text("# API\n\nChanged prose.\n")
    after = graphview.live_worktree_capture(str(repo))
    assert before["worktree"]["digest"] != after["worktree"]["digest"]
    assert graphview._live_capture_reason(before, after) is None


def test_malformed_domain_capture_is_not_promoted_to_legacy_full_proof(tmp_path):
    repo = project(tmp_path)
    value = mapped(repo)
    for location in ("before", "after"):
        broken = deepcopy(value["graph_evidence"])
        broken["worktree"][location]["freshness"]["code"]["digest"] = None
        assert graph_refresh.map_worktree_capture_reason(broken)


def test_docs_graph_versions_link_same_code_to_updated_documentation(tmp_path):
    repo = project(tmp_path)
    first = docgraph.sync(str(repo))["graph"]
    (repo / "docs/api.md").write_text("# API\n\nA more complete explanation.\n")
    second = docgraph.sync(str(repo))["graph"]
    assert first["revisions"]["status"] == second["revisions"]["status"] == "captured"
    assert first["revisions"]["code"] == second["revisions"]["code"]
    assert first["revisions"]["docs"] != second["revisions"]["docs"]


def test_docs_listing_expires_when_doc_map_or_configuration_changes(tmp_path):
    repo = project(tmp_path)
    candidate = {"id": "d1", "candidate": "api.py#f", "targets": [{"file": "docs/api.md", "section": "# API"}]}
    for filename in ("doc-map.json", "config.json"):
        docs.issue_candidate_manifest(str(repo), [candidate])
        state.write_json(filename, {"api.py#other": [{"file": "docs/api.md", "section": "# API"}]}, str(repo))
        chosen, problem = docs.issued_candidate(str(repo), [candidate], "d1")
        assert chosen is None and "stale" in problem
