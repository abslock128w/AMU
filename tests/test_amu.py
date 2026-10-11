"""
tests/test_amu.py
-----------------
Final integration-level tests for the amu submission.

Covers:
  1. classify_consumers → unknown bucket routing for dynamic dispatch / partial patterns
  2. graph.py fallback parser safety with unformatted / garbage text
"""

from amu.classify import classify_consumers
from amu.graph import capabilities, impact

# ===========================================================================
# 1. Unknown bucket: dynamic dispatch & partial analysis routing
# ===========================================================================

class TestUnknownBucketRouting:
    """
    Verify that patterns the entire graph engine cannot statically resolve
    are always routed to the `unknown` bucket rather than silently dropped
    or misclassified into will_break / might_break.
    """

    BASE_CAPS = {
        "out_of_coverage": [],
        "unresolved_patterns": ["dynamic_dispatch", "reflection"],
    }

    # -- dynamic dispatch patterns ------------------------------------------

    def test_dynamic_dispatch_line_increments_unresolved(self):
        raw = "dynamic dispatch found at callsite: module.Handler"
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] >= 1

    def test_multiple_dynamic_lines_all_counted(self):
        raw = (
            "dynamic dispatch: obj.do_something\n"
            "dynamic dispatch: factory.create\n"
            "unresolved reference to: plugin.execute"
        )
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] >= 3

    def test_dynamic_dispatch_not_in_will_break(self):
        raw = "dynamic dispatch: some.Symbol"
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["will_break"] == []

    def test_dynamic_dispatch_not_in_might_break(self):
        raw = "dynamic dispatch: some.Symbol"
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["might_break"] == []

    # -- partial analysis / unresolved patterns -----------------------------

    def test_unresolved_reference_routed_to_unknown(self):
        raw = "unresolved call to external.dependency"
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] >= 1

    def test_partial_analysis_notes_always_present(self):
        """Unknown bucket must always carry a human-readable explanation."""
        buckets = classify_consumers("target.fn", "dynamic dispatch here", self.BASE_CAPS)
        assert isinstance(buckets["unknown"]["notes"], list)
        assert len(buckets["unknown"]["notes"]) > 0

    def test_out_of_coverage_modules_propagated_from_caps(self):
        caps = {
            "out_of_coverage": ["legacy.module", "vendored.lib"],
            "unresolved_patterns": ["dynamic_dispatch"],
        }
        buckets = classify_consumers("target.fn", "", caps)
        assert "legacy.module" in buckets["unknown"]["out_of_coverage"]
        assert "vendored.lib" in buckets["unknown"]["out_of_coverage"]

    def test_mixed_output_routes_correctly(self):
        """Ensure direct/transitive AND dynamic lines are each routed correctly."""
        raw = (
            "hop: 1 module.DirectCaller\n"
            "hop: 2 module.TransitiveCaller\n"
            "dynamic dispatch: some.DynamicSite\n"
            "unresolved: plugin.X"
        )
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["will_break"] == []
        assert len(buckets["might_break"]) >= 1
        assert buckets["unknown"]["unresolved_callsites"] >= 2

    def test_unknown_bucket_zero_counts_on_clean_resolved_output(self):
        """No dynamic/unresolved lines → unresolved counter stays at 0."""
        raw = "hop: 1 module.A\nhop: 2 module.B"
        buckets = classify_consumers("target.fn", raw, self.BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] == 0

    def test_unknown_bucket_structure_invariant(self):
        """Schema must be stable regardless of input."""
        for raw in ["", "garbage", "dynamic dispatch here", "hop: 1 X"]:
            buckets = classify_consumers("sym", raw, self.BASE_CAPS)
            u = buckets["unknown"]
            assert "unresolved_callsites"   in u
            assert "dynamic_dispatch_sites" in u
            assert "out_of_coverage"        in u
            assert "notes"                  in u


# ===========================================================================
# 2. graph.py fallback parser: must not crash on unformatted / garbage text
# ===========================================================================

class TestGraphFallbackSafety:
    """
    impact() and capabilities() both shell out to the `entire` CLI.
    When the CLI is absent the subprocess returns a non-zero exit code.
    These tests verify the fallback paths don't raise exceptions and return
    sensible defaults that downstream consumers can safely process.
    """

    # -- impact() fallback --------------------------------------------------

    def test_impact_returns_string_when_cli_missing(self):
        """impact() must always return a str, even when entire is not installed."""
        result = impact("any.symbol", repo=".")
        assert isinstance(result, str)

    def test_impact_error_string_does_not_raise(self):
        """The error string returned by impact() must be parseable by classify_consumers."""
        raw = impact("any.symbol", repo=".")
        caps = {"out_of_coverage": [], "unresolved_patterns": []}
        buckets = classify_consumers("any.symbol", raw, caps)
        assert isinstance(buckets, dict)

    def test_impact_with_nonexistent_repo_does_not_raise(self):
        result = impact("any.symbol", repo="/nonexistent/path/xyz")
        assert isinstance(result, str)

    def test_impact_with_garbage_symbol_does_not_raise(self):
        result = impact("!!!@@@ BAD SYMBOL <><> ???", repo=".")
        assert isinstance(result, str)

    def test_impact_multiline_unformatted_output_safe(self):
        """Simulate what downstream sees if entire emits unformatted logs."""
        unformatted = (
            "[WARN] parser initialising...\n"
            "  tree-sitter grammar loaded\n"
            "??unknown token??\n"
            "\t\tindented garbage\n"
            "EOF"
        )
        caps = {"out_of_coverage": [], "unresolved_patterns": []}
        # Must not raise
        buckets = classify_consumers("sym", unformatted, caps)
        assert isinstance(buckets, dict)

    # -- capabilities() fallback --------------------------------------------

    def test_capabilities_returns_dict_when_cli_missing(self):
        """capabilities() must always return a dict with the expected fallback keys."""
        result = capabilities(repo=".")
        assert isinstance(result, dict)

    def test_capabilities_fallback_has_out_of_coverage_key(self):
        result = capabilities(repo=".")
        assert "out_of_coverage" in result

    def test_capabilities_fallback_has_unresolved_patterns_key(self):
        result = capabilities(repo=".")
        assert "unresolved_patterns" in result

    def test_capabilities_fallback_unresolved_patterns_nonempty(self):
        """Fallback must list known unresolvable patterns so classify knows what to flag."""
        result = capabilities(repo=".")
        assert len(result["unresolved_patterns"]) > 0

    def test_capabilities_with_nonexistent_repo_does_not_raise(self):
        result = capabilities(repo="/nonexistent/path/xyz")
        assert isinstance(result, dict)

    def test_capabilities_result_feeds_classify_without_error(self):
        """End-to-end: capabilities() output must be directly usable by classify_consumers."""
        caps = capabilities(repo=".")
        buckets = classify_consumers("sym", "dynamic dispatch: foo", caps)
        assert "unknown" in buckets
