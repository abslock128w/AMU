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
    gaps = [g for g in packet.get('gaps') or [] if isinstance(g, str) and ('shadow run' in g or 'candidate' in g)][:3]
    head = ''
    if packet.get('failure'):
        # The runner aborts on the changed code and runs on the code before it: the change broke it.
        text = packet.get('summary') or 'FAILED: the test runner aborts on the changed code.'
    elif flips is None:
        text = head + 'no test could be executed, so nothing is known about what your changes break.'
    elif flips == 0:
        text = head + f"{ran or 'the candidate'} existing tests were run on the code with your uncommitted changes; none that passed before now fails."
    else:
        ids = [label(row) for row in failing]
        text = (head + f'{flips} existing test(s) that passed before your uncommitted changes now fail:\n'
                + '\n'.join('- ' + ident for ident in ids[:SHOWN])
                + (f'\n- and {len(ids) - SHOWN} more' if len(ids) > SHOWN else ''))
    if gaps and not packet.get('failure'):
        text += '\nNot covered by this check: ' + '; '.join(gaps)
    return text


def check(run, workspace, head, row):
    """Run amu (or reuse the last result for an unchanged tree). Returns the text to inject, or None."""
    ledger = djrun.Ledger(run/'ledger.json', config['budget'])
    key = hashlib.sha256(json.dumps(sorted(python_state(workspace).items())).encode()).hexdigest()
    last = run/'last-check.json'
    previous = json.loads(last.read_text()) if last.exists() else None
    if previous and previous['key'] == key:
        row.update(cached=True, flips=previous['flips'], failing=previous['failing'])
        return (f"{head} (no .py file changed since the last check, so its result stands; nothing charged): {previous['body']}"
                f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]')
    left = ledger.left()
    if left < MIN_LEFT:
        row.update(skipped='budget', left=round(left, 1))
        return None
    started = time.time()
    env = dict(os.environ, PYTHONPATH=config['amu'], AMU_INDEX_DIR=str(run/'amu-index'))
    try:
        done = subprocess.run([sys.executable, '-m', 'amu.core', '--repo', str(workspace), '--python', config['python'], *config.get('amu_args', [])],
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
    if packet is not None:
        last.write_text(json.dumps(dict(key=key, body=body, flips=packet.get('flips'), failing=failing)))
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
