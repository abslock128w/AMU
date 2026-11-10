"""sympy 20 labels at v0.7.1 under rubric v3: the v0.7.0 label carried where the first line is the same, judged anew below."""
import json, os
old = json.load(open('engine-v071/evaluation/generalize/v070-47/labels.json'))
over = {
 '30741': dict(first_line_correct=True, adds='something', real=1, false=0, note='First line now right: the 3 hunks are "not recorded (the run stopped after 1080 of 13207 candidate tests)", no longer "ran in no test". The 179 differing calls are 1 grouped row; real as at v0.7.0 (the pull request changes charpoly\'s default symbol), not traced call by call.'),
 '30776': dict(first_line_correct=True, adds='something', note='Fixed: no crash. "adds new code only; its 1 new test fails on the base with AttributeError, as expected", true.'),
 '30803': dict(note='Same line. 0 rows as at v0.7.0; raise missed 1 as before.'),
 '30815': dict(note='Same line. Raise missed 72, was 77.'),
}
L = {}
for n in sorted(os.listdir('out-eb81e2fa/sympy')):
    d, src = f'out-v071/sympy/{n}/', 'tag v0.7.1'
    if not os.path.exists(d + 'comment.md'):
        d, src = f'out-eb81e2fa/sympy/{n}/', 'candidate b81e2fa'
    o = old[f'sympy#{n}']
    rj = json.load(open(d + 'raise.json')) if os.path.exists(d + 'raise.json') else {}
    if not rj and src.startswith('tag'):
        c = f'out-eb81e2fa/sympy/{n}/raise.json'
        rj = json.load(open(c)) if os.path.exists(c) else {}
    e = dict(first=open(d + 'comment.md').readline().strip(), engine=src, first_line_correct=o['first_line_correct'], adds=o['adds'],
             real=o['real'], false=o['false'], raise_catchers=rj.get('catchers'), raise_missed=rj.get('missed'),
             seconds=json.load(open(d + 'timing.json'))['seconds'], note='Same first line as v0.7.0, test counts apart.')
    e.update(over.get(n, {}))
    L[f'sympy#{n}'] = e
json.dump(L, open('reports71/sympy-labels.json', 'w'), indent=1)
import statistics
print(sum(e['first_line_correct'] for e in L.values()), len(L), 'false', sum(e['false'] for e in L.values()), 'missed', sum(e['raise_missed'] or 0 for e in L.values()), 'median s', statistics.median(e['seconds'] for e in L.values()))
print({a: sum(e['adds'] == a for e in L.values()) for a in ('something', 'nothing', 'noise')})
for k, e in L.items(): print(k, e['engine'], e['raise_missed'], e['seconds'])
