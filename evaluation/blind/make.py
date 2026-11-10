"""Draw the blind second-labeling set: 20 comments at random from the v0.7.0 labels (the 47 set and the attrs, httpx
and jinja live pilots), with pull request ids stripped.

Usage: python make.py --patches DIR [--seed 20261109] [--n 20]
DIR holds the runs' patches: DIR/out-e43bcd672/<repo>/<n>/patch.diff (47 set) and DIR/live-<repo>[-v070]/<n>/patch.diff.
Writes items/NN/{comment.md, patch.diff} and key.json (item -> source and the existing labels, rubric v3).
A labeler reads RUBRIC.md and items/ only; key.json is read by score.py alone.
"""
import argparse
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
SOURCES = [  # set, labels file, comments folder, patch folder under --patches
    ('47', 'generalize/v070-47/labels.json', 'generalize/v070-47/comments/{repo}/{n}.md', 'out-e43bcd672/{repo}/{n}'),
    ('attrs', 'pilot/attrs/v070/labels.json', 'pilot/attrs/v070/comments/{n}.md', 'live-attrs-v070/{n}'),
    ('httpx', 'pilot/httpx/v070/labels.json', 'pilot/httpx/v070/comments/{n}.md', 'live-httpx-v070/{n}'),
    ('jinja', 'pilot/jinja/labels.json', 'pilot/jinja/comments/{n}.md', 'live-jinja/{n}'),
]
FIELDS = ('first_line_correct', 'adds', 'false_rows', 'real_rows')


def labels_of(entry):
    """The four compared fields, under one name each (the 47 set says false/real, the pilots false_rows/real_rows)."""
    return dict(first_line_correct=entry['first_line_correct'], adds=entry['adds'],
                false_rows=entry.get('false_rows', entry.get('false', 0)) or 0,
                real_rows=entry.get('real_rows', entry.get('real', 0)) or 0)


def strip(text, number, shas):
    """Remove what names the pull request: its number and every commit id."""
    text = re.sub(r'\b[0-9a-f]{7,40}\b', '<sha>', text)
    for sha in shas:
        text = text.replace(sha, '<sha>')
    return re.sub(rf'(?<![\w.:/-]){number}(?![\w.])', '<pr>', text)


def pool(patches):
    out = []
    for name, labels, comment, patch in SOURCES:
        for key, entry in json.loads((EVAL/labels).read_text()).items():
            repo, n = (key.split('#') if '#' in key else (name, key))
            fields = dict(repo=repo, n=n)
            c, p = EVAL/comment.format(**fields), Path(patches)/patch.format(**fields)/'patch.diff'
            if c.exists() and p.exists():
                out.append(dict(set=name, repo=repo, number=int(n), comment=c, patch=p, labels=labels_of(entry)))
    return sorted(out, key=lambda x: (x['set'], x['repo'], x['number']))


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--patches', required=True)
    a.add_argument('--seed', type=int, default=20261109)
    a.add_argument('--n', type=int, default=20)
    args = a.parse_args()
    everything = pool(args.patches)
    drawn = random.Random(args.seed).sample(everything, args.n)
    key = dict(seed=args.seed, pool=len(everything), rubric='v3', items={})
    for i, item in enumerate(drawn, 1):
        folder = HERE/'items'/f'{i:02d}'
        folder.mkdir(parents=True, exist_ok=True)
        meta = json.loads((item['patch'].parent/'meta.json').read_text()) if (item['patch'].parent/'meta.json').exists() else {}
        shas = [v for v in meta.values() if isinstance(v, str) and re.fullmatch(r'[0-9a-f]{40}', v)]
        (folder/'comment.md').write_text(strip(item['comment'].read_text(), item['number'], shas))
        (folder/'patch.diff').write_text(strip(item['patch'].read_text(), item['number'], shas))
        key['items'][f'{i:02d}'] = dict(set=item['set'], repo=item['repo'], number=item['number'], labels=item['labels'])
    (HERE/'key.json').write_text(json.dumps(key, indent=1) + '\n')
    print(f"{args.n} of {len(everything)} drawn; items/ and key.json written")


if __name__ == '__main__':
    main()
