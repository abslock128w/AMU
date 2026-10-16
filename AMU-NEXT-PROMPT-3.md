# AMU session brief 3: the slow-suite trial (written 2026-10-14)

ultracode

Start in `/Users/Abslock128/Documents/ChatGPT/AMU/observed-tests-cycle-2026-10-13`, branch
`accuracy/observed-tests` at 3ef11cf, pushed to github.com/abslock128w/AMU. PR #2 is open
there and is not merged without my words in chat. Read before doing anything, in this order,
and do not re-derive what they record: `AMU-PRODUCT-PLAN.md` sections 0, 9 to 12;
`evaluation/before-tests/REPORT.md` from "Runner adapter" to the end;
`evaluation/before-tests/blind/TRIAL-RECORD.md` in full, including both Haiku sections.

## Where this stands

AMU is one command. `amu` with no arguments, in a tree with an uncommitted Python edit,
picks up to 150 candidate tests from reachability, lexical match, co-change and a per-test
coverage recording when one exists, runs them on the tree before and after the edit under
the detected runner (pytest, unittest, Django's two runners), and prints the tests whose
outcome flipped, then what it could not reach. A runner that aborts on the changed code is
reported as a failure with its own error. Django: 1.1 s warm, under 10 s mean per hook run.

Five agent trials, all on Django or Click, all scored against hidden upstream tests:
two losses, three ties. The last two were blind, with AMU injected by a hook the agent never
chose, Sonnet then Haiku, 32 runs each. In every one of those 64 runs the agent ran every
frozen breaking test on its own, because the full suite runs in about 160 s and running it
whole is the dominant strategy. Selection cannot help where the whole suite is cheap. The
agent-facing hook line is closed on fast suites by a rule set before the trials.

One mechanism is left with an argument behind it: a suite the agent cannot afford to run
whole. That is this session's trial, and it decides whether an agent-facing product exists.

## Four pieces, in order. Pieces 1 and 2 are independent of each other and may run in parallel.

### Piece 1: the w21 defect

A flagged test that stops failing because the agent edited the test file itself must stay
listed, labelled `test edited, not fixed`. The `--only` recheck compares the test file's
hash against the hash recorded when it was flagged. Unit test on a fixture. Verify on a
copy of w21's tree, rebuilt with `build_workspaces.py` if the scratch copy is gone, that
bare `amu` still lists `test_simple_save` and `test_options`.

### Piece 2: three additions from the research note

(a) Flake recheck: a test that flips is rerun once on the changed side before it is
reported. A flip that does not repeat is reported as `unstable`, never as a failure.
(b) Baseline against pytest-testmon: on Werkzeug, requests, itsdangerous and Click holdout
3, record testmon's selected set beside AMU's candidate set and report recall of the
ground-truth test for each, run once, as one table in REPORT.md. Installing testmon into
the existing evaluation virtualenv is approved.
(c) The dumb hook: same triggers as `hook.py` (after Edit/Write of a `.py`, after a Bash
command that changed a `.py`, before a Bash command that runs tests), but it runs the test
modules for every file the agent has touched and prints their outcome. No AMU. This is the
control arm of every trial from now on. Keep it in `evaluation/before-tests/blind/`.

Ranking numbers must not move after pieces 1 and 2: Werkzeug 10/16 and 16/16, requests plus
itsdangerous 6/8 and 8/8, Click holdout 3 8/8 and 8/8. Show them.

### Piece 3: choose the repository. Stop and ask me before executing its suite.

Candidates: pandas, scikit-learn, sympy. For each, without running its tests: the runner,
the published full-suite time, how many of its last 300 commits touch production code and an
existing test, and whether the suite needs compiled extensions or external services in this
environment. Recommend one. Then stop and wait for my "yes <name>". That is the per-repository
approval and nothing runs before it.

### Piece 4: the slow-suite trial, after my yes

- Measure the full-suite time once in this environment. Set the test-time budget per run to
  one third of it, so no arm can run the suite whole. Hard stop 3,600 s of wall time or
  three times the budget, whichever is larger.
- Freeze eight tasks with `scripts/freeze_holdout.py --blind`: commits that break an existing
  test, ticket or commit message as task text, never the diff. Hash-pin and commit the
  freeze before analysing anything.
- Make one coverage recording at the parent of the oldest task, not charged, and say so.
- Arms: dumb hook versus AMU hook. Sonnet in both. Two trials per task per arm, 32 runs.
  Desktop sessions as before; `claude -p` has no credentials on this Mac. Same permission
  rules in both arms. I will send "next" per batch of four.
- Write `TRIAL-RECORD.md` before the first session: budget in seconds, the fairness rule,
  the commit under test, the model, the win condition. The win condition is runs with at
  least one regression per arm, of 16, AMU wins if fewer. Equal is a tie.
- Score as before against the hidden upstream tests. First line of the report is runs with a
  regression per arm. Then the table, the root cause for every AMU-arm regression, whether
  each agent ran the breaking test before declaring, test time charged with hook time shown,
  wall time, departures, and what executed upstream code.
- If AMU does not beat the dumb hook, the first line says so and the agent-facing product is
  closed. What remains is a CI comment on pull requests for humans, and that is a separate
  brief.

## Rules

- Suites approved for execution: Click, Werkzeug routing, Django, plus the one repository I
  say yes to in piece 3. Scratch copies only.
- `pytest tests scripts/test_before_tests_harness.py` for AMU's own suite; never unscoped at
  the repository root. Use `../observed-tests-evaluation-2026-10-13/werkzeug-env` or a venv
  with `pip install -e ".[dev]"`, which is approved.
- No file under `amu/` changes once a trial session is live. The record names the commit
  under test. This rule corrupted two runs when broken.
- Fresh sets only, frozen by script, run once. Exposed sets are development evidence.
- No row without an evidence label. Budgets are tests.
- Do not merge PR #2. CI auto-fix covers checks and conflicts on the branch only.
- Commit and push each piece. Report after each piece and after the trial: before and after
  table, files changed, tried and rejected, what executed upstream code. Losses in the first
  line. No spin.
