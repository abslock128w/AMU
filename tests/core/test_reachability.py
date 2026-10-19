import difflib
import json
import subprocess
import time

from amu.core import LIMIT, analyze, encoded
from amu.core.index import build


def repository(tmp_path, files):
    for name, source in files.items():
        target = tmp_path/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return tmp_path


def patch(path, before, after):
    return ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/'+path, 'b/'+path))


def run(tmp_path, path, before, after, **options):
    return analyze(tmp_path, patch(path, before, after), full_evidence=True, index_dir=tmp_path/'.index', **options)


def ids(result):
    return [row['id'] for row in result['tests']]


def test_relative_reexport_helper_and_fixture(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'pkg/api.py': source, 'pkg/__init__.py': 'from .api import work\nprint("never execute")\n',
       'tests/conftest.py': 'import pytest\nfrom pkg import work\n@pytest.fixture\ndef value():\n    return work()\n',
       'tests/test_api.py': 'def helper():\n    from pkg import work\n    return work()\ndef test_helper():\n    assert helper()\ndef test_fixture(value):\n    assert value\n'})
    result = run(tmp_path, 'pkg/api.py', source, source.replace('1', '2'))
    assert set(ids(result)) == {'tests/test_api.py::test_helper', 'tests/test_api.py::test_fixture'}
    assert all(r['confidence'] == 'transitive' for r in result['tests'])


def test_factory_instance_method_and_dotted_import(tmp_path):
    source = 'class Thing:\n    def work(self):\n        return 1\ndef factory():\n    return Thing()\n'
    repository(tmp_path, {'pkg/api.py': source, 'pkg/__init__.py': '',
                          'tests/test_api.py': 'import pkg.api\ndef test_work():\n    thing = pkg.api.factory()\n    assert thing.work()\n'})
    result = run(tmp_path, 'pkg/api.py', source, source.replace('1', '2'))
    assert result['tests'][0]['id'] == 'tests/test_api.py::test_work'
    assert result['tests'][0]['confidence'] == 'direct'


def test_incremental_index_reparses_only_changed_source(tmp_path):
    repository(tmp_path, {'a.py': 'x = 1\n', 'b.py': 'x = 2\n'})
    index = tmp_path/'.index'
    assert build(tmp_path, index)[3] == 2
    assert build(tmp_path, index)[3] == 0
    (tmp_path/'a.py').write_text('x = 3\n')
    assert build(tmp_path, index)[3] == 1


def test_graph_snapshot_of_an_unchanged_tree_gives_the_same_report(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'a.py': source, 'tests/test_a.py': 'from a import work\ndef test_work():\n    assert work()\n'})
    first = run(tmp_path, 'a.py', source, source.replace('1', '2'))
    assert list((tmp_path/'.index').rglob('state2-*.json'))
    second = run(tmp_path, 'a.py', source, source.replace('1', '2'))
    assert first['tests'] == second['tests'] and first['report_hash'] == second['report_hash']


def test_non_python_patch_keeps_python_results(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'a.py': source, 'tests/test_a.py': 'from a import work\ndef test_work():\n    assert work()\n'})
    candidate = patch('a.py', source, source.replace('1', '2'))+patch('notes.rst', 'old\n', 'new\n')
    result = analyze(tmp_path, candidate, include_tests=True)
    assert result['tests']
    assert 'Skipped 1 non-Python file(s)' in result['gaps']


def test_package_initializer_with_executable_code_does_not_hide_submodule(tmp_path):
    source = 'def quote(x):\n    return x\n'
    repository(tmp_path, {'src/pkg/__init__.py': 'import os\nDEBUG = os.environ.get("D")\nif DEBUG:\n    print(1)\n',
                          'src/pkg/urls.py': source,
                          'tests/test_urls.py': 'from pkg import urls\ndef test_quote():\n    assert urls.quote(1)\n'})
    result = run(tmp_path, 'src/pkg/urls.py', source, source.replace('return x', 'return str(x)'))
    assert ids(result) == ['tests/test_urls.py::test_quote']


def test_property_getter_and_setter_are_one_symbol_and_decorator_lines_belong(tmp_path):
    source = ('class Box:\n    @property\n    def size(self):\n        return self._size\n'
              '    @size.setter\n    def size(self, value):\n        self._size = value\n')
    repository(tmp_path, {'box.py': source,
                          'tests/test_box.py': 'from box import Box\ndef test_size():\n    b = Box()\n    b.size = 2\n    assert b.size == 2\n'
                                               'def test_other():\n    assert Box\n'})
    getter = run(tmp_path, 'box.py', source, source.replace('return self._size', 'return self._size or 0'))
    decorator = run(tmp_path, 'box.py', source, source.replace('    @property\n', '    @property  # note\n'))
    assert getter['changed_symbols'] == decorator['changed_symbols'] == ['box.py#Box.size']
    assert ids(getter)[0] == 'tests/test_box.py::test_size'


def test_registry_variable_links_consumer_to_registered_class(tmp_path):
    source = 'class IntConverter:\n    def convert(self, v):\n        return int(v)\n'
    repository(tmp_path, {'conv.py': source + 'DEFAULTS = {"int": IntConverter}\n',
                          'router.py': 'from conv import DEFAULTS\ndef route(kind, v):\n    return DEFAULTS[kind]().convert(v)\n',
                          'tests/test_router.py': 'from router import route\ndef test_route():\n    assert route("int", "1") == 1\n'})
    result = run(tmp_path, 'conv.py', source, source.replace('int(v)', 'int(v, 10)'))
    assert ids(result) == ['tests/test_router.py::test_route']


def test_call_bound_to_base_reaches_changed_override(tmp_path):
    source = 'from base import Base\nclass Child(Base):\n    def hook(self):\n        return 1\n'
    repository(tmp_path, {'base.py': 'class Base:\n    def run(self):\n        return self.hook()\n    def hook(self):\n        return 0\n',
                          'child.py': source,
                          'tests/test_run.py': 'from child import Child\ndef test_run():\n    assert Child().run()\n'})
    result = run(tmp_path, 'child.py', source, source.replace('1', '2'))
    assert ids(result) == ['tests/test_run.py::test_run']
    assert result['tests'][0]['confidence'] == 'heuristic'


def test_instance_state_assigned_in_setup_method(tmp_path):
    source = 'class Engine:\n    def start(self):\n        return 1\n'
    repository(tmp_path, {'engine.py': source,
                          'tests/test_engine.py': 'from engine import Engine\nclass TestEngine:\n    def setup_method(self):\n        self.engine = Engine()\n'
                                                  '    def test_start(self):\n        assert self.engine.start()\n'})
    result = run(tmp_path, 'engine.py', source, source.replace('1', '2'))
    assert ids(result) == ['tests/test_engine.py::TestEngine::test_start']


def test_every_changed_symbol_is_represented_before_one_symbol_repeats(tmp_path):
    source = 'def alpha():\n    return 1\ndef beta():\n    return 1\n'
    tests = 'from lib import alpha, beta\n' + ''.join(f'def test_a{i}():\n    assert alpha()\n' for i in range(6)) + 'def test_z():\n    assert beta()\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = run(tmp_path, 'lib.py', source, source.replace('1', '2'))
    assert 'tests/test_lib.py::test_z' in ids(result)[:2]


def test_name_affinity_breaks_ties_between_equally_distant_tests(tmp_path):
    source = 'def get_host():\n    return 1\n'
    repository(tmp_path, {'lib.py': source,
                          'tests/test_lib.py': 'from lib import get_host\ndef test_aaa():\n    assert get_host()\ndef test_get_host():\n    assert get_host()\n'})
    assert ids(run(tmp_path, 'lib.py', source, source.replace('1', '2')))[0] == 'tests/test_lib.py::test_get_host'


def test_same_answer_whether_or_not_the_patch_is_already_applied(tmp_path):
    source = 'def one():\n    return 1\n\n\ndef two():\n    return 2\n'
    changed = source.replace('return 2', 'value = 2\n    return value')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import two\ndef test_two():\n    assert two()\n'})
    before = run(tmp_path, 'lib.py', source, changed)
    (tmp_path/'lib.py').write_text(changed)
    after = run(tmp_path, 'lib.py', source, changed)
    assert before['changed_symbols'] == after['changed_symbols'] == ['lib.py#two']
    assert ids(before) == ids(after)


def test_uncommitted_change_is_the_default_patch(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import work\ndef test_work():\n    assert work()\n'})
    git = ['git', '-C', str(tmp_path), '-c', 'user.email=a@b.c', '-c', 'user.name=a']
    for args in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'base']):
        subprocess.run([*git, *args], check=True)
    (tmp_path/'lib.py').write_text(source.replace('1', '2'))
    result = analyze(tmp_path, index_dir=tmp_path/'.index', include_tests=True)
    assert result['changed_symbols'] == ['lib.py#work'] and ids(result) == ['tests/test_lib.py::test_work']


def test_missing_repository_is_a_gap(tmp_path):
    repository(tmp_path, {'lib.py': 'x = 1\n'})
    result = analyze(tmp_path, index_dir=tmp_path/'.index')
    assert result['tests'] == [] and any('git diff unavailable' in gap for gap in result['gaps'])


def test_unknowns_are_gaps_not_errors(tmp_path):
    repository(tmp_path, {'broken.py': 'def (:\n', 'lib.py': 'def work():\n    return 1\n'})
    result = analyze(tmp_path, 'not a diff at all', index_dir=tmp_path/'.index', coverage_evidence='missing.json')
    assert result['tests'] == [] and any('broken.py' in gap for gap in result['gaps'])
    assert any('missing.json' in gap for gap in result['gaps'])


def test_exception_chain_is_reported_only_through_located_handlers(tmp_path):
    source = 'class Invalid(Exception):\n    pass\ndef convert(v):\n    return int(v)\n'
    changed = source.replace('    return int(v)\n', '    try:\n        return int(v)\n    except ValueError:\n        raise Invalid()\n')
    repository(tmp_path, {'conv.py': source,
                          'match.py': 'from conv import convert, Invalid\nclass NoMatch(Exception):\n    pass\n'
                                      'def match(v):\n    try:\n        return convert(v)\n    except Invalid:\n        raise NoMatch()\n',
                          'app.py': 'from match import match, NoMatch\nclass NotFound(Exception):\n    pass\n'
                                    'def dispatch(v):\n    try:\n        return match(v)\n    except NoMatch:\n        raise NotFound()\n'
                                    'def unguarded(v):\n    return v\n'})
    row = run(tmp_path, 'conv.py', source, changed)['consequences'][0]
    assert row['from'] == 'convert raises Invalid' and row['to'] == 'dispatch raises NotFound'
    assert [step.split(' ')[0] for step in row['path']] == ['conv.py:7', 'match.py:7', 'app.py:7']


def test_exception_walk_on_a_dense_call_graph_is_bounded(tmp_path):
    # Four layers of 60 functions, each calling every function below: 13 million caller paths, 241 nodes.
    # A walk without a memo spent 11 s here (and the whole hook budget on sympy's ArrayAdd).
    source = 'class Invalid(Exception):\n    pass\ndef convert(v):\n    return int(v)\n'
    files, below, module = {'conv.py': source}, ['convert'], 'conv'
    for layer in range(4):
        names = [f'f{layer}_{i}' for i in range(60)]
        files[f'layer{layer}.py'] = f'from {module} import {", ".join(below)}\n' + ''.join(
            f'def {name}(v):\n' + ''.join(f'    {callee}(v)\n' for callee in below) + '    return v\n' for name in names)
        below, module = names, f'layer{layer}'
    files['top.py'] = ('from conv import Invalid\nfrom layer2 import f2_0\nclass Stop(Exception):\n    pass\n'
                       'def top(v):\n    try:\n        return f2_0(v)\n    except Invalid:\n        raise Stop()\n')
    repository(tmp_path, files)
    run(tmp_path, 'conv.py', source, source)  # index outside the timed call
    started = time.perf_counter()
    result = run(tmp_path, 'conv.py', source, source.replace('    return int(v)\n', '    raise Invalid(v)\n'))
    assert time.perf_counter() - started < 3
    assert [(row['from'], row['to']) for row in result['consequences']] == [('convert raises Invalid', 'top raises Stop')]


def test_no_consequence_without_a_located_consumer(tmp_path):
    source = 'def convert(v):\n    return int(v)\n'
    repository(tmp_path, {'conv.py': source})
    result = run(tmp_path, 'conv.py', source, source.replace('    return int(v)\n', '    raise ValueError(v)\n'))
    assert result['consequences'] == []


def test_state_write_and_used_result_consequences(tmp_path):
    source = ('class Cache:\n    def fill(self):\n        self.items = []\n    def size(self):\n        return len(self.items)\n'
              'def load():\n    return 1\n')
    repository(tmp_path, {'cache.py': source, 'use.py': 'from cache import load\ndef consume():\n    value = load()\n    return value\ndef ignore():\n    load()\n'})
    state = run(tmp_path, 'cache.py', source, source.replace('self.items = []', 'self.items = None'))['consequences']
    assert state[0]['from'] == 'Cache.fill writes self.items' and 'Cache.size reads self.items' in state[0]['path'][1]
    used = run(tmp_path, 'cache.py', source, source.replace('return 1', 'return None'))['consequences']
    assert used[0]['from'] == 'load return changed' and len(used[0]['path']) == 2 and 'use.py:3 consume' in used[0]['path'][1]


def coverage_report(tmp_path, contexts):
    (tmp_path/'coverage.json').write_text(json.dumps({'meta': {'show_contexts': True}, 'files': {
        path: {'contexts': lines} for path, lines in contexts.items()}}))


def test_recorded_execution_outranks_static_paths_and_adds_statically_invisible_tests(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'lib.py': source,
                          'tests/test_lib.py': 'import importlib\nfrom lib import work\ndef test_static():\n    assert work()\n'
                                               'def test_dynamic():\n    assert getattr(importlib.import_module("lib"), "work")()\n'})
    assert ids(run(tmp_path, 'lib.py', source, source.replace('1', '2'))) == ['tests/test_lib.py::test_static']
    coverage_report(tmp_path, {'lib.py': {'2': ['tests/test_lib.py::test_dynamic|run']}})
    result = run(tmp_path, 'lib.py', source, source.replace('1', '2'))
    assert ids(result) == ['tests/test_lib.py::test_dynamic', 'tests/test_lib.py::test_static']
    assert result['tests'][0]['confidence'] == 'observed' and result['tests'][0]['expect'] == 'ran_changed_lines'


def test_coverage_staleness_is_per_file(tmp_path):
    source = 'def work():\n    return 1\n'
    files = {'lib.py': source, 'other.py': 'x = 1\n',
             'tests/test_lib.py': 'def test_dynamic():\n    assert __import__("lib").work()\n'}
    repository(tmp_path, files)
    hashes = build(tmp_path, tmp_path/'.index')[1]
    (tmp_path/'.amu').mkdir()
    (tmp_path/'.amu/observed-tests.json').write_text(json.dumps({
        'format': 'amu-observed-tests', 'snapshot': {'source_hashes': hashes},
        'context_table': ['tests/test_lib.py::test_dynamic|run'], 'observations': {'lib.py': {'2': [0]}, 'other.py': {'1': [0]}}}))
    (tmp_path/'other.py').write_text('x = 2\n')
    result = run(tmp_path, 'lib.py', source, source.replace('1', '2'))
    assert result['coverage']['edited_since'] == 1 and result['tests'][0]['confidence'] == 'observed'
    assert 'recorded before an edit' not in result['tests'][0]['why']
    (tmp_path/'lib.py').write_text(source + '# edited\n')
    stale = run(tmp_path, 'lib.py', source + '# edited\n', source.replace('1', '2') + '# edited\n')
    assert stale['tests'][0]['confidence'] == 'observed' and 'recorded before an edit' in stale['tests'][0]['why']


def test_default_packet_stays_under_two_kilobytes_and_keeps_ten_tests(tmp_path):
    names = [f'op_{i}' for i in range(12)]
    source = ''.join(f'def {n}():\n    return 1\n' for n in names)
    tests = 'from package_with_long_name import library_module as lib\n' + ''.join(
        f'def test_{n}_behaves_in_case_{i}():\n    assert lib.{n}()\n' for n in names for i in range(4))
    repository(tmp_path, {'package_with_long_name/library_module.py': source, 'package_with_long_name/__init__.py': '',
                          'tests/integration/test_library_module_operations.py': tests})
    result = analyze(tmp_path, patch('package_with_long_name/library_module.py', source, source.replace('return 1', 'return 2')), index_dir=tmp_path/'.index', include_tests=True)
    assert len(encoded(result)) < LIMIT and len(json.dumps(result, separators=(',', ':')).encode()) < LIMIT
    assert len(result['tests']) == 10 and set(result) >= {'summary', 'changed_symbols', 'tests', 'consequences', 'gaps', 'caveats', 'report_hash'}
    assert list(result)[0] == 'summary'


def test_nested_definition_costs_no_hop_and_deep_paths_are_still_found(tmp_path):
    source = 'def leaf():\n    return 1\n'
    chain = 'from lib import leaf\ndef d():\n    return leaf()\ndef c():\n    return d()\ndef b():\n    return c()\ndef a():\n    return b()\n'
    repository(tmp_path, {'lib.py': source, 'chain.py': chain,
                          'tests/test_chain.py': 'from chain import a\ndef test_deep():\n    def inner():\n        return a()\n    assert inner()\n'})
    result = run(tmp_path, 'lib.py', source, source.replace('1', '2'))
    assert ids(result) == ['tests/test_chain.py::test_deep'] and result['tests'][0]['confidence'] == 'transitive'


def test_patch_vocabulary_separates_tests_that_reach_the_same_hub(tmp_path):
    source = 'def parse(value):\n    return value\n'
    tests = ('from lib import parse\n'
             'def test_alpha():\n    assert parse("plain")\n'
             'def test_beta():\n    assert parse("--Foo-Bar") == "normalized lower dashes"\n'
             'def test_gamma():\n    assert parse("other")\n')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = run(tmp_path, 'lib.py', source, source.replace('return value', 'return value.lower()  # normalized dashes'))
    assert ids(result)[0] == 'tests/test_lib.py::test_beta'


def test_tail_of_the_ranking_stays_ordered_by_relevance(tmp_path):
    source = 'def work():\n    return 1\n'
    tests = 'from lib import work\ndef helper():\n    return work()\n' + ''.join(
        f'def test_far{i}():\n    assert helper()\n' for i in range(8)) + 'def test_zz_near():\n    assert work()\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    assert ids(run(tmp_path, 'lib.py', source, source.replace('1', '2')))[0] == 'tests/test_lib.py::test_zz_near'


def test_running_the_changed_lines_outranks_entering_the_function(tmp_path):
    source = 'def parse(v):\n    if v:\n        return 1\n    return 2\n'
    tests = 'from lib import parse\n' + ''.join(f'def test_{n}():\n    assert parse({v})\n' for n, v in (('aaa', 1), ('bbb', 1), ('zzz', 0)))
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    run_line = {str(n): [f'tests/test_lib.py::test_{t}|run' for t in who]
                for n, who in ((2, ('aaa', 'bbb', 'zzz')), (3, ('aaa', 'bbb')), (4, ('zzz',)))}
    coverage_report(tmp_path, {'lib.py': run_line})
    result = run(tmp_path, 'lib.py', source, source.replace('return 2', 'return 3'))
    assert ids(result)[0] == 'tests/test_lib.py::test_zzz' and 'ran the changed lines' in result['tests'][0]['why']
    assert all(row['confidence'] == 'observed' for row in result['tests'])


def test_change_in_code_no_recorded_test_ran_is_a_gap(tmp_path):
    source = 'def parse(v):\n    if v:\n        return 1\n    return 2\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import parse\ndef test_a():\n    assert parse(1)\n'})
    coverage_report(tmp_path, {'lib.py': {'2': ['tests/test_lib.py::test_a|run'], '3': ['tests/test_lib.py::test_a|run']}})
    result = run(tmp_path, 'lib.py', source, source.replace('return 2', 'return 3'))
    assert result['untested'] == [dict(symbol='parse', file='lib.py', lines='4', basis='coverage')]
    assert ids(result) == ['tests/test_lib.py::test_a']


def test_recording_made_in_another_checkout_is_matched_by_path_suffix(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'src/lib.py': source, 'tests/test_lib.py': 'def test_dynamic():\n    assert __import__("lib").work()\n'})
    coverage_report(tmp_path, {'/ci/build/1234/src/lib.py': {'2': ['tests/test_lib.py::test_dynamic|run']}})
    result = run(tmp_path, 'src/lib.py', source, source.replace('1', '2'))
    assert ids(result) == ['tests/test_lib.py::test_dynamic'] and result['tests'][0]['confidence'] == 'observed'


def test_recording_from_before_the_edit_is_translated_when_the_edit_is_on_disk(tmp_path):
    source = 'def pad():\n    return 0\n\n\ndef parse(v):\n    if v:\n        return 1\n    return 2\n'
    changed = source.replace('def pad():\n    return 0\n', 'def pad():\n    extra = 0\n    more = 0\n    return extra + more\n').replace('return 2', 'return 3')
    tests = 'from lib import parse\n' + ''.join(f'def test_{n}():\n    assert parse({v})\n' for n, v in (('aaa', 1), ('zzz', 0)))
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    coverage_report(tmp_path, {'lib.py': {'6': ['tests/test_lib.py::test_aaa|run', 'tests/test_lib.py::test_zzz|run'],
                                          '7': ['tests/test_lib.py::test_aaa|run'], '8': ['tests/test_lib.py::test_zzz|run']}})
    import os
    os.utime(tmp_path/'coverage.json', (1_600_000_000, 1_600_000_000))
    (tmp_path/'lib.py').write_text(changed)
    result = run(tmp_path, 'lib.py', source, changed)
    assert ids(result)[0] == 'tests/test_lib.py::test_zzz' and 'ran the changed lines of parse' in result['tests'][0]['why']


def test_deletion_only_edit_already_on_disk_still_maps_to_its_function(tmp_path):
    source = 'def work():\n    value = 1\n    value += 1\n    return value\n'
    changed = source.replace('    value += 1\n', '')
    repository(tmp_path, {'lib.py': changed, 'tests/test_lib.py': 'from lib import work\ndef test_work():\n    assert work()\n'})
    result = run(tmp_path, 'lib.py', source, changed)
    assert result['changed_symbols'] == ['lib.py#work'] and ids(result) == ['tests/test_lib.py::test_work']


def test_test_asserting_on_removed_message_is_likely_to_fail_even_without_a_call_path(tmp_path):
    source = 'import warnings\ndef old():\n    warnings.warn("this option name is deprecated and will be removed in version nine")\n    return 1\n'
    changed = source.replace('    warnings.warn("this option name is deprecated and will be removed in version nine")\n', '')
    tests = ('import importlib, pytest\n'
             'def test_warns():\n    with pytest.warns(match="option name is deprecated and will be removed"):\n        getattr(importlib.import_module("lib"), "old")()\n'
             'def test_plain():\n    from lib import old\n    assert old()\n')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = run(tmp_path, 'lib.py', source, changed)
    first = result['tests'][0]
    assert first['id'] == 'tests/test_lib.py::test_warns' and first['expect'] == 'likely_fail' and 'removed text' in first['why']
    assert [r['expect'] for r in result['tests'][1:]] == ['reaches'] and result['summary'].startswith('1 likely_fail')


def test_test_using_a_deleted_definition_is_likely_to_fail_from_either_side_of_the_edit(tmp_path):
    source = 'class LegacyWrapper:\n    pass\n\n\ndef keep():\n    return 1\n'
    changed = 'def keep():\n    return 1\n'
    tests = 'import lib\ndef test_legacy():\n    assert lib.LegacyWrapper\ndef test_keep():\n    assert lib.keep()\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    before = run(tmp_path, 'lib.py', source, changed)
    (tmp_path/'lib.py').write_text(changed)
    after = run(tmp_path, 'lib.py', source, changed)
    for result in (before, after):
        assert result['tests'][0]['id'] == 'tests/test_lib.py::test_legacy' and result['tests'][0]['expect'] == 'likely_fail'
        assert 'uses removed LegacyWrapper' in result['tests'][0]['why']


def test_changed_function_nothing_reaches_is_reported_untested(tmp_path):
    source = 'def used():\n    return 1\n\n\ndef orphan():\n    return 2\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import used\ndef test_used():\n    assert used()\n'})
    result = run(tmp_path, 'lib.py', source, source.replace('return 2', 'return 3'))
    assert result['untested'] == [dict(symbol='orphan', file='lib.py', lines='6', basis='static_only')]
    assert run(tmp_path, 'lib.py', source, source.replace('return 1', 'return 5'))['untested'] == []
    default = analyze(tmp_path, patch('lib.py', source, source.replace('return 2', 'return 3')), index_dir=tmp_path/'.index')
    assert 'untested' not in default and default['summary'].startswith('0 likely_fail, '), 'untested rows stay out of the default packet'


def test_test_expecting_a_removed_warning_is_likely_to_fail(tmp_path):
    source = 'import warnings\ndef legacy():\n    warnings.warn("old", DeprecationWarning)\n    return 1\n'
    tests = ('import pytest\nfrom lib import legacy\n'
             'def test_warns():\n    with pytest.warns(DeprecationWarning, match="x"):\n        legacy()\n'
             'def test_value():\n    assert legacy() == 1\n')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = run(tmp_path, 'lib.py', source, source.replace('    warnings.warn("old", DeprecationWarning)\n', ''))
    assert [(r['id'].split('::')[-1], r['expect']) for r in result['tests']] == [('test_warns', 'likely_fail'), ('test_value', 'reaches')]
    assert 'DeprecationWarning' in result['tests'][0]['why']


def test_boilerplate_wording_shared_by_many_tests_is_not_a_likely_failure(tmp_path):
    source = 'def work():\n    """This is deprecated and will be removed soon."""\n    return 1\n'
    tests = 'from lib import work\n' + ''.join(f'def test_{i}():\n    assert work(), "this thing is deprecated and so on"\n' for i in range(8))
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = run(tmp_path, 'lib.py', source, source.replace('    """This is deprecated and will be removed soon."""\n', ''))
    assert all(r['expect'] == 'reaches' for r in result['tests'])


def test_definition_moved_to_another_file_is_not_a_removed_name(tmp_path):
    source = 'def helper_function():\n    return 1\n\n\ndef keep():\n    return 2\n'
    changed = 'from other import helper_function\n\n\ndef keep():\n    return 2\n'
    repository(tmp_path, {'lib.py': source, 'other.py': 'def helper_function():\n    return 1\n',
                          'tests/test_lib.py': 'import lib\ndef test_helper():\n    assert lib.helper_function()\n'})
    assert all(r['expect'] != 'likely_fail' for r in run(tmp_path, 'lib.py', source, changed)['tests'])


def test_shadow_execution_reports_exactly_the_tests_that_flip(tmp_path):
    import sys

    source = 'def work():\n    return 1\n\n\ndef other():\n    return 5\n'
    changed = source.replace('return 1', 'return 2')
    tests = 'from lib import work, other\ndef test_work():\n    assert work() == 1\ndef test_loose():\n    assert work()\ndef test_other():\n    assert other() == 5\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    for on_disk in (False, True):
        if on_disk:
            (tmp_path/'lib.py').write_text(changed)
        result = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index', run=True, python=sys.executable)
        assert result['predicted_failures'] == ['tests/test_lib.py::test_work'], result
        # Every test in a file that imports the changed module is a candidate (selection by subtraction, brief 4 A3).
        assert result['summary'].startswith('EXECUTED 3 of 3 candidate tests after the change') and '1 now fail' in result['summary']
        assert result['tests'] == []


def test_default_packet_lists_only_expected_failures(tmp_path):
    source = 'import warnings\ndef legacy():\n    warnings.warn("old", DeprecationWarning)\n    return 1\n'
    tests = ('import pytest\nfrom lib import legacy\n'
             'def test_warns():\n    with pytest.warns(DeprecationWarning, match="x"):\n        legacy()\n'
             'def test_value():\n    assert legacy() == 1\n')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    diff = patch('lib.py', source, source.replace('    warnings.warn("old", DeprecationWarning)\n', ''))
    default = analyze(tmp_path, diff, index_dir=tmp_path/'.index')
    assert [r['id'].split('::')[-1] for r in default['tests']] == ['test_warns'] and '--tests' in default['caveats']
    assert len(analyze(tmp_path, diff, index_dir=tmp_path/'.index', include_tests=True)['tests']) == 2


def test_removed_module_constant_and_inherited_test_methods(tmp_path):
    source = 'RAISE = object()\n\n\ndef keep():\n    return 1\n'
    changed = 'FETCH_RAISE = object()\n\n\ndef keep():\n    return 1\n'
    tests = ('import unittest\nimport lib\n'
             'class BaseTests(unittest.TestCase):\n    def test_mode(self):\n        assert lib.RAISE\n'
             'class OtherBackendTests(BaseTests):\n    pass\n')
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': tests})
    result = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index')
    assert [r['id'] for r in result['tests']] == ['tests/test_lib.py::BaseTests::test_mode', 'tests/test_lib.py::OtherBackendTests::test_mode']
    assert all(r['expect'] == 'likely_fail' for r in result['tests']) and 'inherits' in result['tests'][1]['why']
    again = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index')
    assert again['tests'] == result['tests'], 'a reused graph must still know the subclasses'


def test_runner_is_detected_from_the_tree_and_labels_are_pasteable(tmp_path):
    from amu.core import runners
    ident = 'tests/app/test_views.py::ViewTests::test_get'
    layouts = {'django-runtests': {'tests/runtests.py': ''}, 'django-manage': {'manage.py': '', 'app/tests.py': 'import unittest\n'},
               'pytest': {'pyproject.toml': '[tool.pytest.ini_options]\n', 'manage.py': ''},
               'unittest': {'tests/test_x.py': 'import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n'}}
    pasted = {'django-runtests': 'cd tests && python runtests.py app.test_views.ViewTests.test_get',
              'django-manage': 'python manage.py test tests.app.test_views.ViewTests.test_get',
              'pytest': 'python -m pytest ' + ident, 'unittest': 'python -m unittest tests.app.test_views.ViewTests.test_get'}
    for name, files in layouts.items():
        root = repository(tmp_path/name, files)
        assert runners.detect(root) == name and runners.get(root).paste(ident) == pasted[name]
    assert runners.get(tmp_path/'pytest', 'unittest').name == 'unittest'


def test_shadow_execution_with_the_unittest_runner_and_an_unloadable_module(tmp_path):
    import sys
    source = 'LIMIT = 3\n\n\ndef work():\n    return 1\n'
    changed = 'BOUND = 3\n\n\ndef work():\n    return 2\n'
    repository(tmp_path, {'lib.py': source, 'tests/__init__.py': '',
        'tests/test_a.py': 'import unittest\nimport lib\nclass A(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(lib.work(), 1)\n    def test_truthy(self):\n        self.assertTrue(lib.work())\n',
        'tests/test_b.py': 'import unittest\nfrom lib import LIMIT\nclass B(unittest.TestCase):\n    def test_limit(self):\n        self.assertEqual(LIMIT, 3)\n'})
    result = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert result['runner'] == 'unittest' and 'with unittest' in result['summary']
    assert sorted(result['predicted_failures']) == ['python -m unittest tests.test_a.A.test_value', 'python -m unittest tests.test_b.B.test_limit'], result


def test_failing_base_class_test_is_also_run_in_inheriting_subclasses(tmp_path):
    import sys
    source = 'def work():\n    return 1\n'
    tests = ('import unittest\nimport lib\nclass Base(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(lib.work(), 1)\n'
             'class Variant(Base):\n    pass\n')
    repository(tmp_path, {'lib.py': source, 'tests/__init__.py': '', 'tests/test_a.py': tests})
    result = analyze(tmp_path, patch('lib.py', source, source.replace('1', '2')), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert sorted(result['predicted_failures']) == ['python -m unittest tests.test_a.Base.test_value', 'python -m unittest tests.test_a.Variant.test_value']


def test_edited_tree_relinks_only_changed_files_and_matches_a_full_rebuild(tmp_path):
    lib = 'def work():\n    return 1\n\n\ndef other():\n    return 2\n'
    use = 'from lib import work\n\n\ndef call():\n    return work()\n'
    tests = 'from use import call\nfrom lib import other\ndef test_call():\n    assert call()\ndef test_other():\n    assert other()\n'
    repository(tmp_path, {'lib.py': lib, 'use.py': use, 'tests/test_x.py': tests})
    diff = patch('lib.py', lib, lib.replace('return 2', 'return 3'))
    analyze(tmp_path, diff, index_dir=tmp_path/'.index')
    (tmp_path/'use.py').write_text(use.replace('from lib import work', 'from lib import other as work'))
    relinked = analyze(tmp_path, diff, index_dir=tmp_path/'.index', include_tests=True)
    rebuilt = analyze(tmp_path, diff, index_dir=tmp_path/'.fresh', include_tests=True)
    assert any('relinked' in gap for gap in relinked['gaps']) and not any('relinked' in gap for gap in rebuilt['gaps'])
    assert ids(relinked) == ids(rebuilt) and set(ids(relinked)) == {'tests/test_x.py::test_call', 'tests/test_x.py::test_other'}


def test_own_uncommitted_change_is_executed_by_default_and_flips_come_first(tmp_path):
    import sys
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import work\ndef test_exact():\n    assert work() == 1\ndef test_loose():\n    assert work()\n'})
    git = ['git', '-C', str(tmp_path), '-c', 'user.email=a@b.c', '-c', 'user.name=a']
    for args in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'base']):
        subprocess.run([*git, *args], check=True)
    (tmp_path/'lib.py').write_text(source.replace('1', '2'))
    result = analyze(tmp_path, index_dir=tmp_path/'.index', python=sys.executable)
    assert list(result)[:3] == ['flips', 'predicted_failures', 'gaps'] and result['flips'] == 1
    assert result['predicted_failures'] == ['tests/test_lib.py::test_exact']
    static = analyze(tmp_path, index_dir=tmp_path/'.index', run=False)
    assert 'flips' not in static and list(static)[0] == 'summary'
    supplied = analyze(tmp_path, patch('lib.py', source, source.replace('1', '2')), index_dir=tmp_path/'.index')
    assert 'flips' not in supplied, 'a supplied patch is not executed unless asked'


def test_nothing_executed_is_not_reported_as_zero_failures(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'lib.py': source, 'tests/test_lib.py': 'from lib import work\ndef test_exact():\n    assert work() == 1\n'})
    result = analyze(tmp_path, patch('lib.py', source, source.replace('1', '2')), index_dir=tmp_path/'.index', run=True, python='/nonexistent/python')
    assert result['flips'] is None and result['summary'].startswith('NOT EXECUTED') and any('shadow run' in gap for gap in result['gaps'])


def test_recording_attributes_inherited_tests_and_child_processes(tmp_path):
    import sys

    import pytest
    pytest.importorskip('coverage')
    from amu.core import observe
    source = 'def direct():\n    return 1\n\n\ndef from_child():\n    return 2\n\n\ndef orphan():\n    return 3\n'
    tests = ('import subprocess, sys, unittest\nimport lib\n'
             'class Base(unittest.TestCase):\n    def test_direct(self):\n        self.assertEqual(lib.direct(), 1)\n'
             'class Variant(Base):\n    pass\n'
             'class Spawns(unittest.TestCase):\n    def test_child(self):\n'
             '        out = subprocess.run([sys.executable, "-c", "import lib; print(lib.from_child())"], capture_output=True, text=True)\n'
             '        self.assertEqual(out.stdout.strip(), "2")\n')
    repository(tmp_path, {'lib.py': source, 'tests/__init__.py': '', 'tests/test_a.py': tests})
    info = observe.record(tmp_path, sys.executable, index_dir=tmp_path/'.index')
    assert not info['gaps'] and info['tests'] >= 2 and info['links_only_through_child_processes'] >= 1, info
    changed = source.replace('return 2', 'return 20').replace('return 3', 'return 30')
    result = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index', full_evidence=True)
    assert result['coverage']['evidence'] == 'recording'
    by_id = {r['id']: r for r in result['tests']}
    assert by_id['tests/test_a.py::Spawns::test_child']['confidence'] == 'observed', 'a test that only reaches the code through a child process'
    assert result['untested'] == [dict(symbol='orphan', file='lib.py', lines='10', basis='coverage')]
    flips = analyze(tmp_path, patch('lib.py', source, source.replace('return 1', 'return 9')), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert sorted(flips['predicted_failures']) == ['python -m unittest tests.test_a.Base.test_direct', 'python -m unittest tests.test_a.Variant.test_direct']


def test_recorded_lines_follow_the_file_through_edits_made_after_the_recording(tmp_path):
    import sys

    import pytest
    pytest.importorskip('coverage')
    from amu.core import observe
    source = 'def parse(v):\n    if v:\n        return 1\n    return 2\n'
    tests = ('import unittest\nimport lib\nclass T(unittest.TestCase):\n'
             '    def test_truthy(self):\n        self.assertEqual(lib.parse(1), 1)\n'
             '    def test_falsy(self):\n        self.assertEqual(lib.parse(0), 2)\n')
    repository(tmp_path, {'lib.py': source, 'tests/__init__.py': '', 'tests/test_a.py': tests})
    assert not observe.record(tmp_path, sys.executable, index_dir=tmp_path/'.index')['gaps']
    # Someone else edits the file after the recording: every line moves down.
    later = '"""A docstring added later."""\nimport os\n\n\n' + source
    (tmp_path/'lib.py').write_text(later)
    result = analyze(tmp_path, patch('lib.py', later, later.replace('return 2', 'return 3')), index_dir=tmp_path/'.index', full_evidence=True)
    assert result['coverage']['edited_since'] == 1
    first = result['tests'][0]
    assert first['id'] == 'tests/test_a.py::T::test_falsy' and 'ran the changed lines of parse' in first['why'], result['tests']
    flips = analyze(tmp_path, patch('lib.py', later, later.replace('return 2', 'return 3')), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert flips['predicted_failures'] == ['python -m unittest tests.test_a.T.test_falsy'] and flips['flips'] == 1


def test_test_method_on_a_mixin_is_run_in_its_concrete_subclasses_not_reported_as_all_clear(tmp_path):
    import sys
    source = 'def key(v):\n    return "k:" + v\n'
    tests = ('import unittest\nimport lib\n'
             'class KeyTestsMixin:\n    def test_key(self):\n        self.assertEqual(lib.key("a"), "k:a")\n'
             'class LocalKeyTests(KeyTestsMixin, unittest.TestCase):\n    pass\n'
             'class RemoteKeyTests(KeyTestsMixin, unittest.TestCase):\n    pass\n')
    repository(tmp_path, {'lib.py': source, 'tests/__init__.py': '', 'tests/test_a.py': tests})
    result = analyze(tmp_path, patch('lib.py', source, source.replace('"k:"', '"key:"')), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert sorted(result['predicted_failures']) == ['python -m unittest tests.test_a.LocalKeyTests.test_key',
                                                    'python -m unittest tests.test_a.RemoteKeyTests.test_key'], result
    assert result['flips'] == 2 and 'EXECUTED 2 of 2' in result['summary']


def test_label_that_aborts_the_runner_is_isolated_and_reported(tmp_path):
    import sys

    from amu.core import runners
    tests = ('import unittest\nclass Mixin:\n    def test_mixin(self):\n        pass\n'
             'class Real(unittest.TestCase):\n    def test_ok(self):\n        pass\n    def test_bad(self):\n        self.fail("x")\n')
    repository(tmp_path, {'tests/__init__.py': '', 'tests/test_a.py': tests})
    ids = ['tests/test_a.py::Real::test_ok', 'tests/test_a.py::Mixin::test_mixin', 'tests/test_a.py::Real::test_bad']
    found, problem = runners.execute(runners.get(tmp_path, 'unittest'), sys.executable, tmp_path, ids, tmp_path/'r.xml', 60)
    assert found == {'tests/test_a.py::Real::test_ok': 'passed', 'tests/test_a.py::Mixin::test_mixin': 'unrunnable',
                     'tests/test_a.py::Real::test_bad': 'failed'} and problem is None


def test_original_rebuilds_a_file_whose_hunk_starts_with_inserted_lines(tmp_path):
    """New imports at the top of a file: the parent side must still be rebuilt (blind trial, w07)."""
    from amu.core.patch import changes, original
    before = 'import os\nimport re\nimport sys\n\n\ndef f():\n    return 1\n'
    after = 'from unittest import mock\n\nimport os\nimport re\nimport sys\n\n\ndef f():\n    return 2\n'
    (tmp_path/'m.py').write_text(after)
    patch = ('--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,5 @@\n+from unittest import mock\n+\n import os\n import re\n import sys\n'
             '@@ -4,4 +6,4 @@ import sys\n \n \n def f():\n-    return 1\n+    return 2\n')
    edits, _ = changes(tmp_path, patch)
    assert original(tmp_path, 'm.py', edits['m.py']) == before


def test_runner_reason_names_the_exception_and_the_frame_in_the_tree(tmp_path):
    from amu.core.runners import abort_reason
    output = ('Traceback (most recent call last):\n'
              f'  File "{tmp_path}/tests/runtests.py", line 782, in <module>\n'
              f'  File "{tmp_path}/django/contrib/sites/checks.py", line 11, in check_site_id\n'
              '    Site = apps.get_model("sites", "Site")\n'
              f'  File "{tmp_path}/django/apps/registry.py", line 165, in get_app_config\n'
              "LookupError: No installed app with label 'sites'.\n")
    assert abort_reason(output, tmp_path) == "LookupError: No installed app with label 'sites'. (at django/apps/registry.py:165)"


def test_a_runner_that_aborts_only_on_the_changed_code_is_a_failure(tmp_path):
    """Blind trial w04 and w07: nothing ran after the change. That is the change's failure, not a gap."""
    from pathlib import Path

    from amu.core import runners, shadow
    from amu.core.patch import changes
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests'/'test_lib.py').write_text('import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n')
    (tmp_path/'lib.py').write_text('VALUE = 2\n')
    patch = '--- a/lib.py\n+++ b/lib.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n'
    edits, _ = changes(tmp_path, patch)
    real = runners.execute

    def fake(runner, python, tree, ids, report, timeout):
        if Path(tree) == tmp_path:
            return {i: 'unrunnable' for i in ids}, "no outcomes read: NameError: name 'VALUE' is not defined (at lib.py:1)"
        return {i: 'passed' for i in ids}, None
    runners.execute = fake
    try:
        rows, info, gaps = shadow.run(tmp_path, edits, ['tests/test_lib.py::T::test_a'], runner='unittest')
    finally:
        runners.execute = real
    assert rows == []
    assert info['aborted'] == dict(error="NameError: name 'VALUE' is not defined (at lib.py:1)", ran_before=1, probed=1)


def test_parallel_parts_cover_every_candidate_once(tmp_path):
    from amu.core import runners, shadow
    seen, real = [], runners.execute

    def fake(runner, python, tree, ids, report, timeout):
        seen.append(list(ids))
        return {i: 'passed' for i in ids}, None
    runners.execute = fake
    try:
        found, problems = shadow.execute(runners.get(tmp_path, 'django-runtests'), 'python', tmp_path, [f't{i}' for i in range(10)], tmp_path, 'after', 5, 4)
    finally:
        runners.execute = real
    assert len(seen) == 4 and sorted(i for part in seen for i in part) == sorted(f't{i}' for i in range(10))
    assert found == {f't{i}': 'passed' for i in range(10)} and problems == []


def test_remembered_outcomes_before_the_change_expire_with_the_commit(tmp_path):
    import json

    from amu.core.shadow import load_memo
    memo = tmp_path/'memo.json'
    memo.write_text(json.dumps(dict(key='a', before={'t': 'passed'}, rate=12.0)))
    assert load_memo(memo, 'a')['before'] == {'t': 'passed'}
    assert load_memo(memo, 'b') == dict(key='b', before={}, rate=12.0, rates=[12.0], durations={}, flips={})
    memo.write_text(json.dumps(dict(key='a', before={}, rate=0.2, rates=[0.2, 6.0, 14.0])))
    assert load_memo(memo, 'b')['rate'] == 6.0


def test_a_failing_test_made_to_pass_by_editing_it_stays_listed(tmp_path):
    """Blind trial, Haiku w21: the agent edited the two flagged tests instead of its code."""
    import sys

    source = 'def work():\n    return 1\n\n\ndef other():\n    return 5\n'
    changed = source.replace('return 1', 'return 2')
    tests = 'from lib import work, other\ndef test_work():\n    assert work() == 1\ndef test_other():\n    assert other() == 5\n'
    edited = tests.replace('assert work() == 1', 'assert work() == 2')
    repository(tmp_path, {'lib.py': changed, 'tests/test_lib.py': edited})
    diff = patch('lib.py', source, changed) + patch('tests/test_lib.py', tests, edited)
    result = analyze(tmp_path, diff, index_dir=tmp_path/'.index', run=True, python=sys.executable, full_evidence=True)
    flagged = {row['id']: row['label'] for row in result['predicted_failures']}
    assert flagged == {'tests/test_lib.py::test_work': 'test edited, not fixed'}, result
    assert 'test edited, not fixed' in result['summary'] and 'also run as they were before the edit' in result['summary'], result['summary']
    assert result['original_tests']['tests/test_lib.py::test_work'] == 'failed'
    compact = analyze(tmp_path, diff, index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert compact['predicted_failures'] == ['tests/test_lib.py::test_work'], compact
    assert compact['test_edited_not_fixed'] == ['tests/test_lib.py::test_work'], compact
    # A change that breaks nothing, with an unrelated edit in the same test file, flags nothing.
    harmless = source.replace('return 5', 'return 2 + 3')
    added = tests + 'def test_new():\n    assert other() == 5\n'
    repository(tmp_path, {'lib.py': harmless, 'tests/test_lib.py': added})
    quiet = analyze(tmp_path, patch('lib.py', source, harmless) + patch('tests/test_lib.py', tests, added), index_dir=tmp_path/'.index',
                    run=True, python=sys.executable, full_evidence=True)
    assert quiet['predicted_failures'] == [] and quiet['flips'] == 0, quiet
    assert quiet['original_tests'] == {'tests/test_lib.py::test_other': 'passed', 'tests/test_lib.py::test_work': 'passed'}, quiet.get('original_tests')


def test_a_flip_that_does_not_repeat_is_unstable_not_a_failure(tmp_path):
    import sys

    marker = tmp_path/'failed-once'
    source = 'def flag():\n    return 1\n'
    changed = 'def flag():\n    return 2\n'
    tests = ('import pathlib\nfrom lib import flag\n'
             f'MARK = pathlib.Path({str(marker)!r})\n'
             'def test_flaky():\n    if flag() == 2 and not MARK.exists():\n        MARK.write_text("x")\n        assert False\n'
             'def test_steady():\n    assert flag() == 1\n')
    repository(tmp_path, {'lib.py': changed, 'tests/test_lib.py': tests})
    result = analyze(tmp_path, patch('lib.py', source, changed), index_dir=tmp_path/'.index', run=True, python=sys.executable)
    assert result['predicted_failures'] == ['tests/test_lib.py::test_steady'], result
    assert result['unstable'] == ['tests/test_lib.py::test_flaky'] and 'unstable, not counted' in result['summary'], result


def _shadow(tmp_path, files_before, files_after, ids, runner='pytest'):
    """shadow.run on a tree that holds ``files_after``, with the change from ``files_before``."""
    import sys

    from amu.core import shadow
    from amu.core.patch import changes
    repository(tmp_path, files_after)
    diff = ''.join(patch(path, files_before.get(path, ''), files_after.get(path, '')) for path in files_after if files_before.get(path) != files_after[path])
    edits, _ = changes(tmp_path, diff)
    return shadow.run(tmp_path, edits, ids, python=sys.executable, runner=runner)


def test_new_tests_in_an_edited_file_do_not_hide_an_edited_failing_test(tmp_path):
    lib0, lib1 = 'def work():\n    return 1\n', 'def work():\n    return 2\n'
    tests0 = 'from lib import work\ndef test_value():\n    assert work() == 1\n'
    tests1 = ('from lib import work\ndef test_new_a():\n    assert work() == 2\ndef test_new_b():\n    assert isinstance(work(), int)\n'
              'def test_value():\n    assert work() == 2\n')
    rows, info, gaps = _shadow(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests0}, {'lib.py': lib1, 'tests/test_lib.py': tests1},
                               ['tests/test_lib.py::test_new_a', 'tests/test_lib.py::test_new_b', 'tests/test_lib.py::test_value'])
    assert [(r['id'], r['label']) for r in rows] == [('tests/test_lib.py::test_value', 'test edited, not fixed')], (rows, gaps)


def test_a_renamed_class_or_a_deleted_same_named_method_is_removed_not_an_abort(tmp_path):
    lib0, lib1 = 'def work():\n    return 1\n', 'def work():\n    return 2\n'
    tests0 = ('from lib import work\nclass TestA:\n    def test_basic(self):\n        assert work() == 1\n'
              'class TestB:\n    def test_basic(self):\n        assert work() == 1\n')
    tests1 = 'from lib import work\nclass TestA:\n    def test_basic(self):\n        assert work() == 2\n'
    rows, info, gaps = _shadow(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests0}, {'lib.py': lib1, 'tests/test_lib.py': tests1},
                               ['tests/test_lib.py::TestB::test_basic'])
    assert 'aborted' not in info, info
    assert [(r['id'], r['label']) for r in rows] == [('tests/test_lib.py::TestB::test_basic', 'test edited, not fixed')], (rows, gaps)
    assert any('no longer defined' in g for g in gaps), gaps


def test_a_broken_test_module_with_inherited_tests_is_still_a_runner_abort(tmp_path):
    base = 'class Base:\n    def test_inherited(self):\n        assert True\n'
    sub0 = 'from tests.base import Base\n\n\nclass TestSub(Base):\n    pass\n'
    sub1 = 'from tests.base import Base, Nope\n\n\nclass TestSub(Base):\n    pass\n'
    files = {'tests/__init__.py': '', 'tests/base.py': base}
    rows, info, gaps = _shadow(tmp_path, {**files, 'tests/test_sub.py': sub0}, {**files, 'tests/test_sub.py': sub1},
                               ['tests/test_sub.py::TestSub::test_inherited'])
    assert info.get('aborted') and 'Nope' in info['aborted']['error'], (info, gaps)


def test_an_order_dependent_regression_the_change_brings_counts(tmp_path):
    # Owner's decision on brief 4 (A2): fails on the changed code after another test, passes alone, and the code before
    # the change passes in the same order: a state leak the change brings. It counts, with its own label.
    lib0 = 'def collect(x, acc=None):\n    acc = [] if acc is None else acc\n    acc.append(x)\n    return acc\n'
    lib1 = 'def collect(x, acc=[]):\n    acc.append(x)\n    return acc\n'
    tests = 'from lib import collect\ndef test_p():\n    assert collect(1)\ndef test_t():\n    assert collect(2) == [2]\n'
    rows, info, gaps = _shadow(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests},
                               ['tests/test_lib.py::test_p', 'tests/test_lib.py::test_t'])
    assert [(r['id'], r['trust']) for r in rows] == [('tests/test_lib.py::test_t', 'fails: order-dependent, new with this change')], (rows, gaps)
    assert 'alone in a fresh process' in rows[0]['why'] and 'state leak this change brings' in rows[0]['why'], rows
    assert not info.get('unstable'), info.get('unstable')


def test_an_order_dependence_already_on_the_base_stays_unstable(tmp_path):
    lib0 = 'def collect(x, acc=[]):\n    acc.append(x)\n    return acc\n'
    lib1 = 'def collect(x, acc=[]):\n    acc.append(x)  # kept\n    return acc\n'
    tests = 'from lib import collect\ndef test_p():\n    assert collect(1)\ndef test_t():\n    assert collect(2) == [2]\n'
    rows, info, gaps = _shadow(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests}, {'lib.py': lib1, 'tests/test_lib.py': tests},
                               ['tests/test_lib.py::test_p', 'tests/test_lib.py::test_t'])
    assert [(r['id'], r['trust']) for r in rows] == [('tests/test_lib.py::test_t', 'unstable')], (rows, gaps)
    assert 'an order dependence already there' in rows[0]['why'], rows
    assert info.get('unstable') == ['tests/test_lib.py::test_t']


def test_a_new_failing_test_is_not_an_existing_test_that_now_fails(tmp_path):
    lib = 'def work():\n    return 1\n'
    tests0 = 'from lib import work\ndef test_work():\n    assert work() == 1\n'
    tests1 = tests0 + 'def test_added():\n    assert work() == 3\n'
    rows, info, gaps = _shadow(tmp_path, {'lib.py': lib, 'tests/test_lib.py': tests0}, {'lib.py': lib.replace('1', '1  # same'), 'tests/test_lib.py': tests1},
                               ['tests/test_lib.py::test_added', 'tests/test_lib.py::test_work'])
    assert rows == [], rows


def test_a_deleted_unittest_test_is_removed_not_a_failure(tmp_path):
    lib = 'def work():\n    return 1\n'
    tests0 = ('import unittest\nfrom lib import work\nclass T(unittest.TestCase):\n    def test_keep(self):\n        assert work() == 1\n'
              '    def test_gone(self):\n        assert work() == 1\n')
    tests1 = 'import unittest\nfrom lib import work\nclass T(unittest.TestCase):\n    def test_keep(self):\n        assert work() == 1\n'
    files = {'tests/__init__.py': ''}
    rows, info, gaps = _shadow(tmp_path, {**files, 'lib.py': lib, 'tests/test_u.py': tests0},
                               {**files, 'lib.py': lib.replace('1', '1  # same'), 'tests/test_u.py': tests1},
                               ['tests/test_u.py::T::test_gone', 'tests/test_u.py::T::test_keep'], runner='unittest')
    assert rows == [] and any('no longer defined' in g for g in gaps), (rows, gaps)


def test_a_broken_edited_test_module_is_not_reported_as_fully_executed(tmp_path):
    import sys

    from amu.core import analyze
    lib0, lib1 = 'def f():\n    return 1\n', 'def f():\n    return 1  # tidy\n'
    tf0 = 'from lib import f\ndef test_f1():\n    assert f() == 1\ndef test_f2():\n    assert f()\n'
    tf1 = tf0.replace('from lib import f', 'from lib import f, missing_helper')
    repository(tmp_path, {'lib.py': lib1, 'tests/test_f.py': tf1})
    result = analyze(tmp_path, patch('lib.py', lib0, lib1) + patch('tests/test_f.py', tf0, tf1), index_dir=tmp_path/'.index', run=True,
                     python=sys.executable, full_evidence=True)
    assert not result['summary'].startswith('EXECUTED 2 of 2'), result['summary']


def test_new_methods_in_a_subclassing_test_class_do_not_hide_an_edited_failing_test(tmp_path):
    lib0, lib1 = 'def work():\n    return 1\n', 'def work():\n    return 2\n'
    tests0 = 'import unittest\nfrom lib import work\nclass TestWork(unittest.TestCase):\n    def test_value(self):\n        assert work() == 1\n'
    tests1 = ('import unittest\nfrom lib import work\nclass TestWork(unittest.TestCase):\n    def test_new_a(self):\n        assert work() == 2\n'
              '    def test_new_b(self):\n        assert isinstance(work(), int)\n    def test_value(self):\n        assert work() == 2\n')
    for _ in range(4):
        rows, info, gaps = _shadow(tmp_path, {'lib.py': lib0, 'tests/test_lib.py': tests0}, {'lib.py': lib1, 'tests/test_lib.py': tests1},
                                   ['tests/test_lib.py::TestWork::test_new_a', 'tests/test_lib.py::TestWork::test_new_b', 'tests/test_lib.py::TestWork::test_value'])
        assert [(r['id'], r['label']) for r in rows] == [('tests/test_lib.py::TestWork::test_value', 'test edited, not fixed')], (rows, gaps)


def test_a_test_moved_into_an_edited_base_class_is_not_removed(tmp_path):
    base0 = 'class Mixin:\n    pass\n'
    base1 = 'class Mixin:\n    def test_x(self):\n        assert True\n'
    sub0 = 'from tests.base import Mixin\n\n\nclass TestSub(Mixin):\n    def test_x(self):\n        assert True\n'
    sub1 = 'from tests.base import Mixin, nope\n\n\nclass TestSub(Mixin):\n    pass\n'
    rows, info, gaps = _shadow(tmp_path, {'tests/__init__.py': '', 'tests/base.py': base0, 'tests/test_sub.py': sub0},
                               {'tests/__init__.py': '', 'tests/base.py': base1, 'tests/test_sub.py': sub1}, ['tests/test_sub.py::TestSub::test_x'])
    assert info.get('aborted') and not any('no longer defined' in g for g in gaps), (info, gaps)


def test_a_module_level_test_deleted_beside_an_unrelated_same_named_def_is_removed(tmp_path):
    from amu.core.shadow import _removed
    (tmp_path/'tests').mkdir()
    (tmp_path/'tests/test_sub.py').write_text('class TestSub:\n    pass\n')
    (tmp_path/'tests/test_other.py').write_text('def test_x():\n    assert True\n')
    (tmp_path/'tests/test_mod.py').write_text('')
    edits = {'tests/test_sub.py': '', 'tests/test_other.py': '', 'tests/test_mod.py': ''}
    before = {'tests/test_sub.py': 'class TestSub:\n    def test_x(self):\n        assert True\n', 'tests/test_mod.py': 'def test_x():\n    pass\n'}
    # A module-level def of the same name elsewhere is not a base class taking the method over.
    assert _removed('tests/test_sub.py::TestSub::test_x', edits, before, tmp_path)
    assert _removed('tests/test_mod.py::test_x', edits, before, tmp_path)


def test_the_packet_stays_under_its_limit_with_many_long_unstable_ids():
    from amu.core import LIMIT, compact, encoded
    ids = [f'tests/integration/test_library_module_operations.py::TestConcurrentAccess::test_{"x" * 150}_{i:02d}' for i in range(12)]
    gap = f'{len(ids)} test(s) flipped once and not on a rerun; reported as unstable, not as failures: ' + ', '.join(ids[:3]) + ' and 9 more'
    report = dict(tests=[], caveats='', predicted_failures=[], unstable=ids, gaps=[gap], changed_symbols=['lib.py#flag'], impacted=[],
                  consequences=[], runner='pytest', flips=0, summary='EXECUTED 12 of 12')
    out = compact(report)
    assert len(encoded(out)) < LIMIT and len(out['gaps'][0]) <= 240, out
    assert out['unstable'][-1] == f"and {len(ids) - len(out['unstable']) + 1} more", out['unstable']


def _git_tree(tmp_path, files):
    import subprocess
    repository(tmp_path, files)
    for args in (['init', '-q'], ['add', '-A'], ['-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', 'start']):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)


def _bare_candidates(tmp_path):
    """The ids bare amu would execute for the uncommitted change in tmp_path (execution stubbed)."""
    from amu.core import shadow
    seen, real = [], shadow.run
    shadow.run = lambda root, edits, ids, *a, **k: (seen.extend(ids), ([], dict(ran=0, seconds=0.0, runner='rec'), []))[1]
    try:
        report = analyze(tmp_path, None, index_dir=tmp_path/'.index', run=True, full_evidence=True)
    finally:
        shadow.run = real
    return seen, report


def test_a_renamed_function_still_has_candidates_on_the_applied_change(tmp_path):
    lib = ('def slash_redirect(path):\n    return path + "/"\n\n\n'
           'def route(path):\n    return slash_redirect(path)\n')
    files = {'lib.py': lib,
             'tests/test_redirect.py': 'from lib import slash_redirect\ndef test_slash_redirect():\n    assert slash_redirect("a") == "a/"\n',
             'tests/test_route.py': 'from lib import route\ndef test_route():\n    assert route("a") == "a/"\n',
             'tests/test_other.py': 'def test_unrelated():\n    assert 1\n'}
    _git_tree(tmp_path, files)
    (tmp_path/'lib.py').write_text(lib.replace('def slash_redirect', 'def _slash_redirect'))
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_redirect.py::test_slash_redirect' in seen, seen
    assert 'tests/test_route.py::test_route' in seen, seen
    assert 'tests/test_other.py::test_unrelated' not in seen
    assert 'lib.py#slash_redirect (renamed or removed)' in report['changed_symbols'], report['changed_symbols']


def test_a_removed_function_still_has_candidates_on_the_applied_change(tmp_path):
    lib = ('def legacy_total(items):\n    return sum(items)\n\n\n'
           'def report(items):\n    return f"total {legacy_total(items)}"\n\n\n'
           'def keep():\n    return 1\n')
    files = {'lib.py': lib,
             'tests/test_total.py': 'import lib\ndef test_legacy_total():\n    assert lib.legacy_total([1, 2]) == 3\n',
             'tests/test_report.py': 'from lib import report\ndef test_report():\n    assert report([1]) == "total 1"\n',
             'tests/test_keep.py': 'from lib import keep\ndef test_keep():\n    assert keep() == 1\n'}
    _git_tree(tmp_path, files)
    (tmp_path/'lib.py').write_text(lib.replace('def legacy_total(items):\n    return sum(items)\n\n\n', ''))
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_total.py::test_legacy_total' in seen and 'tests/test_report.py::test_report' in seen, seen
    assert 'lib.py#legacy_total (renamed or removed)' in report['changed_symbols'], report['changed_symbols']


def _rows(report):
    return {row['id']: row for row in report['tests']}


def test_a_name_found_by_text_is_labelled_as_such(tmp_path):
    lib = 'def total(items):\n    return sum(items)\n'
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_total.py': 'import lib\ndef test_total():\n    assert lib.total([1]) == 1\n'})
    (tmp_path/'lib.py').write_text(lib.replace('def total', 'def _total'))
    _, report = _bare_candidates(tmp_path)
    row = _rows(report)['tests/test_total.py::test_total']
    assert row['confidence'] == 'heuristic' and row['why'].startswith('names lib.py#total, renamed or removed'), row


def test_a_syntax_error_mid_edit_is_not_a_removal(tmp_path):
    lib = 'def alpha_one():\n    return 1\n\n\ndef beta_two():\n    return 2\n'
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_lib.py': 'from lib import beta_two\ndef test_beta_two():\n    assert beta_two() == 2\n'})
    (tmp_path/'lib.py').write_text(lib.replace('return 1', 'return (1'))
    _, report = _bare_candidates(tmp_path)
    assert not any('renamed or removed' in s for s in report['changed_symbols']), report['changed_symbols']


def test_a_deleted_or_moved_module_still_has_candidates(tmp_path):
    import subprocess
    files = {'pkg/__init__.py': '', 'pkg/legacy.py': 'def legacy_total(items):\n    return sum(items)\n',
             'pkg/core.py': 'def keep():\n    return 1\n',
             'tests/test_legacy.py': 'from pkg.legacy import legacy_total\ndef test_legacy_total():\n    assert legacy_total([1]) == 1\n',
             'tests/test_core.py': 'from pkg.core import keep\ndef test_keep():\n    assert keep() == 1\n'}
    _git_tree(tmp_path, files)
    (tmp_path/'pkg/legacy.py').unlink()
    (tmp_path/'pkg/core.py').write_text('def keep():\n    return 2\n')
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_legacy.py::test_legacy_total' in seen, (seen, report['changed_symbols'])
    subprocess.run(['git', '-C', str(tmp_path), 'checkout', '-q', '--', '.'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'mv', 'pkg/legacy.py', 'pkg/totals.py'], check=True)
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_legacy.py::test_legacy_total' in seen, (seen, report['changed_symbols'])


def test_a_pure_deletion_whose_lines_repeat_below_is_applied(tmp_path):
    lib = 'import os\n\n\ndef legacy_sep():\n    return os.sep\n\n\ndef keep_sep():\n    return os.sep\n'
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_legacy.py': 'from lib import legacy_sep\ndef test_legacy_sep():\n    assert legacy_sep()\n'})
    (tmp_path/'lib.py').write_text(lib.replace('def legacy_sep():\n    return os.sep\n\n\n', ''))
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_legacy.py::test_legacy_sep' in seen and not any('did not apply' in g for g in report['gaps']), (seen, report['gaps'])
    from amu.core.patch import applied, changes
    edits, _ = changes(tmp_path, patch('lib.py', lib, lib.replace('def legacy_sep():\n    return os.sep\n\n\n', '')))
    (tmp_path/'lib.py').write_text(lib)
    assert not applied(tmp_path, 'lib.py', edits['lib.py'])


def test_closures_common_method_names_and_names_defined_elsewhere_do_not_pull_in_unrelated_tests(tmp_path):
    files = {'lib.py': ('import functools\n\n\ndef logged(fn):\n    @functools.wraps(fn)\n    def inner(*a):\n        return fn(*a)\n    return inner\n\n\n'
                        'class Store:\n    def put(self, k, v):\n        self.d = {k: v}\n\n    def get(self, k):\n        return self.d[k]\n\n\n'
                        'def get_host(env):\n    return env["HOST"]\n'),
             'wsgi.py': 'def get_host(env):\n    return env.get("SERVER")\n',
             'tests/test_dict.py': 'def test_dict():\n    inner = {"a": 1}\n    assert inner.get("a") == 1\n',
             'tests/test_wsgi.py': 'from wsgi import get_host\ndef test_wsgi_get_host():\n    assert get_host({"SERVER": 1}) == 1\n',
             'tests/test_store.py': 'from lib import Store\ndef test_store():\n    s = Store()\n    s.put(1, 2)\n    assert s.get(1) == 2\n'}
    _git_tree(tmp_path, files)
    lib = files['lib.py'].replace('    @functools.wraps(fn)\n    def inner(*a):\n        return fn(*a)\n    return inner\n', '    return fn\n')
    lib = lib.replace('\n    def get(self, k):\n        return self.d[k]\n', '').replace('def get_host(env):\n    return env["HOST"]\n', '')
    (tmp_path/'lib.py').write_text(lib)
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_store.py::test_store' in seen, seen
    assert 'tests/test_dict.py::test_dict' not in seen, seen
    assert not any('inner' in s for s in report['changed_symbols']), report['changed_symbols']
    assert _rows(report).get('tests/test_wsgi.py::test_wsgi_get_host', {}).get('confidence') in (None, 'heuristic'), report['tests']


def test_a_test_module_importing_the_old_name_puts_all_its_tests_forward(tmp_path):
    lib = 'def to_upper(s):\n    return s.upper()\n'
    tests = ('import pytest\nfrom lib import to_upper\nCONVERTERS = [(to_upper, "A")]\n\n\n'
             '@pytest.mark.parametrize("fn,want", CONVERTERS)\ndef test_converts(fn, want):\n    assert fn("a") == want\n')
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_conv.py': tests})
    (tmp_path/'lib.py').write_text(lib.replace('to_upper', 'upper_case'))
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_conv.py::test_converts' in seen, (seen, report['gaps'])


def test_co_change_ignores_root_and_wide_commits_and_git_failures(tmp_path, monkeypatch):
    import subprocess

    from amu.core import vanished
    def commit(files, message):
        repository(tmp_path, files)
        for args in (['add', '-A'], ['-c', 'user.email=t@example.invalid', '-c', 'user.name=t', 'commit', '-qm', message]):
            subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'init', '-q'], check=True)
    commit({'lib.py': 'a = 1\n', 'tests/test_lib.py': 'x = 1\n', 'other.py': 'o = 1\n'}, 'root: everything')
    commit({'lib.py': 'a = 2\n', 'tests/test_lib.py': 'x = 2\n'}, 'together')
    commit({'lib.py': 'a = 3\n', **{f'gen/m{i}.py': '' for i in range(vanished.WIDE + 1)}}, 'wide')
    assert vanished.co_changed(tmp_path, 'lib.py') == {'tests/test_lib.py': 1.0}
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 30)
    monkeypatch.setattr(vanished, '_git', slow)
    assert vanished.co_changed(tmp_path, 'lib.py') == {}


def test_a_function_moved_behind_a_deprecation_shim_still_has_candidates_but_a_re_export_does_not(tmp_path):
    lib = 'def parse_form(data):\n    return dict(data)\n'
    shim = ('import warnings\n\n\ndef _parse_form(data):\n    return dict(data)\n\n\n'
            'def __getattr__(name):\n    if name == "parse_form":\n        warnings.warn("deprecated", DeprecationWarning)\n'
            '        return _parse_form\n    raise AttributeError(name)\n')
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_form.py': 'from lib import parse_form\ndef test_parse_form():\n    assert parse_form([]) == {}\n'})
    (tmp_path/'lib.py').write_text(shim)
    seen, report = _bare_candidates(tmp_path)
    assert 'tests/test_form.py::test_parse_form' in seen, (seen, report['changed_symbols'])
    (tmp_path/'forms.py').write_text(lib)
    (tmp_path/'lib.py').write_text('from forms import parse_form\n')
    _, report = _bare_candidates(tmp_path)
    assert 'lib.py#parse_form (renamed or removed)' not in report['changed_symbols'], report['changed_symbols']


def test_a_candidate_the_project_deselects_is_skipped_without_splitting(tmp_path, monkeypatch):
    import subprocess
    import sys

    from amu.core import runners
    repository(tmp_path, {
        'pyproject.toml': '[tool.pytest.ini_options]\naddopts = "-m \'not slow\'"\nmarkers = ["slow"]\n',
        'tests/test_lib.py': ('import pytest\n\n\ndef test_quick():\n    assert True\n\n\n'
                              '@pytest.mark.slow\ndef test_slow():\n    assert True\n\n\n'
                              'class TestGroup:\n    @pytest.mark.slow\n    def test_slow_method(self):\n        assert True\n')})
    starts, real = [], subprocess.run

    def counted(command, **kwargs):
        starts.append(command)
        return real(command, **kwargs)
    monkeypatch.setattr(runners.subprocess, 'run', counted)
    ids = ['tests/test_lib.py::test_quick', 'tests/test_lib.py::test_slow', 'tests/test_lib.py::TestGroup::test_slow_method']
    found, problem = runners.execute(runners.get(tmp_path, 'pytest'), sys.executable, tmp_path, ids, tmp_path/'r.xml', 60)
    assert found == {ids[0]: 'passed', ids[1]: 'skipped', ids[2]: 'skipped'} and problem is None, (found, problem)
    assert len(starts) == 2, starts  # the batch and one collection, no splitting
    only_slow, problem = runners.execute(runners.get(tmp_path, 'pytest'), sys.executable, tmp_path, ids[1:], tmp_path/'r.xml', 60)
    assert only_slow == {ids[1]: 'skipped', ids[2]: 'skipped'} and problem is None, (only_slow, problem)


def test_tests_inside_a_package_make_the_tree_evident(tmp_path):
    from amu.core import runners
    repository(tmp_path, {'pkg/core/basic.py': 'x = 1\n', 'pkg/core/tests/__init__.py': ''})
    assert not runners.evident(tmp_path)
    repository(tmp_path, {'.hidden/tests/test_x.py': 'def test_x():\n    pass\n'})
    assert not runners.evident(tmp_path)
    repository(tmp_path, {'pkg/core/tests/test_basic.py': 'def test_x():\n    pass\n'})
    assert runners.evident(tmp_path)


def test_a_test_named_with_the_bare_prefix_is_a_candidate(tmp_path):
    lib = 'def nth_root(x, n):\n    return x ** (1 / n)\n'
    tests = ('import pytest\nfrom lib import nth_root\n\n\ndef test1():\n    assert nth_root(4, 2) == 2\n\n\n'
             'class TestRoots:\n    def testSquare(self):\n        assert nth_root(9, 2) == 3\n\n\n'
             'def test_cli():\n    def test():\n        return nth_root(8, 3)\n    assert test()\n')
    _git_tree(tmp_path, {'lib.py': lib, 'tests/test_roots.py': tests})
    (tmp_path/'lib.py').write_text(lib.replace('1 / n', '1.0 / n'))
    seen, _ = _bare_candidates(tmp_path)
    assert 'tests/test_roots.py::test1' in seen and 'tests/test_roots.py::TestRoots::testSquare' in seen, seen
    # A function named test inside a test is a local helper; pytest never collects it.
    assert 'tests/test_roots.py::test_cli' in seen and not any(i.endswith('::test_cli::test') for i in seen), seen
