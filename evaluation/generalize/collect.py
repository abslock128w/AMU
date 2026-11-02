"""Collect a fork pilot's live results: per pull request the AMU job time, open-to-comment time, the comment, and the
artifact (report.json, meta.json, patch.diff). Usage: python collect.py --fork owner/name --opened FILE --out DIR
FILE rows: fork PR number, upstream number, created_at (as printed by replay.py)."""
import argparse
import datetime as D
import json
import subprocess
from pathlib import Path


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path]))


def when(text):
    return D.datetime.fromisoformat(text.replace('Z', '+00:00'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--fork', required=True)
    p.add_argument('--opened', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in Path(a.opened).read_text().splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        first, second, created = int(parts[0]), int(parts[1]), parts[2]
        rows.append((min(first, second), max(first, second), created))  # the fork's numbers are the small ones
    runs = api(f'repos/{a.fork}/actions/runs?per_page=100&event=pull_request')['workflow_runs']
    comments = []
    page = 1
    while True:
        got = api(f'repos/{a.fork}/issues/comments?per_page=100&page={page}')
        comments += got
        if len(got) < 100:
            break
        page += 1
    table = []
    for fork_pr, upstream, created in rows:
        pr = api(f'repos/{a.fork}/pulls/{fork_pr}')
        mine = [r for r in runs if r['name'] == 'AMU pull request' and r['head_sha'] == pr['head']['sha']
                and any(x['number'] == fork_pr for x in r.get('pull_requests') or [])] or \
               [r for r in runs if r['name'] == 'AMU pull request' and r['head_sha'] == pr['head']['sha'] and r['created_at'] >= created]
        run = sorted(mine, key=lambda r: r['created_at'])[-1] if mine else None
        jobs = api(f"repos/{a.fork}/actions/runs/{run['id']}/jobs")['jobs'] if run else []
        amu = next((j for j in jobs if j['name'] == 'amu' and j.get('completed_at')), None)
        posted = [c for c in comments if c['issue_url'].endswith(f'/issues/{fork_pr}')]
        folder = out/str(upstream)
        folder.mkdir(exist_ok=True)
        if run:
            subprocess.run(['gh', 'run', 'download', '-R', a.fork, str(run['id']), '-n', 'amu-report', '-D', str(folder)],
                           capture_output=True)
        body = posted[-1]['body'] if posted else ''
        (folder/'comment.md').write_text(body)
        row = dict(upstream=upstream, fork_pr=fork_pr, run=run['id'] if run else None,
                   amu_job_s=(when(amu['completed_at']) - when(amu['started_at'])).seconds if amu else None,
                   queue_s=(when(amu['started_at']) - when(created)).seconds if amu else None,
                   opened_to_comment_s=(when(posted[-1]['created_at']) - when(created)).seconds if posted else None,
                   posted=bool(posted), first=body.splitlines()[0] if body else None)
        table.append(row)
        print(json.dumps(row), flush=True)
    (out/'live.json').write_text(json.dumps(table, indent=1))


if __name__ == '__main__':
    main()
