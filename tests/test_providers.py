import builtins
import json
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from amu import keys
from amu.harness import chat, model, providers


def test_set_credential_validation():
    with pytest.raises(ValueError):
        keys.set_credential("vertex")            # needs project + region
    with pytest.raises(ValueError):
        keys.set_credential("openai-compatible")  # needs base_url + model
    with pytest.raises(ValueError):
        keys.set_credential("nope")
    keys.set_credential("ollama", model="qwen2.5-coder")
    c = keys.credential()
    assert c["family"] == "openai" and c["base_url"] == "http://localhost:11434/v1" and c["model"] == "qwen2.5-coder" and keys.available()


def test_env_openai_base_url_selects_openai_family(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("AMU_MODEL", "local-model")
    c = keys.credential()
    assert c["family"] == "openai" and c["source"] == "env" and model.model_id() == "local-model"
    assert model.request_kwargs(c) == {}  # no Anthropic-only params for other families


def test_missing_or_read_only_credential_home_is_treated_as_model_off(monkeypatch):
    monkeypatch.setattr(keys, "home", lambda: (_ for _ in ()).throw(OSError("read-only home")))
    monkeypatch.setattr(keys, "_ant_profile", lambda: False)

    assert keys.credential()["source"] == "absent"


def test_anthropic_env_keeps_effort(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-x-000000000000")
    assert model.request_kwargs(keys.credential(), "high") == {"output_config": {"effort": "high"}}


def test_gemini_credential_uses_native_defaults_and_masks_key():
    secret = "AIza-gemini-file-secret-1234"
    keys.set_credential("gemini", api_key=secret)

    c = keys.credential()
    assert c == {
        "provider": "gemini",
        "family": "gemini",
        "api_key": secret,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "model": "gemini-flash-latest",
        "source": "file",
    }
    assert keys.masked().endswith("…1234 · https://generativelanguage.googleapis.com/v1beta/models")
    assert secret not in keys.masked()


def test_gemini_env_key_selects_gemini_without_reusing_google_key(monkeypatch):
    monkeypatch.setattr(keys, "_ant_profile", lambda: False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-gemini-env-5678")
    c = keys.credential()
    assert c["provider"] == "gemini"
    assert c["family"] == "gemini"
    assert c["api_key"] == "AIza-gemini-env-5678"
    assert c["model"] == "gemini-flash-latest"
    assert c["base_url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert c["source"] == "env"

    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-google-general-key")
    assert keys.credential()["source"] == "absent"


def test_gemini_can_explicitly_use_google_api_key(monkeypatch):
    monkeypatch.setenv("AMU_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-google-gemini-9012")

    c = keys.credential()
    assert c["provider"] == "gemini"
    assert c["api_key"] == "AIza-google-gemini-9012"
    assert c["source"] == "env"


def test_legacy_gemini_compat_record_is_migrated_to_native_api():
    keys._write(
        {
            "provider": "gemini",
            "family": "openai",
            "api_key": "AIza-legacy-key-1234",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3.8-flash",
        }
    )

    c = keys.credential()
    assert c["family"] == "gemini"
    assert c["base_url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert c["model"] == "gemini-flash-latest"


def test_openai_message_and_tool_mapping():
    tools = chat.tool_definitions()[:1]
    ot = providers.to_openai_tools(tools)
    assert ot[0]["type"] == "function" and ot[0]["function"]["name"] == "graph_search"
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": [NS(type="text", text="calling"), NS(type="tool_use", id="c1", name="graph_search", input={"query": "q"})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}]}]
    om = providers.to_openai_messages([{"type": "text", "text": "SYS"}], msgs)
    assert om[0] == {"role": "system", "content": "SYS"} and om[2]["tool_calls"][0]["function"]["name"] == "graph_search"
    assert om[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


def test_openai_response_mapping():
    r = providers.from_openai_response({"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "c9", "function": {"name": "graph_def", "arguments": "{\"symbol\": \"x\"}"}}]}}]})
    assert r.stop_reason == "tool_use" and r.content[0].type == "tool_use" and r.content[0].input == {"symbol": "x"}
    assert providers.from_openai_response({"choices": [{"finish_reason": "stop", "message": {"content": "done"}}]}).stop_reason == "end_turn"


def test_chat_loop_over_openai_compatible_adapter(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    keys.set_credential("openai-compatible", base_url="http://fake/v1", model="m")
    script = [{"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "graph_def", "arguments": "{\"symbol\": \"classify_consumers\"}"}}]}}]},
              {"choices": [{"finish_reason": "stop", "message": {"content": "classify_consumers is a function in amu/classify.py"}}]}]
    seen = []

    def fake_post(url, body, headers, timeout=600):
        seen.append((url, body))
        return script.pop(0)
    monkeypatch.setattr(providers, "_post_json", fake_post)
    a = chat.Assistant(str(tmp_path), out=lambda t: None, stream=True)   # stream requested, adapter has none → falls back
    ans = a.ask("what is classify_consumers?")
    assert "amu/classify.py" in ans and seen[0][0] == "http://fake/v1/chat/completions"
    assert seen[1][1]["messages"][-1]["role"] == "tool" and "output_config" not in seen[0][1]


def test_gemini_chat_uses_native_endpoint_key_and_tool_round_trip(fake_entire, tmp_path, monkeypatch):
    """Gemini must match the native request shape verified by the user's curl."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    secret = "AIza-gemini-request-key-2468"
    keys.set_credential("gemini", api_key=secret)
    script = [
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "c1",
                                    "name": "graph_def",
                                    "args": {"symbol": "classify_consumers"},
                                }
                            }
                        ]
                    },
                }
            ]
        },
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Gemini can answer this."}]}}]},
    ]
    seen = []

    def fake_post(url, body, headers, timeout=600):
        seen.append((url, body, headers))
        return script.pop(0)

    monkeypatch.setattr(providers, "_post_json", fake_post)
    answer = chat.Assistant(str(tmp_path), out=lambda _t: None, stream=False).ask("hello")

    assert answer == "Gemini can answer this."
    assert len(seen) == 2
    url, body, headers = seen[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    assert headers == {"x-goog-api-key": secret}
    assert body["generationConfig"] == {"maxOutputTokens": 16000}
    assert body["systemInstruction"] == {"parts": [{"text": chat.ASSISTANT_SYSTEM}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "<context>" + chat.Assistant(str(tmp_path))._context() + "</context>\n\nhello"}]}
    ]
    assert body["tools"] == providers.to_gemini_tools(chat.tool_definitions())
    assert seen[1][0] == url and seen[1][2] == headers
    response_part = seen[1][1]["contents"][-1]["parts"][0]["functionResponse"]
    assert response_part["id"] == "c1" and response_part["name"] == "graph_def"
    assert secret not in json.dumps(seen[0][1])


def test_gemini_message_and_response_mapping():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [NS(type="text", text="calling"), NS(type="tool_use", id="c1", name="graph_search", input={"query": "q"})],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}]},
    ]
    contents = providers.to_gemini_contents(messages)
    assert contents[0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert contents[1]["parts"][1] == {"functionCall": {"id": "c1", "name": "graph_search", "args": {"query": "q"}}}
    assert contents[2]["parts"][0]["functionResponse"] == {"id": "c1", "name": "graph_search", "response": {"result": {}}}

    response = providers.from_gemini_response(
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}, {"functionCall": {"id": "c2", "name": "graph_def", "args": {"symbol": "x"}}}]}}]}
    )
    assert response.stop_reason == "tool_use"
    assert response.content[0].text == "ok"
    assert response.content[1].id == "c2" and response.content[1].input == {"symbol": "x"}


def test_openai_compatible_chat_does_not_require_anthropic_sdk(tmp_path, monkeypatch):
    """A base install can use a local OpenAI-compatible provider without Anthropic installed."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    keys.set_credential("openai-compatible", base_url="http://fake/v1", model="m")
    original_import = builtins.__import__

    def without_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("anthropic deliberately unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_anthropic)
    reply = NS(content=[NS(type="text", text="local model reply")], stop_reason="end_turn")
    client = NS(messages=NS(create=lambda **_kwargs: reply))
    a = chat.Assistant(str(tmp_path), out=lambda _t: None, stream=False, client=client)
    assert a.ask("hello") == "local model reply"


def test_openai_compatible_json_model_does_not_require_anthropic_sdk(monkeypatch):
    keys.set_credential("openai-compatible", base_url="http://fake/v1", model="m")
    original_import = builtins.__import__

    def without_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("anthropic deliberately unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_anthropic)
    expected = {"decision": "proceed", "confidence": 0.5, "escalate": False, "evidence": [], "next": "review"}
    reply = NS(content=[NS(type="text", text=json.dumps(expected))], stop_reason="end_turn")
    monkeypatch.setattr(model, "client", lambda: NS(messages=NS(create=lambda **_kwargs: reply)))
    assert model.ask_json("system", "user") == expected


def test_provider_error_is_reported_not_raised(fake_entire, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    keys.set_credential("ollama", model="m")
    monkeypatch.setattr(providers, "_post_json", lambda *a, **k: (_ for _ in ()).throw(providers.ProviderError("http://localhost:11434/v1 unreachable")))
    a = chat.Assistant(str(tmp_path), out=lambda t: None, stream=False)
    assert "unreachable" in a.ask("x")


def test_cli_key_set_provider_and_status():
    r = subprocess.run([sys.executable, "-m", "amu.cli", "key", "set", "--provider", "ollama", "--model", "llama3.1", "--json"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    s = json.loads(subprocess.run([sys.executable, "-m", "amu.cli", "key", "status", "--json"], capture_output=True, text=True).stdout)
    assert s["provider"] == "ollama" and s["base_url"].endswith("11434/v1") and "openrouter" in s["providers"]
