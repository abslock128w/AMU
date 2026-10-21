"""``amu pre-commit``: the summary of the staged change for a pre-commit hook, which never blocks the commit."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def amu(where, *args):
    return subprocess.run([sys.executable, '-m', 'amu.cli', 'pre-commit', *args], cwd=where, capture_output=True, text=True,
                          timeout=300, env=dict(os.environ, PYTHONPATH=str(ROOT)))


def repository(tmp_path):
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (repo/'tests'/'test_calc.py').write_text('from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n')
    (repo/'pytest.ini').write_text('[pytest]\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    return repo


def test_a_staged_change_that_breaks_a_test_is_reported_and_the_commit_goes_on(tmp_path):
    repo = repository(tmp_path)
    done = amu(repo)
    assert done.returncode == 0 and 'amu: nothing staged' in done.stdout, done
    (repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    git(repo, 'add', 'calc.py')
    done = amu(repo)
    assert done.returncode == 0, done
    assert done.stdout.startswith('amu: EXECUTED') and '  flip: tests/test_calc.py::test_add' in done.stdout, done.stdout


def test_install_writes_a_hook_that_cannot_block_and_leaves_an_existing_one_alone(tmp_path):
    repo = repository(tmp_path)
    done = amu(repo, '--install')
    hook = repo/'.git'/'hooks'/'pre-commit'
    assert done.returncode == 0 and hook.exists() and os.access(hook, os.X_OK), done
    assert 'amu pre-commit || true' in hook.read_text() and hook.read_text().rstrip().endswith('exit 0')
    hook.write_text('#!/bin/sh\necho mine\n')
    assert 'exists' in amu(repo, '--install').stdout and hook.read_text() == '#!/bin/sh\necho mine\n'


def test_outside_a_repository_it_still_exits_zero(tmp_path):
    done = amu(tmp_path)
    assert done.returncode == 0, done
