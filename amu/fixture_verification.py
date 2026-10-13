"""Export and explicitly execute a small hash-pinned, AMU-owned cache fixture.

Execution is restricted to the bundled named cases. Fresh directories, isolated
Python mode, clean environments, byte limits and deadlines bound this fixture
runner; they do not provide a security sandbox for arbitrary code.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import stat
import sys
import tempfile
from pathlib import Path

from amu import patch as patch_tools
from amu import process

FIXTURE_VERSION = "shared-cache-v1"
CASES = ("compatible-refactor", "wrong-key", "class-state", "corrected")
TRUSTED_MANIFEST_SHA256 = "d9286add9c9ad658d0f115d7837246f0f2c778c6f092e27f6e35fa87d46581c3"
_DATA_ROOT = Path(__file__).resolve().parent / "benchmark_data" / FIXTURE_VERSION
_ASSET_NAMES = ("cache_case.py", "protected_checks.py", "runner.py", "contract.json") + tuple(
    f"patches/{case}.diff" for case in CASES
)
_MAX_ASSET_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 10
_OUTPUT_LIMIT_BYTES = 64 * 1024


class FixtureVerificationError(ValueError):
    """The named fixture or its output cannot be accepted safely."""


def _case(case: str) -> str:
    if not isinstance(case, str) or case not in CASES:
        raise FixtureVerificationError("case must be one of: " + ", ".join(CASES))
    return case


def _read_asset(name: str) -> bytes:
    path = _DATA_ROOT / name
    try:
        parents = {parent: parent.lstat() for parent in (path.parent, _DATA_ROOT)}
        for before_parent in parents.values():
            if not stat.S_ISDIR(before_parent.st_mode):
                raise FixtureVerificationError("fixture assets must use regular directories")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_ASSET_BYTES:
                raise FixtureVerificationError("fixture asset must be a bounded regular file: " + name)
            content = source.read(_MAX_ASSET_BYTES + 1)
            after = os.fstat(source.fileno())
            if len(content) > _MAX_ASSET_BYTES or _identity(before) != _identity(after):
                raise FixtureVerificationError("fixture asset changed or exceeded its byte limit: " + name)
            if _identity(path.lstat()) != _identity(after) or any(
                _identity(parent.lstat()) != _identity(before_parent) for parent, before_parent in parents.items()
            ):
                raise FixtureVerificationError("fixture asset or its directory changed during inspection: " + name)
            return content
    except OSError as exc:
        raise FixtureVerificationError("cannot read regular fixture asset: " + name) from exc


def _identity(value) -> tuple:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("nonfinite JSON value")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON value")
    return value


def _parse_json(content: bytes) -> dict:
    try:
        value = json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique_pairs,
            parse_float=_finite_float, parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise FixtureVerificationError("fixture returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise FixtureVerificationError("fixture JSON must be an object")
    return value


def _apply_patch(base: bytes, patch: bytes) -> bytes:
    """Apply only one pinned, canonical unified diff of cache_case.py in memory."""
    try:
        original = base.decode("utf-8")
        changes = patch_tools.parse_diff(patch.decode("utf-8"))
        if len(changes) != 1 or changes[0].path != "cache_case.py" or changes[0].operation != "modify":
            raise FixtureVerificationError("fixture patch must modify only cache_case.py")
        candidate = patch_tools.apply_file(changes[0], original)
    except (UnicodeError, patch_tools.PatchError) as exc:
        raise FixtureVerificationError("fixture patch must apply exactly to its pinned UTF-8 source") from exc
    canonical = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile="a/cache_case.py", tofile="b/cache_case.py"
    )).encode("utf-8")
    if canonical != patch or candidate.encode("utf-8") == base:
        raise FixtureVerificationError("fixture patch must be a nonempty canonical diff")
    return candidate.encode("utf-8")


def _load_assets() -> tuple[dict[str, bytes], dict, dict[str, bytes]]:
    manifest_bytes = _read_asset("manifest.json")
    if hashlib.sha256(manifest_bytes).hexdigest() != TRUSTED_MANIFEST_SHA256:
        raise FixtureVerificationError("fixture manifest differs from its trusted hash pin")
    manifest = _parse_json(manifest_bytes)
    if (
        manifest.get("schema_version") != 1 or manifest.get("fixture_version") != FIXTURE_VERSION
        or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(_ASSET_NAMES)
    ):
        raise FixtureVerificationError("fixture manifest inventory is invalid")
    assets = {}
    for name in _ASSET_NAMES:
        content = _read_asset(name)
        if hashlib.sha256(content).hexdigest() != manifest["files"][name]:
            raise FixtureVerificationError("fixture asset differs from its hash pin: " + name)
        assets[name] = content
    contract = _parse_json(assets["contract.json"])
    candidates = {case: _apply_patch(assets["cache_case.py"], assets[f"patches/{case}.diff"]) for case in CASES}
    return assets, contract, candidates


def fixture_patch(case: str) -> str:
    """Return a named bundled candidate patch without executing it."""
    case = _case(case)
    assets, _, _ = _load_assets()
    return assets[f"patches/{case}.diff"].decode("utf-8")


def export_fixture(output: str | Path, case: str | None = None) -> dict:
    """Export reviewable fixture files into an absolute, absent directory."""
    if case is not None:
        case = _case(case)
    path = Path(output).expanduser()
    if not path.is_absolute():
        raise FixtureVerificationError("fixture output must be an absolute absent directory")
    assets, contract, _ = _load_assets()
    selected = CASES if case is None else (case,)
    exported = {
        "cache_case.py": assets["cache_case.py"], "test_cache_case.py": assets["protected_checks.py"],
        "runner.py": assets["runner.py"], "contract.json": assets["contract.json"],
        **{f"patches/{name}.diff": assets[f"patches/{name}.diff"] for name in selected},
    }
    export_manifest = {
        "schema_version": 1, "fixture_version": FIXTURE_VERSION, "trusted_manifest_sha256": TRUSTED_MANIFEST_SHA256,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in exported.items()},
    }
    exported["export_manifest.json"] = (json.dumps(export_manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise FixtureVerificationError("fixture output already exists; no files were overwritten") from exc
    except OSError as exc:
        raise FixtureVerificationError("cannot create fixture output directory") from exc
    try:
        (path / "patches").mkdir(mode=0o700)
        for name, content in exported.items():
            with (path / name).open("xb") as destination:
                destination.write(content)
    except OSError as exc:
        raise FixtureVerificationError("cannot finish fixture export; inspect the partially exported directory") from exc
    return {
        "schema_version": 1, "fixture_version": FIXTURE_VERSION, "status": "exported", "output": str(path),
        "cases": list(selected), "files": sorted(exported), "contract": contract,
        "manifest_sha256": TRUSTED_MANIFEST_SHA256, "execution_performed": False,
    }


def _same_json(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _error(value) -> bool:
    return isinstance(value, dict) and set(value) == {"type", "message", "traceback"} and all(
        isinstance(item, str) for item in value.values()
    )


def _validate_report(report: dict, contract: dict, returncode: int) -> None:
    required = {"schema_version", "fixture_version", "contract_check", "tests", "passed", "exit_code"}
    if not (required <= report.keys() <= required | {"error"}) or (
        type(report["schema_version"]) is not int or report["schema_version"] != 1
        or report["fixture_version"] != FIXTURE_VERSION or type(report["passed"]) is not bool
        or type(report["exit_code"]) is not int or report["exit_code"] not in {0, 1}
        or report["exit_code"] != returncode
    ):
        raise FixtureVerificationError("fixture returned an invalid report envelope")
    mixed = report["contract_check"]
    label = {**contract["mixed_contract"], "basis": contract["basis"]}
    if not isinstance(mixed, dict) or set(mixed) not in (set(label) | {"observed", "passed"}, set(label) | {"observed", "passed", "error"}):
        raise FixtureVerificationError("fixture returned an invalid contract observation")
    if not all(_same_json(mixed.get(key), value) for key, value in label.items()) or type(mixed["passed"]) is not bool:
        raise FixtureVerificationError("fixture contract labels differ from the protected literal contract")
    if "error" in mixed and not _error(mixed["error"]):
        raise FixtureVerificationError("fixture returned an invalid contract error")
    mixed_passed = "error" not in mixed and _same_json(mixed["observed"], mixed["expected"])
    if mixed["passed"] != mixed_passed:
        raise FixtureVerificationError("fixture contract outcome contradicts its observation")
    tests = report["tests"]
    if not isinstance(tests, list):
        raise FixtureVerificationError("fixture tests must be an array")
    if "error" in report:
        if not _error(report["error"]) or tests or report["passed"]:
            raise FixtureVerificationError("fixture returned an invalid runner error")
        tests_passed = False
    else:
        labels = {item["test_id"]: {**item, "basis": contract["basis"]} for item in contract["tests"]}
        seen = set()
        tests_passed = True
        for item in tests:
            if (
                not isinstance(item, dict) or not isinstance(item.get("test_id"), str)
                or item["test_id"] not in labels or item["test_id"] in seen
            ):
                raise FixtureVerificationError("fixture returned an unknown or duplicate test identifier")
            seen.add(item["test_id"])
            label = labels[item["test_id"]]
            if not all(_same_json(item.get(key), value) for key, value in label.items()):
                raise FixtureVerificationError("fixture test labels differ from the protected contract")
            if not isinstance(item.get("outcome"), str) or item["outcome"] not in {"passed", "failed", "error"}:
                raise FixtureVerificationError("fixture returned an invalid test outcome")
            allowed = set(label) | {"observed", "outcome"}
            if item["outcome"] == "passed":
                if set(item) != allowed or not _same_json(item.get("observed"), item["expected"]):
                    raise FixtureVerificationError("passing fixture test contradicts its observation")
            elif set(item) != allowed | {"error"} or not _error(item["error"]):
                raise FixtureVerificationError("failing fixture test needs an error and traceback")
            tests_passed = tests_passed and item["outcome"] == "passed"
        if seen != set(labels):
            raise FixtureVerificationError("fixture omitted protected test identifiers")
    expected_passed = mixed_passed and tests_passed
    if report["passed"] != expected_passed or report["exit_code"] != (0 if expected_passed else 1):
        raise FixtureVerificationError("fixture exit or overall outcome contradicts its protected checks")


def _run_variant(directory: Path, source: bytes, assets: dict[str, bytes], contract: dict) -> dict:
    directory.mkdir(mode=0o700)
    materialized = {
        "cache_case.py": source, "test_cache_case.py": assets["protected_checks.py"],
        "runner.py": assets["runner.py"], "contract.json": assets["contract.json"],
    }
    for name, content in materialized.items():
        (directory / name).write_bytes(content)
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "AMU_DISABLE_MODELS": "1", "LC_ALL": "C", "LANG": "C"}
    if os.name == "nt" and "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    result = process.run(
        [sys.executable, "-I", "-B", str(directory / "runner.py")], timeout=_TIMEOUT_SECONDS,
        stdout_limit=_OUTPUT_LIMIT_BYTES, stderr_limit=_OUTPUT_LIMIT_BYTES, cwd=str(directory), env=env,
    )
    if result.error:
        return {"passed": False, "execution_error": result.error, "returncode": result.returncode}
    try:
        if result.stderr:
            raise FixtureVerificationError("fixture runner emitted unexpected stderr")
        report = _parse_json(result.stdout)
        _validate_report(report, contract, result.returncode)
    except FixtureVerificationError as exc:
        return {"passed": False, "execution_error": "invalid_report", "returncode": result.returncode, "detail": str(exc)}
    return {**report, "returncode": result.returncode}


def verify_fixture(case: str, execute_reviewed_fixture: bool = False) -> dict:
    """Verify one exact bundled case; execution requires a separate explicit opt-in."""
    case = _case(case)
    if type(execute_reviewed_fixture) is not bool:
        raise FixtureVerificationError("execute_reviewed_fixture must be a boolean")
    assets, contract, candidates = _load_assets()
    report = {
        "schema_version": 1, "fixture_version": FIXTURE_VERSION, "case": case,
        "manifest_sha256": TRUSTED_MANIFEST_SHA256, "contract": contract,
        "source_sha256": hashlib.sha256(assets["cache_case.py"]).hexdigest(),
        "patch_sha256": hashlib.sha256(assets[f"patches/{case}.diff"]).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidates[case]).hexdigest(),
        "contract_sha256": hashlib.sha256(assets["contract.json"]).hexdigest(),
        "test_sha256": hashlib.sha256(assets["protected_checks.py"]).hexdigest(),
        "runner_sha256": hashlib.sha256(assets["runner.py"]).hexdigest(),
        "reproduction_command": f"amu verify-fixture --case {case} --execute-reviewed-fixture --json",
        "expected_candidate_passes": contract["cases"][case]["candidate_passes"],
        "execution_requested": execute_reviewed_fixture, "execution_performed": False,
        "status": "execution_authorization_required", "passed": False, "exit_code": 2,
        "execution_boundary": "Only the hash-pinned bundled fixture; bounded disposable child processes are not a security sandbox.",
    }
    if case == "corrected":
        report.update(
            corrected_from_case="wrong-key",
            corrected_from_patch_sha256=hashlib.sha256(assets["patches/wrong-key.diff"]).hexdigest(),
            correction_basis="The corrected named case applies its own nonempty patch to the pinned original baseline.",
        )
    if not execute_reviewed_fixture:
        return report
    with tempfile.TemporaryDirectory(prefix="amu-shared-cache-") as temporary:
        root = Path(temporary)
        original = _run_variant(root / "original", assets["cache_case.py"], assets, contract)
        candidate = _run_variant(root / "candidate", candidates[case], assets, contract)
    report.update(execution_performed=True, original=original, candidate=candidate)
    if "execution_error" in original or "execution_error" in candidate:
        report.update(status="execution_failed", exit_code=2, matches_expected_outcome=False)
    elif not original["passed"]:
        report.update(status="baseline_failed", exit_code=2, matches_expected_outcome=False)
    else:
        passed = original["passed"] and candidate["passed"]
        report.update(
            status="passed" if passed else "regression", passed=passed, exit_code=0 if passed else 1,
            matches_expected_outcome=original["passed"] and candidate["passed"] == report["expected_candidate_passes"],
        )
    return report
