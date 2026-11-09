"""Agreement between a blind labeler's answers and the existing labels (key.json), per field.

Usage: python score.py [--labeler blind]
Rows: exact agreement for first_line_correct and adds (with Cohen's kappa), and for the row counts both exact
agreement and agreement on whether any false (or real) row exists. Writes agreement-<labeler>.json.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    seen = sum(a == b for a, b in pairs) / n
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(left[k] * right[k] for k in set(left) | set(right)) / (n * n)
    return None if chance == 1 else round((seen - chance) / (1 - chance), 2)


def main():
    a = argparse.ArgumentParser()
    a.add_argument('--labeler', default='blind')
    args = a.parse_args()
    key = json.loads((HERE/'key.json').read_text())
    answers = json.loads((HERE/f'answers-{args.labeler}.json').read_text())
    common = sorted(set(key['items']) & set(answers))
    out = dict(labeler=args.labeler, rubric=key['rubric'], items=len(common), fields={}, disagreements=[])
    tests = dict(first_line_correct=lambda v: v, adds=lambda v: v, false_rows=lambda v: v, real_rows=lambda v: v,
                 any_false_row=lambda v: v > 0, any_real_row=lambda v: v > 0)
    for field, f in tests.items():
        source = field.replace('any_false_row', 'false_rows').replace('any_real_row', 'real_rows')
        pairs = [(f(key['items'][i]['labels'][source]), f(answers[i][source])) for i in common]
        out['fields'][field] = dict(agree=sum(x == y for x, y in pairs), of=len(pairs),
                                    kappa=kappa(pairs) if field in ('first_line_correct', 'adds', 'any_false_row') else None)
    for i in common:
        diff = {k: dict(existing=key['items'][i]['labels'][k], blind=answers[i][k])
                for k in ('first_line_correct', 'adds', 'false_rows', 'real_rows')
                if key['items'][i]['labels'][k] != answers[i][k]}
        if diff:
            item = key['items'][i]
            out['disagreements'].append(dict(item=i, source=f"{item['set']}:{item['repo']}#{item['number']}", fields=diff,
                                             note=answers[i].get('note', '')))
    (HERE/f'agreement-{args.labeler}.json').write_text(json.dumps(out, indent=1) + '\n')
    for field, r in out['fields'].items():
        print(f"{field}: {r['agree']} of {r['of']}" + (f", kappa {r['kappa']}" if r['kappa'] is not None else ''))


if __name__ == '__main__':
    main()
