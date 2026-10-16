"""B2: changed functions executed side by side on the inputs the tests call them with."""
import difflib
import sys

import pytest

from amu.core import analyze

NEW = sys.version_info >= (3, 12)
pytestmark = pytest.mark.skipif(not NEW, reason='sys.monitoring needs Python 3.12')


def change(tmp_path, files_before, files_after):
    for name, text in files_after.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    diff = ''.join(''.join(difflib.unified_diff(files_before[n].splitlines(True), files_after[n].splitlines(True), 'a/' + n, 'b/' + n))
                   for n in files_after if files_before.get(n) != files_after[n])
    return analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)


def test_a_behaviour_change_no_test_catches_is_reported_with_its_input(tmp_path):
    lib0 = 'def clamp(v, top=10):\n    return min(v, top)\n'
    lib1 = 'def clamp(v, top=10):\n    return min(v, top - 1)\n'
    tests = 'from lib import clamp\n\n\ndef test_small():\n    assert clamp(3) == 3\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    assert report['flips'] == 0, report['summary']
    assert report['side_by_side']['compared_calls'] == 1 and report['side_by_side']['targets'] == 1, report['side_by_side']
    assert report['behaviour_differences'] == [], report['behaviour_differences']  # clamp(3) is 3 either way
    tests2 = tests + '\n\ndef test_big():\n    assert clamp(50) > 0\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests2}, {'lib.py': lib1, 'tests/test_lib.py': tests2})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#clamp' and row['differs'] == ['result'] and '50' in row['input'], row
    assert row['before']['result'] == ['return', '10'] and row['after']['result'] == ['return', '9'], row
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert '### Behaviour differs on captured input (1)' in body and '`lib.py#clamp`' in body, body


def test_mutation_printing_and_exceptions_count_and_noise_does_not(tmp_path):
    lib0 = ('import random\n\n\ndef add(items, x):\n    items.append(x)\n    return len(items)\n\n\n'
            'def shout(x):\n    print(x)\n\n\ndef parse(x):\n    return int(x)\n\n\ndef noisy(x):\n    return random.random() + x\n')
    lib1 = ('import random\n\n\ndef add(items, x):\n    items.append(x)\n    items.append(x)\n    return len(items) - 1\n\n\n'
            'def shout(x):\n    print(x.upper())\n\n\ndef parse(x):\n    return int(x, 16)\n\n\ndef noisy(x):\n    return random.random() + x + 0\n')
    tests = ('from lib import add, shout, parse, noisy\n\n\ndef test_all():\n    add([], 1)\n    shout("hi")\n'
             '    parse("10")\n    noisy(1)\n')
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target'].split('#')[1]: row['differs'] for row in report['behaviour_differences']}
    assert found == {'add': ['args_after'], 'shout': ['stdout'], 'parse': ['result']}, report['behaviour_differences']
    assert report['side_by_side']['unstable'] == 1  # noisy: neither version repeats itself, so nothing is said


def test_a_method_using_super_is_compared_with_its_class(tmp_path):
    lib0 = ('class Base:\n    def size(self):\n        return 1\n\n\nclass Box(Base):\n    def __init__(self, n):\n        self.n = n\n\n'
            '    def size(self):\n        return super().size() + self.n\n')
    lib1 = lib0.replace('return super().size() + self.n', 'return super().size() * self.n')
    tests = 'from lib import Box\n\n\ndef test_box():\n    assert Box(2).size()\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    [row] = report['behaviour_differences']
    assert row['target'] == 'lib.py#Box.size' and row['before']['result'] == ['return', '3'] and row['after']['result'] == ['return', '2'], row


def test_changed_functions_that_call_each_other_run_as_changed_together(tmp_path):
    # Found on Django 7b8504eaab: the changed caller needs state the changed callee now sets. Mixing versions raised.
    lib0 = ('class Detector:\n    def prepare(self):\n        self.keys = [1]\n\n    def detect(self):\n        self.prepare()\n'
            '        return len(self.keys)\n')
    lib1 = ('class Detector:\n    def prepare(self):\n        self.keys = [1]\n        self.extra = [2]\n\n    def detect(self):\n'
            '        self.prepare()\n        return len(self.keys) + len(self.extra)\n')
    tests = 'from lib import Detector\n\n\ndef test_detect():\n    assert Detector().detect()\n'
    report = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests})
    found = {row['target']: row for row in report['behaviour_differences']}
    assert found['lib.py#Detector.detect']['after']['result'] == ['return', '2'], found
    assert found['lib.py#Detector.detect']['before']['result'] == ['return', '1']
    assert found['lib.py#Detector.prepare']['differs'] == ['args_after']
