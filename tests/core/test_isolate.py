"""C2: the hunk that makes a flipped test fail, by delta debugging over independently applied hunks."""
import difflib
import sys

from amu.core import analyze, isolate
from amu.core.patch import changes


def test_ddmin_finds_one_cause_and_a_pair_that_only_fails_together():
    calls = []

    def one(subset):
        calls.append(subset)
        return 'fail' if 5 in subset else 'pass'
    found, finished = isolate.ddmin(list(range(8)), one, lambda: True)
    assert found == [5] and finished and len(calls) <= 8, calls
    found, _ = isolate.ddmin(list(range(8)), lambda s: 'fail' if {1, 6} <= set(s) else 'pass', lambda: True)
    assert sorted(found) == [1, 6]
    # A subset that does not run is not a pass: the search keeps the larger set rather than guess.
    found, _ = isolate.ddmin([0, 1], lambda s: 'unresolved' if len(s) == 1 else 'fail', lambda: True)
    assert found == [0, 1]
    found, finished = isolate.ddmin(list(range(8)), one, lambda: False)
    assert not finished and found == list(range(8))


LIB0 = ''.join(f'def f{i}():\n    return {i}\n\n\n' for i in range(9))


def test_hunks_apply_independently(tmp_path):
    lib1 = LIB0.replace('return 1\n', 'return 10\n').replace('return 8\n', 'return 80\n')
    (tmp_path/'lib.py').write_text(lib1)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), lib1.splitlines(True), 'a/lib.py', 'b/lib.py', n=1))
    edits, _ = changes(tmp_path, diff)
    assert len(edits['lib.py']['hunks']) == 2
    only_second = isolate.apply(LIB0, edits['lib.py'], {1})
    assert 'return 80' in only_second and 'return 10' not in only_second and 'return 1\n' in only_second
    assert isolate.apply(LIB0, edits['lib.py'], {0, 1}) == lib1 and isolate.apply(LIB0, edits['lib.py'], set()) == LIB0


def test_a_flip_names_the_hunk_that_causes_it(tmp_path):
    lib1 = LIB0.replace('return 1\n', 'return 10\n').replace('return 4\n', 'return 40\n').replace('return 8\n', 'return 80\n')
    tests = 'import lib\n\n\ndef test_f4():\n    assert lib.f4() == 4\n\n\ndef test_f1():\n    assert lib.f1()\n'
    (tmp_path/'tests').mkdir()
    (tmp_path/'lib.py').write_text(lib1)
    (tmp_path/'tests/test_lib.py').write_text(tests)
    diff = ''.join(difflib.unified_diff(LIB0.splitlines(True), lib1.splitlines(True), 'a/lib.py', 'b/lib.py', n=1))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    [row] = report['flip_trust']
    assert row['id'] == 'tests/test_lib.py::test_f4' and row['label'] == 'fails', row
    found = row['isolated']
    assert found['finished'] and [(h['file'], h['hunk'], h['of']) for h in found['hunks']] == [('lib.py', 2, 3)], found
    assert found['hunks'][0]['start'] == lib1.splitlines().index('    return 40') + 1
    from amu.ci.render import render
    body = render(report, dict(head='a' * 40, merge_base='b' * 40))
    assert 'caused by hunk 2 of 3 in `lib.py` at line' in body, body
