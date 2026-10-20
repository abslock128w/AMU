"""A runner that refuses a whole batch because of one label group (Django's runtests and gis_tests without a spatial
backend, retrospective 2026-10-18 defect 1): the group is dropped with a gap, and every other candidate still runs."""
import difflib
import sys

from amu.core import runners, shadow
from amu.core.patch import changes

GIS = 'Aborting: A GIS database backend is required to run gis_tests.'
TESTS = 'import unittest\n\nimport lib\n\n\nclass T(unittest.TestCase):\n' + ''.join(
    f'    def test_{n}(self):\n        self.assertEqual(lib.VALUE, 1)\n' for n in 'abcdef')


def django(tmp_path, refuse='gis_tests', message=GIS, groups=('basic', 'gis_tests'), extra=''):
    """A tree with a fake tests/runtests.py that, like Django's, refuses any batch holding a label of ``refuse`` before
    any test runs (``refuse`` None: every batch). Each start's labels go to starts.log."""
    log = tmp_path/'starts.log'
    (tmp_path/'tests').mkdir(parents=True, exist_ok=True)
    (tmp_path/'tests'/'runtests.py').write_text(
        'import sys\nimport unittest\n'
        'labels = [a for a in sys.argv[1:] if "." in a]\n'
        f'with open({str(log)!r}, "a") as log:\n    log.write(" ".join(labels) + "\\n")\n'
        'print("Testing against Django installed in \'/somewhere/django\'")\n'
        f'if {refuse!r} is None or {refuse!r} in {{label.split(".")[0] for label in labels}}:\n'
        f'    print({message!r})\n    sys.exit(1)\n'
        + extra +
        'result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(labels))\n'
        'sys.exit(not result.wasSuccessful())\n')
    for group in groups:
        (tmp_path/'tests'/group).mkdir(exist_ok=True)
        (tmp_path/'tests'/group/'__init__.py').write_text('')
        (tmp_path/'tests'/group/'tests.py').write_text(TESTS)
    (tmp_path/'lib.py').write_text('VALUE = 1\n')
    return log


def ident(group, name):
    return f'tests/{group}/tests.py::T::test_{name}'


def starts(log):
    return [line.split() for line in log.read_text().splitlines()] if log.exists() else []


def test_a_refused_group_is_dropped_and_every_other_label_runs(tmp_path):
    # Both halves of the batch hold a gis_tests label: splitting it read as "the runner aborts whatever the labels".
    log = django(tmp_path)
    runner = runners.get(tmp_path, 'django-runtests')
    ids = [ident('gis_tests', 'a'), ident('basic', 'a'), ident('gis_tests', 'b'), ident('basic', 'b')]
    found, problem = runners.execute(runner, sys.executable, tmp_path, ids, tmp_path/'r.xml', 60)
    assert found == {ident('gis_tests', 'a'): 'unrunnable', ident('basic', 'a'): 'passed',
                     ident('gis_tests', 'b'): 'unrunnable', ident('basic', 'b'): 'passed'}, (found, problem)
    assert problem is None
    assert runners.refusals(runner, tmp_path) == {'gis_tests': GIS}
    # The whole batch once, then the rest without the group: no split per label.
    assert starts(log) == [[runner.label(i) for i in ids], ['basic.tests.T.test_a', 'basic.tests.T.test_b']]


def test_a_refused_group_is_left_out_of_later_batches_up_front(tmp_path):
    log = django(tmp_path)
    runner = runners.get(tmp_path, 'django-runtests')
    runners.execute(runner, sys.executable, tmp_path, [ident('gis_tests', 'a'), ident('basic', 'a')], tmp_path/'r.xml', 60)
    log.unlink()
    found, problem = runners.execute(runner, sys.executable, tmp_path, [ident('gis_tests', 'c'), ident('basic', 'c')], tmp_path/'r.xml', 60)
    assert found == {ident('gis_tests', 'c'): 'unrunnable', ident('basic', 'c'): 'passed'} and problem is None
    assert starts(log) == [['basic.tests.T.test_c']]
    # Only refused labels: nothing starts, and nothing ran.
    found, problem = runners.execute(runner, sys.executable, tmp_path, [ident('gis_tests', 'd')], tmp_path/'r.xml', 60)
    assert found == {ident('gis_tests', 'd'): 'unrunnable'} and problem == 'no outcomes read: ' + GIS
    assert starts(log) == [['basic.tests.T.test_c']]


def test_an_abort_that_names_a_group_but_comes_back_without_it_is_not_a_refusal(tmp_path):
    message = "LookupError: No installed app with label 'basic'."
    log = django(tmp_path, refuse=None, message=message, groups=('basic', 'other'))
    runner = runners.get(tmp_path, 'django-runtests')
    ids = [ident('basic', 'a'), ident('other', 'a'), ident('other', 'b')]
    found, problem = runners.execute(runner, sys.executable, tmp_path, ids, tmp_path/'r.xml', 60)
    assert found == {i: 'unrunnable' for i in ids} and problem == 'no outcomes read: ' + message
    assert runners.refusals(runner, tmp_path) == {}
    assert len(starts(log)) <= 4


def test_pytest_labels_have_no_group():
    runner = runners.get('.', 'pytest')
    assert runners.refusal(runner, 'ERROR: not found: tests/gis_tests/test_a.py', ['tests/gis_tests/test_a.py::test_x']) is None


def _shadow(tmp_path, ids, monkeypatch, chunk=2, parts=1, budget=120):
    """shadow.run with the change VALUE 1 -> 2 in lib.py, ``parts`` processes, ``chunk`` candidates per chunk."""
    monkeypatch.setattr(shadow, 'CHUNK', chunk)
    monkeypatch.setattr(shadow, 'PARTS', parts)
    (tmp_path/'lib.py').write_text('VALUE = 2\n')
    diff = ''.join(difflib.unified_diff(['VALUE = 1\n'], ['VALUE = 2\n'], 'a/lib.py', 'b/lib.py'))
    edits, _ = changes(tmp_path, diff)
    return shadow.run(tmp_path, edits, ids, python=sys.executable, budget=budget, runner='django-runtests')


def test_a_chunk_of_refused_labels_does_not_end_the_run(tmp_path, monkeypatch):
    """Django #21881, #21890, #22005, #22041: the first chunk was all gis_tests and nothing else ran."""
    django(tmp_path)
    ids = [ident('gis_tests', 'a'), ident('gis_tests', 'b'), ident('basic', 'a'), ident('gis_tests', 'c'), ident('basic', 'b')]
    rows, info, gaps = _shadow(tmp_path, ids, monkeypatch)
    assert info['ran'] == 2 and 'aborted' not in info, (info, gaps)
    assert sorted(row['id'] for row in rows) == [ident('basic', 'a'), ident('basic', 'b')], (rows, gaps)
    assert all(row['before'] == 'passed' and row['after'] == 'failed' for row in rows), rows
    assert f'3 candidate(s) in gis_tests not run: django-runtests refuses them before any test runs: {GIS}' in gaps, gaps
    assert not any('budget' in gap or 'could not be run' in gap or 'shadow run (after' in gap for gap in gaps), gaps


def test_a_chunk_the_runner_aborts_on_does_not_end_the_run(tmp_path, monkeypatch):
    # A refusal that does not name the group: the generic split isolates the labels, and the next chunk still runs.
    log = django(tmp_path, refuse='spatial', message='Aborting: a database backend is required.', groups=('basic', 'spatial'))
    ids = [ident('basic', 'a'), ident('basic', 'b'), ident('spatial', 'a'), ident('spatial', 'b'), ident('basic', 'c')]
    rows, info, gaps = _shadow(tmp_path, ids, monkeypatch)
    assert sorted(row['id'] for row in rows) == [ident('basic', 'a'), ident('basic', 'b'), ident('basic', 'c')], (rows, gaps)
    assert info['ran'] == 3 and info['unrunnable'] == 2, info
    assert 'shadow run (after the change, django-runtests): no outcomes read: Aborting: a database backend is required.' in gaps, gaps
    assert '2 candidate(s) could not be run by django-runtests and are not covered by this result' in gaps, gaps
    assert ['basic.tests.T.test_c'] in starts(log)


def test_a_runner_that_runs_nothing_on_either_side_stops_after_two_chunks(tmp_path, monkeypatch):
    log = django(tmp_path, refuse=None, message='Aborting: settings missing.')
    ids = [ident('basic', n) for n in 'abcdef']
    rows, info, gaps = _shadow(tmp_path, ids, monkeypatch)
    assert rows == [] and info['ran'] == 0 and 'aborted' not in info, (info, gaps)
    assert any(gap.startswith('shadow run stopped after 4 of 6 candidates: django-runtests ran none of them') for gap in gaps), gaps
    assert not any('budget' in gap for gap in gaps), gaps
    assert not any('basic.tests.T.test_e' in labels for labels in starts(log))


def test_a_slice_that_times_out_ends_the_run_even_when_another_slice_is_refused(tmp_path, monkeypatch):
    """Review of this fix: a chunk with one slice refused and one cut at the budget counted the cut slice as run."""
    django(tmp_path, extra='import time\nif "basic" in {label.split(".")[0] for label in labels}:\n    time.sleep(30)\n')
    ids = [ident(group, n) for n in 'abcd' for group in ('gis_tests', 'basic')]
    _, info, gaps = _shadow(tmp_path, ids, monkeypatch, chunk=2, parts=2, budget=6)
    assert info['ran'] == 0, (info, gaps)
    assert not [gap for gap in gaps if 'budget' in gap and ' 0 of' not in gap], gaps
