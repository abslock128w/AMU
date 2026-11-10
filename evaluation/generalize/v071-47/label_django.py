"""Django 17 labels at v0.7.1 under rubric v3: the v0.7.0 label carried where the first line is the same, judged anew below."""
import json, os
old = json.load(open('engine-v071/evaluation/generalize/v070-47/labels.json'))
over = {
 '21887': dict(note='Same line as v0.7.0: ran in no test, true for the MySQL-only hunk on SQLite.'),
 '22026': dict(first_line_correct=True, adds='noise', real=0, false=1, note='First line now right: the 44 hunks are "not recorded (the run stopped after 11208 of 18770 candidate tests)", no longer "ran in no test". 1 row for 2 calls on ModelDetailView.get_context_data shows the same text on both sides: the results part past the 300 characters kept. Counted false as before; defect T6, fixed after the tag.'),
 '22046': dict(note='Same line. Raise missed 4 at v0.7.0, 0 now.'),
 '22095': dict(first_line_correct=True, note='Fixed: _os.py:61 no longer reads "not executable on Python 3.14"; every changed hunk ran on both sides. Raise missed 9 at v0.7.0, 0 now.'),
 '22154': dict(real=24, false=0, note='Rows grouped: 24 rows for 216 calls on _apply_rel_filters, real and intended. Raise missed 11 at v0.7.0, 0 now.'),
 '22158': dict(note='Fixed wording: one hunk named, the phrase printed once.'),
 '22176': dict(note='Now "2 of 7 changed hunks not recorded (the run stopped after 10878 of 15746 candidate tests)". True on this run: the after side stopped at its time budget on a loaded machine. v0.7.0 said "ran in no test" after 12531 tests; neither run reached the GDAL raster hunks.'),
 '22204': dict(first_line_correct=True, adds='nothing', note='Fixed: no crash. "1 of 1 changed hunk ran in no test: oracle/features.py:152", true on SQLite.'),
}
L = {}
for n in sorted(os.listdir('out-eb81e2fa/django')):
    d, src = f'out-v071/django/{n}/', 'tag v0.7.1'
    if not os.path.exists(d + 'comment.md'):
        d, src = f'out-eb81e2fa/django/{n}/', 'candidate b81e2fa'
    o = old[f'django#{n}']
    rj = json.load(open(d + 'raise.json')) if os.path.exists(d + 'raise.json') else {}
    e = dict(first=open(d + 'comment.md').readline().strip(), engine=src, first_line_correct=o['first_line_correct'], adds=o['adds'],
             real=o['real'], false=o['false'], raise_catchers=rj.get('catchers'), raise_missed=rj.get('missed'),
             seconds=json.load(open(d + 'timing.json'))['seconds'], note='Same first line as v0.7.0, test counts apart.')
    e.update(over.get(n, {}))
    L[f'django#{n}'] = e
json.dump(L, open('reports71/django-labels.json', 'w'), indent=1)
print(sum(e['first_line_correct'] for e in L.values()), len(L), 'false', sum(e['false'] for e in L.values()), 'missed', sum(e['raise_missed'] or 0 for e in L.values()))
for k, e in L.items(): print(k, e['engine'], e['first'][18:150])
