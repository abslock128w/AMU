import subprocess

import pytest
from typer.testing import CliRunner

from amu import cli


def repo(tmp_path, before, after, caller):
    (tmp_path / "api.py").write_text(before)
    (tmp_path / "client.py").write_text(caller)
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "api.py").write_text(after)
    return str(tmp_path)


@pytest.mark.parametrize("signature,call,status", [
    ("x, optional=1", "f(1)", "binding_valid"),
    ("x, required", "f(1)", "binding_invalid"),
    ("x, /", "f(x=1)", "binding_invalid"),
    ("*, x", "f(1)", "binding_invalid"),
    ("x", "f(*args)", "unknown"),
    ("x", "f(**kwargs)", "unknown"),
])
def test_argument_binding_is_distinct_from_relationship_confidence(tmp_path, signature, call, status):
    from amu.compatibility import assess
    path = repo(tmp_path, "def f(x): pass\n", f"def f({signature}): pass\n",
                f"from api import f\ndef consumer(*args, **kwargs):\n    {call}\n")
    result = assess(path, "api.py#f", "HEAD", ["client.py#consumer"])
    assert result["calls"][0]["status"] == status
    assert result["scope"] == "static_argument_binding_only"
    assert result["runtime_compatible"] is None


@pytest.mark.parametrize("prefix,body", [("from api import f as alias", "alias(1)"), ("import api as a", "a.f(1)")])
def test_static_import_aliases(tmp_path, prefix, body):
    from amu.compatibility import assess
    path = repo(tmp_path, "def f(x): pass\n", "def f(x, y): pass\n", f"{prefix}\ndef consumer():\n    {body}\n")
    result = assess(path, "api.py#f", "HEAD", ["client.py#consumer"])
    assert result["calls"][0]["newly_invalid"] is True


@pytest.mark.parametrize("caller", [
    "from api import f\ndef consumer(f):\n    f(1)\n",
    "from api import f\nf = other\ndef consumer():\n    f(1)\n",
    "from other import f\ndef consumer():\n    f(1)\n",
])
def test_shadowing_or_wrong_module_never_proves_binding(tmp_path, caller):
    from amu.compatibility import assess
    path = repo(tmp_path, "def f(x): pass\n", "def f(x, y): pass\n", caller)
    result = assess(path, "api.py#f", "HEAD", ["client.py#consumer"])
    assert not any(row["status"] == "binding_invalid" for row in result["calls"])
    assert result["unknown"]


def test_decorated_target_cannot_claim_signature_compatibility(tmp_path):
    from amu.compatibility import assess
    path = repo(tmp_path, "def f(x): pass\n", "@wrapper\ndef f(x, y): pass\n", "from api import f\ndef consumer():\n    f(1)\n")
    result = assess(path, "api.py#f", "HEAD", ["client.py#consumer"])
    assert result["unknown"] and not result["calls"]


def test_cli_reports_newly_invalid_call_without_graph_or_model(tmp_path):
    path = repo(tmp_path, "def f(x): pass\n", "def f(x, y): pass\n", "from api import f\ndef consumer():\n    f(1)\n")
    result = CliRunner().invoke(cli.app, ["compatibility", "--symbol", "api.py#f", "--caller", "client.py#consumer", "--repo", path, "--json"])
    assert result.exit_code == 1
    assert '"newly_invalid": true' in result.stdout
