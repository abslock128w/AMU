"""Freeze the pull requests for the v0.4.2 performance test (fresh pull requests only).

Usage: python evaluation/performance-v042/freeze.py --clones /Users/Shared/amu-retro-2026-10-18/repos [--out freeze.json]

Selection, decided before any pull request was looked at:
- Django and sympy: merged into the default branch (Django ``main``, sympy ``master``) at or after ``AFTER``, the end
  of the Brief 5 freeze window (its cutoff), and before the moment this script runs; ordered by merge time, newest
  first; the first 20 whose changed files include production Python are kept;
- Flask: merged into ``main``, any date (Flask had no earlier window); ordered by merge time, newest first; the first
  10 whose changed files include production Python are kept;
- production Python: a ``.py`` file under the package (``django/``, ``sympy/``, ``src/flask/``) and not in a ``tests``
  or ``benchmarks`` directory, whatever its status (added, modified, removed, renamed);
- test files the pull request adds or edits: ``.py`` files under ``tests/`` (Django, Flask) or in a ``tests``
  directory (sympy), with status added, modified or renamed.

Read-only GitHub API through ``gh``; git fetches each pull request's head into the local clone and computes the merge
base the Action compares against: ``git merge-base <base.sha> <head.sha>``. The output is pinned like Brief 5's:
``sha256`` over the canonical JSON of ``prs``, also written to ``freeze.sha256``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

AFTER = '2026-10-18T00:00:00Z'
REPOS = {
    'django': dict(slug='django/django', branch='main', package='django/', tests=lambda p: p.startswith('tests/'), count=20,
                   since=AFTER),
    'sympy': dict(slug='sympy/sympy', branch='master', package='sympy/', tests=lambda p: '/tests/' in p, count=20, since=AFTER),
    'flask': dict(slug='pallets/flask', branch='main', package='src/flask/', tests=lambda p: p.startswith('tests/'), count=10,
                  since=None),
}
QUERY = '''query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number title mergedAt baseRefName baseRefOid headRefOid mergeCommit { oid } } }
  }
}'''


def gh(*args):
    for attempt in range(4):
        done = subprocess.run(['gh', *args], capture_output=True, text=True)
        if done.returncode == 0:
            return done.stdout
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f'gh {args[:3]} failed: {done.stderr[:300]}')


def git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def merged(spec, until):
    window = f"{spec['since'][:10]}..{until[:10]}" if spec['since'] else f"<={until[:10]}"
    found, after = [], None
    while True:
        args = ['api', 'graphql', '-f', f'query={QUERY}', '-f',
                f"q=repo:{spec['slug']} is:pr is:merged base:{spec['branch']} merged:{window} sort:updated-desc"]
        if after:
            args += ['-f', f'after={after}']
        page = json.loads(gh(*args))['data']['search']
        found += [n for n in page['nodes'] if n and n.get('mergedAt') and n['mergedAt'] < until
                  and (spec['since'] is None or n['mergedAt'] >= spec['since'])]
        if not page['pageInfo']['hasNextPage'] or (spec['since'] is None and len(found) >= 200):
            break
        after = page['pageInfo']['endCursor']
    return sorted(found, key=lambda n: (n['mergedAt'], n['number']), reverse=True)


def is_production(spec, path):
    parts = path.split('/')
    return (path.endswith('.py') and path.startswith(spec['package']) and 'tests' not in parts[:-1]
            and 'benchmarks' not in parts[:-1])


def files(slug, number):
    return [json.loads(line) for line in gh('api', '--paginate', f'repos/{slug}/pulls/{number}/files', '--jq',
                                            '.[] | {filename, status, previous_filename}').splitlines()]


def freeze(clones, until):
    out, scanned = {}, {}
    for name, spec in REPOS.items():
        repo = Path(clones)/name
        kept, skipped = [], []
        for node in merged(spec, until):
            if len(kept) == spec['count']:
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
            try:
                git(repo, 'cat-file', '-e', node['baseRefOid'])
            except subprocess.CalledProcessError:
                git(repo, 'fetch', '--quiet', 'origin', node['baseRefOid'])
            base = git(repo, 'merge-base', node['baseRefOid'], head)
            kept.append(dict(number=node['number'], title=node['title'], merged_at=node['mergedAt'], base_sha=node['baseRefOid'],
                             head_sha=head, merge_base=base, merge_commit=(node.get('mergeCommit') or {}).get('oid'),
                             production_files=production, test_files=tests, files=len(changed)))
            print(f"{name} #{node['number']}: {len(production)} production, {len(tests)} test files", file=sys.stderr, flush=True)
        out[name] = kept
        scanned[name] = dict(merged_scanned=len(kept) + len(skipped), skipped_no_production_python=skipped,
                             short=len(kept) < spec['count'])
    return out, scanned


def pin(prs):
    return hashlib.sha256(json.dumps(prs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--clones', required=True)
    p.add_argument('--out', default=str(Path(__file__).with_name('freeze.json')))
    a = p.parse_args(argv)
    until = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    prs, scanned = freeze(a.clones, until)
    digest = pin(prs)
    doc = dict(v=1, written=until, after=AFTER, until=until, counts={k: v['count'] for k, v in REPOS.items()},
               selection=__doc__.split('Selection, decided before any pull request was looked at:\n', 1)[1].split('\n\n')[0],
               scanned=scanned, sha256=digest, prs=prs)
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + '\n')
    Path(a.out).with_name('freeze.sha256').write_text(f'{digest}  prs in freeze.json\n')
    print(digest)


def check(path=None):
    path = Path(path or Path(__file__).with_name('freeze.json'))
    doc = json.loads(path.read_text())
    digest = pin(doc['prs'])
    pinned = path.with_name('freeze.sha256').read_text().split()[0]
    if not digest == doc['sha256'] == pinned:
        raise SystemExit(f'freeze.json does not match its pin: {digest} {doc["sha256"]} {pinned}')
    return doc


if __name__ == '__main__':
    main()
