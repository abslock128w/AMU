"""Hash-pinned, reviewed Phase 3 counterexamples with real protected checks."""

import difflib
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amu import patch as patch_tools
from amu import process

ROOT = Path(__file__).resolve().parents[1] / "amu" / "benchmark_data" / "phase3-counterexamples-v1"
VERSION = "phase3-counterexamples-v1"
FAMILIES = ("explicit-zero", "stable-order")
CASES = ("compatible", "regression", "corrected")
TRUSTED_MANIFEST_SHA256 = "9f692ff3c1d7c2aaa452e4d7e933783dbfaadf44488c4c8355ac318c551c6e8d"
LIMIT = 64 * 1024


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _same_json(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _assets():
    manifest_bytes = (ROOT / "manifest.json").read_bytes()
    assert _hash(manifest_bytes) == TRUSTED_MANIFEST_SHA256
    manifest = json.loads(manifest_bytes)
    expected = {"runner.py"}
    for family in FAMILIES:
        expected.update(f"{family}/{name}" for name in ("case.py", "protected_checks.py", "contract.json"))
        expected.update(f"{family}/patches/{case}.diff" for case in CASES)
    assert manifest["schema_version"] == 1 and manifest["fixture_version"] == VERSION
    assert set(manifest["files"]) == expected
    assets = {}
    for name in sorted(expected):
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        content = path.read_bytes()
        assert len(content) <= LIMIT and _hash(content) == manifest["files"][name]
        assets[name] = content
    return assets


def _candidate(source, patch):
    original = source.decode("utf-8")
    parsed = patch_tools.parse_diff(patch.decode("utf-8"))
    assert len(parsed) == 1 and parsed[0].path == "case.py" and parsed[0].operation == "modify"
    candidate = patch_tools.apply_file(parsed[0], original)
    canonical = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile="a/case.py", tofile="b/case.py",
    )).encode("utf-8")
    assert canonical == patch and candidate.encode("utf-8") != source
    return candidate.encode("utf-8")


def _validate_report(report, contract, returncode):
    assert set(report) == {"schema_version", "fixture_version", "contract_check", "tests", "passed", "exit_code"}
    assert report["schema_version"] == 1 and report["fixture_version"] == VERSION
    assert type(report["passed"]) is bool and type(report["exit_code"]) is int
    assert report["exit_code"] == returncode and returncode in (0, 1)
    probe = report["contract_check"]
    label = {**contract["probe"], "basis": contract["basis"]}
    assert all(_same_json(probe.get(key), value) for key, value in label.items())
    assert set(probe) in (set(label) | {"observed", "passed"}, set(label) | {"observed", "passed", "error"})
    assert type(probe["passed"]) is bool
    probe_passed = "error" not in probe and _same_json(probe["observed"], probe["expected"])
    assert probe["passed"] == probe_passed
    labels = {item["test_id"]: {**item, "basis": contract["basis"]} for item in contract["tests"]}
    assert len(report["tests"]) == len(labels)
    seen = set()
    for item in report["tests"]:
        assert item["test_id"] in labels and item["test_id"] not in seen
        seen.add(item["test_id"])
        expected = labels[item["test_id"]]
        assert all(_same_json(item.get(key), value) for key, value in expected.items())
        assert item["outcome"] in ("passed", "failed", "error")
        if item["outcome"] == "passed":
            assert set(item) == set(expected) | {"observed", "outcome"}
            assert _same_json(item["observed"], item["expected"])
        else:
            assert set(item) == set(expected) | {"observed", "outcome", "error"}
            assert item["error"]["type"] in ("AssertionError", "TypeError", "ValueError")
            assert isinstance(item["error"]["traceback"], str) and item["error"]["traceback"]
    assert seen == set(labels)
    passed = probe_passed and all(item["outcome"] == "passed" for item in report["tests"])
    assert report["passed"] == passed and report["exit_code"] == (0 if passed else 1)


def _run(source, family, assets, contract):
    with tempfile.TemporaryDirectory(prefix="amu-phase3-reviewed-") as temporary:
        directory = Path(temporary)
        (directory / "case.py").write_bytes(source)
        (directory / "protected_checks.py").write_bytes(assets[f"{family}/protected_checks.py"])
        (directory / "contract.json").write_bytes(assets[f"{family}/contract.json"])
        (directory / "runner.py").write_bytes(assets["runner.py"])
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "AMU_DISABLE_MODELS": "1",
               "LC_ALL": "C", "LANG": "C"}
        if os.name == "nt" and "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        result = process.run([sys.executable, "-I", "-B", str(directory / "runner.py")],
                             timeout=10, stdout_limit=LIMIT, stderr_limit=LIMIT, cwd=str(directory), env=env)
    assert result.error is None and not result.stderr, result.error
    report = json.loads(result.stdout)
    _validate_report(report, contract, result.returncode)
    return report


def _observations(report):
    return {
        "passed": report["passed"],
        "probe": {"input": report["contract_check"]["input"],
                  "expected": report["contract_check"]["expected"],
                  "observed": report["contract_check"]["observed"],
                  "passed": report["contract_check"]["passed"]},
        "tests": [
            {"test_id": item["test_id"], "input": item["input"], "expected": item["expected"],
             "observed": item["observed"], "outcome": item["outcome"],
             **({"error_type": item["error"]["type"]} if "error" in item else {})}
            for item in report["tests"]
        ],
    }


def reviewed_evidence():
    """Run only the six hash-pinned named candidates and their pinned originals."""
    assets = _assets()
    evidence = {"schema_version": 1, "fixture_version": VERSION,
                "manifest_sha256": TRUSTED_MANIFEST_SHA256, "families": {}}
    for family in FAMILIES:
        source = assets[f"{family}/case.py"]
        contract = json.loads(assets[f"{family}/contract.json"])
        assert contract["schema_version"] == 1 and contract["fixture_version"] == VERSION
        assert contract["family"] == family and contract["owner"] == "AMU"
        assert set(contract["cases"]) == set(CASES)
        original = _run(source, family, assets, contract)
        assert original["passed"] is True
        family_evidence = {
            "source_sha256": _hash(source), "contract_sha256": _hash(assets[f"{family}/contract.json"]),
            "protected_checks_sha256": _hash(assets[f"{family}/protected_checks.py"]),
            "runner_sha256": _hash(assets["runner.py"]), "original": _observations(original), "cases": {},
        }
        for case in CASES:
            patch = assets[f"{family}/patches/{case}.diff"]
            candidate = _candidate(source, patch)
            report = _run(candidate, family, assets, contract)
            expected_passes = contract["cases"][case]["candidate_passes"]
            assert type(expected_passes) is bool and report["passed"] is expected_passes
            if case == "regression":
                assert any(item["outcome"] == "failed" for item in report["tests"])
            family_evidence["cases"][case] = {
                "patch_sha256": _hash(patch), "candidate_sha256": _hash(candidate),
                "expected_candidate_passes": expected_passes, "candidate": _observations(report),
            }
        assert contract["cases"]["corrected"]["corrects"] == "regression"
        family_evidence["cases"]["corrected"].update(
            corrected_from_case="regression",
            corrected_from_patch_sha256=family_evidence["cases"]["regression"]["patch_sha256"],
        )
        evidence["families"][family] = family_evidence
    return evidence


class TestReviewedPhase3Counterexamples(unittest.TestCase):
    def test_reviewed_cases_match_pinned_evidence(self):
        actual = reviewed_evidence()
        expected = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)

    def test_manifest_pin_rejects_coupled_asset_and_manifest_edit(self):
        from shutil import copytree

        with tempfile.TemporaryDirectory(prefix="amu-phase3-tamper-") as temporary:
            copied = Path(temporary) / "fixture"
            copytree(ROOT, copied)
            source = copied / "explicit-zero" / "case.py"
            source.write_bytes(source.read_bytes() + b"\n# changed\n")
            manifest = json.loads((copied / "manifest.json").read_text())
            manifest["files"]["explicit-zero/case.py"] = _hash(source.read_bytes())
            (copied / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            with patch.object(sys.modules[__name__], "ROOT", copied):
                with self.assertRaises(AssertionError):
                    _assets()
