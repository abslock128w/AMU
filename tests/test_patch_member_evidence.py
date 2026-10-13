"""Recommendation specificity, using source inspection only."""
import base64
import difflib
import hashlib
import json
from dataclasses import replace

import pytest

from amu.services.impact import PatchRequest, execute_patch

SOURCE = '''class Quota:
    ceiling = 8
    floor = 2

    @classmethod
    def upper(cls):
        return cls.ceiling

    @classmethod
    def lower(cls):
        return cls.floor

    @classmethod
    def label(cls):
        return "quota"
'''
TESTS = '''from limits import Quota

def test_upper():
    assert Quota.upper() == 8

def test_lower():
    assert Quota.lower() == 2

def test_label():
    assert Quota.label() == "quota"

def test_class_only():
    assert Quota is not None

def test_alias():
    receiver = Quota
    assert receiver.upper() == 8
'''


def setup_case(tmp_path, source=SOURCE, tests=TESTS, after=None):
    (tmp_path / 'limits.py').write_text(source)
    (tmp_path / 'test_limits.py').write_text(tests)
    patch = ''.join(difflib.unified_diff(source.splitlines(True),
                                       (after or source.replace('ceiling = 8', 'ceiling = 9')).splitlines(True),
                                       fromfile='a/limits.py', tofile='b/limits.py'))
    return PatchRequest(patch, base='worktree', section='recommended_checks', page_size=1)


def retrieve(tmp_path, request, section):
    rows, fragments, cursor, packets = [], {}, None, []
    while True:
        packet = execute_patch(str(tmp_path), replace(request, section=section, cursor=cursor))
        packets.append(packet)
        for item in packet['details']['items']:
            if 'evidence_fragment' in item:
                f = item['evidence_fragment']
                fragments.setdefault(f['item_sha256'], []).append(f)
            else:
                rows.append(item)
        cursor = packet['page']['next_cursor']
        if cursor is None:
            break
    for sha, parts in fragments.items():
        parts.sort(key=lambda p: p['fragment_index'])
        assert len(parts) == parts[0]['fragment_count']
        data = b''.join(base64.b64decode(p['data']) for p in parts)
        assert hashlib.sha256(data).hexdigest() == sha
        rows.append(json.loads(data))
    return rows, packets


def named(rows, name):
    return next(row for row in rows if row['test_id'] == 'test_limits.py::test_' + name)


def test_member_path_and_broad_candidates_are_distinct(tmp_path):
    request = setup_case(tmp_path)
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assert len(rows) == 5  # Broad candidates stay in the same recommendation denominator.
    upper = named(rows, 'upper')
    assoc = next(a for a in upper['associations'] if a['entity_id'] == 'limits.py#Quota.ceiling')
    assert assoc['relationship'] == 'source-member-path'
    assert assoc['resolution'] == 'resolved'
    assert [step['role'] for step in assoc['path']] == ['test_reference', 'source_declaration', 'state_access']
    assert assoc['path'][1]['entity_id'] == 'limits.py#Quota.upper'
    assert assoc['path'][2]['location']['line'] == 7
    assert assoc['path'][2]['phase'] == 'before'
    assert upper['coverage_proven'] is False
    for name in ('label', 'class_only', 'alias', 'lower'):
        row = named(rows, name)
        assert 'limits.py#Quota.ceiling' not in row['entities']
        assert not any(a['entity_id'] == 'limits.py#Quota.ceiling' for a in row['associations'])
        broad = next(a for a in row['associations'] if a['relationship'] == 'broad-class-reference')
        assert broad['entity_id'] == 'limits.py#Quota'
        assert broad['resolution'] == 'unresolved'
        assert 'absence' in broad['limitation']


def test_multiple_members_keep_distinct_consumers(tmp_path):
    request = setup_case(tmp_path, after=SOURCE.replace('ceiling = 8', 'ceiling = 9').replace('floor = 2', 'floor = 3'))
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assert 'limits.py#Quota.ceiling' in named(rows, 'upper')['entities']
    assert 'limits.py#Quota.floor' not in named(rows, 'upper')['entities']
    assert 'limits.py#Quota.floor' in named(rows, 'lower')['entities']
    assert 'limits.py#Quota.ceiling' not in named(rows, 'lower')['entities']


def test_summary_findings_and_pages_agree(tmp_path):
    request = setup_case(tmp_path)
    rows, packets = retrieve(tmp_path, request, 'recommended_checks')
    findings, finding_packets = retrieve(tmp_path, request, 'findings')
    assert len({p['report_hash'] for p in [*packets, *finding_packets]}) == 1
    assert packets[0]['assessment']['counts']['recommended_checks'] == len(rows)
    ids = [row['test_id'] for row in rows]
    assert ids == sorted(ids)
    assert packets[0]['assessment']['recommended_test_ids'] == ids[:3]
    preview = packets[0]['assessment']['recommendation_relationships']
    assert [row['test_id'] for row in preview] == ids[:3]
    for summary, row in zip(preview, rows):
        assert summary['relationships'] == sorted({a['relationship'] for a in row['associations']})
    state = next(f for f in findings if f['entity_id'] == 'limits.py#Quota.ceiling')
    assert state['recommended_test_ids'] == ['test_limits.py::test_upper']
    assert 'test_limits.py::test_label' in state['broad_class_test_ids']
    assert 'test_limits.py::test_class_only' in state['broad_class_test_ids']
    for f in findings:
        assert all(f['entity_id'] in named(rows, tid.split('::test_')[1])['entities'] for tid in f['recommended_test_ids'])


@pytest.mark.parametrize('change', [
    lambda s: s.replace('class Quota:', 'class Quota(object):'),
    lambda s: s.replace('    @classmethod\n    def upper(cls):', '    @unknown\n    def upper(cls):'),
    lambda s: s.replace('        return cls.ceiling', '        cls = object()\n        return cls.ceiling'),
])
def test_ambiguous_dispatch_never_becomes_a_member_path(tmp_path, change):
    request = setup_case(tmp_path, source=change(SOURCE))
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assert len(rows) == 5
    assert 'limits.py#Quota.ceiling' not in named(rows, 'upper')['entities']
    assert not any(a['relationship'] == 'source-member-path' for a in named(rows, 'upper')['associations'])


def test_instance_receiver_remains_unresolved_without_new_dispatch_analysis(tmp_path):
    source = SOURCE.replace('@classmethod\n    def upper(cls)', 'def upper(self)').replace('cls.ceiling', 'self.ceiling')
    tests = TESTS.replace('Quota.upper()', 'Quota().upper()')
    request = setup_case(tmp_path, source=source, tests=tests)
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    row = named(rows, 'upper')
    assert 'limits.py#Quota' in row['entities']
    assert 'limits.py#Quota.ceiling' not in row['entities']
    assert all(a['resolution'] == 'unresolved' for a in row['associations'])


def test_direct_member_reference_has_no_sibling_member_association(tmp_path):
    tests = 'from limits import Quota\n\ndef test_upper():\n    assert Quota.ceiling == 8\n'
    request = setup_case(tmp_path, tests=tests, after=SOURCE.replace('ceiling = 8', 'ceiling = 9').replace('floor = 2', 'floor = 3'))
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    row = rows[0]
    assert 'limits.py#Quota.ceiling' in row['entities']
    assert 'limits.py#Quota.floor' not in row['entities']
    assoc = next(a for a in row['associations'] if a['entity_id'] == 'limits.py#Quota.ceiling')
    assert assoc['resolution'] == 'resolved'
    assert assoc['path'][1]['location']['line'] == 2


def test_loading_a_method_object_does_not_join_its_state_access(tmp_path):
    tests = 'from limits import Quota\n\ndef test_upper():\n    assert callable(Quota.upper)\n'
    request = setup_case(tmp_path, tests=tests)
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assert 'limits.py#Quota.upper' in rows[0]['entities']
    assert 'limits.py#Quota.ceiling' not in rows[0]['entities']


def test_import_alias_retains_a_located_write_path(tmp_path):
    source = SOURCE.replace('return cls.ceiling', 'cls.ceiling = 12\n        return None')
    tests = 'from limits import Quota as Budget\n\ndef test_upper():\n    Budget.upper()\n'
    request = setup_case(tmp_path, source=source, tests=tests)
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assoc = next(a for a in rows[0]['associations'] if a['entity_id'] == 'limits.py#Quota.ceiling')
    assert assoc['path'][0]['reference'] == 'Budget.upper'
    assert assoc['path'][0]['import_line'] == 1
    assert assoc['path'][-1]['access'] == 'write'


def test_rebound_source_member_cannot_supply_a_path(tmp_path):
    source = SOURCE + '    upper = lambda: 10\n'
    request = setup_case(tmp_path, source=source)
    rows, _ = retrieve(tmp_path, request, 'recommended_checks')
    assert 'limits.py#Quota.ceiling' not in named(rows, 'upper')['entities']
    assert 'limits.py#Quota.upper' not in named(rows, 'upper')['entities']


def test_member_path_fragments_reconstruct_without_changing_summary(tmp_path, monkeypatch):
    from amu.services import impact_report

    monkeypatch.setattr(impact_report, 'MAX_NORMAL_ITEM_BYTES', 256)
    request = setup_case(tmp_path)
    rows, packets = retrieve(tmp_path, request, 'recommended_checks')
    assert packets[0]['retrieval']['recommended_checks']['fragmented_items'] > 0
    assert {row['test_id'] for row in rows} == {f'test_limits.py::test_{name}' for name in ('upper', 'lower', 'label', 'class_only', 'alias')}
    assert 'limits.py#Quota.ceiling' in named(rows, 'upper')['entities']
    assert 'limits.py#Quota.ceiling' not in named(rows, 'label')['entities']


def test_missing_declaration_does_not_guess_a_containing_class():
    import ast

    from amu.plan import patch_test_recommendations

    source = 'from limits import outer\n\ndef test_outer():\n    assert outer\n'
    rows = patch_test_recommendations(
        [{'path': 'test_limits.py', 'tree': ast.parse(source), 'sha256': hashlib.sha256(source.encode()).hexdigest()}],
        {'limits.py': {'outer.inner'}}, declarations={})
    assert len(rows) == 1
    assert rows[0]['entities'] == ['limits.py#outer']
    assert rows[0]['associations'][0]['relationship'] == 'broad-source-reference'
    assert rows[0]['associations'][0]['resolution'] == 'unresolved'
