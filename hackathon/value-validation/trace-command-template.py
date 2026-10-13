"""Capture actual local command inputs/outputs in the assigned condition only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CONDITION = Path(__file__).resolve().parent.parent
REPO = CONDITION / 'repo'
ARTIFACTS = CONDITION / 'artifacts'
parser = argparse.ArgumentParser()
parser.add_argument('--start', action='store_true')
parser.add_argument('--finish', action='store_true')
parser.add_argument('--label', default='command')
parser.add_argument('command', nargs=argparse.REMAINDER)
args = parser.parse_args()
now = datetime.now(timezone.utc).isoformat()
life = ARTIFACTS / 'task-lifecycle.json'
if args.start:
    assert not life.exists(), 'Run already started'
    life.write_text(json.dumps({'started_at_utc': now, 'monotonic_start': time.monotonic()}, indent=2) + '\n')
    print('Task clock started.'); raise SystemExit(0)
if args.finish:
    data = json.loads(life.read_text())
    data.update(finished_at_utc=now, task_elapsed_seconds=time.monotonic() - data['monotonic_start'])
    life.write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(data)); raise SystemExit(0)
command = args.command
if command and command[0] == '--': command = command[1:]
assert command, 'Supply a command after --'
environment = {**os.environ, 'HOME': str(CONDITION / 'home'),
               'PYTHONDONTWRITEBYTECODE': '1', 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
start = time.perf_counter()
result = subprocess.run(command, cwd=REPO, env=environment,
                        capture_output=True, text=True, timeout=120)
receipt = {'label': args.label, 'started_at_utc': now,
           'command': command, 'cwd': str(REPO),
           'elapsed_seconds': time.perf_counter() - start,
           'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
target = ARTIFACTS / f'command-{time.time_ns()}.json'
target.write_text(json.dumps(receipt, indent=2) + '\n')
sys.stdout.write(result.stdout); sys.stderr.write(result.stderr)
print(f'\n[Recorded {target.name}; exit {result.returncode}; {receipt["elapsed_seconds"]:.3f}s]', file=sys.stderr)
raise SystemExit(result.returncode)
