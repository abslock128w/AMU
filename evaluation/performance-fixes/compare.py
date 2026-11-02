"""Per PR: v0.4.2 against the fixes, on what the labels depend on: the first line, behaviour rows (count, distinct
outcomes), change-evidence and new-code ids, side-by-side set-asides. Prints only PRs where something differs."""
import json, sys, collections
R = '/Users/Shared/amu-retro-2026-10-18'
L = json.load(open(R + '/pr-v042/labels.json'))


def view(tag, repo, n):
    d = f'{R}/out-{tag}/{repo}/{n}'
    try:
        r = json.load(open(d + '/report.json'))
        first = open(d + '/comment.md').read().splitlines()[0]
    except Exception as e:
        return None
    rows = r.get('behaviour_differences') or []
    side = r.get('side_by_side') or {}
    return dict(first=first, rows=len(rows),
                outcomes=collections.Counter(tuple(x['after']['result'][:2]) for x in rows).most_common(3),
                evidence=sorted(x['id'].split('::')[-1] for x in r.get('change_evidence') or []),
                new_code=len([x for x in r.get('replayed') or [] if x.get('verdict') == 'new_code']),
                unstable=len([x for x in r.get('replayed') or [] if x.get('verdict') == 'unstable']),
                built_before=side.get('input_built_before_the_change'), misfit=side.get('calls_not_fitting_a_changed_signature'),
                seconds=json.load(open(d + '/meta.json'))['seconds'])


for key, label in sorted(L.items()):
    repo, n = label['repo'], label['number']
    a, b = view('v042', repo, n), view('fixes', repo, n)
    if b is None:
        continue
    same = a and all(a[k] == b[k] for k in ('first', 'rows', 'evidence', 'new_code', 'unstable'))
    if same and '--all' not in sys.argv:
        continue
    print(f'## {key}  label was: first {label["first_line_correct"]}, {label["adds"]}, rows {label["rows"]}')
    for k in ('first', 'rows', 'outcomes', 'evidence', 'new_code', 'unstable', 'built_before', 'misfit', 'seconds'):
        if a is None or a[k] != b[k]:
            print(f'   {k}: {None if a is None else a[k]}  ->  {b[k]}')
