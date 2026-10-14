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

## Result

Not yet run.
