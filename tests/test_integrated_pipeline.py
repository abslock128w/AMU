"""Static fixtures are parsed only; assertions test the analyzer itself."""

import ast
import hashlib

from amu.value_flow import analyze_repository


def flow(source, changed, target):
    def trees(files):
        return {p: {"tree": ast.parse(s), "sha256": hashlib.sha256(s.encode()).hexdigest()} for p, s in files.items()}

    return analyze_repository(trees(source), trees(changed), {target})


def test_constructed_method_container_field_and_param_assertion():
    src = {
        "api.py": 'class API:\n    def result(self):\n        return {"value": 1, "other": 9}\n',
        "client.py": 'from api import API\ndef read():\n    obj=API()\n    return obj.result()["value"]\n',
        "test_api.py": 'import pytest\nfrom client import read\n@pytest.mark.parametrize("expected", [pytest.param(1, id="first")])\ndef test_result(expected):\n    assert read()==expected\n    assert 9==9\n',
    }
    f = flow(src, {**src, "api.py": src["api.py"].replace('"value": 1', '"value": 2')}, "api.py#API.result")
    assert any(
        r["consumer_id"] == "client.py#read" and r["operation"] == "return" and r["value_may_reach_consumer"]
        for r in f["relationships"]
    )
    assert {r["test_id"] for r in f["assertions"]} == {"test_api.py::test_result"}


def test_property_and_stored_object_field_have_located_paths():
    src = {
        "api.py": "class API:\n    @property\n    def result(self):\n        return 1\nclass Box:\n    pass\ndef read():\n    obj=API()\n    box=Box()\n    box.value=obj.result\n    return box.value\n",
        "test_api.py": "from api import read\ndef test_result():\n    assert read()==1\n",
    }
    f = flow(src, {**src, "api.py": src["api.py"].replace("return 1", "return 2")}, "api.py#API.result@getter")
    assert f["assertions"]
    assert any(s["role"] == "state_write" for a in f["assertions"] for s in a["path"])


def test_tuple_selection_does_not_promote_unchanged_or_reassigned_fields():
    src = {
        "api.py": "def source():\n    return (1,9)\n",
        "test_api.py": "from api import source\ndef test_related():\n    x,y=source()\n    assert x==1\ndef test_other():\n    x,y=source()\n    assert y==9\ndef test_reassigned():\n    x,y=source()\n    x=9\n    assert x==9\n",
    }
    f = flow(src, {**src, "api.py": src["api.py"].replace("(1,9)", "(2,9)")}, "api.py#source")
    assert {a["test_id"] for a in f["assertions"]} == {"test_api.py::test_related"}


def test_ambiguous_factory_escape_and_guard_join_abstain():
    src = {
        "api.py": "class API:\n    def result(self):\n        return 1\n",
        "test_api.py": "from api import API\ndef test_unknown(obj):\n    assert obj.result()==1\ndef test_escaped():\n    obj=API()\n    opaque(obj)\n    assert obj.result()==1\ndef test_join(flag):\n    obj=API()\n    if flag:\n        obj=unknown()\n    assert obj.result()==1\n",
    }
    f = flow(src, {**src, "api.py": src["api.py"].replace("return 1", "return 2")}, "api.py#API.result")
    assert not f["assertions"]


def test_constructor_shadow_and_invalid_binding_do_not_produce_assertion_paths():
    cases = [
        "class API:\n    def __init__(self):\n        self.result=lambda:9\n    def result(self):\n        return 1\n",
        "class API:\n    def __init__(self,required):\n        self.value=required\n    def result(self):\n        return 1\n",
        "class API:\n    def __getattr__(self,name):\n        return unknown\n    def result(self):\n        return 1\n",
    ]
    for source in cases:
        src = {
            "api.py": source,
            "test_api.py": "from api import API\ndef test_result():\n    obj=API()\n    assert obj.result()==1\n",
        }
        result = flow(src, {**src, "api.py": source.replace("return 1", "return 2")}, "api.py#API.result")
        assert not result["assertions"]


def test_constant_false_guard_and_overload_shadow_abstain():
    src = {
        "api.py": "def source():return 1\n",
        "test_api.py": "from api import source\ndef test_unreachable():\n    if False:\n        assert source()==1\n",
    }
    assert not flow(src, {**src, "api.py": "def source():return 2\n"}, "api.py#source")["assertions"]
    from amu.semantic import overload_nodes

    assert not overload_nodes(
        ast.parse(
            "from typing import overload\nclass API:\n    overload=unknown\n    @overload\n    def method(self):...\n"
        )
    )


def test_unchanged_stored_field_and_supported_property_setter():
    src = {
        "api.py": "class API:\n    def __init__(self):\n        self.changed=1\n        self.other=9\n",
        "test_api.py": "from api import API\ndef test_other():\n    obj=API()\n    assert obj.other==9\ndef test_changed():\n    obj=API()\n    assert obj.changed==1\n",
    }
    f = flow(src, {**src, "api.py": src["api.py"].replace("self.changed=1", "self.changed=2")}, "api.py#API.__init__")
    assert {a["test_id"] for a in f["assertions"]} == {"test_api.py::test_changed"}
    src = {
        "api.py": "class API:\n    @property\n    def value(self):\n        return self._value\n    @value.setter\n    def value(self,new):\n        self._value=new\n",
        "test_api.py": "from api import API\ndef test_value():\n    obj=API()\n    obj.value=1\n    assert obj.value==1\n",
    }
    f = flow(
        src, {**src, "api.py": src["api.py"].replace("self._value=new", "self._value=new+1")}, "api.py#API.value@setter"
    )
    assert {a["test_id"] for a in f["assertions"]} == {"test_api.py::test_value"}


def test_literal_param_condition_relevance_keeps_execution_and_oracle_unknown():
    source = "def source(value):\n    if value is None:\n        return 0\n    return 1\n"
    src = {
        "api.py": source,
        "test_api.py": 'import pytest\nfrom api import source\n@pytest.mark.parametrize("value", [pytest.param(0,id="zero")])\ndef test_condition(value):\n    assert source(value)==1\n',
    }
    f = flow(src, {**src, "api.py": source.replace("value is None", "value == 0")}, "api.py#source")
    assert f["assertions"][0]["changed_condition"]["outcome"] is True
    assert f["assertions"][0]["changed_condition"]["path_feasibility_proven"] is False
    guarded = {
        **src,
        "test_api.py": src["test_api.py"].replace(
            "    assert source(value)==1", "    if value is None:\n        assert source(value)==1"
        ),
    }
    assert not any(
        a.get("changed_condition")
        for a in flow(guarded, {**guarded, "api.py": source.replace("value is None", "value == 0")}, "api.py#source")[
            "assertions"
        ]
    )


def test_factory_mixed_receiver_alternatives_are_not_one_certain_type():
    source = 'class API:\n    def result(self):return 1\ndef factory(flag):\n    if flag:\n        return API()\n    return "unrelated"\n'
    src = {
        "api.py": source,
        "test_api.py": "from api import factory\ndef test_unknown(flag):\n    obj=factory(flag)\n    assert obj.result()==1\n",
    }
    f = flow(src, {**src, "api.py": source.replace("return 1", "return 2")}, "api.py#API.result")
    assert not f["assertions"]


def test_class_replacement_invalidates_receiver():
    source = "class API:\n    def result(self):return 1\nclass Other:\n    def result(self):return 9\n"
    src = {
        "api.py": source,
        "test_api.py": "from api import API,Other\ndef test_changed_class():\n    obj=API()\n    obj.__class__=Other\n    assert obj.result()==9\n",
    }
    assert not flow(src, {**src, "api.py": source.replace("return 1", "return 2")}, "api.py#API.result")["assertions"]
