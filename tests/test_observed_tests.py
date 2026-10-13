"""Observed execution is useful evidence, never an assertion or deselection proof."""
import difflib
import json
from dataclasses import replace

import pytest

from amu import observed_tests
from amu.services import impact


def fixture(root):
    source = 'def choose(value):\n    if value:\n        return 1\n    return 0\n'
    (root / 'api.py').write_text(source)
    (root / 'test_api.py').write_text('def test_dynamic():\n    pass\ndef test_other():\n    pass\n')
    snapshot = observed_tests.snapshot(root, environment='py311-ci', command='pytest --cov-context=test')
    (root / 'snapshot.json').write_text(json.dumps(snapshot))
    raw = {'meta': {'version': '7.10', 'show_contexts': True}, 'files': {
        'api.py': {'executed_lines': [1, 2, 3], 'contexts': {
            '1': [''], '2': ['test_api.py::test_dynamic[pipe|value]|run'],
            '3': ['test_api.py::test_dynamic[pipe|value]|run']}}}}
    (root / 'coverage.json').write_text(json.dumps(raw))
    bundle = observed_tests.import_report(root, 'snapshot.json', 'coverage.json')
    (root / 'evidence.json').write_text(json.dumps(bundle))
    patch = ''.join(difflib.unified_diff(source.splitlines(True),
        source.replace('return 0', 'return -1').splitlines(True), fromfile='a/api.py', tofile='b/api.py'))
    return impact.PatchRequest(patch, base='worktree', coverage_evidence='evidence.json')


def test_unexecuted_changed_branch_still_prioritizes_observed_function(tmp_path):
    req = fixture(tmp_path)
    baseline = impact.execute_patch(str(tmp_path), replace(req, coverage_evidence=None))
    report = impact.execute_patch(str(tmp_path), req)
    assert baseline['summary']['existing_checks'] == []
    check = report['summary']['existing_checks'][0]
    assert check['test_id'] == 'test_api.py::test_dynamic[pipe|value]'
    assert check['kind'] == 'observed-baseline-execution'
    assert check['coverage_proven'] is False
    assert report['assessment']['executed'] is False
    assert report['summary']['observed_execution']['automatic_deselection'] is False
    detail = impact.execute_patch(str(tmp_path), replace(req, section='recommended_checks'))
    row = detail['details']['items'][0]
    assert row['observed_execution'][0]['lines'] == [2, 3]
    assert row['observed_execution'][0]['phases'] == ['run']
    assert row['behavioral_rank'] == 4
    assert not any(a.get('result_assertion') for a in row['associations'])


@pytest.mark.parametrize('change', ['test', 'dependency', 'new-file', 'config'])
def test_stale_evidence_falls_back_without_claiming_no_tests(tmp_path, change):
    req = fixture(tmp_path)
    if change == 'test':
        (tmp_path / 'test_api.py').write_text('def test_dynamic():\n    assert False\n')
    elif change == 'dependency':
        # This dependency must already have been in the source binding.
        (tmp_path / 'api.py').write_text((tmp_path / 'api.py').read_text() + '\n# changed\n')
    elif change == 'new-file':
        (tmp_path / 'helper.py').write_text('VALUE = 1\n')
    else:
        (tmp_path / 'pytest.ini').write_text('[pytest]\naddopts=-q\n')
    report = impact.execute_patch(str(tmp_path), req)
    assert report['summary']['observed_execution']['status'] == 'stale'
    assert not report['summary']['existing_checks']


def test_changed_artifact_invalidates_cached_cursor(tmp_path):
    req = fixture(tmp_path)
    report = impact.execute_patch(str(tmp_path), req)
    bundle = json.loads((tmp_path / 'evidence.json').read_text())
    bundle['context_table'].append('test_api.py::test_other|setup')
    bundle['observations']['api.py']['2'] = [len(bundle['context_table']) - 1]
    (tmp_path / 'evidence.json').write_text(json.dumps(bundle))
    with pytest.raises(impact.ImpactInputError, match='stale'):
        impact.execute_patch(str(tmp_path), replace(req, section='recommended_checks',
            cursor=report['retrieval']['recommended_checks']['cursor']))


def test_source_changes_between_snapshot_and_import_are_refused(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'test_api.py').write_text('# changed\n')
    with pytest.raises(impact.ImpactInputError, match='changed'):
        observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')


@pytest.mark.parametrize('bad', ['../escape.json', '/tmp/escape.json', 'nested/../../escape.json'])
def test_artifact_paths_stay_inside_authorized_repository(tmp_path, bad):
    with pytest.raises(impact.ImpactInputError):
        impact.validate_patch_request(impact.PatchRequest('--- a/api.py\n+++ b/api.py\n@@ -1 +1 @@\n-a\n+b\n', coverage_evidence=bad))


def test_missing_contexts_are_not_mistaken_for_per_test_coverage(tmp_path):
    fixture(tmp_path)
    (tmp_path / 'coverage.json').write_text(json.dumps({'meta': {}, 'files': {'api.py': {'executed_lines': [2]}}}))
    with pytest.raises(impact.ImpactInputError, match='contexts'):
        observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')


def test_cli_ingestion_and_mcp_forwarding(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from amu import cli, mcp_server
    req = fixture(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli.app, ['coverage-snapshot', '--repo', str(tmp_path),
        '--environment', 'ci-123', '--command', 'pytest', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['format'] == 'amu-coverage-snapshot'
    result = runner.invoke(cli.app, ['coverage-import', '--repo', str(tmp_path),
        '--snapshot', 'snapshot.json', '--coverage-json', 'coverage.json', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['format'] == 'amu-observed-tests'
    result = runner.invoke(cli.app, ['analyze-patch', '--repo', str(tmp_path), '--base', 'worktree',
        '--patch', req.patch, '--coverage-evidence', 'evidence.json', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['summary']['observed_execution']['prioritized_tests'] == 1
    captured = []
    monkeypatch.setattr(mcp_server, '_cli', lambda *args, **kwargs: captured.append((args, kwargs)) or '{}')
    mcp_server.amu_analyze_patch(req.patch, base='worktree', repo=str(tmp_path), coverage_evidence='evidence.json')
    assert '--coverage-evidence=evidence.json' in captured[0][0]


def test_unattributed_imports_and_other_context_plugins_do_not_select_tests(tmp_path):
    req = fixture(tmp_path)
    bundle = json.loads((tmp_path / 'evidence.json').read_text())
    bundle['context_table'] = ['', 'some_other_plugin_context']
    bundle['observations']['api.py'] = {'1': [0], '2': [1]}
    (tmp_path / 'evidence.json').write_text(json.dumps(bundle))
    report = impact.execute_patch(str(tmp_path), req)
    assert report['summary']['existing_checks'] == []
    assert report['summary']['observed_execution']['unobserved_entities'] == ['api.py#choose']
    assert report['summary']['observed_execution']['unattributed_contexts'] == 2


def test_added_function_has_no_invented_execution(tmp_path):
    req = fixture(tmp_path)
    old = (tmp_path / 'api.py').read_text()
    patch = ''.join(difflib.unified_diff(old.splitlines(True),
        (old + '\ndef added():\n    return 2\n').splitlines(True), fromfile='a/api.py', tofile='b/api.py'))
    report = impact.execute_patch(str(tmp_path), replace(req, patch=patch))
    assert not report['summary']['existing_checks']
    assert report['summary']['observed_execution']['unobserved_entities'] == ['api.py#added']


def test_symlink_artifact_and_unknown_test_are_rejected(tmp_path):
    req = fixture(tmp_path)
    (tmp_path / 'link.json').symlink_to(tmp_path / 'evidence.json')
    with pytest.raises(impact.ImpactInputError, match='symbolic'):
        impact.execute_patch(str(tmp_path), replace(req, coverage_evidence='link.json'))
    raw = json.loads((tmp_path / 'coverage.json').read_text())
    raw['files']['api.py']['contexts']['2'] = ['../outside.py::test_bad|run']
    (tmp_path / 'coverage.json').write_text(json.dumps(raw))
    with pytest.raises(impact.ImpactInputError, match='inside'):
        observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')


def test_all_execution_phases_retained_and_duplicate_contexts_deduplicated(tmp_path):
    req = fixture(tmp_path)
    bundle = json.loads((tmp_path / 'evidence.json').read_text())
    bundle['context_table'] = ['test_api.py::test_dynamic|setup', 'test_api.py::test_dynamic|teardown']
    bundle['observations']['api.py'] = {'2': [0, 0, 1]}
    (tmp_path / 'evidence.json').write_text(json.dumps(bundle))
    report = impact.execute_patch(str(tmp_path), replace(req, section='recommended_checks'))
    assert report['details']['items'][0]['observed_execution'][0]['phases'] == ['setup', 'teardown']


def test_real_pytest_cov_artifact_through_dynamic_fixture(tmp_path):
    """Execute only this reviewed self-contained fixture, never an upstream repo."""
    import os
    import subprocess
    import sys

    pytest.importorskip('pytest_cov')
    source = ('class Counter:\n    def __init__(self, value):\n        self.value = int(value)\n'
              '    def result(self):\n        return self.value + 1\n')
    (tmp_path / 'engine.py').write_text(source)
    (tmp_path / 'test_engine.py').write_text(
        'import pytest\nfrom engine import Counter\n'
        '@pytest.fixture\ndef instance():\n    return Counter("4")\n'
        'def test_result(instance):\n    assert getattr(instance, "result")() == 5\n'
        'def test_unrelated():\n    assert 1 + 1 == 2\n')
    snap = observed_tests.snapshot(tmp_path, environment='reviewed-fixture', command='pytest --cov-context=test')
    (tmp_path / 'snapshot.json').write_text(json.dumps(snap))
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'COVERAGE_CORE': 'ctrace', 'COVERAGE_FILE': str(tmp_path / '.coverage')}
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env.pop('PYTHONPATH', None)
    commands = [[sys.executable, '-m', 'pytest', '-p', 'pytest_cov.plugin', '--cov=engine',
                 '--cov-context=test', '--cov-report=', '-q'],
                [sys.executable, '-m', 'coverage', 'json', '--show-contexts', '-o', 'coverage.json']]
    for command in commands:
        run = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout + run.stderr
    imported_json = observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')
    imported_data = observed_tests.import_report(tmp_path, 'snapshot.json', data_path='.coverage')
    assert imported_json['observations'] == imported_data['observations']
    (tmp_path / 'evidence.json').write_text(json.dumps(imported_data))
    patch = ''.join(difflib.unified_diff(source.splitlines(True), source.replace('+ 1', '+ 2').splitlines(True),
                                       fromfile='a/engine.py', tofile='b/engine.py'))
    base = impact.PatchRequest(patch, base='worktree')
    static = impact.execute_patch(str(tmp_path), base)
    observed = impact.execute_patch(str(tmp_path), replace(base, coverage_evidence='evidence.json'))
    assert not static['summary']['existing_checks']
    assert [c['test_id'] for c in observed['summary']['existing_checks']] == ['test_engine.py::test_result']
    assert observed['summary']['observed_execution']['status'] == 'source-matched'


def test_limits_and_malformed_artifacts_fail_explicitly(tmp_path, monkeypatch):
    req = fixture(tmp_path)
    bundle = json.loads((tmp_path / 'evidence.json').read_text())
    bundle['observations']['api.py']['2'] = [False]
    (tmp_path / 'evidence.json').write_text(json.dumps(bundle))
    with pytest.raises(impact.ImpactInputError, match='index'):
        impact.execute_patch(str(tmp_path), req)
    (tmp_path / 'bad.json').write_text('{"a": 1, "a": 2}')
    with pytest.raises(impact.ImpactInputError, match='duplicate'):
        observed_tests.read_json(tmp_path, 'bad.json')
    monkeypatch.setattr(observed_tests, 'MAX_OBSERVATIONS', 1)
    with pytest.raises(impact.ImpactInputError, match='limit'):
        observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')


def test_deletion_uses_old_span_and_new_line_numbers_are_irrelevant(tmp_path):
    req = fixture(tmp_path)
    old = (tmp_path / 'api.py').read_text()
    patch = ''.join(difflib.unified_diff(old.splitlines(True), [], fromfile='a/api.py', tofile='/dev/null'))
    result = impact.execute_patch(str(tmp_path), replace(req, patch=patch))
    assert result['summary']['observed_execution']['prioritized_tests'] == 1
    patch = ''.join(difflib.unified_diff(old.splitlines(True), ('# inserted\n' + old.replace('return 0', 'return -1')).splitlines(True),
                                       fromfile='a/api.py', tofile='b/api.py'))
    result = impact.execute_patch(str(tmp_path), replace(req, patch=patch))
    assert result['summary']['observed_execution']['prioritized_tests'] == 1


def test_method_body_change_does_not_promote_other_class_execution(tmp_path):
    source = 'class Thing:\n    def __init__(self):\n        self.ready = True\n    def result(self):\n        return 1\n'
    (tmp_path / 'api.py').write_text(source)
    (tmp_path / 'test_api.py').write_text('def test_result():\n    pass\ndef test_construct():\n    pass\n')
    snap = observed_tests.snapshot(tmp_path, environment='fixture', command='pytest')
    (tmp_path / 'snapshot.json').write_text(json.dumps(snap))
    raw = {'meta': {'show_contexts': True}, 'files': {'api.py': {'contexts': {
        '3': ['test_api.py::test_construct|run', 'test_api.py::test_result|run'],
        '5': ['test_api.py::test_result|run']}}}}
    (tmp_path / 'coverage.json').write_text(json.dumps(raw))
    (tmp_path / 'evidence.json').write_text(json.dumps(observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')))
    patch = ''.join(difflib.unified_diff(source.splitlines(True), source.replace('return 1','return 2').splitlines(True),
                                       fromfile='a/api.py', tofile='b/api.py'))
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(patch, base='worktree', coverage_evidence='evidence.json'))
    assert [row['test_id'] for row in report['summary']['existing_checks']] == ['test_api.py::test_result']


def test_invalid_unicode_is_an_input_error(tmp_path):
    req = fixture(tmp_path)
    with pytest.raises(impact.ImpactInputError, match='UTF-8'):
        impact.validate_patch_request(replace(req, coverage_evidence='\ud800.json'))
    raw = json.loads((tmp_path / 'coverage.json').read_text())
    raw['files']['api.py']['contexts']['2'] = ['\ud800']
    (tmp_path / 'coverage.json').write_text(json.dumps(raw))
    with pytest.raises(impact.ImpactInputError, match='UTF-8'):
        observed_tests.import_report(tmp_path, 'snapshot.json', 'coverage.json')


def test_class_structural_change_is_not_discarded_as_method_only():
    import ast

    from amu.change_analysis import analyze_python_change
    old = 'class Thing:\n    setting = 1\n    def result(self):\n        return self.setting\n'
    new = old.replace('setting = 1', 'setting = 2')
    changes = analyze_python_change('api.py', old, new)['modified_entities']
    aggregate = observed_tests.aggregate_class_changes({'api.py': {'tree': ast.parse(old)}}, {'api.py': ast.parse(new)}, changes)
    assert 'api.py#Thing' not in aggregate
