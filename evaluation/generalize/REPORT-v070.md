# Causes 4 and 5, measured on the 47 set at v0.7.0 (2026-11-03)

The 47 set was re-run locally on engine `43bcd672`, the code released as v0.7.0. It is the same 47 pull requests from
Django (17), sympy (20) and Flask (10) as `REPORT.md` (v0.6.0), with the same settings: Python 3.14, a 300 s budget
per side, a 1,200 s wall cap, and the raise measure (`raise_missed.py`) on every pull request. No engine code changed
during the runs.

New for v0.7.0: on Django and sympy, every pull request had a suite recording to load. The recording is the one the
nightly `amu-record.yml` would have made: the last commit before 03:23 UTC on the merge base's day, recorded across 4
shards and merged (`recordings.json`). The PR job loaded the nearest ancestor recording of its merge base and never
recorded.

Files: `v070-47/labels.json` (hand labels, with the v0.6.0 label beside each), `v070-47/data.json` (per pull request:
mode, candidate sources, suite recording, rows, excluded fields, raise catchers and misses, first line, seconds),
`v070-47/recordings.json`, `v070-47/comments/`, and `v070-47/reports.tar.xz`. The live pilots are in
`evaluation/pilot/{attrs,httpx,jinja}/REPORT.md`.

## Losses

- **Missed raise catchers: Django 24 of 495, sympy 79 of 11,240. The target was 0.** At v0.6.0 the figures were 61
  and 11. No pull request fell back to the static graph. Every miss below was traced to one of three causes.
  - **The test listing misses inherited and generated tests (Django, all 24).** AMU lists a suite's tests statically.
    It misses methods defined on a test class's direct parent and methods added at import:
    - `FileBasedCachePathLibTests(FileBasedCacheTests)` lacks the parent's 9 own methods;
    - `SerializerDataTests` is filled by `register_tests`;
    - `NamespacedAdminKeepChangeListFiltersTests` and `OrderWithRespectToGFKTests` are affected the same way.

    Neither the recording nor the PR job can name these tests. Each one, run on its own, records the changed line.
    Affected PRs: 22046 (4), 22095 (9), 22154 (11).
  - **Coverage that depends on test order (sympy 30815, 77 of 3,027).** sympy caches results across tests, so whether
    a test reaches `Expr.as_terms` depends on what ran before it. In the recording's shard the missed tests did not
    reach the changed lines. Recorded on their own, 2 of the 3 tried do. One recording per commit cannot see this.
    Its effect is not bounded.
  - **Crashes (sympy 30776, 1; and 30803, 1).** 30776 produced no report (below). On 30803 the one missed test is
    `test_FourierSeries__operations`, not traced.
- **Two crashes from the cause 3 guard evaluator: Django 22204 and sympy 30776.** Both added a chained comparison as a
  guard: `(21,) <= self.connection.oracle_version < (23,)` and `if not -n <= i < n:`. `Guards.truth` hands a chained
  comparison to `value`, which hands it back at the same depth, until a RecursionError. Both comments read "no flip
  count". At v0.6.0 both were right.
- **First line right on 40 of 47 (v0.6.0: 43).** 3 fixed, 6 now wrong:
  - **Django 22095: regressed.** `_makedirs = os.makedirs if PY315 else makedirs` ran in 151 tests, but the new
    conditional-expression guard marks the hunk "not executable on Python 3.14 here". Only one operand is gated. The
    line itself runs.
  - **Django 22204 and sympy 30776: crashes** (above).
  - **sympy 30741.** It says "3 of 17 changed hunks ran in no test" after a head run cut at 640 of 13,207 candidates.
    The pull request's own `test_Matrix_berkowitz_charpoly` calls one of those three. A cut-off run cannot say "ran in
    no test".
  - **Flask 5812.** The line lists `ctx.py:235/242/251` as code that ran in no test. These are docstring or comment
    edits inside a block that mixes unchanged and changed lines. Re-applying the block for "not code" ignores the
    unchanged lines inside it.
  - **Flask 6133.** "(at import)" for a new method whose body ran only in the pull request's own test.
  - 5812 and 6133 had the same defects at v0.6.0 and were labelled right then. Applying the same rubric, v0.6.0 is 41
    of 47.
- **Real rows of v0.5.1 retained: Flask 22 of 22, sympy at most 2 of 11 (as at v0.6.0).** Cause 5 did not bring the
  sympy rows back.
  - **sympy 30803 (3 rows):** still 0. 677 differing calls were dropped as unstable on the rerun, with 44 fields
    excluded.
  - **sympy 30741 (6 explicit-x rows, Poly → PurePoly):** not among its 179 rows. All 179 return PurePoly on both
    sides. Whether the 2 default-symbol rows are among them can't be told from the 300-character previews.
- **2 false rows remain: Django 22026.** Both are on `ModelDetailView.get_context_data`, with equal previews. They are
  not traced and are counted false, as at v0.6.0.
- **Cost.** Median job: Django 111 s (v0.6.0: 393 s), sympy 534 s (724 s), Flask 103 s (61 s). The base side's second
  paired run is the Flask increase.
- **Recording cost.** Django: 4 shards of about 2.5 min each in parallel, 3.4 MB per commit. sympy: 4 shards of about
  10–19 min each, 5.3–5.4 MB per commit.
  - The recordings hold 17,223–17,376 of about 19,300 Django tests. Most of the rest skip here (Postgres, GIS, Oracle,
    MySQL, memcached, Playwright).
  - sympy: 12,007–12,262 of 12,853–13,234.

## Numbers

Labels: rubric v0.6.0: v2, except "41 with the same rubric", which is v3; v0.7.0: v3 (rubric in `evaluation/PROTOCOL.md`).

| | v0.6.0 (`43d8b82`) | v0.7.0 (`43bcd672`) |
|---|---:|---:|
| First line right | 43 of 47 (41 with the same rubric) | 40 of 47 (3 fixed, 6 wrong) |
| Adds something / nothing / noise | 19 / 24 / 4 | 20 / 24 / 3 |
| Behaviour rows: real / false | 384 / 41 | 724 / 2 |
| Real rows of v0.5.1 still found | 24 of 33 | 24 of 33 at most (Flask 22 of 22, sympy ≤ 2 of 11) |
| Record fields excluded base-vs-base | – | 504 (Flask 15, Django 85, sympy 404) |
| Static fallback ("no suite recording for base") | 37 of 37 | 0 of 37 |
| Whole suite run on both sides | 10 of 47 (Flask) | 10 of 47 (Flask) |
| Raise catchers missed: Flask | 0 of 1,490 | 0 of 1,486 |
| … Django | 61 of 495 | 24 of 495 |
| … sympy | 11 of 11,237 | 79 of 11,240 |

Real rows by pull request:
- Flask 5812: 366, all on the three context functions, real and intended.
- Flask 5898: 6.
- Django 22154: 169 on `_apply_rel_filters`. The queryset no longer pins `_db` and carries the instance as a hint.
- Django 22169: 2 on `JoinPromoter.update_join_types`, for XOR.
- sympy 30741: 179, counted real and not traced row by row.
- sympy 30841: 2 on `singularities`.

Each was checked against its pull request's diff.

False rows removed by base-vs-base exclusion:
- Django 22026: 6 `_django_version` rows and 1 path-parts row.
- Django 22171: 3 `_django_version` rows.
- sympy 30781: 30 `_mhash` rows.

None of the exclusion is a per-value rule. The excluded fields are those that differed between two runs of the merge
base, one of them under another hash seed.

First lines changed against v0.6.0 (all in `labels.json`):

Labels: rubric v0.6.0: v2; v0.7.0: v3 (rubric in `evaluation/PROTOCOL.md`).

| PR | v0.6.0 | v0.7.0 |
|---|---|---|
| Django 22158 | wrong: "ran on both sides (at import)" | right: "not executable on Python 3.14 here (needs PY315)". The phrase is printed twice. |
| Django 22186 | right, but the Windows line was not named | right: "not executable on darwin here (needs os.name == 'nt')" |
| Django 22171 | wrong: 3 false rows | right |
| sympy 30781 | wrong: 30 false rows | right |
| Django 22095 | right | wrong: a version-gated expression marks a line that ran as "not executable" |
| Django 22204 | right | wrong: RecursionError |
| sympy 30776 | right | wrong: RecursionError |
| sympy 30741 | right | wrong: "ran in no test" after a cut-off run |
| Flask 5812, 6133 | right (labelled) | wrong (same defects as then) |

Also changed, and right:
- Flask 5928: names the `else` branch of `sys.version_info >= (3, 11)`.
- sympy 30825: "type annotations only".
- sympy 29693 and 29763: "adds new code only".
- sympy 30571: 29 hunks of the Lark parser ran in no test, because lark is not installed here.

## What would fix the losses (not done here)

1. **List tests by the runner's own discovery, not statically.** Or run the recording per test module and keep
   whatever ran. That covers inherited and generated test methods.
2. **Evaluate chained comparisons in `Guards.truth`, and bound its recursion.** Gate a line only when the whole line is
   on the skipped side, not when one operand of a conditional expression is.
3. **When the head run is cut, a hunk without a recorded run is "not recorded", not "ran in no test".**
4. **Record a test's coverage the same way in the recording and the PR job.** Order-dependent coverage, as with
   sympy's cache, needs either several recordings in different orders or the hunk's whole function as the unit.
