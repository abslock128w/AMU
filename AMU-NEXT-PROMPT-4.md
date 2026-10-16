# AMU session brief 4: from test picker to difference finder (written 2026-10-16)

Start in `/Users/Abslock128/Documents/ChatGPT/AMU/observed-tests-cycle-2026-10-13` on `main`
(github.com/abslock128w/AMU, release v0.1.0 published as `anti-messup`). Work on a new branch
`difference-finder`. Read first, in this order, and do not re-derive what they record:
`AMU-PRODUCT-PLAN.md` sections 9 to 13; `evaluation/before-tests/REPORT.md` from "Runner
adapter" to the end; `AMU-CI-BRIEF.md`; and the research report
`../reports/Making AMU far better.md` in full, with its notes under
`../research_notes/Making AMU far better/` when a number needs its source.

## Why this brief exists

Existing regression tests detect semantics-changing Python edits at 7.6 percent recall in the
one study that measured it (ChangeGuard, FSE 2025). AMU's own data agrees: 5 of 18 Click
commits and 11 of 250 Django commits broke any existing test. AMU today is silent on the
nine edits in ten whose behaviour change no test reaches. The product is the GitHub Action
comment for human reviewers. This brief makes that comment say something true and useful on
every pull request, in three phases ordered by evidence per unit of work. The seven agent
trials are closed; nothing here is measured by an agent trial.

## Phase A: make every finding believable, and find the free ones (days)

### A1. Replay the pull request's own tests against the base tree
For every test the PR adds or edits, run it on the base tree and on the head tree. Report
fail-on-base and pass-on-head as `change evidence: the PR's own test`, with the test id. A
test that passes on both is reported as none. A test that fails on both is reported as a
gap. This covers the quarter of commits that only add tests and costs one extra runner call.
Check: on the Werkzeug and Click development sets, every case whose truth was "new tests
only" now produces a change-evidence row, and no case produces one from a pass-to-pass test.

### A2. Flip-trust layer
A flip is reported as a failure only after:
- both sides ran with the same seeded order (pytest-randomly with a fixed seed where pytest
  is the runner; stable collection order otherwise);
- the reach check: the flipped test executed at least one changed line on the head side,
  from the per-test coverage or the tracer in B1; a flip whose test did not touch changed code
  is reported as `not caused by this change` with the reason;
- the rerun triad on the head side: in-process rerun, fresh-process rerun, and a rerun after
  the rest of the candidate set; a flip that does not repeat in all three is `unstable`, with
  which rerun disagreed.
The comment never hides a flip; it labels it. Check: on the sympy slow-suite results folder,
re-score the 32 recorded trees: no confirmed flip is lost, and every flip has one of the
three labels. Record the false-flip rate the triad produces on the 50-PR counter.

### A3. Selection by subtraction
Replace the ranked top-150 with: the file-level import closure of changed files as the safe
candidate set; a depth cap of 10 on the reach graph; hub symbols (fan-in above the 99th
percentile) traversed only through tests with recorded execution evidence; order by most
recent flip then by per-test duration; lexical match kept only as a cold-start tiebreaker;
every test the PR touches always runs. Check: Werkzeug 10/16 and 16/16, requests plus
itsdangerous 6/8 and 8/8, Click holdout 3 8/8 and 8/8 do not drop; candidate count and time
per case recorded beside the old numbers.

### A4. Base-side cache and process cost
Plugin autoload off in both runners. Content-addressed cache of base-side PASS outcomes keyed
on test file, transitive first-party import closure, interpreter, lockfile hash, runner args;
PASS only, file granularity. Check: a second run against the same base on Django skips the
base side entirely and the comment says so.

Phase A ends with: a report section, the four checks recorded, the Action updated, and the
AMU repository's own PRs showing the new comment. Then open a PR to main and stop for my
words before merging.

## Phase B: see what static analysis cannot (weeks)

### B1. First-hit edge tracer on sys.monitoring
Per test, record caller-to-callee edges with PY_START plus CALL, DISABLE after first hit,
restart per test. Python 3.12 and later only; fall back to coverage contexts below that.
Subprocesses through AMU's own `.pth` hook, which already exists. Exception events
(RAISE, PY_UNWIND) switched on only for the rerun of a flipped test, and the chain from the
changed function to the failing assertion goes into the comment as the path. Check: measured
overhead on the Django suite beside coverage contexts; the DyPyBench finding that static
graphs hold about half of runtime edges reproduced on one of our sets, with AMU's number.

### B2. Side-by-side execution of changed functions
During the base-side run, capture the arguments every changed function was called with
(PY_START with argument snapshot, deep-copied where possible, bounded in size and count).
Replay each captured call on both trees and compare return value, raised exception, mutation
of arguments, stdout, and the sequence of external calls. Report a difference as
`behaviour differs on captured input`, with the input and both outcomes. Normalise known
nondeterminism; run each comparison twice and keep only stable differences. Check: on the
Django 102 non-breaking commits, the fraction with a stable difference, and a hand check of
20 of them labelling each as intended or not. Target the ChangeGuard range: above 70 percent
precision on the hand sample.

Phase B ends the same way as A: report, checks, PR, stop.

## Phase C: generate what is missing, and present it (after B)

### C1. Change-directed differential test generation
For changed functions with no captured arguments and no test evidence, generate a test with
the model, keep it only if it runs, passes five times on its target tree, covers a changed
line, and differs across trees. Assertions come from execution, never from the model. Budget
cap per PR in dollars and minutes, both inputs to the Action. Report as
`generated test shows a difference`, with the test body collapsed. Check: on 50 PRs from the
counter, acceptance rate of the filter chain and cost per PR, beside DiffTestGen's 78
percent and four cents.

### C2. Hunk isolation and presentation
For a confirmed flip, bisect over independently applicable hunks to name the hunk, and
annotate it inline on the PR. Status check that is informational, never failing, in v1. Quiet
mode when nothing was found. Acted-on counter already exists; add a per-row kind so the
counter says which row types get acted on.

## Not in scope, by the evidence
A learned ranker on repository history. CodeQL, Pysa or Semgrep dataflow. Dynamic slicing.
Formal regression verification. Mutation beyond a diff-scoped proof of blindness, which waits
until C is measured. Any agent trial.

## Rules
- Measure on the existing frozen sets and on the live PR counter. No new agent trials.
- No row without an evidence label. The comment never claims more than ran.
- Budgets are tests: warm under 2 s on Django before execution; comment under the size cap.
- AMU's own suite: `pytest tests scripts/test_before_tests_harness.py` with
  `/Users/Shared/amu-slow-suite-2026-10-15/slow/env/bin/python`; one known failure,
  test_mcp_verification, passes when the package is installed.
- Approved suites for execution: Click, Werkzeug routing, Django, sympy, in scratch copies.
- Commit each item, push, report with before and after tables, files changed, tried and
  rejected, what executed upstream code. Open one PR per phase; merge only on my words.
