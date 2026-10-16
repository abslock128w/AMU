"""Phase A of brief 4: the change's own tests replayed on the code before it, flip trust, selection, base cache."""
import difflib
import sys

from amu.core import analyze, replay


def repository(tmp_path, files):
    for name, source in files.items():
        target = tmp_path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return tmp_path


def patch(path, before, after):
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        '/dev/null' if not before else 'a/'+path, 'b/'+path))


def diff(before, after):
    return ''.join(patch(path, before.get(path, ''), after.get(path, '')) for path in after if before.get(path) != after[path])


def run_change(tmp_path, before, after, **options):
    repository(tmp_path, after)
    return analyze(tmp_path, diff(before, after), full_evidence=True, index_dir=tmp_path/'.index', run=True,
                   python=sys.executable, **options)


LIB0 = 'def work():\n    return 1\n\n\ndef other():\n    return 5\n'
LIB1 = 'def work():\n    return 2\n\n\ndef other():\n    return 5\n'


def test_touched_lists_added_and_edited_tests_only():
    class Root:
        pass
    before = 'def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 1\n\n\nclass TestC:\n    def test_d(self):\n        pass\n'
    after = before.replace('def test_b():\n    assert 1', 'def test_b():\n    assert 2') + '\n\ndef test_new():\n    pass\n'
    found = replay.definitions(after)
    old = replay.definitions(before)
    assert sorted(name for name, dump in found.items() if old.get(name) != dump) == ['test_b', 'test_new']
    assert replay.definitions('class TestC:\n    def test_d(self):\n        pass\ndef test_x():\n    pass\n', module_level=False) .keys() == {'TestC::test_d'}
    assert replay.is_test_file('tests/test_x.py') and replay.is_test_file('pkg/tests.py')
    assert not replay.is_test_file('django/test/client.py') and not replay.is_test_file('lib.py')


def test_a_new_test_that_fails_before_the_change_and_passes_after_is_change_evidence(tmp_path):
    tests0 = 'from lib import work, other\ndef test_other():\n    assert other() == 5\n'
    tests1 = tests0 + 'def test_new_value():\n    assert work() == 2\ndef test_new_steady():\n    assert other() == 5\n'
    result = run_change(tmp_path, {'lib.py': LIB0, 'tests/test_lib.py': tests0}, {'lib.py': LIB1, 'tests/test_lib.py': tests1})
    assert [row['id'] for row in result['change_evidence']] == ['tests/test_lib.py::test_new_value'], result
    assert result['change_evidence'][0]['label'] == replay.EVIDENCE
    verdicts = {row['id']: row['verdict'] for row in result['replayed']}
    assert verdicts == {'tests/test_lib.py::test_new_value': 'evidence', 'tests/test_lib.py::test_new_steady': 'none'}
    assert result['flips'] == 0 and "change evidence" in result['summary']


def test_a_new_test_file_importing_what_the_change_adds_counts_as_failing_before(tmp_path):
    lib1 = LIB1 + '\n\ndef added():\n    return 7\n'
    tests1 = 'from lib import added\ndef test_added():\n    assert added() == 7\n'
    result = run_change(tmp_path, {'lib.py': LIB0}, {'lib.py': lib1, 'tests/test_added.py': tests1})
    assert [(row['id'], row['before']) for row in result['change_evidence']] == [('tests/test_added.py::test_added', 'unloadable')], result


def test_a_new_test_that_fails_on_both_sides_is_a_gap_not_evidence(tmp_path):
    tests1 = 'from lib import work\ndef test_wrong():\n    assert work() == 3\n'
    result = run_change(tmp_path, {'lib.py': LIB0}, {'lib.py': LIB1, 'tests/test_lib.py': tests1})
    assert result.get('change_evidence') == [] and any('fail before and after' in gap for gap in result['gaps']), result


def test_a_test_whose_parametrize_table_changed_counts_as_edited():
    before = ('import pytest\nCASES = {"a": 1}\nROWS = list(CASES.items())\n@pytest.mark.parametrize("k,v", ROWS)\n'
              'def test_table(k, v):\n    assert v\ndef test_plain():\n    assert 1\n')
    after = before.replace('{"a": 1}', '{"a": 1, "b": 2}')
    old, new = replay.definitions(before), replay.definitions(after)
    assert [name for name in new if old.get(name) != new[name]] == ['test_table']


def test_a_flip_is_a_failure_only_after_the_reach_check_and_three_reruns(tmp_path):
    tests = ('import pathlib\nfrom lib import work\n'
             'def test_work():\n    assert work() == 1\n'
             'def test_reads_the_source():\n    assert "return 1" in (pathlib.Path(__file__).parent.parent/"lib.py").read_text()\n')
    result = run_change(tmp_path, {'lib.py': LIB0, 'tests/test_lib.py': tests}, {'lib.py': LIB1, 'tests/test_lib.py': tests},
                        also=['tests/test_lib.py::test_reads_the_source'])
    labels = {row['id'].split('::')[-1]: row for row in result['flip_trust']}
    assert labels['test_work']['label'] == 'fails' and labels['test_work']['reach'] >= 1, labels
    assert set(labels['test_work']['reruns'].values()) == {'failed'}
    assert labels['test_reads_the_source']['label'] == 'not caused by this change', labels
    assert 'none of the changed lines' in labels['test_reads_the_source']['why']
    assert result['predicted_failures'] == [dict(result['predicted_failures'][0], id='tests/test_lib.py::test_work')]
    assert result['flips'] == 1 and 'not caused by this change, not counted' in result['summary'], result['summary']
    assert result['order'].startswith(('pytest-randomly', 'the same explicit order'))


def test_the_in_process_rerun_works_for_unittest_runners(tmp_path):
    tests = 'import unittest\nfrom lib import work\nclass TestWork(unittest.TestCase):\n    def test_work(self):\n        self.assertEqual(work(), 1)\n'
    result = run_change(tmp_path, {'lib.py': LIB0, 'tests/test_lib.py': tests}, {'lib.py': LIB1, 'tests/test_lib.py': tests},
                        runner='unittest')
    [row] = result['flip_trust']
    assert row['label'] == 'fails' and row['reruns'] == dict(in_process='failed', fresh_process='failed', after_set='failed'), row
