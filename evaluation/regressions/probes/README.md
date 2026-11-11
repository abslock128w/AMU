# Probes on the replayed regressions (2026-11-10)

v0.7.2 catches 1 of the 20 confirmed regressions in `../REPORT.md`: the regression shows only on an input no existing
test passes. This folder measures model-written probes for every changed function (`amu/core/generate.py`, written by
`claude -p`, kept only on execution evidence) on the same 20, and their noise on the frozen 47 pull requests.

## Rule, fixed before the runs

Each kept probe is rerun at the fix commit. It is **restored** when its outcome there equals its outcome on the merge
base, **intended** when it equals the outcome on the head, else **other**. A regression is caught when a restored
probe is on a regressed function or the function that holds it; "any function" drops that condition.

## Results

| | v0.7.2 | Trial 1 | Trial 2 | Trial 3 |
|---|---:|---:|---:|---:|
| Caught on the regressed function | 1 of 20 | 8 of 20 | 10 of 20 | 8 of 20 |
| Caught on any function | 1 of 20 | 10 of 20 | 12 of 20 | 8 of 20 |
| Caught by a headline row | | | 9 of 20 | 4 of 20 |
| Rows kept | | 95 | 143 | 135 |
| Restored | | 17 | 35 | 19 |
| Intended | | 74 | 103 | 111 |
| Other | | 4 | 5 | 5 |

Trial 1 probed every changed function. Trial 2 added: nested functions probed through the function that holds them,
generators as targets, the 40 most changed functions first, Django probes hosted in the test app they import.
Django 18620 was caught in trial 1 and missed in trial 2: the model wrote other probes. The catch depends on which
inputs the model picks. Trial 3 (2026-11-11) changed the prompt: inputs are built from types that exist, with no
made-up stand-in class, and a row whose setup defines a class with no bases and no decorator is not a headline row.

About 3 rows in 4 show the intended change, and nothing in a row's outcome kind tells the two apart, with one
exception: a call that returned on the merge base and on the head fails at a statement of the project that is not a
`raise` ("crash").

| Trial 2 rows | Restored | Intended or other |
|---|---:|---:|
| Crash | 15 | 20 |
| All other rows | 20 | 88 |

Regressions with a restored crash row: 9 of 20 on any function (attrs 1267 and 759, Django 18270 and 18314,
requests 6644, the three requests 7272 fixes, sympy 25753).

Trial 3, same split, rows on a stand-in class counted with the other rows:

| Trial 3 rows | Restored | Intended or other |
|---|---:|---:|
| Crash | 7 | 24 |
| All other rows | 12 | 92 |

Regressions with a restored headline row in trial 3: 4 of 20 (attrs 1267, Django 18270, sympy 30882 and 25753).
Lost against trial 2: the three requests 7272 fixes (trial 2 caught them through a made-up `Proxy` class; with real
types the crash rows there showed the intended change), attrs 759 (no row kept), Django 18314 (no restored row),
requests 6644 (caught, by rows that are not crashes). Gained: sympy 30882. One run each, so part of this is the
run-to-run movement above and part is the prompt; the data does not separate them. The trial 2 rows scored under
the trial 3 rule give 6 of 20.

## Noise on the 47 pull requests

These pull requests broke nothing known, so a row there shows a change that was meant, or a contrived input.

| | Rows | Pull requests with rows |
|---|---:|---:|
| All kept rows | 201 | 26 of 47 |
| Crash rows, one per function and statement | 8 | 6 of 47 |

The crash rows, labelled by one reader (the author of the change, no second reader):

| PR | Row | Label |
|---|---|---|
| Flask 5818 | three internal functions now take a context argument | real break for old callers |
| Flask 6096 | `app.run` rejects an out-of-range port in `SERVER_NAME` | real, minor |
| Django 22026 | a deprecation warning the probe turned into an error | noise; filtered since (a warning is `raised`) |
| Django 22169 | object built with a made-up connector name | noise, contrived input |
| Django 22204 | fake version object that cannot be compared | noise, contrived input |
| Flask 6013 | fake name object with only `endswith` | noise, contrived input |

Three noise pull requests in 47 after the warning filter. The bar in `../../STATUS.md` is 2.

Trial 3 on the same 47: 202 rows on 24 pull requests, headline rows on 2.

| PR | Row | Label |
|---|---|---|
| Flask 5818 | two internal functions fail for callers that pass the old arguments | real break for old callers |
| Flask 6096 | `app.run` rejects an out-of-range port in `SERVER_NAME` | real, minor |

No noise pull request in 47, same single reader. The three noise rows of trial 2 are gone: no stand-in class was
written for Django 22169, 22204 or Flask 6013, and the 2 rows built on one (Django 22026) are in the collapsed list.
The price is the catch count above: 4 of 20 against 9.

## Limits

- The model may know these fixes from training: the catch counts are a ceiling.
- One run per pull request. Django 18620 shows the result moves between runs.
- The comment lists crash rows and collapses the rest, so the 3 regressions that show only as a changed return value
  are in the collapsed list, not the headline.
- Probes are off unless the workflow sets `claude-code` or an API key.

## Files

`probe_trial.py` runs the replay with probes, `probe_score.py` reruns kept probes at the fix, `probe_origin.py`
classifies newly raising rows, `run47p.py` runs the 47 set. `score-trial1.json`, `score-trial2.json`, `score-trial3.json`,
`set47-rows.json` and `set47-rows-trial3.json` are their outputs. From trial 3 the engine records `origin`, `at` and
`stand_in` in each row, so `probe_origin.py` is only needed for the older trials.
