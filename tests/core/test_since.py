"""``amu --since REF``: bare amu on the commits since REF and what is not committed yet."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(repo, *args):
    return subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t', *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def amu(repo, *args):
    done = subprocess.run([sys.executable, '-m', 'amu.cli', *args], cwd=repo, capture_output=True, text=True, timeout=300,
                          env=dict(os.environ, PYTHONPATH=str(ROOT)))
    return json.loads(done.stdout) if done.stdout.strip().startswith('{') else done


def test_since_runs_the_commits_since_a_ref_that_bare_amu_no_longer_sees(tmp_path):
    repo = tmp_path/'repo'
    (repo/'tests').mkdir(parents=True)
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (repo/'tests'/'test_calc.py').write_text('from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n')
    (repo/'pytest.ini').write_text('[pytest]\n')
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'base')
    git(repo, 'checkout', '-qb', 'feature')
    (repo/'calc.py').write_text('def add(a, b):\n    return a - b\n')
    git(repo, 'commit', '-qam', 'flip')
    report = amu(repo, '--since', 'main')
    assert isinstance(report, dict), report
    assert report['flips'] == 1 and report['predicted_failures'] == ['tests/test_calc.py::test_add'], report
    # Uncommitted work on top counts too.
    (repo/'calc.py').write_text('def add(a, b):\n    return a + b + 0\n')
    assert amu(repo, '--since', 'main')['flips'] == 0
    assert any('unknown-ref' in gap for gap in amu(repo, '--since', 'unknown-ref')['gaps'])
