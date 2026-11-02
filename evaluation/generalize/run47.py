"""Run the frozen 47 pull requests with an AMU engine checkout as the Action runs it, then the raise measure.

Usage: python run47.py --engine DIR --tag TAG [--extras E] [--only repo#n ...] [--no-raise]
Outputs under out-TAG/<repo>/<n>/: report.json, meta.json, patch.diff, comment.md, raise.json, timing.json.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

W = Path(__file__).resolve().parent
FREEZE = Path('/Users/Abslock128/Documents/ChatGPT/AMU/fix-base/evaluation/performance-v042/freeze.json')
AMU_PY = str(W/'amuvenv'/'bin'/'python')
WORKERS = {'django': 4, 'sympy': 4, 'flask': 3, 'attrs': 3}


def env_for(name, head):
    if name == 'attrs':
        return None
    if name != 'flask':
        return name
    lock = (head/'uv.lock').read_text() if (head/'uv.lock').exists() else ''
    lines = lock.splitlines()
    pin = next((line.split('"')[1] for prev, line in zip(lines, lines[1:]) if prev == 'name = "pytest"'), '')
    major_minor = tuple(int(p) for p in pin.split('.')[:2]) if pin else (99, 0)
    return 'flask-p90' if major_minor < (9, 1) else 'flask'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)


def one(engine, tag, name, pr, extras, measure):
    out = W/f'out-{tag}'/name/str(pr['number'])
    if (out/'timing.json').exists():
        return json.loads((out/'timing.json').read_text())
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    clone = W/'attrs' if name == 'attrs' else W/'repos'/name
    head = W/f'work-{tag}'/name/str(pr['number'])
    git(clone, 'worktree', 'remove', '--force', str(head))
    shutil.rmtree(head, ignore_errors=True)
    head.parent.mkdir(parents=True, exist_ok=True)
    if git(clone, 'cat-file', '-e', pr['head_sha']).returncode:
        git(clone, 'fetch', '-q', 'origin', pr['head_sha'])
    git(clone, 'worktree', 'add', '--detach', '-q', str(head), pr['head_sha'])
    python = str(W/'attrsvenv'/'bin'/'python') if name == 'attrs' else str(W/'env'/env_for(name, head)/'bin'/'python')
    env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, PYTHONPATH=str(engine))
    command = [AMU_PY, '-P', '-m', 'amu.ci.run', '--base', pr['base_sha'], '--head', pr['head_sha'], '--out', str(out),
               '--python', python, '--pr', str(pr['number']), '--install-ok', 'true', '--amu-ref', tag,
               '--budget', '300', '--wall-cap', '1200', '--generate-dollars', '0', '--extras', extras]
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=env)
    seconds = round(time.monotonic() - began, 1)
    (out/'run.log').write_text(done.stdout[-20000:] + done.stderr[-20000:])
    comment = subprocess.run([AMU_PY, '-P', '-m', 'amu.ci.render', str(out)], capture_output=True, text=True, env=env).stdout
    (out/'comment.md').write_text(comment)
    timing = dict(repo=name, number=pr['number'], seconds=seconds, first=(comment.splitlines() or [''])[0])
    if measure and (out/'report.json').exists() and (out/'patch.diff').exists():
        runner = 'django-runtests' if name == 'django' else 'pytest'
        raised = subprocess.run([sys.executable, str(engine/'evaluation'/'generalize'/'raise_missed.py'), '--head', str(head),
                                 '--patch', str(out/'patch.diff'), '--report', str(out/'report.json'), '--python', python,
                                 '--runner', runner, '--workers', str(WORKERS[name])], capture_output=True, text=True)
        (out/'raise.json').write_text(raised.stdout or json.dumps(dict(error=raised.stderr[-3000:])))
    git(clone, 'worktree', 'remove', '--force', str(head))
    (out/'timing.json').write_text(json.dumps(timing))
    return timing


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--engine', required=True)
    p.add_argument('--tag', required=True)
    p.add_argument('--extras', default='')
    p.add_argument('--only', nargs='*')
    p.add_argument('--no-raise', action='store_true')
    p.add_argument('--set', default='47', choices=('47', 'attrs'))
    a = p.parse_args()
    prs = json.loads(FREEZE.read_text())['prs'] if a.set == '47' else dict(attrs=json.loads((W/'attrs15.json').read_text()))
    for name in (('flask', 'django', 'sympy') if a.set == '47' else ('attrs',)):
        for pr in prs[name]:
            if a.only and f"{name}#{pr['number']}" not in a.only:
                continue
            timing = one(Path(a.engine), a.tag, name, pr, a.extras, not a.no_raise)
            print(json.dumps(timing), flush=True)


if __name__ == '__main__':
    main()
