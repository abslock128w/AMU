"""Regression evidence for lexical import references; samples are only parsed."""
import ast

import pytest

from amu.plan import patch_test_recommendations


def selected(source):
    return [row['test_id'] for row in patch_test_recommendations(
        [{'path': 'tests/test_format.py', 'tree': ast.parse(source), 'sha256': '0' * 64}],
        {'formatter.py': {'format_value'}},
    )]


@pytest.mark.parametrize('declaration,body', [
    ('def test_local(*format_value):', 'assert format_value == ()'),
    ('def test_local(**format_value):', 'assert format_value == {}'),
    ('def test_local():', 'def format_value(value):\n        return value\n    assert format_value(2) == 2'),
    ('def test_local():', 'class format_value:\n        pass\n    assert format_value()'),
    ('def test_local():', 'from other import format_value\n    assert format_value(2)'),
    ('def test_local():', 'try:\n        pass\n    except ValueError as format_value:\n        assert format_value'),
])
def test_shadowed_import_is_not_an_application_reference(declaration, body):
    source = f'from formatter import format_value\n\n{declaration}\n    {body}\n'
    assert selected(source) == []


def test_sibling_local_assignment_does_not_rebind_module_import():
    source = ('from formatter import format_value\n\n'
              'def test_direct():\n    assert format_value(2)\n\n'
              'def test_local():\n    format_value = lambda x: x\n    assert format_value(2)\n')
    assert selected(source) == ['tests/test_format.py::test_direct']


def test_nested_function_binding_does_not_shadow_outer_reference():
    source = ('from formatter import format_value\n\n'
              'def test_direct():\n'
              '    def helper():\n        format_value = None\n        return format_value\n'
              '    assert format_value(2)\n')
    assert selected(source) == ['tests/test_format.py::test_direct']


def test_nested_shadowed_reference_does_not_recommend_outer_test():
    source = ('from formatter import format_value\n\n'
              'def test_wrapper():\n'
              '    def helper(format_value):\n        return format_value(2)\n'
              '    assert helper(lambda x: x) == 2\n')
    assert selected(source) == []


def test_module_reassignment_still_abstains():
    source = ('from formatter import format_value\n'
              'format_value = lambda x: x\n\n'
              'def test_local():\n    assert format_value(2)\n')
    assert selected(source) == []
