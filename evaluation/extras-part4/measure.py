"""Build queue part 4: run the frozen 100 pull requests once with every --extras feature on, as the Action would.

Usage: python measure.py --root /Users/Shared/amu-retro-2026-10-18 --engine ENGINE_EXPORT [--repo NAME] [--only N ...]

Same set, budget (300 s), wall cap (1200 s) and environments as the brief 5 retrospective (its run.py is reused), with
the engine exported from feat/flags-part4 put first on PYTHONPATH. Recording is off for both repositories: the features
measured here do not use it. Results go to ROOT/out-q4/<repo>/<number>/; done.json marks a finished pull request.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RETRO = Path('/Users/Abslock128/Documents/ChatGPT/AMU/q4-flags/evaluation/retrospective')
sys.path.insert(0, str(RETRO))
import run as retro  # noqa: E402
from freeze import check  # noqa: E402

EXTRAS = 'refactor,explore,mutation,equivalence'


def one(root, engine, name, pr):
    clone, out = root/'repos'/name, root/'out-q4'/name/str(pr['number'])
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    work = root/'work-q4'/name/str(pr['number'])
    head = work/'head'
    retro.worktree(clone, head, pr['head_sha'])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cache, target = root/'cache-q4'/name/'index', head/'.amu'/'index'
    shutil.rmtree(head/'.amu', ignore_errors=True)
    if cache.is_dir():
        shutil.copytree(cache, target, symlinks=True)
    env = dict(retro.environment(root), PYTHONPATH=str(engine))
    command = [retro.amu_python(root), '-P', '-m', 'amu.ci.run', '--base', pr['base_sha'], '--head', pr['head_sha'],
               '--out', str(out), '--python', str(root/'env'/name/'bin'/'python'), '--pr', str(pr['number']),
               '--install-ok', 'true', '--amu-ref', 'feat/flags-part4', '--budget', str(retro.BUDGET),
               '--wall-cap', str(retro.WALL_CAP), '--generate-dollars', '0', '--extras', EXTRAS]
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=env)
    (out/'run.log').write_text(done.stdout + '\n--- stderr ---\n' + done.stderr)
    if target.is_dir():
        shutil.rmtree(cache, ignore_errors=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, cache, symlinks=True)
    report = json.loads((out/'report.json').read_text())
    meta = json.loads((out/'meta.json').read_text())
    result = dict(repo=name, number=pr['number'], seconds=round(time.monotonic() - began, 1), status=meta.get('status'),
                  error=report.get('error'), flips=report.get('flips'), first_flip_seconds=report.get('first_flip_seconds'),
                  refactors=len(report.get('refactors') or []), refactor_seconds=report.get('refactor_seconds'),
                  mutation={k: v for k, v in (report.get('mutation') or {}).items() if k != 'survivors'},
                  survivors=len((report.get('mutation') or {}).get('survivors') or []),
                  equivalence={k: v for k, v in (report.get('equivalence') or {}).items() if k != 'rows'},
                  equivalence_rows=len((report.get('equivalence') or {}).get('rows') or []))
    (out/'done.json').write_text(json.dumps(result, indent=1))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', required=True)
    p.add_argument('--engine', required=True)
    p.add_argument('--repo', choices=['django', 'sympy'], action='append')
    p.add_argument('--only', type=int, nargs='+')
    a = p.parse_args(argv)
    root, engine, doc = Path(a.root), Path(a.engine), check()
    os.environ.pop('PYTHONPATH', None)
    for name in a.repo or ['django', 'sympy']:
        for pr in sorted(doc['prs'][name], key=lambda r: (r['merged_at'], r['number'])):
            if a.only and pr['number'] not in a.only:
                continue
            with retro.suite_lock(root, f"q4 {name} {pr['number']}", yield_to_probes=True):
                result = one(root, engine, name, pr)
            print(json.dumps({k: result.get(k) for k in ('repo', 'number', 'status', 'seconds', 'refactors', 'survivors',
                                                          'equivalence_rows', 'first_flip_seconds', 'error')}), flush=True)


if __name__ == '__main__':
    main()
