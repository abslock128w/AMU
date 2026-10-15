import json
import subprocess
import sys
from pathlib import Path

import pytest

from amu.ci import run as ci_run

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pull_request(tmp_path, monkeypatch):
    """A repository with main and a pull request branch that turns one test red."""
    monkeypatch.setenv('PYTHONPATH', str(ROOT))
    monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (repo/'tests'/'test_calc.py').write_text('from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\n'
                                             'def test_other():\n    assert True\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-qb', 'feature')
    (repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    git(repo, 'commit', '-qam', 'flip')
    return repo, base, git(repo, 'rev-parse', 'HEAD')


def test_flip_against_merge_base_is_saved(pull_request, tmp_path):
    repo, base, head = pull_request
    # A commit that lands on main after the branch point stays out of the diff.
    git(repo, 'checkout', '-q', 'main')
    (repo/'other.py').write_text('x = 1\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'later on main')
    later = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'feature')
    report, meta = ci_run.run(repo, later, head, tmp_path/'out', python=sys.executable, pr=7)
    assert report['flips'] == 1
    assert [f['id'] if isinstance(f, dict) else f for f in report['predicted_failures']] == ['tests/test_calc.py::test_add']
    assert meta['merge_base'] == base and meta['status'] == 'ran' and meta['pr'] == 7
    assert 'other.py' not in (tmp_path/'out'/'patch.diff').read_text()
    saved = json.loads((tmp_path/'out'/'report.json').read_text())
    assert saved['flips'] == 1


def test_checkout_not_at_head_runs_nothing(pull_request, tmp_path):
    repo, base, head = pull_request
    git(repo, 'checkout', '-q', 'main')
    report, meta = ci_run.run(repo, base, head, tmp_path/'out', python=sys.executable)
    assert 'not the pull request head' in report['error'] and meta['status'] == 'error'


def test_missing_base_is_an_error_not_a_crash(pull_request, tmp_path):
    repo, _, head = pull_request
    report, _ = ci_run.run(repo, 'f' * 40, head, tmp_path/'out', python=sys.executable)
    assert 'no merge base' in report['error']


def test_main_always_exits_zero_and_writes_outputs(pull_request, tmp_path, monkeypatch):
    repo, base, head = pull_request
    outputs = tmp_path/'github-output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(outputs))
    monkeypatch.chdir(repo)
    assert ci_run.main(['--base', base, '--head', head, '--out', str(tmp_path/'out'), '--python', sys.executable]) == 0
    assert f'merge-base={base}' in outputs.read_text() and 'flips=1' in outputs.read_text()
    assert ci_run.main(['--base', 'nope', '--out', str(tmp_path/'bad')]) == 0
    assert 'error' in json.loads((tmp_path/'bad'/'report.json').read_text())
