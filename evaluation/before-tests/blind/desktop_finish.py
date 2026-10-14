"""Close one desktop-session run: snapshot the agent's tree and write result.json.
Usage: desktop_finish.py ROOT ID STARTED_EPOCH SESSION_ID [--stopped]"""
import json
import subprocess
import sys
import time
from pathlib import Path

root, ident, started, session = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]), sys.argv[4]
work, run = root/'ws'/ident, root/'runs'/ident
ended = time.time()
subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True)
(run/'agent.diff').write_bytes(subprocess.run(['git', '-C', str(work), 'diff', '--cached', 'HEAD', '--binary'], check=True, capture_output=True).stdout)
(run/'result.json').write_text(json.dumps(dict(started=started, ended=ended, wall_seconds=round(ended - started, 1), session=session,
                                               stopped_at_hard_cap='--stopped' in sys.argv), indent=1))
print(ident, round(ended - started), 's')
