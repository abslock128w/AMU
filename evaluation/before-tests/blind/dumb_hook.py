#!/usr/bin/env python3
"""The control arm's hook: same triggers as hook.py, no AMU. It runs the test modules for every file the agent has
touched and puts their outcome in front of the agent.

Triggers, as in hook.py: after Edit/Write/MultiEdit of a .py file in the workspace; after a Bash command during which
a .py file changed; before a Bash command that runs the tests. An unchanged .py state reuses the last result, not
charged. Every run is charged its whole wall time to the run's test-time ledger.

Which modules, for every .py file that differs from HEAD (git status), in this order, at most MAX_MODULES:
1. a touched test module is run itself; a touched conftest.py runs the test modules under its directory;
2. a touched production file `.../name.py` (a package's `__init__.py` is the package): test modules named
   `test_name.py` in a tests directory of the same package, and every test module that imports it (read with the
   parser: `import a.b.name`, `from a.b.name import`, `from a.b import name`, relative imports).
Test modules are files pytest or Django's runner collect (test_*.py, *_test.py, tests.py under a tests directory);
Django's gis_tests, which abort the runner without a GIS database, are never chosen.
No reachability, no coverage, no ranking: this is what a careful script would do without AMU.
Usage (from the hook configuration): dumb_hook.py RUN_DIR/config.json, with the tool event as JSON on stdin.
"""
import ast
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hook  # noqa: E402  (shares the config, the triggers, the .py state and the label format)

config, djrun = hook.config, hook.djrun
MAX_MODULES = 25
SHOWN = hook.SHOWN


def is_test_module(path):
    """A file pytest or Django's runner collects tests from."""
    name = Path(path).name
    return hook.is_test(path) and (name.startswith('test_') or name.endswith('_test.py') or name in ('tests.py', 'test.py'))


def test_files(workspace):
    out = subprocess.run(['git', '-C', str(workspace), 'ls-files', '--cached', '--others', '--exclude-standard'],
                         capture_output=True, text=True).stdout.split()
    return sorted(p for p in out if p.endswith('.py') and is_test_module(p) and not p.startswith('tests/gis_tests/'))


def dotted(path):
    """Module name of a .py path; a package's __init__.py is the package."""
    parts = path[:-3].split('/')
    if parts[-1] == '__init__':
        parts = parts[:-1]
    return '.'.join(parts)


def imported(path, text):
    """Every module name a file imports, including `from a import b` as a.b, resolved from the file's own package."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    package = dotted(path).rsplit('.', 1)[0] if '.' in dotted(path) else ''
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                anchor = package.split('.')[:len(package.split('.')) - node.level + 1] if package else []
                base = '.'.join([*anchor, base] if base else anchor)
            names.add(base)
            names.update(f'{base}.{alias.name}' if base else alias.name for alias in node.names)
    return names


def modules_for(workspace, touched):
    """Test files to run for the touched .py files, with the rule that chose each, and how many were left out."""
    tests = test_files(workspace)
    chosen, why = [], {}
    def add(path, reason):
        if path not in why and (Path(workspace)/path).is_file():
            chosen.append(path)
            why[path] = reason
    imports = {}
    def importers(name):
        if not imports:
            for t in tests:
                try:
                    imports[t] = imported(t, (Path(workspace)/t).read_text(errors='replace'))
                except OSError:
                    imports[t] = set()
        return [t for t in tests if name in imports[t]]
    for path in touched:
        if is_test_module(path) and not path.startswith('tests/gis_tests/'):
            add(path, 'touched test file')
    for path in touched:
        if is_test_module(path):
            continue
        if Path(path).name == 'conftest.py':
            folder = str(Path(path).parent)
            for t in tests:
                if folder in ('.', '') or t.startswith(folder + '/'):
                    add(t, f'under {path}')
            continue
        # Named after the file, in the nearest tests directory of the same package, then every importer.
        stem, folder = Path(path).stem, Path(path).parent
        named = [t for t in tests if Path(t).name in (f'test_{stem}.py', f'tests_{stem}.py')
                 and (Path(t).parent.parent == folder or str(Path(t).parent).startswith(str(folder)))]
        for t in named:
            add(t, f'named after {path}')
        for t in importers(dotted(path)):
            add(t, f'imports {dotted(path)}')
    return chosen[:MAX_MODULES], why, max(0, len(chosen) - MAX_MODULES)


VERBOSE = re.compile(r'^(?P<id>\S.*?::.+?) (?P<state>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b')
COLLECT = re.compile(r'ERROR collecting (?P<path>\S+)')
ORDER = {'failed': 3, 'passed': 2, 'skipped': 1}


def read_verbose(text):
    """({test function id: state}, items) from pytest -v output, even a partial one. A function fails if any of its
    parametrized cases fails; a module that cannot be collected fails as a whole."""
    states, items = {}, 0
    for line in text.splitlines():
        match = VERBOSE.match(line)
        if match:
            items += 1
            ident = re.sub(r'\[.*\]$', '', match['id'])
            state = {'PASSED': 'passed', 'XPASS': 'passed', 'FAILED': 'failed', 'ERROR': 'failed'}.get(match['state'], 'skipped')
            if ORDER[state] > ORDER.get(states.get(ident), 0):
                states[ident] = state
        collect = COLLECT.search(line)
        if collect:
            states[collect['path'] + '::module_could_not_be_collected'] = 'failed'
    return states, items


def run_modules(workspace, modules, timeout):
    """({test id: state}, items, problems, complete) for the chosen test files, under the project's runner."""
    python = config['python']
    if (Path(workspace)/'tests'/'runtests.py').is_file():
        labels = [m[len('tests/'):-3].replace('/', '.') if m.startswith('tests/') else m[:-3].replace('/', '.') for m in modules]
        result = djrun.run(python, workspace, labels, shards=1, timeout=timeout, home=Path(config['run'])/'home')
        return result['states'], len(result['states']), result['problems'], not any('stopped' in p for p in result['problems'])
    command = [python, '-m', 'pytest', *modules, '-v', '-p', 'no:cacheprovider', '--no-header', '-rN', '--continue-on-collection-errors']
    try:
        done = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        output, complete = done.stdout + done.stderr, True
        # 0 all passed, 1 some failed, 5 nothing collected (all skipped at import, for example): none is a problem.
        problems = [] if done.returncode in (0, 1, 5) else [f'pytest exited {done.returncode}: ' + output.strip().splitlines()[-1][-300:] if output.strip() else f'pytest exited {done.returncode}']
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b''
        output = partial.decode(errors='replace') if isinstance(partial, bytes) else partial
        complete, problems = False, [f'stopped after {timeout} s, the time allowed for this run; the results so far are shown']
    states, items = read_verbose(output)
    return states, items, problems, complete


def check(run, workspace, head, row):
    ledger = djrun.Ledger(run/'ledger.json', config['budget'])
    state = sorted(hook.python_state(workspace).items())
    key = hashlib.sha256(json.dumps(state).encode()).hexdigest()
    last = run/'last-check.json'
    previous = json.loads(last.read_text()) if last.exists() else None
    if previous and previous['key'] == key:
        row.update(cached=True, failing=previous['failing'])
        return (f"{head} (no .py file changed since the last run, so its result stands; nothing charged): {previous['body']}"
                f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]')
    touched = [path for path, _ in state]
    started = time.time()
    modules, why, dropped = modules_for(workspace, touched)
    if not modules:
        row.update(modules=0)
        body = 'no test module matches the files you changed, so nothing was run.'
        last.write_text(json.dumps(dict(key=key, body=body, failing=[])))
        return f'{head}: {body}\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]'
    left = ledger.left()
    if left < hook.MIN_LEFT:
        row.update(skipped='budget', left=round(left, 1))
        return None
    # The same allowance as the AMU hook's two sides: two thirds of the budget left, at most 120 s.
    timeout = max(10, min(120, int(left * 2 / 3)))
    states, items, problems, complete = run_modules(workspace, modules, timeout)
    seconds = time.time() - started
    ledger.charge('hook', seconds, detail=row.get('trigger'))
    failed = sorted(i for i, s in states.items() if s == 'failed')
    passed = sum(s == 'passed' for s in states.values())
    names = ', '.join(modules[:8]) + (f' and {len(modules) - 8} more' if len(modules) > 8 else '')
    body = (f'ran {len(states)} tests ({items} cases) in {len(modules)} test module(s) for the files you changed ({names}) in {seconds:.0f} s: '
            f'{passed} passed, {len(failed)} failed.')
    if failed:
        body += '\nFailing now:\n' + '\n'.join('- ' + hook.label(i) for i in failed[:SHOWN]) + (f'\n- and {len(failed) - SHOWN} more' if len(failed) > SHOWN else '')
    if problems:
        body += '\nProblems: ' + '; '.join(problems[:3])
    if dropped:
        body += f'\n{dropped} further matching test module(s) not run (at most {MAX_MODULES} per run).'
    row.update(seconds=round(seconds, 2), modules=len(modules), ran=len(states), items=items, failing=[hook.label(i) for i in failed],
               problems=problems, complete=complete, why={m: why[m] for m in modules})
    if complete:
        # A run cut short says nothing final about this .py state; the next trigger runs again.
        last.write_text(json.dumps(dict(key=key, body=body, failing=row['failing'])))
    return f'{head} ({seconds:.0f} s, charged to the test-time budget): {body}\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]'


def main():
    event = json.load(sys.stdin)
    run, workspace = Path(config['run']), Path(config['workspace']).resolve()
    tool, phase, given = event.get('tool_name'), event.get('hook_event_name'), event.get('tool_input') or {}
    snapshot = run/'snapshots'/f"{event.get('tool_use_id', 'unknown')}.json"
    row = dict(at=round(time.time(), 1), event=phase, tool=tool, hook='dumb')
    head = None
    with open(run/'hook.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if phase == 'PreToolUse' and tool == 'Bash':
            snapshot.parent.mkdir(exist_ok=True)
            snapshot.write_text(json.dumps(hook.python_state(workspace)))
            command = str(given.get('command') or '')
            if hook.RUNS_TESTS.search(command):
                head = 'Automatic test run before this test run'
                row.update(trigger='before ./rt', command=command[:200])
        elif phase == 'PostToolUse' and tool == 'Bash':
            before = json.loads(snapshot.read_text()) if snapshot.exists() else {}
            after = hook.python_state(workspace)
            changed = sorted(p for p in set(before) | set(after) if before.get(p, 'unchanged-at-HEAD') != after.get(p, 'unchanged-at-HEAD'))
            if changed:
                head = f"Automatic test run after this command changed {', '.join(changed[:3])}{' and more' if len(changed) > 3 else ''}"
                row.update(trigger='after Bash', files=changed)
        elif phase == 'PostToolUse' and tool in ('Edit', 'Write', 'MultiEdit'):
            path = str(given.get('file_path') or '')
            try:
                rel = str(Path(path).resolve().relative_to(workspace))
            except ValueError:
                rel = None
            if rel and rel.endswith('.py'):
                head = f'Automatic test run after the edit to {rel}'
                row.update(trigger='after edit', files=[rel])
        if head is None:
            return
        text = check(run, workspace, head, row)
        row['injected'] = text
        if text:
            print(json.dumps({'hookSpecificOutput': {'hookEventName': phase, 'additionalContext': text}}))
    with open(run/'hook.jsonl', 'a') as handle:
        handle.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    main()
