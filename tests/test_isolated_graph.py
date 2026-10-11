import copy

import pytest

from amu.graph_readiness import live_map_evidence


def isolated():
    return {"focus": {"id": "f", "name": "f", "qualified_name": "f", "file_path": "a.py"},
            "completeness_scope": {"level": "complete"},
            **{key: {"total": 0, "entries": []} for key in
               ("callers", "callees", "type_consumers", "data_flows")}}


def test_complete_exact_isolated_target_is_not_a_broken_graph():
    report = live_map_evidence([], {"f": isolated()}, root_file="a.py")
    assert report["approvable"]
    assert report["isolated_targets"] == ["a.py#f"]


@pytest.mark.parametrize("change", ["unknown_coverage", "missing_section", "truncated", "wrong_focus", "partial_failure"])
def test_empty_graph_needs_positive_target_coverage(change):
    raw = copy.deepcopy(isolated())
    if change == "unknown_coverage":
        raw.pop("completeness_scope")
    elif change == "missing_section":
        raw.pop("data_flows")
    elif change == "truncated":
        raw["callers"]["total"] = 1
    elif change == "wrong_focus":
        raw["focus"]["file_path"] = "other.py"
    else:
        raw["partial_failures"] = [{"file_path": "a.py", "code": "parse_error"}]
    assert not live_map_evidence([], {"f": raw}, root_file="a.py")["approvable"]


def test_nonempty_neighbor_does_not_hide_an_uncovered_isolated_target():
    raw = isolated()
    other = isolated()
    other.pop("completeness_scope")
    raw["callers"] = {"total": 1, "entries": [{"endpoint": {"name": "g", "file_path": "b.py"}, "relation": "CALLS", "depth": 1}]}
    assert not live_map_evidence([], {"f": raw, "g": other}, root_file="a.py")["approvable"]
