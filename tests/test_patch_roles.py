"""Role separation and candidate provenance never turn edits into proof."""

import ast
from dataclasses import replace

import pytest

from amu.patch_roles import file_role
from amu.services import impact
from tests.test_patch_actionable import diff


def test_naming_alone_does_not_classify_verification():
    helper = ast.parse('def helper():\n    return 1\n')
    assert file_role('test_server.py', helper, helper, [], {})['role'] == 'uncertain'
    assert file_role('tests/helper.py', helper, helper, [], {})['role'] == 'uncertain'
    assert file_role('qa/helper.py', helper, helper, [], {'testpaths': ['qa']})['role'] == 'verification'
    tests = [{'path': 'tests/test_core.py', 'tree': ast.parse('def test_ok():\n    assert True\n')}]
    assert file_role('tests/helper.py', helper, helper, tests, {})['role'] == 'verification'


def _mixed(tmp_path):
    source = 'def score(value):\n    return 1 if value < 0 else 2\n'
    tests = ('from core import score\n\ndef test_unchanged():\n    assert score(0) == 2\n\n'
             'def test_modified():\n    assert score(0) == 2\n')
    changed = tests.replace('def test_modified():\n    assert score(0) == 2',
                            'def test_modified():\n    assert score(0) == 1')
    changed += '\ndef test_added():\n    assert score(0) == 1\n'
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'core.py').write_text(source)
    (tmp_path / 'tests/test_core.py').write_text(tests)
    return impact.PatchRequest(diff(source, source.replace('< 0', '<= 0'), 'core.py') +
                               diff(tests, changed, 'tests/test_core.py'), base='worktree')


def test_mixed_roles_provenance_and_shared_guidance(tmp_path):
    request = _mixed(tmp_path)
    packet = impact.execute_patch(str(tmp_path), request)
    summary = packet['summary']
    assert summary['obligation_counts']['production'] > 0
    assert summary['obligation_counts']['verification'] > 0
    assert all(row['role'] == 'production' for row in summary['obligations'])
    assert summary['verification_changes']['test_status_counts'] == {'added': 1, 'modified': 1, 'removed': 0, 'unchanged': 1}
    assert summary['verification_groups']
    checks = impact.execute_patch(str(tmp_path), replace(request, section='recommended_checks'))['details']['items']
    assert {row['test_id'].split('::')[-1] for row in checks} == {'test_unchanged', 'test_modified'}
    for row in checks:
        provenance = row['test_provenance']
        assert provenance['status'] == ('modified' if row['test_id'].endswith('modified') and not row['test_id'].endswith('unchanged') else 'unchanged')
        assert provenance['before']['assertions'][0]['expression'] == 'score(0) == 2'
        assert not provenance['independent_proof']
    changed = next(row for row in checks if row['test_id'].endswith('test_modified'))
    assert changed['test_provenance']['after']['assertions'][0]['expression'] == 'score(0) == 1'
    changes = impact.execute_patch(str(tmp_path), replace(request, section='changes'))['details']['items']
    added = next(row for row in changes if row['id'].endswith('#test_added'))
    assert added['test_provenance']['status'] == 'added'
    assert added['test_provenance']['before'] is None


def test_test_only_obligations_remain_visible_and_pagination_complete(tmp_path):
    before = 'def test_value():\n    return 1\n'
    after = before.replace('1', '2')
    (tmp_path / 'test_core.py').write_text(before)
    request = impact.PatchRequest(diff(before, after, 'test_core.py'), base='worktree', page_size=1)
    summary = impact.execute_patch(str(tmp_path), request)
    assert summary['summary']['obligations'][0]['role'] == 'verification'
    assert summary['summary']['obligation_counts']['production'] == 0
    for section, retrieval in summary['retrieval'].items():
        rows, cursor = [], None
        while True:
            page = impact.execute_patch(str(tmp_path), replace(request, section=section, cursor=cursor))
            assert page['report_hash'] == summary['report_hash']
            rows.extend(page['details']['items'])
            cursor = page['page']['next_cursor']
            if cursor is None:
                break
        assert len(rows) == retrieval['logical_items']
    assert len(summary['summary']['obligations']) + summary['summary']['omitted_obligations'] == summary['assessment']['counts']['obligations']


def test_role_configuration_changes_invalidate_cursor(tmp_path):
    request = _mixed(tmp_path)
    config = tmp_path / 'pyproject.toml'
    config.write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    first = impact.execute_patch(str(tmp_path), request)
    config.write_text('[tool.pytest.ini_options]\ntestpaths = ["qa"]\n')
    with pytest.raises(impact.ImpactInputError, match='cursor'):
        impact.execute_patch(str(tmp_path), replace(request, section='obligations', cursor=first['retrieval']['obligations']['cursor']))


def test_broad_wrapper_path_is_not_labeled_behavioral(tmp_path):
    source = 'def helper(value):\n    return value + 1\n\ndef public(value):\n    return helper(value)\n'
    tests = 'from core import public\n\ndef test_setup():\n    value = public(1)\n    assert 9 == 9\n'
    (tmp_path / 'core.py').write_text(source)
    (tmp_path / 'test_core.py').write_text(tests)
    packet = impact.execute_patch(str(tmp_path), impact.PatchRequest(
        diff(source, source.replace('value + 1', 'value + 2'), 'core.py'), base='worktree'))
    check = packet['summary']['existing_checks'][0]
    assert check['kind'] == 'supported-source-path'
    assert check['obligation_evidence'][0]['evidence_category'] == 'supported-source-path-only'
    assert 'No behavior-related baseline assertion was established' in packet['summary']['recommendation_search']
    assert 'propagation' in packet['summary']['verification_groups'][0]['unresolved'][0]


def test_documentation_and_ambiguous_file_roles_are_explicit(tmp_path):
    before = 'def helper():\n    return 1\n'
    (tmp_path / 'test_server.py').write_text(before)
    (tmp_path / 'guide.md').write_text('# Old\n')
    patch = diff(before, before.replace('1', '2'), 'test_server.py') + diff('# Old\n', '# New\n', 'guide.md')
    report = impact.execute_patch(str(tmp_path), impact.PatchRequest(patch, base='worktree'))
    summary = report['summary']
    roles = {row['path']: row['source_role'] for row in summary['changed_files']}
    assert roles['test_server.py']['role'] == 'uncertain'
    assert roles['guide.md']['role'] == 'documentation'
    assert all(row['source_role']['basis'] and row['source_role']['uncertainty']
               for row in report['identity']['file_coverage'])
    assert summary['obligation_counts']['uncertain'] > 0


def test_removed_and_added_file_tests_are_not_baseline_proof(tmp_path):
    old = 'def test_old():\n    return 1\n'
    new = 'def test_new():\n    return 2\n'
    (tmp_path / 'test_old.py').write_text(old)
    request = impact.PatchRequest(diff(old, None, 'test_old.py') + diff(None, new, 'test_new.py'), base='worktree')
    packet = impact.execute_patch(str(tmp_path), request)
    assert packet['summary']['verification_changes']['test_status_counts'] == {
        'unchanged': 0, 'modified': 0, 'added': 1, 'removed': 1}
    details = impact.execute_patch(str(tmp_path), replace(request, section='changes'))['details']['items']
    assert {row['test_provenance']['status'] for row in details} == {'added', 'removed'}
    assert not packet['summary']['existing_checks']


def test_role_configuration_symlink_is_not_followed(tmp_path):
    request = _mixed(tmp_path)
    (tmp_path / 'pyproject.toml').symlink_to(tmp_path / 'missing.toml')
    with pytest.raises(impact.ImpactInputError, match='symbolic'):
        impact.execute_patch(str(tmp_path), request)
