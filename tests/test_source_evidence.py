import pytest

from amu.source_evidence import (
    direct_call_evidence,
    filter_contradicted_python_call_edges,
    filter_python_call_evidence,
    graph_call_evidence,
    symbol_presence,
    symbol_reference_evidence,
)


def _cross_module_sources(tmp_path, facade):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "client.py").write_text(
        "import pkg.facade as api\n\n"
        "def consumer(value):\n"
        "    return api.target(value)\n"
    )
    (package / "facade.py").write_text(facade)
    (package / "definitions.py").write_text("def target(value):\n    return value\n")


def test_absolute_import_inside_package_resolves_from_repository_root(tmp_path):
    _cross_module_sources(tmp_path, "def target(value):\n    return value\n")
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/facade.py", scope_start=3, scope_end=4,
    ) == "verified"


@pytest.mark.parametrize("facade", [
    "from .definitions import target\n",
    "from .definitions import *\n",
    "from .definitions import target as imported\ntarget = imported\n",
    "def __getattr__(name):\n    raise AttributeError(name)\n",
    "# Provider cannot know whether a generated binding exists.\n",
])
def test_cross_module_reexport_candidates_stay_unverifiable_not_false(facade, tmp_path):
    _cross_module_sources(tmp_path, facade)
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=4,
    ) == "unverifiable"


def test_reexport_edge_remains_visible_with_an_explicit_approval_blocker(tmp_path):
    _cross_module_sources(tmp_path, "from .definitions import *\n")
    impacts = {"consumer": {
        "focus": {"name": "consumer", "file_path": "pkg/client.py", "start_line": 3, "end_line": 4},
        "callees": {"total": 1, "entries": [{
            "endpoint": {"name": "target", "file_path": "pkg/definitions.py"},
            "relation": "CALLS", "depth": 1,
        }]},
    }}
    filtered, contradictions, uncertainty = filter_python_call_evidence(str(tmp_path), impacts)
    assert filtered == impacts
    assert not contradictions
    assert [row["evidence"] for row in uncertainty] == ["source:python-ast:unverified-call-identity"]


def test_own_same_named_definition_in_other_module_remains_a_contradiction(tmp_path):
    _cross_module_sources(tmp_path, "def target(value):\n    return value + 1\n")
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=4,
    ) == "contradicted"


def test_rebound_same_named_definition_cannot_disprove_a_reexport(tmp_path):
    _cross_module_sources(tmp_path,
                          "def target(value):\n    return value + 1\nfrom .definitions import target\n")
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=4,
    ) == "unverifiable"


@pytest.mark.parametrize("facade", [
    "def target(value):\n    return value\nif CONDITION:\n    def target(value):\n        return value + 1\n",
    "def target(value):\n    return value\ndef sibling(value=generate_exports()):\n    return value\n",
    "def target(value):\n    return value\nclass Sibling(make_export_base()):\n    pass\n",
])
def test_potential_export_rebindings_do_not_become_wrong_module_proof(facade, tmp_path):
    _cross_module_sources(tmp_path, facade)
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=4,
    ) == "unverifiable"


def test_real_unverified_reexport_call_is_not_disproved_by_a_matching_docstring(tmp_path):
    _cross_module_sources(tmp_path, "from .definitions import target\n")
    (tmp_path / "pkg/client.py").write_text(
        "import pkg.facade as api\n\ndef consumer(value):\n"
        '    """Usage: api.target(value)."""\n'
        "    return api.target(value)\n"
    )
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=5,
    ) == "unverifiable"


@pytest.mark.parametrize("alias_binding,call", [
    ("from .facade import target as alias\n", "alias(value)"),
    ("from .facade import target as imported\nalias = imported\n", "alias(value)"),
])
def test_aliased_reexport_call_is_not_disproved_by_target_docstring(alias_binding, call, tmp_path):
    _cross_module_sources(tmp_path, "from .definitions import target\n")
    source = alias_binding + '\ndef consumer(value):\n    """Usage: target(value)."""\n    return ' + call + "\n"
    (tmp_path / "pkg/client.py").write_text(source)
    first = source.splitlines().index("def consumer(value):") + 1
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=first, scope_end=first + 2,
    ) == "unverifiable"


def test_an_unrelated_import_alias_does_not_hide_a_docstring_only_edge(tmp_path):
    _cross_module_sources(tmp_path, "def other(value):\n    return value\n")
    (tmp_path / "pkg/client.py").write_text(
        "from .facade import other as alias\n\ndef consumer(value):\n"
        '    """Usage: target(value)."""\n'
        "    return alias(value)\n"
    )
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=3, scope_end=5,
    ) == "contradicted"


@pytest.mark.parametrize("rebind", [
    "target = other\n",
    "if CONDITION:\n    target = other\n",
    "if CONDITION:\n    from .other import target\n",
    "def target(value):\n    return value + 1\n",
    "del target\n",
    "exec(code)\n",
    "globals()['target'] = other\n",
    "globals().update({'target': other})\n",
])
def test_stale_or_dynamic_module_import_binding_is_not_verified(rebind, tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "definitions.py").write_text("def target(value):\n    return value\n")
    source = "from .definitions import target\n" + rebind + "\ndef caller(value):\n    return target(value)\n"
    (package / "client.py").write_text(source)
    first = source.splitlines().index("def caller(value):") + 1
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/definitions.py", scope_start=first, scope_end=first + 1,
    ) == "unverifiable"


@pytest.mark.parametrize("rebind", [
    "api.target = other\n", "setattr(api, 'target', other)\n", "api.__dict__['target'] = other\n",
])
def test_mutated_imported_namespace_cannot_verify_original_endpoint(rebind, tmp_path):
    _cross_module_sources(tmp_path, "def target(value):\n    return value\n")
    source = "import pkg.facade as api\n" + rebind + "\ndef consumer(value):\n    return api.target(value)\n"
    (tmp_path / "pkg/client.py").write_text(source)
    first = source.splitlines().index("def consumer(value):") + 1
    assert graph_call_evidence(
        str(tmp_path), "pkg/client.py", "target", target_path="pkg/facade.py", scope_start=first, scope_end=first + 1,
    ) == "unverifiable"


def test_executable_python_call_is_verified_while_docstring_example_is_not(tmp_path):
    (tmp_path / "sample.py").write_text(
        '"""Example only: np.argmax(values)."""\n\n'
        "def helper(values):\n"
        "    return values\n\n"
        "def caller(values):\n"
        "    return helper(values)\n"
    )

    assert direct_call_evidence(str(tmp_path), "sample.py", "helper", line=7) == "verified"
    assert direct_call_evidence(str(tmp_path), "sample.py", "argmax") == "contradicted"


def test_unsafe_or_unparseable_source_is_unverifiable(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n")
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def external(): pass\n")

    assert direct_call_evidence(str(tmp_path), "broken.py", "anything") == "unverifiable"
    assert direct_call_evidence(str(tmp_path), "../outside.py", "external") == "unverifiable"


def test_symbol_presence_proves_simple_python_definitions_but_not_ambiguous_source(tmp_path):
    (tmp_path / "symbols.py").write_text(
        "def top_level():\n"
        "    return 1\n\n"
        "class Container:\n"
        "    def method(self):\n"
        "        return top_level()\n\n"
        "    async def async_method(self):\n"
        "        return top_level()\n"
    )
    (tmp_path / "broken.py").write_text("def broken(:\n")

    assert symbol_presence(str(tmp_path), "symbols.py", "top_level") == "present"
    assert symbol_presence(str(tmp_path), "symbols.py", "Container") == "present"
    assert symbol_presence(str(tmp_path), "symbols.py", "Container.method") == "present"
    assert symbol_presence(str(tmp_path), "symbols.py", "Container.async_method") == "present"
    assert symbol_presence(str(tmp_path), "symbols.py", "missing") == "absent"
    assert symbol_presence(str(tmp_path), "symbols.py", "Container.missing") == "absent"
    assert symbol_presence(str(tmp_path), "missing.py", "missing") == "absent"

    assert symbol_presence(str(tmp_path), "broken.py", "broken") == "unverifiable"
    assert symbol_presence(str(tmp_path), "symbols.py", "Container.Inner.method") == "unverifiable"
    assert symbol_presence(str(tmp_path), "symbols.py", "not-valid") == "unverifiable"
    assert symbol_presence(str(tmp_path / "missing-repo"), "symbols.py", "top_level") == "unverifiable"


def test_symbol_reference_evidence_proves_a_direct_consumer_no_longer_names_old_target(tmp_path):
    (tmp_path / "consumers.py").write_text(
        "from .target import old_target as old_alias\n\n"
        "def stale():\n"
        "    return old_alias()\n\n"
        "def migrated():\n"
        "    return renamed_target()\n\n"
        "class Container:\n"
        "    def typed(self, value: old_target) -> int:\n"
        "        return 1\n\n"
        "def dynamic(module, name):\n"
        "    return getattr(module, name)()\n"
    )

    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "stale", "old_target") == "present"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "migrated", "old_target") == "absent"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "Container.typed", "old_target") == "present"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "dynamic", "old_target") == "unverifiable"
    assert symbol_reference_evidence(str(tmp_path), "missing.py", "gone", "old_target") == "endpoint_absent"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "too.deep.scope", "old_target") == "unverifiable"


def test_symbol_reference_evidence_fails_closed_for_alias_registries_and_nested_scopes(tmp_path):
    (tmp_path / "consumers.py").write_text(
        "from .target import old_target as imported\n"
        "legacy = imported\n\n"
        "def aliased():\n"
        "    return legacy()\n\n"
        "def registry(registry):\n"
        "    return registry['old_target']()\n\n"
        "def dynamic_registry(registry, name):\n"
        "    return registry[name]()\n\n"
        "def nested():\n"
        "    def callback():\n"
        "        return old_target()\n"
        "    return callback\n"
    )

    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "aliased", "old_target") == "present"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "registry", "old_target") == "present"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "dynamic_registry", "old_target") == "unverifiable"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "nested", "old_target") == "unverifiable"
    assert symbol_reference_evidence(str(tmp_path), "consumers.py", "missing_consumer", "old_target") == "endpoint_absent"


def test_graph_call_evidence_uses_the_focused_function_not_docstrings_or_nested_or_sibling_calls(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        "    return 1\n\n"
        "def focused():\n"
        '    """Example only: target()."""\n'
        "    def nested():\n"
        "        return target()\n"
        "    return 0\n\n"
        "def sibling():\n"
        "    return target()\n"
    )

    assert graph_call_evidence(str(tmp_path), "sample.py", "target", scope_start=4, scope_end=8) == "contradicted"
    assert graph_call_evidence(str(tmp_path), "sample.py", "target", scope_start=10, scope_end=11) == "verified"


def test_filter_removes_only_a_docstring_only_direct_call_and_preserves_a_real_one(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        '    """Example only: helper()."""\n'
        "    return 0\n\n"
        "def helper():\n"
        "    return 1\n\n"
        "def consumer():\n"
        "    return target()\n"
    )
    impacts = {
        "target": {
            "focus": {"name": "target", "file_path": "sample.py", "start_line": 1, "end_line": 3},
            "callers": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {"name": "consumer", "file_path": "sample.py"},
                        "relation": "CALLS",
                        "depth": 1,
                        "call_site": {"file_path": "sample.py", "line": 9},
                    }
                ],
            },
            "callees": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {"name": "helper", "file_path": "sample.py"},
                        "relation": "CALLS",
                        "depth": 1,
                    }
                ],
            },
        }
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(tmp_path), impacts)

    assert filtered["target"]["callers"]["entries"] == impacts["target"]["callers"]["entries"]
    assert filtered["target"]["callees"] == {"total": 0, "entries": []}
    assert contradictions == [
        {
            "target": "target",
            "section": "callees",
            "source_path": "sample.py",
            "endpoint": "helper",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]


def test_filter_removes_docstring_only_callee_when_provider_omits_endpoint_path(tmp_path):
    """A provider's unresolved endpoint must not hide a source-proven false edge."""
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        '    \"\"\"Example only: documented_helper().\"\"\"\n'
        "    return real_helper()\n\n"
        "def real_helper():\n"
        "    return 1\n"
    )
    impacts = {
        "target": {
            "focus": {"name": "target", "file_path": "sample.py", "start_line": 1, "end_line": 3},
            "callees": {
                "total": 2,
                "entries": [
                    {
                        "endpoint": {"name": "documented_helper", "file_path": None},
                        "relation": "CALLS",
                        "depth": 1,
                    },
                    {
                        "endpoint": {"name": "real_helper", "file_path": "sample.py"},
                        "relation": "CALLS",
                        "depth": 1,
                    },
                ],
            },
        }
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(tmp_path), impacts)

    assert [entry["endpoint"]["name"] for entry in filtered["target"]["callees"]["entries"]] == ["real_helper"]
    assert contradictions == [
        {
            "target": "target",
            "section": "callees",
            "source_path": "sample.py",
            "endpoint": "documented_helper",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]


def test_filter_removes_a_docstring_only_direct_async_call(tmp_path):
    (tmp_path / "sample.py").write_text(
        "async def target():\n"
        '    """Example only: helper()."""\n'
        "    return 0\n\n"
        "async def helper():\n"
        "    return 1\n"
    )
    impacts = {
        "target": {
            "focus": {"name": "target", "file_path": "sample.py", "start_line": 1, "end_line": 3},
            "callees": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {"name": "helper", "file_path": "sample.py"},
                        "relation": "ASYNC_CALLS",
                        "depth": 1,
                    }
                ],
            },
        }
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(tmp_path), impacts)

    assert filtered["target"]["callees"] == {"total": 0, "entries": []}
    assert contradictions == [
        {
            "target": "target",
            "section": "callees",
            "source_path": "sample.py",
            "endpoint": "helper",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]


def test_filter_uses_caller_endpoint_span_when_a_call_site_is_omitted(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def target():\n"
        "    return 0\n\n"
        "def documented_caller():\n"
        '    """Example only: target()."""\n'
        "    return 0\n\n"
        "def unrelated_real_caller():\n"
        "    return target()\n"
    )
    impacts = {
        "target": {
            "focus": {"name": "target", "file_path": "sample.py", "start_line": 1, "end_line": 2},
            "callers": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {
                            "name": "documented_caller",
                            "file_path": "sample.py",
                            "start_line": 4,
                            "end_line": 6,
                        },
                        "relation": "CALLS",
                        "depth": 1,
                    }
                ],
            },
        }
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(tmp_path), impacts)

    assert filtered["target"]["callers"] == {"total": 0, "entries": []}
    assert contradictions == [
        {
            "target": "target",
            "section": "callers",
            "source_path": "sample.py",
            "endpoint": "documented_caller",
            "line": None,
            "evidence": "source:python-ast:docstring-only-call",
        }
    ]


def test_filter_leaves_transitive_and_non_python_candidates_for_the_graph_to_qualify(tmp_path):
    (tmp_path / "sample.py").write_text('def target():\n    """Example only: helper()."""\n    return 0\n')
    impacts = {
        "target": {
            "focus": {"name": "target", "file_path": "sample.py", "start_line": 1, "end_line": 3},
            "callees": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {"name": "helper", "file_path": "sample.py"},
                        "relation": "CALLS",
                        "depth": 2,
                    }
                ],
            },
        },
        "native_target": {
            "focus": {"name": "native_target", "file_path": "native.c", "start_line": 1, "end_line": 3},
            "callees": {
                "total": 1,
                "entries": [
                    {
                        "endpoint": {"name": "helper", "file_path": "native.c"},
                        "relation": "CALLS",
                        "depth": 1,
                    }
                ],
            },
        },
    }

    filtered, contradictions = filter_contradicted_python_call_edges(str(tmp_path), impacts)

    assert filtered == impacts
    assert contradictions == []
