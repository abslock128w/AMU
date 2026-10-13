"""Static-only regressions; fixtures are authored here, never upstream execution."""

import ast
import difflib
import hashlib

from amu.change_analysis import analyze_python_change
from amu.services.impact import PatchRequest, execute_patch


def patch(before, after, path):
    return "".join(
        difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/" + path, tofile="b/" + path)
    )


def test_property_accessor_identity_preserves_setter_and_deleter():
    before = "class Response:\n    @property\n    def value(self):\n        return 1\n    @value.setter\n    def value(self, new):\n        self._value = new\n"
    after = (
        before.replace("self._value = new", "self._value = None")
        + "    @value.deleter\n    def value(self):\n        del self._value\n"
    )
    changes = analyze_python_change("api.py", before, after)["modified_entities"]
    by_id = {r["id"]: r for r in changes}
    assert "api.py#Response.value@setter" in by_id
    assert "api.py#Response.value@deleter" in by_id
    assert "api.py#Response.value@getter" not in by_id
    assert by_id["api.py#Response.value@setter"]["before_location"]["line"] == 6
    assert by_id["api.py#Response.value@deleter"]["change"] == "added"


def test_ordinary_reexports_do_not_block_package_submodule(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("from .other import Other as Other\n")
    (tmp_path / "pkg/other.py").write_text("class Other:\n    pass\n")
    before = "def load():\n    return [1]\n"
    (tmp_path / "pkg/api.py").write_text(before)
    (tmp_path / "test_api.py").write_text("from pkg import api\ndef test_load():\n    assert api.load()[0] == 1\n")
    report = execute_patch(
        str(tmp_path),
        PatchRequest(
            patch(before, before.replace("[1]", "None"), "pkg/api.py"), base="worktree", section="recommended_checks"
        ),
    )
    assert [r["test_id"] for r in report["details"]["items"]] == ["test_api.py::test_load"]
    assert report["details"]["items"][0]["behavioral_rank"] <= 1


def analyze(before, after, changed):
    from amu.value_flow import analyze_repository

    def trees(files):
        return {p: {"tree": ast.parse(s), "sha256": hashlib.sha256(s.encode()).hexdigest()} for p, s in files.items()}

    return analyze_repository(trees(before), trees(after), changed)


def test_return_flow_guard_alias_and_unrelated_assertion():
    files = {
        "pkg/__init__.py": "from .api import load as fetch\n",
        "pkg/api.py": "def load():\n    return [1]\n",
        "client.py": """from pkg import fetch

def consume():
    value = fetch()
    alias = value
    return alias[0]

def guarded():
    value = fetch()
    if value is None:
        return 0
    return value[0]

def unrelated():
    fetch()
    return 8

def rebound():
    fetch = lambda: [8]
    return fetch()[0]

def shadowed(fetch):
    return fetch()[0]
""",
        "test_client.py": """from client import consume, unrelated
from pkg import fetch

def test_good():
    assert consume() == 1

def test_bad():
    assert unrelated() == 8

def test_alias():
    alias = fetch
    result = alias()
    assert result[0] == 1

def test_reassigned():
    result = fetch()
    result = [8]
    assert result[0] == 8
""",
    }
    after = {**files, "pkg/api.py": files["pkg/api.py"].replace("[1]", "None")}
    report = analyze(files, after, {"pkg/api.py#load"})
    rels = report["relationships"]
    assert any(r["consumer_id"] == "client.py#consume" and r["operation"] == "index" for r in rels)
    guarded = [r for r in rels if r["consumer_id"] == "client.py#guarded" and r["operation"] == "index"]
    assert guarded and guarded[0]["constraints"][0]["kind"] == "not-none"
    assert not any(r["consumer_id"] in {"client.py#shadowed", "client.py#rebound"} for r in rels)
    tests = report["assertions"]
    assert {r["test_id"] for r in tests} == {"test_client.py::test_good", "test_client.py::test_alias"}
    assert all(r["observed_execution"] is False for r in rels)
    assert report["summary_changes"][0]["after"]["may_return_none"] is True


def test_exception_identity_handler_and_cycle_limits():
    files = {
        "api.py": 'def load():\n    raise ValueError("bad")\n',
        "client.py": """from api import load

def handled():
    try:
        load()
    except ValueError:
        return 0

def propagated():
    load()

def shadowed(ValueError):
    try:
        load()
    except ValueError:
        return 0

def cycle():
    return cycle()
""",
    }
    report = analyze(files, {**files, "api.py": files["api.py"].replace("ValueError", "TypeError")}, {"api.py#load"})
    handled = [
        r for r in report["relationships"] if r["consumer_id"] == "client.py#handled" and r["operation"] == "exception"
    ]
    assert any(r["constraints"] and r["constraints"][0]["exception_id"] == "builtins.ValueError" for r in handled)
    assert any("cycle" in r["reason"] for r in report["uncertainties"])
    shadowed = [
        r for r in report["relationships"] if r["consumer_id"] == "client.py#shadowed" and r["operation"] == "exception"
    ]
    assert not any(r["constraints"] for r in shadowed)


def test_handled_exception_does_not_reappear_in_unrelated_wrapper_failure():
    files = {
        "api.py": 'def source():\n    raise ValueError("changed")\n',
        "client.py": 'from api import source\ndef wrapped():\n    try:\n        source()\n    except ValueError:\n        pass\n    raise ValueError("unrelated")\ndef outer():\n    wrapped()\n',
    }
    flow = analyze(files, {**files, "api.py": files["api.py"].replace("ValueError", "TypeError")}, {"api.py#source"})
    assert not any(
        r["consumer_id"] == "client.py#outer" and r["phase"] == "before" and r["operation"] == "exception"
        for r in flow["relationships"]
    )
    assert any(
        r["consumer_id"] == "client.py#outer" and r["phase"] == "after" and r["operation"] == "exception"
        for r in flow["relationships"]
    )


def test_imported_receiver_mutation_and_module_rebinding_abstain():
    files = {
        "api.py": "def source():\n    return [1]\n",
        "client.py": "import api\ndef consume():\n    api.source = lambda: [8]\n    return api.source()[0]\n",
        "test_api.py": "from api import source\nsource = lambda: [8]\ndef test_other():\n    assert source()[0] == 8\n",
    }
    flow = analyze(files, {**files, "api.py": files["api.py"].replace("[1]", "None")}, {"api.py#source"})
    assert not flow["assertions"]
    assert not any(r["consumer_id"] == "client.py#consume" for r in flow["relationships"])


def test_dependency_and_new_competing_module_invalidate_cursor(tmp_path):
    from dataclasses import replace

    import pytest

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("from .api import load as load\n")
    before = "def load():\n    return [1]\n"
    (tmp_path / "pkg/api.py").write_text(before)
    (tmp_path / "client.py").write_text("from pkg import load\ndef consume():\n    return load()[0]\n")
    (tmp_path / "test_client.py").write_text("from client import consume\ndef test_good():\n    assert consume()==1\n")
    request = PatchRequest(
        patch(before, before.replace("[1]", "None"), "pkg/api.py"), base="worktree", section="findings"
    )
    report = execute_patch(str(tmp_path), request)
    cursor = report["retrieval"]["findings"]["cursor"]
    (tmp_path / "client.py").write_text("from pkg import load\ndef consume():\n    return 8\n")
    with pytest.raises(ValueError, match="stale"):
        execute_patch(str(tmp_path), replace(request, cursor=cursor))
    fresh = execute_patch(str(tmp_path), request)
    (tmp_path / "pkg.py").write_text("load = lambda: [8]\n")
    with pytest.raises(ValueError, match="stale"):
        execute_patch(str(tmp_path), replace(request, cursor=fresh["retrieval"]["findings"]["cursor"]))


def test_container_none_is_not_a_none_return_and_parameter_flow_is_bounded():
    files = {
        "api.py": "def source(value):\n    return [None]\ndef identity(value):\n    return value\n",
        "client.py": "from api import source, identity\ndef consume():\n    return identity(source(1))[0]\n",
    }
    flow = analyze(files, {**files, "api.py": files["api.py"].replace("[None]", "None")}, {"api.py#source"})
    summary = next(r for r in flow["summary_changes"] if r["entity_id"] == "api.py#source")
    assert summary["before"]["may_return_none"] is False
    assert summary["after"]["may_return_none"] is True
    assert any(r["consumer_id"] == "client.py#consume" and r["operation"] == "index" for r in flow["relationships"])


def test_handler_result_is_related_but_later_unrelated_result_is_not():
    files = {
        "api.py": 'def source():\n    raise ValueError("bad")\n',
        "client.py": "from api import source\ndef handled():\n    try:\n        source()\n    except ValueError:\n        return 7\n    return 8\n",
        "test_client.py": "from client import handled\ndef test_handler():\n    assert handled()==7\n",
    }
    report = analyze(files, {**files, "api.py": files["api.py"].replace("ValueError", "TypeError")}, {"api.py#source"})
    assert any(r["test_id"] == "test_client.py::test_handler" for r in report["assertions"])
    before = [
        r
        for r in report["relationships"]
        if r["phase"] == "before" and r["consumer_id"] == "client.py#handled" and r["operation"] == "return"
    ]
    assert len(before) == 1 and before[0]["constraints"][0]["kind"] == "exception-handler"


def test_unchanged_literal_return_field_and_container_alias_do_not_promote_assertions():
    files = {
        "api.py": 'def source():\n    return {"changed": 1, "other": 9}\n',
        "test_api.py": 'from api import source\ndef test_other():\n    result=source()\n    assert result["other"]==9\ndef test_related():\n    assert source()["changed"]==1\ndef test_wrapped_other():\n    result=[source(),9]\n    assert result[1]==9\n',
    }
    flow = analyze(
        files, {**files, "api.py": files["api.py"].replace('"changed": 1', '"changed": 2')}, {"api.py#source"}
    )
    assert {r["test_id"] for r in flow["assertions"]} == {"test_api.py::test_related"}
    assert {r["test_id"] for r in flow["unrelated_assertions"]} == {"test_api.py::test_other"}


def test_unsupported_control_flow_does_not_invent_an_early_return_guard():
    files = {
        "api.py": "def source():\n    return [1]\n",
        "client.py": "from api import source\ndef consume():\n    value=source()\n    if value is None:\n        while unknown():\n            pass\n    return value[0]\n",
    }
    flow = analyze(files, {**files, "api.py": files["api.py"].replace("[1]", "None")}, {"api.py#source"})
    assert not any(r["operation"] == "index" and r["consumer_id"] == "client.py#consume" for r in flow["relationships"])
    assert any("unsupported control flow" in r["reason"] for r in flow["uncertainties"])


def test_scope_shadowing_and_similarly_named_methods_do_not_resolve():
    files = {
        "api.py": "def source():\n    return [1]\n",
        "other.py": "def source():\n    return [8]\n",
        "client.py": "import other\nfrom api import source\ndef reassigned():\n    alias=source\n    alias=lambda: [8]\n    return alias()[0]\ndef other_method(obj):\n    return obj.source()[0]\ndef different_module():\n    return other.source()[0]\n",
    }
    flow = analyze(files, {**files, "api.py": files["api.py"].replace("[1]", "None")}, {"api.py#source"})
    assert not flow["relationships"]


def test_unsupported_decorators_and_flow_limits_are_disclosed(monkeypatch):
    from amu import value_flow

    files = {
        "api.py": "@unknown\ndef source():\n    return [1]\n",
        "test_api.py": "from api import source\ndef test_source():\n    assert source()[0]==1\n",
    }
    flow = analyze(files, files, {"api.py#source"})
    assert not flow["assertions"]
    assert any("decorated function" in r["reason"] for r in flow["uncertainties"])
    monkeypatch.setattr(value_flow, "MAX_STEPS", 1)
    flow = analyze(
        {"api.py": "def source():\n    return 1\n"}, {"api.py": "def source():\n    return None\n"}, {"api.py#source"}
    )
    assert any("step limit" in r["reason"] for r in flow["uncertainties"])


def test_unresolved_expression_marks_the_function_summary_incomplete():
    files = {"api.py": "def source(flag):\n    return 1 if flag else None\n"}
    flow = analyze(files, {"api.py": "def source(flag):\n    return None\n"}, {"api.py#source"})
    row = next(r for r in flow["summary_changes"] if r["entity_id"] == "api.py#source")
    assert row["before"]["incomplete"] is True
    assert row["after"]["incomplete"] is False


def test_nested_property_callback_keeps_its_own_qualified_name():
    before = "class Response:\n    @property\n    def params(self):\n        def on_update(value):\n            return 1\n        return on_update\n"
    changes = analyze_python_change("api.py", before, before.replace("return 1", "return 2"))["modified_entities"]
    callback = next(r for r in changes if r["kind"] == "function")
    assert callback["id"] == "api.py#Response.params@getter.on_update"
    assert callback["qualified_name"] == "Response.params.on_update"
    assert "accessor" not in callback
