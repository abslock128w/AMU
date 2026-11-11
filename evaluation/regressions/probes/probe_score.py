"""Score the probe trial. For each confirmed regression, rerun the kept probes at the fix commit.
A probe is: restored (outcome at the fix equals the base outcome), intended (equals the head outcome), or other.
Caught: a restored probe whose target is a regressed function or the function enclosing it. 'caught_any' drops the
target condition. Usage: PYTHONPATH=<engine> python probe_score.py --tag TAG"""
import argparse, json, re, shutil, sys, tempfile
from pathlib import Path
import verify as V
from amu.core import generate, runners

a = argparse.ArgumentParser(); a.add_argument('--tag', required=True); args = a.parse_args()
here = Path(__file__).resolve().parent
W = here.parent
cands = {V.cid(c): c for c in json.loads((here/'candidates.json').read_text())}
rows = []
for line in (here/'verified.jsonl').read_text().splitlines():
    row = json.loads(line)
    if row['verdict'] != 'confirmed':
        continue
    key, c = row['id'], cands[row['id']]
    source = key
    if key.startswith('requests:7272:'):
        source = 'requests:7272:6404f345e5'
    out = W/f'out-reg-{args.tag}'/source.replace(':', '_')
    if not (out/'probes.json').exists():
        rows.append(dict(id=key, state='not run')); continue
    data = json.loads((out/'probes.json').read_text())
    kept, verdicts = data['kept'], []
    if kept:
        clone, python = V.CLONES[c['repo']], V.PYTHONS[c['repo']]/'bin'/'python'
        tree = W/'regwork'/'probe-fix'/key.replace(':', '_')
        V.git(clone, 'worktree', 'remove', '--force', str(tree)); shutil.rmtree(tree, ignore_errors=True)
        tree.parent.mkdir(parents=True, exist_ok=True)
        V.git(clone, 'worktree', 'add', '--detach', '-q', str(tree), c['fix_sha'], check=True)
        runner = runners.get(tree)
        files, ids = generate.test_file(runner.name, list(enumerate(kept)), tree)
        generate._install(tree, files)
        with tempfile.TemporaryDirectory() as scratch:
            runs = [generate._run(runner, str(python), tree, list(ids.values()), scratch, f'fix-{n}', 300) for n in range(2)]
        for i, probe in enumerate(kept):
            got = [r.get(i) for r in runs]
            fix = got[0][0] if got[0] and got[0] == got[1] else None
            # The probe's number is part of its test's name and differs between the trial run and this one.
            same = lambda x, y: re.sub(r'test_amu_probe_\d+', 'probe', json.dumps(x)) == re.sub(r'test_amu_probe_\d+', 'probe', json.dumps(y))
            verdicts.append(dict(target=probe['target'], call=probe['call'], before=probe['before'], after=probe['after'], fix=fix,
                                 verdict='unstable or did not run' if fix is None else 'restored' if same(fix, probe['before'])
                                 else 'intended' if same(fix, probe['after']) else 'other'))
        V.git(clone, 'worktree', 'remove', '--force', str(tree))
    names = {r.split('#', 1)[-1] for r in c.get('regressed') or []}
    def on(target):
        q = target.split('#')[-1]
        return any(n == q or n.startswith(q + '.') for n in names)
    restored = [v for v in verdicts if v['verdict'] == 'restored']
    rows.append(dict(id=key, seconds=data['seconds'], info=data['info'], regressed=c.get('regressed'), kept=len(kept),
                     restored=len(restored), intended=sum(v['verdict'] == 'intended' for v in verdicts),
                     other=sum(v['verdict'] in ('other', 'unstable or did not run') for v in verdicts),
                     caught=any(on(v['target']) for v in restored), caught_any=bool(restored), probes=verdicts))
    print({k: rows[-1][k] for k in ('id', 'kept', 'restored', 'intended', 'other', 'caught', 'caught_any')}, flush=True)
(W/f'out-reg-{args.tag}'/'score.json').write_text(json.dumps(rows, indent=1))
done = [r for r in rows if 'caught' in r]
print('caught', sum(r['caught'] for r in done), 'caught_any', sum(r['caught_any'] for r in done), 'of', len(done), 'run;', len(rows) - len(done), 'not run')
