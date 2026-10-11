from amu.plan import plan_from_map


def make_map(edges, *, depth=1, confidence="sound"):
    names = sorted({n for pair in edges for n in pair})
    baselines = {f"{n}.py#{n}": [] for n in names}
    for caller, callee in edges:
        baselines[f"{callee}.py#{callee}"].append({"target": f"{callee}.py#{callee}",
            "related": f"{caller}.py#{caller}", "direction": "in", "relation": "CALLS",
            "depth": depth, "confidence": confidence, "provenance": "parser"})
    return {"root": {"file": "root.py"}, "graph_evidence": {"edge_baselines": baselines},
            "nodes": [{"id": n, "path": f"{n}.py", "symbol": n, "confidence": "sound", "order": 1,
                       "risk": "might_break", "via": "CALLS", "reason": "call", "verify": "test"} for n in names]}


def plan(edges, **kwargs):
    return plan_from_map(make_map(edges, **kwargs), [{"symbol": "root.py#root", "change_class": "body"}])


def test_chain_uses_dependencies_not_distance():
    phases = plan([("c", "b"), ("b", "a")])
    assert phases[1]["nodes"] == ["c"]
    assert phases[2]["nodes"] == ["b", "a"]


def test_diamond_keeps_both_consumers_before_shared_dependency():
    phases = plan([("d", "b"), ("d", "c"), ("b", "a"), ("c", "a")])
    assert phases[1]["nodes"] == ["d"]
    assert phases[2]["nodes"] == ["b", "c", "a"]


def test_cycle_is_a_group_not_an_arbitrary_linear_claim():
    phases = plan([("a", "b"), ("b", "a"), ("c", "b")])
    assert phases[1]["nodes"] == ["c"]
    assert phases[2]["ordering"]["cycles"] == [["a.py#a", "b.py#b"]]


def test_distance_and_guesses_do_not_create_dependency_constraints():
    for kwargs in ({"depth": 2}, {"confidence": "guessed"}):
        phases = plan([("b", "a")], **kwargs)
        assert phases[2]["ordering"]["edge_count"] == 0
        assert phases[2]["ordering"]["warnings"]


def test_missing_individual_caller_coverage_cannot_claim_leaf():
    mapped = make_map([("b", "a")])
    del mapped["graph_evidence"]["edge_baselines"]["b.py#b"]
    phases = plan_from_map(mapped, [{"symbol": "root.py#root", "change_class": "body"}])
    assert not phases[1]["nodes"]
    assert phases[2]["nodes"] == ["b", "a"]
    assert phases[2]["ordering"]["missing_coverage"] == ["b.py#b"]
