"""Rerun kept return>raise probes at the head with the origin recorder; classify raised / call / crash.
Usage: PYTHONPATH=<engine>:regressions python regressions/probe_origin.py"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
import verify as V
import run47
from amu.core import generate, runners

W = Path(__file__).resolve().parent.parent
here = W/'regressions'
jobs = []
cands = {V.cid(c): c for c in json.loads((here/'candidates.json').read_text())}
for f in sorted((W/'out-reg-probe1').glob('*/probes.json')):
    d = json.loads(f.read_text()); c = cands[d['id']]
    jobs.append((d['id'], V.CLONES[c['repo']], str(V.PYTHONS[c['repo']]/'bin'/'python'), c['head'], d['kept'], f.parent))
freeze = json.loads(run47.FREEZE.read_text())['prs']
for name in ('flask', 'django', 'sympy'):
    for pr in freeze[name]:
        out = W/'out-probe1'/name/str(pr['number'])
        if (out/'report.json').exists():
            kept = json.loads((out/'report.json').read_text()).get('generated_differences') or []
            jobs.append((f"{name}#{pr['number']}", W/'repos'/name, None, pr['head_sha'], kept, out))
for key, clone, python, head, kept, out in jobs:
    picked = [(i, p) for i, p in enumerate(kept) if p['before'][0] == 'return' and p['after'][0] == 'raise']
    if (out/'origin.json').exists() or not picked:
        continue
    tree = W/'regwork'/'probe-origin'/key.replace(':', '_').replace('#', '_')
    V.git(clone, 'worktree', 'remove', '--force', str(tree)); shutil.rmtree(tree, ignore_errors=True)
    tree.parent.mkdir(parents=True, exist_ok=True)
    V.git(clone, 'worktree', 'add', '--detach', '-q', str(tree), head, check=True)
    if python is None:
        name = key.split('#')[0]
        python = str(W/'env'/run47.env_for(name, tree)/'bin'/'python')
    runner = runners.get(tree)
    files, ids = generate.test_file(runner.name, picked, tree)
    generate._install(tree, files)
    origins = {}
    with tempfile.TemporaryDirectory() as scratch:
        got = generate._run(runner, python, tree, list(ids.values()), scratch, 'origin', 600, origins=origins)
    rows = [dict(index=i, target=p['target'], call=p['call'], type=p['after'][1], at=origins.get(i), ran=i in got,
                 origin=generate.origin(tree, origins.get(i), set(files)) if i in got and got[i][0][0] == 'raise' else None)
            for i, p in picked]
    (out/'origin.json').write_text(json.dumps(rows, indent=1))
    V.git(clone, 'worktree', 'remove', '--force', str(tree))
    print(key, [(r['type'], r['origin']) for r in rows], flush=True)
