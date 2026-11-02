"""The execution recorder: watched lines per test and calls into watched functions, from a side's own run."""
import subprocess
import sys
import textwrap

import pytest

from amu.core import record, runners

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')

PACKAGE = {
    'src/pkg/__init__.py': 'from ._impl import shape, Box\n',
    'src/pkg/__init__.pyi': 'def shape(value: object) -> str: ...\nclass Box: ...\n',
    'src/pkg/_impl.py': textwrap.dedent('''\
        def shape(value):
            if isinstance(value, list):
                return "list"
            return "other"


        class Box:
            __slots__ = ("size",)

            def __init__(self, size):
                self.size = size

            def grow(self, by):
                self.size += by
                return self.size
        '''),
    'tests/test_shape.py': textwrap.dedent('''\
        from pkg import shape, Box


        def test_list():
            assert shape([1]) == "list"


        def test_other():
            assert shape(3) == "other"


        def test_box():
            assert Box(2).grow(1) == 3


        def test_nothing():
            assert True
        '''),
    'tests/test_unit.py': textwrap.dedent('''\
        import unittest

        from pkg import shape


        class ShapeCase(unittest.TestCase):
            def test_list(self):
                self.assertEqual(shape([]), "list")
        '''),
}


def project(tmp_path):
    for name, text in PACKAGE.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    return tmp_path


def run(tmp_path, command, lines, targets, cwd=None):
    env = runners.environment(tmp_path, record.prepare(tmp_path/'.rec', tmp_path, lines, targets))
    done = subprocess.run(command, cwd=cwd or tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode in (0, 1), done.stdout + done.stderr
    return record.read(tmp_path/'.rec', tmp_path, cwd)


def test_each_test_is_credited_with_the_watched_lines_it_ran_however_it_imported_them(tmp_path):
    # The tests reach shape through the package's re-export, with a stub beside it: no name resolution is involved.
    project(tmp_path)
    coverage, calls, _ = run(tmp_path, [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_shape.py'],
                             {'src/pkg/_impl.py': [3, 4]}, [])
    assert record.tests_on(coverage, 'src/pkg/_impl.py', [3]) == {'tests/test_shape.py::test_list'}, coverage
    assert record.tests_on(coverage, 'src/pkg/_impl.py', [4]) == {'tests/test_shape.py::test_other'}, coverage
    assert 'tests/test_shape.py::test_nothing' not in coverage
    assert calls == []


def test_unittest_ids_resolve_to_their_file(tmp_path):
    project(tmp_path)
    coverage, _, _ = run(tmp_path, [sys.executable, '-m', 'unittest', '-v', 'tests.test_unit'], {'src/pkg/_impl.py': [3]}, [])
    assert record.tests_on(coverage, 'src/pkg/_impl.py', [3]) == {'tests/test_unit.py::ShapeCase::test_list'}, coverage


def test_calls_carry_digests_of_input_and_output_and_slotted_state_is_part_of_the_input(tmp_path):
    project(tmp_path)
    targets = [('src/pkg/_impl.py', 'shape'), ('src/pkg/_impl.py', 'Box.grow')]
    _, calls, _ = run(tmp_path, [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_shape.py'], {}, targets)
    by = {(c['test'], c['function']): c for c in calls}
    grow = by['tests/test_shape.py::test_box', 'src/pkg/_impl.py#Box.grow']
    assert '"size"' in grow['input_shown'] and '"2"' in grow['input_shown'], grow  # the slot's value is in the input
    assert grow['output_shown'] == '["return", "3"]', grow
    assert by['tests/test_shape.py::test_list', 'src/pkg/_impl.py#shape']['output_shown'] == '["return", "\'list\'"]'
    # The same call made again gives the same digests: they are stable across processes.
    _, again, _ = run(tmp_path, [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_shape.py'], {}, targets)
    assert {(c['test'], c['function'], c['input'], c['output']) for c in again} == \
        {(c['test'], c['function'], c['input'], c['output']) for c in calls}


def test_an_exception_is_the_output_of_the_call_it_left(tmp_path):
    project(tmp_path)
    (tmp_path/'src/pkg/_impl.py').write_text(PACKAGE['src/pkg/_impl.py'].replace('return "other"', 'raise ValueError(value)'))
    (tmp_path/'tests/test_shape.py').write_text(PACKAGE['tests/test_shape.py'].replace(
        'assert shape(3) == "other"', 'import pytest\n    with pytest.raises(ValueError):\n        shape(3)'))
    _, calls, _ = run(tmp_path, [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_shape.py'], {},
                      [('src/pkg/_impl.py', 'shape')])
    [other] = [c for c in calls if c['test'] == 'tests/test_shape.py::test_other']
    assert other['output_shown'].startswith('["raise", "ValueError", "3"]'), other


def test_pairing_compares_only_equal_inputs():
    def call(test, n, i, o):
        return dict(test=test, function='m.py#f', n=n, input=i, output=o, input_shown=i, output_shown=o)
    before = [call('t::a', 0, 'x', '1'), call('t::a', 1, 'y', '2'), call('t::b', 0, 'z', '3')]
    after = [call('t::a', 0, 'x', '9'), call('t::a', 1, 'Y', '2'), call('t::c', 0, 'z', '3')]
    rows, counts = record.pair(before, after)
    assert [(r['test'], r['n'], r['before'], r['after']) for r in rows] == [('t::a', 0, '1', '9')]
    assert counts == dict(paired=2, compared=1, inputs_differ=1, unpaired=2, unreadable=0)


def test_a_value_a_cached_property_holds_is_not_part_of_an_object(tmp_path):
    # Whether a lazy attribute was computed yet says when someone asked for it, not what the object is: the code before
    # a change may read it earlier than the code after.
    from functools import cached_property

    (tmp_path/'m.py').write_text(textwrap.dedent('''\
        import functools


        class Req:
            def __init__(self):
                self.path = "/"

            @functools.cached_property
            def cookies(self):
                return {}


        def describe(req):
            return req
        '''))
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_m.py').write_text(textwrap.dedent('''\
        from m import Req, describe


        def test_fresh():
            describe(Req())


        def test_read():
            req = Req()
            req.cookies
            describe(req)
        '''))
    _, calls, _ = run(tmp_path, [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests'], {}, [('m.py', 'describe')])
    assert len({c['input'] for c in calls}) == 1 and len({c['output'] for c in calls}) == 1, calls
    assert cached_property  # the decorator the project uses here
