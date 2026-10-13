"""Run only the reviewed, self-contained files next to this pinned runner."""

import importlib.util
import json
import sys
import traceback
import unittest
from pathlib import Path


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ReportingResult(unittest.TestResult):
    def __init__(self, contract):
        super().__init__()
        self.contract = contract
        self.reports = []
        self.current = None

    def startTest(self, test):
        super().startTest(test)
        test_id = "test_cache_case.py::TestTypedCache::" + test._testMethodName
        label = next(item for item in self.contract["tests"] if item["test_id"] == test_id)
        self.current = {**label, "basis": self.contract["basis"], "outcome": "passed", "observed": None}

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.current.update(outcome="failed", error={
            "type": err[0].__name__, "message": str(err[1]), "traceback": self._exc_info_to_string(err, test),
        })

    def addError(self, test, err):
        super().addError(test, err)
        self.current.update(outcome="error", error={
            "type": err[0].__name__, "message": str(err[1]), "traceback": self._exc_info_to_string(err, test),
        })

    def stopTest(self, test):
        self.current["observed"] = getattr(test, "observed", None)
        self.reports.append(self.current)
        super().stopTest(test)


def main():
    root = Path(__file__).resolve().parent
    contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
    mixed = {
        "test_id": "contract::mixed_identity",
        "input": [1, "1"],
        "expected": {"kinds": ["int", "str"], "values": [1, "1"], "same_identity": False},
        "basis": contract["basis"],
        "observed": None,
        "passed": False,
    }
    report = {
        "schema_version": 1, "fixture_version": "shared-cache-v1",
        "contract_check": mixed, "tests": [], "passed": False, "exit_code": 1,
    }
    try:
        cache = load_module("cache_case", root / "cache_case.py")
        try:
            integer = cache.TypedCache.lookup(1)
            string = cache.TypedCache.lookup("1")
            mixed["observed"] = {
                "kinds": [integer.kind, string.kind],
                "values": [integer.value, string.value],
                "same_identity": integer is string,
            }
            mixed["passed"] = (
                mixed["observed"] == mixed["expected"]
                and type(integer.value) is int and type(string.value) is str
            )
        except Exception as exc:
            mixed["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        checks = load_module("test_cache_case", root / "test_cache_case.py")
        result = ReportingResult(contract)
        unittest.defaultTestLoader.loadTestsFromTestCase(checks.TestTypedCache).run(result)
        report["tests"] = result.reports
        report["passed"] = mixed["passed"] and result.wasSuccessful() and result.testsRun == len(contract["tests"])
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    report["exit_code"] = 0 if report["passed"] else 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
