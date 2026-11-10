# The 47 set and four pilots at v0.7.1 (2026-11-10)

The same 47 pull requests as `REPORT-v070.md` (Django 17, sympy 20, Flask 10) and the 15-pull-request pilots on attrs,
httpx, jinja and requests. Settings as before: Python 3.14, 300 s per side, a 1,200 s wall cap. Labels use rubric v3
(`evaluation/PROTOCOL.md`). Label files: `v071-47/{django,flask,sympy}-labels.json` and
`evaluation/pilot/<repo>/v071/labels.json` (requests: `evaluation/pilot/requests/labels.json`).

## Losses

- **Not every label is from the tag.** Each label names its engine. All 20 sympy labels and 11 of 17 Django labels
  come from candidate `b81e2fa`, the commit before the last fix of the release (#105). The tag reruns of sympy were
  still going when this was written. The requests pilot is also on `b81e2fa`. Flask, jinja, attrs and httpx are on
  the tag.
- **Regressions: 1 of 20 caught** (`evaluation/regressions/REPORT.md`). Every miss has one cause: no test that existed
  when the pull request was opened fails on its head. AMU runs the tests that exist.
- **Flask: first line right on 8 of 10, as at v0.7.0.** 5812 still names docstring edits as code that ran in no test.
  6133 still reads "(at import)". Both are fixed after the tag, by #109 and #107.
- **sympy: 73 raise catchers missed** (30815: 72, 30803: 1). Was 79. The cause on 30815 is unchanged: coverage that
  depends on test order. The comment now says so. It is not bounded.
- **Django: 1 false row remains** (22026, `get_context_data`), not traced. Two Django comments still add noise.
- **sympy 30841: 2 false rows, labelled real until now.** The rows on `singularities` showed in one run and not the
  next, so they were traced. The change moves a call: an `atan` is call 4 before and call 5 after, with the same result
  both times. The run on the second hash seed reorders calls inside a test, the whole first argument then read as a
  per-run field and was left out, and the two indexes paired as "the same input". The same rows were labelled real at
  v0.7.0. The first line states them, so it is counted wrong. Fixed after the tag by #110: 0 rows in every run since.
- **requests #7272 showed false rows at the tag: 10.** 7 on `super_len` (an open file's size was not in the input)
  and 3 on `api.post`. Fixed after the tag by #108 and #110. The replay now shows 0 rows.
- **A container subclass's items are still not part of the input.** An `OrderedDict` or a list subclass is recorded
  by its attributes only. Reading the items was tried and dropped: on sympy the assumptions cache is a dict subclass
  filled lazily, and nearly every input then differed (187 compared calls fell to 3).
- **Cost.** sympy median 474 s on a loaded machine (v0.7.0: 534 s). The requests fork took a median 3,160 s from
  opening to comment, because 7 of 15 comment runs failed at startup on the runner and were rerun.

## Numbers

| Repo | PRs | First line right | was (v0.7.0) | False rows | was | Raise missed | was | Adds something / nothing / noise | Median s |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| Django | 17 | 17 | 14 | 1 | 2 | 0 of 495 | 24 | 6 / 9 / 2 | 110.9 |
| sympy | 20 | 19 | 18 | 2 | 0 | 73 of 11,239 | 79 | 11 / 9 / 0 | 474.3 |
| Flask | 10 | 8 | 8 | 0 | 0 | not measured | | 5 / 5 / 0 | 18.2 |
| attrs (pilot) | 15 | 15 | 15 | 0 | 0 | | | 6 / 9 / 0 | 72 |
| httpx (pilot) | 15 | 15 | 15 | 0 | 0 | | | 3 / 12 / 0 | 65 |
| jinja (pilot) | 15 | 15 | 13 | 0 | 121 | 0 | | 7 / 8 / 0 | 20.5 |
| requests (pilot, new) | 15 | 15 | | 0 | | | | 3 / 12 / 0 | 188 |

The 47 set: first line right on 44 of 47 (v0.7.0: 40, or 39 with sympy 30841 relabelled). The sympy "was" column is as
labelled then; with 30841 relabelled it is 17 right and 2 false rows. Counted flips: 0 everywhere.

## Stability

10 of the 47 (Django 4, Flask 2, sympy 4) were run three times (`v071-47/stability.json`). The whole set was planned;
the time ran out.

| | of 10 |
|---|---:|
| First line word for word the same in all three runs | 6 |
| The same apart from test counts | 9 |
| Differently worded | 1 |
| Row count the same | 10 |

The one reworded line is Django 22176: two runs hit the time budget ("2 of 7 changed hunks not recorded"), the third
finished ("ran in no test"). All 10 have 0 rows, so the sample does not measure row stability.

## What changed the numbers

- Django 22046, 22095, 22154: the 24 missed raise catchers were inherited and generated tests. The runner's own
  collection now lists the tests.
- Django 22204 and sympy 30776: the chained-comparison crash is gone.
- sympy 30741: a cut-off run reads "not recorded (the run stopped after 1080 of 13207 candidate tests)". Its 179
  differing calls are 1 grouped row.
- jinja: the 121 false rows came from one pull request. They are 0.
- attrs, one pull request: 650 calls that show the same change are 1 row with a count.

## Fixed after the tag (on main, not released)

| PR | What |
|---|---|
| #107 | TYPE_CHECKING blocks are typing; a body that ran only in the pull request's own test is not "(at import)" |
| #108 | An input's length survives masking; a row names the field where results part |
| #109 | Diffs line up on lines that occur once, so a rewritten docstring is its own hunk |
| #110 | An open file's size is part of the input; a per-run value in one argument no longer hides the others; a row is confirmed by one more run on each side; two runs that made different calls at an index name no unstable field |

## Measurement trust

A second, blind labelling of 20 comments agreed on the first line for 19 (kappa 0.64) and on "adds" for 16 (kappa
0.6). Read every count above with that in mind: one label in five on "adds" could go the other way.
