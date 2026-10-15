"""Baseline: pytest-testmon's selected tests beside AMU's candidate set, scored by recall of the ground-truth tests.

Usage: testmon_baseline.py SUITE [SUITE ...] --python PYTHON --out OUT.json --scratch DIR
SUITE is werkzeug (werkzeug-v2.json), holdout2 (requests and itsdangerous) or holdout3 (Click). Run once.

Per case with at least one ground-truth test that existed before the commit (`existing_truth`; a test the commit
adds cannot be selected by anyone):
1. a scratch copy of the parent tree;
2. testmon's baseline: the whole suite once under `pytest --testmon` (coverage's C tracer; Python 3.14's default
   sys.monitoring core cannot record the dynamic contexts testmon needs);
3. the commit's production patch applied;
4. testmon's selection: `pytest --testmon --collect-only`, read as test functions (parameters dropped);
5. AMU's candidate set: what bare `amu` would execute on a git working copy of the parent tree with the patch applied
   and uncommitted, which is what the hook runs (shadow execution stubbed out, nothing run).
Recall = ground-truth tests in the set / ground-truth tests that existed before AND passed in testmon's baseline run. A
truth test that fails or errors in this environment would be re-selected by testmon as a failing test and scored as a
hit whatever the change; it is left out of both tools' scores and listed. A baseline with collection errors is flagged.
Selection sizes are reported too: selecting the whole suite has recall 1 and saves nothing.

Projects that mark tests out with `-m` in addopts (Click: -m 'not stress') make testmon switch selection off; the
same tests are deselected instead by a small plugin, which also deselects Werkzeug's live dev-server tests and registers
the pytest-timeout mark they use (pytest-timeout is not installed). Every run is in its own process group, killed whole
on timeout.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT/'evaluation/before-tests'
MANIFESTS = {'werkzeug': 'werkzeug-v2.json', 'holdout2': 'holdout2.json', 'holdout3': 'holdout3.json'}
PLUGIN = '''import pytest

# Deselected as the projects' own configuration or CI does, without -m (which switches testmon's selection off):
# Click's high-iteration stress tests, and Werkzeug's tests that start a live dev server (they rely on pytest-timeout,
# which is not installed here, and can hang).
DESELECT = ('stress', 'dev_server')


def pytest_configure(config):
    # Werkzeug marks tests with pytest-timeout's mark; registered so filterwarnings=error does not abort collection.
    config.addinivalue_line('markers', 'timeout: pytest-timeout mark, registered only')


def pytest_collection_modifyitems(config, items):
    keep, drop = [], []
    for item in items:
        (drop if any(item.get_closest_marker(name) for name in DESELECT) else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
'''


def function_id(node):
    return re.sub(r'\[.*\]$', '', node.strip())


def run(command, cwd, env, timeout):
    """Run in a process group of its own, output to a file. On timeout the whole group is killed: a dev server a test
    started would otherwise hold the output pipe open and the wait would never end."""
    started = time.time()
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        out.seek(0)
        text = out.read().decode(errors='replace')
    return subprocess.CompletedProcess(command, proc.returncode, text, ''), round(time.time() - started, 1)


def amu_candidates(parent, patch_file, work, python):
    """The ids bare amu would execute on a git working copy of ``parent`` with the patch applied and uncommitted:
    shadow execution is replaced by a recorder."""
    sys.path.insert(0, str(ROOT))
    from amu.core import analyze, shadow
    tree = work/'amu-tree'
    shutil.copytree(parent, tree, ignore=shutil.ignore_patterns('.amu', '.testmondata', '__pycache__', '.pytest_cache', '.git'))
    git = ['git', '-C', str(tree), '-c', 'user.email=b@example.invalid', '-c', 'user.name=b']
    for args in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'parent']):
        subprocess.run([*git, *args], check=True, capture_output=True)
    with open(patch_file, 'rb') as handle:
        subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, check=True, capture_output=True)
    seen = []

    def record(root, edits, ids, python=None, budget=60, runner=None, memo=None):
        seen.extend(ids)
        return [], dict(ran=0, seconds=0.0, runner='recorded'), []
    real, shadow.run = shadow.run, record
    try:
        analyze(tree, None, index_dir=work/'index', run=True, python=python)
    finally:
        shadow.run = real
    return list(dict.fromkeys(seen))


def baseline_outcomes(report):
    """{path::Class::test: passed|failed|skipped} per test function from the baseline's junit report."""
    found, order = {}, {'failed': 3, 'passed': 2, 'skipped': 1}
    try:
        cases = list(ET.parse(report).getroot().iter('testcase'))
    except (OSError, ET.ParseError):
        return found
    for case in cases:
        parts = case.get('classname', '').split('.')
        for cut in range(len(parts), 0, -1):
            if (Path(report).parent/'tree'/('/'.join(parts[:cut]) + '.py')).is_file():
                break
        else:
            continue
        ident = '::'.join(['/'.join(parts[:cut]) + '.py', *parts[cut:], case.get('name', '').split('[')[0]])
        state = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
        state = 'failed' if state in ('failure', 'error') else state
        if order[state] > order.get(found.get(ident), 0):
            found[ident] = state
    return found


def case(row, python, scratch):
    work = scratch/row['case_id']
    shutil.rmtree(work, ignore_errors=True)
    tree = work/'tree'
    shutil.copytree(row['tree'], tree, ignore=shutil.ignore_patterns('.amu', '.testmondata', '__pycache__', '.pytest_cache'))
    (work/'plugins').mkdir()
    (work/'plugins'/'amu_nostress.py').write_text(PLUGIN)
    env = dict(PATH='/usr/bin:/bin', HOME=str(work/'home'), LANG='en_US.UTF-8', COVERAGE_CORE='ctrace', PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=':'.join([str(tree/'src'), str(tree), str(work/'plugins')]))
    (work/'home').mkdir()
    # testmon reads pytest's --lf option, so the cache plugin stays on, with its directory in the scratch copy.
    base = [python, '-m', 'pytest', '--testmon', '-o', f'cache_dir={work/"cache"}', '-p', 'amu_nostress', '-o', 'addopts=', '-q',
            '--continue-on-collection-errors']
    baseline, baseline_seconds = run([*base, f'--junitxml={work/"baseline.xml"}'], tree, env, 3600)
    tail = (baseline.stdout + baseline.stderr).strip().splitlines()[-1:] or ['']
    outcomes = baseline_outcomes(work/'baseline.xml')
    if 'INTERNALERROR' in baseline.stdout + baseline.stderr or not re.search(r'\d+ passed', tail[0]) or not outcomes:
        raise SystemExit(f"{row['case_id']}: testmon baseline did not complete: {(baseline.stdout + baseline.stderr)[-600:]}")
    with open(row['patch'], 'rb') as handle:
        applied = subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=tree, stdin=handle, capture_output=True)
    assert applied.returncode == 0, applied.stderr
    selection, select_seconds = run([*base, '--collect-only'], tree, env, 600)
    output = selection.stdout + selection.stderr
    if 'selection automatically deactivated' in output:
        raise SystemExit(f"{row['case_id']}: testmon switched its selection off: {output[:400]}")
    selected = sorted({function_id(line) for line in selection.stdout.splitlines() if '::' in line and not line.startswith(' ')})
    collected = re.search(r'(\d+)(?:/(\d+))? tests? collected', output)
    candidates = amu_candidates(row['tree'], row['patch'], work, python)
    # Scored over truth tests that passed in testmon's baseline; the rest are listed, not scored.
    truth = sorted(t for t in row['existing_truth'] if outcomes.get(t) == 'passed')
    unscored = {t: outcomes.get(t, 'not collected') for t in row['existing_truth'] if outcomes.get(t) != 'passed'}
    if not truth:
        return dict(case_id=row['case_id'], existing_truth=sorted(row['existing_truth']), unscored=unscored, scored=False,
                    baseline_result=tail[0][:200], baseline_seconds=baseline_seconds)
    def recall(chosen):
        return round(len(set(truth) & set(chosen)) / len(truth), 3)
    return dict(case_id=row['case_id'], existing_truth=truth, unscored=unscored, scored=True,
                collection_errors='error' in tail[0],
                testmon=dict(selected=len(selected), recall=recall(selected), hits=sorted(set(truth) & set(selected)),
                             collected_line=collected[0] if collected else None, baseline_seconds=baseline_seconds,
                             baseline_result=tail[0][:200], select_seconds=select_seconds),
                amu=dict(candidates=len(candidates), recall=recall(candidates), hits=sorted(set(truth) & set(candidates))))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('suites', nargs='+', choices=sorted(MANIFESTS))
    p.add_argument('--python', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--scratch', required=True)
    p.add_argument('--only', nargs='*', help='case ids, for a dry run')
    a = p.parse_args()
    out, scratch = Path(a.out), Path(a.scratch)
    rows = json.loads(out.read_text()) if out.exists() else []
    done = {r['case_id'] for r in rows}
    version = subprocess.run([a.python, '-m', 'pip', 'show', 'pytest-testmon'], capture_output=True, text=True).stdout
    for suite in a.suites:
        manifest = DATA/MANIFESTS[suite]
        for row in json.loads(manifest.read_text()):
            if not row['existing_truth'] or row['case_id'] in done or (a.only and row['case_id'] not in a.only):
                continue
            assert hashlib.sha256(Path(row['patch']).read_bytes()).hexdigest() == row['patch_sha256']
            result = dict(suite=suite, testmon_version=re.search(r'Version: (\S+)', version)[1], **case(row, a.python, scratch))
            rows.append(result)
            out.write_text(json.dumps(rows, indent=1) + '\n')
            print(json.dumps({k: result[k] for k in ('suite', 'case_id', 'testmon', 'amu')}), flush=True)
