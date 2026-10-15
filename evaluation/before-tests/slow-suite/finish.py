"""Close one desktop-session run: copy its transcript, snapshot the agent's tree, write result.json.

Usage: finish.py ROOT ID
The start time and session id come from runs/sessions.jsonl. The end time is the transcript's last timestamp. A run
whose transcript goes past the hard stop is not snapshotted here; it is reported, so its tree at the hard stop can be
rebuilt by hand from the transcript (and the rebuild recorded in runs/departures.md).
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE/'rules.json').read_text())

if __name__ == '__main__':
    root, ident = Path(sys.argv[1]), sys.argv[2]
    work, run = root/'ws'/ident, root/'runs'/ident
    row = [json.loads(line) for line in (root/'runs'/'sessions.jsonl').read_text().splitlines() if json.loads(line)['id'] == ident][-1]
    folder = Path.home()/'.claude'/'projects'/('-' + str(work).strip('/').replace('/', '-').replace('.', '-'))
    transcript = max(folder.glob('*.jsonl'), key=lambda p: p.stat().st_mtime)
    shutil.copy(transcript, run/'session.jsonl')
    stamps = [datetime.fromisoformat(json.loads(line)['timestamp'].replace('Z', '+00:00')).timestamp()
              for line in transcript.read_text().splitlines() if '"timestamp"' in line and json.loads(line).get('timestamp')]
    ended = max(stamps)
    stop = row['started'] + RULES['hard_cap_seconds']
    if ended > stop:
        print(ident, f'PAST THE HARD STOP by {ended - stop:.0f} s: rebuild the tree at the hard stop by hand')
        sys.exit(2)
    subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True)
    (run/'agent.diff').write_bytes(subprocess.run(['git', '-C', str(work), 'diff', '--cached', 'HEAD', '--binary'], check=True,
                                                  capture_output=True).stdout)
    (run/'result.json').write_text(json.dumps(dict(started=row['started'], ended=ended, wall_seconds=round(ended - row['started'], 1),
                                                   session=row['session'], stopped_at_hard_cap=False), indent=1))
    ledger = json.loads((run/'ledger.json').read_text()) if (run/'ledger.json').exists() else []
    print(ident, f"{ended - row['started']:.0f} s wall, test time {sum(r['seconds'] for r in ledger):.0f} s,",
          f"{sum(r['tool'] == 'rt' for r in ledger)} ./rt, {sum(r['tool'] == 'hook' for r in ledger)} hook runs,",
          len((run/'agent.diff').read_text(errors='replace').splitlines()), 'diff lines')
