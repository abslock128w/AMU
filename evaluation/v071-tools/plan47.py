"""Matrix for eval47.yml: the recording jobs (one per nightly commit, as record47.py groups them) and the run jobs.
Usage: python plan47.py PLAN.json  (needs repos/django and repos/sympy under AMU_EVAL_WORK). Prints GitHub outputs."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record47 import FREEZE, W, nightly  # noqa: E402

plan = json.loads(Path(sys.argv[1]).read_text())
only = set(plan.get('only') or [])
prs = json.loads(FREEZE.read_text())['prs']
record, runs = {}, []
for repo in ('flask', 'django', 'sympy'):
    for pr in prs[repo]:
        key = f"{repo}#{pr['number']}"
        if only and key not in only:
            continue
        runs.append(dict(repo=repo, n=pr['number']))
        if repo != 'flask' and plan.get('record', True):
            commit = nightly(W/'repos'/repo, pr['merge_base'])
            record.setdefault((repo, commit), []).append(pr['number'])
rec = [dict(repo=r, commit=c, prs=' '.join(map(str, n))) for (r, c), n in record.items()]
print(json.dumps(rec, indent=1), file=sys.stderr)
with open(os.environ.get('GITHUB_OUTPUT', '/dev/stdout'), 'a') as out:
    out.write(f'rec={json.dumps(rec)}\nruns={json.dumps(runs)}\nhas_rec={"yes" if rec else ""}\n')
