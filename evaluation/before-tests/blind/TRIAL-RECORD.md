# Blind, unasked trial: trial record

Written 2026-10-14, before any agent was started. Rules: `../TRIAL-RULES.md`, plus what is written here.
The numbers are in `rules.json`; the scripts read them from there.

## What is being tested

The moment the earlier trials did not measure. An agent is given an upstream ticket and asked to implement
the change. It is not told that anything breaks and it never sees the upstream diff. In one arm, bare `amu`
runs from a `PostToolUse` hook after every Edit or Write of a `.py` file and its result is put into the
agent's context. The agent is not told what ran, is not offered a tool, and cannot call one.

AMU is tested at the commit that holds this record. No file under `amu/` changes between this record and
the last agent finishing.

## Tasks

Frozen at commit fb202b4 by `scripts/freeze_holdout.py --skip 476 --blind --want 8 --name blind`
(`../phase0/blind.json`, screening log in `../phase0/blind-protocol.json`), then `make_tasks.py`
(`tasks.json`; every file pinned by sha256 in `freeze.json`). The script started at position 476 of
Django's history, past every commit screened for Phase 0 (0 to 249) and the claim run (250 to 475), and
screened 151 commits. It kept the first eight that:

- break an existing test: a test in a test module the commit touched passes on the parent tree and fails
  with only the commit's production change applied;
- have a subject starting with `Fixed #`, so a ticket exists to give the agent;
- change at most 200 production lines, added plus removed.

The last two are feasibility filters for a blind task and were written into the script before it ran.
Thirteen commits were passed over for the subject rule, none for size.

| Task | Case | Ticket | Frozen breaking tests | Production files |
|---|---|---|---:|---:|
| 1 | dj-60a9c704 | #37067 | 1 | 1 |
| 2 | dj-fb292a54 | #36912 | 1 | 1 |
| 3 | dj-63c56cda | #35870 | 21 | 8 |
| 4 | dj-179aa21b | #37024 | 1 | 1 |
| 5 | dj-fcf91688 | #36973 | 1 | 1 |
| 6 | dj-856c9153 | #37004 | 1 | 1 |
| 7 | dj-f05fac88 | #36795 | 2 | 4 |
| 8 | dj-2333d566 | #36894 | 4 | 3 |

Nothing has been run on these eight except the screening. AMU has not been run on them. I have read their
subjects and counts, not their diffs or their breaking tests' code.

Each agent receives `TASK.md`: the ticket's summary and description as reported on
code.djangoproject.com (fetched once as CSV, stored in `tickets/`), then the upstream commit message. It
receives the parent tree as a fresh git repository with one commit. The upstream commit, its diff and its
tests are not in the workspace.

## Arms

32 runs: 8 tasks, 2 arms, 2 trials per task and arm. Every run is a separate headless Claude Code process
(`claude -p`, model `sonnet`), fresh context, started in its workspace by `run_trial.py`.

- **Control.** The prompt below. `./rt` runs any Django test label, or the full suite with no label.
- **AMU arm.** The same prompt, the same `./rt`, and a `PostToolUse` hook on Edit, Write and MultiEdit of
  `.py` files inside the workspace (`hook.py`). The hook runs `python -m amu.core --repo WORKSPACE
  --full-evidence` with AMU's defaults (60 s per side, coverage recording as candidate source) and returns
  one message as `additionalContext`: how many existing tests that passed before the uncommitted changes now
  fail, their ids as `./rt` labels (40 at most), any limit on that execution (candidates not run, budget
  reached), and the test time used. It does not say what produced the message.

The only difference between the arms is the settings file passed with `--settings`: empty for control, the
hook for the AMU arm. Both run with `--setting-sources ""` and `--strict-mcp-config`, so nothing from the
owner's Claude settings, memory, CLAUDE.md files or MCP servers is loaded. Allowed tools: Read, Edit, Write,
Grep, Glob, Bash. Web tools and sub-agents are denied. Workspace directory names are shuffled (`w01` to
`w32`) and do not name the arm. The hook, its configuration, the ledger and AMU's index live outside the
workspace.

Both arms of a task and trial run at the same time. Two such pairs run at once. Order: trial 1 of tasks
1 to 8, then trial 2.

## Budget

- **900 seconds of test-execution wall-clock time per run, the same for both arms**, kept in a ledger
  outside the workspace. Every `./rt` run is charged its running time and stopped when the budget is
  reached. In the AMU arm every hook run is charged its whole running time to the same ledger: index build,
  tree copy and every test on either side. A hook run is not started with less than 20 s left, and is
  stopped when the budget runs out.
- Why 900 and not the default 180: the task is to implement a change and check it, and the brief gives the
  control arm the full suite. The full suite takes 160 s through `./rt` on a quiet machine (four serial
  parts side by side; Django's own `--parallel` aborts in this environment when a test fails because
  `tblib` is not installed). 900 s is five full-suite runs. A dry run of the hook on two exposed claim-run
  cases took 16 to 56 s per call.
- Time spent reading, thinking and editing is not limited. A run is stopped at 3,600 s of total wall time
  and scored on its tree as it stood; that is reported per run.
- **Not charged:** one per-test coverage recording of Django's suite, made once before the trial at
  455e787b, the parent of the oldest task's commit, so it predates every task by 0 to 138 commits as a CI
  artefact would. It is charged nowhere and that is a real cost of the AMU arm this trial does not measure.
  It took 510 s and attributes 15,733 tests across 1,979 source files, 225 child processes included.
  AMU's index and graph are not pre-built; the first hook call in each workspace builds them on the clock.

## Prompt (both arms, identical)

> You are working in {workspace}. It is a git repository holding the Django web framework: source in
> django/, tests in tests/, documentation in docs/. HEAD is the state before your work.
>
> Your task is in TASK.md: implement that change in this repository.
>
> Make sure your change does not break anything. Behaviour the task does not ask to change must keep
> working, and the existing tests must pass when you are done. Where the task changes behaviour on purpose,
> update the tests that describe the old behaviour and add tests for the new behaviour.
>
> Rules:
> - Run tests only with ./rt (see ./rt --help). ./rt with no arguments runs the full suite, which takes
>   about 3 to 5 minutes. Do not call runtests.py, pytest or python yourself.
> - You have a budget of 900 seconds of test-execution time. Every test run in this workspace is charged
>   its running time to that budget, whether you start it or an automatic check does, and the amount used is
>   printed each time. When it is used up no more tests can be run. Time you spend reading, thinking and
>   editing is not limited.
> - Work only inside this directory. Do not read anything outside it, do not use the network, and do not
>   use git to fetch, pull or clone.
> - Leave your work uncommitted in the working tree.
>
> When you are finished, end your reply with a single JSON object on the last line:
> {"done": true, "tests_run": ["the labels you ran, or 'full suite'"], "notes": "one sentence"}

The sentence about an automatic check is in both prompts so the prompts are identical. In the control arm
no automatic check exists.

## Measures, scored after the agent's process ends (`score.py`)

Reference runs, once per task, full suite: the parent tree (P) and the upstream commit's own tree (U).
Per run, full suite: the agent's tree with its `tests/` directory replaced by upstream's `tests/` at the
task's commit (H, the hidden tests).

1. **Regressions shipped (headline).** A test that passed on P, passed on U and does not pass on H. Every
   such test is run again by itself on H and counts only if it fails again; one that passes alone is listed
   as unstable and not counted. Requiring a pass on U removes tests that fail in this environment for
   reasons unrelated to the change.
2. The same list split in two: tests whose source is identical on the parent and upstream (upstream left
   them alone, so the agent broke something the change did not need to break), and tests upstream rewrote
   (the agent's behaviour differs from what upstream's updated test expects).
3. Upstream's new tests that fail on H. Reported, not counted as regressions: they measure whether the
   change matches upstream, not whether something was broken.
4. **Red tests left in the agent's own tree:** tests that passed on P and fail on the agent's tree as
   shipped, with its own tests, in the modules upstream touched and the modules the agent touched.
5. **Whether the agent ran the breaking test before declaring:** for each frozen breaking test, whether a
   completed `./rt` run named it, its class, its module, its package, or the full suite. Separately,
   whether a hook message listed it.
6. Wall time of the process, tokens (input, cache and output summed, from the CLI's own result record),
   test time charged, `./rt` runs, hook runs, hook messages injected, hook runs skipped for budget.

## What would count

Counted per arm over its 16 runs: **runs that shipped at least one regression**, then the total number of
regression tests. The AMU arm wins if fewer of its runs shipped a regression. Equal run counts are a tie
whatever the test totals say, and a tie or a loss is reported as one in the first line.

If AMU does not win, the report lists every AMU-arm run that shipped a regression with: whether the hook
fired, what it printed, whether the breaking test was among AMU's candidates, whether the runner started,
and what the agent did next.

## Rules for runs that go wrong

- A process that ends without making any tool call (sign-in, network or API failure) is not a run. Its
  workspace is rebuilt and it is started once more; the record says so.
- Every transcript is searched for network commands and for reads outside the workspace. A run that
  fetched or read upstream's version of the change is removed together with the other run of its pair, and
  reported.
- These commits date from late 2025 to spring 2026 and may be in the model's training data. Both arms
  share that; the trial cannot remove it.
- Nothing is rerun because of its result.

## Change to this record before the start: desktop-app sessions

Written before any agent started. The `claude` CLI on this machine had no credentials, so no headless
process could run. On the owner's instruction the agents are Claude desktop-app sessions instead:

- Each run is a session started from the coordinating session with `start_session` (fresh context, working
  folder = the workspace, no worktree, model Sonnet, permission mode acceptEdits). The owner authorised
  the 32 sessions.
- **The hook comes from the workspace's own `.claude/settings.json`**: the PostToolUse hook above for the
  AMU arm, an empty hooks block for control. That file and `PROMPT.md` sit inside the workspace (excluded
  from git), so an agent that opens `.claude/settings.json` can see a command naming `hook.py`. Nothing
  else about it is in the workspace.
- Both arms' settings also carry the same permission rules: `./rt`, file tools and read-only shell commands
  (`git diff/status/log/show`, `ls`, `grep`, `rg`, `cat`, `head`, `tail`, `wc`, `find`, `sed -n`) are
  allowed; web tools, `curl`, `wget`, `pip`, `git fetch/pull/clone/commit` are denied. Anything else waits
  for a permission prompt that nobody answers; such a run is left until it ends or reaches the hard stop.
- The prompt above is given verbatim in the session's brief and as `PROMPT.md`, which the brief says to read
  first. The brief's background text says only that the folder is a Django checkout prepared for one ticket.
- Sessions have the desktop app's tool set, not the six tools listed above; it is the same in both arms.
- No CLAUDE.md exists in any workspace or at user level; the workspaces are under `/private/tmp`, outside
  the owner's home directory, so no ancestor CLAUDE.md applies. The app's own system prompt is the same in
  both arms.
- At most four sessions at a time: both arms of two task-and-trial pairs. Wall time runs from the
  `start_session` call to the coordinating session's notice that the session ended its turn, so it includes
  a few seconds of notice latency. The 3,600 s hard stop is enforced with `stop_session`.
- A session that ends its turn with a question instead of a declaration is not answered; its tree is scored
  as it stands, and the report says so.
- Tokens come from the session's own transcript file under `~/.claude/projects/`.
- On the first AMU-arm session, the transcript is read to confirm the hook's text reached the agent before
  its next tool call. If it did not, the trial stops there.

## Pilot, and a second change before the real start

Written before any run of the real trial started. On the owner's instruction, the first six desktop
sessions are a **pilot and are not scored**: trial 1 of tasks 1 to 3, both arms (w02, w27, w09, w13, w04,
w20 under the first root). Nothing from them was scored or compared against upstream; their trees,
ledgers and hook logs are kept beside the first root's workspaces and are not used.

What the pilot showed:

- **The Edit/Write hook missed two of three AMU-arm runs.** In w09 (5 tool calls) and w04 (16 tool calls)
  every edit was made through Bash, with `python3` heredocs and `sed -i`, never with the Edit or Write tool,
  so the hook never fired and those agents received no message. In w02 the agent used Edit; the hook fired
  25 s after the first edit and named the task's frozen breaking test. That message was attached to the
  edit's result, before the agent's next turn; the agent had sent a `./rt` call in the same batch as the
  edit, so that one call could not have seen it.
- **The permission rules do not stop `python3`.** Agents in both arms ran `python3` heredocs to edit files
  without any prompt, although the prompt says not to call python and `python3` is not on the allow list.
  The rules stay identical in both arms and are not changed.
- Agents in both arms tried `git commit` (denied) and one ran `git stash` around a test run and restored it.
- The app gives each session the path of a shared notes folder for sessions started from the same session.
  It stayed empty during the pilot. It is checked before scoring.

**Changes for the real trial**, on the owner's instruction:

1. The hook also fires **after any Bash command during which a `.py` file in the tree changed**: git
   status and file contents of changed and untracked `.py` files are recorded by a PreToolUse call before
   the command and compared after it.
2. The hook also fires **before any Bash command that runs `./rt` or `runtests.py`**, and puts the current
   flips in front of the agent before the tests run (PreToolUse `additionalContext`).
3. A check whose tree has the same uncommitted `.py` state as the last check reuses that result, says so,
   and charges nothing. Every check that runs is charged in full, as before.
4. The real trial runs under a new root directory (`.../scratchpad/blind/`), so no path, transcript folder
   or per-folder memory of a pilot session is shared with a real run. Workspace names and the task-and-arm
   mapping are unchanged (same seed).

The first AMU-arm session's transcript is read to confirm that the PreToolUse text reaches the agent; if it
does not, the trial stops there and the owner is told.

## Result (added after the run; nothing above this line was changed afterwards)

**By the rule written before the start, the AMU arm shipped fewer regressions: 6 of its 16 runs shipped at
least one, against 7 of 16 for control (1,712 regression tests against 1,719). The whole difference is one
control run (task 4, w07) whose bug no AMU-arm agent wrote and which the hook could not have executed
against, so this trial does not show the hook reducing regressions.**

Scores: `results/scores.json`. Per run: `results/runs/<id>/` (ledger, hook log, diff, timing).

| Task | AMU arm: regressions (in tests upstream left alone) | Control: regressions (left alone) | Frozen breaking tests the hook listed |
|---|---|---|---|
| 1 | w02 0, w23 0 | w27 0, w03 0 | w23 1 of 1; w02 none |
| 2 | w09 0, w06 0 | w13 0, w28 0 | none |
| 3 | w04 854 (814), w18 854 (814) | w20 854 (814), w01 854 (814) | 1 of 21 in each |
| 4 | w15 0, w10 0 | **w07 5 (5)**, w08 0 | none |
| 5 | w31 1 (0), w30 1 (0) | w14 1 (0), w29 1 (0) | none |
| 6 | w21 1 (0), w11 1 (0) | w24 1 (0), **w32 3 (2)** | none frozen; 2 other breaking tests in both runs |
| 7 | w16 0, w26 0 | w17 0, w19 0 | none |
| 8 | w22 0, w12 0 | w05 0, w25 0 | 2 of 4 in each |
| Runs with a regression | **6 of 16** | **7 of 16** | |

| Measure, per arm over 16 runs | AMU arm | Control |
|---|---:|---:|
| Regression tests | 1,712 | 1,719 |
| Same, without task 3 | 4 in 4 runs | 11 in 5 runs |
| Regressions in tests upstream left alone, without task 3 | 0 | 7 in 2 runs |
| Upstream's new tests failing (not regressions) | 25 | 28 |
| Red tests left in the agent's own tree | 0 | 0 |
| Runs whose agent ran every frozen breaking test before declaring | 16 | 16 |
| Runs that declared done | 16 | 16 |
| Session wall time, median / total | 166 s / 3,802 s | 170 s / 3,206 s |
| Test time charged, total | 2,453 s (hook 1,303 s) | 1,506 s |
| `./rt` runs / hook runs / hook messages | 36 / 55 / 92 | 43 / 0 / 0 |
| Tokens, total incl. cache reads | 11.4 M | 16.1 M |

What the counts are made of:

- **Task 3 (854 in every run, both arms) is a scoring artefact.** Upstream's rewritten test modules import
  `get_blank_choice_label` from `django.db.models.utils`, a helper upstream added and neither the ticket
  nor the commit message names. On every agent's tree those modules fail to import, so all their tests
  count. The set is identical in all four runs. The definition was fixed beforehand and is applied as
  written; the "without task 3" rows are a post-hoc view and are labelled as one.
- **Tasks 5 and 6 (one in every run, both arms)** are tests upstream rewrote for its own behaviour
  (`test_clash_between_related_query_name_and_manager`, `test_get_queryset_falls_back_to_pk_when_no_ordering_defined`).
  All eight agents of each task implemented the ticket differently from upstream in the same way. No tool
  running the agent's existing tests can see a hidden rewritten test.
- **w07 (task 4, control): a real regression.** Its new `SITE_ID` check calls `apps.get_model("sites",
  "Site")`, which raises `LookupError` when `django.contrib.sites` is not installed. Five system-check
  tests upstream left alone fail. Both AMU-arm agents of task 4 wrote a check without this bug, so the hook
  never had it to find. Counterfactual, run after scoring: bare `amu` on a copy of w07's tree ranked all of
  those tests among its candidates and executed none: Django's runner aborts on the broken check at start,
  and AMU reported `NOT EXECUTED` (`results/counterfactual-w07.json`). The hook would have said nothing
  useful.
- **w32 (task 6, control): two real regressions**, `GenericInlineFormsetTests.test_options` and
  `ModelFormsetTest.test_simple_save`. It edited `test_simple_save` to match its own behaviour and never
  ran `generic_relations`. These are exactly the two tests the hook listed in both AMU-arm runs of task 6
  (w21, w11), whose agents then changed their implementation and shipped neither. The other control run
  (w24) found them by running packages. Counterfactual: bare `amu` on w32's tree names `test_options` in
  25 s (`results/counterfactual-w32.json`); `test_simple_save` it cannot, since the agent rewrote it.

### AMU-arm runs that shipped a regression, root cause

| Run | Hook fired | What it printed at the end | Breaking test among candidates | Runner started | Agent's response |
|---|---|---|---|---|---|
| w04, w18 (task 3) | 6 runs each | 0 flips | no: the failing tests exist only in upstream's test files | yes, after early `NOT EXECUTED` checks on half-edited trees | fixed the one test the hook listed |
| w31, w30 (task 5) | 4 and 2 runs | 0 flips | no: the failing test is upstream's rewrite | yes | nothing to respond to |
| w21, w11 (task 6) | 5 and 3 runs | 2 flips, then 0 | the hidden test is upstream's rewrite; the 2 listed tests were real breakage | yes | changed the implementation; both listed tests pass in the shipped tree |

No AMU-arm run shipped a regression the hook had printed. No agent ignored a hook message.

### Departures from the brief and from this record

- Desktop-app sessions instead of headless processes, and a six-run pilot, both recorded above before the
  real start. The hook was widened to Bash after the pilot showed two of three agents never used Edit.
- Permission prompts were answered in three runs although the record says none would be: w31 (AMU arm,
  `git stash` around one test run), w16 (AMU arm) and w17 (control), both a `sed -i` on
  `tests/queries/tests.py`. w16 and w17 shipped none; w31 shipped only the task 5 rewritten-test
  regression that all four task 5 runs shipped.
- `python3` heredocs ran without a prompt in both arms; the rules did not stop agents calling python.
- w01 (control) took a second turn after a backgrounded full-suite run finished; its tree was compared with
  the first snapshot and was unchanged; its end time is the second turn's.
- Wall time comes from each session's transcript timestamps. Token totals come from the session transcripts
  and include cache reads; their output-token counts look low and are not used for any comparison.
- The scorer was changed after the runs to read desktop transcripts (commit 07db2ad); its definitions were
  not changed. The counterfactual `amu` runs on w07 and w32 were added after scoring.
- The shared notes folder the app offers to sibling sessions stayed empty. No transcript shows network use
  or a read outside the workspace.

### What executed upstream code

Django's own tests only, in scratch copies: screening of 151 commits for the freeze; timing runs; one
full-suite coverage recording at 455e787b; hook dry runs on two exposed claim cases; the six pilot sessions;
the 32 trial sessions' `./rt` runs and 55 hook runs; scoring (8 parent and 8 upstream full suites, 32
hidden-test full suites, 32 own-tree module runs, reruns of suspects); one module rerun each on w04 and w07
to read their errors; the two counterfactual `amu` runs.

## Correction after the report (2026-10-14, written before any engine change was committed)

One statement in the result above is wrong, and so is the line of the report that repeats it. The
counterfactual on w07's tree did not execute nothing because Django's runner aborted. Its packet's own gap
says `tests/sites_tests/tests.py: other side of the change could not be built; nothing executed`; I inferred
the abort without reading it. The cause is an AMU defect: when a hunk of the change begins with inserted
lines (w07 added `from unittest import mock` at the top of its test file), the code that rebuilds the file
as it was before the change placed the reversed hunk at the wrong line and gave up. With that fixed, bare
`amu` on a copy of w07's tree executes 198 candidates in 23 s and names exactly the five tests w07 shipped
broken (`results/counterfactual-w07-after-fix.json`). The hook in this trial could not have caught w07 because
of an AMU bug, not because of the runner.

The four hook runs in the trial that executed nothing (w04 three times, w18 once) were real runner aborts:
the agent's half-edited tree raised `NameError` or `ImportError` while Django started. That defect did not
fire in any of the trial's 55 hook runs. Neither correction changes the counts or the first line.

# Rerun with Haiku: trial record

Written 2026-10-14, before any Haiku session started. Everything above stays as the record of the Sonnet run.

## What changes and what does not

- **Agent model: Haiku (`claude-haiku-5-5`) in both arms**, instead of Sonnet. The reason, set by the owner:
  Sonnet shipped a regression the hook could address in only one run of sixteen, so the base rate is raised
  with a cheaper agent. If the hook cuts regressions here, the product claim becomes "makes cheap agents ship
  safely". If it does not, the agent-facing hook line is closed.
- **AMU under test: commit 929cd0e.** Two engine changes since the Sonnet run, both reported in
  `../REPORT.md`: (1) a test runner that aborts on the changed code and runs on the code before it is reported
  as a failure, with the runner's own error, and the hook injects that line; the "before" side is now rebuilt
  correctly when a hunk begins with inserted lines, the defect that hid w07's regressions; (2) hook cost:
  Django candidates in up to four parallel processes, outcomes before the change remembered within a session,
  candidates capped by time allowed, and test-only edits recheck only the last flipped tests. No file under
  `amu/` changes between this record and the last Haiku session finishing.
- **Hook:** `hook.py` at 929cd0e. Triggers as in the Sonnet run (after Edit/Write of a `.py` file, after a
  Bash command that changed a `.py` file, before a Bash command that runs `./rt` or `runtests.py`).
- **Unchanged:** the eight frozen tasks and their text, the prompt word for word, `./rt`, the 900 s test-time
  ledger with hook time charged, the 3,600 s hard stop, the permission rules in both arms, desktop-app sessions
  started from the coordinating session, at most four at a time (both arms of two tasks), the coverage
  recording at 455e787b (not charged), the scorer and every definition in "Measures" and "What would count".
- 32 runs, no pilot: the mechanism was verified in the Sonnet run. Fresh root directory
  (`.../scratchpad/haiku/`), so nothing of a Sonnet session's paths, transcripts or per-folder memory is shared.
  Workspace names and the task-and-arm mapping are the same seed as before.
- Permission prompts: the owner is asked not to answer any. A run with an answered prompt is marked.

AMU's full suite at 929cd0e, before the first Haiku session (`pytest tests scripts/test_before_tests_harness.py`
with the owner's Werkzeug evaluation environment): 2,577 passed, 14 skipped, 1 failed. The failure is
`tests/test_mcp_verification.py`, which compares the installed package's build hash with the source and fails
on any uncommitted-to-install source change, as recorded in `../REPORT.md` before this work. No failure comes
from 22f91ef or 929cd0e, so the commit under test stays 929cd0e.

## What would count (same rule, first line of the report)

Runs with at least one regression, per arm, of 16. The AMU arm wins if fewer of its runs shipped one.
Equal counts are a tie. The report also gives the regression-test totals, the split by tests upstream left
alone, the task 3 artefact as before, and a root cause for every AMU-arm run that shipped a regression.
