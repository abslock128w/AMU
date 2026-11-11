# Probes on the replayed regressions (2026-11-10)

v0.7.2 catches 1 of the 20 confirmed regressions in `../REPORT.md`: the regression shows only on an input no existing
test passes. This folder measures model-written probes for every changed function (`amu/core/generate.py`, written by
`claude -p`, kept only on execution evidence) on the same 20, and their noise on the frozen 47 pull requests.

## Rule, fixed before the runs

Each kept probe is rerun at the fix commit. It is **restored** when its outcome there equals its outcome on the merge
base, **intended** when it equals the outcome on the head, else **other**. A regression is caught when a restored
probe is on a regressed function or the function that holds it; "any function" drops that condition.

## Results

| | v0.7.2 | Trial 1 | Trial 2 |
|---|---:|---:|---:|
| Caught on the regressed function | 1 of 20 | 8 of 20 | 10 of 20 |
| Caught on any function | 1 of 20 | 10 of 20 | 12 of 20 |
| Rows kept | | 95 | 143 |
| Restored | | 17 | 35 |
| Intended | | 74 | 103 |
| Other | | 4 | 5 |

Trial 1 probed every changed function. Trial 2 added: nested functions probed through the function that holds them,
generators as targets, the 40 most changed functions first, Django probes hosted in the test app they import.
Django 18620 was caught in trial 1 and missed in trial 2: the model wrote other probes. The catch depends on which
inputs the model picks.

About 3 rows in 4 show the intended change, and nothing in a row's outcome kind tells the two apart, with one
exception: a call that returned on the merge base and on the head fails at a statement of the project that is not a
`raise` ("crash").

| Trial 2 rows | Restored | Intended or other |
|---|---:|---:|
| Crash | 15 | 20 |
| All other rows | 20 | 88 |

Regressions with a restored crash row: 9 of 20 on any function (attrs 1267 and 759, Django 18270 and 18314,
requests 6644, the three requests 7272 fixes, sympy 25753).

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

## Limits

- The model may know these fixes from training: the catch counts are a ceiling.
- One run per pull request. Django 18620 shows the result moves between runs.
- The comment lists crash rows and collapses the rest, so the 3 regressions that show only as a changed return value
  are in the collapsed list, not the headline.
- Probes are off unless the workflow sets `claude-code` or an API key.

## Files

`probe_trial.py` runs the replay with probes, `probe_score.py` reruns kept probes at the fix, `probe_origin.py`
classifies newly raising rows, `run47p.py` runs the 47 set. `score-trial1.json`, `score-trial2.json` and
`set47-rows.json` are their outputs.
