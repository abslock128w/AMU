# Slow-suite trial (sympy): trial record

Written 2026-10-15, before any agent session started. The numbers are in `rules.json`; the scripts read them from
there. Rules otherwise as in the two Django trials (`../blind/TRIAL-RECORD.md`), except where this record says.

## What is being tested

Whether, on a project whose full suite does not fit in the test budget, an agent with AMU's hook ships fewer
regressions than an agent with a dumb hook. Both arms have a hook with the same triggers, charged to the same
ledger. The control arm's hook has no AMU in it (`../blind/dumb_hook.py`). Each agent gets an upstream sympy commit
message and is asked to implement the change. It is not told that anything breaks, and it never sees the upstream diff.

**AMU under test: b4fb71f.** The commit that holds this record has the same `amu/` as b4fb71f. No file under `amu/`
changes between this record and the last session finishing.

AMU's suite at b4fb71f (`pytest tests scripts/test_before_tests_harness.py`, Werkzeug evaluation env): 2,614 passed, 14
skipped, 1 failed. The failure is `tests/test_mcp_verification.py`, the installed package's build-hash check, which
fails on any source change not yet installed, as in every earlier record.

Engine changes since the Haiku rerun (each is its own commit and is reported in `../REPORT.md`):
- 1e69a20 and 0a32a96: the edited-test check, the flake recheck, and the review fixes.
- 533ec61: candidates for renamed and removed definitions.
- ebffb45: deselected candidates are skipped, not split.
- 11479c7: execution in trees whose tests live inside their packages.
- b4fb71f: `test1`-style test names count, and nested functions do not.

The last three were found while preparing this trial: by the sympy configuration, and by a mechanics check of both
hooks on sympy 92e2a7e6. That tree is newer than every task and is not one of them.

The mechanics check, one edit to `rs_nth_root` that breaks tests:
- The dumb hook ran 3 modules in 50 s and listed 10 failing tests.
- AMU at 533ec61 executed nothing, because it found no test directory it recognised; that was fixed in 11479c7.
- At 11479c7, AMU listed 9 of the 10, with 16 candidates against a cap of 150. The missing test,
  `test_ring_series.py::test1`, calls `rs_nth_root` directly but was never recorded as a test, because AMU required
  the prefix `test_`. It was a selection defect, not the cap or the budget; fixed in b4fb71f.
- At b4fb71f, AMU lists all 10. Every case's ranking top 10 is unchanged (`../blind/results/ranking/rank-p6-*.json`).

**Model: Sonnet (`claude-sonnet-5-5`) in both arms.**

## Suite and budget

sympy's full suite was measured once, before anything else in this piece, with its own runner. `bin/test`, serial,
calls pytest's main with sympy's pyproject configuration. The run was in a scratch copy at sympy 92e2a7e6
(2026-10-13), with Python 3.14.6 from the evaluation virtualenv, on a quiet machine:

- **555 s wall** (552.6 s inside pytest);
- 13,297 passed, 737 skipped, 263 deselected (marked `slow` or `tooslow`, which sympy's addopts leave out), 329
  xfailed, 2 xpassed, 0 failed.

- **Test-time budget per run: 185 s**, one third of 555 s, the same in both arms. A full suite does not fit.
- **Hard stop: 3,600 s** of session wall time, which is max(3,600, 3 × 185). The run is scored on its tree as it stood.

Test ids. `bin/test` is pytest, and its test ids are pytest node ids (`sympy/core/tests/test_basic.py::test_equality`),
so they paste into pytest directly. The trial uses pytest, and no sympy runner adapter was needed. `./rt` runs pytest
serially, as `bin/test` does by default, under sympy's own configuration. It takes test paths, node ids and
`-k EXPRESSION`, and refuses other options, so the measured cost of a full suite holds for every agent.

AMU's runner change for this project (ebffb45). sympy's marker filter deselects slow tests even when they are named.
AMU read such a candidate as a missing outcome and restarted pytest up to 12 times to isolate it. It now asks pytest
once which named tests it still selects, and treats the rest as skipped.

## Tasks

Frozen by `scripts/freeze_holdout.py --project sympy --blind --want 8 --name sympy --head 92e2a7e6 --data
evaluation/before-tests/slow-suite` (`sympy.json`, with the screening log in `sympy-protocol.json`), then
`make_tasks.py` (`tasks.json`). Every file is pinned by sha256 in `freeze.json`, and the freeze was committed before any
analysis. The script walks back from 92e2a7e6 through non-merge commits and keeps the first eight that meet all three
of these conditions:
- they break an existing test: a test in a test module the commit touched passes on the parent tree and fails with
  only the commit's production change applied (sympy's default configuration, so slow tests are not run);
- their subject is not review, merge, docs or mailmap housekeeping, and is at least 30 characters long, so the task
  text says what to do;
- they change at most 200 production lines, added plus removed.

86 commits were screened.

| Task | Case | Date | Subject | Frozen breaking tests (outside the lexical top 40) | Production files |
|---|---|---|---|---|---:|
| 1 | sy-434184cd | 2026-10-09 | make as_base_exp literal, like fraction (#26912) | 5 (5) | 7 |
| 2 | sy-aa1004e2 | 2026-10-08 | polys: fix precision calculation in rs_nth_root | 1 (1) | 1 |
| 3 | sy-30eb1f6c | 2026-10-07 | make `trigsimp` more targeted in transformation (#30605) | 2 (2) | 2 |
| 4 | sy-1837fd2a | 2026-10-06 | interactive: keep the original input in the history with isympy -I | 1 (1) | 1 |
| 5 | sy-97cf00f4 | 2026-09-30 | ArrayAdd: do not allow scalar arguments (#30600) | 2 (1) | 4 |
| 6 | sy-94f14043 | 2026-09-28 | fix(acot, acoth): do not extract a minus sign at a possible branch point (#30590) | 2 (1) | 2 |
| 7 | sy-253f564d | 2026-09-24 | printing: elementwise function application notation for array expressions (#30572) | 2 (2) | 3 |
| 8 | sy-c3882307 | 2026-09-16 | Fix algebraicity of negative powers of zero | 1 (1) | 1 |

**The mix differs from the brief: all eight are indirect, none lexical.** Each case has at least one breaking test
outside the 40 tests most lexically similar to the diff. In tasks 5 and 6 one of the two breaking tests is inside the top
40. The owner decided to keep them: eight indirect cases are the harder set, and that favours no arm.

Why the filter gave this mix. In `--blind` mode the script keeps the first eight commits that break an existing test,
of either kind, in screening order. It does not aim for a mix. The lexical window is 40 tests out of about 14,000 in
sympy's default run, and every one of the first eight breaking commits had a
breaking test outside it. The script's closing line ("Frozen 8 indirect and 0 lexical") counts its kept list as
indirect in `--blind` mode whatever the kind. The kinds above come from each case's own `indirect` field, and the lists
are full (40 each).

Task 6's commit message quotes the one-line guard its fix adds (`if arg.is_nonzero and
arg.could_extract_minus_sign():`). That is the message as committed, not the diff, and both arms get it.

Each agent receives `TASK.md`, which is the upstream commit message as committed. sympy's commits carry no ticket
text, and GitHub is not fetched. The agent gets the parent tree as a fresh git repository with one commit. The
upstream commit, its diff and its tests are not in the workspace. sympy's own `AGENTS.md` (which `CLAUDE.md` imports)
is part of the tree, as upstream has it, in both arms. It covers PR etiquette and coding style and mentions
`bin/test quality`.

## Arms

32 runs: 8 tasks × 2 arms × 2 trials. Each run is a Claude desktop-app session started from the coordinating session
with `start_session`: fresh context, working folder the workspace, no worktree, model Sonnet, permission mode
acceptEdits. The workspace's own `.claude/settings.json` holds the hook and the permission rules.

- **Control: the dumb hook** (`dumb_hook.py`). For every `.py` file that differs from HEAD it runs, with pytest under
  the project's configuration:
  - the touched test modules;
  - the test modules under a touched `conftest.py`;
  - test modules named after a touched file in its package's tests directory (`sympy/core/basic.py` →
    `sympy/core/tests/test_basic.py`);
  - every test module that imports it, read with the parser.

  It runs at most 25 modules and reports the tests failing now. It never runs AMU.
- **AMU arm: the AMU hook** (`hook.py`). It runs `python -m amu.core --repo WORKSPACE --full-evidence`, which
  executes ranked candidates on both sides of the uncommitted change. It reports how many existing tests that passed
  before now fail, with their ids (40 at most), and any limit on that execution.

**Fairness rule:** the only difference between the arms is which program the hook command runs. Everything else is
the same:
- the triggers: after Edit, Write or MultiEdit; after a Bash command during which a `.py` file in the tree changed;
  and before a Bash command that runs `./rt`, pytest or `bin/test`;
- the prompt, `./rt` and the 185 s ledger;
- every check charged in full to the same ledger, and no check started with under 20 s left;
- a check on an unchanged `.py` state reuses its result and charges nothing;
- the permission rules;
- the machine.

Each hook's time allowance per check is its own design, fixed before this record:
- the dumb hook takes two thirds of what is left, at most 120 s;
- AMU takes, per side, a third of what is left, between 10 and 60 s.

Neither hook names the tool behind it. Workspace names are shuffled (`w01` to `w32`) and do not name the arm.

Both arms of a task and trial run at the same time, and at most four sessions run at once (both arms of two
task-and-trial pairs). The order is trial 1 of tasks 1 to 8, then trial 2. The owner sends "next" for each batch of
four.

**Not charged, AMU arm only.** Each is a real cost of the AMU arm that this trial does not measure.
- One per-test coverage recording of sympy's default suite at 117eaf45, the parent of the oldest task's commit (task 8),
  as a CI artefact would be (`python -m amu.core --observe`, Coverage.py's C tracer, Python 3.14.6). The first
  recording took 1,631 s. The suite passed: 13,218 passed, 732 skipped, 263 deselected, 329 xfailed, 2 xpassed. It
  attributes 12,420 tests across 1,349 source files, with 2 child processes. It was lost in the restart described below
  and remade once at the same commit, the same way. The remake took 1,342 s, with the same counts: the same outcomes,
  12,420 tests across 1,349 files, 2 child processes. The trial uses the remake. The recording hook is
  placed in the evaluation virtualenv for the run and removed after it.
- **AMU's index, pre-built in every AMU-arm workspace before its session starts** (`prebuild_index.py`, called by
  `build_workspaces.py`), in the directory the hook's checks use. Build times are in `runs/<id>/index-build.json`.
  The five built last, on a quiet machine, took 46.5 to 47.0 s each. The other eleven took 182 to 323 s, and the first
  (w12) took 2,053 s, while other programs loaded the machine during the build. Every index was then reloaded once
  and returned complete in about 1 s, with 1,606 to 1,608 files and about 47,900 records. The owner's reason: in real use the index persists per repository, so a cold build charged on every run
  would measure a cost the product does not have. Cold, it took 48 s of the 185 s on the mechanics check. The first
  check in a session then only relinks the files the agent changed.

The dumb hook needs neither.

## Departure before the start: a restart, and the trial's files moved

The machine restarted at 08:51 IST on 2026-10-15, while the 32 workspaces were being built and before any session
started. Everything under `/private/tmp` was cleared: the sympy clone, the eight frozen parent trees, the coverage
recording and the half-built workspaces. Nothing committed was affected: the suite measurement, the freeze, the
engine commits and these scripts. The evaluation virtualenv lives outside `/tmp` and survived.

What was done, before any session:
- sympy was cloned again, blobless. 92e2a7e6 is present, with the same 63,035 commits behind it.
- The eight parent trees were extracted again from the frozen parent commits. Each task's production patch, taken
  again from the frozen commit pair, matches the sha256 pinned in `sympy.json`, so the tasks are the frozen ones.
  `rules.json` `cases_dir` points the scripts at the new trees. The manifest itself is unchanged and still pinned.
- The coverage recording was remade once (above).
- The trial's files now live in `/Users/Shared/amu-slow-suite-2026-10-15/`: the clone, `cases/`, `record/`, and the
  trial root `slow/` holding `ws/` and `runs/`. That location survives a restart and is outside the owner's home
  directory. No CLAUDE.md exists in `/Users` or `/Users/Shared`, so no ancestor CLAUDE.md reaches a workspace. Each
  workspace holds only sympy's own (see Tasks), in both arms.
- The workspaces were built after the remade recording, on a quiet machine.

## Prompt (both arms, identical)

The text of `desktop_prepare.py`'s PROMPT, with the workspace path filled in, given verbatim in the session's brief and
as `PROMPT.md`. It says `./rt` with no arguments runs the full suite in about 9 to 10 minutes, and that the budget is
185 seconds of test time, charged for every test run, "whether you start it or an automatic check does".

## Measures (`score.py`), as in the Django trials

Reference runs, once per task, of the full default suite: the parent tree (P) and the upstream commit's own tree (U).
Per run, the full default suite on the agent's tree with every `sympy/**/tests/` directory replaced by upstream's at
the task's commit (H, the hidden tests). Scoring runs use pytest-xdist with 8 workers. Agents' runs are serial.

1. **Regressions shipped (headline).** A test that passed on P, passed on U and does not pass on H. Every such test is
   run again by itself, serially, on H, and counts only if it fails again. One that passes alone is listed as
   unstable and not counted.
2. The same list split into tests whose source is identical on the parent and upstream, and tests upstream rewrote.
3. Upstream's new tests that fail on H: reported, not counted.
4. Red tests left in the agent's own tree: tests that passed on P and fail on the agent's tree as shipped, in the test
   modules upstream touched and the ones the agent touched.
5. Whether the agent ran each frozen breaking test before declaring: a completed `./rt` run whose paths or ids cover
   it, or the full suite (a `-k` run counts when its expression occurs in the test id). Separately, whether a hook
   message listed it.
6. Wall time, tokens, test time charged, `./rt` runs, hook runs, hook messages, and checks skipped for budget.

## What would count

Counted per arm over its 16 runs: **runs that shipped at least one regression**, then the number of regression tests.
**The AMU arm wins if fewer of its runs shipped a regression. Equal counts are a tie.** The report's first line gives
the runs with a regression per arm. If AMU does not beat the dumb hook, the first line says so, and by the owner's rule
the agent-facing product is closed.

If AMU does not win, the report lists every AMU-arm run that shipped a regression, with:
- whether the hook fired and what it printed;
- whether the breaking test was among AMU's candidates;
- whether the runner started;
- what the agent did next.

## Rules for runs that go wrong

As in the Django trials:
- A session that ends without any tool call is not a run, and is started once more.
- A run that read upstream's change, or used the network, is removed together with its pair.
- A session that ends with a question is not answered, and is scored as it stands.
- The owner is asked not to answer permission prompts, and a run with an answered prompt is marked.
- On the first AMU-arm session, the transcript is read to confirm the hook's text reached the agent before its next
  tool call. If it did not, the trial stops there.
- Nothing is rerun because of its result.
- These commits are from September and October 2026; both arms share whatever the model knows of them.

## Result (added after the run; nothing above this line was changed afterwards)

**AMU did not beat the dumb hook: 8 of its 16 runs shipped a regression, against 6 of 16 for the dumb hook (20
regression tests against 16). By the owner's rule, the agent-facing product is closed.**

Scores: `results/scores.json`. Per run: `results/runs/<id>/` (ledger, hook log, diff, result). Departures:
`results/departures.md`. Reference runs: `results/references.json`. Every parent and upstream full suite had 0
failures.

| Task | AMU arm: regressions (in tests upstream left alone) | Dumb hook: regressions (left alone) |
|---|---|---|
| 1 | w12 4 (0), w06 4 (0) | w23 4 (0), w22 4 (0) |
| 2 | **w30 3 (2)**, w05 0 | w26 0, w10 0 |
| 3 | **w09 1 (0), w04 1 (0)** | w18 0, w03 0 |
| 4 | w11 0, w29 0 | w25 0, w21 0 |
| 5 | w19 3 (1), w13 3 (1) | w08 3 (1), w24 3 (1) |
| 6 | w31 0, w20 0 | w32 0, w17 0 |
| 7 | w27 0, w01 0 | w28 0, w02 0 |
| 8 | w07 0, w14 1 (0) | **w16 1 (0), w15 1 (0)** |
| Runs with a regression | **8 of 16** | **6 of 16** |

| Measure, per arm over 16 runs | AMU arm | Dumb hook |
|---|---:|---:|
| Regression tests | 20 | 16 |
| Regressions in tests upstream left alone | 4 in 3 runs | 2 in 2 runs |
| Upstream's new tests failing (not regressions) | 5 | 4 |
| Runs leaving a red test in the agent's own tree | 1 (w30, its tree at the hard stop) | 4 |
| Runs whose agent ran every frozen breaking test before declaring | 9 | 6 |
| Runs whose hook listed at least one frozen breaking test | 14 | 12 |
| Runs that declared done | 16 | 16 |
| Session wall time, median / total | 279 s / 17,397 s | 589 s / 16,319 s |
| Test time charged, total (of it, hook) | 1,788 s (1,636 s) | 1,663 s (1,453 s) |
| `./rt` runs / hook runs / hook messages / checks skipped for budget | 29 / 58 / 93 / 12 | 28 / 59 / 102 / 5 |
| Tokens, total incl. cache reads | 16.6 M | 16.5 M |
| Runs with an answered permission prompt | 6 | 10 |

Where the arms differ, on three tasks (tasks 1 and 5 are identical in all four runs, and tasks 4, 6 and 7 are clean):
- **Task 2:** w30's 3 regressions come from its tree at the hard stop. The agent waited on a permission prompt from
  05:3x UTC until 38 minutes after the stop, with an edit half done. Counterfactual, run after scoring: w30's tree
  after the late answer passes all 37 of upstream's `test_ring_series.py` tests. The hook had listed
  `test_nth_root`.
- **Task 3:** both AMU-arm runs fail upstream's rewritten `test_hyperbolic_simp`. Neither edited it, and it passes in
  their own trees, so their `trigsimp` change behaves differently from upstream's. w04's hook listed it mid-run, and
  the agent's next edit cleared it. Both dumb-hook runs match upstream there, but both left `test_issue_12791` red in
  their own trees; upstream's rewrite of that test accepts their behaviour.
- **Task 8:** both dumb-hook runs and one AMU-arm run (w14) fail upstream's rewritten `test_Pow_is_algebraic`. w07
  does not.

Post hoc, labelled as such: without w30, AMU has 7 runs with a regression against 6, still not a win.

### AMU-arm runs that shipped a regression, root cause

| Run | Hook fired | What it printed at the end | Breaking test among candidates | Runner started | What the agent did |
|---|---|---|---|---|---|
| w12, w06 (task 1) | 3 checks each | 1 flip: `test_as_base_exp`, then edited-test rechecks | yes | yes | updated `test_as_base_exp` as the task changes it; the 4 counted tests are ones upstream rewrote, the same 4 in all four runs |
| w30 (task 2) | 7 | `test_nth_root` | yes | yes | blocked on a prompt at the hard stop; scored mid-edit (above) |
| w09, w04 (task 3) | 3 and 4 | 0 flips at the end | `test_hyperbolic_simp` was listed in w04 and then passed | yes | implementation diverges from upstream's rewritten test |
| w19, w13 (task 5) | 2 each | nothing: the first check ran 184 s and spent the budget | no result | no: AMU's static phase never finished | edited by reading only; shipped the same 3 regressions as both dumb-hook runs, which ran their tests |
| w14 (task 8) | 3 | `test_Pow_is_algebraic` | yes | yes | updated the test to its own behaviour; upstream's rewrite expects another; both dumb-hook runs did the same |

**AMU defect found in the run (w19, w13).** For each newly added `raise`, `consequences.handlers` enumerates every
caller path up to MAX_HOPS without a memo. On sympy's call graph around `ArrayAdd.__new__` that is exponential, and the
static phase ran past the whole budget. It cost the AMU arm all testing in both task 5 runs, though the regression
counts there equal the dumb hook's. It was not fixed during the run, because `amu/` was frozen.

### Departures (details in `results/departures.md`)

- The machine restarted before any session. Trees were regenerated and checked against the frozen patch hashes, and the
  recording was remade (above).
- Permission prompts were answered in 16 runs. In w26 and w30 the answer came 38 minutes after the hard stop, so those
  trees were rebuilt to their state at the stop from the transcripts. The trees after the answers are kept as
  `agent-final.diff`.
- From batch 2 on, the owner asked for batches to start without waiting. The app starts sessions only on the owner's
  own message, so each batch still began on one.
- Every session listed the shared notes folder the app offers to sibling sessions; it was empty throughout. No
  transcript shows network use.

### What executed upstream code

sympy's tests only, in copies outside the owner's checkout:
- the one full-suite measurement and the screening of 86 commits;
- the coverage recording, twice (the first was lost in the restart);
- the mechanics checks on 92e2a7e6;
- the 32 sessions' `./rt` and hook runs;
- scoring: 16 reference full suites, 32 hidden-test full suites, suspect reruns and 32 own-tree module runs;
- one diagnostic AMU run on a copy of w19's tree, and the w30 counterfactual.

## Post hoc paired re-analysis (2026-10-15, after the result; no new runs)

**Post hoc. Not a pre-registered measure. On the six tasks the plan calls clean (all but task 2, the permission-prompt
artefact, and task 5, AMU's walk defect), the AMU arm reached a tree with no red test first on 5 of 6, at every wait
threshold tried. That meets the owner's bar, so the win condition for one final trial is written below. It is not
run. Read over all 8 tasks instead, the count is 6 of 8 (5 of 8 at higher thresholds), below the bar.**

Asked by the owner after the result: pair by task, exclude w26 and w30 as prompt-blocked, and report per pair wall
time to declared done, test time charged, red tests left in the agent's own tree, and whether the frozen breaking
test was run. Script: `posthoc.py`; output: `results/posthoc.json`. The 32 scored runs and their saved transcripts are
the only input.

**Which six.** The bar ("five of six clean tasks") comes from `AMU-PRODUCT-PLAN.md` section 13, which does not list
the six. Its paragraph names task 2 as a permission-prompt artefact and task 5 as the task AMU's defect stopped from
testing; the other six are 1, 3, 4, 6, 7 and 8. That is the reading used for the decision. w26 and w30 are both in
task 2 (trial 1), so the owner's exclusion alone leaves 8 tasks with task 2 on one run per arm; the 8-task counts are
given too. The decision differs between the two readings.

**Wall time is corrected for permission prompts.** 16 runs waited on prompts the owner answered, some for 15 minutes
or more (w01 and w02 each 2,500 s or so). For every tool call over 20 s, the wait is the gap from call to result, less
test and hook seconds the ledger logged inside the gap, less 5 s. "Active" is wall minus waits. Per task, each arm is
the mean of its runs. An arm reaches a tree with no red test only if all its runs do.

| Task | Runs (AMU; dumb) | Active s to declared, AMU / dumb | Test s charged | Red tests in own tree | Share of frozen breaking tests run | AMU first to a no-red tree |
|---|---|---|---|---|---|---|
| 1 | w12, w06; w23, w22 | 161 / 178 | 108 / 132 | 0 / 0 | 0.4 / 0.4 | yes |
| 2 | w05; w10 | 170 / 184 | 114 / 93 | 0 / 0 | 1.0 / 1.0 | yes (no at 60 s) |
| 3 | w09, w04; w18, w03 | 209 / 270 | 139 / 178 | 0 / 1.0 | 0.75 / 0 | yes: dumb arm left a red test |
| 4 | w11, w29; w25, w21 | 64 / 66 | 27 / 8 | 0 / 0 | 1.0 / 0.5 | yes, by 1 to 2 s |
| 5 | w19, w13; w08, w24 | 339 / 250 | 185 / 115 | 0 / 0 | 0 / 1.0 | no |
| 6 | w31, w20; w32, w17 | 158 / 238 | 117 / 175 | 0 / 0 | 0.5 / 0.5 | yes |
| 7 | w27, w01; w28, w02 | 222 / 139 | 124 / 69 | 0 / 0 | 0.75 / 0.25 | no |
| 8 | w07, w14; w16, w15 | 113 / 111 | 80 / 83 | 0 / 0.5 | 1.0 / 0.5 | yes: dumb arm left a red test |

AMU-arm task wins on the six (tasks 1, 3, 4, 6, 7, 8), then on all 8; default 20 s threshold, 60 s and 120 s in brackets:
- first to a tree with no red test: **5 of 6 (5, 5)**; 6 of 8 (5, 5);
- lower active time: 4 of 6 (4, 4); 5 of 8 (4, 4); lower raw wall time: 5 of 6; 5 of 8;
- less test time charged: 4 of 6; 4 of 8;
- fewer red tests in its own tree: 2 of 6, 4 ties; 2 of 8, 6 ties;
- larger share of frozen breaking tests run: 4 of 6, 2 ties; 4 of 8, 3 ties.

What the wins rest on, so the next trial is read with them in mind:
- **Tasks 3 and 8** are won because dumb-hook agents left a red test in their own tree. In task 3 that red test
  (`test_issue_12791`) is one upstream rewrote to accept the dumb arm's behaviour, and it is the AMU arm that shipped
  regressions there. Finishing first with no red test is not the same as matching upstream.
- **Task 4** is won by 1 to 2 s of a 65 s mean.
- **Tasks 1 and 6** are the only wins by a margin (17 s and 80 s of active time).
- Task 2, excluded from the six, flips with the threshold and rests on one run per arm. Task 5, excluded, is a loss
  caused by the defect now fixed in 087e832.

### Pre-registered win condition for one final trial (written, not run)

Hypothesis from the re-analysis above: with AMU's hook, an agent reaches a tree with no red test sooner than with the
dumb hook, without shipping more regressions. Nothing below has been run. Running it needs the owner's decision.

- **Tasks:** 8 new sympy commits, frozen with the same script and filters from a window that excludes the 86 already
  screened, committed before any session.
- **Arms and runs:** as in this trial (Sonnet, AMU hook against dumb hook, 2 runs per task per arm, 185 s test
  budget, same prompt). AMU at 087e832 or later, frozen in the record before the first session.
- **Permission prompts removed, not corrected for:** each workspace allows `sed -i`, `python3`, `cat >` and `./rt`
  without asking. A run that still waits on a prompt is excluded with its task.
- **Primary measure, per task:** each arm's mean active time to declared done, where active time is wall time less
  prompt waits (`posthoc.py`'s method, 20 s threshold). An arm reaches the target only if every run leaves no red
  test in its own tree; an arm that does not loses the task.
- **Win:** the AMU arm is first on at least 5 of 6 tasks, counting only tasks with no excluded run. With 7 or 8
  tasks compared, at least 6 of 7 or 7 of 8.
- **Guard:** the AMU arm's runs that ship a regression (this trial's headline measure and scorer) are no more than
  the dumb hook's. If the guard fails, the trial is lost whatever the primary measure says.
- **Otherwise:** lost, and the agent line is closed with nothing further to test.
