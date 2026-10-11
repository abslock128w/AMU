"""A resolved edge is not proof of an incompatible API change."""

import pytest

from amu.classify import build_map, classify_consumers
from amu.graph_refresh import live_edges
from amu.graphview import payload_from_impact


def impact(provenance=None):
    result = {"focus": {"id": "f", "file_path": "api.py", "name": "f"}}
    for section, direction in (("callers", "in"), ("callees", "out"), ("type_consumers", "in"), ("data_flows", "in")):
        entry = {"endpoint": {"id": section, "file_path": section + ".py", "name": "g"},
                 "relation": "CALLS", "direction": direction, "depth": 1}
        if provenance is not None:
            entry["provenance"] = provenance
        result[section] = {"total": 1, "entries": [entry]}
    return result


@pytest.mark.parametrize("change", ["signature", "rename", "move", "remove", "body"])
def test_graph_alone_never_proves_breakage(change):
    result = build_map("api.py", ["f"], {"f": impact()}, change, {}, {}, 2)
    assert all(n["confidence"] == "sound" for n in result["nodes"])
    assert all(n["risk"] == "might_break" and n["action"] == "review" for n in result["nodes"])
    assert all(n["compatibility"]["status"] == "not_assessed" for n in result["nodes"])


@pytest.mark.parametrize("source,expected", [(None, "unknown"), ("parser", "parser"),
    ("compiler/indexer", "compiler/indexer"), ("runtime", "runtime"), ("unsupported", "unknown")])
def test_provenance_survives_map_display_and_baseline(source, expected):
    raw = impact(source)
    mapped = build_map("api.py", ["f"], {"f": raw}, "signature", {}, {}, 2)
    displayed = payload_from_impact("f", raw)
    baseline = live_edges("api.py#f", raw)
    assert baseline.available
    for edges in (mapped["ripples"], displayed["edges"], baseline.edges):
        assert all(e["provenance"] == expected for e in edges)
        assert all(e["confidence"] == "sound" for e in edges)


def test_legacy_direct_text_is_not_breakage_proof():
    result = classify_consumers("f", "direct caller: g", {})
    assert result["will_break"] == []
    assert result["might_break"]


def test_heuristic_provenance_cannot_become_sound_baseline():
    raw = impact("heuristic")
    mapped = build_map("api.py", ["f"], {"f": raw}, "signature", {}, {}, 2)
    assert all(n["confidence"] == "guessed" for n in mapped["nodes"])
    assert all(e["provenance"] == "heuristic" for e in mapped["ripples"])
    assert not live_edges("api.py#f", raw).available


@pytest.mark.parametrize("signature", ["def f(x, optional=1)", "def f(x, required)"])
def test_signature_text_without_call_binding_is_neither_breakage_nor_safety_proof(signature):
    raw = impact()
    raw["focus"]["signature"] = signature
    mapped = build_map("api.py", ["f"], {"f": raw}, "signature", {}, {}, 2)
    assert {n["risk"] for n in mapped["nodes"]} == {"might_break"}
    assert {n["compatibility"]["status"] for n in mapped["nodes"]} == {"not_assessed"}
