"""Pinned fixture export, fail-closed execution and protected result validation."""

import ast
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from amu import benchmark, process
from amu import fixture_verification as fixture


def _forbid_process(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("fixture execution was not authorized")

    monkeypatch.setattr(process, "run", forbidden)


def _report(*, passed=True):
    _, contract, _ = fixture._load_assets()
    mixed = {
        **copy.deepcopy(contract["mixed_contract"]), "basis": contract["basis"],
        "observed": copy.deepcopy(contract["mixed_contract"]["expected"]), "passed": passed,
    }
    tests = [
        {**copy.deepcopy(item), "basis": contract["basis"], "observed": copy.deepcopy(item["expected"]), "outcome": "passed"}
        for item in contract["tests"]
    ]
    if not passed:
        mixed["observed"] = {"kinds": ["int", "int"], "values": [1, 1], "same_identity": True}
        tests[2].update(
            outcome="failed", observed=copy.deepcopy(mixed["observed"]),
            error={"type": "AssertionError", "message": "identical records", "traceback": "protected failure traceback"},
        )
    return {
        "schema_version": 1, "fixture_version": fixture.FIXTURE_VERSION,
        "contract_check": mixed, "tests": tests, "passed": passed, "exit_code": 0 if passed else 1,
    }


def _result(report):
    content = json.dumps(report).encode()
    return process.Result(report["exit_code"], content, b"", None, len(content), 0)


def test_manifest_and_all_named_candidate_sources_are_pinned():
    assets, contract, candidates = fixture._load_assets()
    assert contract["owner"] == "AMU"
    assert set(candidates) == set(fixture.CASES)
    for case, source in candidates.items():
        ast.parse(source.decode())
        assert source != assets["cache_case.py"]
        assert fixture.fixture_patch(case).startswith("--- a/cache_case.py\n+++ b/cache_case.py\n")
    assert "    _cache = None\n" in candidates["class-state"].decode()
    assert "key = str(value)" in candidates["wrong-key"].decode()


def test_default_verification_never_executes_and_supplies_independent_contract(monkeypatch):
    _forbid_process(monkeypatch)
    report = fixture.verify_fixture("wrong-key")
    assert report["status"] == "execution_authorization_required"
    assert report["exit_code"] == 2
    assert report["execution_performed"] is False
    for name in ("source_sha256", "patch_sha256", "candidate_sha256", "contract_sha256", "test_sha256", "runner_sha256"):
        assert len(report[name]) == 64
    assert report["reproduction_command"] == "amu verify-fixture --case wrong-key --execute-reviewed-fixture --json"
    assert report["contract"]["mixed_contract"] == {
        "test_id": "contract::mixed_identity", "input": [1, "1"],
        "expected": {"kinds": ["int", "str"], "values": [1, "1"], "same_identity": False},
    }


@pytest.mark.parametrize("case", ["../wrong-key", "wrong-key.diff", "/tmp/program.py", "", None, ["wrong-key"]])
def test_arbitrary_paths_and_programs_are_rejected_before_execution(case, monkeypatch):
    _forbid_process(monkeypatch)
    with pytest.raises(fixture.FixtureVerificationError, match="case must be one of"):
        fixture.verify_fixture(case, execute_reviewed_fixture=True)


@pytest.mark.parametrize("opt_in", [1, "true", None])
def test_execution_opt_in_must_be_an_explicit_boolean(opt_in, monkeypatch):
    _forbid_process(monkeypatch)
    with pytest.raises(fixture.FixtureVerificationError, match="must be a boolean"):
        fixture.verify_fixture("corrected", execute_reviewed_fixture=opt_in)


def test_export_is_nonexecuting_self_contained_and_does_not_overwrite(tmp_path, monkeypatch):
    _forbid_process(monkeypatch)
    output = tmp_path / "fixture"
    report = fixture.export_fixture(output)
    assert report["execution_performed"] is False
    assert (output / "test_cache_case.py").read_bytes() == (fixture._DATA_ROOT / "protected_checks.py").read_bytes()
    assert not (output / "protected_checks.py").exists()
    manifest = json.loads((output / "export_manifest.json").read_text())
    assert manifest["trusted_manifest_sha256"] == fixture.TRUSTED_MANIFEST_SHA256
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises(fixture.FixtureVerificationError, match="already exists"):
        fixture.export_fixture(output)
    assert before == {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}


def test_export_optional_case_limits_patches(tmp_path, monkeypatch):
    _forbid_process(monkeypatch)
    report = fixture.export_fixture(tmp_path / "fixture", case="wrong-key")
    assert report["cases"] == ["wrong-key"]
    assert sorted((tmp_path / "fixture" / "patches").iterdir()) == [tmp_path / "fixture" / "patches" / "wrong-key.diff"]


def test_export_relative_output_is_rejected(monkeypatch):
    _forbid_process(monkeypatch)
    with pytest.raises(fixture.FixtureVerificationError, match="absolute absent"):
        fixture.export_fixture("relative-directory")


@pytest.mark.parametrize("name", fixture._ASSET_NAMES)
def test_any_asset_drift_fails_before_process(name, tmp_path, monkeypatch):
    _forbid_process(monkeypatch)
    copied = tmp_path / "data"
    shutil.copytree(fixture._DATA_ROOT, copied)
    (copied / name).write_bytes((copied / name).read_bytes() + b"\n")
    monkeypatch.setattr(fixture, "_DATA_ROOT", copied)
    with pytest.raises(fixture.FixtureVerificationError, match="differs from its hash pin"):
        fixture.verify_fixture("wrong-key", execute_reviewed_fixture=True)


def test_coupled_asset_and_manifest_change_is_not_trusted(tmp_path, monkeypatch):
    _forbid_process(monkeypatch)
    copied = tmp_path / "data"
    shutil.copytree(fixture._DATA_ROOT, copied)
    source = copied / "cache_case.py"
    source.write_bytes(source.read_bytes() + b"\n# modified\n")
    manifest = json.loads((copied / "manifest.json").read_text())
    manifest["files"]["cache_case.py"] = hashlib.sha256(source.read_bytes()).hexdigest()
    (copied / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    monkeypatch.setattr(fixture, "_DATA_ROOT", copied)
    with pytest.raises(fixture.FixtureVerificationError, match="manifest differs from its trusted hash pin"):
        fixture.verify_fixture("corrected", execute_reviewed_fixture=True)


def test_asset_symlink_fails_before_process(tmp_path, monkeypatch):
    _forbid_process(monkeypatch)
    copied = tmp_path / "data"
    shutil.copytree(fixture._DATA_ROOT, copied)
    source = copied / "runner.py"
    source.unlink()
    source.symlink_to(fixture._DATA_ROOT / "runner.py")
    monkeypatch.setattr(fixture, "_DATA_ROOT", copied)
    with pytest.raises(fixture.FixtureVerificationError, match="regular fixture asset"):
        fixture.verify_fixture("corrected", execute_reviewed_fixture=True)


def test_original_candidate_use_independent_disposable_copies_and_clean_bounded_runner(monkeypatch):
    calls = []
    baseline = _report()
    regression = _report(passed=False)
    original_env_value = "private-original-environment-marker"
    monkeypatch.setenv("AMU_PRIVATE_TEST_MARKER", original_env_value)

    def run(argv, **options):
        directory = Path(options["cwd"])
        calls.append((argv, options, directory))
        assert argv[:3] == [fixture.sys.executable, "-I", "-B"]
        assert argv[3] == str(directory / "runner.py")
        assert options["timeout"] == 10
        assert options["stdout_limit"] == options["stderr_limit"] == 64 * 1024
        assert "AMU_PRIVATE_TEST_MARKER" not in options["env"]
        assert "PYTHONPATH" not in options["env"]
        assert (directory / "test_cache_case.py").is_file()
        assert not (directory / "__pycache__").exists()
        if directory.name == "original":
            assert "key = (type(value).__name__, value)" in (directory / "cache_case.py").read_text()
            return _result(baseline)
        assert "key = str(value)" in (directory / "cache_case.py").read_text()
        return _result(regression)

    monkeypatch.setattr(process, "run", run)
    report = fixture.verify_fixture("wrong-key", execute_reviewed_fixture=True)
    assert len(calls) == 2
    assert calls[0][2] != calls[1][2]
    assert not calls[0][2].exists() and not calls[1][2].exists()
    assert report["original"]["passed"] is True and report["candidate"]["passed"] is False
    assert report["status"] == "regression" and report["exit_code"] == 1
    assert report["matches_expected_outcome"] is True


def test_failing_original_is_not_labeled_a_candidate_regression(monkeypatch):
    baseline_failure = _report(passed=False)
    monkeypatch.setattr(process, "run", lambda *args, **kwargs: _result(baseline_failure))
    report = fixture.verify_fixture("wrong-key", execute_reviewed_fixture=True)
    assert report["status"] == "baseline_failed" and report["exit_code"] == 2
    assert report["matches_expected_outcome"] is False


def test_corrected_case_identifies_prior_bad_patch_without_claiming_that_is_its_baseline(monkeypatch):
    _forbid_process(monkeypatch)
    report = fixture.verify_fixture("corrected")
    assert report["corrected_from_case"] == "wrong-key"
    assert report["corrected_from_patch_sha256"] == hashlib.sha256(fixture.fixture_patch("wrong-key").encode()).hexdigest()
    assert "pinned original baseline" in report["correction_basis"]


@pytest.mark.parametrize("error,code", [("timeout", 124), ("output_limit", 125), ("io", 126), ("missing", 127)])
def test_process_failures_do_not_publish_partial_or_successful_results(error, code, monkeypatch):
    monkeypatch.setattr(process, "run", lambda *args, **kwargs: process.Result(code, b"partial", b"partial", error, 7, 7))
    report = fixture.verify_fixture("corrected", execute_reviewed_fixture=True)
    assert report["status"] == "execution_failed" and report["exit_code"] == 2
    assert report["original"]["execution_error"] == error
    assert "partial" not in json.dumps(report)


@pytest.mark.parametrize("bad", [b"{}", b"[]", b"{\"a\":1,\"a\":2}", b"{\"a\":NaN}", b"{\"a\":1e999}", b"\xff"])
def test_invalid_runner_json_fails_closed(bad, monkeypatch):
    monkeypatch.setattr(process, "run", lambda *args, **kwargs: process.Result(0, bad, b"", None, len(bad), 0))
    report = fixture.verify_fixture("corrected", execute_reviewed_fixture=True)
    assert report["status"] == "execution_failed" and report["passed"] is False
    assert report["candidate"]["execution_error"] == "invalid_report"


@pytest.mark.parametrize("mutation", ["missing-test", "duplicate-test", "changed-expectation", "contradicted-outcome", "changed-type", "bad-test-id", "bad-outcome"])
def test_runner_cannot_omit_checks_or_relabel_expectations(mutation, monkeypatch):
    report = _report()
    if mutation == "missing-test":
        report["tests"].pop()
    elif mutation == "duplicate-test":
        report["tests"].append(copy.deepcopy(report["tests"][0]))
    elif mutation == "changed-expectation":
        report["contract_check"]["expected"]["same_identity"] = True
    elif mutation == "contradicted-outcome":
        report["contract_check"]["observed"]["same_identity"] = True
    elif mutation == "changed-type":
        report["tests"][0]["observed"]["value"] = True
    elif mutation == "bad-test-id":
        report["tests"][0]["test_id"] = []
    else:
        report["tests"][0]["outcome"] = []
    monkeypatch.setattr(process, "run", lambda *args, **kwargs: _result(report))
    result = fixture.verify_fixture("corrected", execute_reviewed_fixture=True)
    assert result["status"] == "execution_failed" and result["exit_code"] == 2


@pytest.mark.parametrize("case,passes", [("compatible-refactor", True), ("wrong-key", False), ("class-state", False), ("corrected", True)])
def test_reviewed_bundled_cases_execute_actual_protected_checks(case, passes):
    report = fixture.verify_fixture(case, execute_reviewed_fixture=True)
    assert report["original"]["passed"] is True
    assert report["candidate"]["passed"] is passes
    assert report["matches_expected_outcome"] is True
    assert report["exit_code"] == (0 if passes else 1)
    assert report["candidate"]["contract_check"]["input"] == [1, "1"]
    assert len(report["candidate"]["tests"]) == 4
    if not passes:
        assert report["candidate"]["contract_check"]["passed"] is False
        assert any("error" in item and "traceback" in item["error"] for item in report["candidate"]["tests"])


def _workflow_inputs():
    analyses, verifications, measurements = {}, {}, {}
    for case in fixture.CASES:
        verification = fixture.verify_fixture(case)
        original = _report()
        candidate = _report(passed=verification["expected_candidate_passes"])
        original["returncode"] = original["exit_code"]
        candidate["returncode"] = candidate["exit_code"]
        passes = candidate["passed"]
        verification.update(execution_requested=True, execution_performed=True,
                            original=original, candidate=candidate,
                            status="passed" if passes else "regression", passed=passes,
                            exit_code=0 if passes else 1, matches_expected_outcome=True)
        analyses[case] = {
            "schema_version": 2, "operation": "concrete-patch-impact",
            "request": {"patch_sha256": verification["patch_sha256"]},
            "identity": {"files": [{"path": "cache_case.py", "operation": "modify",
                                    "baseline_sha256": verification["source_sha256"],
                                    "candidate_sha256": verification["candidate_sha256"]}]},
            "assessment": {"counts": {"findings": 1, "state_dependencies": 4},
                           "executed": False, "regression_proven": False,
                           "recommended_test_ids": [candidate["tests"][2]["test_id"]]},
            "report_hash": "a" * 64,
            "limitations": ["Static source evidence does not prove a regression."],
        }
        verifications[case] = verification
        measurements[case] = {"analysis_latency_seconds": 0.01, "verification_latency_seconds": 0.02,
                              "initial_response_bytes": 1024, "complete_evidence_response_bytes": 4096}
    return analyses, verifications, measurements


def test_fixture_workflow_record_preserves_supplied_evidence_and_separates_measurement_types(monkeypatch):
    _forbid_process(monkeypatch)
    inputs = _workflow_inputs()
    report = benchmark.fixture_workflow_record(*inputs, tool_version="test")
    assert report["engineering_correctness"]["original_passes"] == 4
    assert report["engineering_correctness"]["candidate_passes"] == 2
    assert report["engineering_correctness"]["matches_independent_labels"] == 4
    assert report["static_accuracy"]["status"] == "not_scored"
    assert report["agent_effectiveness"]["status"] == "not_measured"
    assert report["cases"][0]["analysis"] == inputs[0]["compatible-refactor"]
    assert report["cases"][0]["verification"] == inputs[1]["compatible-refactor"]
    assert report["cases"][0]["analysis_report_bytes"] > 0
    assert report["cases"][0]["recommended_test_ids"] == [
        "test_cache_case.py::TestTypedCache::test_distinct_integer_and_string"
    ]
    assert "precision" not in json.dumps(report)


@pytest.mark.parametrize("bad", ["missing-case", "patch-identity", "verification-identity", "not-executed",
                                 "execution-not-requested", "outer-status", "outer-exit", "outer-result",
                                 "outer-label", "changed-contract", "analysis-source", "false-outcome",
                                 "missing-measurement", "nonfinite-measurement"])
def test_fixture_workflow_rejects_incomplete_or_contradictory_supplied_evidence(bad, monkeypatch):
    _forbid_process(monkeypatch)
    analyses, verifications, measurements = _workflow_inputs()
    if bad == "missing-case":
        analyses.pop("class-state")
    elif bad == "patch-identity":
        analyses["wrong-key"]["request"]["patch_sha256"] = "0" * 64
    elif bad == "verification-identity":
        verifications["wrong-key"]["candidate_sha256"] = "0" * 64
    elif bad == "not-executed":
        verifications["wrong-key"]["execution_performed"] = False
    elif bad == "execution-not-requested":
        verifications["wrong-key"]["execution_requested"] = False
    elif bad == "outer-status":
        verifications["wrong-key"]["status"] = "execution_authorization_required"
    elif bad == "outer-exit":
        verifications["wrong-key"]["exit_code"] = 2
    elif bad == "outer-result":
        verifications["wrong-key"]["passed"] = True
    elif bad == "outer-label":
        verifications["wrong-key"]["matches_expected_outcome"] = False
    elif bad == "changed-contract":
        verifications["wrong-key"]["contract"]["basis"] = "changed"
    elif bad == "analysis-source":
        analyses["wrong-key"]["identity"]["files"][0]["baseline_sha256"] = "0" * 64
    elif bad == "false-outcome":
        verifications["wrong-key"]["candidate"]["passed"] = True
    elif bad == "missing-measurement":
        measurements["wrong-key"].pop("initial_response_bytes")
    else:
        measurements["wrong-key"]["analysis_latency_seconds"] = float("inf")
    with pytest.raises(ValueError):
        benchmark.fixture_workflow_record(analyses, verifications, measurements)
