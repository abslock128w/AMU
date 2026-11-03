"""The suite recordings a nightly ci/amu-record.yml would have made for the 47 set: for each pull request's merge base,
the last commit of its history before 03:23 UTC on the merge base's day (the schedule's time), recorded with the
frozen engine. Usage: python record47.py --engine DIR --repos django sympy [--only N ...]
Writes suites/<repo>/<commit>.json.gz and suites/<repo>/log.jsonl."""
import argparse
import datetime as D
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

W = Path(os.environ.get('AMU_EVAL_WORK') or Path(__file__).resolve().parent)
FREEZE = Path(os.environ.get('AMU_EVAL_FREEZE') or Path(__file__).resolve().parents[1]/'performance-v042'/'freeze.json')
AMU_PY = os.environ.get('AMU_EVAL_PY') or str(W/'amuvenv'/'bin'/'python')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True).stdout.strip()


def nightly(clone, merge_base):
    when = D.datetime.fromtimestamp(int(git(clone, 'show', '-s', '--format=%ct', merge_base)), D.timezone.utc)
    cut = when.replace(hour=3, minute=23, second=0, microsecond=0)
    if cut > when:
        cut -= D.timedelta(days=1)
    return git(clone, 'rev-list', '-1', f'--before={cut.isoformat()}', merge_base)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--engine', required=True)
    p.add_argument('--repos', nargs='+', default=['django', 'sympy'])
    p.add_argument('--only', nargs='*', type=int)
    p.add_argument('--shards', type=int, default=4)
    a = p.parse_args()
    for repo in a.repos:
        clone = W/'repos'/repo
        out = W/'suites'/repo
        out.mkdir(parents=True, exist_ok=True)
        wanted = []
        for pr in sorted(json.loads(FREEZE.read_text())['prs'][repo], key=lambda pr: pr['number']):
            if a.only and pr['number'] not in a.only:
                continue
            wanted.append((pr['number'], pr['merge_base']))  # the freeze's merge base (was the v0.7.0 run's meta.json)
        commits = {}
        for pr, merge_base in wanted:
            commits.setdefault(nightly(clone, merge_base), []).append(pr)
        python = str(W/'env'/repo/'bin'/'python')
        runner = ['--runner', 'django-runtests'] if repo == 'django' else []
        for commit, prs in commits.items():
            target = out/f'{commit}.json.gz'
            if target.exists():
                continue
            tree = W/'recwork'/repo/commit[:10]
            git(clone, 'worktree', 'remove', '--force', str(tree))
            shutil.rmtree(tree, ignore_errors=True)
            tree.parent.mkdir(parents=True, exist_ok=True)
            git(clone, 'worktree', 'add', '--detach', '-q', str(tree), commit)
            env = dict({k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}, PYTHONPATH=str(a.engine))
            began = time.monotonic()
            # Shards side by side, as the workflow's job matrix runs them, then merged.
            shards = [tree.parent/f'{commit[:10]}-shard-{n}.json.gz' for n in range(a.shards)]
            running = [subprocess.Popen([AMU_PY, '-P', '-m', 'amu.ci.record_suite', '--repo', '.', '--python', python, *runner,
                                         '--shard', f'{n}/{a.shards}', '--out', str(shard)], cwd=tree, env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                       for n, shard in enumerate(shards)]
            said = [proc.communicate() for proc in running]
            done = subprocess.run([AMU_PY, '-P', '-m', 'amu.ci.record_suite', '--merge', *map(str, shards), '--out', str(target)],
                                  cwd=tree, capture_output=True, text=True, env=env)
            row = dict(repo=repo, commit=commit, prs=prs, seconds=round(time.monotonic() - began, 1), shards=a.shards,
                       shard_results=[(s[0].strip().splitlines() or [''])[-1] for s in said],
                       result=(done.stdout.strip().splitlines() or [''])[-1], error=done.stderr[-2000:] if done.returncode else '')
            for shard in shards:
                shard.unlink(missing_ok=True)
            with open(out/'log.jsonl', 'a') as handle:
                handle.write(json.dumps(row) + '\n')
            print(json.dumps(row), flush=True)
            git(clone, 'worktree', 'remove', '--force', str(tree))
    return 0


if __name__ == '__main__':
    sys.exit(main())
