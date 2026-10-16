# Brief 4, Phase A: from test picker to difference finder (2026-10-16)

Branch `difference-finder`, one commit per item. Measured on the frozen sets and AMU's own counter; no agent trial.

## Summary

| Item | Check | Result |
|---|---|---|
| A1 replay the PR's own tests on the base | every "new tests only" case gives a change-evidence row; none from a pass-to-pass test | 8 of 9 cases run; the ninth (Click 405a5481) is a refactor whose new tests pass on the base too; 0 rows from pass-to-pass. 3 Werkzeug cases not run (outside the approved routing suite) |
| A2 flip-trust layer | sympy 32 trees: no confirmed flip lost, every flip labelled | 33 of 33 kept, 54 of 54 labelled; 21 more flips found, each a frozen breaking test or scored regression. Two defects found by the check on the way, both fixed |
| A3 selection by subtraction | ranking does not drop; candidates and time beside the old | ranking top 10 identical on all 39 cases; truth tests among candidates 100 → 104 of 105; candidate lists 2 to 5 times longer |
| A4 base cache, plugin autoload off | a second run against the same Django base skips the base side, and the comment says so | dj-65a9f141: 0 run, 2 from cache, comment says so; dj-7dee35f5: 1 of 362 ran again (not a pass) |
| Budget | warm under 2 s on Django before execution | 0.87 to 1.72 s on all five cases (old engine 0.83 to 2.30 s) |

## A1. The PR's own tests, replayed on the base (`amu/core/replay.py`, `amu/core/shadow.py`)

Every test the change adds or edits runs first, on the head and on the base code with the tests as the change leaves
them (production files at the base, test files at the head). A test counts as edited when its own syntax changed or any
module-level value it reads (followed through the names those read) changed, so a new row in a parametrize table
counts. Verdicts:
- fails on the base, passes on the head: `change evidence: the PR's own test`. The base failure must repeat in a fresh
  process. A test that does not even load on the base (it imports what the change adds) counts as failing there and
  says so;
- passes on both: none, and the comment says these tests show nothing about the change;
- fails on both, or passes on the base and fails on the head: a gap naming the tests.

Check, `evaluation/difference-finder/a1_replay.py` (only the touched tests execute):

| Case | New tests only | Truth tests | Touched found | Evidence | Pass on both | Fail | Seconds |
|---|---|---:|---:|---:|---:|---:|---:|
| click-v2-9f0bcdf0 | no | 8 | 8 | 7 | 1 | 0 | 4.3 |
| click-v2-b90faad1 | no | 39 | 39 | 13 | 26 | 0 | 6.7 |
| click-v2-d0368817 | no | 1 | 1 | 0 | 1 | 0 | 2.1 |
| click-v2-e2bddbc8 | no | 39 | 39 | 11 | 28 | 0 | 3.9 |
| click-v2-f67c2bb6 | no | 1 | 1 | 1 | 0 | 0 | 4.0 |
| click-v2-05f6fd0d | yes | 1 | 1 | 1 | 0 | 0 | 1.7 |
| click-v2-405a5481 | yes | 2 | 2 | **0** | 2 | 0 | 1.1 |
| click-v2-f25f697a | yes | 4 | 4 | 3 | 1 | 0 | 2.5 |
| click-v2-22177f85 | yes | 1 | 1 | 1 | 0 | 0 | 2.0 |
| click-v2-271effb3 | yes | 1 | 1 | 1 | 0 | 0 | 2.3 |
| 03-594452f6 | no | 1 | 1 | 1 | 0 | 0 | 2.8 |
| 12-6d7c0b4e | yes | 1 | 1 | 1 | 0 | 0 | 1.9 |
| 14-c678a231 | yes | 1 | 1 | 1 | 0 | 0 | 2.2 |
| 15-ab16e62d | yes | 1 | 1 | 1 | 0 | 0 | 1.9 |

- The tests AMU treats as touched equal the frozen truth in all 14 cases.
- **The check as written fails on one case, correctly.** Click 405a5481 moves `help` from `Option` and `Argument` to
  `Parameter`; its two new tests pass on the base, because both classes already handled `help` there. "Pass on both"
  is the right answer; the comment says those tests show nothing about the change.
- No evidence row comes from a pass-to-pass test: every evidence row has a failing (or unloadable) base outcome that
  repeated in a fresh process.
- Not run: Werkzeug 01-4da3786f (`tests/test_urls.py`), 13-1b8bc581 and extra-5b0e3209 (`tests/test_http.py`). The
  brief approves "Werkzeug routing"; I asked before running them and had no answer.
- The Click set is holdout 1 (`click-sealed-v2.json`), exposed since its first run. Werkzeug 03 was missed by the first
  version (its edit is a new row in a module-level table); the fingerprint was widened after seeing it.

## A2. Flip-trust layer (`amu/core/trust.py`)

A flip counts as a failure only when:
- **same order on both sides**: pytest-randomly with seed 20261016 when the project has it, otherwise the same explicit
  order and `PYTHONHASHSEED=0` on both sides. pytest-randomly is not installed in the evaluation environments, so every
  run here used the second.
- **reach**: rerun alone in a fresh process, the test ran at least one changed line on the head. The tracer is a
  `sitecustomize` placed first on `PYTHONPATH` (so child processes are traced too, and the environment's own
  sitecustomize still loads): `sys.monitoring` LINE events with DISABLE after each location on 3.12+, `settrace`
  restricted to changed files below. A flip that ran no changed line is `not caused by this change`.
- **three reruns on the head**: twice in a row in one process (a pytest plugin, `-p amu_trust_rerun`; for unittest the
  label twice); alone in a fresh process; after the rest of the candidates, in order. A flip that passes in any is
  `unstable`, naming which. When the fresh-process rerun alone passes and the after-the-rest rerun fails, the same order
  is run on the base: the label then says whether the order dependence came with this change.

Every flip leaves with one label: `fails`, `unstable` or `not caused by this change`. Only `fails` is counted; the
others are listed under "Flips not counted". A rerun a runner cannot make is named on the flip's row: Django's runner
drops a label given twice, so the same-process rerun is never made there.

Decision to flag: an earlier review had a test that an order-dependent regression is **not** demoted to unstable. The
brief's rule demotes it. I followed the brief; the row says "an order dependence this change brings" when the base
passes in the same order. If you want those counted as failures, it is one condition in `trust.triad`.

Found by the check and fixed: the reruns were charged to the per-side budget, so on sympy w19 the edited-test check
had no time left and three flips the old engine confirmed went unreported. Verifying flips now has its own clock
(`trust_seconds`).

Sympy re-score (`a2_sympy.py`, `a2_score.py`): each of the slow-suite trial's 32 recorded trees (the workspace's parent
plus the run's `agent.diff`), bare amu with a 60 s budget per side, old engine (main, 96d7fb5) and new, on scratch
copies.

| Pass | Engine | Old engine's confirmed flips kept | Flips, all labelled | Labels |
|---|---|---:|---:|---|
| 1 | 0ed2c7a (A1 + A2) | 30 of 33 | 46 | 46 fails |
| 2 | 6136127 (+ A3, A4) | 12 of 33 | 33 | 12 fails |
| 3 | ee4048e (final) | **33 of 33** | **54** | 54 fails, 0 unstable, 0 not caused |

- Pass 1 lost three flips in w19: the reruns used the per-side budget and the edited-test check was skipped. Fixed:
  reruns have their own clock.
- Pass 2 lost 21, all `test edited, not fixed`: the larger A3 candidate list filled the budget and the same check was
  skipped. Fixed: the change's own test files are always checked, with at least the 20 s confirmation allowance.
- The 21 flips pass 3 finds beyond the old engine are each a frozen breaking test of the task or a regression the trial
  scored on that tree (w14 and w16: `test_Pow_is_algebraic`, a shipped regression the old engine did not report).
- No flip on these trees was unstable or failed the reach check, so the sympy set does not exercise those labels; the
  unit tests do. Median time per tree: 145 s old, 153 s new.

False-flip rate on the 50-PR counter (`python -m amu.ci.counters abslock128w/AMU`, 2026-10-16, before this PR): 3 pull
requests, 1 flip rerun in a fresh job, 0 false. The counters now also record flips labelled unstable and not caused,
and change-evidence rows; the fresh-job rerun covers only counted flips, so its false-flip rate is the triad's.

## A3. Selection by subtraction (`amu/core/select.py`)

Candidates: every test in a test file within the file-level import closure of the changed files, depth cap 10; a hub
(import fan-in above the 99th percentile of files and above 20) is not expanded through its importers, so tests past it
come in only from a recording that saw them run the change; plus static reach; plus the change's own tests. Order: own
tests, most recent confirmed flip, shortest measured duration (from earlier runs' junit times), then the old ranking as
a tiebreaker. The 150 cap is gone; the time the budget allows bounds what runs. The diverse ranking now orders only the
40 shown rows.

Static ranking, rerun with the harness (`df-a3-*`): every case's top 10 identical. Werkzeug 10/16 and 16/16,
requests and itsdangerous 6/8 and 8/8, Click holdout 3 8/8 and 8/8.

What bare amu would run on the 26 testmon cases (`a3_select.py`, execution stubbed):

| Set | Truth tests among candidates, old → new | Mean candidates, old → new | Slowest analysis, old → new |
|---|---|---|---|
| Werkzeug (10) | 21/21 → 21/21 | 93 → 472 | 0.50 → 0.37 s |
| requests and itsdangerous (8) | 22/23 → 23/23 | 61 → 181 | 0.26 → 0.21 s |
| Click holdout 3 (8) | 57/61 → 60/61 | 150 → 482 | 0.46 → 0.48 s |
| All 26 | 100/105 → 104/105 | | |

At the default 60 s per side (about 720 tests at the default rate), every truth test found is inside what runs. The
cost of the larger sets is test time, bounded by the budget.

On sympy the closure is almost the whole suite (about 12,900 candidate tests, 16 hubs gated): subtraction removes little
there, and the budget and the order decide what runs. That is how the A2 check's second pass lost flips (above).

## A4. Base-side cache and process cost (`amu/core/basecache.py`, `amu/core/runners.py`)

- **Plugin autoload off.** pytest runs with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and every installed plugin named with
  `-p`, except those that reorder, rerun, select or decorate tests (xdist, testmon, cov, rerunfailures, flaky, sugar,
  html, picked, random-order, instafail). pytest-randomly is loaded with the fixed seed. unittest and Django runners
  load no pytest plugins.
- **Cache of base-side passes.** Per test file, keyed on the file, its transitive first-party imports, conftest.py files
  above it, the interpreter (version and prefix), lock and configuration files, and the runner arguments. Only passes;
  a failure always runs again. Stored under `.amu/index/<v>/base-passes/`, which the action already caches.

Django, two runs of bare amu on the same tree and index (`a4_django.py`, budget 120 s):

| Case | Flips | Run 1 base side | Run 2 base side | Run 2 comment |
|---|---:|---|---|---|
| dj-537c6df3 | 0 | nothing needed | nothing needed | |
| dj-65a9f141 | 2 | 2 run, 2 cached | 0 run, 2 from cache | "The merge base side was not run: all 2 outcomes it needed are passes cached from an earlier run…" |
| dj-7dee35f5 | 362 | 362 run, 361 cached | 1 run, 361 from cache | |

On dj-7dee35f5 one base outcome was not a pass and ran again, as designed.

Budgets: warm analysis before execution on Django, execution stubbed, three runs each after a cold build, quiet machine:

| Case | Old engine | New |
|---|---|---|
| dj-537c6df3 | 1.03 to 1.06 s | 0.94 to 0.97 s |
| dj-65a9f141 | 1.20 to 1.24 s | 1.03 to 1.07 s |
| dj-7dee35f5 | 2.28 to 2.30 s | 1.71 to 1.72 s |
| dj-80ea222c | 1.30 to 1.33 s | 1.07 to 1.10 s |
| dj-f1d11117 | 0.83 to 0.86 s | 0.87 to 0.90 s |

dj-7dee35f5 removes a definition, which costs a scan for the old name; it was over budget before this work. The import
map and selection add about 0.05 s; the diverse pick now stops scanning early (same picks, checked by a randomized
test), and it orders only the 40 shown rows.

Comment size: the cap is unchanged (60,000 characters, halving lists to fit) and the new sections go through it;
`tests/ci/test_render_post.py` covers 2,000 flips.

## Files

`amu/core/replay.py`, `trust.py`, `select.py`, `basecache.py` (new); `amu/core/__init__.py`, `shadow.py`,
`runners.py`, `index.py`, `rank.py`; `amu/ci/render.py`, `counters.py`; `tests/core/test_difference_finder.py`,
`tests/core/test_reachability.py`, `tests/ci/test_counters.py`, `tests/ci/test_render_post.py`;
`evaluation/difference-finder/*`.

## Tried and rejected

- Running the in-process rerun by giving pytest the same item twice: pytest's setup state rejects an item that was not
  torn down. The plugin reruns through `runtestprotocol` with teardown in between instead.
- Writing the reach tracer into the project's site-packages (as the recorder does): `sitecustomize` on `PYTHONPATH`
  touches nothing installed.
- Bumping the index version for the import map: it would have thrown away stored recordings (686 s on Django). The
  snapshot file got a new name instead.
- Hub threshold at the 99th percentile alone: on small trees it made ordinary files hubs. A floor of 20 importers.

## What executed upstream code

Click holdout 1 and Werkzeug routing touched tests (A1); sympy's tests on 32 recorded trial trees, twice per engine
(A2); Django's tests on three Phase 0 case copies, twice each (A4). All in scratch copies.
