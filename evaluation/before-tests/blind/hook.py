#!/usr/bin/env python3
"""Hook for the AMU arm of the blind trial: run bare amu on the workspace and put the flip count and the
flipped test ids in front of the agent. Three triggers:

- PostToolUse on Edit, Write or MultiEdit of a .py file inside the workspace;
- PostToolUse on Bash, when any .py file in the tree changed during the command (git status and file
  contents before against after; the before state is saved by the PreToolUse call for the same command);
- PreToolUse on Bash, when the command runs ./rt or runtests.py: the current flips, before the tests run.

A tree whose uncommitted .py state is the same as at the last check reuses that check's result and is
charged nothing. Every check that runs is charged its whole running time to the run's test-time ledger.
Usage (from the hook configuration): hook.py RUN_DIR/config.json, with the tool event as JSON on stdin.
It never names the tool it runs.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

config = json.loads(Path(sys.argv[1]).read_text())
sys.path.insert(0, config['tools'])
import djrun  # noqa: E402

MIN_LEFT = 20   # seconds of budget below which the check is not started
SHOWN = 40      # flipped tests listed at most
EDITED = 'test edited, not fixed'
RUNS_TESTS = re.compile(r'(^|[\s;&|(`])(\./rt|rt)(\s|$|;|&|\|)|runtests\.py')


def label(row):
    """tests/app/module.py::Class::test -> app.module.Class.test, the form ./rt takes."""
    ident = row['id'] if isinstance(row, dict) else str(row).rsplit(' ', 1)[-1]
    path, *rest = ident.split('::')
    path = path[len('tests/'):] if path.startswith('tests/') else path
    return '.'.join([path[:-3].replace('/', '.') if path.endswith('.py') else path, *rest])


def python_state(workspace):
    """{path: sha1 of content, or None when deleted} for every .py file git reports as changed or untracked."""
    out = subprocess.run(['git', '-C', str(workspace), 'status', '--porcelain', '--untracked-files=all', '-z'],
                         capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0')).stdout.decode(errors='replace')
    state, entries = {}, out.split('\0')
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in 'RC':
            index += 1  # the original path of a rename follows
        if path.endswith('.py'):
            file = workspace/path
            state[path] = hashlib.sha1(file.read_bytes()).hexdigest() if file.is_file() else None
    return state


def message(packet):
    """The result, without the line's opening words."""
    flips, failing = packet.get('flips'), packet.get('predicted_failures') or []
    counted = re.match(r'EXECUTED (\d+) of (\d+)', packet.get('summary') or '')
    ran = counted and counted[1]
    # Only what limits this execution; repository-wide static caveats are left out of the agent's context.
    gaps = [g for g in packet.get('gaps') or [] if isinstance(g, str)
            and ('shadow run' in g or 'candidate' in g or 'edited test files' in g or 'could not be rerun' in g)][:3]
    head = ''
    if packet.get('failure'):
        # The runner aborts on the changed code and runs on the code before it: the change broke it.
        text = packet.get('summary') or 'FAILED: the test runner aborts on the changed code.'
    elif flips is None:
        text = head + 'no test could be executed, so nothing is known about what your changes break.'
    elif flips == 0:
        text = head + f"{ran or 'the candidate'} existing tests were run on the code with your uncommitted changes; none that passed before now fails."
    else:
        forms = {'skipped': 'is skipped', 'removed': 'is gone', 'unrunnable': 'cannot run', 'not failing': 'no longer fails'}
        ids = [label(row) + (f" ({EDITED}: as edited it {forms.get(row.get('edited_form'), 'passes')})"
                             if isinstance(row, dict) and row.get('label') == EDITED else '') for row in failing]
        text = (head + f'{flips} existing test(s) that passed before your uncommitted changes now fail:\n'
                + '\n'.join('- ' + ident for ident in ids[:SHOWN])
                + (f'\n- and {len(ids) - SHOWN} more' if len(ids) > SHOWN else ''))
        if any(f'({EDITED}' in ident for ident in ids):
            text += (f'\n"{EDITED}": the test as it was before your edit still fails on your changed code; '
                     'only your edit to the test changed its outcome.')
    if packet.get('unstable'):
        text += ('\nUnstable, not counted (failed once, passed on a rerun): '
                 + ', '.join(label(ident) for ident in packet['unstable'][:10]))
    if gaps and not packet.get('failure'):
        text += '\nNot covered by this check: ' + '; '.join(gaps)
    return text


def _path(ident):
    return ident.split('::')[0]


def kept_form(workspace, ident):
    """What a flagged test kept by the hash rule is now: gone when its file no longer names it, otherwise only known
    not to fail (it may pass or be skipped)."""
    path = Path(workspace)/_path(ident)
    name = ident.split('::')[-1]
    text = path.read_text(errors='replace') if path.is_file() else ''
    return 'not failing' if re.search(r'\bdef\s+' + re.escape(name) + r'\b', text) else 'removed'


def file_hash(workspace, ident):
    """sha1 of the file that defines a test id (path::Class::test), or None when it is gone."""
    path = Path(workspace)/ident.split('::')[0]
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else None


def is_test(path):
    """A test file: under a tests/ directory, or named test_*.py, *_test.py or conftest.py. A package named test
    (django/test/) is production code."""
    parts = path.split('/')
    name = parts[-1]
    return 'tests' in parts[:-1] or name.startswith('test_') or name.endswith('_test.py') or name == 'conftest.py'


def keys(workspace):
    """(production key, test key, whole key) over the uncommitted .py state."""
    state = sorted(python_state(workspace).items())

    def digest(rows):
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return digest([r for r in state if not is_test(r[0])]), digest([r for r in state if is_test(r[0])]), digest(state)


def check(run, workspace, head, row):
    """Run amu, or reuse the last result. Returns the text to inject, or None.

    Unchanged .py state: the last result stands, nothing is charged. Only test files changed since the last check:
    nothing new can flip in code the agent did not touch, so only the tests that flipped last time are run again,
    or nothing at all when none did. Otherwise a full check, its time per side capped by the budget left."""
    ledger = djrun.Ledger(run/'ledger.json', config['budget'])
    production, tests_key, key = keys(workspace)
    last = run/'last-check.json'
    previous = json.loads(last.read_text()) if last.exists() else None
    if previous and previous['key'] == key:
        row.update(cached=True, flips=previous['flips'], failing=previous['failing'])
        return (f"{head} (no .py file changed since the last check, so its result stands; nothing charged): {previous['body']}"
                f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]')
    only, also = None, None
    if previous and previous.get('production') == production:
        if not previous.get('ids') and previous.get('flips') == 0:
            row.update(cached=True, flips=previous['flips'], failing=previous['failing'], reason='only test files changed')
            previous['key'] = key
            last.write_text(json.dumps(previous))
            return (f"{head} (only test files changed since the last check, which found no failure; nothing charged): {previous['body']}"
                    f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]')
        if previous.get('ids'):
            only = previous['ids']
    if not only and previous and previous.get('ids'):
        # A full check also reruns what was flagged before: a flagged test renamed or deleted since is not a candidate.
        also = previous['ids']
    left = ledger.left()
    if left < MIN_LEFT:
        row.update(skipped='budget', left=round(left, 1))
        return None
    started = time.time()
    env = dict(os.environ, PYTHONPATH=config['amu'], AMU_INDEX_DIR=str(run/'amu-index'))
    side = str(max(10, min(60, int(left / 3))))
    extra = ['--budget', side]
    if only:
        extra += ['--only', *only]
        row['only'] = len(only)
    elif also:
        extra += ['--also', *also]
        row['also'] = len(also)
    try:
        done = subprocess.run([sys.executable, '-m', 'amu.core', '--repo', str(workspace), '--python', config['python'], *config.get('amu_args', []), *extra],
                              env=env, capture_output=True, text=True, timeout=left, stdin=subprocess.DEVNULL)
        output, problem = done.stdout.strip(), done.stderr.strip()[-600:] if done.returncode else None
    except subprocess.TimeoutExpired:
        output, problem = '', 'stopped: the test-time budget ran out during the check'
    seconds = time.time() - started
    ledger.charge('hook', seconds, detail=row.get('trigger'))
    try:
        packet = json.loads(output)
    except ValueError:
        packet = None
    if packet is None:
        text = f'{head} did not finish ({seconds:.0f} s, charged to the test-time budget).'
    else:
        body = message(packet)
        text = f'{head} ({seconds:.0f} s, charged to the test-time budget): {body}'
        (run/'packets').mkdir(exist_ok=True)
        (run/'packets'/f'{int(started * 1000)}.json').write_text(output)
    failing = [] if packet is None else [label(r) for r in packet.get('predicted_failures') or []]
    row.update(seconds=round(seconds, 2), problem=problem, flips=None if packet is None else packet.get('flips'), failing=failing,
               summary=None if packet is None else packet.get('summary'))
    if packet is not None and packet.get('flips') is None and previous and previous.get('ids'):
        # A check that ran nothing (runner failure, timeout) says nothing about what was flagged: keep it flagged.
        last.write_text(json.dumps(dict(previous, key=key, production=production, tests=tests_key, body=body)))
        text += ('\nStill flagged from the previous check, not rechecked this time: '
                 + ', '.join(label(ident) for ident in previous['ids'][:SHOWN]) + (' and more' if len(previous['ids']) > SHOWN else ''))
        return text + f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]'
    if packet is not None:
        if only:
            # The recheck of earlier flips, with the code unchanged since: a flagged test that no longer fails, that the
            # check did not run in its original form, and whose file (or the test files as a whole) changed since it was
            # flagged, stays listed. Editing the test is not fixing the code.
            flagged = previous.get('hashes') or {}
            kept = [ident for ident in only if ident not in {r['id'] for r in packet.get('predicted_failures') or [] if isinstance(r, dict)}
                    and ident not in (packet.get('original_tests') or {})
                    and (file_hash(workspace, ident) != (flagged.get(ident) or {}).get('file')
                         or tests_key != (flagged.get(ident) or {}).get('tests'))]
            if kept:
                packet['predicted_failures'] = [*(packet.get('predicted_failures') or []),
                                                *(dict(id=ident, before='passed', after='failed', label=EDITED, edited_form=kept_form(workspace, ident))
                                                  for ident in kept)]
                packet['flips'] = len(packet['predicted_failures'])
                body = message(packet)
                text = f'{head} ({seconds:.0f} s, charged to the test-time budget): {body}'
                failing = [label(r) for r in packet['predicted_failures']]
                row.update(flips=packet['flips'], failing=failing, kept_by_hash=kept)
        ids = [r['id'] for r in packet.get('predicted_failures') or [] if isinstance(r, dict)]
        # Each flagged test keeps the hashes its file, and the test files as a whole, had when it was first flagged.
        earlier = (previous or {}).get('hashes') or {}
        hashes = {ident: earlier[ident] if isinstance(earlier.get(ident), dict) else dict(file=file_hash(workspace, ident), tests=tests_key)
                  for ident in ids}
        last.write_text(json.dumps(dict(key=key, production=production, tests=tests_key, body=body, flips=packet.get('flips'), failing=failing,
                                        ids=ids, hashes=hashes)))
    return text + f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]'


def main():
    event = json.load(sys.stdin)
    run, workspace = Path(config['run']), Path(config['workspace']).resolve()
    tool, phase, given = event.get('tool_name'), event.get('hook_event_name'), event.get('tool_input') or {}
    snapshot = run/'snapshots'/f"{event.get('tool_use_id', 'unknown')}.json"
    row = dict(at=round(time.time(), 1), event=phase, tool=tool)
    head = None
    with open(run/'hook.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if phase == 'PreToolUse' and tool == 'Bash':
            snapshot.parent.mkdir(exist_ok=True)
            snapshot.write_text(json.dumps(python_state(workspace)))
            command = str(given.get('command') or '')
            if RUNS_TESTS.search(command):
                head = 'Automatic test check before this test run'
                row.update(trigger='before ./rt', command=command[:200])
        elif phase == 'PostToolUse' and tool == 'Bash':
            before = json.loads(snapshot.read_text()) if snapshot.exists() else {}
            after = python_state(workspace)
            changed = sorted(p for p in set(before) | set(after) if before.get(p, 'unchanged-at-HEAD') != after.get(p, 'unchanged-at-HEAD'))
            if changed:
                head = f"Automatic test check after this command changed {', '.join(changed[:3])}{' and more' if len(changed) > 3 else ''}"
                row.update(trigger='after Bash', files=changed)
        elif phase == 'PostToolUse' and tool in ('Edit', 'Write', 'MultiEdit'):
            path = str(given.get('file_path') or '')
            try:
                rel = str(Path(path).resolve().relative_to(workspace))
            except ValueError:
                rel = None
            if rel and rel.endswith('.py'):
                head = f'Automatic test check after the edit to {rel}'
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
