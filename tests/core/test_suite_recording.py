"""Whole-suite recordings made outside the pull request job (core.suite, ci.record_suite), read by the pull request job
through the diff since their commit. Synthetic projects only."""
import difflib
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from amu.core import analyze, evidence, suite
from amu.core.index import cache_dir
from amu.core.patch import changes

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason='sys.monitoring needs Python 3.12')

IMPL = textwrap.dedent('''\
    def shape(value):
        if isinstance(value, list):
            return [shape(v) for v in value]
        return type(value).__name__


    def other():
        return 2
    ''')
FILES = {
    'lib.py': IMPL,
    'tests/test_shape.py': textwrap.dedent('''\
        from lib import shape


        def test_nested():
            assert shape([1, [2]]) == ["int", ["int"]]


        def test_flat():
            assert shape(1) == "int"
        '''),
    'tests/test_other.py': 'from lib import other\n\n\ndef test_unrelated():\n    assert other() == 2\n',
}
BROKEN = IMPL.replace('return [shape(v) for v in value]', 'return [shape(v) for v in value][::-1]')


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def project(root, files=FILES):
    for name, text in files.items():
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        (root/name).write_text(text)
    git(root, 'init', '-q')
    commit(root, 'base')


def commit(root, message):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD')


def record_here(root):
    from amu.ci import record_suite
    assert record_suite.main(['--repo', str(root), '--out', str(root/'.amu/suite/{commit}.json.gz'),
                              '--python', sys.executable]) == 0
    return git(root, 'rev-parse', 'HEAD')


def not_fitting(root):
    """A memo that says the suite does not fit the budget (as on a large project)."""
    memo = cache_dir(root, root/'.index')/'shadow-memo.json'
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text(json.dumps(dict(key=None, suite_seconds=10_000)))


def change(root, before, after):
    (root/'lib.py').write_text(after)
    diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'a/lib.py', 'b/lib.py'))
    return analyze(root, diff, full_evidence=True, index_dir=root/'.index', run=True, python=sys.executable)


def test_a_recording_names_the_tests_of_each_line(tmp_path):
    project(tmp_path)
    sha = record_here(tmp_path)
    data = suite.read(tmp_path/'.amu/suite'/f'{sha}.json.gz')
    assert data['commit'] == sha and data['complete'] and data['planned'] == 3, {k: data[k] for k in ('planned', 'ran')}
    found = suite.Recording(tmp_path, sha, data, sha)
    assert found.tests('lib.py', IMPL, [3]) == {'tests/test_shape.py::test_nested'}
    assert found.tests('lib.py', IMPL, [2]) == {'tests/test_shape.py::test_nested', 'tests/test_shape.py::test_flat'}
    assert found.tests('lib.py', IMPL, [8]) == {'tests/test_other.py::test_unrelated'}


def test_shards_merge_into_the_same_recording(tmp_path):
    project(tmp_path)
    from amu.ci import record_suite
    for n in (0, 1):
        record_suite.main(['--repo', str(tmp_path), '--shard', f'{n}/2', '--out', str(tmp_path/f'shard-{n}.json.gz'),
                           '--python', sys.executable])
    record_suite.main(['--repo', str(tmp_path), '--merge', str(tmp_path/'shard-0.json.gz'), str(tmp_path/'shard-1.json.gz'),
                       '--out', str(tmp_path/'merged.json.gz')])
    merged = suite.read(tmp_path/'merged.json.gz')
    sha = git(tmp_path, 'rev-parse', 'HEAD')
    found = suite.Recording(tmp_path, sha, merged, sha)
    assert merged['complete'] and merged['planned'] == 3 and merged['shards'] == 2
    assert found.tests('lib.py', IMPL, [2]) == {'tests/test_shape.py::test_nested', 'tests/test_shape.py::test_flat'}


def test_past_the_budget_the_pull_request_job_chooses_from_the_recording_and_records_nothing_itself(tmp_path):
    project(tmp_path)
    sha = record_here(tmp_path)
    not_fitting(tmp_path)
    report = change(tmp_path, IMPL, BROKEN)
    chosen = report['selection_evidence']
    assert chosen['suite']['commit'] == sha, chosen
    assert report['candidate_sources'] == {'tests/test_shape.py::test_nested': 'suite recording',
                                           'tests/test_shape.py::test_flat': 'suite recording'}, report['candidate_sources']
    assert chosen['ran_before'] == 2, chosen  # no whole-suite run on the merge base
    assert [row['id'] for row in report['predicted_failures']] == ['tests/test_shape.py::test_nested'], report['summary']
    assert not chosen.get('static_fallback'), chosen


def test_the_recording_of_an_ancestor_is_read_through_the_diff_since(tmp_path):
    project(tmp_path)
    record_here(tmp_path)
    # Since the recording: lines added above the function, and a new test file that calls it.
    moved = '# a comment\n# another\n\n' + IMPL
    (tmp_path/'lib.py').write_text(moved)
    (tmp_path/'tests/test_new.py').write_text('from lib import shape\n\n\ndef test_list():\n    assert shape([1])\n')
    commit(tmp_path, 'since')
    not_fitting(tmp_path)
    report = change(tmp_path, moved, BROKEN.replace('def shape', '# a comment\n# another\n\ndef shape', 1))
    sources = report['candidate_sources']
    assert sources.get('tests/test_shape.py::test_nested') == 'suite recording', sources
    assert sources.get('tests/test_new.py::test_list') in ('test file changed since the recording', 'static graph'), sources
    assert 'tests/test_new.py::test_list' in sources and 'tests/test_other.py::test_unrelated' not in sources, sources


def test_without_a_recording_the_first_line_says_selection_was_static():
    from amu.ci.render import headline
    report = dict(hunks=[dict(path='lib.py', line=3, state='executed-both-sides', tests=2)],
                  selection_evidence=dict(mode='coverage', static_fallback=True))
    assert 'no suite recording for base; selected statically' in headline(report, 'a' * 40, 5)


class Runner:
    name, parallel = 'pytest', False

    def cwd(self, tree):
        return tree


def test_tests_a_cut_off_recording_saw_are_candidates(tmp_path):
    # The merge base's own recording stops at its budget after the first test; that test ran the hunk, so it is a
    # candidate though the recording is incomplete. The static graph adds the rest, as before.
    project(tmp_path)
    after = BROKEN
    diff = ''.join(difflib.unified_diff(IMPL.splitlines(True), after.splitlines(True), 'a/lib.py', 'b/lib.py'))
    edits, _ = changes(tmp_path, diff)

    def execute(runner, python, tree, ids, scratch, name, timeout, parts, env=None):
        out = Path(json.loads(Path(env['AMU_REC_SPEC']).read_text())['out'])
        with open(out/'1.jsonl', 'a') as handle:
            for ident in [i for i in ids if 'shape' in i]:
                handle.write(json.dumps(dict(k='cov', t=ident, f={'lib.py': [3]})) + '\n')
        time.sleep(1.2)
        return {i: 'passed' for i in ids}, []

    everything = ['tests/test_shape.py::test_nested', 'tests/test_shape.py::test_flat', 'tests/test_other.py::test_unrelated']
    plan = dict(static=['tests/test_shape.py::test_flat'], everything=everything, own=[], key=None, suite=None)
    trees = dict(before=tmp_path, after=tmp_path)
    found = evidence.run(execute, Runner(), sys.executable, trees, edits, {'lib.py': IMPL}, {'lib.py': after}, plan,
                         tmp_path/'scratch', 1.5, None, 1, 1, {'suite_seconds': 10_000}, None)
    assert not found['base_complete'], found
    assert found['sources'].get('tests/test_shape.py::test_flat') == evidence.CUT, found['sources']
    assert found['static_fallback'], found
