"""Versioned, labeled fixture scorecards; these are not population estimates.

Public API scores measure whether a static declaration change receives a review
signal. They do not establish that documentation must change, or prove runtime
compatibility. Relation scores compare supplied observations with explicit
labels; they never invent provider observations or hide missed edges by abstaining.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

_MAX_CORPUS_BYTES = 4 * 1024 * 1024
_DECISIONS = {"change", "no_change", "unknown"}
_DEFAULT_CORPUS = Path(__file__).resolve().parent / "benchmark_data"
Edge = tuple[str, str, str]


def _version(tool_version: str | None) -> str:
    if tool_version is not None:
        return tool_version
    from amu import __version__

    return __version__


def _ratio(numerator: int, denominator: int) -> float | None:
    # Empty denominators are undefined, not successful 100% measurements.
    return numerator / denominator if denominator else None


def _revision(sources: Any) -> str:
    encoded = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load_corpus(path: Path) -> dict:
    from amu import state_io

    try:
        corpus = state_io.read(path, max_bytes=_MAX_CORPUS_BYTES)
    except state_io.StateIOError as exc:
        raise ValueError(f"cannot read accuracy corpus {path}: {exc}") from exc
    if not isinstance(corpus, dict) or type(corpus.get("schema_version")) is not int or corpus["schema_version"] != 1:
        raise ValueError("accuracy corpus must be an object with schema_version 1")
    if not isinstance(corpus.get("fixture_version"), str) or not corpus["fixture_version"]:
        raise ValueError("accuracy corpus needs a nonempty fixture_version")
    return corpus


def _public_cases(corpus: dict) -> list[dict]:
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("public API accuracy corpus needs nonempty cases")
    identities: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or any(
            not isinstance(case.get(field), str) or not case[field]
            for field in ("id", "path", "category", "rationale")
        ):
            raise ValueError("public API case needs id, path, category and rationale")
        if case["id"] in identities:
            raise ValueError(f"duplicate public API case: {case['id']}")
        identities.add(case["id"])
        if not all(isinstance(case.get(field), str) for field in ("before", "after")):
            raise ValueError(f"case {case['id']} needs before and after source strings")
        if case.get("expected") not in _DECISIONS:
            raise ValueError(f"case {case['id']} has invalid expected decision")
    return cases


def _evaluate_public_api(case: dict) -> dict:
    from amu.api_manifest import compare_sources

    comparison = compare_sources(case["before"], case["after"], case["path"])
    changes = comparison.get("changes", [])
    unknowns = comparison.get("unknowns", [])
    uncertain = bool(unknowns) or comparison.get("complete") is not True
    return {
        "decision": "change" if changes else "unknown" if uncertain else "no_change",
        "uncertainty_reported": uncertain,
        "comparison": comparison,
    }


def _prediction(value: object) -> dict:
    if isinstance(value, str):
        value = {"decision": value, "uncertainty_reported": value == "unknown"}
    if not isinstance(value, dict) or value.get("decision") not in _DECISIONS:
        raise ValueError("case evaluator must return a valid decision")
    uncertainty = value.get("uncertainty_reported", value["decision"] == "unknown")
    if type(uncertainty) is not bool:
        raise ValueError("uncertainty_reported must be a boolean")
    return {**value, "uncertainty_reported": uncertainty}


def run_public_api(
    evaluate: Callable[[dict], object] | None = None,
    *,
    corpus_path: str | Path | None = None,
    tool_version: str | None = None,
) -> dict:
    """Evaluate every public API fixture; optionally inject a case evaluator.

    Known positives that receive ``unknown`` count as misses. Unknown negative
    decisions remain unassessed instead of counting as true negatives. Cases
    labeled ``unknown`` test uncertainty reporting independently, including when
    the adapter also reports a known change. No fixture is silently skipped.
    Packaged versioned fixtures are the default; callers can supply another corpus.
    """
    path = Path(corpus_path) if corpus_path is not None else _DEFAULT_CORPUS / "public-api-v1.json"
    if path.is_dir():
        path = path / "public-api-v1.json"
    corpus = _load_corpus(path)
    cases = _public_cases(corpus)
    evaluator = evaluate or _evaluate_public_api
    results: list[dict] = []
    tp = fp = fn = tn = abstained = errors = required_unknown = reported_unknown = unsupported_negative = 0
    for case in cases:
        try:
            prediction = _prediction(evaluator(case))
        except Exception as exc:
            # Evaluation failures are visible abstentions, never skipped successes.
            prediction = {
                "decision": "unknown",
                "uncertainty_reported": True,
                "evaluation_error": exc.__class__.__name__,
            }
            errors += 1
        expected, observed = case["expected"], prediction["decision"]
        if observed == "unknown":
            abstained += 1
        if expected == "unknown":
            required_unknown += 1
            correct = prediction["uncertainty_reported"] and "evaluation_error" not in prediction
            reported_unknown += int(correct)
        elif expected == "change":
            correct = observed == "change"
            tp += int(correct)
            fn += int(not correct)
        elif observed == "unknown":
            correct = False
            unsupported_negative += 1
        else:
            correct = observed == "no_change"
            tn += int(correct)
            fp += int(not correct)
        results.append({
            "id": case["id"], "category": case["category"], "expected": expected,
            "observed": observed, "matches_label": correct, "assessment": prediction,
        })
    known = len(cases) - required_unknown
    known_abstentions = sum(row["observed"] == "unknown" and row["expected"] != "unknown" for row in results)
    return {
        "schema_version": 1,
        "benchmark": "public-api-review-signals",
        "fixture_version": corpus["fixture_version"],
        "corpus_revision": _revision(corpus),
        "source_revision": _revision([
            {key: case[key] for key in ("id", "path", "before", "after")} for case in cases
        ]),
        "tool_version": _version(tool_version),
        "label_origin": corpus.get("label_origin", "unspecified"),
        "scope": corpus.get("scope", "supplied labeled fixtures only"),
        "evaluator": getattr(evaluator, "__qualname__", evaluator.__class__.__name__),
        "counts": {
            "cases": len(cases), "binary_labeled": known, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "abstentions": abstained, "binary_abstentions": known_abstentions,
            "negative_unassessed": unsupported_negative, "evaluation_errors": errors,
            "uncertainty_required": required_unknown, "uncertainty_reported": reported_unknown,
        },
        "metrics": {
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "specificity": _ratio(tn, tn + fp), "binary_coverage": _ratio(known - known_abstentions, known),
            "uncertainty_reporting": _ratio(reported_unknown, required_unknown),
        },
        "cases": results,
        "limitations": [
            "Hand-authored synthetic fixtures do not estimate NumPy or repository-wide accuracy.",
            "A static review signal does not prove a runtime change or required documentation edit.",
            "Required uncertainty cases are excluded from binary precision/recall and scored separately.",
            "Undefined metrics are null; abstaining on a labeled positive still lowers recall.",
        ],
    }


def _endpoint(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048 or value.count("#") != 1:
        raise ValueError("relation endpoints must be normalized relative-path#qualified-symbol strings")
    path, symbol = value.split("#", 1)
    relative = PurePosixPath(path)
    if (
        not path or relative.is_absolute() or ".." in relative.parts or "\\" in path
        or relative.as_posix() != path or path == "." or not symbol.strip()
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("relation endpoints must be normalized relative-path#qualified-symbol strings")
    return value


def _edge(value: object) -> Edge:
    if isinstance(value, Mapping):
        source, relation, target = value.get("source"), value.get("relation"), value.get("target")
    elif isinstance(value, (tuple, list)) and len(value) == 3:
        source, relation, target = value
    else:
        raise ValueError("relation edge must contain source, relation, target")
    if not isinstance(relation, str) or not relation or not relation.isascii() or not all(
        char.isalnum() or char == "_" for char in relation
    ):
        raise ValueError("relation must be a nonempty ASCII relation name")
    return _endpoint(source), relation.upper(), _endpoint(target)


def _edges(values: Iterable[object]) -> tuple[set[Edge], int]:
    rows = [_edge(value) for value in values]
    return set(rows), len(rows) - len(set(rows))


def _edge_rows(edges: set[Edge]) -> list[dict]:
    return [{"source": source, "relation": relation, "target": target} for source, relation, target in sorted(edges)]


def score_relations(
    expected: Iterable[object],
    observed: Iterable[object],
    *,
    negatives: Iterable[object] = (),
    abstentions: Iterable[object] = (),
    completeness: bool | Mapping[str, object] | None = None,
    provenance: str = "raw_provider",
    fixture_version: str = "unspecified",
    source_revision: str | None = None,
    tool_version: str | None = None,
) -> dict:
    """Score supplied normalized edges, including missing and false observations.

    Tuples use (source, relation, target). Only explicit negative labels count
    as true negatives, and only with ``completeness=True`` (or ``{'complete':
    True}``). A validated scorecard must be called separately from a raw one.
    Abstentions record unresolved edges; they cannot erase false negatives.
    """
    wanted, expected_duplicates = _edges(expected)
    actual, observed_duplicates = _edges(observed)
    absent, negative_duplicates = _edges(negatives)
    unresolved, abstention_duplicates = _edges(abstentions)
    if wanted & absent:
        raise ValueError("a relation cannot have both positive and negative labels")
    if actual & unresolved:
        raise ValueError("an edge cannot be both observed and abstained")
    if completeness is not None and not isinstance(completeness, (bool, Mapping)):
        raise ValueError("completeness must be a boolean or a coverage object")
    complete = completeness is True or isinstance(completeness, Mapping) and completeness.get("complete") is True
    hits, false, missed = wanted & actual, actual - wanted, wanted - actual
    # Incomplete retrieval or an explicit abstention cannot establish a negative.
    true_negatives = absent - actual - unresolved if complete else set()
    labeled_false = absent & actual
    return {
        "schema_version": 1, "benchmark": "labeled-relations", "provenance": provenance,
        "fixture_version": fixture_version, "source_revision": source_revision,
        "label_revision": _revision({"positive": _edge_rows(wanted), "negative": _edge_rows(absent)}),
        "tool_version": _version(tool_version), "completeness": completeness,
        "counts": {
            "expected": len(wanted), "observed": len(actual), "negative_labels": len(absent),
            "tp": len(hits), "fp": len(false), "fn": len(missed), "tn": len(true_negatives),
            "labeled_negative_fp": len(labeled_false),
            "negative_unassessed": len(absent - actual - true_negatives),
            "abstentions": len(unresolved), "positive_abstentions": len(unresolved & wanted),
            "duplicate_expected": expected_duplicates, "duplicate_observed": observed_duplicates,
            "duplicate_negative": negative_duplicates, "duplicate_abstentions": abstention_duplicates,
        },
        "metrics": {
            "precision": _ratio(len(hits), len(actual)), "recall": _ratio(len(hits), len(wanted)),
            "specificity": _ratio(len(true_negatives), len(true_negatives) + len(labeled_false)),
        },
        "true_positives": _edge_rows(hits), "false_positives": _edge_rows(false),
        "false_negatives": _edge_rows(missed), "true_negatives": _edge_rows(true_negatives),
        "abstentions": _edge_rows(unresolved),
        "limitations": [
            "Scores describe supplied labeled edges and supplied observations only.",
            "Explicit completeness is required to score absent negative labels as true negatives.",
            "Source validation and raw provider retrieval must be reported in separate scorecards.",
            "Undefined precision/recall/specificity is null, never 1.0.",
        ],
    }


def run_relations(
    observed: Iterable[object],
    *,
    validated: Iterable[object] | None = None,
    completeness: bool | Mapping[str, object] | None = None,
    validation_completeness: bool | Mapping[str, object] | None = None,
    abstentions: Iterable[object] = (),
    validation_abstentions: Iterable[object] = (),
    corpus_path: str | Path | None = None,
    tool_version: str | None = None,
) -> dict:
    """Compare external observations with the versioned relation fixture labels.

    The caller supplies observations and runs any provider/source validator.
    Source validation has separate completeness; raw capture cannot prove it.
    This runner deliberately does not substitute expected edges for observations.
    """
    path = Path(corpus_path) if corpus_path is not None else _DEFAULT_CORPUS / "relations-v1.json"
    if path.is_dir():
        path = path / "relations-v1.json"
    corpus = _load_corpus(path)
    if not isinstance(corpus.get("sources"), dict) or not all(
        isinstance(path, str) and isinstance(source, str) for path, source in corpus["sources"].items()
    ):
        raise ValueError("relation corpus needs a source mapping")
    if not isinstance(corpus.get("expected_edges"), list) or not isinstance(corpus.get("negative_edges"), list):
        raise ValueError("relation corpus needs explicit expected_edges and negative_edges lists")
    options = {
        "negatives": corpus["negative_edges"], "fixture_version": corpus["fixture_version"],
        "source_revision": _revision(corpus["sources"]), "tool_version": tool_version,
        "completeness": completeness,
    }
    reports = {"raw_provider": score_relations(
        corpus["expected_edges"], observed, abstentions=abstentions, provenance="raw_provider", **options,
    )}
    if validated is not None:
        validation_options = {**options, "completeness": validation_completeness}
        reports["amu_source_validated"] = score_relations(
            corpus["expected_edges"], validated, abstentions=validation_abstentions,
            provenance="amu_source_validated", **validation_options,
        )
    return {
        "schema_version": 1, "fixture_version": corpus["fixture_version"],
        "corpus_revision": _revision(corpus),
        "source_revision": options["source_revision"], "tool_version": _version(tool_version),
        "label_origin": corpus.get("label_origin", "unspecified"),
        "scope": corpus.get("scope", "supplied labeled fixtures only"),
        "reports": reports,
    }


def fixture_workflow_record(
    analyses: Mapping[str, dict],
    verifications: Mapping[str, dict],
    measurements: Mapping[str, dict],
    *,
    tool_version: str | None = None,
) -> dict:
    """Record supplied static reports and actual fixture results without execution.

    Static findings are review evidence, not binary predictions of a regression.
    Runtime results and caller measurements remain explicit supplied observations;
    this record does not attest to execution or establish agent effectiveness.
    """
    from amu import fixture_verification as fixture

    for name, supplied in (("analyses", analyses), ("verifications", verifications), ("measurements", measurements)):
        if not isinstance(supplied, Mapping) or set(supplied) != set(fixture.CASES):
            raise ValueError(name + " must contain each exact named bundled fixture case")
        if not all(isinstance(value, dict) for value in supplied.values()):
            raise ValueError(name + " entries must be objects")
    assets, contract, candidates = fixture._load_assets()
    cases = []
    baseline_passes = candidate_passes = runtime_failures = label_matches = 0
    for case in fixture.CASES:
        analysis, verification = analyses[case], verifications[case]
        identity = {
            "manifest_sha256": fixture.TRUSTED_MANIFEST_SHA256,
            "source_sha256": hashlib.sha256(assets["cache_case.py"]).hexdigest(),
            "patch_sha256": hashlib.sha256(assets[f"patches/{case}.diff"]).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidates[case]).hexdigest(),
            "contract_sha256": hashlib.sha256(assets["contract.json"]).hexdigest(),
            "test_sha256": hashlib.sha256(assets["protected_checks.py"]).hexdigest(),
            "runner_sha256": hashlib.sha256(assets["runner.py"]).hexdigest(),
        }
        if verification.get("case") != case or any(verification.get(key) != value for key, value in identity.items()):
            raise ValueError("verification identity differs from the trusted named fixture: " + case)
        if (
            not isinstance(analysis.get("request"), dict)
            or analysis["request"].get("patch_sha256") != identity["patch_sha256"]
        ):
            raise ValueError("static analysis patch identity differs from its named fixture: " + case)
        expected_file = {"path": "cache_case.py", "operation": "modify",
                         "baseline_sha256": identity["source_sha256"],
                         "candidate_sha256": identity["candidate_sha256"]}
        assessment = analysis.get("assessment")
        if (analysis.get("schema_version") not in {2, 3, 4, 5, 6, 7} or analysis.get("operation") != "concrete-patch-impact"
                or not isinstance(analysis.get("identity"), dict)
                or analysis["identity"].get("files") != [expected_file]
                or not isinstance(analysis.get("report_hash"), str) or len(analysis["report_hash"]) != 64
                or any(char not in "0123456789abcdef" for char in analysis["report_hash"])
                or not isinstance(assessment, dict) or assessment.get("executed") is not False
                or assessment.get("regression_proven") is not False):
            raise ValueError("static analysis does not identify the unexecuted named fixture candidate: " + case)
        if (verification.get("execution_requested") is not True or verification.get("execution_performed") is not True
                or not fixture._same_json(verification.get("contract"), contract)
                or verification.get("expected_candidate_passes") is not contract["cases"][case]["candidate_passes"]):
            raise ValueError("fixture workflow needs explicit executed results: " + case)
        required_measurements = {"analysis_latency_seconds", "verification_latency_seconds",
                                 "initial_response_bytes", "complete_evidence_response_bytes"}
        measured = measurements[case]
        if not required_measurements <= measured.keys() or any(
            type(measured[key]) not in (int, float) or not 0 <= measured[key] <= 1e9
            or not math.isfinite(measured[key])
            for key in ("analysis_latency_seconds", "verification_latency_seconds")
        ) or any(type(measured[key]) is not int or not 0 <= measured[key] <= 2**31
                 for key in ("initial_response_bytes", "complete_evidence_response_bytes")):
            raise ValueError("fixture workflow needs nonnegative measured latency and response bytes: " + case)
        try:
            analysis_bytes = json.dumps(analysis, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            json.dumps(verification, allow_nan=False)
            json.dumps(measurements[case], allow_nan=False)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("fixture workflow observations must be finite JSON") from exc
        original, candidate = verification.get("original"), verification.get("candidate")
        if not isinstance(original, dict) or not isinstance(candidate, dict):
            raise ValueError("fixture workflow needs original and candidate result objects: " + case)
        transport_failed = "execution_error" in original or "execution_error" in candidate
        original_passed = "execution_error" not in original and original.get("passed") is True
        candidate_passed = "execution_error" not in candidate and candidate.get("passed") is True
        if not transport_failed:
            for actual in (original, candidate):
                fixture._validate_report(
                    {key: value for key, value in actual.items() if key != "returncode"}, contract, actual.get("returncode")
                )
        else:
            runtime_failures += 1
        if transport_failed:
            expected_status, expected_passed, expected_exit, label_match = "execution_failed", False, 2, False
        elif not original_passed:
            expected_status, expected_passed, expected_exit, label_match = "baseline_failed", False, 2, False
        else:
            expected_status = "passed" if candidate_passed else "regression"
            expected_passed, expected_exit = candidate_passed, 0 if candidate_passed else 1
            label_match = candidate_passed is contract["cases"][case]["candidate_passes"]
        if (verification.get("status") != expected_status or verification.get("passed") is not expected_passed
                or type(verification.get("exit_code")) is not int or verification["exit_code"] != expected_exit
                or verification.get("matches_expected_outcome") is not label_match):
            raise ValueError("fixture workflow envelope contradicts protected results: " + case)
        baseline_passes += int(original_passed)
        candidate_passes += int(candidate_passed)
        label_matches += int(label_match)
        cases.append({
            "case": case, "identity": identity, "independent_label": contract["cases"][case],
            "static_counts": assessment.get("counts", {}),
            "recommended_test_ids": assessment.get("recommended_test_ids", []),
            "report_hash": analysis.get("report_hash"), "analysis_report_bytes": len(analysis_bytes),
            "measurements": measurements[case], "analysis": analysis, "verification": verification,
        })
    return {
        "schema_version": 1, "benchmark": "owned-fixture-workflow", "fixture_version": fixture.FIXTURE_VERSION,
        "tool_version": _version(tool_version),
        "trusted_task": {"owner": contract["owner"], "manifest_sha256": fixture.TRUSTED_MANIFEST_SHA256},
        "independent_labels": contract, "cases": cases,
        "engineering_correctness": {
            "basis": "Supplied actual protected fixture results, separate from static impact reports.",
            "cases": len(cases), "original_passes": baseline_passes, "candidate_passes": candidate_passes,
            "matches_independent_labels": label_matches, "runtime_transport_failures": runtime_failures,
        },
        "static_accuracy": {"status": "not_scored", "reason": "Static findings do not claim binary runtime predictions."},
        "agent_effectiveness": {"status": "not_measured", "reason": "No controlled agent comparison was performed."},
        "limitations": [
            "This small AMU-owned fixture is not evidence about NumPy or external repositories.",
            "Runtime results and latency measurements are supplied observations, not an execution attestation.",
            "Static report counts and recommended tests do not prove a regression or safety.",
            "Bounded disposable fixture execution is not a security sandbox for arbitrary code.",
        ],
    }
