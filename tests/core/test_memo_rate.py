"""The test rate the shadow memo keeps between runs: it plans how many candidates run first, and a run with budget
left goes on past that plan. Retrospective 2026-10-18, sympy: the remembered rate went 15 -> 2 -> 3 -> 0.2 tests/s and
#30540 ran 48 of 12,885 candidates in 47 s of its 300 s."""
import json
import subprocess
import sys
import time
from pathlib import Path

from amu.core import runners, shadow
from amu.core.patch import changes


def _tree(tmp_path):
    """A committed tree with an uncommitted one-line change: (root, edits, memo file outside the tree)."""
    root = tmp_path/'repo'
    (root/'tests').mkdir(parents=True)
    (root/'tests'/'test_lib.py').write_text('import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n')
    (root/'lib.py').write_text('VALUE = 1\n')
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'start']):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
    (root/'lib.py').write_text('VALUE = 2\n')
    edits, _ = changes(root, '--- a/lib.py\n+++ b/lib.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n')
    return root, edits, tmp_path/'memo.json'


def _ids(count):
    return [f'tests/test_lib.py::T::test_{n}' for n in range(count)]


class _Clock:
    """shadow's ``time``: perf_counter moves only by the seconds the fake runner says its tests took."""

    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        return self.now

    def time(self):
        return time.time()


def _runner(monkeypatch, seconds):
    """Runs shadow on a clock of its own, with a runner whose n-th candidate takes ``seconds(n)``. A call that does not
    finish within its timeout reads nothing. Returns the after-side calls as (tests, seconds taken), filled as it runs."""
    clock, calls = _Clock(), []

    def execute(runner, python, tree, ids, report, timeout):
        need = sum(seconds(int(i.rsplit('_', 1)[1])) for i in ids)
        clock.now += min(need, timeout)
        if Path(report).name.startswith('after'):
            calls.append((len(ids), min(need, timeout)))
        return ({i: 'passed' for i in ids}, None) if need <= timeout else ({}, 'timed out')
    monkeypatch.setattr(shadow, 'time', clock)
    monkeypatch.setattr(runners, 'execute', execute)
    return calls


def _shadow(root, edits, ids, memo, budget):
    return shadow.run(root, edits, ids, python=sys.executable, budget=budget, runner='unittest', memo=memo)


def test_a_tiny_remembered_rate_does_not_stop_a_run_with_budget_left(tmp_path, monkeypatch):
    """#30540: a rate of 0.2 tests/s from another merge base planned 48 candidates; they ran in 47 s of 300."""
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=0.2)))
    _runner(monkeypatch, lambda n: 0.01)
    rows, info, gaps = _shadow(root, edits, _ids(400), memo, 60)
    assert info['ran'] == 400 and info['candidates'] == 400, (info, gaps)
    assert not any('lower-ranked' in gap for gap in gaps), gaps


def test_candidates_past_the_plan_stop_where_the_budget_keeps_its_share_for_the_checks_after(tmp_path, monkeypatch):
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=0.2)))
    calls = _runner(monkeypatch, lambda n: 0.01)
    rows, info, gaps = _shadow(root, edits, _ids(1000), memo, 10)
    # 40 planned at 0.2 tests/s; about 800 fit in the 8 s that are the candidates' share of 10 s at 100 tests/s.
    assert 760 <= info['ran'] == info['candidates'] <= 800, (info, gaps)
    assert sum(took for _, took in calls) <= 10 * shadow.FILL + 1e-6, calls
    assert not any('stopped at its' in gap for gap in gaps), gaps
    assert any(gap.startswith(f"{1000 - info['ran']} lower-ranked candidate(s) not run") for gap in gaps), gaps


def test_candidates_slower_than_the_ones_before_them_do_not_eat_the_share_kept_for_the_checks_after(tmp_path, monkeypatch):
    """select.order runs the shortest measured tests first, so the candidates past the plan are slower than the ones
    the pace was measured on. A pace taken from the first chunk fitted 3,160 more in 7.9 s; they took 31.6 s."""
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=0.2)))
    calls = _runner(monkeypatch, lambda n: 0.0025 if n < shadow.CHUNK else 0.01)
    rows, info, gaps = _shadow(root, edits, _ids(4000), memo, 10)
    assert shadow.CHUNK < info['ran'] == info['candidates'], (info, gaps)
    assert sum(took for _, took in calls) <= 10 * shadow.FILL + 1e-6, calls
    assert not any('stopped at its' in gap for gap in gaps), gaps


def test_a_run_stopped_at_its_budget_does_not_drag_the_remembered_rate_down(tmp_path, monkeypatch):
    """sympy #30477: 80 of 696 candidates ran before a chunk was stopped at the budget, and the stopped chunk's time was
    charged to the 73 outcomes read: the next run planned with 0.2 tests/s."""
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=100.0)))
    calls = _runner(monkeypatch, lambda n: 0.005 if n < shadow.CHUNK else 60)  # 40 tests in 0.2 s, then one that hangs
    rows, info, gaps = _shadow(root, edits, _ids(400), memo, 2)
    assert info['ran'] == shadow.CHUNK and len(calls) == 2, (info, gaps)
    # Charged with the stopped chunk's time, the 40 outcomes give 20 tests/s; the chunk that finished gave 200.
    kept = json.loads(memo.read_text())
    assert kept['rate'] == 150.0 and kept['rates'] == [100.0, 200.0], kept


def test_one_slow_run_or_a_run_of_few_tests_does_not_set_the_remembered_rate(tmp_path, monkeypatch):
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=100.0, rates=[100.0, 100.0, 100.0])))
    _runner(monkeypatch, lambda n: 0.02)
    _shadow(root, edits, _ids(shadow.CHUNK), memo, 60)
    kept = json.loads(memo.read_text())
    assert kept['rate'] == 100.0 and kept['rates'] == [100.0, 100.0, 100.0, 50.0], kept
    # sympy #30684: 8 candidates. Too few to measure anything.
    _shadow(root, edits, _ids(8), memo, 60)
    assert json.loads(memo.read_text())['rates'] == kept['rates']
