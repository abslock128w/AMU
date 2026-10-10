"""Behavioral regression cases for the nonexecuting Python API adapter."""

import json

from amu import api_python
from amu.api_python import analyze


def entries(source, path="api.py"):
    result = analyze(source, path)
    return result, {entry["qualified_name"]: entry for entry in result["entries"]}


def test_literal_all_narrows_exports_but_keeps_exported_private_protocols():
    result, found = entries('''
__all__ = ["_Widget", "_exported"]
def omitted(): pass
def _exported(value): return value
class _Widget:
    def __call__(self, value): return value
    def _internal(self): pass
''')
    assert result["status"] == "complete"
    assert set(found) == {"_Widget", "_Widget.__call__", "_exported"}
    assert found["_exported"]["visibility"] == "declared"


def test_explicit_empty_all_exposes_no_conventional_exports():
    result, found = entries('__all__ = []\ndef public(): pass\n')
    assert result["status"] == "complete"
    assert found == {}


def test_dynamic_all_retains_candidates_and_marks_coverage_partial():
    result, found = entries('__all__ = exported_names()\ndef public(): pass\ndef _private(): pass\n')
    assert result["status"] == "partial"
    assert "public" in found
    assert found["public"]["visibility"] == "unknown"
    assert any("__all__" in unknown["reason"] for unknown in result["unknowns"])


def test_literal_export_augmentation_and_unresolved_exports():
    result, found = entries('__all__ = ["a"]\n__all__ += ["b", "missing"]\ndef a(): pass\ndef b(): pass\n')
    assert set(found) == {"a", "b"}
    assert result["status"] == "partial"
    assert any("missing" in unknown["reason"] for unknown in result["unknowns"])


def test_overloads_are_grouped_and_overload_change_changes_signature_fingerprint():
    source = '''
from typing import overload as ov
__all__ = ["convert"]
@ov
def convert(value: int) -> str: ...
@ov
def convert(value: str) -> str: ...
def convert(value): return str(value)
'''
    result, before = entries(source)
    changed, after = entries(source.replace("value: int", "value: float"))
    assert result["status"] == changed["status"] == "complete"
    assert len(before["convert"]["facets"]["overloads"]) == 2
    assert before["convert"]["signature_fingerprint"] != after["convert"]["signature_fingerprint"]
    assert before["convert"]["body_fingerprint"] == after["convert"]["body_fingerprint"]


def test_repeated_ordinary_declarations_are_ambiguous():
    result, found = entries('def public(value): pass\ndef public(value, other): pass\n')
    assert result["status"] == "partial"
    assert "public" not in found
    assert any("repeated" in unknown["reason"] for unknown in result["unknowns"])


def test_rebound_overload_alias_is_not_mistaken_for_typing_decorator():
    result, found = entries('''
from typing import overload
overload = custom_decorator
@overload
def public(value: int): ...
def public(value): return value
''')
    assert result["status"] == "partial"
    assert "public" not in found


def test_reexports_aliases_constants_and_config_keys_have_source_evidence():
    result, found = entries('''
from .core import public as alias
import example.core as core
LIMIT = 10
DEFAULTS = {"timeout": 3, "strict": False}
''', "pkg/__init__.py")
    assert result["status"] == "complete"
    assert found["alias"]["facets"]["reexports"] == [".core.public"]
    assert found["core"]["facets"]["reexports"] == ["example.core"]
    assert found["LIMIT"]["kind"] == "constant"
    assert found["DEFAULTS"]["facets"]["config_keys"] == ["timeout", "strict"]
    assert found["alias"]["location"]["line"] == 2
    assert all(entry["id"].startswith("pkg/__init__.py#") for entry in found.values())
    assert all(entry["provenance"] == "parser" for entry in found.values())
    assert all(entry["confidence"] == "requires_verification" for entry in found.values())


def test_public_local_alias_tracks_private_function_changes():
    source = 'def _internal(value=3): return value\npublic = _internal\n'
    _, before = entries(source)
    _, after = entries(source.replace("value=3", "value=4"))
    assert set(before) == {"public"}
    assert before["public"]["facets"]["aliases"] == ["_internal"]
    assert before["public"]["signature_fingerprint"] != after["public"]["signature_fingerprint"]


def test_public_class_alias_preserves_private_implementation_methods():
    source = 'class _Internal:\n    def call(self, value=3): return value\nPublic = _Internal\n'
    _, before = entries(source)
    _, after = entries(source.replace("value=3", "value=4"))
    assert "Public.call" in before
    assert before["Public.call"]["signature_fingerprint"] != after["Public.call"]["signature_fingerprint"]


def test_default_annotation_deprecation_and_behavior_hints_are_bounded_static_facets():
    _, found = entries('''
import warnings
def public(value: int = 3, *, strict: bool = False) -> str:
    warnings.warn("old", DeprecationWarning)
    if strict:
        raise ValueError("invalid")
    def local():
        raise RuntimeError("local")
    return str(value)
''')
    facets = found["public"]["facets"]
    assert facets["defaults"] == {"value": "3", "strict": "False"}
    assert facets["annotations"]["return"] == "str"
    assert facets["exceptions"] == ["ValueError"]
    assert facets["returns"] == ["call:str"]
    assert facets["deprecations"]
    assert facets["behavior_evidence"] == "static syntax hints; runtime behavior requires verification"


def test_cli_literals_are_hints_and_do_not_resolve_parser_receiver():
    _, found = entries('''
def command(parser):
    parser.add_argument("--limit", default=3, type=int)
    parser.add_argument(option_name)
    return parser
''')
    options = found["command"]["facets"]["cli_options"]
    assert options[0]["options"] == ["--limit"]
    assert options[0]["default"] == "3"
    assert options[0]["confidence"] == "lexical_receiver_unresolved"
    assert options[1]["dynamic"] is True


def test_wildcard_and_conditional_exports_do_not_claim_complete_coverage():
    result, _ = entries('from .core import *\nif enabled:\n    def conditional(): pass\n')
    assert result["status"] == "partial"
    assert any("wildcard" in unknown["reason"] for unknown in result["unknowns"])
    assert any("conditional" in unknown["reason"] for unknown in result["unknowns"])


def test_non_python_parse_failure_and_resource_bounds_are_explicit():
    assert analyze("export const value = 3", "api.ts")["status"] == "unsupported"
    assert analyze("def unfinished(", "api.py")["status"] == "partial"
    assert analyze("#" * (2 * 1024 * 1024 + 1), "api.py")["status"] == "partial"


def test_ast_entry_bounds_and_nested_local_defs_are_not_public():
    source = "\n".join(f"def public_{index}(): pass" for index in range(2100))
    result = analyze(source, "api.py")
    assert result["status"] == "partial"
    assert len(result["entries"]) <= 2000
    _, found = entries('def public():\n    def local(): pass\n    return local\n')
    assert set(found) == {"public"}


def test_formatting_and_comment_edits_leave_fingerprints_unchanged():
    _, before = entries('def public(value=3):\n    return value\n')
    _, after = entries('# comment\ndef public( value = 3 ):\n    return value  # comment\n')
    assert before["public"]["signature_fingerprint"] == after["public"]["signature_fingerprint"]
    assert before["public"]["body_fingerprint"] == after["public"]["body_fingerprint"]


def test_alias_to_import_and_alias_chain_preserve_target_identity():
    result, found = entries('from .core import public as _implementation\n_hidden = _implementation\npublic = _hidden\n')
    assert result["status"] == "complete"
    assert found["public"]["facets"]["reexports"] == [".core.public"]


def test_alias_cannot_resolve_a_later_definition_or_reassigned_target():
    result, _ = entries('public = _internal\ndef _internal(): pass\n')
    assert result["status"] == "partial"
    result, _ = entries('def _internal(): pass\npublic = _internal\ndef _internal(value): pass\n')
    assert result["status"] == "partial"


def test_class_method_overwritten_by_attribute_uses_effective_binding():
    result, found = entries('class Public:\n    def run(self): pass\n    run = None\n')
    assert result["status"] == "complete"
    assert found["Public.run"]["kind"] == "binding"


def test_sensitive_defaults_constants_and_dictionary_values_are_not_displayed():
    source = '''
API_KEY: str = "sensitive-one"
CONFIG = {"password": "sensitive-two", "safe": "visible"}
def public(api_key: str = "sensitive-three", *, token="sensitive-four"):
    return None
'''
    _, found = entries(source)
    rendered = json.dumps(found)
    for secret in ["sensitive-one", "sensitive-two", "sensitive-three", "sensitive-four"]:
        assert secret not in rendered
    assert "visible" in rendered
    _, changed = entries(source.replace("sensitive-one", "changed-secret"))
    assert found["API_KEY"]["signature_fingerprint"] != changed["API_KEY"]["signature_fingerprint"]


def test_source_side_effects_and_decorators_are_never_executed(tmp_path):
    marker = tmp_path / "executed"
    result, found = entries(f'''\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n@unknown_decorator\ndef public(): pass\n''')
    assert "public" in found
    assert not marker.exists()


def test_async_contract_changes_signature_even_when_arguments_are_identical():
    _, before = entries('def fetch():\n    return 1\n')
    _, after = entries('async def fetch():\n    return 1\n')
    assert before["fetch"]["signature_fingerprint"] != after["fetch"]["signature_fingerprint"]
    assert after["fetch"]["facets"]["async"] is True
    assert before["fetch"]["body_fingerprint"] == after["fetch"]["body_fingerprint"]


def test_module_namespace_updates_lower_coverage():
    result, _ = entries('globals().update(dynamic_exports())\n')
    assert result["status"] == "partial"


def test_named_constant_alias_tracks_effective_initializer():
    result, before = entries('DEFAULT = 10\nLIMIT = DEFAULT\n')
    _, after = entries('DEFAULT = 20\nLIMIT = DEFAULT\n')
    assert result["status"] == "complete"
    assert before["LIMIT"]["signature_fingerprint"] != after["LIMIT"]["signature_fingerprint"]


def test_generator_protocol_changes_but_nested_generator_does_not_change_outer_contract():
    _, ordinary = entries('def stream():\n    return 1\n')
    _, generator = entries('def stream():\n    yield 1\n')
    _, nested = entries('def stream():\n    def local():\n        yield 1\n    return local\n')
    assert ordinary["stream"]["signature_fingerprint"] != generator["stream"]["signature_fingerprint"]
    assert generator["stream"]["facets"]["generator"] is True
    assert nested["stream"]["facets"]["generator"] is False


def test_ast_and_manifest_output_limits_make_partial_coverage_explicit(monkeypatch):
    result = analyze("VALUE = [" + "0," * 50_000 + "]", "api.py")
    assert result["status"] == "partial"
    assert result["entries"] == []
    assert any("node limit" in unknown["reason"] for unknown in result["unknowns"])
    monkeypatch.setattr(api_python, "MAX_MANIFEST_BYTES", 1_000)
    result = analyze("\n".join(f"def public_{index}(): pass" for index in range(10)), "api.py")
    assert result["status"] == "partial"
    assert len(result["entries"]) < 10
    assert any("output limit" in unknown["reason"] for unknown in result["unknowns"])


def test_function_local_all_does_not_change_module_export_visibility():
    result, found = entries('def public():\n    __all__ = dynamic_names()\n    return __all__\n')
    assert result["status"] == "complete"
    assert set(found) == {"public"}


def test_importing_a_different_submodule_changes_reexport_surface_evidence():
    _, before = entries('import numpy.linalg\n')
    _, after = entries('import numpy.random\n')
    assert before["numpy"]["facets"]["imported_modules"] == ["numpy.linalg"]
    assert before["numpy"]["signature_fingerprint"] != after["numpy"]["signature_fingerprint"]
