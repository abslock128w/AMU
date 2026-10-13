"""Optional integration replay of the preserved real patch parents, source only."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from amu.services import impact


@pytest.mark.parametrize('name,production,verification,candidates', [
    ('callback', 8, 35, 15), ('git-enumeration', 13, 34, 4),
])
def test_archived_patch_roles_and_candidates(name, production, verification, candidates):
    location = os.environ.get('AMU_REAL_PATCH_INPUTS')
    if not location:
        pytest.skip('requires explicitly selected preserved static patch inputs')
    case = Path(location) / name
    identity = json.loads((case / 'identity.json').read_text())
    patch = (case / 'patch.diff').read_bytes()
    assert hashlib.sha256(patch).hexdigest() == identity['patch_sha256']
    request = impact.PatchRequest(patch.decode(), base='worktree')
    packet = impact.execute_patch(str(case / 'repository'), request)
    counts = packet['summary']['obligation_counts']
    assert counts == {'production': production, 'verification': verification, 'documentation': 0, 'uncertain': 0}
    assert all(item['role'] == 'production' for item in packet['summary']['obligations'])
    assert packet['assessment']['counts']['recommended_checks'] == candidates
    assert not any(item['kind'] == 'behavioral-evidence' for item in packet['summary']['existing_checks'])
    detail = impact.execute_patch(str(case / 'repository'), replace(request, section='obligations', page_size=1))
    assert detail['report_hash'] == packet['report_hash']
    assert packet['summary']['verification_changes']['obligation_count'] == verification
    for path, digest in json.loads((case / 'source-sha256.json').read_text()).items():
        assert hashlib.sha256((case / 'repository' / path).read_bytes()).hexdigest() == digest
