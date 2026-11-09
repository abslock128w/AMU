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


def test_a_comment_added_above_new_code_in_a_function_counts_as_that_code(tmp_path):
    # A comment has no bytecode; it stood for the whole enclosing function, whose def line runs at import, so a hunk
    # in a function only a newer Python calls read "ran on both sides (at import)".
    lib = ('import sys\nNEWER = sys.version_info[:2] >= (99, 0)\n\n\ndef build(x):\n    if NEWER:\n        return replace(x)\n'
           '    return x\n\n\ndef replace(x):\n    return x * 2\n')
    after = lib.replace('def replace(x):\n    return x * 2\n',
                        'def replace(x):\n    # A local helper, so the module function is not changed in place.\n    def twice(v):\n'
                        '        return v * 2\n\n    return twice(x)\n')
    files = {'lib.py': lib, 'tests/test_lib.py': 'from lib import build\n\n\ndef test_build():\n    assert build(1)\n'}
    report, body = change(tmp_path, files, {'lib.py': after})
    assert [row['state'] for row in report['hunks']] == ['not-executable-here'], report['hunks']


def test_a_nonlocal_line_counts_as_the_statement_after_it(tmp_path):
    # `nonlocal` has no bytecode and holds no statement with any: it read "ran in no test" inside a function every test runs.
    lib = 'def deco(f):\n    count = 0\n\n    def wrap(x):\n        return f(x)\n    return wrap\n'
    after = lib.replace('    def wrap(x):\n        return f(x)\n', '    def wrap(x):\n        nonlocal count\n        count += 1\n        return f(x)\n')
    tests = 'from lib import deco\n\n\ndef test_deco():\n    assert deco(abs)(-2) == 2\n'
    report, body = change(tmp_path, {'lib.py': lib, 'tests/test_lib.py': tests}, {'lib.py': after})
    assert [row['state'] for row in report['hunks']] == ['executed-both-sides'], report['hunks']


# A changed line under a condition false in this job did not run, though the rest of its hunk did: the row names it.
@pytest.mark.parametrize('condition', ["sys.platform == 'not-this-platform'", "sys.platform.startswith('not-this')",
                                       'sys.version_info >= (99, 0)', "os.name == 'not-this-os'"])
def test_a_guarded_line_in_a_hunk_that_ran_is_not_executable_here(tmp_path, condition):
    lib0 = 'import os\nimport sys\n\n\ndef join(parts):\n    joined = "/".join(parts)\n    return joined\n'
    lib1 = (f'import os\nimport sys\n\n\ndef join(parts):\n    if {condition}:\n        joined = "\\\\".join(parts)\n'
            '    else:\n        joined = "/".join(parts)\n    return joined\n')
    tests = 'from lib import join\n\n\ndef test_join():\n    assert join(["a", "b"]) == "a/b"\n'
    report, body = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    [row] = report['hunks']
    assert row['state'] == 'not-executable-here' and row['line'] == 7, row
    assert row['why'].startswith('not executable on ') and row['why'].endswith(f'(needs {condition})'), row
    assert row['tests'] == 1, row  # the rest of the hunk ran
    assert '1 of 1 changed hunk not executable on' in body[0] and '`lib.py:7`' in body[0], body[0]


@pytest.mark.parametrize('flag', ['sys.version_info >= (99, 0)', "sys.platform == 'not-this-platform'"])
def test_code_reachable_only_through_a_conditional_expression_false_here_cannot_run_here(tmp_path, flag):
    compat = f'import sys\nNEWER = {flag}\n'
    lib0 = textwrap.dedent('''\
        from compat import NEWER


        class Base:
            def show(self):
                return "base"


        def helper():
            a = 1
            b = 2
            c = 3
            return a + b + c


        KINDS = {int: Base}
        ''')
    lib1 = textwrap.dedent('''\
        from compat import NEWER


        class Base:
            def show(self):
                return "base"


        class Newer(Base):
            def show(self):
                return "newer"


        def helper():
            a = 1
            b = 2
            c = 3
            return a + b + c


        KINDS = {int: Base, **({float: Newer} if NEWER else {})}
        ''')
    tests = 'from lib import KINDS\n\n\ndef test_kinds():\n    assert KINDS[int]().show() == "base"\n'
    report, body = change(tmp_path, {'compat.py': compat, 'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    # The class is used only inside the branch not taken here; the KINDS line itself runs at import, so it ran.
    assert [row['state'] for row in report['hunks']] == ['not-executable-here', 'executed-both-sides'], report['hunks']
    assert '1 of 2 changed hunks not executable on' in body[0], body[0]


# A chained comparison as a guard: evaluated pair by pair when it can be told, unknown (never gated) when not.
@pytest.mark.parametrize('condition,gated', [('(99, 0) <= sys.version_info < (100, 0)', True),
                                             ('(21,) <= self.connection.version < (23,)', False),
                                             ('not -n <= len(parts) < n', False)])
def test_a_chained_comparison_guard_is_evaluated_or_unknown_never_a_crash(tmp_path, condition, gated):
    lib0 = 'import sys\n\n\nclass Joiner:\n    def join(self, parts, n=9):\n        joined = "/".join(parts)\n        return joined\n'
    lib1 = (f'import sys\n\n\nclass Joiner:\n    def join(self, parts, n=9):\n        if {condition}:\n'
            '            joined = "\\\\".join(parts)\n        else:\n            joined = "/".join(parts)\n        return joined\n')
    tests = 'from lib import Joiner\n\n\ndef test_join():\n    assert Joiner().join(["a", "b"]) == "a/b"\n'
    report, body = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert 'error' not in report, report.get('error')
    [row] = report['hunks']
    assert (row['state'] == 'not-executable-here') is gated, row


# Only one operand of the conditional expression is gated; the line itself ran in a test, so it is executed.
def test_a_gated_operand_on_a_line_that_ran_is_executed(tmp_path):
    lib0 = 'import os\n\n\ndef make(path):\n    maker = os.makedirs\n    maker(path, exist_ok=True)\n    return path\n'
    lib1 = ('import os\nimport sys\n\n\ndef make(path):\n    maker = os.mkdir if sys.version_info >= (99, 0) else os.makedirs\n'
            '    maker(path, exist_ok=True)\n    return path\n')
    tests = 'from lib import make\n\n\ndef test_make(tmp_path):\n    assert make(str(tmp_path / "a" / "b"))\n'
    report, body = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    states = {row['line']: row['state'] for row in report['hunks']}
    assert 'not-executable-here' not in states.values(), report['hunks']
    assert states.get(6) == 'executed-both-sides', report['hunks']
    assert 'not executable' not in body[0], body[0]


def test_a_signature_change_says_which_tests_called_the_function(tmp_path):
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': LIB.replace('def area(w, h):', 'def area(w, h=1):')})
    [row] = report['hunks']
    assert row['state'] == 'executed-both-sides' and row['tests'] == 2 and row['why'] == '', row
    assert body[0].endswith('every changed hunk ran on both sides, in up to 2 tests'), body[0]


def test_a_signature_change_no_test_calls_ran_in_no_test(tmp_path):
    lib = LIB + '\n\ndef unused(x):\n    return x\n'
    report, body = change(tmp_path, {'lib.py': lib, 'tests/test_lib.py': TESTS}, {'lib.py': lib.replace('def unused(x):', 'def unused(x, y=0):')})
    [row] = report['hunks']
    assert row['state'] == 'executed-none', row


def test_a_class_whose_methods_no_test_calls_did_not_run_though_its_lines_ran_at_import(tmp_path):
    after = LIB + '\n\nclass Helper:\n    def run(self):\n        return area(1, 1)\n\n\nREGISTRY = [Helper]\n'
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': after})
    [row] = report['hunks']
    assert row['state'] == 'executed-none', row
    assert '1 of 1 changed hunk ran in no test' in body[0], body[0]


def test_type_annotations_only(tmp_path):
    after = LIB.replace('def area(w, h):', 'def area(w: int, h: int) -> int:')
    report, body = change(tmp_path, {'lib.py': LIB, 'tests/test_lib.py': TESTS}, {'lib.py': after})
    assert [row['state'] for row in report['hunks']] == ['annotations-only'], report['hunks']
    assert body[0].startswith('**AMU: type annotations only**'), body[0]


def test_reach_guesses_of_the_static_graph_are_not_gaps_once_the_hunks_were_recorded(tmp_path):
    lib = 'def hidden():\n    return 1\n'
    tests = 'import lib\n\n\ndef test_by_name():\n    assert getattr(lib, "hid" + "den")() >= 1\n'
    report, body = change(tmp_path, {'lib.py': lib, 'tests/test_lib.py': tests}, {'lib.py': lib.replace('return 1', 'return 2')})
    assert [row['state'] for row in report['hunks']] == ['executed-both-sides'], report['hunks']
    assert not any('five source hops' in g or 'call sites unresolved' in g for g in report.get('gaps') or []), report['gaps']


# Flask 5812, jinja 2099: unchanged lines inside one block of changes are part of it when it is re-applied.
def test_comments_removed_around_an_unchanged_line_are_not_code(tmp_path):
    lib0 = 'def area(w, h):\n    # width\n    x = w\n    # height\n    return x * h\n'
    lib1 = 'def area(w, h):\n    x = w\n    return x * h\n'
    tests = 'from lib import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n'
    report, body = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    assert [row['state'] for row in report['hunks']] == ['not-code'], report['hunks']
    assert body[0].startswith('**AMU: docstring or comment changes only**'), body[0]


# A docstring edit and a code edit a line apart share a diff block: the docstring part is not code, the code part has
# its own state.
def test_a_block_mixing_a_docstring_edit_and_a_code_edit_is_split(tmp_path):
    lib0 = 'def area(w, h):\n    """Area of a box."""\n    x = w\n    return x * h\n\n\ndef unused():\n    return 1\n'
    lib1 = 'def area(w, h):\n    """Area of a w by h box."""\n    x = w\n    return h * x\n\n\ndef unused():\n    return 1\n'
    tests = 'from lib import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n'
    report, body = change(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1})
    states = sorted((row['line'], row['state']) for row in report['hunks'])
    assert states == [(2, 'not-code'), (4, 'executed-both-sides')], report['hunks']
    assert body[0].endswith('every changed hunk ran on both sides, in up to 1 test'), body[0]


# jinja 2099: a generator script in a top-level scripts/ folder that the package does not import is not production.
def test_a_script_the_package_does_not_import_is_not_production(tmp_path):
    lib = LIB
    script = 'def build():\n    return 1\n\n\nif __name__ == "__main__":\n    print(build())\n'
    report, body = change(tmp_path, {'lib.py': lib, 'scripts/gen.py': script, 'tests/test_lib.py': TESTS},
                          {'lib.py': lib.replace('* d', '* d * 1'), 'scripts/gen.py': script.replace('return 1', 'return 2')})
    states = {row['path']: row['state'] for row in report['hunks']}
    assert states == {'lib.py': 'executed-both-sides', 'scripts/gen.py': 'not-production'}, report['hunks']
    assert 'scripts/gen.py' not in body[0] and body[0].endswith('every changed hunk ran on both sides, in up to 1 test'), body[0]


TYPED = 'from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from collections.abc import Mapping\n\n    Box = Mapping[str, int]\nelse:\n    Box = dict\n\n' + LIB


def test_a_change_under_type_checking_is_typing_only_not_unrun_code(tmp_path):
    """An alias or import under ``if TYPE_CHECKING:`` never runs: it is typing, not a hunk no test reached. Added, edited
    and deleted lines alike; the else branch does run and is not touched by this."""
    edited = TYPED.replace('Mapping[str, int]', 'Mapping[str, float]')
    report, body = change(tmp_path, {'lib.py': TYPED, 'tests/test_lib.py': TESTS}, {'lib.py': edited})
    assert [row['state'] for row in report['hunks']] == ['annotations-only'], report['hunks']
    assert 'TYPE_CHECKING' in report['hunks'][0]['why'] and body[0].startswith('**AMU: type annotations only**'), body[0]


def test_a_deleted_import_under_type_checking_is_typing_only(tmp_path):
    before = TYPED.replace('    from collections.abc import Mapping\n', '    from collections.abc import Mapping\n    from os import PathLike\n')
    report, body = change(tmp_path, {'lib.py': before, 'tests/test_lib.py': TESTS}, {'lib.py': TYPED})
    assert [row['state'] for row in report['hunks']] == ['annotations-only'], report['hunks']
    assert 'ran in no test' not in body[0], body[0]


def test_the_else_of_type_checking_is_code(tmp_path):
    report, body = change(tmp_path, {'lib.py': TYPED, 'tests/test_lib.py': TESTS}, {'lib.py': TYPED.replace('Box = dict\n', 'Box = list\n')})
    assert [row['state'] for row in report['hunks']] == ['executed-both-sides'], report['hunks']
