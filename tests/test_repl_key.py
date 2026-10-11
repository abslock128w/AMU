import subprocess
import sys


def _repl(stdin: str, cwd, env_extra=None):
    import os

    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "amu.cli"], input=stdin, cwd=cwd, capture_output=True, text=True, env=env, timeout=60
    )


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    return tmp_path


def test_banner_shows_model_off_with_guidance(fake_entire, tmp_path):
    r = _repl("/key\n", _repo(tmp_path))
    assert "○ model off" in r.stderr and "/key add" in r.stderr


def test_free_text_without_model_guides_to_key_add(fake_entire, tmp_path):
    r = _repl("what does this repo do\n", _repo(tmp_path))
    assert "○ model off" in r.stderr and "/key add" in r.stderr


def test_guided_add_turns_model_on(fake_entire, tmp_path):
    # /key add → provider 2 (ollama, no key) → model name → banner flips to on; nothing secret echoed
    r = _repl("/key add\n2\nqwen2.5-coder\n/key\n", _repo(tmp_path))
    assert "● model on" in r.stderr and "ollama" in r.stderr and "qwen2.5-coder" in r.stderr


def test_guided_add_anthropic_never_echoes_key(fake_entire, tmp_path):
    r = _repl("/key add\n1\nsk-ant-api03-secret-value-1234567890\n/key\n", _repo(tmp_path))
    assert "● model on" in r.stderr and "secret-value" not in r.stdout + r.stderr and "…7890" in r.stderr


def test_guided_add_gemini_uses_default_model_and_never_echoes_key(fake_entire, tmp_path):
    secret = "AIza-gemini-guided-secret-9012"
    r = _repl(f"/key add\ngemini\n{secret}\n/key\n", _repo(tmp_path))
    transcript = r.stdout + r.stderr
    assert "gemini" in transcript and "gemini-flash-latest" in transcript and "● model on" in transcript
    assert secret not in transcript and "…9012" in transcript


def test_banner_shows_model_on_when_env_key_present(fake_entire, tmp_path):
    r = _repl("/key\n", _repo(tmp_path), {"ANTHROPIC_API_KEY": "sk-ant-api03-env-000000000000"})
    assert "● model on" in r.stderr and "via env" in r.stderr


def test_key_remove_turns_model_off(fake_entire, tmp_path):
    r = _repl("/key add\n2\nm\n/key remove\n", _repo(tmp_path))
    assert r.stderr.count("● model on") >= 1 and "○ model off" in r.stderr.split("● model on")[-1]


def test_compact_start_hint_is_visible_on_launch_and_on_empty_enter(fake_entire, tmp_path):
    r = _repl("\n", _repo(tmp_path))
    assert r.stderr.count("Start: /tree") >= 2
    assert "AMU command guide" not in r.stderr


def test_unknown_command_echoes_what_was_typed_without_dumping_help(fake_entire, tmp_path):
    r = _repl("/wat\n", _repo(tmp_path))
    assert "Not a command: /wat" in r.stderr
    assert "Not a command: /unknown" not in r.stderr
    assert "AMU command guide" not in r.stderr


def test_bare_open_is_a_command_not_a_change(fake_entire, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "plan.py").write_text("x = 1\ny = 2\n")
    r = _repl("open src/plan.py\ncat src/plan.py 1 2\n", _repo(tmp_path))
    assert "amu plan --targets" not in r.stdout and "/cat src/plan.py" in r.stdout and "    2  y = 2" in r.stdout


def test_model_error_is_shown_in_repl(fake_entire, tmp_path, monkeypatch):
    from amu import keys

    keys.set_credential("openai-compatible", base_url="http://127.0.0.1:9/v1", model="m")
    r = _repl("what does this repo do\n", _repo(tmp_path))
    assert "✗ model call failed" in r.stdout + r.stderr and "unreachable" in r.stdout + r.stderr


def test_openrouter_model_must_be_namespaced():
    import pytest

    from amu import keys

    with pytest.raises(ValueError):
        keys.set_credential("openrouter", api_key="k", model="openai")
    keys.set_credential("openrouter", api_key="k", model="openai/gpt-4o")
