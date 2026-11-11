"""Phase 0 probe trial: replay confirmed regressions with probes generated for every changed function (claude -p).
Usage: python probe_trial.py --engine DIR --tag TAG [--only ID ...]. Out: out-reg-TAG/<id>/{report.json,probes.json}."""
import argparse, json, shutil, sys
from pathlib import Path
import replay as R

a = argparse.ArgumentParser()
a.add_argument('--engine', required=True); a.add_argument('--tag', required=True); a.add_argument('--only', nargs='*')
a.add_argument('--minutes', default='15'); a.add_argument('--budget', default='120')
args = a.parse_args()
here = Path(__file__).resolve().parent
cands = {R.V.cid(c): c for c in json.loads((here/'candidates.json').read_text())}
real = R.subprocess.run
def run(command, *p, **k):
    if '--generate-dollars' in command:
        command = [x for x in command]
        command[command.index('--budget') + 1] = args.budget
        command[command.index('--wall-cap') + 1] = '2400'
        command += ['--generate-cli', '--generate-minutes', args.minutes]
    return real(command, *p, **k)
R.subprocess.run = run
for line in (here/'verified.jsonl').read_text().splitlines():
    row = json.loads(line)
    if row['verdict'] != 'confirmed' or (args.only and row['id'] not in args.only):
        continue
    key, c = row['id'], cands[row['id']]
    out = R.W/f'out-reg-{args.tag}'/key.replace(':', '_')
    if (out/'probes.json').exists():
        continue
    shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    seconds, comment = R.run_amu(Path(args.engine), args.tag, c, key, out, None)
    report = json.loads((out/'report.json').read_text()) if (out/'report.json').exists() else {}
    found = report.get('generated_differences') or []
    info = report.get('generated') or {}
    (out/'probes.json').write_text(json.dumps(dict(id=key, seconds=seconds, regressed=c.get('regressed'), info=info, kept=found), indent=1))
    print(key, seconds, {k: info.get(k) for k in ('functions', 'proposed', 'compiled', 'ran', 'stable', 'reached', 'accepted')}, flush=True)
