"""Test-suite environment.

The legacy suite (test_amu / test_classify / test_plan / test_contract) proves the
noon-Curveball behaviour *without* the real `entire` binary, so by default the
real one is hidden from PATH. Tests that need graph data opt in to the recorded
fake via the `fake_entire` fixture (tests/bin/entire → tests/fake_entire.py).
"""

import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
_EMPTY_BIN = TESTS_DIR / "_nobin"


@pytest.fixture(autouse=True)
def _hide_real_entire(monkeypatch, tmp_path_factory):
    _EMPTY_BIN.mkdir(exist_ok=True)
    # Isolate the workspace: never touch the developer's real ~/.amu from the suite.
    monkeypatch.setenv("AMU_HOME", str(tmp_path_factory.mktemp("amu-home")))
    # Keep python + git reachable, drop package-manager bins where `entire` lives.
    keep = [p for p in os.environ.get("PATH", "").split(os.pathsep)
            if p and "homebrew" not in p and "/.local/share/entire" not in p]
    monkeypatch.setenv("PATH", os.pathsep.join([str(_EMPTY_BIN)] + keep))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AMU_PROVIDER", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    yield


@pytest.fixture
def fake_entire(monkeypatch):
    """Put the recorded-fixture fake `entire` first on PATH."""
    monkeypatch.setenv("PATH", os.pathsep.join([str(TESTS_DIR / "bin"), os.environ["PATH"]]))
    monkeypatch.setenv("AMU_FAKE_FIXTURES", str(TESTS_DIR / "fixtures"))
    monkeypatch.setenv("PYTHON_FOR_FAKE", sys.executable)
    yield TESTS_DIR / "fixtures"
