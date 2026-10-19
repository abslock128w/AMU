"""The test rate the shadow memo keeps between runs: it plans how many candidates run first, and a run with budget
left goes on past that plan. Retrospective 2026-10-18, sympy: the remembered rate went 15 -> 2 -> 3 -> 0.2 tests/s and
#30540 ran 48 of 12,885 candidates in 47 s of its 300 s."""
import json
import subprocess
import sys
import time

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


def _shadow(root, edits, ids, memo, budget):
    return shadow.run(root, edits, ids, python=sys.executable, budget=budget, runner='unittest', memo=memo)


def test_a_tiny_remembered_rate_does_not_stop_a_run_with_budget_left(tmp_path, monkeypatch):
    """#30540: a rate of 0.2 tests/s from another merge base planned 48 candidates; they ran in 47 s of 300."""
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=0.2)))
    monkeypatch.setattr(runners, 'execute', lambda runner, python, tree, ids, report, timeout: ({i: 'passed' for i in ids}, None))
    rows, info, gaps = _shadow(root, edits, _ids(400), memo, 60)
    assert info['ran'] == 400 and info['candidates'] == 400, (info, gaps)
    assert not any('lower-ranked' in gap for gap in gaps), gaps


def test_candidates_past_the_plan_stop_where_the_budget_keeps_its_share_for_the_checks_after(tmp_path, monkeypatch):
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=0.2)))

    def hundred_a_second(runner, python, tree, ids, report, timeout):
        time.sleep(len(ids) / 100)
        return {i: 'passed' for i in ids}, None
    monkeypatch.setattr(runners, 'execute', hundred_a_second)
    rows, info, gaps = _shadow(root, edits, _ids(1000), memo, 3)
    # 40 planned at 0.2 tests/s; about 240 fit in the 2.4 s that are the candidates' share of 3 s at 100 tests/s.
    assert shadow.CHUNK < info['ran'] < 1000, (info, gaps)
    assert any(gap.startswith(f"{1000 - info['candidates']} lower-ranked candidate(s) not run") for gap in gaps), gaps


def test_a_run_stopped_at_its_budget_does_not_drag_the_remembered_rate_down(tmp_path, monkeypatch):
    """sympy #30477: 80 of 696 candidates ran before a chunk was stopped at the budget, and the stopped chunk's time was
    charged to the 73 outcomes read: the next run planned with 0.2 tests/s."""
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=100.0)))
    calls = []

    def first_chunk_then_stopped(runner, python, tree, ids, report, timeout):
        calls.append(len(ids))
        if len(calls) == 1:
            time.sleep(0.2)  # 40 tests in 0.2 s: 200 tests/s
            return {i: 'passed' for i in ids}, None
        time.sleep(timeout)
        return {}, 'timed out'
    monkeypatch.setattr(runners, 'execute', first_chunk_then_stopped)
    rows, info, gaps = _shadow(root, edits, _ids(400), memo, 2)
    assert info['ran'] == shadow.CHUNK and len(calls) == 2, (info, gaps)
    # Charged with the stopped chunk's time, the 40 outcomes give 20 tests/s; the chunk that finished gave 200.
    kept = json.loads(memo.read_text())
    assert kept['rate'] > 50 and kept['rates'][-1] > 50, kept


def test_one_slow_run_or_a_run_of_few_tests_does_not_set_the_remembered_rate(tmp_path, monkeypatch):
    root, edits, memo = _tree(tmp_path)
    memo.write_text(json.dumps(dict(key='another merge base', before={}, rate=100.0, rates=[100.0, 100.0, 100.0])))

    def fifty_a_second(runner, python, tree, ids, report, timeout):
        time.sleep(len(ids) / 50)
        return {i: 'passed' for i in ids}, None
    monkeypatch.setattr(runners, 'execute', fifty_a_second)
    _shadow(root, edits, _ids(shadow.CHUNK), memo, 60)
    kept = json.loads(memo.read_text())
    assert kept['rate'] == 100.0 and len(kept['rates']) == 4 and kept['rates'][-1] <= 50, kept
    # sympy #30684: 8 candidates. Too few to measure anything.
    _shadow(root, edits, _ids(8), memo, 60)
    assert json.loads(memo.read_text())['rates'] == kept['rates']
