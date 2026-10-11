import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from amu import state
from amu.harness import chat


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize("name", [".env", ".env.local", "key.pem", "id_rsa", ".amu/credentials.json"])
def test_sensitive_files_are_not_read(repo, name):
    path = repo / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("FAKE_PRIVATE_CONTENT")
    result = chat.Assistant(str(repo), stream=False)._read(name, 1, 10)
    assert "FAKE_PRIVATE_CONTENT" not in result
    assert "blocked" in result


def test_ignored_file_and_symlink_alias_are_blocked(repo):
    (repo / ".gitignore").write_text("private.txt\n")
    (repo / "private.txt").write_text("FAKE_PRIVATE_CONTENT")
    (repo / "alias.txt").symlink_to(repo / "private.txt")
    assistant = chat.Assistant(str(repo), stream=False)
    assert "FAKE_PRIVATE_CONTENT" not in assistant._read("private.txt", 1, 10)
    assert "FAKE_PRIVATE_CONTENT" not in assistant._read("alias.txt", 1, 10)


def test_outbound_boundary_redacts_context_and_tool_results(repo):
    assistant = chat.Assistant(str(repo), stream=False)
    calls = []
    assistant.messages = [{"role": "user", "content": 'api_key="FAKE_VALUE_123456"'},
                          {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                           "content": json.dumps({"file_path": ".env", "snippet": "HIDDEN_DATA"})}]}]
    client = NS(messages=NS(create=lambda **kw: calls.append(kw)))
    assistant._call(client, [])
    sent = json.dumps(calls)
    assert "FAKE_VALUE_123456" not in sent and "HIDDEN_DATA" not in sent


def test_disabled_policy_makes_no_provider_call(repo):
    state.write_json("config.json", {"outbound": {"enabled": False}}, str(repo))
    assistant = chat.Assistant(str(repo), stream=False)
    calls = []
    with pytest.raises(ValueError, match="disabled"):
        assistant._call(NS(messages=NS(create=lambda **kw: calls.append(kw))), [])
    assert calls == []


def test_custom_exclusions_and_limits(repo):
    from amu.outbound import Policy, PolicyError

    state.write_json("config.json", {"outbound": {"deny_paths": ["private/*"], "max_file_bytes": 20,
                                                 "max_payload_bytes": 50}}, str(repo))
    policy = Policy(str(repo))
    assert policy.clean({"file_path": "private/code.py", "snippet": "PRIVATE_TEXT"}) == {"redacted": "blocked file context"}
    (repo / "large.py").write_text("x" * 21)
    assert "blocked" in policy.read("large.py", 1, 2)
    with pytest.raises(PolicyError, match="size limit"):
        policy.prepare({"messages": "x" * 100})


def test_preview_is_redacted_and_makes_no_provider_call(repo, monkeypatch):
    from typer.testing import CliRunner

    from amu import cli
    from amu.harness import model

    monkeypatch.setattr(model, "client", lambda: pytest.fail("preview contacted provider"))
    result = CliRunner().invoke(cli.app, ["model-preview", 'password="FAKE_PASS_123"', "--repo", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["sent"] is False
    assert "FAKE_PASS_123" not in result.stdout and "[REDACTED]" in result.stdout


def test_planner_boundary_obeys_repo_policy_and_redacts(repo, monkeypatch):
    from amu.harness import model

    calls = []
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "credential", lambda: {"family": "openai", "api_key": "FAKE_KNOWN_CREDENTIAL"})

    def create(**kwargs):
        calls.append(kwargs)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(
            {"decision": "ask", "confidence": 0.5, "escalate": True, "evidence": [], "next": "review"}))])

    monkeypatch.setattr(model, "client", lambda: NS(messages=NS(create=create)))
    user = json.dumps({"api_key": "FAKE_SECRET_VALUE", "notes": "FAKE_KNOWN_CREDENTIAL"})
    assert model.ask_json("system", user, "test", str(repo))["decision"] == "ask"
    assert "FAKE_SECRET_VALUE" not in json.dumps(calls)
    assert "FAKE_KNOWN_CREDENTIAL" not in json.dumps(calls)
    state.write_json("config.json", {"outbound": {"enabled": False}}, str(repo))
    assert model.ask_json("system", user, "test", str(repo))["decision"] == "manual"
    assert len(calls) == 1


def test_sdk_blocks_are_redacted_without_mutating_history(repo):
    from amu.outbound import Policy

    original = NS(type="tool_use", name="read_lines", input={"api_key": "FAKE_SECRET"})
    cleaned = Policy(str(repo)).prepare({"messages": [original]})
    assert cleaned["messages"][0].input["api_key"] == "[REDACTED]"
    assert original.input["api_key"] == "FAKE_SECRET"


def test_private_key_redacted_before_line_slicing(repo):
    (repo / "sample.py").write_text("-----BEGIN PRIVATE KEY-----\nFAKE_BODY\n-----END PRIVATE KEY-----\n")
    assert "FAKE_BODY" not in chat.Assistant(str(repo), stream=False)._read("sample.py", 2, 2)


def test_corrupt_policy_blocks_instead_of_using_defaults(repo):
    from amu.outbound import Policy, PolicyError

    (repo / ".amu").mkdir()
    (repo / ".amu/config.json").write_text("{invalid")
    with pytest.raises(PolicyError, match="invalid outbound policy"):
        Policy(str(repo))


def test_provider_diagnostics_never_echo_remote_body_or_url(monkeypatch):
    import io
    import urllib.error

    from amu.harness import providers

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid?key=FAKE_URL_SECRET", 401, "bad", {},
                                     io.BytesIO(b"FAKE_REMOTE_SECRET"))

    monkeypatch.setattr(providers.urllib.request, "urlopen", fail)
    with pytest.raises(providers.ProviderError) as caught:
        providers._post_json("https://example.invalid", {}, {})
    assert "401" in str(caught.value)
    assert "FAKE" not in str(caught.value)


def test_disabled_policy_does_not_initialize_provider(repo, monkeypatch):
    from amu.harness import model

    state.write_json("config.json", {"outbound": {"enabled": False}}, str(repo))
    monkeypatch.setattr(model, "available", lambda: True)
    monkeypatch.setattr(model, "client", lambda: pytest.fail("disabled policy initialized provider"))
    assert "disabled" in chat.Assistant(str(repo), stream=False, out=lambda text: None).ask("hello")
    assert model.ask_json("system", "user", "test", str(repo))["decision"] == "manual"


def test_memory_blocked_path_never_enters_initial_context(repo):
    state.write_json("memory.json", {"entries": [{"file": ".env", "kind": "note", "text": "HIDDEN_MEMORY_CONTENT"}]}, str(repo))
    assert "HIDDEN_MEMORY_CONTENT" not in chat.Assistant(str(repo), stream=False)._context()
