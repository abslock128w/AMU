"""Hand labels for the 47 set at v0.7.0 (engine 43bcd672). Prior = v0.6.0 labels."""
import json, subprocess
from pathlib import Path
W = Path(__file__).resolve().parent.parent
S = Path('/private/tmp/claude-502/-Users-Abslock128-Documents-ChatGPT-AMU-fix-base/b2f1bc66-3e20-4459-84e0-72c90221b63f/scratchpad')
prior = json.loads(subprocess.check_output(['git', '-C', '/Users/Abslock128/Documents/ChatGPT/AMU/fix-base', 'show',
                                            'github/main:evaluation/generalize/v060-47/labels.json']))
SAME = 'Same as v0.6.0.'
L = {
 'django#21855': (True, 'nothing', 0, 0, SAME),
 'django#21887': (True, 'nothing', 0, 0, SAME),
 'django#22026': (False, 'noise', 0, 2, "2 rows, both on ModelDetailView.get_context_data, whose 300-character previews are equal on both sides; not traced, counted false as in v0.6.0. The 6 _django_version rows and the 1 path-parts row are gone (cause 5: 9 fields excluded). The first line still announces 2 behaviour differences."),
 'django#22046': (True, 'nothing', 0, 0, SAME),
 'django#22095': (False, 'nothing', 0, 0, "Regressed. `_os.py:61` is `_makedirs = os.makedirs if PY315 else makedirs`: the line ran in 151 tests, but the new conditional-expression guard marks the whole hunk 'not executable on Python 3.14 here (needs PY315)'. Only one operand is gated; the line itself runs."),
 'django#22102': (True, 'something', 0, 0, SAME),
 'django#22137': (True, 'something', 0, 0, SAME),
 'django#22154': (True, 'something', 169, 0, "169 rows on GenericRelatedObjectManager._apply_rel_filters, real and intended: the queryset no longer pins `_db` ('default' -> None) and carries the instance as a hint, which is the pull request's change and what its new test asserts."),
 'django#22158': (True, 'nothing', 0, 0, "Fixed: 'not executable on Python 3.14 here (needs PY315)' for the two 3.15-only serializer hunks. The phrase 'not executable on Python 3.14 here' is printed twice in the line."),
 'django#22169': (True, 'something', 2, 0, "2 rows on JoinPromoter.update_join_types, real and intended: the set of tables to promote changes under XOR, the pull request's fix."),
 'django#22171': (True, 'nothing', 0, 0, "Fixed: the 3 _django_version rows are gone (1 field excluded) and the first line no longer announces them."),
 'django#22176': (True, 'noise', 0, 0, SAME),
 'django#22179': (True, 'something', 0, 0, SAME),
 'django#22186': (True, 'nothing', 0, 0, "Better: now names the Windows-only line, '1 of 2 changed hunks not executable on darwin here (needs os.name == 'nt')'."),
 'django#22187': (True, 'nothing', 0, 0, SAME),
 'django#22189': (True, 'something', 0, 0, SAME),
 'django#22204': (False, 'nothing', 0, 0, "Regressed: no report. RecursionError in states.Guards.truth: the added guard `(21,) <= self.connection.oracle_version < (23,)` is a chained comparison; truth() passes it to value(), which passes it back to truth() at the same depth."),
 'flask#5812': (False, 'nothing', 366, 0, "366 rows, all real and intended, on the same three functions as v0.6.0 (request_context 321, test_request_context 32, app_context 13); 5 fields excluded (HTTP_COOKIE). First line wrong: '7 of 67 changed hunks ran in no test' includes ctx.py:235/242/251, where a docstring or comment edit inside a block reads as code (block re-application ignores the unchanged lines inside it)."),
 'flask#5818': (True, 'something', 0, 0, "Now also names app.py:79, the hunk adding remove_ctx: its wrapper body ran in no test of the suite. True."),
 'flask#5898': (True, 'something', 6, 0, SAME),
 'flask#5899': (True, 'nothing', 0, 0, SAME),
 'flask#5917': (True, 'something', 0, 0, SAME),
 'flask#5928': (True, 'something', 0, 0, "Better: helpers.py:670 is the `else: raise self.errors[0]` branch of `if sys.version_info >= (3, 11)`, unreachable on 3.14, and is named so."),
 'flask#6013': (True, 'nothing', 0, 0, SAME),
 'flask#6096': (True, 'something', 0, 0, SAME),
 'flask#6133': (False, 'nothing', 0, 0, "Now 'every changed hunk ran on both sides (at import)'. The new method's body ran only in the pull request's own test, so it should read 'ran only in the pull request's own tests'. The v0.6.0 line ('in up to 342 tests') was labelled right but was not accurate either."),
 'flask#6168': (True, 'nothing', 0, 0, "Same as v0.6.0. 1 field excluded base-vs-base."),
}
def load(repo):
    return {x['number']: x for x in json.loads((S/f'{repo}7.json').read_text())}
out = {}
for key, (right, adds, real, false, note) in L.items():
    repo, n = key.split('#'); n = int(n)
    x = load(repo)[n]; p = prior[key]
    out[key] = dict(repo=repo, number=n, first_line_correct=right, prior_first_line_correct=p['first_line_correct'], adds=adds,
                    prior_adds=p['adds'], real=real, false=false, prior_real=p['real'], prior_false=p['false'], rows=x['rows'],
                    excluded_fields=x['excluded_fields'], raise_catchers=x['raise_catchers'], raise_missed=x['raise_missed'],
                    note=note, first=x['first'])
extra = W/'evalv070'/'labels47-sympy.json'
if extra.exists():
    out.update(json.loads(extra.read_text()))
(W/'evalv070'/'v070-47'/'labels.json').write_text(json.dumps(out, indent=1, sort_keys=True) + '\n')
print(len(out))
