"""The numbers of brief 5, piece 3, computed from the saved results (PROTOCOL.md, "The numbers, in this order").

Usage: python -P evaluation/retrospective/score.py --root ROOT [--labels labels.json] [--out numbers.json]

Reads ROOT/out/<repo>/<n>/ for every frozen pull request: report.json, meta.json, verify/verify.json, probes.json,
probe-targets.json. Hand labels come from labels.json (side-by-side sample and kept probes). Prints a Markdown summary
and writes the numbers as JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from freeze import check  # noqa: E402

from amu.ci.render import nothing_found  # noqa: E402
from amu.core.replay import is_test_file  # noqa: E402


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def sample(prs_with_difference, size=20):
    """The hand sample, fixed by PROTOCOL.md: all of them when 20 or fewer, else the 20 lowest sha256('<repo>#<n>')."""
    keyed = sorted(prs_with_difference, key=lambda key: hashlib.sha256(key.encode()).hexdigest())
    return sorted(keyed[:size]) if len(keyed) > size else sorted(keyed)


def per_pr(root, name, pr):
    out = Path(root)/'out'/name/str(pr['number'])
    report, meta = read(out/'report.json', {}), read(out/'meta.json', {})
    verify = read(out/'verify'/'verify.json')
    probes = read(out/'probes.json', []) or []
    targets = read(out/'probe-targets.json')
    side = report.get('side_by_side') if isinstance(report.get('side_by_side'), dict) else None
    trust = [r for r in report.get('flip_trust') or [] if isinstance(r, dict)]
    counted = [r for r in report.get('predicted_failures') or []]
    def production(ident):
        return not is_test_file(str(ident).split('#')[0])
    uncalled_ids = [t['id'] for t in (targets or {}).get('targets') or []]
    called_ids = list((side or {}).get('called') or [])
    row = dict(repo=name, number=pr['number'], title=pr['title'], done=(out/'done.json').exists(),
               production_comparable=sum(map(production, uncalled_ids + called_ids)),
               production_uncalled=sum(map(production, uncalled_ids)),
               verify_seconds=(read(out/'timing.json', {}) or {}).get('verify_seconds'),
               observe=(read(out/'timing.json', {}) or {}).get('observe', True),
               error=report.get('error'), aborted=bool(report.get('failure')), seconds=meta.get('seconds'),
               flips=report.get('flips'), counted=len(counted),
               not_counted=[dict(id=r.get('id'), label=r.get('label')) for r in trust if not str(r.get('label', '')).startswith('fails')],
               verify=verify and {k: verify.get(k) for k in ('checked', 'held', 'false', 'other', 'problem', 'states')},
               ran=(report.get('executed') or {}).get('ran'), candidates=(report.get('executed') or {}).get('candidates'),
               behaviour=len(report.get('behaviour_differences') or []),
               sbs=side and {k: side.get(k) for k in ('targets', 'functions_called', 'compared_calls', 'differences', 'unstable')},
               not_compared=len((side or {}).get('not_compared') or {}),
               changed_functions=len(report.get('changed_functions') or []),
               evidence=len(report.get('change_evidence') or []), replayed=len(report.get('replayed') or []),
               quiet=bool(report) and nothing_found(report),
               recording_gap=next((g for g in report.get('gaps') or [] if 'recording' in str(g)), None),
               probe_targets=None if targets is None else len(targets.get('targets') or []),
               probes_attempted=sum(len(r.get('probes') or []) for r in probes),
               probes_kept=sum(len(r.get('differences') or []) for r in probes),
               probe_minutes=read(out/'probe-minutes.json', {}).get('minutes'),
               kept=[dict(target=d.get('target'), call=d.get('call'), before=d.get('before'), after=d.get('after'))
                     for r in probes for d in r.get('differences') or []])
    return row


def summary(rows, labels):
    done = [r for r in rows if r['done']]
    counted = sum(r['counted'] for r in done)
    checked = sum((r['verify'] or {}).get('checked') or 0 for r in done)
    false = sum((r['verify'] or {}).get('false') or 0 for r in done)
    other = sum((r['verify'] or {}).get('other') or 0 for r in done)
    targets = sum((r['sbs'] or {}).get('targets') or 0 for r in done)
    called = sum((r['sbs'] or {}).get('functions_called') or 0 for r in done)
    shares = [1 - (r['sbs']['functions_called'] or 0) / r['sbs']['targets'] for r in done if r['sbs'] and r['sbs'].get('targets')]
    seconds = [r['seconds'] for r in done if isinstance(r['seconds'], (int, float))]
    with_difference = [f"{r['repo']}#{r['number']}" for r in done if r['behaviour']]
    labelled = [v for k, v in (labels.get('side_by_side') or {}).items() if k.split('/')[0] in {r['repo'] for r in done}]
    rows_labelled = [x for v in labelled for x in v]
    real = sum(x['label'].startswith('real') for x in rows_labelled)
    return dict(
        prs=len(rows), done=len(done), errors=sum(bool(r['error']) for r in done), aborted=sum(r['aborted'] for r in done),
        counted_flips=counted, verify_checked=checked, verify_false=false, verify_other=other,
        false_flip_rate=None if not checked else false / checked,
        prs_with_counted_flip=sum(r['counted'] > 0 for r in done),
        prs_with_difference=len(with_difference), difference_prs=with_difference,
        sample=sample(with_difference), sample_rows=len(rows_labelled), sample_real=real,
        precision=None if not rows_labelled else real / len(rows_labelled),
        comparable_functions=targets, functions_called=called,
        uncalled_share=None if not targets else 1 - called / targets,
        uncalled_share_median_per_pr=None if not shares else statistics.median(shares),
        not_comparable=sum(r['not_compared'] for r in done),
        median_seconds=None if not seconds else statistics.median(seconds), worst_seconds=None if not seconds else max(seconds),
        quiet=sum(r['quiet'] for r in done), with_evidence=sum(r['evidence'] > 0 for r in done),
        probes_attempted=sum(r['probes_attempted'] for r in done), probes_kept=sum(r['probes_kept'] for r in done),
        production_comparable=sum(r['production_comparable'] for r in done),
        production_uncalled=sum(r['production_uncalled'] for r in done),
        uncalled_share_production=None if not sum(r['production_comparable'] for r in done) else
            sum(r['production_uncalled'] for r in done) / sum(r['production_comparable'] for r in done),
        prs_with_probe_targets=sum(bool(r['probe_targets']) for r in done),
        probe_minutes_median=None if not [r['probe_minutes'] for r in done if r['probe_minutes'] is not None] else
            statistics.median([r['probe_minutes'] for r in done if r['probe_minutes'] is not None]),
        probe_minutes_max=max([r['probe_minutes'] for r in done if r['probe_minutes'] is not None], default=None),
        probe_labels={label: sum(1 for k, v in (labels.get('probes') or {}).items() if k.split('/')[0] in {r['repo'] for r in done}
                                 for x in v if x['label'] == label)
                      for label in ('intended', 'not intended (cosmetic)', 'not intended', 'artifact')},
        sample_artifacts=len(rows_labelled) - real,
    )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--root', required=True)
    p.add_argument('--labels', default=str(HERE/'labels.json'))
    p.add_argument('--out', default=str(HERE/'numbers.json'))
    a = p.parse_args(argv)
    doc = check()
    labels = read(a.labels, {}) or {}
    result = dict(rows=[], repos={})
    for name, prs in doc['prs'].items():
        rows = [per_pr(a.root, name, pr) for pr in sorted(prs, key=lambda r: (r['merged_at'], r['number']))]
        result['rows'] += rows
        result['repos'][name] = summary(rows, labels)
    result['repos']['both'] = summary(result['rows'], labels)
    Path(a.out).write_text(json.dumps(result, indent=1))
    for name, s in result['repos'].items():
        print(f"## {name}\n" + '\n'.join(f'- {k}: {v}' for k, v in s.items() if k not in ('difference_prs',)))


if __name__ == '__main__':
    main()
