"""Run the frozen pull requests one at a time as the v0.4.2 Action would, and save the comment and JSON per pull request.

Usage: python evaluation/performance-v042/run.py [--only REPO#N ...]

As the Action at v0.4.2: merge base against head, budget 300 s per side, wall cap 1200 s, recording off, no generated
tests (no key), extras as released (none; rename labels are on by default), the fresh-job flip rerun (amu.ci.verify on
the merge base) when flips are counted, the comment rendered quiet. The engine is `git archive v0.4.2` (ENGINE), run by
the retrospective's AMU interpreter with -P and the engine first on PYTHONPATH. Each repository's index cache is
carried from one pull request to the next, as the Action's cache would be on a busy repository (Django and sympy start
from the Brief 5 cache; Flask starts cold). One pull request at a time; ANTHROPIC_* dropped.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'retrospective'))
from run import worktree  # noqa: E402  the retrospective's helper
from freeze import check  # noqa: E402

ROOT = Path('/Users/Shared/amu-retro-2026-10-18')
ENGINE = ROOT/'engine-v042'
AMU_PY = str(ROOT/'amu-venv-16ccd49'/'bin'/'python')
ORDER = ('django', 'sympy', 'flask')


def environment():
    tmp = ROOT/'tmp-v042'
    tmp.mkdir(exist_ok=True)
    return dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, TMPDIR=str(tmp),
                PYTHONPATH=str(ENGINE))


def environment_name(name, head):
    """The interpreter the project's own install would give: Flask pins pytest in uv.lock, and its conftest used
    pytest's private ``monkeypatch.notset`` until pytest 9.1 removed it; such a pull request gets env/flask-p90
    (pytest 9.0.2), the rest env/flask (pytest 9.2). Django and sympy use their retrospective environments."""
    if name != 'flask':
        return name
    lock = (head/'uv.lock').read_text() if (head/'uv.lock').exists() else ''
    pin = next((line.split('"')[1] for prev, line in zip(lock.splitlines(), lock.splitlines()[1:])
                if prev == 'name = "pytest"'), '')
    major_minor = tuple(int(p) for p in pin.split('.')[:2]) if pin else (99, 0)
    return 'flask-p90' if major_minor < (9, 1) else 'flask'


def one(name, pr):
    out = ROOT/'out-v042'/name/str(pr['number'])
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    work = ROOT/'work-v042'/name/str(pr['number'])
    head, base = work/'head', work/'base'
    clone = ROOT/'repos'/name
    worktree(clone, head, pr['head_sha'])
    worktree(clone, base, pr['merge_base'])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cache = ROOT/'cache-v042'/name/'index'
    if not cache.is_dir() and (ROOT/'cache'/name/'index').is_dir():
        shutil.copytree(ROOT/'cache'/name/'index', cache, symlinks=True)
    target = head/'.amu'/'index'
    shutil.rmtree(head/'.amu', ignore_errors=True)
    if cache.is_dir():
        shutil.copytree(cache, target, symlinks=True)
    python = str(ROOT/'env'/environment_name(name, head)/'bin'/'python')
    env = environment()
    command = [AMU_PY, '-P', '-m', 'amu.ci.run', '--base', pr['base_sha'], '--head', pr['head_sha'], '--out', str(out),
               '--python', python, '--pr', str(pr['number']), '--install-ok', 'true', '--amu-ref', 'v0.4.2',
               '--budget', '300', '--wall-cap', '1200', '--generate-dollars', '0']
    timing = dict(repo=name, number=pr['number'], engine='v0.4.2', started=time.time(), cache_restored=cache.is_dir())
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=env)
    timing['run_seconds'] = round(time.monotonic() - began, 1)
    (out/'run.log').write_text(done.stdout + '\n--- stderr ---\n' + done.stderr)
    if target.is_dir():
        shutil.rmtree(cache, ignore_errors=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, cache, symlinks=True)
    report = json.loads((out/'report.json').read_text())
    meta = json.loads((out/'meta.json').read_text())
    if report.get('predicted_failures'):
        began = time.monotonic()
        verify = subprocess.run([AMU_PY, '-P', '-m', 'amu.ci.verify', '--dir', str(out), '--out', str(out/'verify'),
                                 '--python', python], cwd=base, capture_output=True, text=True, env=env)
        timing['verify_seconds'] = round(time.monotonic() - began, 1)
        (out/'verify.log').write_text(verify.stdout + '\n--- stderr ---\n' + verify.stderr)
    code = ('import json, sys; from amu.ci.render import load, load_verify, render; '
            'r, m = load(sys.argv[1]); v = load_verify(sys.argv[2]); print(render(r, m, verify=v, quiet=True))')
    rendered = subprocess.run([AMU_PY, '-P', '-c', code, str(out), str(out/'verify')], capture_output=True, text=True, env=env)
    (out/'comment.md').write_text(rendered.stdout if rendered.returncode == 0 else rendered.stdout + rendered.stderr)
    timing['job_seconds'] = meta.get('seconds')
    result = dict(timing, status=meta.get('status'), flips=report.get('flips'), error=report.get('error'),
                  first_line=(rendered.stdout.splitlines() or [''])[0])
    (out/'done.json').write_text(json.dumps(result, indent=1))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--only', nargs='+', default=[])
    a = p.parse_args(argv)
    doc = check(HERE/'freeze.json')
    for name in ORDER:
        for pr in sorted(doc['prs'][name], key=lambda r: (r['merged_at'], r['number'])):
            if a.only and f"{name}#{pr['number']}" not in a.only:
                continue
            print(json.dumps({k: v for k, v in one(name, pr).items() if k != 'started'}), flush=True)


if __name__ == '__main__':
    main()
