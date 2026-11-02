# The 47 performance-test pull requests re-run after fixes #71–#75 (2026-11-02)

The v0.4.2 performance test ([../performance-v042/REPORT.md](../performance-v042/REPORT.md)) listed defects 1–5 as the cause of all 102 false behaviour rows and 13 false evidence tests. Each was fixed in its own PR with a test:

| Fix | PR | Defect |
|---|---|---|
| 1 | #71 | side-by-side ran the changed function in the old module's globals, so names the change imports were missing (NameError) |
| 2 | #72 | side-by-side ran changed methods on objects the old code built, which lack attributes the new constructor sets (AttributeError) |
| 3 | #73 | calls captured from the old code were bound positionally to a changed signature (inserted or moved parameters) |
| 4 | #74 | arguments were captured after defaults were bound, so a changed default value was never compared |
| 5 | #75 | the evidence confirmation reran all failing tests together, so one broken test's cascade repeated |

Same 47 pull requests (`../performance-v042/freeze.json`), same driver (`../performance-v042/run.py`, now taking the engine, ref and output tag from `AMU_PERF_ENGINE`, `AMU_PERF_REF`, `AMU_PERF_TAG`), engine at `main` `6b84c99`. Labels were redone by hand for every pull request whose comment changed beyond test counts; the others keep their v0.4.2 labels.

## Results

| | Django | sympy | Flask | All |
|---|---:|---:|---:|---:|
| First line correct | 12 → 13 of 17 | 17 → 17 of 20 | 8 → 10 of 10 | 37 → 40 of 47 |
| Adds something a reviewer would want | 6 → 7 | 7 → 7 | 2 → 5 | 15 → 19 |
| Adds nothing | 9 → 9 | 12 → 12 | 4 → 4 | 25 → 25 |
| Adds noise | 2 → 1 | 1 → 1 | 4 → 1 | 7 → 3 |
| Counted flips | 0 → 0 | 0 → 0 | 0 → 0 | 0 → 0 |
| Behaviour-difference rows | 9 → 0 | 6 → 11 | 110 → 41 | 125 → 52 |
| … real | 0 → 0 | 6 → 11 | 17 → 20 | 23 → 31 |
| … false | 9 → 0 | 0 → 0 | 93 → 21 | 102 → 21 |
| False evidence tests (cascade) | 0 → 0 | 0 → 0 | 13 → 0 | 13 → 0 |
| Calls set aside: object built before the change (fix 2) | — | — | 0 → 66 | 0 → 66 |
| Calls set aside: did not fit the changed signature | — | — | 111 → 186 | 111 → 186 |
| Median job time | 276 → 107 s | 295 → 305 s | 13 → 13 s | |
| Worst job time | 691 → 326 s | 452 → 491 s | 15 → 18 s | |

False rows fell from 102 to 21. All 21 left are on one pull request, Flask #5812 (see below). The 13 false evidence tests are gone.

## Per pull request

| PR | Before (v0.4.2) | After | Fix |
|---|---|---|---|
| Flask #5818 | noise; first line wrong; 33 rows, 0 real | something; first line right; 0 rows, 21 of 22 functions compared | 3: rebinding by name. All 33 misbound rows are gone, and 186 calls that could not be rebound are counted, not listed. |
| Flask #5898 | noise; first line wrong; 0 rows (the 302 → 303 default change missed) | something; first line right; 6 rows, all real | 4: `redirect()` and `App.redirect()` show `302 FOUND` → `303 SEE OTHER` on 3 captured calls each. Each row is marked as assuming the caller left `code` at the default, which is true for all three tests. No Flask test checks the default, so this is the only signal of the change. |
| Flask #5928 | noise; 14 evidence tests, 13 false | something; 1 evidence test (`test_robust_teardown`, the PR's regression test) | 5: the 13 cascade failures pass alone on the base and are counted unstable. |
| Flask #5812 | noise; 77 rows, 17 real | noise; 35 rows, 14 real (60% false, was 78%) | 2: 66 calls set aside. 21 false rows are left (next section). |
| Django #22026 | noise; first line wrong; 9 rows, 0 real (NameError) | something; first line right; 0 rows | Not fix 1. Side-by-side could not start ("Argument list too long" at 17,539 test labels), so no call was compared. Fix 1 is shown by its unit test, not by this pull request. |
| sympy #30741 | 4 rows, all real | 8 rows, all real | 2 rows come from a test the v0.4.2 run did not reach in time. 2 come from fix 4: `charpoly()` with the default symbol now gives a Dummy generator (`lambda` → `_0`), which the PR intends. |
| sympy #30803 | 2 rows, all real | 3 rows, all real | One more row of the same kind (`_rebuild_expr` returns a `FracElement`). It comes from more calls captured, not from a fix. |
| Django #22176 | noise (subTest evidence) | noise, unchanged | Out of scope (defect 6). Side-by-side did not start this time either (Argument list too long). |

The other 39 pull requests differ only in tests run and time.

## What is still false: Flask #5812

Flask #5812 merges the request context into the app context. The test process pushes the old `RequestContext`, and the as-changed code looks for the request on the app context. The 21 false rows are of four kinds:

- **15 rows: `RuntimeError: There is no request in this context`.** Raised by `preprocess_request`, `dispatch_request`, `process_response`, `do_teardown_request` and `_prepare_send_file_kwargs`. The input is the old context state, not a behaviour change. Fix 2 covers only a missing attribute, so these are not set aside.
- **1 row: `has_request_context()` True → False.** Same cause.
- **4 rows: `'AppContext' object has no attribute '_after_request_functions'`.** These should have been set aside by fix 2, but were not: `fresh_attributes` compares the names set in constructors per file, not per class. The old `RequestContext.__init__`, in the same file, already set `_after_request_functions`, so the name did not count as new for `AppContext`. Making the comparison per class would set these 4 aside.
- **1 row: `'NoneType' object has no attribute 'allowed_methods'`.** The old context has no matched URL rule.

The real rows went from 17 to 14. Fix 2 sets aside only AttributeError results, and none of the real rows is one, so the 3 rows lost are from which calls were captured, not from the fix.

## Confounds

- **Tests run and job times.** The re-run ran more tests in less time on Django: 22026 ran 5,610 → 17,539 tests, and the Django median job fell from 276 to 107 s. The budget (300 s per side) and the restored caches were the same. None of the five fixes speeds up test runs (fix 5 adds reruns), so the difference is the machine, not the engine; the times above do not measure the fixes. More tests reached also means more captured calls, which accounts for the extra real rows on sympy #30741 and #30803.
- **Argument list too long.** With more tests selected, Django #22026 and #22176 crossed the command-line length limit, so side-by-side did not start on either. In v0.4.2 it started on both. This is the known unfixed defect from the v0.4.0 re-score. On #22026 it hides whether fix 1 works on real code.
- **The run restarted twice.** It stopped once when the session ended and once when the disk filled up. Each partial pull request was deleted and run again from scratch, so every result is from one complete run.

## Found, not fixed

- Fix 2 compares constructor attribute names per file, not per class (4 rows on Flask #5812).
- Side-by-side does not set aside failures caused by a context or global state that the old code set up (`RuntimeError: There is no request in this context`; 16 rows on Flask #5812).
- "Argument list too long" stops side-by-side on large Django selections. The gap then also says "none of the 3 comparable changed functions was called by the tests run on the code before the change", although the runner never started.

## Files

- `labels.json`: all 47 labels after the re-run (8 changed, 39 carried over from v0.4.2).
- `<repo>/<number>.md`: the 47 comments as rendered.
- `reports.tar.xz`: `report.json` and `done.json` for every pull request.
- `compare.py`: the per-PR comparison against the v0.4.2 run.

## Follow-up: the three leftovers fixed (#77–#79), 2026-11-02

Each leftover defect above got its own PR with a test that fails on the code before it. The three pull requests they affect (Flask #5812, Django #22026, Django #22176) were re-run on all three together (engine `0b8312b`, `main` plus #78 and #79). The other 44 cannot change: none of them had a false row or a side-by-side start failure. `labels.json` holds the updated labels; the comments and reports are in `leftovers/`.

| Fix | PR | What it changes |
|---|---|---|
| Attributes new per class | #77 | Constructor attributes count as new per class (and its subclasses in the same file), not per file. |
| Uncopyable arguments put back | #78 | Arguments that copy neither way get their attributes put back after each run. As-changed failures inside a method of an object the old code built are set aside. |
| Command line too long | #79 | Labels run in halves when the command line exceeds the system limit. There is no "none … was called" gap when the runner never started. |

The `RuntimeError` rows on Flask #5812 were not what this report said above. They were not context state the old code set up. Tracing the run showed that `AppContext(app)` cannot be copied (the app holds a lock), so side-by-side ran the as-changed `AppContext.__init__` on the live context being built. That left `_request = None` and the other new attributes on it, and the test went on to push that half-rebuilt context. Every later as-changed function that read it failed. #78 puts such arguments back after each run.

| | Before #77–#79 | After |
|---|---:|---:|
| Flask #5812 rows | 35 (14 real, 21 false) | 17 (16 real, 1 false) |
| Flask #5812 calls set aside as built before the change | 66 | 91 |
| Django #22026 side-by-side | did not start | 71 calls of 2 functions compared, 0 rows |
| Django #22176 side-by-side | did not start | 28 calls of 2 functions compared, 0 rows |
| All 47: behaviour rows | 52 (31 real, 21 false) | 34 (33 real, 1 false) |
| All 47: adds noise | 3 | 2 (Django #22176 subTest evidence, sympy #29693 adds-only) |
| All 47: first line correct | 40 | 40 |

Django #22026 now also shows #71 on real code. `get_prefetcher` was compared on captured calls, and the 9 `NameError` rows of v0.4.2 do not come back.

What is still false:
- **Flask #5812, 1 row:** `make_default_options_response` raises `'NoneType' object has no attribute 'allowed_methods'`. The old context has no matched URL rule where the new code looks for one.
- **Two noise PRs:** Django #22176 (subTest failure blocks not parsed) and sympy #29693 ("adds new code only" for a method found by name dispatch). Both are outside these fixes.
