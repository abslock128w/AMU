import ast
import json
import os
import subprocess
import sys

import pytest

from amu import benchmark


def _edge(source="pkg/local.py#caller", target="pkg/local.py#helper", relation="CALLS"):
    return {"source": source, "target": target, "relation": relation}


def _corpus(tmp_path, expected):
    path = tmp_path / "public-api-v1.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "fixture_version": "scoring-test-v1",
        "cases": [{
            "id": str(index), "category": "scoring", "path": "pkg/api.py",
            "before": "def f(): pass\n", "after": "def f(value): pass\n",
            "expected": decision, "rationale": "Exercise the confusion matrix.",
        } for index, decision in enumerate(expected)],
    }))
    return path


def test_public_api_scorecard_penalizes_misses_and_false_alarms_and_discloses_abstentions(tmp_path):
    path = _corpus(tmp_path, ["change", "change", "no_change", "no_change", "no_change", "unknown", "unknown"])
    predictions = ["change", "unknown", "change", "no_change", "unknown",
                   {"decision": "change", "uncertainty_reported": True}, "no_change"]
    report = benchmark.run_public_api(lambda case: predictions[int(case["id"])], corpus_path=path, tool_version="test")

    assert report["counts"] == {
        "cases": 7, "binary_labeled": 5, "tp": 1, "fp": 1, "fn": 1, "tn": 1,
        "abstentions": 2, "binary_abstentions": 2, "negative_unassessed": 1,
        "evaluation_errors": 0, "uncertainty_required": 2, "uncertainty_reported": 1,
    }
    assert report["metrics"] == {
        "precision": 0.5, "recall": 0.5, "specificity": 0.5,
        "binary_coverage": 0.6, "uncertainty_reporting": 0.5,
    }
    assert report["tool_version"] == "test"
    assert report["source_revision"].startswith("sha256:")
    assert len(report["cases"]) == 7


def test_evaluator_failures_are_not_counted_as_successful_uncertainty_reporting(tmp_path):
    path = _corpus(tmp_path, ["change", "no_change", "unknown"])

    def unavailable(case):
        raise RuntimeError("unavailable")

    report = benchmark.run_public_api(unavailable, corpus_path=path)
    assert report["counts"]["fn"] == 1
    assert report["counts"]["tn"] == 0
    assert report["counts"]["evaluation_errors"] == 3
    assert report["metrics"]["uncertainty_reporting"] == 0
    assert report["metrics"]["precision"] is None
    assert all(not case["matches_label"] for case in report["cases"])


def test_all_negative_corpus_has_undefined_recall_not_perfect_recall(tmp_path):
    path = _corpus(tmp_path, ["no_change"])
    report = benchmark.run_public_api(lambda case: "no_change", corpus_path=tmp_path)
    assert report["counts"]["tn"] == 1
    assert report["metrics"]["precision"] is None
    assert report["metrics"]["recall"] is None
    assert report["metrics"]["specificity"] == 1
    assert path.exists()


def test_public_api_manifest_fixtures_cover_declared_contracts_and_uncertainty():
    corpus = json.loads((benchmark._DEFAULT_CORPUS / "public-api-v1.json").read_text())
    cases = benchmark._public_cases(corpus)
    assert {case["category"] for case in cases} >= {
        "signature", "defaults", "alias", "reexport", "exports", "negative", "overload",
        "exceptions", "returns", "property", "async", "constant", "deprecation", "uncertainty",
    }
    assert {case["id"] for case in cases if case["expected"] == "unknown"} == {
        "dynamic-exports", "conditional-reexport", "star-import",
    }


def test_default_public_api_runner_uses_adapter_and_records_real_comparisons():
    pytest.importorskip("amu.api_manifest")
    report = benchmark.run_public_api()
    assert report["counts"]["evaluation_errors"] == 0
    assert report["counts"]["cases"] == 20
    assert report["counts"]["tp"] + report["counts"]["fn"] == 14
    assert all("comparison" in row["assessment"] for row in report["cases"])
    # The runner may reveal actual errors; it must retain the non-perfect score.
    for row in report["cases"]:
        if row["expected"] == "change":
            assert row["matches_label"] == (row["observed"] == "change")


def test_relation_scoring_counts_missing_false_and_duplicate_edges():
    same = _edge()
    cross = _edge("pkg/remote.py#caller")
    docstring = _edge("pkg/local.py#example_only")
    report = benchmark.score_relations([same, cross], [same, same, docstring], negatives=[docstring],
                                       completeness=True, source_revision="fixture-sha", tool_version="provider-v1")
    assert {key: report["counts"][key] for key in ("tp", "fp", "fn", "tn", "duplicate_observed")} == {
        "tp": 1, "fp": 1, "fn": 1, "tn": 0, "duplicate_observed": 1,
    }
    assert report["metrics"] == {"precision": 0.5, "recall": 0.5, "specificity": 0}
    assert report["false_negatives"] == [cross]
    assert report["false_positives"] == [docstring]


def test_partial_and_abstained_negative_edges_cannot_be_true_negatives():
    positive, negative = _edge(), _edge("pkg/local.py#isolated")
    partial = benchmark.score_relations([positive], [], negatives=[negative],
                                        abstentions=[positive], completeness={"complete": False, "captured": 0})
    assert partial["counts"]["fn"] == 1
    assert partial["counts"]["positive_abstentions"] == 1
    assert partial["counts"]["tn"] == 0
    assert partial["counts"]["negative_unassessed"] == 1
    assert partial["metrics"]["recall"] == 0
    assert partial["metrics"]["precision"] is None
    complete = benchmark.score_relations([], [], negatives=[negative], abstentions=[negative], completeness=True)
    assert complete["counts"]["tn"] == 0
    assert complete["counts"]["negative_unassessed"] == 1


def test_isolated_case_scores_only_explicit_negatives_and_undefined_empty_metrics():
    isolated = _edge("pkg/local.py#isolated")
    report = benchmark.score_relations([], [], negatives=[isolated], completeness=True)
    assert report["counts"]["tn"] == 1
    assert report["metrics"] == {"precision": None, "recall": None, "specificity": 1}
    unknown = benchmark.score_relations([], [])
    assert unknown["metrics"] == {"precision": None, "recall": None, "specificity": None}


def test_relation_runner_keeps_raw_provider_and_validated_scores_separate():
    corpus = json.loads((benchmark._DEFAULT_CORPUS / "relations-v1.json").read_text())
    true = corpus["expected_edges"][0]
    false = corpus["negative_edges"][1]
    report = benchmark.run_relations([true, false], validated=[true], completeness=True, tool_version="fixture-provider")
    raw, validated = report["reports"]["raw_provider"], report["reports"]["amu_source_validated"]
    assert raw["counts"]["fp"] == 1
    assert raw["counts"]["fn"] == 2
    assert validated["counts"]["fp"] == 0
    assert validated["counts"]["fn"] == 2  # Filtering cannot invent the missing cross-file edges.
    assert validated["counts"]["tn"] == 0
    assert validated["counts"]["negative_unassessed"] == 3
    assert raw["counts"]["tn"] == 2
    assert raw["completeness"] is True
    assert validated["completeness"] is None
    assert raw["provenance"] == "raw_provider"
    assert validated["provenance"] == "amu_source_validated"
    assert raw["source_revision"] == validated["source_revision"] == report["source_revision"]
    assert "not a NumPy" in report["scope"]


def test_relation_source_validation_requires_its_own_explicit_completeness():
    report = benchmark.run_relations([], validated=[], completeness=False,
                                    validation_completeness={"complete": True, "source_scopes": 5})
    raw, validated = report["reports"]["raw_provider"], report["reports"]["amu_source_validated"]
    assert raw["counts"]["tn"] == 0
    assert validated["counts"]["tn"] == 3
    assert validated["completeness"] == {"complete": True, "source_scopes": 5}


def test_relation_fixture_labels_agree_with_independent_python_source_evidence(tmp_path):
    from amu.source_evidence import graph_call_evidence

    corpus = json.loads((benchmark._DEFAULT_CORPUS / "relations-v1.json").read_text())
    for path, source in corpus["sources"].items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source)

    def assess(edge):
        source_path, source_symbol = edge["source"].split("#")
        target_path, target_symbol = edge["target"].split("#")
        tree = ast.parse(corpus["sources"][source_path])
        scope = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == source_symbol)
        return graph_call_evidence(str(tmp_path), source_path, target_symbol, target_path=target_path,
                                   scope_start=scope.lineno, scope_end=scope.end_lineno)

    assert [assess(edge) for edge in corpus["expected_edges"]] == ["verified", "verified", "verified"]
    # The isolated scope has no executable call; the validator conservatively
    # abstains. It does not convert missing static evidence into runtime proof.
    assert [assess(edge) for edge in corpus["negative_edges"]] == [
        "unverifiable", "contradicted", "contradicted",
    ]


@pytest.mark.parametrize("value", [
    _edge("../outside.py#f"), _edge("/absolute.py#f"), _edge("pkg//local.py#f"),
    _edge("pkg/local.py"), _edge(relation="CALLS!"), {"source": "pkg/local.py#f"},
])
def test_relation_malformed_identity_is_rejected_instead_of_skipped(value):
    with pytest.raises(ValueError):
        benchmark.score_relations([], [value])


def test_relation_inconsistent_labels_and_claims_are_rejected():
    edge = _edge()
    with pytest.raises(ValueError, match="positive and negative"):
        benchmark.score_relations([edge], [], negatives=[edge])
    with pytest.raises(ValueError, match="observed and abstained"):
        benchmark.score_relations([edge], [edge], abstentions=[edge])


def test_relation_direction_and_qualified_identity_are_not_merged():
    forward = _edge("pkg/one.py#same", "pkg/two.py#same")
    reverse = _edge("pkg/two.py#same", "pkg/one.py#same")
    report = benchmark.score_relations([forward], [reverse])
    assert report["counts"]["tp"] == 0
    assert report["counts"]["fp"] == report["counts"]["fn"] == 1


def test_fixture_revision_changes_when_sources_change(tmp_path):
    path = _corpus(tmp_path, ["change"])
    first = benchmark.run_public_api(lambda case: "change", corpus_path=path)
    corpus = json.loads(path.read_text())
    corpus["cases"][0]["after"] = "def f(value=1): pass\n"
    path.write_text(json.dumps(corpus))
    second = benchmark.run_public_api(lambda case: "change", corpus_path=path)
    assert first["source_revision"] != second["source_revision"]


def test_changed_labels_have_a_distinct_corpus_revision_even_when_sources_match(tmp_path):
    path = _corpus(tmp_path, ["change"])
    first = benchmark.run_public_api(lambda case: "change", corpus_path=path)
    corpus = json.loads(path.read_text())
    corpus["cases"][0]["expected"] = "no_change"
    path.write_text(json.dumps(corpus))
    second = benchmark.run_public_api(lambda case: "change", corpus_path=path)
    assert first["source_revision"] == second["source_revision"]
    assert first["corpus_revision"] != second["corpus_revision"]
    assert second["counts"]["fp"] == 1


def test_custom_corpus_rejects_duplicate_labels(tmp_path):
    path = _corpus(tmp_path, ["change", "no_change"])
    corpus = json.loads(path.read_text())
    corpus["cases"][1]["id"] = "0"
    path.write_text(json.dumps(corpus))
    with pytest.raises(ValueError, match="duplicate"):
        benchmark.run_public_api(lambda case: "change", corpus_path=path)


@pytest.mark.parametrize("payload", [
    '{"schema_version": 1, "schema_version": 1, "fixture_version": "ambiguous"}',
    '{"schema_version": 1, "fixture_version": "ambiguous", "metadata": {"label": 1, "label": 2}}',
    '{"schema_version": 1, "fixture_version": "invalid", "value": NaN}',
    '{"schema_version": 1, "fixture_version": "invalid", "value": Infinity}',
    '{"schema_version": 1, "fixture_version": "invalid", "value": 1e999}',
    '{"schema_version": true, "fixture_version": "invalid"}',
])
def test_custom_corpus_rejects_ambiguous_and_nonfinite_json(tmp_path, payload):
    path = tmp_path / "public-api-v1.json"
    path.write_text(payload)
    with pytest.raises(ValueError):
        benchmark.run_public_api(lambda case: "change", corpus_path=path)


def test_custom_corpus_rejects_symlinks_and_oversized_sources(tmp_path):
    target = _corpus(tmp_path, ["change"])
    link = tmp_path / "linked.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="safely read"):
        benchmark.run_public_api(lambda case: "change", corpus_path=link)
    target.write_text(" " * (benchmark._MAX_CORPUS_BYTES + 1))
    with pytest.raises(ValueError, match="byte limit"):
        benchmark.run_public_api(lambda case: "change", corpus_path=target)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO support required")
def test_custom_fifo_corpus_fails_promptly_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "fifo.json"
    os.mkfifo(path)
    result = subprocess.run(
        [sys.executable, "-c", "from amu.benchmark import run_public_api; import sys; run_public_api(corpus_path=sys.argv[1])", str(path)],
        capture_output=True, text=True, timeout=3,
    )
    assert result.returncode != 0
    assert "cannot read accuracy corpus" in result.stderr
