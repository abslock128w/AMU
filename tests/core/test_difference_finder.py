"""Phase A of brief 4: the change's own tests replayed on the code before it, flip trust, selection, base cache."""
import difflib
import sys
from pathlib import Path

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


def test_selection_is_the_import_closure_with_hubs_passed_only_on_evidence():
    from amu.core import select
    facts = {'pkg/core.py': dict(deps=[]), 'pkg/__init__.py': dict(deps=['pkg.core']), 'pkg/util.py': dict(deps=['pkg.core.thing']),
             **{f'tests/test_{n}.py': dict(deps=['pkg']) for n in range(150)}, 'tests/test_util.py': dict(deps=['pkg.util'])}
    modules = {'pkg': 'pkg/__init__.py', 'pkg.core': 'pkg/core.py', 'pkg.util': 'pkg/util.py'}
    table = select.importers(facts, modules)
    hubs = select.hubs(table, facts)
    assert hubs == {'pkg/__init__.py'}
    files, reached = select.closure(table, ['pkg/core.py'], hubs)
    assert set(files) == {'pkg/core.py', 'pkg/__init__.py', 'pkg/util.py', 'tests/test_util.py'} and reached == {'pkg/__init__.py'}
    deep = {f'm{n}.py': dict(deps=[f'm{n - 1}']) for n in range(1, 15)}
    chain = select.importers({'m0.py': dict(deps=[]), **deep}, {f'm{n}': f'm{n}.py' for n in range(15)})
    assert max(select.closure(chain, ['m0.py'], set())[0].values()) == select.DEPTH


def test_run_order_is_touched_then_recent_flip_then_duration_then_ranking():
    from amu.core import select
    ids = ['a', 'b', 'c', 'd', 'e']
    got = select.order(ids, touched=['e'], flips={'d': 100, 'c': 50}, durations={'a': 5.0, 'b': 0.1}, tiebreak={'a': 0, 'b': 1})
    assert got == ['e', 'd', 'c', 'b', 'a']
    assert select.order(['x', 'y', 'z'], tiebreak={'z': 0, 'y': 1, 'x': 2}) == ['z', 'y', 'x']


def test_a_second_run_against_the_same_base_takes_its_passes_from_the_cache(tmp_path):
    # Only passes are cached: a test the change adds that fails on the base would run there again, by design.
    tests = 'from lib import work\ndef test_work():\n    assert work() == 1\n'
    change = ({'lib.py': LIB0, 'tests/test_lib.py': tests}, {'lib.py': LIB1, 'tests/test_lib.py': tests})
    first = run_change(tmp_path, *change)
    assert first['executed']['base_cache']['ran'] >= 1 and first['executed']['base_cache']['stored'] >= 1, first['executed']
    second = analyze(tmp_path, diff(*change), full_evidence=True, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert second['executed']['base_cache']['ran'] == 0 and second['executed']['base_cache']['answered'] >= 1, second['executed']
    assert second['flips'] == first['flips'] == 1
    from amu.ci.render import render
    body = render(second, dict(head='a' * 40, merge_base='b' * 40))
    assert 'The merge base side was not run' in body, body


def test_a_cached_pass_is_keyed_on_what_the_test_imports(tmp_path):
    from amu.core.basecache import BaseCache
    repository(tmp_path, {'lib.py': 'x = 1\n', 'helper.py': 'import lib\n', 'tests/test_a.py': 'import helper\n'})
    cache = BaseCache(tmp_path/'c', {'tests/test_a.py': ['helper.py'], 'helper.py': ['lib.py']}, tmp_path, sys.executable, ['pytest'])
    cache.store(tmp_path, {'tests/test_a.py::test_x': 'passed', 'tests/test_a.py::test_y': 'failed'})
    assert cache.lookup(tmp_path, ['tests/test_a.py::test_x', 'tests/test_a.py::test_y']) == {'tests/test_a.py::test_x': 'passed'}
    (tmp_path/'lib.py').write_text('x = 2\n')
    assert cache.lookup(tmp_path, ['tests/test_a.py::test_x']) == {}


def test_plugin_autoload_is_off_and_reordering_or_rerunning_plugins_are_not_loaded(monkeypatch):
    from amu.core import runners
    monkeypatch.setitem(runners._PLUGINS, 'py', [('django', 'pytest_django.plugin'), ('xdist.looponfail', 'xdist.looponfail'),
                                                 ('randomly', 'pytest_randomly'), ('pytest_cov', 'pytest_cov.plugin'),
                                                 ('rerunfailures', 'pytest_rerunfailures')])
    runner = runners.Runner()
    assert runners.plugin_args(runner, 'py') == ['-p', 'pytest_django.plugin', '-p', 'pytest_randomly'] and runner.autoload_off
    assert runners.environment(Path('.'), autoload=False)['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert runners.plugin_args(runners.Unittest(), 'py') == []


def test_the_bounded_diverse_pick_equals_the_full_scan():
    import random

    from amu.core import rank

    def full(scores, limit):
        remaining = {s: 1.0 for per in scores.values() for s in per}
        strongest = {}
        for per in scores.values():
            for s, v in per.items():
                strongest[s] = max(strongest.get(s, 0.0), v)
        left, order = dict(scores), []

        def gain(t):
            return sum((rank.FLOOR + (1 - rank.FLOOR) * remaining[s]) * v for s, v in left[t].items())
        while left and len(order) < limit:
            best = min(left, key=lambda t: (-gain(t), t))
            order.append((best, gain(best)))
            for s, v in left.pop(best).items():
                if strongest[s] > 0:
                    remaining[s] *= 1 - 0.5 * v / strongest[s]
        return order
    generator = random.Random(4)
    for _ in range(30):
        scores = {f't{n}': {f's{k}': round(generator.choice([0.0, 0.1, 0.3, 0.3, 1.0, 2.5]), 2) for k in generator.sample(range(6), 3)}
                  for n in range(generator.randint(1, 80))}
        assert rank.diverse(scores, 40) == full(scores, 40)


def test_the_third_rerun_replays_only_what_ran_before_the_flip_in_its_batch():
    from amu.core import trust
    batch_a, batch_b = ['a1', 'a2', 'flip1', 'a3'], ['b1', 'flip2', 'b2']
    context = {i: batch_a for i in batch_a} | {i: batch_b for i in batch_b}
    assert trust._preceding(['flip1', 'flip2'], [*batch_a, *batch_b], context) == ['a1', 'a2', 'b1']
    # Without batch records, every other candidate goes first, as before.
    assert trust._preceding(['flip1'], ['a1', 'flip1', 'a3'], None) == ['a1', 'a3']


def test_a_runner_limit_covers_the_splits_made_to_isolate_an_unknown_label(tmp_path):
    # Found on PR #20: side-by-side, capped at 120 s, took 505 s. A label pytest rejected sent the batch into splits,
    # and each split was given the full limit again.
    import time

    from amu.core import runners
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests'/'test_slow.py').write_text('import time\n\n\ndef test_a():\n    time.sleep(4)\n\n\ndef test_b():\n    time.sleep(4)\n')
    ids = ['tests/test_slow.py::test_a', 'tests/test_slow.py::test_gone', 'tests/test_slow.py::test_b']
    began = time.monotonic()
    found, problem = runners.execute(runners.get(tmp_path, 'pytest'), sys.executable, tmp_path, ids, tmp_path/'r.xml', 6)
    assert time.monotonic() - began < 8, (found, problem, time.monotonic() - began)
    assert found.get('tests/test_slow.py::test_a') == 'passed' and problem == 'timed out', (found, problem)
