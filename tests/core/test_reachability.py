import difflib
import json
import subprocess

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
    assert list((tmp_path/'.index').rglob('graph-*.json'))
    second = run(tmp_path, 'a.py', source, source.replace('1', '2'))
    assert first['tests'] == second['tests'] and first['report_hash'] == second['report_hash']


def test_non_python_patch_keeps_python_results(tmp_path):
    source = 'def work():\n    return 1\n'
    repository(tmp_path, {'a.py': source, 'tests/test_a.py': 'from a import work\ndef test_work():\n    assert work()\n'})
    candidate = patch('a.py', source, source.replace('1', '2'))+patch('notes.rst', 'old\n', 'new\n')
    result = analyze(tmp_path, candidate)
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
    result = analyze(tmp_path, index_dir=tmp_path/'.index')
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
    result = analyze(tmp_path, patch('package_with_long_name/library_module.py', source, source.replace('return 1', 'return 2')), index_dir=tmp_path/'.index')
    assert len(encoded(result)) < LIMIT and len(json.dumps(result, separators=(',', ':')).encode()) < LIMIT
    assert len(result['tests']) == 10 and set(result) >= {'summary', 'changed_symbols', 'untested', 'tests', 'consequences', 'gaps', 'caveats', 'report_hash'}
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
        assert result['summary'].startswith('EXECUTED 2 candidate tests') and '1 now fail' in result['summary']
        assert {r['id'].split('::')[-1]: r['expect'] for r in result['tests']} == {'test_loose': 'unchanged'}
