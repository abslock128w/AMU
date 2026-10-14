#!/usr/bin/env python3
"""PostToolUse hook for the AMU arm of the blind trial: after an Edit or Write of a .py file, run bare amu
on the workspace and put the flip count and the flipped test ids in front of the agent.

Usage (from the hook configuration): hook.py RUN_DIR/config.json, with the tool event as JSON on stdin.
Its whole running time is charged to the run's test-time ledger. It never names the tool it runs.
"""
import fcntl
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


def label(row):
    """tests/app/module.py::Class::test -> app.module.Class.test, the form ./rt takes."""
    ident = row['id'] if isinstance(row, dict) else str(row).rsplit(' ', 1)[-1]
    path, *rest = ident.split('::')
    path = path[len('tests/'):] if path.startswith('tests/') else path
    return '.'.join([path[:-3].replace('/', '.') if path.endswith('.py') else path, *rest])


def message(rel, packet, seconds):
    flips, failing = packet.get('flips'), packet.get('predicted_failures') or []
    counted = re.match(r'EXECUTED (\d+) of (\d+)', packet.get('summary') or '')
    ran = counted and counted[1]
    # Only what limits this execution; repository-wide static caveats are left out of the agent's context.
    gaps = [g for g in packet.get('gaps') or [] if isinstance(g, str) and ('shadow run' in g or 'candidate' in g)][:3]
    head = f'Automatic test check after the edit to {rel} ({seconds:.0f} s, charged to the test-time budget): '
    if flips is None:
        text = head + 'no test could be executed, so nothing is known about what this edit breaks.'
    elif flips == 0:
        text = head + f'{ran or 'the candidate'} existing tests were run on the code with your uncommitted changes; none that passed before now fails.'
    else:
        ids = [label(row) for row in failing]
        text = (head + f'{flips} existing test(s) that passed before your uncommitted changes now fail:\n'
                + '\n'.join('- ' + ident for ident in ids[:SHOWN])
                + (f'\n- and {len(ids) - SHOWN} more' if len(ids) > SHOWN else ''))
    if gaps:
        text += '\nNot covered by this check: ' + '; '.join(gaps)
    return text


def main():
    event = json.load(sys.stdin)
    path = str((event.get('tool_input') or {}).get('file_path') or '')
    run, workspace = Path(config['run']), Path(config['workspace']).resolve()
    if event.get('tool_name') not in ('Edit', 'Write', 'MultiEdit') or not path.endswith('.py'):
        return
    try:
        rel = str(Path(path).resolve().relative_to(workspace))
    except ValueError:
        return
    ledger = djrun.Ledger(run/'ledger.json', config['budget'])
    row = dict(at=round(time.time(), 1), file=rel)
    with open(run/'hook.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        left = ledger.left()
        if left < MIN_LEFT:
            row.update(skipped='budget', left=round(left, 1))
        else:
            started = time.time()
            env = dict(os.environ, PYTHONPATH=config['amu'], AMU_INDEX_DIR=str(run/'amu-index'))
            try:
                done = subprocess.run([sys.executable, '-m', 'amu.core', '--repo', str(workspace), '--python', config['python'], *config.get('amu_args', [])],
                                      env=env, capture_output=True, text=True, timeout=left, stdin=subprocess.DEVNULL)
                output, problem = done.stdout.strip(), done.stderr.strip()[-600:] if done.returncode else None
            except subprocess.TimeoutExpired:
                output, problem = '', 'stopped: the test-time budget ran out during the check'
            seconds = time.time() - started
            ledger.charge('hook', seconds, detail=rel)
            try:
                packet = json.loads(output)
            except ValueError:
                packet = None
            if packet is None:
                text = f'Automatic test check after the edit to {rel} did not finish ({seconds:.0f} s, charged to the test-time budget).'
            else:
                text = message(rel, packet, seconds)
                (run/'packets').mkdir(exist_ok=True)
                (run/'packets'/f'{int(started * 1000)}.json').write_text(output)
            text += f'\n[test time: {ledger.spent():.0f} of {ledger.budget} s used]'
            row.update(seconds=round(seconds, 2), problem=problem, flips=None if packet is None else packet.get('flips'),
                       failing=[] if packet is None else [label(r) for r in packet.get('predicted_failures') or []],
                       summary=None if packet is None else packet.get('summary'), injected=text)
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': text}}))
    with open(run/'hook.jsonl', 'a') as handle:
        handle.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    main()
