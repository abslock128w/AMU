"""Freeze the last 50 merged pull requests of Django and sympy that touch production Python (brief 5, piece 1).

Usage: python evaluation/retrospective/freeze.py --clones /Users/Shared/amu-retro-2026-10-18/repos [--out freeze.json]

Read-only GitHub API through ``gh``; git only fetches each pull request's head into the local clone and computes the
merge base the Action would compare against: ``git merge-base <base.sha> <head.sha>``, where base.sha is the base
branch tip GitHub recorded for the pull request (the Action's ``github.event.pull_request.base.sha``).

Selection, decided before any pull request was looked at:
- merged into the default branch (Django ``main``, sympy ``master``) before ``CUTOFF``;
- ordered by merge time, newest first; the first 50 whose changed files include production Python are kept;
- production Python: a ``.py`` file under the package (``django/``, ``sympy/``) and not in a ``tests`` or
  ``benchmarks`` directory, whatever its status (added, modified, removed, renamed);
- test files the pull request adds or edits: ``.py`` files under ``tests/`` (Django) or in a ``tests`` directory
  (sympy), with status added, modified or renamed.

The output is pinned: ``sha256`` is the hash of the canonical JSON of ``prs`` (sorted keys, no spaces), and the same
hash is written to ``freeze.sha256``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CUTOFF = '2026-10-18T00:00:00Z'
WINDOW = '2026-06-01..2026-10-17'
COUNT = 50
REPOS = {
    'django': dict(slug='django/django', branch='main', package='django/', tests=lambda p: p.startswith('tests/')),
    'sympy': dict(slug='sympy/sympy', branch='master', package='sympy/', tests=lambda p: '/tests/' in p),
}
QUERY = '''query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number title mergedAt baseRefName baseRefOid headRefOid mergeCommit { oid } } }
  }
}'''


def gh(*args):
    done = subprocess.run(['gh', *args], capture_output=True, text=True, check=True)
    return done.stdout


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def merged(slug, branch):
    found, after = [], None
    while True:
        args = ['api', 'graphql', '-f', f'query={QUERY}', '-f', f'q=repo:{slug} is:pr is:merged base:{branch} merged:{WINDOW}']
        if after:
            args += ['-f', f'after={after}']
        page = json.loads(gh(*args))['data']['search']
        found += [n for n in page['nodes'] if n and n.get('mergedAt') and n['mergedAt'] < CUTOFF]
        if not page['pageInfo']['hasNextPage']:
            break
        after = page['pageInfo']['endCursor']
    return sorted(found, key=lambda n: (n['mergedAt'], n['number']), reverse=True)


def is_production(spec, path):
    parts = path.split('/')
    return (path.endswith('.py') and path.startswith(spec['package']) and 'tests' not in parts[:-1]
            and 'benchmarks' not in parts[:-1])


def files(slug, number):
    rows = []
    for line in gh('api', '--paginate', f'repos/{slug}/pulls/{number}/files', '--jq',
                   '.[] | {filename, status, previous_filename}').splitlines():
        rows.append(json.loads(line))
    return rows


def freeze(clones):
    out, scanned = {}, {}
    for name, spec in REPOS.items():
        repo = Path(clones)/name
        kept, skipped = [], []
        for node in merged(spec['slug'], spec['branch']):
            if len(kept) == COUNT:
                break
            changed = files(spec['slug'], node['number'])
            paths = {row['filename'] for row in changed} | {row['previous_filename'] for row in changed if row.get('previous_filename')}
            production = sorted(p for p in paths if is_production(spec, p))
            if not production:
                skipped.append(node['number'])
                continue
            tests = sorted(row['filename'] for row in changed if row['filename'].endswith('.py') and spec['tests'](row['filename'])
                           and row['status'] in ('added', 'modified', 'renamed'))
            git(repo, 'fetch', '--quiet', 'origin', f"+refs/pull/{node['number']}/head:refs/pull/{node['number']}/head")
            head = git(repo, 'rev-parse', f"refs/pull/{node['number']}/head")
            if head != node['headRefOid']:
                raise SystemExit(f"{name} #{node['number']}: fetched head {head} is not the API's {node['headRefOid']}")
            git(repo, 'cat-file', '-e', node['baseRefOid'])
            base = git(repo, 'merge-base', node['baseRefOid'], head)
            kept.append(dict(number=node['number'], title=node['title'], merged_at=node['mergedAt'], base_sha=node['baseRefOid'],
                             head_sha=head, merge_base=base, merge_commit=(node.get('mergeCommit') or {}).get('oid'),
                             production_files=production, test_files=tests, files=len(changed)))
            print(f"{name} #{node['number']}: {len(production)} production, {len(tests)} test files", file=sys.stderr)
        if len(kept) < COUNT:
            raise SystemExit(f'{name}: only {len(kept)} pull requests in the window')
        out[name] = kept
        scanned[name] = dict(merged_scanned=len(kept) + len(skipped), skipped_no_production_python=skipped)
    return out, scanned


def pin(prs):
    return hashlib.sha256(json.dumps(prs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--clones', required=True)
    p.add_argument('--out', default=str(Path(__file__).with_name('freeze.json')))
    a = p.parse_args(argv)
    prs, scanned = freeze(a.clones)
    digest = pin(prs)
    doc = dict(v=1, written='2026-10-18', cutoff=CUTOFF, window=WINDOW, count=COUNT,
               selection=__doc__.split('Selection, decided before any pull request was looked at:\n', 1)[1].split('\n\n')[0],
               scanned=scanned, sha256=digest, prs=prs)
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    Path(a.out).with_name('freeze.sha256').write_text(f'{digest}  prs in freeze.json\n')
    print(digest)


def check(path=None):
    """Raise unless freeze.json's prs still hash to its pinned sha256 and to freeze.sha256."""
    path = Path(path or Path(__file__).with_name('freeze.json'))
    doc = json.loads(path.read_text())
    digest = pin(doc['prs'])
    pinned = path.with_name('freeze.sha256').read_text().split()[0]
    if not digest == doc['sha256'] == pinned:
        raise SystemExit(f'freeze.json does not match its pin: {digest} {doc["sha256"]} {pinned}')
    return doc


if __name__ == '__main__':
    main()
