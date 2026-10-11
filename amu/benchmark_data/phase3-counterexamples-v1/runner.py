"""Run the adjacent reviewed case and protected checks in an isolated child."""

import importlib.util
import json
import sys
import traceback
import unittest
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Observations(unittest.TestResult):
    def __init__(self, contract):
        super().__init__()
        self.contract = contract
        self.tests = []
        self.current = None

    def startTest(self, test):
        super().startTest(test)
        test_id = test._testMethodName
        label = next(item for item in self.contract["tests"] if item["test_id"] == test_id)
        self.current = {**label, "basis": self.contract["basis"], "observed": None, "outcome": "passed"}

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
        self.tests.append(self.current)
        super().stopTest(test)


def main():
    root = Path(__file__).resolve().parent
    contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
    report = {"schema_version": 1, "fixture_version": "phase3-counterexamples-v1",
              "contract_check": None, "tests": [], "passed": False, "exit_code": 1}
    try:
        load("case", root / "case.py")
        checks = load("protected_checks", root / "protected_checks.py")
        probe = {**contract["probe"], "basis": contract["basis"], "observed": None, "passed": False}
        try:
            probe["observed"] = checks.probe()
            probe["passed"] = type(probe["observed"]) is dict and probe["observed"] == probe["expected"]
        except Exception as exc:
            probe["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        report["contract_check"] = probe
        result = Observations(contract)
        unittest.defaultTestLoader.loadTestsFromTestCase(checks.ProtectedChecks).run(result)
        report["tests"] = result.tests
        report["passed"] = probe["passed"] and result.wasSuccessful() and result.testsRun == len(contract["tests"])
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
    report["exit_code"] = 0 if report["passed"] else 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
