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
    assert '### Behaviour differs on the same input (1 change, 1 call)' in body, body


def test_a_difference_neither_side_repeats_is_not_a_row(tmp_path):
    lib0 = 'import random\n\n\ndef noisy(x):\n    return random.random() + x\n'
    lib1 = 'import random\n\n\ndef noisy(x):\n    return random.random() + x + 0\n'
    tests = 'from lib import noisy\n\n\ndef test_noisy():\n    assert noisy(1) >= 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    assert report['side_by_side']['unstable'] == 1, report['side_by_side']


def test_what_a_call_leaves_on_its_arguments_is_shown_but_not_a_difference(tmp_path):
    # A constructor stores the callable itself instead of a lambda around it: the result (None) is the same, and what
    # differs on the object is how it holds the callable, not what calling it does. Only the result is compared.
    lib0 = 'class Wrap:\n    def __init__(self, fn):\n        self.call = lambda v: fn(v)\n'
    lib1 = 'class Wrap:\n    def __init__(self, fn):\n        self.call = fn\n'
    tests = 'from lib import Wrap\n\n\ndef test_wrap():\n    assert Wrap(abs).call(-1) == 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    assert report['side_by_side']['compared_calls'] == 1, report['side_by_side']


def test_a_changed_default_is_compared_when_the_caller_left_it_out(tmp_path):
    # The input includes every argument, defaults applied: a caller that leaves `code` out gets 302 before and 303 after,
    # so the inputs never matched. A parameter whose default changed is held apart, and each side holding its own
    # default counts as the same call.
    lib0 = 'def redirect(location, code=302):\n    return (code, location)\n'
    lib1 = 'def redirect(location, code=303):\n    return (code, location)\n'
    tests = ('from lib import redirect\n\n\ndef test_default():\n    assert redirect("/a")[1] == "/a"\n\n\n'
             'def test_explicit():\n    assert redirect("/a", 301)[0] == 301\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['behaviour_differences']
    assert row['test'] == 'tests/test_lib.py::test_default' and "code left at each side's default" in row['input'], row
    assert row['before']['result'] == ['return', ['tuple', ['302', "'/a'"], 2]], row
    assert row['after']['result'] == ['return', ['tuple', ['303', "'/a'"], 2]], row


def test_where_each_side_runs_is_not_a_difference(tmp_path):
    # The two sides run in different copies of the tree: a returned path holds a different directory on each.
    lib0 = 'import os\n\n\ndef where(name):\n    return os.path.join(os.path.dirname(__file__), name)\n'
    lib1 = lib0.replace('return os.path.join(', 'return os.path.join(  # same\n        ')
    tests = 'from lib import where\n\n\ndef test_where():\n    assert where("x").endswith("x")\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['side_by_side']['compared_calls'] == 1 and report['behaviour_differences'] == [], report['side_by_side']


# Values that belong to the process or the checkout, not to what the code does. The merge base runs the tests behind a
# difference twice, the second time in another checkout made the way the head's was made; a field that differs between
# those two runs is left out of the comparison. No rule names a kind of value.
KEY = textwrap.dedent('''\
    import os


    class Key:
        __slots__ = ("name", "size", "_h", "owner")

        def __init__(self, name, size=0, eager=False):
            self.name = name
            self.size = size
            self._h = hash(name) if eager else None
            self.owner = os.getpid() if eager else None

        def __hash__(self):
            if self._h is None:
                self._h = hash(self.name)
            return self._h
    ''')


def test_a_cache_filled_with_a_process_value_is_not_a_difference(tmp_path):
    lib0 = KEY + '\n\ndef make(name):\n    key = Key(name)\n    hash(key)\n    return key\n'
    lib1 = KEY + '\n\ndef make(name):\n    key = Key(name)\n    return key\n'
    tests = 'from lib import make\n\n\ndef test_make():\n    assert make("a").name == "a"\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    assert report['side_by_side']['excluded_fields'] >= 1, report['side_by_side']


def test_where_and_how_the_checkout_was_made_is_not_a_difference(tmp_path):
    lib0 = textwrap.dedent('''\
        import os
        from pathlib import Path


        def where():
            here = Path(__file__).resolve().parent
            return {"parts": list(here.parts), "repository": os.path.isdir(os.path.join(here, ".git")), "n": 1}
        ''')
    lib1 = lib0.replace('"n": 1}', '"n": 2 - 1}')
    tests = 'from lib import where\n\n\ndef test_where():\n    assert where()["n"] == 1\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert report['behaviour_differences'] == [], report['behaviour_differences']
    assert report['side_by_side']['excluded_fields'] >= 2, report['side_by_side']


def test_a_real_difference_beside_a_process_value_is_a_row(tmp_path):
    lib0 = KEY + '\n\ndef make(name):\n    return Key(name, len(name), eager=True)\n'
    lib1 = KEY + '\n\ndef make(name):\n    return Key(name, len(name) + 1, eager=True)\n'
    tests = 'from lib import make\n\n\ndef test_make():\n    assert make("ab").name == "ab"\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#make', row


def test_inputs_that_differ_only_in_a_process_value_are_compared(tmp_path):
    lib0 = KEY + '\n\ndef label(key):\n    return key.name.upper()\n'
    lib1 = KEY + '\n\ndef label(key):\n    return key.name.lower()\n'
    tests = 'from lib import Key, label\n\n\ndef test_label():\n    assert label(Key("Ab", eager=True))\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#label' and row['before']['result'] == ['return', "'AB'"], row


# A nested function without arguments reads its state from the enclosing call (its closure). The change reorders the
# calls without changing what the nested function returns for a given state: compared by call order, every pair would
# differ; compared on equal inputs, with the closure part of the input, none does.
NESTED0 = textwrap.dedent('''\
    def labels(words):
        out = []
        for word in words:
            def show():
                return word.upper()
            out.append(show())
        return out
    ''')
NESTED1 = textwrap.dedent('''\
    def labels(words):
        out = []
        for word in sorted(words):
            def show():
                return str.upper(word)
            out.append(show())
        return out
    ''')
NESTED_TESTS = 'from lib import labels\n\n\ndef test_labels():\n    assert set(labels(["b", "a", "c"])) == {"A", "B", "C"}\n'


def test_a_nested_function_is_compared_with_its_closure_as_input(tmp_path):
    report = change(tmp_path, {'lib.py': NESTED0, 'tests/test_lib.py': NESTED_TESTS}, {'lib.py': NESTED1})
    rows = [row for row in report['behaviour_differences'] if row['target'].endswith('show')]
    assert rows == [], rows
