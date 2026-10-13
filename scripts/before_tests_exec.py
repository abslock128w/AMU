"""Execution-backed evaluation. Runs upstream tests in disposable copies; needs the owner's approval per repository.

For one frozen case it (1) records per-test coverage on the parent tree, (2) reruns the same
tests with the production patch applied, and (3) scores the static and the coverage-assisted
ranking against the existing tests whose outcome the patch changed.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT/'evaluation/before-tests'
EXEC = DATA/'exec'


def environment(tree, home, coverage_file=None):
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'PYTHONPATH': str(tree/'src'),
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'COVERAGE_CORE': 'ctrace'}
    if coverage_file:
        env['COVERAGE_FILE'] = str(coverage_file)
    return env


def outcomes(tree, report):
    """{test function id: sorted outcomes of its parameter instances}."""
    found = {}
    for case in ET.parse(report).getroot().iter('testcase'):
        parts = case.get('classname', '').split('.')
        for cut in range(len(parts), 0, -1):
            if (tree/('/'.join(parts[:cut])+'.py')).is_file():
                break
        else:
            continue
        ident = '::'.join(['/'.join(parts[:cut])+'.py', *parts[cut:], case.get('name', '').split('[')[0]])
        state = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
        found.setdefault(ident, []).append((case.get('name'), state))
    return {ident: sorted(states) for ident, states in found.items()}


def pytest(python, tree, tests, report, package=None, coverage_file=None, timeout=900, extra=()):
    command = [python, '-B', '-m', 'pytest', *tests, *extra, '-q', '-p', 'no:cacheprovider', '--junitxml='+str(report),
               '-o', 'addopts=', '-W', 'ignore']
    if coverage_file:
        command += ['-p', 'pytest_cov.plugin', '--cov='+package, '--cov-context=test', '--cov-report=']
    home = tree.parent/'home'
    home.mkdir(exist_ok=True)
    start = time.perf_counter()
    done = subprocess.run(command, cwd=tree, env=environment(tree, home, coverage_file), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    tail = done.stdout.decode(errors='replace').strip().splitlines()[-1:] or ['']
    return dict(command=command, exit=done.returncode, seconds=round(time.perf_counter()-start, 1), summary=tail[0][:200])


def run_case(row, python, tests, package, extra=()):
    work = EXEC/row['case_id']
    base, patched = work/'base', work/'patched'
    runs = work/'runs.json'
    if not runs.exists():
        shutil.rmtree(work, ignore_errors=True)
        for tree in (base, patched):
            shutil.copytree(row['tree'], tree, ignore=shutil.ignore_patterns('.git', '.amu', '__pycache__'))
        # patch(1), not git apply: inside another repository's subdirectory git apply succeeds and changes nothing.
        with open(row['patch'], 'rb') as handle:
            applied = subprocess.run(['/usr/bin/patch', '-p1', '--batch', '-s'], cwd=patched, stdin=handle, capture_output=True)
        touched = [line[6:].strip() for line in Path(row['patch']).read_text().splitlines() if line.startswith('+++ b/')]
        differs = any(not (base/name).exists() or (patched/name).read_bytes() != (base/name).read_bytes()
                      for name in touched if (patched/name).exists())
        ok = applied.returncode == 0 and differs
        record = pytest(python, base, tests, work/'base.xml', package, base/'.coverage', extra=extra)
        after = pytest(python, patched, tests, work/'patched.xml', extra=extra) if ok else None
        runs.write_text(json.dumps(dict(record=record, patched=after, patch_applied=ok)))
        # Outcomes need the patched tree's file layout only once.
        (work/'patched-outcomes.json').write_text(json.dumps(outcomes(patched, work/'patched.xml') if after and (work/'patched.xml').exists() else {}))
        shutil.rmtree(patched, ignore_errors=True)
    saved = json.loads(runs.read_text())
    record, after = saved['record'], saved['patched']
    applied = subprocess.CompletedProcess([], 0 if saved['patch_applied'] else 1)
    before_states = outcomes(base, work/'base.xml') if (work/'base.xml').exists() else {}
    after_states = json.loads((work/'patched-outcomes.json').read_text())
    changed = sorted(ident for ident in before_states if after_states and before_states[ident] != [tuple(x) for x in after_states.get(ident, [])])
    patch = Path(row['patch']).read_text()
    sys.path.insert(0, str(ROOT))
    from amu.core import analyze
    scored = {}
    for label, evidence in (('static', str(work/'no-coverage')), ('coverage', '.coverage')):
        packet = analyze(base, patch, coverage_evidence=evidence, index_dir=work/'index', include_tests=True)
        top = [t['id'] for t in packet['tests']][:10]
        scored[label] = dict(top10=top, observed=sum(t['confidence'] == 'observed' for t in packet['tests']),
                             bytes=len(json.dumps(packet, separators=(',', ':')).encode()),
                             gaps=[g for g in packet['gaps'] if 'overage' in g or 'recorded' in g])
    result = dict(case_id=row['case_id'], record=record, patched=after, patch_applied=applied.returncode == 0,
                  tests_run=len(before_states), outcome_changed=changed, commit_truth=row['existing_truth'], ranking=scored)
    (work/'result.json').write_text(json.dumps(result, indent=1)+'\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', required=True)
    p.add_argument('--python', required=True, help='interpreter with pytest, pytest-cov and coverage')
    p.add_argument('--package', required=True)
    p.add_argument('--tests', nargs='+', default=['tests'])
    p.add_argument('--cases', nargs='*')
    p.add_argument('--ignore', nargs='*', default=[], help='reviewed test files to leave out, for example stress suites')
    a = p.parse_args()
    for row in json.loads((DATA/a.manifest).read_text()):
        if a.cases and row['case_id'] not in a.cases:
            continue
        try:
            r = run_case(row, a.python, a.tests, a.package, ['--ignore='+name for name in a.ignore])
        except subprocess.TimeoutExpired:
            print(json.dumps(dict(case_id=row['case_id'], error='timeout')), flush=True)
            continue
        print(json.dumps(dict(case_id=r['case_id'], record=r['record']['summary'], patched=(r['patched'] or {}).get('summary'),
                              outcome_changed=len(r['outcome_changed']), commit_truth=len(r['commit_truth']),
                              **{k+'_hits_outcome': len(set(v['top10']) & set(r['outcome_changed'])) for k, v in r['ranking'].items()},
                              **{k+'_hits_commit': len(set(v['top10']) & set(r['commit_truth'])) for k, v in r['ranking'].items()})), flush=True)
