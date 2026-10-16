"""Time the hook on a fixed replay of each AMU-arm run, for comparing hook versions. Development use: the
eight tasks are exposed now. Usage: replay_hook.py ROOT RECORDING AMU_SOURCE HOOK_FILE OUT.json

For every AMU-arm workspace of the blind trial: a fresh copy of the parent tree, then four hook events in the
order agents produced them most often: a test run on the clean tree; the agent's production edits made by a
shell command; its test edits made by a shell command; another test run. Each event's hook time is read from
the ledger, the flips from the hook log.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_workspaces as bw  # noqa: E402


def apply(work, diff, tests):
    paths = [line.split(' b/', 1)[1] for line in diff.splitlines() if line.startswith('diff --git ')]
    chosen = [p for p in paths if (p.startswith('tests/') == tests)]
    if chosen:
        subprocess.run(['git', '-C', str(work), 'apply', '--whitespace=nowarn', *[f'--include={p}' for p in chosen], '-'],
                       input=diff.encode(), check=True)
    return chosen


def event(root, ident, phase, use_id, command):
    run = root/'runs'/ident
    payload = json.dumps(dict(hook_event_name=phase, tool_name='Bash', tool_use_id=use_id, tool_input=dict(command=command)))
    subprocess.run(['/opt/homebrew/bin/python3', str(root/'tools'/'hook.py'), str(run/'config.json')], input=payload.encode(),
                   capture_output=True, check=True)


if __name__ == '__main__':
    root, recording, amu, hook, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[5])
    results = json.loads((HERE/'results/runs/map.json').read_text()) if (HERE/'results/runs/map.json').exists() else json.loads((HERE/'results/map.json').read_text())
    manifest = {row['case_id']: row for row in json.loads((HERE.parent/'phase0/blind.json').read_text())}
    tasks = json.loads((HERE/'tasks.json').read_text())
    (root/'tools').mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE/'djrun.py', root/'tools'/'djrun.py')
    shutil.copy(hook, root/'tools'/'hook.py')
    rows = []
    for ident, slot in sorted(results.items()):
        if slot['arm'] != 'amu':
            continue
        bw.build(root, ident, manifest[slot['case']], tasks[slot['task']]['text'], 'amu', recording)
        config = json.loads((root/'runs'/ident/'config.json').read_text())
        config.update(amu=amu, tools=str(root/'tools'))
        (root/'runs'/ident/'config.json').write_text(json.dumps(config))
        work, diff = root/'ws'/ident, (HERE/'results/runs'/ident/'agent.diff').read_text()
        steps = []
        event(root, ident, 'PreToolUse', 'a', './rt queries')
        event(root, ident, 'PreToolUse', 'b', 'python3 - <<EOF')
        production = apply(work, diff, tests=False)
        event(root, ident, 'PostToolUse', 'b', 'python3 - <<EOF')
        event(root, ident, 'PreToolUse', 'c', 'python3 - <<EOF')
        test_files = apply(work, diff, tests=True)
        event(root, ident, 'PostToolUse', 'c', 'python3 - <<EOF')
        event(root, ident, 'PreToolUse', 'd', './rt queries')
        ledger = json.loads((root/'runs'/ident/'ledger.json').read_text())
        log = [json.loads(line) for line in (root/'runs'/ident/'hook.jsonl').read_text().splitlines()]
        rows.append(dict(id=ident, task=slot['task'], production_files=len(production), test_files=len(test_files),
                         hook_seconds=[round(r['seconds'], 1) for r in ledger], events=[dict(trigger=r.get('trigger'), cached=r.get('cached'),
                         only=r.get('only'), flips=r.get('flips'), failing=r.get('failing'), seconds=r.get('seconds')) for r in log]))
        print(json.dumps(rows[-1]), flush=True)
        out.write_text(json.dumps(rows, indent=1))
