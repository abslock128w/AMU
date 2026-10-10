import json
import os
import subprocess
import sys

import pytest

from amu import keys


def _amu(args, env=None):
    return subprocess.run([sys.executable, "-m", "amu.cli", *args], capture_output=True, text=True, env={**os.environ, **(env or {})})


def test_set_stores_0600_and_never_prints_value(tmp_path):
    r = _amu(["key", "set", "--value", "sk-ant-api03-testvalue-abcdef123456"])
    assert r.returncode == 0 and "sk-ant-api03-testvalue-abcdef123456" not in r.stdout + r.stderr and "…3456" in r.stdout
    p = keys._path()
    assert oct(p.stat().st_mode & 0o777) == "0o600" and json.loads(p.read_text())["api_key"].endswith("3456") and json.loads(p.read_text())["provider"] == "anthropic"
    assert keys.source() == "file"


def test_gemini_key_set_stores_native_defaults_without_printing_value():
    secret = "AIza-gemini-cli-secret-7890"
    r = _amu(["key", "set", "--provider", "gemini", "--value", secret, "--json"])
    assert r.returncode == 0
    assert secret not in r.stdout + r.stderr
    assert "…7890" in json.loads(r.stdout)["masked"]

    stored = json.loads(keys._path().read_text())
    assert stored == {
        "provider": "gemini",
        "family": "gemini",
        "api_key": secret,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "model": "gemini-flash-latest",
    }
    assert oct(keys._path().stat().st_mode & 0o777) == "0o600"

    status = json.loads(_amu(["key", "status", "--json"]).stdout)
    assert status["provider"] == "gemini"
    assert status["model"] == "gemini-flash-latest"
    assert status["base_url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert "gemini" in status["providers"]
    assert secret not in json.dumps(status)


def test_env_wins_over_file(monkeypatch):
    keys.set_key("sk-ant-api03-file-value-000000000000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-env-value-1111111111111")
    assert keys.source() == "env" and keys.resolve().endswith("1111")


def test_rejects_non_key():
    with pytest.raises(ValueError):
        keys.set_key("hunter2")
    assert _amu(["key", "set", "--value", "nope"]).returncode == 2


def test_status_and_remove():
    keys.set_key("sk-ant-api03-file-value-000000000000")
    s = json.loads(_amu(["key", "status", "--json"]).stdout)
    assert s["source"] == "file" and s["provider"] == "anthropic" and "…0000" in s["masked"] and "file-value" not in json.dumps(s)
    assert keys.remove_key() is True and keys.source() == "absent" and keys.remove_key() is False


def test_doctor_reports_source_not_value(tmp_path):
    keys.set_key("sk-ant-api03-file-value-000000000000")
    r = _amu(["doctor", "--json"])
    out = json.loads(r.stdout)
    assert out["anthropic_key_source"] == "file" and "file-value" not in r.stdout
