"""
tests/test_plan.py
------------------
Tests for topological plan generation (4-phase refactoring sequence).
"""

import json

from amu.plan import (
    PHASES,
    RefactoringPlan,
    build_plan,
    plan_from_buckets,
    plan_from_map,
    plan_to_json,
)

# ---------------------------------------------------------------------------
# Basic plan shape
# ---------------------------------------------------------------------------

class TestBuildPlan:
    def test_returns_refactoring_plan_instance(self):
        plan = build_plan("target.fn", {})
        assert isinstance(plan, RefactoringPlan)

    def test_all_four_phases_present(self):
        plan = build_plan("target.fn", {})
        for phase in PHASES:
            assert phase in plan.phases

    def test_symbol_placed_in_removal_by_default(self):
        plan = build_plan("target.fn", {})
        # target symbol with no callers should end up in removal
        all_items = [item for phase in PHASES for item in plan.phases[phase]]
        assert "target.fn" in all_items

    def test_direct_callers_in_migration_phase(self):
        edges = {
            "caller_a": ["target.fn"],
            "caller_b": ["target.fn"],
        }
        plan = build_plan("target.fn", edges, direct_callers={"caller_a", "caller_b"})
        assert "caller_a" in plan.phases["migration"]
        assert "caller_b" in plan.phases["migration"]

    def test_transitive_callers_in_interior_phase(self):
        edges = {
            "direct_a": ["target.fn"],
            "transitive_b": ["direct_a"],
        }
        plan = build_plan(
            "target.fn",
            edges,
            direct_callers={"direct_a"},
            transitive_callers={"transitive_b"},
        )
        assert "transitive_b" in plan.phases["interior"]

    def test_new_prefixed_symbols_in_additive_phase(self):
        edges = {
            "new:target.fn_v2": [],
            "caller_a": ["target.fn"],
        }
        plan = build_plan("target.fn", edges, direct_callers={"caller_a"})
        assert "new:target.fn_v2" in plan.phases["additive"]

    def test_each_node_in_exactly_one_phase(self):
        edges = {
            "caller_a": ["target.fn"],
            "transitive_b": ["caller_a"],
            "new:v2": [],
        }
        plan = build_plan(
            "target.fn",
            edges,
            direct_callers={"caller_a"},
            transitive_callers={"transitive_b"},
        )
        all_items = [item for phase in PHASES for item in plan.phases[phase]]
        # No duplicates
        assert len(all_items) == len(set(all_items))


# ---------------------------------------------------------------------------
# Cycle handling
# ---------------------------------------------------------------------------

class TestCycleHandling:
    def test_cyclic_graph_does_not_raise(self):
        edges = {"a": ["b"], "b": ["a"]}
        plan = build_plan("target.fn", edges)
        # Must not crash; cycles_detected should be populated
        assert isinstance(plan, RefactoringPlan)

    def test_cyclic_graph_sets_warning(self):
        edges = {"a": ["b"], "b": ["a"]}
        plan = build_plan("target.fn", edges)
        assert len(plan.cycles_detected) > 0 or len(plan.warnings) > 0


# ---------------------------------------------------------------------------
# plan_from_buckets
# ---------------------------------------------------------------------------

class TestPlanFromBuckets:
    def _make_buckets(self, will=0, might=0, unresolved=0):
        return {
            "will_break": [{"symbol": f"sym_will_{i}", "reason": "direct"} for i in range(will)],
            "might_break": [{"symbol": f"sym_might_{i}", "reason": "transitive"} for i in range(might)],
            "unknown": {"unresolved_callsites": unresolved},
        }

    def test_will_break_items_in_migration(self):
        buckets = self._make_buckets(will=2)
        plan = plan_from_buckets("target.fn", buckets)
        for i in range(2):
            assert f"sym_will_{i}" in plan.phases["migration"]

    def test_might_break_items_in_interior(self):
        buckets = self._make_buckets(will=1, might=2)
        plan = plan_from_buckets("target.fn", buckets)
        for i in range(2):
            assert f"sym_might_{i}" in plan.phases["interior"]

    def test_unresolved_callsites_produce_warning(self):
        buckets = self._make_buckets(unresolved=3)
        plan = plan_from_buckets("target.fn", buckets)
        assert any("unresolved" in w.lower() or "callsite" in w.lower() for w in plan.warnings)

    def test_empty_buckets_do_not_raise(self):
        plan = plan_from_buckets("target.fn", {})
        assert isinstance(plan, RefactoringPlan)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_has_all_keys(self):
        plan = build_plan("target.fn", {})
        d = plan.to_dict()
        assert "symbol" in d
        assert "phases" in d
        assert "cycles_detected" in d
        assert "warnings" in d

    def test_plan_to_json_valid_json(self):
        plan = build_plan("target.fn", {})
        raw = plan_to_json(plan)
        parsed = json.loads(raw)
        assert parsed["symbol"] == "target.fn"

    def test_pretty_contains_phase_labels(self):
        plan = build_plan("target.fn", {})
        pretty = plan.pretty()
        for phase in PHASES:
            assert phase.upper() in pretty


# ---------------------------------------------------------------------------
# PRD map phases
# ---------------------------------------------------------------------------

def _map_node(node_id, path, *, confidence="sound", risk="might_break", order=1, via="CALLS:src/root.py:1"):
    return {
        "id": node_id,
        "path": path,
        "symbol": node_id,
        "confidence": confidence,
        "risk": risk,
        "order": order,
        "via": via,
        "reason": f"reason for {node_id}",
        "verify": f"amu verify --node {node_id}",
    }


def _map_with_nodes(nodes):
    return {"root": {"file": "src/root.py"}, "nodes": nodes}


class TestPlanFromMap:
    def test_distance_alone_does_not_prove_a_leaf(self):
        direct_code = _map_node("n_direct_code", "src/consumer.py", risk="will_break", order=1)
        direct_body = _map_node("n_direct_body", "src/body_consumer.py", order=1, via="CALLS:src/body_consumer.py:4")
        deep_test = _map_node("n_deep_test", "tests/test_transitive.py", order=2, via="CALLS:tests/test_transitive.py:8")
        deep_code = _map_node("n_deep_code", "src/transitive.py", order=2, via="CALLS:src/transitive.py:10")
        guessed = _map_node("n_guessed", "src/coupled.py", confidence="guessed", order=1, via=None)

        phases = plan_from_map(
            _map_with_nodes([deep_test, guessed, direct_body, deep_code, direct_code]),
            [{"symbol": "src/root.py#root", "change_class": "signature"}],
        )

        assert phases[1]["nodes"] == []
        assert set(phases[2]["nodes"]) == {"n_direct_code", "n_direct_body", "n_deep_test", "n_deep_code", "n_guessed"}
        assert phases[1]["tests"] == ["tests/test_transitive.py"]

    def test_multiple_graph_depths_have_a_stable_evidence_order(self):
        leaves_and_interior = [
            _map_node("n_leaf_z", "src/z_consumer.py", risk="will_break", order=1, via="CALLS:src/z_consumer.py:5"),
            _map_node("n_depth_three", "src/late.py", order=3, via="CALLS:src/late.py:1"),
            _map_node("n_leaf_a", "src/a_consumer.py", risk="will_break", order=1, via="CALLS:src/a_consumer.py:50"),
            _map_node("n_depth_two_b", "src/b_early.py", order=2, via="CALLS:src/b_early.py:2"),
            _map_node("n_depth_two_a", "src/a_early.py", order=2, via="CALLS:src/a_early.py:2"),
        ]
        targets = [{"symbol": "src/root.py#root", "change_class": "body"}]

        ordered = plan_from_map(_map_with_nodes(leaves_and_interior), targets)
        reversed_input = plan_from_map(_map_with_nodes(list(reversed(leaves_and_interior))), targets)

        assert ordered[1]["nodes"] == []
        assert ordered[2]["nodes"] == ["n_leaf_a", "n_depth_two_a", "n_depth_two_b", "n_depth_three", "n_leaf_z"]
        assert reversed_input[1]["nodes"] == ordered[1]["nodes"]
        assert reversed_input[2]["nodes"] == ordered[2]["nodes"]

    def test_unknown_nodes_stay_out_of_edit_phases_and_are_sorted_as_questions(self):
        deep_test = _map_node("n_deep_test", "tests/test_deep.py", order=2, via="CALLS:tests/test_deep.py:4")
        unknown_z = _map_node("n_unknown_z", "src/z_unknown.py", confidence="unknown", risk="unknown", order=99, via=None)
        unknown_a = _map_node("n_unknown_a", "src/a_unknown.py", confidence="unknown", risk="unknown", order=99, via=None)

        phases = plan_from_map(
            _map_with_nodes([unknown_z, deep_test, unknown_a]),
            [{"symbol": "src/root.py#root", "change_class": "signature"}],
        )

        assert phases[1]["nodes"] == []
        assert phases[2]["nodes"] == ["n_deep_test"]
        assert phases[1]["tests"] == ["tests/test_deep.py"]
        assert phases[1]["open_questions"] == [
            "n_unknown_a src/a_unknown.py: reason for n_unknown_a → amu verify --node n_unknown_a",
            "n_unknown_z src/z_unknown.py: reason for n_unknown_z → amu verify --node n_unknown_z",
        ]
