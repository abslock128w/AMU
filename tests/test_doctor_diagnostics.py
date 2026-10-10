"""Doctor never turns a failed or unexecuted required probe into readiness."""

import json

import pytest
from typer.testing import CliRunner

from amu import cli, entire, keys
from amu.services import diagnostics as diagnostic_service

_REAL_CREDENTIAL = keys.credential
_REAL_DIAGNOSTIC_CREDENTIAL = diagnostic_service._credential_record


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(entire, "available", lambda: True)
    monkeypatch.setattr(entire, "graph_available", lambda: True)
    monkeypatch.setattr(entire, "version", lambda: "fixture-entire-1")
    monkeypatch.setattr(entire, "graph_version", lambda: "fixture-graph-1")
    monkeypatch.setattr(entire, "doctor", lambda: {"healthy": True, "rc": 0})
    monkeypatch.setattr(keys, "credential", lambda: {"provider": None, "source": "absent"})
    monkeypatch.setattr(cli.state, "config", lambda *_: {})
    return monkeypatch


def _invoke(repo, *options):
    return CliRunner().invoke(cli.app, ["doctor", "--repo", str(repo), *options, "--json"])


def test_doctor_graph_health_failure_is_nonzero_even_when_binary_versions_exist(tmp_path, provider):
    provider.setattr(entire, "doctor", lambda: {"error": "health write-test blocked", "rc": 1})
    result = _invoke(tmp_path)
    assert result.exit_code == 3, result.output
    packet = json.loads(result.stdout)
    assert packet["ready"] is False
    assert packet["status"] == "not_ready"
    assert next(row for row in packet["checks"] if row["name"] == "graph_health")["status"] == "failed"


def _repo_ready():
    return {"schema_version": 1, "ready": True, "category": "READY", "checks": [
        {"name": "repository_access", "status": "passed", "required": True},
        {"name": "git_repository", "status": "passed", "required": True},
        {"name": "graph_index", "status": "passed", "required": True},
        {"name": "graph_freshness", "status": "passed", "required": True},
    ]}


@pytest.fixture
def diagnostics(provider):
    from amu.services import diagnostics

    provider.setattr(diagnostics, "_repository_probe", lambda *_: _repo_ready())
    provider.setattr(diagnostics, "_credential_record", lambda: {"provider": None, "source": "absent"})
    provider.setattr(diagnostics, "_identity", lambda: {
        "amu_version": "0.2.0", "distribution_version": "0.2.0", "python_version": "3.11",
        "executable": "/selected/python", "module": "/selected/amu/__init__.py", "build_sha256": "a" * 64,
    })
    return diagnostics


def test_doctor_reports_package_runtime_and_preserves_legacy_fields(tmp_path, diagnostics):
    result = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    packet = json.loads(result.stdout)
    assert packet["ready"] is True and packet["status"] == "ready"
    assert packet["identity"]["amu_version"] == "0.2.0"
    assert packet["identity"]["build_sha256"] == "a" * 64
    assert set(("entire", "graph", "doctor", "anthropic_key_present", "anthropic_key_source", "provider", "model", "unparsed", "install")) <= set(packet)
    assert packet["mcp"]["requested"] is False


def test_doctor_does_not_create_amu_or_write_any_workflow_state(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(cli.state, "write_json", lambda *_args, **_kwargs: pytest.fail("doctor must not persist"))
    monkeypatch.setattr(cli.state, "config", lambda *_args: pytest.fail("state.config creates .amu"))
    result = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".amu").exists()


def test_doctor_models_disabled_prevents_credential_discovery(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setattr(diagnostics, "_credential_record", lambda: pytest.fail("disabled doctor must not discover credentials"))
    result = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    packet = json.loads(result.stdout)
    assert packet["anthropic_key_present"] is False
    assert packet["anthropic_key_source"] == "disabled"


def test_doctor_unknown_or_unexecuted_repository_probe_never_claims_ready(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_repository_probe", lambda *_: {"ready": False, "checks": [
        {"name": "graph_index", "status": "not_run", "required": True},
        {"name": "graph_freshness", "status": "unknown", "required": True},
    ]})
    result = _invoke(tmp_path)
    assert result.exit_code == 3
    packet = json.loads(result.stdout)
    assert packet["ready"] is False


def test_doctor_mcp_is_optional_and_base_never_probes_server(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_mcp_probe", lambda *_: pytest.fail("MCP was not requested"))
    assert _invoke(tmp_path).exit_code == 0


def test_requested_mcp_missing_dependency_or_failed_protocol_blocks_ready(tmp_path, diagnostics, monkeypatch):
    calls = []

    def probe(*args):
        calls.append(args)
        return {"status": "unavailable", "error_kind": "mcp_dependency_missing", "exit": 3}

    monkeypatch.setattr(diagnostics, "_mcp_probe", probe)
    result = _invoke(tmp_path, "--mcp", "--mcp-python", "/selected/python")
    assert result.exit_code == 3
    packet = json.loads(result.stdout)
    assert packet["ready"] is False
    assert packet["mcp"]["requested"] is True
    assert calls == [("/selected/python", str(tmp_path))]


def test_mcp_runtime_option_without_request_is_input_error(tmp_path, diagnostics):
    result = _invoke(tmp_path, "--mcp-python", "/selected/python")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["exit"] == 2


def test_mcp_probe_uses_absolute_operator_selection_for_relative_repo(tmp_path, diagnostics, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def probe(python, repo):
        calls.append((python, repo))
        return {
            "status": "matched", "manifest": {"match": True}, "protocol": {"initialized": True},
            "runtime": {"amu_version": "0.2.0", "build_sha256": "a" * 64},
        }

    monkeypatch.setattr(diagnostics, "_mcp_probe", probe)
    result = CliRunner().invoke(cli.app, ["doctor", "--repo", ".", "--mcp", "--mcp-python", "/selected/python", "--json"])
    assert result.exit_code == 0, result.output
    assert calls == [("/selected/python", str(tmp_path))]


def test_doctor_diagnostic_failures_never_print_raw_provider_secrets(tmp_path, diagnostics, monkeypatch):
    secret = "sk-ant-private-doctor-key"
    endpoint = "https://private.endpoint.example/v1"
    monkeypatch.setattr(entire, "doctor", lambda: {"error": f"permission denied: {secret} {endpoint}", "rc": 1})
    monkeypatch.setattr(diagnostics, "_credential_record", lambda: {"provider": "anthropic", "source": "env", "api_key": secret, "base_url": endpoint, "model": secret})
    result = _invoke(tmp_path)
    assert result.exit_code == 3
    assert secret not in result.stdout + result.stderr
    assert endpoint not in result.stdout + result.stderr
    packet = json.loads(result.stdout)
    assert packet["anthropic_key_present"] is True


def test_doctor_unexpected_probe_exception_is_failed_not_success(tmp_path, diagnostics, monkeypatch):
    def failed():
        raise RuntimeError("private details")

    monkeypatch.setattr(entire, "doctor", failed)
    result = _invoke(tmp_path)
    assert result.exit_code == 3
    assert "private details" not in result.stdout + result.stderr


def test_human_doctor_distinguishes_not_ready_from_tooling_present(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(entire, "doctor", lambda: {"error": "health failed", "rc": 1})
    result = CliRunner().invoke(cli.app, ["doctor", "--repo", str(tmp_path)])
    assert result.exit_code == 3
    assert "not ready" in result.stdout.lower()
    assert "graph_health" in result.stdout


@pytest.mark.parametrize("health", [
    {}, None, {"rc": 1}, {"rc": True}, {"healthy": False}, {"status": "degraded"},
    {"checks": [{"required": True, "status": "not_run"}]},
    {"checks": [{"status": "failed"}]},
])
def test_shared_health_predicate_rejects_failed_or_unexecuted_probes(diagnostics, health):
    assert diagnostics.provider_health_ready(health) is False


def test_contradictory_required_readiness_row_cannot_hide_unknown_status(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_repository_probe", lambda *_: {"ready": True, "checks": [
        {"name": "graph_freshness", "status": "unknown", "ready": True, "required": True},
    ]})
    assert _invoke(tmp_path).exit_code == 3


def test_mcp_matched_flag_does_not_hide_an_uninitialized_protocol(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_mcp_probe", lambda *_: {
        "status": "matched", "manifest": {"match": True}, "protocol": {"initialized": False},
        "runtime": {"amu_version": "0.2.0", "build_sha256": "a" * 64},
    })
    assert _invoke(tmp_path, "--mcp").exit_code == 3


def test_mcp_old_build_with_same_semantic_version_cannot_claim_ready(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_mcp_probe", lambda *_: {
        "status": "matched", "manifest": {"match": True}, "protocol": {"initialized": True},
        "runtime": {"amu_version": "0.2.0", "build_sha256": "b" * 64},
    })
    assert _invoke(tmp_path, "--mcp").exit_code == 3


def test_repo_inspect_is_a_read_only_thin_preflight_adapter(tmp_path, monkeypatch):
    from amu import repository_diagnostics

    calls = []
    packet = {"ready": False, "checks": [], "error_kind": "repository_not_indexed"}
    monkeypatch.setattr(repository_diagnostics, "repo_preflight", lambda path: calls.append(path) or packet)
    monkeypatch.setattr(cli.workspace, "add", lambda *_args: pytest.fail("inspect must not register"))
    result = CliRunner().invoke(cli.app, ["repo", "inspect", str(tmp_path), "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout) == packet
    assert calls == [str(tmp_path)]
    assert not (tmp_path / ".amu").exists()


def test_repo_inspect_requires_an_explicit_path():
    result = CliRunner().invoke(cli.app, ["repo", "inspect", "--json"])
    assert result.exit_code == 2


def test_successful_mcp_probe_requires_matching_runtime_manifest_and_protocol(tmp_path, diagnostics, monkeypatch):
    monkeypatch.setattr(diagnostics, "_mcp_probe", lambda *_: {
        "status": "matched", "manifest": {"match": True}, "protocol": {"initialized": True},
        "runtime": {"amu_version": "0.2.0", "build_sha256": "a" * 64},
    })
    packet = json.loads(_invoke(tmp_path, "--mcp").stdout)
    assert packet["ready"] is True
    assert packet["mcp"]["repository_allowlist_checked"] is False


def test_doctor_never_creates_global_credentials_home(tmp_path, diagnostics, monkeypatch):
    home = tmp_path / "missing-amu-home"
    monkeypatch.setenv("AMU_HOME", str(home))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AMU_PROVIDER", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(keys, "credential", _REAL_CREDENTIAL)
    monkeypatch.setattr(diagnostics, "_credential_record", _REAL_DIAGNOSTIC_CREDENTIAL)
    monkeypatch.setattr(keys, "_ant_profile", lambda: False)
    assert _invoke(tmp_path).exit_code == 0
    assert not home.exists()


def test_doctor_does_not_probe_ambient_auth_profile(tmp_path, diagnostics, monkeypatch):
    home = tmp_path / "credentials-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("AMU_HOME", str(home))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "AMU_PROVIDER", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(keys, "credential", _REAL_CREDENTIAL)
    monkeypatch.setattr(diagnostics, "_credential_record", _REAL_DIAGNOSTIC_CREDENTIAL)
    calls = []
    monkeypatch.setattr(keys, "_ant_profile", lambda: calls.append(True) or False)
    assert _invoke(tmp_path).exit_code == 0
    assert not calls


def test_default_doctor_never_creates_workspace_home(tmp_path, diagnostics, monkeypatch):
    home = tmp_path / "missing-workspace"
    monkeypatch.setenv("AMU_HOME", str(home))
    monkeypatch.setattr(cli.workspace, "resolve", lambda *_args: pytest.fail("doctor must not implicitly access workspace state"))
    result = CliRunner().invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    assert not home.exists()


def test_existing_stored_key_presence_is_retained_without_printing_values(tmp_path, diagnostics, monkeypatch):
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    credential = root / "credentials.json"
    secret = "sk-ant-stored-private-key"
    endpoint = "https://private.endpoint.example/v1"
    credential.write_text(json.dumps({"provider": "anthropic", "api_key": secret, "base_url": endpoint, "model": "claude-opus-5"}))
    credential.chmod(0o600)
    monkeypatch.setenv("AMU_HOME", str(root))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "AMU_PROVIDER", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(diagnostics, "_credential_record", _REAL_DIAGNOSTIC_CREDENTIAL)
    result = _invoke(tmp_path)
    packet = json.loads(result.stdout)
    assert result.exit_code == 0
    assert packet["anthropic_key_present"] is True and packet["anthropic_key_source"] == "file"
    assert secret not in result.stdout + result.stderr and endpoint not in result.stdout + result.stderr


def test_wrong_mode_stored_credential_is_not_read_or_called_absent(tmp_path, diagnostics, monkeypatch):
    root = tmp_path / "credentials"
    root.mkdir(mode=0o700)
    credential = root / "credentials.json"
    credential.write_text('{"provider":"anthropic","api_key":"private"}')
    credential.chmod(0o644)
    monkeypatch.setenv("AMU_HOME", str(root))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "AMU_PROVIDER", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(diagnostics, "_credential_record", _REAL_DIAGNOSTIC_CREDENTIAL)
    result = _invoke(tmp_path)
    packet = json.loads(result.stdout)
    assert result.exit_code == 0
    assert packet["anthropic_key_present"] is None
    assert packet["anthropic_key_source"] == "not_inspected"


@pytest.mark.parametrize("status", ["unknown", "not_run", "not_executed", "blocked", "pending"])
def test_unknown_provider_health_status_cannot_be_overridden_by_a_healthy_flag(diagnostics, status):
    assert diagnostics.provider_health_ready({"healthy": True, "status": status}) is False


@pytest.mark.parametrize("health", [
    {"healthy": True, "status": "unsupported-health-label"},
    {"healthy": True, "status": True},
    {"healthy": True, "checks": "not_executed"},
    {"healthy": True, "checks": [{"required": True, "status": "passed", "healthy": False}]},
    {"healthy": True, "checks": [{"required": True, "status": "passed", "success": False}]},
    {"healthy": True, "checks": [{"required": True, "status": "passed", "available": False}]},
])
def test_malformed_or_contradictory_provider_health_cannot_claim_ready(diagnostics, health):
    assert diagnostics.provider_health_ready(health) is False


@pytest.mark.parametrize("status", ["unknown", "not_run", "not_executed", "blocked", "pending"])
def test_unknown_required_provider_check_blocks_ready_even_with_healthy_flag(diagnostics, status):
    health = {"healthy": True, "checks": [{"required": True, "healthy": True, "status": status}]}
    assert diagnostics.provider_health_ready(health) is False


def test_redaction_precedes_diagnostic_string_size_limit(diagnostics):
    secret = "private-known-key-value-across-boundary"
    value = "x" * 2030 + secret
    redacted = diagnostics._text(value, (secret,))
    assert "private-known-key" not in redacted
    assert len(redacted) <= 2048


def test_version_probe_does_not_expose_key_prefix_at_redaction_boundary(tmp_path, diagnostics, monkeypatch):
    secret = "private-known-key-value-across-boundary"
    monkeypatch.setattr(diagnostics, "_credential_record", lambda: {
        "provider": "anthropic", "source": "env", "api_key": secret,
    })
    monkeypatch.setattr(entire, "version", lambda: "x" * 2030 + secret)
    result = _invoke(tmp_path)
    assert result.exit_code == 0
    assert "private-known-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "oversized", "duplicate", "malformed"])
def test_unusable_credential_files_remain_unknown_without_writes(tmp_path, diagnostics, monkeypatch, kind):
    import os

    root = tmp_path / "credentials-home"
    root.mkdir(mode=0o700)
    credential = root / "credentials.json"
    original = b'{"provider":"anthropic","api_key":"private-doctor-file-key"}'
    if kind == "symlink":
        elsewhere = tmp_path / "elsewhere.json"
        elsewhere.write_bytes(original)
        elsewhere.chmod(0o600)
        credential.symlink_to(elsewhere)
    elif kind == "fifo":
        os.mkfifo(credential, 0o600)
    else:
        data = original
        if kind == "oversized":
            data = b" " * (64 * 1024 + 1) + original
        elif kind == "duplicate":
            data = b'{"provider":"anthropic","provider":"gemini","api_key":"private-doctor-file-key"}'
        elif kind == "malformed":
            data = b'{"provider":'
        credential.write_bytes(data)
        credential.chmod(0o600)
        if kind == "hardlink":
            os.link(credential, root / "credential-copy.json")
    before = {item.name: item.lstat() for item in root.iterdir()}
    monkeypatch.setenv("AMU_HOME", str(root))
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "AMU_PROVIDER", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(diagnostics, "_credential_record", _REAL_DIAGNOSTIC_CREDENTIAL)
    result = _invoke(tmp_path)
    packet = json.loads(result.stdout)
    assert result.exit_code == 0
    assert packet["anthropic_key_present"] is None
    assert packet["anthropic_key_source"] == "not_inspected"
    assert "private-doctor-file-key" not in result.stdout + result.stderr
    assert {item.name: item.lstat() for item in root.iterdir()} == before
