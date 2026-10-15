"""Post the rendered report as one comment per pull request: find it by its marker, edit it, or create it.

Usage: python -m amu.ci.post --dir DIR --repo OWNER/NAME --pr N    (token in $GITHUB_TOKEN)

Only a comment that carries the marker and was written by the posting account (by default github-actions[bot]) is
edited. A marker copied into someone else's comment is ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .render import MARKER, load, render

API = 'https://api.github.com'
AUTHOR = 'github-actions[bot]'


class GitHub:
    def __init__(self, token, api=API):
        self.token, self.api = token, api.rstrip('/')

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.api + path, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'amu-ci'})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read() or b'null')

    def pages(self, path):
        page = 1
        while True:
            batch = self.request('GET', f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
            yield from batch
            if len(batch) < 100:
                return
            page += 1


def ours(comment, author=AUTHOR):
    return MARKER in (comment.get('body') or '') and (comment.get('user') or {}).get('login') == author


def upsert(github, repo, pr, body, author=AUTHOR):
    """Edit the existing comment or create one. Returns ('edited' | 'created', comment id)."""
    existing = next((c for c in github.pages(f'/repos/{repo}/issues/{pr}/comments') if ours(c, author)), None)
    if existing:
        github.request('PATCH', f"/repos/{repo}/issues/comments/{existing['id']}", dict(body=body))
        return 'edited', existing['id']
    created = github.request('POST', f'/repos/{repo}/issues/{pr}/comments', dict(body=body))
    return 'created', created['id']


def main(argv=None):
    p = argparse.ArgumentParser(prog='amu ci post', description=__doc__.split('\n')[0])
    p.add_argument('--dir', required=True, help='folder with report.json and meta.json')
    p.add_argument('--repo', required=True)
    p.add_argument('--pr', type=int, required=True)
    p.add_argument('--author', default=AUTHOR, help='login whose marked comment is edited')
    a = p.parse_args(argv)
    report, meta = load(a.dir)
    try:
        action, ident = upsert(GitHub(os.environ['GITHUB_TOKEN']), a.repo, a.pr, render(report, meta), a.author)
        print(f'amu: comment {ident} {action} on #{a.pr}')
    except (KeyError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f'amu: could not post the comment: {type(exc).__name__}: {exc}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
