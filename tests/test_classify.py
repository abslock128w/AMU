"""
tests/test_classify.py
-----------------------
Tests for the unknown bucket, safe fallback, and classify_consumers behaviour.
"""

from amu.classify import classify_consumers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_CAPS = {
    "out_of_coverage": [],
    "unresolved_patterns": ["dynamic_dispatch", "reflection"],
}

CAPS_WITH_COVERAGE_GAPS = {
    "out_of_coverage": ["legacy_module", "vendored_lib"],
    "unresolved_patterns": ["dynamic_dispatch"],
}


# ---------------------------------------------------------------------------
# Unknown bucket tests  (Curveball requirement)
# ---------------------------------------------------------------------------

class TestUnknownBucket:
    def test_unknown_bucket_always_present(self):
        """Unknown bucket must always exist, even with no impact output."""
        buckets = classify_consumers("my.symbol", "", BASE_CAPS)
        assert "unknown" in buckets

    def test_unknown_bucket_has_required_keys(self):
        buckets = classify_consumers("my.symbol", "", BASE_CAPS)
        unknown = buckets["unknown"]
        assert "unresolved_callsites" in unknown
        assert "dynamic_dispatch_sites" in unknown
        assert "out_of_coverage" in unknown
        assert "notes" in unknown

    def test_unknown_unresolved_callsites_increments(self):
        raw = "callsite: unresolved reference\nanother unresolved line\ndynamic dispatch found"
        buckets = classify_consumers("my.symbol", raw, BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] >= 2

    def test_unknown_out_of_coverage_propagated(self):
        buckets = classify_consumers("my.symbol", "", CAPS_WITH_COVERAGE_GAPS)
        assert "legacy_module" in buckets["unknown"]["out_of_coverage"]
        assert "vendored_lib" in buckets["unknown"]["out_of_coverage"]

    def test_unknown_notes_non_empty(self):
        buckets = classify_consumers("my.symbol", "", BASE_CAPS)
        assert len(buckets["unknown"]["notes"]) > 0

    def test_unknown_counts_zero_on_clean_output(self):
        raw = "hop: 1 caller_a\nhop: 2 caller_b"
        buckets = classify_consumers("my.symbol", raw, BASE_CAPS)
        assert buckets["unknown"]["unresolved_callsites"] == 0


# ---------------------------------------------------------------------------
# will_break / might_break bucket tests
# ---------------------------------------------------------------------------

class TestWillBreakMightBreak:
    def test_direct_hop_classified_as_potential_impact(self):
        raw = "hop: 1 module.DirectCaller"
        buckets = classify_consumers("target.fn", raw, BASE_CAPS)
        assert buckets["will_break"] == []
        assert len(buckets["might_break"]) >= 1

    def test_transitive_hop_classified_as_might_break(self):
        raw = "hop: 2 module.TransitiveCaller"
        buckets = classify_consumers("target.fn", raw, BASE_CAPS)
        assert len(buckets["might_break"]) >= 1

    def test_direct_keyword_classified_as_potential_impact(self):
        raw = "direct caller: module.Foo"
        buckets = classify_consumers("target.fn", raw, BASE_CAPS)
        assert buckets["will_break"] == []
        assert len(buckets["might_break"]) >= 1

    def test_transitive_keyword_classified_as_might_break(self):
        raw = "transitive consumer: module.Bar"
        buckets = classify_consumers("target.fn", raw, BASE_CAPS)
        assert len(buckets["might_break"]) >= 1

    def test_empty_output_yields_empty_will_and_might(self):
        buckets = classify_consumers("target.fn", "", BASE_CAPS)
        assert buckets["will_break"] == []
        assert buckets["might_break"] == []


# ---------------------------------------------------------------------------
# Safe fallback tests (no entire CLI available)
# ---------------------------------------------------------------------------

class TestSafeFallback:
    def test_empty_impact_output_does_not_raise(self):
        """classify_consumers must never raise even with empty/garbage input."""
        buckets = classify_consumers("any.symbol", "", BASE_CAPS)
        assert isinstance(buckets, dict)

    def test_garbage_impact_output_does_not_raise(self):
        raw = "\x00\xff garbage \n\t\r binary-ish data !!!@@@"
        buckets = classify_consumers("any.symbol", raw, BASE_CAPS)
        assert isinstance(buckets, dict)

    def test_error_prefixed_output_does_not_raise(self):
        raw = "Error running impact: entire: command not found"
        buckets = classify_consumers("any.symbol", raw, BASE_CAPS)
        assert isinstance(buckets, dict)

    def test_empty_caps_does_not_raise(self):
        buckets = classify_consumers("any.symbol", "hop: 1 foo", {})
        assert isinstance(buckets, dict)
        assert "out_of_coverage" in buckets["unknown"]
