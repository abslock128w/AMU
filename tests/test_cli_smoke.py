import json
import subprocess
import sys


def _run(*args, cwd="."):
    return subprocess.run([sys.executable, "-m", "amu.cli", *args], cwd=cwd, capture_output=True, text=True)


def test_help_mentions_brand():
    r = _run("--help")
    assert r.returncode == 0 and "TWC Thugs" in r.stdout


def test_doctor_exit_3_with_install_hint_when_entire_missing(tmp_path):
    r = _run("doctor", "--json", cwd=tmp_path)
    assert r.returncode == 3 and "brew" in json.loads(r.stdout)["install"]


def test_doctor_never_prints_key_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    r = _run("doctor", "--json", cwd=tmp_path)
    assert "sk-ant-secret-value" not in r.stdout + r.stderr and json.loads(r.stdout)["anthropic_key_present"] is True


def test_map_requires_target(tmp_path):
    assert _run("map", cwd=tmp_path).returncode == 2


def test_map_with_fake_entire(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    r = _run("map", "--symbol", "amu/classify.py#classify_consumers", "--json", cwd=tmp_path)
    assert r.returncode == 0
    m = json.loads(r.stdout)
    assert m["resolution"]["sound"] > 0 and m["root"]["internal"] > 0 and "TWC" not in r.stdout
