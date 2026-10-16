"""Score check A2 from a2-sympy.json: no confirmed flip lost, every flip labelled.

Usage: a2_score.py a2-sympy.json

A flip the old engine confirmed (its predicted_failures) is lost when the new engine neither lists it among its
failures nor labels it in flip_trust. Every flip the new engine saw must carry one of: fails, unstable, not caused by
this change.
"""
import json
import sys
from collections import Counter

LABELS = {'fails', 'unstable', 'not caused by this change'}


def ids(rows):
    return {row['id'] if isinstance(row, dict) else row for row in rows or []}


def main(path):
    results = json.load(open(path))
    by = {}
    for row in results:
        by.setdefault(row['run'], {})[row['engine']] = row
    table, labels, lost, unlabelled, errors = [], Counter(), [], [], []
    for run in sorted(by):
        old, new = by[run].get('old'), by[run].get('a2')
        if not old or not new or 'error' in old or 'error' in new:
            errors.append(run)
            continue
        confirmed_old = ids(old.get('predicted_failures'))
        trust = {row['id']: row['label'] for row in new.get('flip_trust') or []}
        failures_new = ids(new.get('predicted_failures'))
        for ident in failures_new:
            trust.setdefault(ident, 'fails')
        missing = sorted(confirmed_old - set(trust))
        lost += [(run, i) for i in missing]
        unlabelled += [(run, i) for i, label in trust.items() if label not in LABELS]
        labels.update(trust.values())
        table.append(dict(run=run, old_flips=len(confirmed_old), new_fails=sum(v == 'fails' for v in trust.values()),
                          unstable=sum(v == 'unstable' for v in trust.values()),
                          not_caused=sum(v == 'not caused by this change' for v in trust.values()),
                          old_confirmed_now=dict(Counter(trust.get(i, 'lost') for i in confirmed_old)),
                          new_only=sorted(set(trust) - confirmed_old), lost=missing,
                          seconds_old=old.get('seconds'), seconds_new=new.get('seconds')))
    summary = dict(runs=len(table), errors=errors, old_confirmed=sum(r['old_flips'] for r in table), labels=dict(labels),
                   lost=lost, unlabelled=unlabelled,
                   old_confirmed_labelled=dict(sum((Counter(r['old_confirmed_now']) for r in table), Counter())))
    print(json.dumps(dict(summary=summary, table=table), indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
