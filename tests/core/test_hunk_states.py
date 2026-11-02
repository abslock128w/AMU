"""The first line from per-hunk evidence states: the worst hunk is what it reports; every hunk is listed."""
import difflib
import subprocess
import sys
import textwrap

import pytest

from amu.ci.render import render
from amu.core import analyze

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')
HERE = f'{sys.version_info[0]}.{sys.version_info[1]}'

LIB = textwrap.dedent('''\
    def area(w, h):
        """Area of a w by h box."""
        # width times height
        return w * h


    def volume(w, h, d):
        return area(w, h) * d
    ''')
TESTS = 'from lib import area, volume\n\n\ndef test_area():\n    assert area(2, 3) == 6\n\n\ndef test_volume():\n    assert volume(1, 2, 3) == 6\n'


def change(tmp_path, before, after):
    for name, text in before.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base']):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    for name, text in after.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    diff = ''.join(''.join(difflib.unified_diff(before.get(n, '').splitlines(True), after[n].splitlines(True), 'a/' + n, 'b/' + n))
                   for n in after if before.get(n) != after[n])
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    return report, render(report, dict(head='a' * 40, merge_base='b' * 40)).splitlines()


def test_a_comment_typo_is_not_code(tmp_path):
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': LIB.replace('width times', 'width by')})
    assert [row['state'] for row in report['hunks']] == ['not-code'], report['hunks']
    assert body[0].startswith('**AMU: docstring or comment changes only**'), body[0]


def test_a_docstring_edit_beside_a_new_function_is_new_code_only(tmp_path):
    after = LIB.replace('Area of a w by h box.', 'Area of a box, w by h.') + '\n\ndef perimeter(w, h):\n    return 2 * (w + h)\n'
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': after})
    assert sorted(row['state'] for row in report['hunks']) == ['new-code', 'not-code'], report['hunks']
    assert body[0].startswith('**AMU: adds new code only**'), body[0]


def test_a_new_function_existing_code_calls_is_not_new_code(tmp_path):
    after = LIB.replace('return area(w, h) * d', 'return base(w, h) * d') + '\n\ndef base(w, h):\n    return area(w, h)\n'
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': after})
    assert 'new-code' not in [row['state'] for row in report['hunks']], report['hunks']
    assert 'adds new code' not in body[0], body[0]


def test_every_hunk_ran_on_both_sides(tmp_path):
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': LIB.replace('* d', '* d * 1')})
    assert [row['state'] for row in report['hunks']] == ['executed-both-sides'], report['hunks']
    assert body[0].endswith('ran all 2 tests; every changed hunk ran on both sides, in up to 1 test'), body[0]
    assert '### Changed hunks (1)' in body


def test_a_hunk_no_test_runs_is_named_in_the_first_line(tmp_path):
    lib = LIB + '\n\ndef unused(x):\n    return x + 1\n'
    report, body = change(tmp_path, {'lib.py': lib, 'tests/test_lib.py': TESTS}, {'lib.py': lib.replace('x + 1', 'x + 2')})
    assert [row['state'] for row in report['hunks']] == ['executed-none'], report['hunks']
    assert body[0].endswith('; 1 of 1 changed hunk ran in no test: `lib.py:12`'), body[0]


@pytest.mark.parametrize('guard,reason', [
    # The guard is a constant in another module, imported: the function runs only when it holds.
    ("import sys\nNEWER = sys.version_info[:2] >= (99, 0)\n", f'not executable on Python {HERE} here'),
    ("import sys\nNEWER = sys.platform == 'not-this-platform'\n", f'not executable on {sys.platform} here'),
    ("try:\n    import amu_no_such_module\n    NEWER = True\nexcept ImportError:\n    NEWER = False\n",
     'not executable on amu_no_such_module not installed here'),
])
def test_code_only_called_under_a_condition_false_here_cannot_run_here(tmp_path, guard, reason):
    lib = ('from compat import NEWER\n\n\ndef build(x):\n    if NEWER:\n        return replace(x)\n    return x\n\n\n'
           'def replace(x):\n    return x * 2\n')
    files = {'compat.py': guard, 'lib.py': lib, 'tests/test_lib.py': 'from lib import build\n\n\ndef test_build():\n    assert build(1)\n'}
    report, body = change(tmp_path, files, {'lib.py': lib.replace('x * 2', 'x * 3')})
    [row] = report['hunks']
    assert row['state'] == 'not-executable-here' and row['why'] == reason, row
    assert body[0].endswith(f'; 1 of 1 changed hunk {reason}: `lib.py:11`'), body[0]


def test_a_line_inside_a_literal_spread_over_lines_is_seen_through_its_statement(tmp_path):
    # An element added to a multi-line __all__ or __slots__ has no bytecode of its own: no line event fires on it.
    lib = LIB + '\n\n__all__ = [\n    "area",\n    "volume",\n    "x1",\n    "x2",\n]\n'
    report, body = change(tmp_path, {'lib.py': lib, 'tests/test_lib.py': TESTS}, {'lib.py': lib.replace('    "x2",\n', '    "x2",\n    "x3",\n')})
    [row] = report['hunks']
    assert row['state'] == 'executed-both-sides', row
    assert 'ran in no test' not in body[0], body[0]
