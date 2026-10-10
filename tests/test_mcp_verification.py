"""Actual local initialize/list parity plus bounded optional-dependency failures."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amu import mcp_manifest, mcp_verification


def test_actual_selected_mcp_server_matches_reviewed_manifest_without_tool_calls():
    pytest.importorskip("mcp")
    repo = Path(__file__).resolve().parents[1]
    original = {name: (repo / ".amu" / name).read_bytes() if (repo / ".amu" / name).is_file() else None
                for name in ("map.json", "contract.json", "nav.json")}
    result = mcp_verification.probe(sys.executable, str(repo))
    assert result["status"] == "matched", result
    assert result["manifest"]["match"] is True
    assert result["protocol"]["initialized"] is True
    assert result["protocol"]["tool_count"] == 4
    assert result["protocol"]["server_info"]["version"]
    assert result["runtime"]["build_sha256"] == mcp_manifest.runtime_identity()["build_sha256"]
    assert result["authority"]["tool_calls_executed"] is False
    assert result["authority"]["repository_authorization_certified"] is False
    assert original == {name: (repo / ".amu" / name).read_bytes() if (repo / ".amu" / name).is_file() else None for name in original}


def test_optional_sdk_absence_does_not_start_a_process(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_verification.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(mcp_verification.process, "run", lambda *_args, **_kwargs: pytest.fail("do not start a process or install"))
    result = mcp_verification.probe(sys.executable, str(tmp_path))
    assert result["status"] == "unavailable" and result["error_kind"] == "mcp_sdk_unavailable"


@pytest.mark.parametrize("error", ["timeout", "output_limit", "missing", "io"])
def test_probe_cannot_treat_partial_or_failed_process_output_as_parity(monkeypatch, tmp_path, error):
    monkeypatch.setattr(mcp_verification.importlib.util, "find_spec", lambda _name: object())
    calls = []

    def run(command, **options):
        calls.append(options)
        return SimpleNamespace(returncode=3, error=error, stdout=b'{"status":"matched"}', stderr=b"private diagnostic")

    monkeypatch.setattr(mcp_verification.process, "run", run)
    result = mcp_verification.probe(sys.executable, str(tmp_path))
    assert result["status"] == "failed" and result["manifest"]["match"] is False
    assert "private diagnostic" not in json.dumps(result)
    assert calls[0]["timeout"] == 25 and calls[0]["stdout_limit"] == 128 * 1024
    assert calls[0]["env"]["AMU_DISABLE_MODELS"] == "1"


@pytest.mark.parametrize("stdout", [b"", b"not-json", b"[]", b'{"status":"matched","status":"failed"}', b'{"status":"matched"}'])
def test_probe_rejects_malformed_response(monkeypatch, tmp_path, stdout):
    monkeypatch.setattr(mcp_verification.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(mcp_verification.process, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, error=None, stdout=stdout, stderr=b""))
    assert mcp_verification.probe(sys.executable, str(tmp_path))["error_kind"] == "mcp_probe_invalid_response"


def test_probe_does_not_forward_git_injection_or_model_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_verification.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setenv("GIT_DIR", "/fake-isolated-fixture")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-sensitive-key")
    captured = []

    def run(*_args, **options):
        captured.append(options["env"])
        return SimpleNamespace(returncode=3, error="missing", stdout=b"", stderr=b"")

    monkeypatch.setattr(mcp_verification.process, "run", run)
    mcp_verification.probe(sys.executable, str(tmp_path))
    assert "GIT_DIR" not in captured[0] and "OPENAI_API_KEY" not in captured[0]


def test_probe_rejects_invalid_operator_inputs_before_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_verification.process, "run", lambda *_args, **_kwargs: pytest.fail("no execution"))
    for python in (None, True, "relative-python", str(tmp_path)):
        assert mcp_verification.probe(python, str(tmp_path))["exit"] == 2
    for timeout in (True, 0, 61):
        assert mcp_verification.probe(sys.executable, str(tmp_path), timeout=timeout)["exit"] == 2


def test_initialize_identity_rejects_conflicting_or_duplicate_metadata():
    identity = "AMU_RUNTIME_IDENTITY=" + json.dumps(mcp_manifest.public_identity())
    assert mcp_verification._initialize_identity({"instructions": identity}) is not None
    assert mcp_verification._initialize_identity({"instructions": identity + "\n" + identity}) is None
    assert mcp_verification._initialize_identity({"instructions": "AMU_RUNTIME_IDENTITY=[]"}) is None
