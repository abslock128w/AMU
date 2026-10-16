"""Post the rendered report as one comment per pull request: find it by its marker, edit it, or create it.

Usage: python -m amu.ci.post --dir DIR --repo OWNER/NAME (--event $GITHUB_EVENT_PATH | --pr N)    (token in $GITHUB_TOKEN)

Runs in a ``workflow_run`` job that never checks out or executes the pull request's code. The artifact it reads was
written by that code, so it is treated as data: the pull request number and head SHA come from the trusted
``workflow_run`` event and the API, never from the artifact, and the JSON only feeds the renderer. A run that is not
for the pull request's current head (an older push finishing late) posts nothing, so it cannot overwrite a newer
comment.

Only a comment that carries the marker and was written by the posting account (by default github-actions[bot]) is
edited. A marker copied into someone else's comment is ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import counters
from .render import MARKER, load, load_verify, render

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
    """Edit the existing comment or create one. ``body`` is text, or a function of the previous comment's text.
    Returns ('edited' | 'created', comment id)."""
    existing = next((c for c in github.pages(f'/repos/{repo}/issues/{pr}/comments') if ours(c, author)), None)
    if callable(body):
        body = body(existing.get('body') if existing else None)
    if existing:
        github.request('PATCH', f"/repos/{repo}/issues/comments/{existing['id']}", dict(body=body))
        return 'edited', existing['id']
    created = github.request('POST', f'/repos/{repo}/issues/{pr}/comments', dict(body=body))
    return 'created', created['id']


def resolve(github, repo, event):
    """(pull request number, head SHA) for a workflow_run event, or (None, reason). Only open pull requests whose
    current head is the run's head count."""
    run = event.get('workflow_run') or {}
    if run.get('event') != 'pull_request':
        return None, f"the run was triggered by {run.get('event')!r}, not a pull request"
    if run.get('conclusion') == 'cancelled':
        return None, 'the run was cancelled, most likely by a newer push'
    head = run.get('head_sha')
    numbers = [pr.get('number') for pr in run.get('pull_requests') or []]
    if not numbers:
        # A fork's pull request is not listed on the run; find it by the head repository's owner and branch.
        owner = ((run.get('head_repository') or {}).get('owner') or {}).get('login')
        branch = run.get('head_branch')
        if not owner or not branch:
            return None, 'the run names no head repository or branch'
        query = urllib.parse.urlencode(dict(state='open', head=f'{owner}:{branch}'))
        numbers = [pr['number'] for pr in github.request('GET', f'/repos/{repo}/pulls?{query}') or []]
    for number in numbers:
        pr = github.request('GET', f'/repos/{repo}/pulls/{number}')
        if pr.get('state') == 'open' and (pr.get('head') or {}).get('sha') == head:
            return number, head
    return None, f'no open pull request has {head} as its head; a newer push may have replaced it'


def main(argv=None):
    p = argparse.ArgumentParser(prog='amu ci post', description=__doc__.split('\n')[0])
    p.add_argument('--dir', required=True, help='folder with report.json and meta.json')
    p.add_argument('--repo', required=True)
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument('--event', help='workflow_run event file ($GITHUB_EVENT_PATH)')
    which.add_argument('--pr', type=int, help='pull request number, for a run outside workflow_run')
    p.add_argument('--author', default=AUTHOR, help='login whose marked comment is edited')
    p.add_argument('--verify-dir', help="folder with the fresh-job rerun's verify.json, when there is one")
    a = p.parse_args(argv)
    report, meta = load(a.dir)
    try:
        github = GitHub(os.environ['GITHUB_TOKEN'])
        number = a.pr
        if a.event:
            with open(a.event) as handle:
                number, head = resolve(github, a.repo, json.load(handle))
            if number is None:
                print(f'amu: nothing posted: {head}')
                return 0
            if meta.get('pr') not in (None, number) or meta.get('head') not in (None, head):
                print(f"amu: the artifact names #{meta.get('pr')} at {meta.get('head')}; using #{number} at {head} from the event")
            meta = dict(meta, pr=number, head=head)
        verify = load_verify(a.verify_dir) if a.verify_dir else None

        def body(previous):
            extra = ''
            if meta.get('counters') is True:
                extra = counters.block(counters.extend(previous, counters.record(report, meta, verify)))
            return render(report, meta, extra=extra, verify=verify)
        action, ident = upsert(github, a.repo, number, body, a.author)
        print(f'amu: comment {ident} {action} on #{number}')
    except (KeyError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f'amu: could not post the comment: {type(exc).__name__}: {exc}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
