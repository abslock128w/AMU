"""Run sympy's own tests in a tree with pytest, under sympy's configuration, and read per-test outcomes.

sympy's own runner (`bin/test`) calls pytest's main with the project's pyproject configuration; this runs the same
pytest with the same configuration, serially, as `bin/test` does by default. Its addopts (`-m 'not slow and not
tooslow'`) apply here too. Outcomes are per test function, path::Class::test as pytest names it, parameters merged:
a function fails if any of its cases fails. Standard library only. The time ledger is djrun.Ledger, shared with the
Django trial.
"""
import os
import re
import signal
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ORDER = {'failed': 3, 'passed': 2, 'skipped': 1}
SUMMARY = re.compile(r'^=+ .*\b(passed|failed|error|errors|skipped|deselected|no tests ran)\b.* in [\d.]+s')


def function_id(tree, classname, name):
    parts = classname.split('.') if classname else []
    for cut in range(len(parts), 0, -1):
        if (Path(tree)/('/'.join(parts[:cut]) + '.py')).is_file():
            return '::'.join(['/'.join(parts[:cut]) + '.py', *parts[cut:], name.split('[')[0]])
    return None


def read_junit(tree, report):
    """({test id: state}, [test files that could not be collected])."""
    found, unloadable = {}, []
    try:
        cases = list(ET.parse(report).getroot().iter('testcase'))
    except (OSError, ET.ParseError):
        return found, unloadable
    for case in cases:
        state = next((child.tag for child in case if child.tag in ('failure', 'error', 'skipped')), 'passed')
        ident = function_id(tree, case.get('classname', ''), case.get('name', ''))
        if ident is None:
            # A collection error: no classname, the name is the module's dotted path.
            module = case.get('name', '').replace('.', '/') + '.py'
            if state == 'error' and (Path(tree)/module).is_file():
                unloadable.append(module)
            continue
        state = 'failed' if state in ('failure', 'error') else state
        if ORDER[state] > ORDER.get(found.get(ident), 0):
            found[ident] = state
    return found, sorted(set(unloadable))


def run(python, tree, labels=(), timeout=3600, home=None, workers=0, extra=()):
    """Run ``labels`` (paths or pytest node ids; the whole default suite when empty). Returns dict(states, output,
    problems, seconds, complete). ``workers`` > 0 uses pytest-xdist (scoring only; agents' runs are serial)."""
    tree = Path(tree)
    home = Path(home or tree.parent/'home')
    home.mkdir(parents=True, exist_ok=True)
    env = dict(PATH='/usr/bin:/bin', HOME=str(home), PYTHONPATH=str(tree), PYTHONDONTWRITEBYTECODE='1', LANG='en_US.UTF-8',
               HYPOTHESIS_STORAGE_DIRECTORY=str(home/'hypothesis'))
    problems, complete = [], True
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch)/'junit.xml'
        command = [str(python), '-B', '-m', 'pytest', *labels, '-p', 'no:cacheprovider', '--continue-on-collection-errors',
                   '-rfE', '--tb=short', '-q', f'--junitxml={report}', *(['-n', str(workers)] if workers else []), *extra]
        started = time.time()
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(command, cwd=tree, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            try:
                proc.wait(timeout=max(1, timeout))
            except subprocess.TimeoutExpired:
                # The whole group: pytest's workers or a test's child would otherwise outlive the run.
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                complete = False
                problems.append('stopped when the time ran out; tests not reached have no result')
            seconds = round(time.time() - started, 2)
            log.seek(0)
            output = log.read().decode(errors='replace')
        states, unloadable = read_junit(tree, report)
    if complete and not states and proc.returncode not in (0, 5):
        problems.append(f'pytest ended without a result (exit {proc.returncode}): ' + (output.strip().splitlines() or [''])[-1][-300:])
    for module in unloadable:
        problems.append(f'{module} could not be collected')
    return dict(states=states, output=output, problems=problems, seconds=seconds, complete=complete)


def failures(output, limit=160):
    """pytest's failure section, cut to ``limit`` lines."""
    lines = output.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r'^=+ (FAILURES|ERRORS) =+$', line)), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if re.match(r'^=+ short test summary info =+$', lines[i]) or SUMMARY.match(lines[i])), len(lines))
    keep = lines[start:end]
    return keep[:limit] + ([f'... {len(keep) - limit} more lines of failure output not shown'] if len(keep) > limit else [])


def covers(label, test):
    """Whether running ``label`` (a directory, file or node id) runs ``test`` (path::Class::test)."""
    label = label.rstrip('/')
    return test == label or test.startswith(label + '::') or test.startswith(label + '/') or label in ('', '.', 'sympy')
