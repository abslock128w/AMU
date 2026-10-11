"""Trusted process configuration must stop model credential discovery first."""

from types import SimpleNamespace

import pytest

from amu import keys, outbound, state
from amu.harness import chat, model, providers


@pytest.fixture(autouse=True)
def credential_environment(monkeypatch):
    for name in (
        "AMU_DISABLE_MODELS", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "AMU_PROVIDER", "AMU_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def unexpected_discovery(*args, **kwargs):
    pytest.fail("disabled models must not discover credentials or create a provider client")


def assert_disabled():
    credential = keys.credential()
    assert {name: credential[name] for name in ("provider", "family", "source")} == {
        "provider": None, "family": None, "source": "absent",
    }
    assert not credential.get("api_key")
    assert keys.source() == "absent"
    assert keys.resolve() is None
    assert keys.available() is False
    assert model.available() is False
    assert model.credential() == credential


@pytest.mark.parametrize("environment", [
    {"ANTHROPIC_API_KEY": "synthetic-anthropic-key"},
    {"GEMINI_API_KEY": "synthetic-gemini-key"},
    {"GOOGLE_API_KEY": "synthetic-google-key", "AMU_PROVIDER": "gemini"},
    {"OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_API_KEY": "synthetic-openai-key"},
    {"AMU_PROVIDER": "ollama"},
    {"AMU_PROVIDER": "lmstudio"},
    {"AMU_PROVIDER": "openrouter", "OPENAI_API_KEY": "synthetic-openrouter-key"},
    {"ANTHROPIC_API_KEY": "synthetic-anthropic-key", "GEMINI_API_KEY": "synthetic-gemini-key",
     "OPENAI_BASE_URL": "https://example.invalid/v1", "AMU_MODEL": "synthetic-model"},
])
def test_disable_switch_precedes_environment_credentials(monkeypatch, environment):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setattr(keys, "_read", unexpected_discovery)
    monkeypatch.setattr(keys, "_ant_profile", unexpected_discovery)
    assert_disabled()


@pytest.mark.parametrize("record", [
    {"provider": "anthropic", "family": "anthropic", "api_key": "synthetic-stored-key"},
    {"anthropic_api_key": "synthetic-legacy-key"},
    {"provider": "bedrock", "family": "anthropic", "region": "synthetic-region"},
    {"provider": "vertex", "family": "anthropic", "project_id": "synthetic-project", "region": "synthetic-region"},
])
def test_disable_switch_never_reads_stored_or_ambient_credentials(monkeypatch, record):
    keys._write(record)
    path = keys._path()
    before = path.read_bytes()
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setattr(keys, "_read", unexpected_discovery)
    monkeypatch.setattr(keys, "_ant_profile", unexpected_discovery)
    assert_disabled()
    assert path.read_bytes() == before


def test_disable_switch_does_not_probe_ant_profile(monkeypatch):
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setattr(keys, "_read", lambda: {})
    monkeypatch.setattr(keys.shutil, "which", unexpected_discovery)
    monkeypatch.setattr(keys.subprocess, "run", unexpected_discovery)
    assert_disabled()


def test_disabled_model_request_returns_manual_without_outbound(monkeypatch, tmp_path):
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient-key")
    monkeypatch.setattr(keys, "_read", unexpected_discovery)
    monkeypatch.setattr(keys, "_ant_profile", unexpected_discovery)
    monkeypatch.setattr(model, "client", unexpected_discovery)
    monkeypatch.setattr(model.outbound, "Policy", unexpected_discovery)
    monkeypatch.setattr(providers, "make_client", unexpected_discovery)

    result = model.ask_json("synthetic system", "synthetic request", repo=str(tmp_path))

    assert result["decision"] == "manual"
    assert result["escalate"] is True
    assert result["confidence"] == 0.0
    assert result["evidence"] == []


@pytest.mark.parametrize("value", [None, "", "0", "true", "TRUE", "yes", " 1", "1 ", "01"])
def test_only_exact_one_disables_regular_credential_resolution(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("AMU_DISABLE_MODELS", value)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-regular-key")
    monkeypatch.setattr(keys, "_read", unexpected_discovery)
    monkeypatch.setattr(keys, "_ant_profile", unexpected_discovery)

    assert keys.source() == "env"
    assert keys.resolve() == "synthetic-regular-key"
    assert keys.available() is True


@pytest.mark.parametrize("credential", [
    {"provider": None, "family": None, "source": "absent"},
    {"provider": "anthropic", "family": "anthropic", "api_key": "synthetic-key"},
    {"provider": "bedrock", "family": "anthropic", "region": "synthetic-region"},
    {"provider": "vertex", "family": "anthropic", "project_id": "synthetic-project", "region": "synthetic-region"},
    {"provider": "foundry", "family": "anthropic", "resource": "synthetic-resource", "api_key": "synthetic-key"},
    {"provider": "openai-compatible", "family": "openai", "base_url": "https://example.invalid/v1"},
    {"provider": "gemini", "family": "gemini", "base_url": "https://example.invalid/models"},
])
def test_disabled_factory_refuses_every_provider_before_initialization(monkeypatch, credential):
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setattr(providers, "validate_endpoint", unexpected_discovery)
    monkeypatch.setattr(providers, "_sdk_http_client", unexpected_discovery)
    monkeypatch.setattr(providers, "OpenAICompatClient", unexpected_discovery)
    monkeypatch.setattr(providers, "GeminiClient", unexpected_discovery)

    with pytest.raises(providers.ProviderError, match="AMU_DISABLE_MODELS") as caught:
        providers.make_client(credential)

    assert caught.value.retryable is False
    assert "synthetic" not in str(caught.value)


def test_direct_model_client_cannot_fall_back_to_ambient_sdk(monkeypatch):
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-ambient-key")
    monkeypatch.setattr(keys, "_read", unexpected_discovery)
    monkeypatch.setattr(keys, "_ant_profile", unexpected_discovery)
    monkeypatch.setattr(providers, "_sdk_http_client", unexpected_discovery)

    with pytest.raises(providers.ProviderError, match="AMU_DISABLE_MODELS"):
        model.client()


@pytest.mark.parametrize("value", [None, "0", "true"])
def test_regular_model_factory_remains_available_without_exact_disable_flag(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("AMU_DISABLE_MODELS", value)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-compatible-key")
    calls = []
    client = object()

    def make_client(base_url, api_key, provider):
        calls.append((base_url, api_key, provider))
        return client

    monkeypatch.setattr(providers, "OpenAICompatClient", make_client)

    assert model.client() is client
    assert calls == [("https://example.invalid/v1", "synthetic-compatible-key", "openai-compatible")]


def test_disable_switch_blocks_preexisting_outbound_policy_before_payload_access(monkeypatch, tmp_path):
    state.write_json("config.json", {"outbound": {"enabled": True}}, str(tmp_path))
    policy = outbound.Policy(str(tmp_path))
    monkeypatch.setattr(policy, "clean", unexpected_discovery)
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")

    with pytest.raises(outbound.PolicyError, match="AMU_DISABLE_MODELS"):
        policy.prepare({"messages": "synthetic private request"})


def test_disable_switch_blocks_file_read_before_policy_probe(monkeypatch, tmp_path):
    (tmp_path / "source.py").write_text("synthetic private source\n")
    policy = outbound.Policy(str(tmp_path))
    monkeypatch.setattr(policy, "denied", unexpected_discovery)
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")

    result = policy.read("source.py", 1, 1)

    assert "blocked" in result and "AMU_DISABLE_MODELS" in result
    assert "synthetic private source" not in result


def test_disable_switch_stops_chat_with_preexisting_injected_client(monkeypatch, tmp_path):
    monkeypatch.setattr(keys, "_ant_profile", lambda: False)
    client = SimpleNamespace(messages=SimpleNamespace(create=unexpected_discovery))
    assistant = chat.Assistant(str(tmp_path), stream=False, client=client)
    monkeypatch.setenv("AMU_DISABLE_MODELS", "1")

    with pytest.raises(outbound.PolicyError, match="AMU_DISABLE_MODELS"):
        assistant._call(client, [])


@pytest.mark.parametrize("value", [None, "0", "true"])
def test_regular_outbound_preparation_and_read_remain_available(monkeypatch, tmp_path, value):
    if value is not None:
        monkeypatch.setenv("AMU_DISABLE_MODELS", value)
    (tmp_path / "source.py").write_text("public source\n")
    policy = outbound.Policy(str(tmp_path))
    monkeypatch.setattr(policy, "denied", lambda name: False)

    assert policy.prepare({"messages": "public request"}) == {"messages": "public request"}
    assert "public source" in policy.read("source.py", 1, 1)
