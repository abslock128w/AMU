import json
from pathlib import Path

from amu import state
from amu.classify import build_map

IMP = json.loads((Path(__file__).parent / "fixtures" / "impact.json").read_text())


def _map(change="signature", imp=None, caps=None):
    return build_map(
        "amu/classify.py", ["classify_consumers"],
        {"classify_consumers": IMP if imp is None else imp}, change, {}, caps or {}, 2,
    )


def test_sound_needs_resolved_edge():
    m = _map()
    assert all(n["confidence"] == "sound" for n in m["nodes"] if n["evidence"][0].startswith("impact:callers"))


def test_every_non_sound_node_has_reason_and_verify():
    m = _map(imp={"error": "boom"})
    m["map_hash"] = "x"
    state.validate("map", m)
    assert all(n["reason"] and n["verify"] for n in m["nodes"] if n["confidence"] != "sound")


def test_failed_impact_is_unknown_not_dropped():
    m = _map(imp={"error": "unresolved"})
    assert m["resolution"]["unknown"] == 1 and "unresolved" in m["nodes"][0]["reason"]


def test_syntax_error_file_in_root_package_becomes_unknown_node():
    imp = {**IMP, "partial_failures": [{"code": "E_PARSE_ERROR", "file_path": "amu/broken.py", "effect_on_semantic_completeness": "incomplete"},
                                       {"code": "E_UNSUPPORTED_LANGUAGE", "file_path": "doc/x.f90", "effect_on_semantic_completeness": "omitted"}]}
    m = _map(imp=imp)
    assert any(n["confidence"] == "unknown" and n["path"] == "amu/broken.py" for n in m["nodes"])
    assert not any(n["path"] == "doc/x.f90" for n in m["nodes"])
    assert "2 files unparsed repo-wide" in m["resolution"]["qualifier"]


def test_ambiguous_symbol_is_unknown_not_silent():
    imp = {**IMP, "callers": {"total": 0, "entries": []}, "data_flows": {"total": 0, "entries": None},
           "disambiguation_required": True, "definitions": [{"file_path": "a.py"}, {"file_path": "b.py"}]}
    m = _map(imp=imp)
    assert m["resolution"]["unknown"] == 1 and "ambiguous: 2 definitions" in m["nodes"][0]["reason"]


def test_zero_unknown_line_carries_qualifier():
    m = _map()
    assert m["resolution"]["unknown"] == 0 and "not counted" in m["resolution"]["qualifier"]


def test_capability_limits_are_explicit_without_inventing_an_unknown_consumer():
    m = _map(caps={"unresolved_patterns": ["reflection", "dynamic_dispatch", "reflection"]})

    assert m["resolution"]["unknown"] == 0
    assert m["resolution"]["limitations"] == [
        {
            "kind": "unresolved_pattern",
            "pattern": "dynamic_dispatch",
            "reason": "graph capability reports this unresolved pattern; no concrete affected callsite was returned",
            "evidence": "capability:unresolved_patterns:dynamic_dispatch",
        },
        {
            "kind": "unresolved_pattern",
            "pattern": "reflection",
            "reason": "graph capability reports this unresolved pattern; no concrete affected callsite was returned",
            "evidence": "capability:unresolved_patterns:reflection",
        },
    ]
    assert "capability limits: dynamic_dispatch, reflection" in m["resolution"]["qualifier"]


def test_ripples_preserve_only_graph_backed_evidence_in_stable_order():
    first = {
        "callers": {"entries": [
            {"endpoint": {"file_path": "amu/z.py", "qualified_name": "zeta"}, "relation": "CALLS",
             "direction": "in", "depth": 2, "call_site": {"file_path": "amu/z.py", "line": 22}},
            {"endpoint": {"file_path": "amu/a.py", "qualified_name": "alpha"}, "relation": "CALLS",
             "direction": "in", "depth": 1, "call_site": {"file_path": "amu/a.py", "line": 10}},
        ]},
        "data_flows": {"entries": [
            {"endpoint": {"file_path": "amu/a.py", "name": "alpha"}, "relation": "DATA_FLOWS",
             "direction": "in", "depth": 1},
        ]},
        "co_changes": {"entries": [{"file_path": "tests/test_a.py", "name": "test_alpha"}]},
    }
    second = {
        **first,
        "callers": {"entries": list(reversed(first["callers"]["entries"]))},
    }

    ripples = _map(imp=first)["ripples"]
    assert ripples == _map(imp=second)["ripples"]
    assert [(r["confidence"], r["evidence"][0], r["endpoint"]["path"]) for r in ripples] == [
        ("sound", "impact:callers", "amu/a.py"),
        ("sound", "impact:callers", "amu/z.py"),
        ("guessed", "impact:co_changes", "tests/test_a.py"),
        ("sound", "impact:data_flows", "amu/a.py"),
    ]
    caller = ripples[0]
    assert caller["source"] == {"path": "amu/classify.py", "symbol": "classify_consumers"}
    assert caller["relation"] == "CALLS"
    assert caller["direction"] == "in"
    assert caller["depth"] == 1
    assert caller["via"] == "amu/a.py:10"


def test_ripples_do_not_make_up_edges_for_missing_endpoints_or_failed_impacts():
    imp = {
        "callers": {"entries": [
            {"endpoint": {}},
            {"endpoint": {"file_path": "amu/real.py", "name": "real"}, "relation": "CALLS", "depth": 1},
        ]},
        "callees": {"entries": [None, {"endpoint": "not-an-object"}]},
    }
    m = _map(imp=imp)
    assert len(m["ripples"]) == 1
    assert m["ripples"][0]["endpoint"] == {"path": "amu/real.py", "symbol": "real"}
    assert _map(imp={"error": "no graph"})["ripples"] == []


def test_body_change_never_will_break():
    assert all(n["risk"] != "will_break" for n in _map("body")["nodes"])


def test_next_is_runnable():
    assert _map()["next"].startswith("amu plan --targets")
