"""Behaviour differences from paired records: each side's calls recorded in its own run, compared on equal inputs."""
import difflib
import subprocess
import sys
import textwrap

import pytest

from amu.core import analyze

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')


def change(tmp_path, before, after, **kwargs):
    for name, text in before.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    for name, text in after.items():
        (tmp_path/name).write_text(text)
    diff = ''.join(''.join(difflib.unified_diff(before.get(n, '').splitlines(True), after[n].splitlines(True), 'a/' + n, 'b/' + n))
                   for n in after if before.get(n) != after[n])
    return analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable, **kwargs)


# A slotted class gains a field its constructor sets, and a method reads it. On the code after the change every object
# has the field; on the code before it none has. Running the new method on an object the old code built raises
# AttributeError: that is how the old side-by-side read it. No real call does that.
SLOTTED0 = textwrap.dedent('''\
    class Field:
        __slots__ = ("name", "alias")

        def __init__(self, name, alias=None):
            self.name = name
            self.alias = alias or name

        def renamed(self, name):
            return Field(name, None if self.alias == self.name else self.alias)
    ''')
SLOTTED1 = textwrap.dedent('''\
    class Field:
        __slots__ = ("name", "alias", "alias_is_default")

        def __init__(self, name, alias=None):
            self.name = name
            self.alias = alias or name
            self.alias_is_default = alias is None

        def renamed(self, name):
            return Field(name, None if self.alias_is_default else self.alias)
    ''')
TESTS = textwrap.dedent('''\
    from lib import Field


    def test_renamed_follows_a_default_alias():
        assert Field("a").renamed("b").alias == "b"


    def test_renamed_keeps_an_explicit_alias():
        assert Field("a", "x").renamed("b").alias == "x"
    ''')


def test_a_slotted_class_gaining_a_field_gives_no_false_row(tmp_path):
    report = change(tmp_path, {'lib.py': SLOTTED0, 'tests/test_lib.py': TESTS}, {'lib.py': SLOTTED1})
    assert report['side_by_side']['mode'] == 'paired records', report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    # The objects differ (one has a field the other lacks), so their calls are not compared: counted, not guessed.
    assert report['side_by_side']['inputs_differ'] >= 1, report['side_by_side']


def test_the_old_mode_is_behind_a_flag(tmp_path):
    report = change(tmp_path, {'lib.py': SLOTTED0, 'tests/test_lib.py': TESTS}, {'lib.py': SLOTTED1},
                    extras=('sidebyside-legacy',))
    assert report['side_by_side'].get('mode') != 'paired records'
    # Old mode: the changed methods run on objects the old code built; its set-aside rules decide what is shown.
    assert 'input_built_before_the_change' in report['side_by_side'], report['side_by_side']


def test_a_real_difference_on_an_equal_input_is_a_row(tmp_path):
    lib0 = 'def clamp(v, top=10):\n    return min(v, top)\n'
    lib1 = 'def clamp(v, top=10):\n    return min(v, top - 1)\n'
    tests = 'from lib import clamp\n\n\ndef test_big():\n    assert clamp(50) > 0\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#clamp' and row['test'] == 'tests/test_lib.py::test_big', row
    assert row['before']['result'] == ['return', '10'] and row['after']['result'] == ['return', '9'], row
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert body.splitlines()[0].endswith('; 1 behaviour difference on the same input'), body
    assert '### Behaviour differs on the same input (1)' in body, body


def test_a_difference_neither_side_repeats_is_not_a_row(tmp_path):
    lib0 = 'import random\n\n\ndef noisy(x):\n    return random.random() + x\n'
    lib1 = 'import random\n\n\ndef noisy(x):\n    return random.random() + x + 0\n'
    tests = 'from lib import noisy\n\n\ndef test_noisy():\n    assert noisy(1) >= 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    assert report['side_by_side']['unstable'] == 1, report['side_by_side']


def test_a_difference_in_what_a_call_leaves_on_its_arguments_says_so(tmp_path):
    # A constructor stores a different callable: the return value (None) is the same; the object is not.
    lib0 = 'class Wrap:\n    def __init__(self, fn):\n        self.call = lambda v: fn(v)\n'
    lib1 = 'class Wrap:\n    def __init__(self, fn):\n        self.call = fn\n'
    tests = 'from lib import Wrap\n\n\ndef test_wrap():\n    assert Wrap(abs).call(-1) == 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['behaviour_differences']
    assert row['differs'] == ['args_after'] and row['before']['result'] == row['after']['result'] == ['return', 'None'], row
    assert row['before']['args_after'] != row['after']['args_after'], row
