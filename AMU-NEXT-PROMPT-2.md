# Prompt for the next AMU session (written 2026-10-14)

Start in `/Users/Abslock128/Documents/ChatGPT/AMU/observed-tests-cycle-2026-10-13`, branch
`accuracy/observed-tests` at commit 8262822, pushed to github.com/abslock128w/AMU. PR #2 is
open there and must not be merged without the owner's words in chat. Read, in this order,
before doing anything: `AMU-PRODUCT-PLAN.md` sections 0, 9, 10, 11; `evaluation/before-tests/REPORT.md`
from the heading "Runner adapter" to the end; `TRIAL-RECORD.md`. Do not re-derive what they
record.

## What AMU is now

`amu` with no arguments, in a repository with an uncommitted Python edit, picks up to 150
candidate tests (reachability, lexical, co-change, and a coverage recording when one exists
under `.amu/index/`), runs them on the tree before and after the edit under the detected
runner (pytest, unittest, Django's `runtests.py` or `manage.py`), and prints the tests whose
outcome flipped, then what it could not reach. 1.1 s warm and 1.5 s after an edit on Django
before test execution; the execution itself is bounded at 60 s per side. `--static` gives
the static packet. `--observe` records per-test coverage including child processes.

What is out of the default packet, each by a rule set before its measurement: the ranked
list of tests that merely reach the change (Phase 0, Django), static untested rows (13
percent author agreement), coverage-backed untested rows (23 percent, no later-fix signal).

## What three trials showed

Two losses and a tie. In every trial the agent was told that existing tests break and was
given the diff. Under those conditions a capable agent searches callers and runs modules in
seconds, and in the last trial not one of six agents given AMU chose to run it. Those trials
measured search. They did not measure the moment AMU is for: the agent has just made a
change, believes it is done, and does not know anything broke.

## The one job of this session: the blind, unasked trial

Design, then run, then report. Nothing else until it has reported.

1. **Tasks.** From the Django history already fetched, freeze with `scripts/freeze_holdout.py`
   eight commits that break an existing test, none from any exposed set (the Phase 0 five,
   the claim-run six, and anything in `TRIAL-RECORD.md`). For each, the agent receives the
   upstream issue text or commit message and the parent tree, never the diff. Hash-pin the
   freeze and commit it before analysing anything.
2. **Arms.** Control: the agent implements the change and is told to make sure it did not
   break anything, with the full suite available and a wall-clock budget. AMU arm: identical,
   plus a `PostToolUse` hook on Edit and Write of `.py` files that runs `amu` and injects the
   flip count and the flipped test ids into the agent's context. The agent is not told about
   AMU and has no AMU tool to call. Same model, same prompt, same budget, two trials per
   task per arm, so 32 runs.
3. **Measures, scored against the hidden upstream tests after the agent declares done:**
   regressions shipped (upstream tests that fail on the agent's tree and passed on the
   parent), whether the agent ran the breaking test before declaring, wall time, tokens,
   and how many hook injections occurred. Write the budget in seconds and every rule into
   `TRIAL-RECORD.md` before the first agent starts.
4. **Charging.** Hook runs count inside the AMU arm's wall-clock budget. The coverage
   recording, if used, is made once before the trial from the parent tree and is not
   charged, and the record says so.
5. **Report.** First line: did the AMU arm ship fewer regressions, with the counts. Then the
   table, then departures from this brief, then what executed upstream code. If AMU does
   not reduce regressions, the report says so and lists the tasks where the hook fired and
   the agent ignored it, with what it printed.

## If the trial is won

Build presence: `pipx`/`uvx` install with a standard-library core, the Claude Code plugin
with that hook, a pre-commit hook, the GitHub Action that comments flips on a PR, README cut
to the one sentence and a 20-line transcript. Plan section 4, Phase 3.

## If the trial is lost

Root-cause per task, in the report: did the hook fire, what did it print, did the agent read
it, was the breaking test in the candidates, did the runner start. No new features until
each loss has a named cause.

## Standing rules

- Only Click, Werkzeug routing and Django suites are approved for execution, in scratch
  copies. Anything else needs the owner's yes once per repository.
- Never run `pytest` unscoped at the repository root; use `pytest tests scripts/test_before_tests_harness.py`.
- Never edit index or runner code while a recording, screening or trial is running; it
  silently corrupted two runs on 2026-10-14.
- Fresh holdouts only, frozen by script, run once. Exposed sets are development evidence.
- No refusal paths. No row without an evidence label. Budgets are tests.
- CI auto-fix authorization covers fixing checks and conflicts on the branch. Merging,
  deleting branches and changing remotes need the owner's words in chat.
- Report after the trial with tables, files changed, tried and rejected, what executed
  upstream code. Plain statements, no spin, losses in the first line.
