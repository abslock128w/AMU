"""Result observations and request-local syntax reuse, never target execution."""

import ast
import difflib
import hashlib

import pytest

from amu.services.impact import PatchRequest, execute_patch
from amu.value_flow import analyze_repository


def trees(files):
    return {p: {"tree": ast.parse(s), "sha256": hashlib.sha256(s.encode()).hexdigest()} for p, s in files.items()}


def compare(files, after, changed="api.py#source"):
    return analyze_repository(trees(files), trees(after), {changed})


def test_parameterized_result_assertion_survives_missing_obligation(tmp_path):
    before = "def source(value):\n    while value:\n        value = value[1:]\n    return value\n"
    after = before.replace("value = value[1:]", "value = value[1:]\n    else:\n        break_here = True")
    # Loop change has no recognized changed expression; no loop model is added.
    (tmp_path / "api.py").write_text(before)
    (tmp_path / "test_api.py").write_text(
        'import pytest\nfrom api import source\n@pytest.mark.parametrize(("value", "expect"), [("", ""), ("x"*4, "")])\ndef test_source(value, expect):\n    assert source(value)==expect\n'
    )
    patch = "".join(
        difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/api.py", tofile="b/api.py")
    )
    report = execute_patch(str(tmp_path), PatchRequest(patch, base="worktree", section="recommended_checks"))
    row = report["details"]["items"][0]
    a = next(a for a in row["associations"] if a["entity_id"] == "api.py#source" and a.get("result_assertion"))
    assert a["result_assertion"]["assertion"]["location"]["line"] == 5
    assert a["result_assertion"]["changed_behavior_established"] is False
    assert not a["obligation_evidence"]
    assert row["verification_category"] == "related-result-assertion"
    assert row["behavioral_rank"] >= 3
    assert "changed-operation" in row["verification_gap"]


def test_parameter_marker_shadowing_and_arbitrary_decorators_are_unresolved():
    source = "def source():\n    return [1]\n"
    for header in ["import pytest\npytest=unknown\n", "import pytest\n"]:
        decorator = '@pytest.mark.parametrize("x", [1])' if "unknown" in header else "@unknown"
        files = {
            "api.py": source,
            "test_api.py": header
            + "from api import source\n"
            + decorator
            + "\ndef test_source(x):\n    assert source()[0]==1\n",
        }
        flow = compare(files, {**files, "api.py": source.replace("[1]", "None")})
        assert not flow["assertions"]


def test_related_result_negatives_and_guard_constraints():
    source = "def source():\n    return [1]\n"
    tests = """from api import source

def test_alias():
    saved=source
    value=saved()
    copy=value
    assert copy[0]==1

def test_guard():
    value=source()
    if value is None:
        return
    assert value[0]==1

def test_reassigned():
    value=source()
    value=[9]
    assert value[0]==9

def test_unrelated():
    source()
    assert 9==9

def test_dynamic(obj):
    assert obj.source()[0]==1

def test_escaped_alias():
    saved=source
    opaque(saved)
    assert saved()[0]==1

def test_short_circuit():
    assert True or source()

def test_join():
    value=source()
    if value is None:
        result=9
    else:
        result=value[0]
    assert result==9
"""
    files = {"api.py": source, "test_api.py": tests}
    flow = compare(files, {**files, "api.py": source.replace("[1]", "None")})
    assert {r["test_id"] for r in flow["assertions"]} == {"test_api.py::test_alias", "test_api.py::test_guard"}
    guard = next(r for r in flow["assertions"] if r["test_id"].endswith("test_guard"))
    assert guard["constraints"][0]["kind"] == "not-none"
    assert any("escaped" in r["reason"] for r in flow["uncertainties"])
    assert any("branch-dependent" in r["reason"] for r in flow["uncertainties"])


def test_handler_selects_a_literal_result_not_a_later_unrelated_assertion():
    files = {
        "api.py": 'def source():\n    raise ValueError("bad")\n',
        "test_api.py": "from api import source\ndef test_handled():\n    try:\n        source()\n    except ValueError:\n        value=9\n    assert value==9\n",
    }
    flow = compare(files, {**files, "api.py": files["api.py"].replace("ValueError", "TypeError")})
    assert not flow["assertions"]  # handler-local join is deliberately unresolved


def test_syntax_memo_preserves_all_flow_evidence_and_limits(monkeypatch):
    from amu import value_flow

    source = "def source():\n    return [1]\n"
    files = {
        "api.py": source,
        "client.py": "from api import source\ndef consume():\n    value=source()\n    return value[0]\n",
        "test_api.py": "from client import consume\ndef test_source():\n    assert consume()==1\n",
    }
    before = trees(files)
    after = {**before, "api.py": trees({"api.py": source.replace("[1]", "None")})["api.py"]}
    cached = analyze_repository(before, after, {"api.py#source"})
    original = value_flow.Resolver.lexical_facts
    monkeypatch.setattr(
        value_flow.Resolver,
        "lexical_facts",
        lambda self, node: (frozenset(value_flow._bindings(node)), value_flow._dynamic_namespace(node)),
    )
    uncached = analyze_repository(before, after, {"api.py#source"})
    assert cached == uncached
    monkeypatch.setattr(value_flow.Resolver, "lexical_facts", original)


def test_request_local_reuse_does_not_stale_dependencies_or_configuration(tmp_path):
    from dataclasses import replace

    source = "def source():\n    return [1]\n"
    (tmp_path / "api.py").write_text(source)
    (tmp_path / "client.py").write_text("from api import source\ndef consume():\n    return source()[0]\n")
    (tmp_path / "test_api.py").write_text("from client import consume\ndef test_source():\n    assert consume()==1\n")
    patch = "".join(
        difflib.unified_diff(
            source.splitlines(True),
            source.replace("[1]", "None").splitlines(True),
            fromfile="a/api.py",
            tofile="b/api.py",
        )
    )
    req = PatchRequest(patch, base="worktree", section="findings")
    packet = execute_patch(str(tmp_path), req)
    cursor = packet["retrieval"]["findings"]["cursor"]
    (tmp_path / "client.py").write_text("from api import source\ndef consume():\n    return 9\n")
    with pytest.raises(ValueError, match="stale"):
        execute_patch(str(tmp_path), replace(req, cursor=cursor))
    packet = execute_patch(str(tmp_path), req)
    cursor = packet["retrieval"]["findings"]["cursor"]
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    with pytest.raises(ValueError, match="stale"):
        execute_patch(str(tmp_path), replace(req, cursor=cursor))


def test_fast_uncertainty_dedup_keeps_the_original_truncation_record():
    from amu.change_analysis import _location
    from amu.value_flow import MAX_UNKNOWN, VERSION, Resolver

    r = Resolver(trees({"api.py": "def source():\n    return 1\n"}))
    node = ast.parse("call()").body[0]
    expected = []
    for reason in ["reason " + str(i) for i in range(MAX_UNKNOWN + 30)] + ["reason 1", "reason 199", "reason 900"]:
        row = {"path": "api.py", "location": _location(node), "reason": reason, "analyzer": VERSION}
        if row not in expected:
            if len(expected) < MAX_UNKNOWN:
                expected.append(row)
            elif not any(v["reason"].startswith("uncertainty limit") for v in expected):
                expected[-1] = {**row, "reason": "uncertainty limit reached; additional unresolved boundaries omitted"}
        r.unknown("api.py", node, reason)
    assert r.unknowns == expected


def test_marker_literal_limits_and_class_shadowing_do_not_promote():
    source = "def source():\n    return [1]\n"
    tests = [
        'import pytest\nfrom api import source\n@pytest.mark.parametrize("x", ["a"*100000000])\ndef test_source(x):\n    assert source()[0]==1\n',
        'import pytest\nfrom api import source\n@pytest.mark.parametrize("x", build())\ndef test_source(x):\n    assert source()[0]==1\n',
        'import pytest\nfrom api import source\nclass TestAPI:\n    pytest=unknown\n    @pytest.mark.parametrize("x", [1])\n    def test_source(self,x):\n        assert source()[0]==1\n',
    ]
    for test in tests:
        files = {"api.py": source, "test_api.py": test}
        flow = compare(files, {**files, "api.py": source.replace("[1]", "None")})
        assert not flow["assertions"]
        assert flow["uncertainties"]


def test_result_observation_never_attaches_to_a_broad_class_reference(tmp_path):
    before = "class API:\n    @property\n    def changed(self):\n        return 1\n    @property\n    def other(self):\n        return 9\n"
    (tmp_path / "api.py").write_text(before)
    (tmp_path / "test_api.py").write_text(
        "from api import API\ndef test_other():\n    value=API()\n    assert value.other==9\n"
    )
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(True),
            before.replace("return 1", "return 2").splitlines(True),
            fromfile="a/api.py",
            tofile="b/api.py",
        )
    )
    packet = execute_patch(str(tmp_path), PatchRequest(patch, base="worktree", section="recommended_checks"))
    assert all(not a.get("result_assertion") for r in packet["details"]["items"] for a in r.get("associations", []))


def test_marker_budget_does_not_displace_existing_summary_functions(monkeypatch):
    from amu import value_flow

    files = {
        "api.py": "def source():\n    return 1\n",
        "test_api.py": 'import pytest\nfrom api import source\n@pytest.mark.parametrize("value", [1])\ndef test_source(value):\n    assert source()==1\n',
    }
    monkeypatch.setattr(value_flow, "MAX_FUNCTIONS", 1)
    resolver = value_flow.Resolver(trees(files))
    assert list(resolver.functions) == ["api.py#source"]
    assert "test_api.py#test_source" in resolver.test_observers
