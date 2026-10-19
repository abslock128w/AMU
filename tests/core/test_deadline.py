"""A deadline (CI's wall cap): amu reports what it found before it and lists the steps it did not start."""
import difflib
import sys
import time

from amu.core import analyze, shadow, trust

LIB0 = ''.join(f'def f{i}():\n    return {i}\n\n\n' for i in range(9))
LIB1 = LIB0.replace('return 1\n', 'return 10\n').replace('return 4\n', 'return 40\n').replace('return 8\n', 'return 80\n')
TESTS = 'import lib\n\n\ndef test_f4():\n    assert lib.f4() == 4\n\n\ndef test_f1():\n    assert lib.f1()\n'


def change(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'lib.py').write_text(LIB1)
    (tmp_path/'tests/test_lib.py').write_text(TESTS)
    return ''.join(difflib.unified_diff(LIB0.splitlines(True), LIB1.splitlines(True), 'a/lib.py', 'b/lib.py', n=1))


def test_steps_past_the_deadline_are_not_started_and_the_flip_found_before_it_is_kept(tmp_path, monkeypatch):
    diff = change(tmp_path)
    # The deadline comes as the flip's reruns end: isolating its hunk (test_isolate) and side-by-side are past it.
    reruns, ended = trust.triad, []

    def triad(*args, **kwargs):
        found = reruns(*args, **kwargs)
        ended.append(True)
        return found
    monkeypatch.setattr(trust, 'triad', triad)
    monkeypatch.setattr(shadow, 'late', lambda deadline: deadline is not None and bool(ended))
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     deadline=time.monotonic() + 600)
    assert report['flips'] == 1 and [row['id'] for row in report['predicted_failures']] == ['tests/test_lib.py::test_f4']
    [row] = report['flip_trust']
    assert row['label'] == 'fails' and 'isolated' not in row, row
    assert report['cut'] == ['flip isolation'] + (['side-by-side'] if sys.version_info >= (3, 12) else [])
    assert report['gaps'][0] == 'not done before the wall cap: ' + ', '.join(report['cut'])
    assert 'side_by_side' not in report


def test_a_deadline_already_past_runs_nothing_and_says_so(tmp_path):
    diff = change(tmp_path)
    started = time.monotonic()
    report = analyze(tmp_path, diff, full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable,
                     deadline=time.monotonic())
    assert time.monotonic() - started < 20
    assert report['flips'] is None and report['executed']['ran'] == 0
    assert report['cut'] == ['2 of 2 candidate tests'] and report['gaps'][0] == 'not done before the wall cap: 2 of 2 candidate tests'


def test_without_a_deadline_nothing_is_cut(tmp_path):
    report = analyze(tmp_path, change(tmp_path), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert report['flips'] == 1 and 'cut' not in report and report['flip_trust'][0]['isolated']['finished']
    assert not [gap for gap in report['gaps'] if 'wall cap' in gap]


def test_the_reruns_of_a_flip_stop_at_the_deadline_below_their_own_floor(tmp_path):
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_slow.py').write_text('import time\n\n\ndef test_slow():\n    time.sleep(60)\n')
    runner = shadow.runners.get(tmp_path, 'pytest')
    started = time.monotonic()
    verdicts, _ = trust.triad(runner, sys.executable, tmp_path, ['tests/test_slow.py::test_slow'], ['tests/test_slow.py::test_slow'],
                              tmp_path/'scratch', 60, {}, deadline=time.monotonic() + 3)
    assert time.monotonic() - started < 15
    assert verdicts['tests/test_slow.py::test_slow']['reruns'] == dict(in_process=None, fresh_process=None, after_set=None)


def test_the_changes_own_tests_the_deadline_leaves_without_a_result_are_named_not_dropped(tmp_path):
    # The cache of earlier passes answers some replayed tests and the deadline stops the run of the rest on the code
    # before the change (Django #21610 under a 100 s wall cap: 4 of 7 answered, 3 rows of change evidence lost).
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_own.py').write_text('import time\n\n\ndef test_a():\n    pass\n\n\ndef test_b():\n    time.sleep(60)\n')
    a, b = 'tests/test_own.py::test_a', 'tests/test_own.py::test_b'

    class Cache:
        def lookup(self, tree, ids):
            return {i: 'passed' for i in ids if i == a}

        def store(self, tree, found):
            pass
    started = time.monotonic()
    rows, _, unfinished = shadow._replay(shadow.runners.get(tmp_path, 'pytest'), sys.executable, tmp_path, tmp_path, {}, {}, {},
                                         True, [a, b], {a: 'passed', b: 'passed'}, tmp_path/'scratch', 60, Cache(),
                                         deadline=time.monotonic() + 3)
    assert time.monotonic() - started < 15
    assert rows == [dict(id=a, head='passed', base='passed', verdict='none')] and unfinished == [b]
