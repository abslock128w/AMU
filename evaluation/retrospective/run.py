"""Run AMU on every frozen pull request as the Action would, locally, one at a time (brief 5, piece 2; PROTOCOL.md).

Usage: python evaluation/retrospective/run.py --root /Users/Shared/amu-retro-2026-10-18 [--repo django|sympy] [--only N ...]

For each pull request of freeze.json, oldest merge first per repository: worktrees at the head and at the merge base;
``.amu/index`` restored from the previous pull request's; the Action's run step (``amu.ci.run``) from the frozen v0.2.6
install; the cache saved back; the verify job (``amu.ci.verify``) on the merge-base worktree when flips were counted;
the comment rendered as the posting job renders it. Results go to ROOT/out/<repo>/<number>/ and a ``done.json`` marks a
pull request finished, so a restart resumes where it stopped and never runs a finished pull request again.

Only this script and probe.py run suites, and both hold ROOT/suite.lock while they do.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from freeze import check  # noqa: E402

BUDGET, WALL_CAP, AMU_REF = 300, 1200, 'v0.2.6'


def tree_hash(folder):
    digest = hashlib.sha256()
    for path in sorted(Path(folder).rglob('*.py')):
        digest.update(str(path.relative_to(folder)).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


@contextlib.contextmanager
def suite_lock(root, who):
    lock = Path(root)/'suite.lock'
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(5)
    (lock/'holder').write_text(f'{who} {os.getpid()} {time.time():.0f}\n')
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def git(repo, *args, check=True):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=check).stdout.strip()


def worktree(clone, where, sha):
    if (where/'.git').exists():
        if git(where, 'rev-parse', 'HEAD') == sha:
            return
        git(clone, 'worktree', 'remove', '--force', str(where), check=False)
    shutil.rmtree(where, ignore_errors=True)
    where.parent.mkdir(parents=True, exist_ok=True)
    git(clone, 'worktree', 'add', '--detach', '--quiet', str(where), sha)


def amu_python(root):
    return str(Path(root)/'amu-venv'/'bin'/'python')


def environment(root):
    tmp = Path(root)/'tmp'
    tmp.mkdir(exist_ok=True)
    return dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, TMPDIR=str(tmp))


def render(root, out):
    code = ('import json, sys; from amu.ci.render import load, load_verify, render; '
            'r, m = load(sys.argv[1]); v = load_verify(sys.argv[2]); print(render(r, m, verify=v, quiet=True))')
    done = subprocess.run([amu_python(root), '-P', '-c', code, str(out), str(out/'verify')], capture_output=True, text=True,
                          env=environment(root))
    (out/'comment.md').write_text(done.stdout)
    return done.returncode


def one(root, name, pr, engine):
    root = Path(root)
    clone, out = root/'repos'/name, root/'out'/name/str(pr['number'])
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    if tree_hash(engine) != (root/'engine.sha256').read_text().split()[0]:
        raise SystemExit('the installed AMU changed since the run started; stopping')
    work = root/'work'/name/str(pr['number'])
    head, base = work/'head', work/'base'
    worktree(clone, head, pr['head_sha'])
    worktree(clone, base, pr['merge_base'])
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cache = root/'cache'/name/'index'
    target = head/'.amu'/'index'
    shutil.rmtree(head/'.amu', ignore_errors=True)
    restored = cache.is_dir()
    if restored:
        shutil.copytree(cache, target, symlinks=True)
    python = str(root/'env'/name/'bin'/'python')
    command = [amu_python(root), '-P', '-m', 'amu.ci.run', '--base', pr['base_sha'], '--head', pr['head_sha'], '--out', str(out),
               '--python', python, '--pr', str(pr['number']), '--install-ok', 'true', '--amu-ref', AMU_REF,
               '--budget', str(BUDGET), '--wall-cap', str(WALL_CAP), '--observe', '--generate-dollars', '0']
    timing = dict(repo=name, number=pr['number'], cache_restored=restored, started=time.time())
    began = time.monotonic()
    done = subprocess.run(command, cwd=head, capture_output=True, text=True, env=environment(root))
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
        verify = subprocess.run([amu_python(root), '-P', '-m', 'amu.ci.verify', '--dir', str(out), '--out', str(out/'verify'),
                                 '--python', python], cwd=base, capture_output=True, text=True, env=environment(root))
        timing['verify_seconds'] = round(time.monotonic() - began, 1)
        (out/'verify.log').write_text(verify.stdout + '\n--- stderr ---\n' + verify.stderr)
    timing['render_status'] = render(root, out)
    timing['job_seconds'] = meta.get('seconds')
    timing['finished'] = time.time()
    (out/'timing.json').write_text(json.dumps(timing, indent=1))
    result = dict(timing, status=meta.get('status'), flips=report.get('flips'), error=report.get('error'))
    (out/'done.json').write_text(json.dumps(result, indent=1))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', required=True)
    p.add_argument('--repo', choices=['django', 'sympy'], action='append')
    p.add_argument('--only', type=int, nargs='+')
    a = p.parse_args(argv)
    root = Path(a.root)
    doc = check()
    engine = Path(subprocess.check_output([amu_python(root), '-P', '-c', 'import amu, os; print(os.path.dirname(amu.__file__))'],
                                          text=True).strip())
    pinned = root/'engine.sha256'
    if not pinned.exists():
        pinned.write_text(f'{tree_hash(engine)}  {engine}\n')
    for name in a.repo or ['django', 'sympy']:
        for pr in sorted(doc['prs'][name], key=lambda r: (r['merged_at'], r['number'])):
            if a.only and pr['number'] not in a.only:
                continue
            with suite_lock(root, f"run {name} {pr['number']}"):
                result = one(root, name, pr, engine)
            print(json.dumps({k: result.get(k) for k in ('repo', 'number', 'status', 'flips', 'job_seconds', 'error')}), flush=True)


if __name__ == '__main__':
    main()
