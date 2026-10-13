"""Source-only checks for bounded concrete-change syntax evidence."""

import ast
import hashlib
import json

import pytest

from amu import change_analysis
from amu.change_analysis import analyze_python_change

CACHE = '''class Memo:
    _entries = {}

    @classmethod
    def lookup(cls, value):
        key = (type(value).__name__, value)
        previous = cls._entries.get(key)
        if previous is None:
            cls._entries[key] = value
        return previous
'''


def changes(before, after, path="module.py"):
    return analyze_python_change(path, before, after)


def finding(result, category, entity="module.py#Memo.lookup"):
    return next(item for item in result["findings"] if item["category"] == category and item["entity_id"] == entity)


def test_private_class_state_change_connects_unchanged_classmethod_consumers():
    result = changes(CACHE, CACHE.replace("_entries = {}", "_entries = None"))
    modified = {item["qualified_name"] for item in result["modified_entities"]}
    assert "Memo._entries" in modified
    assert "Memo.lookup" not in modified
    consumers = [item for item in result["state_dependencies"] if item["state_id"] == "module.py#Memo._entries"]
    assert {(item["phase"], item["access"]) for item in consumers} >= {
        ("before", "cache_get"), ("after", "cache_get"), ("before", "cache_set"), ("after", "cache_set")}
    assert all(item["consumer_id"] == "module.py#Memo.lookup" for item in consumers)
    declaration = finding(result, "state_declaration", "module.py#Memo._entries")
    assert "None" in declaration["after"][0]["snippet"]
    assert declaration["potential_effect"].startswith("Inferred:")


def test_changed_key_construction_binds_assignment_and_unchanged_consuming_spans():
    result = changes(CACHE, CACHE.replace("(type(value).__name__, value)", "str(value)"))
    access = finding(result, "state_access")
    assert "key construction" in access["observation"]
    assert {item["role"] for item in access["before"]} == {"key_definition", "state_access"}
    assert any("type(value).__name__" in item["snippet"] for item in access["before"])
    assert any("key = str(value)" in item["snippet"] for item in access["after"])
    assert any(".get(key)" in item["snippet"] for item in access["before"])
    assert any("[key] = value" in item["snippet"] for item in access["after"])
    for dep in result["state_dependencies"]:
        assert dep["key_definition"]["ast_hash"]
        assert dep["key_expression"] == "key"
    before_key = next(item for item in access["before"] if item["role"] == "key_definition")
    assignment = ast.parse("key = (type(value).__name__, value)").body[0]
    assert before_key["ast_hash"] == hashlib.sha256(ast.dump(assignment, include_attributes=False).encode()).hexdigest()


@pytest.mark.parametrize("definition", [
    "if value:\n            key = value\n        else:\n            key = None",
    "key = value\n        key = str(value)",
    "for key in value:\n            pass",
    "key = value\n        del key",
])
def test_conditional_rebound_or_deleted_keys_abstain(definition):
    source = CACHE.replace("key = (type(value).__name__, value)", definition)
    result = changes(source, source)
    assert result["state_dependencies"]
    assert all("key_definition" not in item for item in result["state_dependencies"])
    assert any("key construction unresolved" in item["reason"] for item in result["uncertainties"])


@pytest.mark.parametrize("decorator", ["", "@custom", "@classmethod\n    @custom", "@cm", "@staticmethod"])
def test_only_plain_builtin_classmethod_establishes_class_receiver(decorator):
    source = CACHE.replace("@classmethod", decorator)
    result = changes(source, source.replace("_entries = {}", "_entries = None"))
    assert not result["state_dependencies"]
    assert result["uncertainties"]


def test_shadowed_classmethod_does_not_establish_receiver():
    source = "classmethod = custom\n" + CACHE
    assert not [item for item in changes(source, source)["state_dependencies"] if item["state_id"].endswith("#Memo._entries")]
    source = CACHE.replace("_entries = {}", "_entries = {}\n    classmethod = custom")
    assert not [item for item in changes(source, source)["state_dependencies"] if item["state_id"].endswith("#Memo._entries")]


def test_rebound_classmethod_receiver_abstains():
    source = CACHE.replace("key = (type(value).__name__, value)", "cls = unrelated\n        key = value")
    result = changes(source, source)
    assert not result["state_dependencies"]
    assert any("receiver cls is rebound" in item["reason"] for item in result["uncertainties"])


def test_wildcard_or_dynamic_namespace_leaves_classmethod_binding_unresolved():
    for prefix in ["from other import *\n", "globals()['classmethod'] = custom\n"]:
        result = changes(prefix + CACHE, prefix + CACHE)
        assert not result["state_dependencies"]
        assert any("namespace" in item["reason"] for item in result["uncertainties"])


def test_alias_receiver_is_explicitly_unresolved():
    source = CACHE.replace("previous = cls._entries.get(key)", "alias = cls\n        previous = alias._entries.get(key)").replace("cls._entries[key] = value", "alias._entries[key] = value")
    result = changes(source, source)
    assert not result["state_dependencies"]
    assert any("alias" in item["reason"] for item in result["uncertainties"])


@pytest.mark.parametrize("binding", ["def key(): pass", "match value:\n            case key: pass"])
def test_string_and_definition_key_rebinding_abstains(binding):
    source = CACHE.replace("previous = cls._entries.get(key)", binding + "\n        previous = cls._entries.get(key)")
    result = changes(source, source)
    assert result["state_dependencies"]
    assert all("key_definition" not in item for item in result["state_dependencies"])


def test_receiver_name_is_not_hardcoded():
    source = CACHE.replace("cls", "kind")
    result = changes(source, source.replace("_entries = {}", "_entries = None"), "pkg/other.py")
    assert {item["state_id"] for item in result["state_dependencies"]} == {"pkg/other.py#Memo._entries"}


def test_descriptor_or_decorated_class_attribute_access_abstains():
    for source in [CACHE.replace("_entries = {}", "_entries = Descriptor()"), "@custom\n" + CACHE,
                   CACHE.replace("class Memo:", "class Memo(metaclass=Custom):")]:
        result = changes(source, source)
        assert not result["state_dependencies"]
        assert result["uncertainties"]


def test_module_private_state_reads_writes_and_cache_operations():
    source = '''_memo = {}
def lookup(value):
    global _memo
    key = value
    _memo[key] = value
    previous = _memo.get(key)
    _memo.clear()
    _memo = None
    return previous
'''
    result = changes(source, source.replace("_memo = {}", "_memo = None"))
    accesses = {item["access"] for item in result["state_dependencies"]}
    assert accesses >= {"cache_set", "cache_get", "cache_clear", "write"}
    assert all(item["state_scope"] == "module" for item in result["state_dependencies"])


def test_locally_shadowed_module_state_and_closed_over_local_abstain():
    source = '''_memo = {}
def outer():
    _memo = {}
    def inner():
        return _memo.get(1)
    return _memo.get(1)
'''
    assert not changes(source, source)["state_dependencies"]


def test_call_arguments_return_branch_and_exception_categories():
    before = '''async def convert(value):
    if value > 0:
        return adapt(value, strict=False)
    try:
        process(value)
    except ValueError:
        raise RuntimeError("bad")
'''
    after = before.replace("value > 0", "value >= 0").replace("strict=False", "strict=True").replace("ValueError", "TypeError").replace('RuntimeError("bad")', 'RuntimeError("invalid")')
    result = changes(before, after)
    assert {item["category"] for item in result["findings"]} >= {"call", "return", "branch", "exception"}
    assert result["modified_entities"][0]["kind"] == "async_function"
    assert all(item["potential_effect"].startswith("Inferred:") for item in result["findings"])
    assert all(item["verification"] and item["uncertainties"] for item in result["findings"])


def test_write_value_change_is_evidence_even_when_subscript_is_unchanged():
    result = changes(CACHE, CACHE.replace("cls._entries[key] = value", "cls._entries[key] = str(value)"))
    assert any("[key] = str(value)" in item["snippet"] for item in finding(result, "state_access")["after"])


def test_added_deleted_sources_and_repeated_entity_identity():
    source = "def _private():\n    return call(1)\n"
    assert changes(None, source)["modified_entities"][0]["change"] == "added"
    assert changes(source, None)["modified_entities"][0]["change"] == "deleted"
    repeated = source + source
    result = changes(repeated, repeated)
    assert not result["modified_entities"]
    assert any("ambiguous lexical identity" in item["reason"] for item in result["uncertainties"])


def test_malformed_unsupported_and_source_bounds_are_explicit():
    result = changes("def missing(:", "def valid(): pass")
    assert result["status"] == "partial"
    assert not result["modified_entities"] and not result["findings"]
    assert result["uncertainties"][0]["phase"] == "before"
    assert changes("", "", "file.js")["status"] == "unsupported"
    oversized = "#" + "x" * change_analysis.MAX_SOURCE_BYTES
    assert "2 MiB" in changes(oversized, "")["uncertainties"][0]["reason"]


def test_node_and_depth_bounds_are_explicit(monkeypatch):
    monkeypatch.setattr(change_analysis, "MAX_AST_NODES", 8)
    assert "limit" in changes("x = [1, 2, 3, 4]", "")["uncertainties"][0]["reason"]
    monkeypatch.setattr(change_analysis, "MAX_AST_NODES", 50_000)
    monkeypatch.setattr(change_analysis, "MAX_AST_DEPTH", 4)
    assert "limit" in changes("def f(): return foo(bar(1))", "")["uncertainties"][0]["reason"]


def test_snippet_cut_does_not_hide_changes_after_display_limit():
    before = "def f():\n    return call('" + "x" * 1500 + "a')\n"
    after = before.replace("a')", "b')")
    result = changes(before, after)
    call = finding(result, "call", "module.py#f")
    assert call["before"][0]["snippet"] == call["after"][0]["snippet"]
    assert call["before"][0]["snippet_truncated"]
    assert call["before_ast_hash"] != call["after_ast_hash"]
    assert call["before"][0]["content_hash"] != call["after"][0]["content_hash"]


def test_all_event_evidence_is_available_beyond_64_for_caller_pagination():
    before = "def f():\n" + "".join(f"    call({value})\n" for value in range(100))
    result = changes(before, before.replace("call(99)", "call(100)"))
    call = finding(result, "call", "module.py#f")
    assert not call["evidence_truncated"]
    assert len(call["before"]) == len(call["after"]) == 100
    assert "call(100)" in call["after"][-1]["snippet"]
    assert call["before_ast_hash"] != call["after_ast_hash"]


@pytest.mark.parametrize("rebind", ["Memo = unrelated", "if flag:\n    Memo = unrelated", "class Memo: pass", "from other import Memo"])
def test_rebound_or_repeated_direct_class_receiver_abstains(rebind):
    source = CACHE + rebind + "\ndef plain(key):\n    return Memo._entries.get(key)\n"
    result = changes(source, source)
    assert not [item for item in result["state_dependencies"] if item["consumer_id"] == "module.py#plain" and item["state_id"].endswith("#Memo._entries")]
    assert any("receiver" in item["reason"] or "ambiguous" in item["reason"] for item in result["uncertainties"])


def test_result_is_deterministic_and_json_serializable():
    after = CACHE.replace("(type(value).__name__, value)", "str(value)")
    result = changes(CACHE, after)
    assert json.dumps(result, sort_keys=True) == json.dumps(changes(CACHE, after), sort_keys=True)
    assert changes(CACHE, CACHE)["findings"] == []
    assert changes(CACHE, "\n" + CACHE)["modified_entities"] == []


def test_analyzing_source_never_executes_it(tmp_path):
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\ndef f(): return 1\n"
    result = changes(source, source.replace("return 1", "return 2"))
    assert result["findings"]
    assert not marker.exists()
