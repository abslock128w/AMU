"""Replay upstream pull requests as pull requests on a fork, with the AMU workflows installed per the install note.

Usage: python replay.py --clone DIR --fork owner/name --list FILE.tsv --install CMD --prefix amu6 --upstream NAME
                        [--main] [--open]
FILE.tsv rows: number, merge base, head sha, title. Workflows come from ../rel/ci (the release's templates) with both
install lines set to CMD. --main commits them to the fork's default branch (the posting workflow runs only from
there). Each base branch is <prefix>-base-<merge base[:10]> = merge base + one commit adding the workflows; each head
branch is amu-pr-<n> = the pull request's own head commit. --open opens the pull requests through the REST API and
prints number, upstream number, created_at. Bodies name the upstream pull request without # or a link.
"""
import argparse
import json
import subprocess
from pathlib import Path

W = Path(__file__).resolve().parent
TRAILER = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'


def git(clone, *args, check=True):
    done = subprocess.run(['git', '-C', str(clone), *args], capture_output=True, text=True)
    if check and done.returncode:
        raise SystemExit(f'git {" ".join(args)}: {done.stderr}')
    return done.stdout.strip()


def workflows(install):
    out = {}
    for name in ('amu-pr.yml', 'amu-pr-comment.yml'):
        text = (W/'rel'/'ci'/name).read_text()
        if name == 'amu-pr.yml':
            text = text.replace('install: python -m pip install -e .', 'install: ' + install)
            assert text.count('install: ' + install) == 2, 'both install lines'
        out[name] = text
    return out


def commit_on(clone, start, files, message, branch):
    work = W/'replay-work'
    git(clone, 'worktree', 'remove', '--force', str(work), check=False)
    git(clone, 'worktree', 'add', '-q', '--detach', str(work), start)
    folder = work/'.github'/'workflows'
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder/name).write_text(text)
    git(work, 'add', '.github/workflows/amu-pr.yml', '.github/workflows/amu-pr-comment.yml')
    git(work, 'commit', '-qm', message, '-m', TRAILER)  # the committer is git's configured identity
    git(work, 'branch', '-f', branch, 'HEAD')
    git(clone, 'worktree', 'remove', '--force', str(work))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--clone', required=True)
    p.add_argument('--fork', required=True)
    p.add_argument('--list', required=True)
    p.add_argument('--install', required=True)
    p.add_argument('--prefix', required=True)
    p.add_argument('--upstream', required=True)
    p.add_argument('--main', action='store_true')
    p.add_argument('--open', action='store_true')
    a = p.parse_args()
    clone, files = Path(a.clone), workflows(a.install)
    rows = [line.rstrip('\n').split('\t') for line in Path(a.list).read_text().splitlines() if line.strip()]
    git(clone, 'fetch', '-q', 'origin')
    if a.main:
        default = json.loads(subprocess.check_output(['gh', 'api', f'repos/{a.fork}', '--jq', '{b: .default_branch}']))['b']
        git(clone, 'fetch', '-q', 'fork', default, check=False)
        start = f'fork/{default}' if git(clone, 'rev-parse', '-q', '--verify', f'fork/{default}', check=False) else f'origin/{default}'
        commit_on(clone, start, files, 'Install the AMU pull request workflows (AMU v0.6.0) for a replay pilot on this fork',
                  'replay-main')
        git(clone, 'push', '-q', 'fork', f'replay-main:refs/heads/{default}')
    refs = []
    for number, base, head, *_ in rows:
        branch = f'{a.prefix}-base-{base[:10]}'
        commit_on(clone, base, files, f'Add the AMU pull request workflows (AMU v0.6.0) at merge base {base[:10]}', branch)
        git(clone, 'branch', '-f', f'amu-pr-{number}', head)
        refs += [f'refs/heads/{branch}:refs/heads/{branch}', f'refs/heads/amu-pr-{number}:refs/heads/amu-pr-{number}']
    git(clone, 'push', '-q', 'fork', *sorted(set(refs)))
    if a.open:
        for number, base, head, title, *_ in rows:
            body = (f'Replay of upstream {a.upstream} pull request {number}, for an AMU v0.6.0 pilot on this fork. Base: the '
                    f'upstream merge base {base[:10]} plus one commit adding the AMU workflows. Head: the pull request\'s own '
                    'commits. Not for merging.')
            made = json.loads(subprocess.check_output(['gh', 'api', f'repos/{a.fork}/pulls', '-f', f'title=Replay {number} (v0.6.0): {title.replace("#", "")}',
                                                       '-f', f'head=amu-pr-{number}', '-f', f'base={a.prefix}-base-{base[:10]}',
                                                       '-f', f'body={body}', '--jq', '{n: .number, at: .created_at}']))
            print(made['n'], number, made['at'], flush=True)


if __name__ == '__main__':
    main()
