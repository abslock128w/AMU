"""Opt-in counters for the adoption bars: counts and SHAs only, never source, test names or error text.

With ``counters: 'true'`` the posting job keeps, in a hidden block of its own comment, one record per push. Nothing
is sent anywhere else. Each record has:
- the head and merge-base SHAs;
- the flip count, tests run and candidates, and the number of gaps;
- tests the pull request adds or edits that were replayed on the merge base, and how many of them are change evidence;
- flips the trust checks labelled unstable or not caused by this change (the flip count holds only those that passed);
- whether the runner aborted or AMU errored, and seconds;
- from the fresh-job rerun, flips checked, held and false.

Collector: python -m amu.ci.counters OWNER/NAME [OWNER/NAME ...] [--first 50]    (token in $GITHUB_TOKEN)
reads those blocks from pull requests' comments and prints, over the first N pull requests commented on:
- the false-flip rate: flips that failed again on the unchanged side, of those rerun;
- the share of pull requests that got a flip comment and then got a push that cleared it before merge.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BLOCK = re.compile(r'<!-- amu-ci-counters (\{.*?\}) -->', re.S)
SHA = re.compile(r'^[0-9a-f]{7,40}$')
COUNTS = ('flips', 'ran', 'candidates', 'gaps', 'checked', 'held', 'false', 'evidence', 'replayed', 'unstable', 'not_caused')
FLAGS = ('aborted', 'error')
KEEP = 100  # pushes kept per pull request


def record(report, meta, verify=None):
    """One push's counts from the artifact, every field typed and nothing else kept."""
    executed = report.get('executed') or {}
    verify = verify or {}
    return clean(dict(head=meta.get('head'), merge_base=meta.get('merge_base'), flips=report.get('flips'),
                      ran=executed.get('ran'), candidates=executed.get('candidates'), gaps=len(report.get('gaps') or []),
                      aborted=bool(report.get('failure')), error=bool(report.get('error')), seconds=meta.get('seconds'),
                      checked=verify.get('checked'), held=verify.get('held'), false=verify.get('false'),
                      evidence=len(report.get('change_evidence') or []) if 'replayed' in report else None,
                      replayed=len(report.get('replayed') or []) if 'replayed' in report else None,
                      unstable=sum(row.get('label') == 'unstable' for row in report.get('flip_trust') or [] if isinstance(row, dict))
                      if 'flip_trust' in report else None,
                      not_caused=sum(row.get('label') == 'not caused by this change' for row in report.get('flip_trust') or []
                                     if isinstance(row, dict)) if 'flip_trust' in report else None))


def clean(raw):
    out = {}
    for key in ('head', 'merge_base'):
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and SHA.match(value) else None
    for key in COUNTS:
        value = raw.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**7 else None
    for key in FLAGS:
        out[key] = raw.get(key) is True
    seconds = raw.get('seconds')
    out['seconds'] = round(float(seconds), 1) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None
    return out


def history(body):
    found = BLOCK.search(body or '')
    if not found:
        return []
    try:
        pushes = json.loads(found.group(1)).get('pushes', [])
    except (ValueError, AttributeError):
        return []
    return [clean(push) for push in pushes if isinstance(push, dict)]


def block(pushes):
    kept = [clean(push) for push in pushes][-KEEP:]
    return '<!-- amu-ci-counters ' + json.dumps(dict(v=1, pushes=kept), separators=(',', ':')) + ' -->'


def extend(previous_body, current):
    """The history from our previous comment, with this push recorded (a rerun of the same head replaces it)."""
    pushes = [push for push in history(previous_body) if push['head'] != current['head']]
    return pushes + [current]


def outcome(pushes, merged):
    """For one pull request: (got a flip comment, cleared before merge, flips rerun, false flips)."""
    flagged = cleared = False
    for push in pushes:
        if push['flips']:
            flagged, cleared = True, False
        elif flagged and push['flips'] == 0 and push['ran']:
            cleared = True
    checked = sum(push['checked'] or 0 for push in pushes)
    false = sum(push['false'] or 0 for push in pushes)
    return flagged, flagged and cleared and merged, checked, false


def summarize(prs, first=50):
    """``prs``: [(repo, number, merged, pushes)] in the order AMU first commented."""
    rows = [row for row in prs if row[3]][:first]
    flagged = cleared = checked = false = 0
    for _, _, merged, pushes in rows:
        got, done, rerun, wrong = outcome(pushes, merged)
        flagged += got
        cleared += done
        checked += rerun
        false += wrong
    labelled = {key: sum(push.get(key) or 0 for row in rows for push in row[3]) for key in ('unstable', 'not_caused', 'evidence')}
    return dict(pull_requests=len(rows), with_a_flip=flagged, cleared_before_merge=cleared,
                flips_labelled_unstable=labelled['unstable'], flips_labelled_not_caused=labelled['not_caused'],
                change_evidence_rows=labelled['evidence'],
                cleared_share=round(cleared / flagged, 3) if flagged else None,
                flips_rerun=checked, false_flips=false, false_flip_rate=round(false / checked, 3) if checked else None)


def collect(github, repo, author):
    from .render import MARKER
    prs = []
    for pr in github.pages(f'/repos/{repo}/pulls?state=all&sort=created&direction=asc'):
        for comment in github.pages(f"/repos/{repo}/issues/{pr['number']}/comments"):
            if MARKER in (comment.get('body') or '') and (comment.get('user') or {}).get('login') == author:
                pushes = history(comment['body'])
                if pushes:
                    prs.append((comment.get('created_at') or '', repo, pr['number'], bool(pr.get('merged_at')), pushes))
                break
    return prs


def main(argv=None):
    from .post import AUTHOR, GitHub
    p = argparse.ArgumentParser(prog='amu ci counters', description='Adoption measures from AMU comments.')
    p.add_argument('repos', nargs='+', metavar='OWNER/NAME')
    p.add_argument('--first', type=int, default=50)
    p.add_argument('--author', default=AUTHOR)
    a = p.parse_args(argv)
    github = GitHub(os.environ['GITHUB_TOKEN'])
    found = sorted(row for repo in a.repos for row in collect(github, repo, a.author))
    print(json.dumps(summarize([row[1:] for row in found], a.first), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
