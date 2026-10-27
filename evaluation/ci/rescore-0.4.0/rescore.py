"""Re-score ten frozen pull requests (comment-usefulness test) on the fixed engine, as evaluation/retrospective/run.py ran
them on v0.2.6. Same: SHAs (freeze.json), budget 300, wall cap 1200, --observe as each PR had it (sympy off),
--generate-dollars 0, ANTHROPIC_* dropped, verify when flips are counted, comment rendered quiet, suite.lock not taken (see main).
Differences, on purpose: the engine is a snapshot (ENGINE, on PYTHONPATH with -P, run by amu-venv-16ccd49's interpreter);
own worktrees (work-rescore/), output (out-rescore/), TMPDIR (tmp-rescore/); each PR starts from a copy of the index
cache as the retrospective left it (cache/<repo>/index), not saved back.

Usage: python rescore.py ENGINE_DIR AMU_REF
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, '/Users/Abslock128/Documents/ChatGPT/AMU/fix-base/evaluation/retrospective')
from run import worktree  # noqa: E402  the retrospective's own helper

ROOT = Path('/Users/Shared/amu-retro-2026-10-18')
FREEZE = Path('/Users/Abslock128/Documents/ChatGPT/AMU/fix-base/evaluation/retrospective/freeze.json')
TEN = [('django', 21820), ('django', 21805), ('django', 21736), ('sympy', 30540), ('sympy', 30572),
       ('django', 21853), ('django', 21714), ('django', 21659), ('sympy', 30515), ('sympy', 30477)]
AMU_PY = str(ROOT/'amu-venv-16ccd49'/'bin'/'python')


def environment(engine):
    tmp = ROOT/'tmp-rescore'
    tmp.mkdir(exist_ok=True)
    return dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, TMPDIR=str(tmp), PYTHONPATH=engine)


def one(name, pr, engine, ref):
    out = ROOT/'out-rescore'/name/str(pr['number'])
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    frozen = json.loads((ROOT/'out'/name/str(pr['number'])/'timing.json').read_text())
    observe = frozen.get('observe') is not False and name == 'django'
    work = ROOT/'work-rescore'/name/str(pr['number'])
    head, base = work/'head', work/'base'
    clone = ROOT/'repos'/name
    worktree(clone, head, pr['head_sha'])
    worktree(clone, base, pr['merge_base'])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    target = head/'.amu'/'index'
    shutil.rmtree(head/'.amu', ignore_errors=True)
    shutil.copytree(ROOT/'cache'/name/'index', target, symlinks=True)
    python = str(ROOT/'env'/name/'bin'/'python')
    env = environment(engine)
    command = [AMU_PY, '-P', '-m', 'amu.ci.run', '--base', pr['base_sha'], '--head', pr['head_sha'], '--out', str(out),
               '--python', python, '--pr', str(pr['number']), '--install-ok', 'true', '--amu-ref', ref,
               '--budget', '300', '--wall-cap', '1200', *(['--observe'] if observe else []), '--generate-dollars', '0']
    timing = dict(repo=name, number=pr['number'], observe=observe, engine=ref, started=time.time())
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=env)
    timing['run_seconds'] = round(time.monotonic() - began, 1)
    (out/'run.log').write_text(done.stdout + '\n--- stderr ---\n' + done.stderr)
    report = json.loads((out/'report.json').read_text())
    meta = json.loads((out/'meta.json').read_text())
    if report.get('predicted_failures'):
        verify = subprocess.run([AMU_PY, '-P', '-m', 'amu.ci.verify', '--dir', str(out), '--out', str(out/'verify'),
                                 '--python', python], cwd=base, capture_output=True, text=True, env=env)
        (out/'verify.log').write_text(verify.stdout + '\n--- stderr ---\n' + verify.stderr)
    code = ('import json, sys; from amu.ci.render import load, load_verify, render; '
            'r, m = load(sys.argv[1]); v = load_verify(sys.argv[2]); print(render(r, m, verify=v, quiet=True))')
    rendered = subprocess.run([AMU_PY, '-P', '-c', code, str(out), str(out/'verify')], capture_output=True, text=True, env=env)
    (out/'comment.md').write_text(rendered.stdout + (rendered.stderr if rendered.returncode else ''))
    timing['job_seconds'] = meta.get('seconds')
    result = dict(timing, status=meta.get('status'), flips=report.get('flips'), error=report.get('error'))
    (out/'done.json').write_text(json.dumps(result, indent=1))
    return result


def main():
    engine, ref = sys.argv[1], sys.argv[2]
    prs = json.loads(FREEZE.read_text())['prs']
    for name, number in TEN:
        pr = next(p for p in prs[name] if p['number'] == number)
        # suite.lock is left by the dead part 4 measurement (pid 6741); nothing else runs these suites, so it is not taken.
        print(json.dumps(one(name, pr, engine, ref)), flush=True)


if __name__ == '__main__':
    main()
