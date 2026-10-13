# Claim run: trial record

Written 2026-10-14, before any agent was started. This is the first trial whose result counts as a claim
about the product as it now exists. Rules: `../TRIAL-RULES.md`.

## What is being tested

AMU at commit b9123df: bare `amu` on a developer's own uncommitted change runs candidate tests on the changed
code, confirms each failure against the code as it was, and prints the ones that flip. Candidates come from a stored per-test coverage recording
plus static analysis.

## Tasks

Frozen by `scripts/freeze_holdout.py --skip 250 --with-lexical --name claim` (`../phase0/claim.json`,
hash in `../phase0/claim-protocol.json`). The script walked back from position 250 of Django's history, past
every commit used in Phase 0, and screened 226 commits. It kept the first three whose change breaks an
existing test outside the 40 tests most lexically similar to the diff (indirect), and the first three whose
breaking tests are all inside that top 40 (lexical). None of the five exposed Phase 0 cases is among them.
Nothing has been run on these six cases except the screening itself. AMU has not been run on them.

| Task | Case | Kind | Breaking tests (frozen) |
|---|---|---|---:|
| 1 | dj-8acd0007 | indirect | 6 |
| 2 | dj-65acb3cc | indirect | 4 |
| 3 | dj-ca5746b8 | indirect | 1 |
| 4 | dj-57c8c8b1 | lexical | 2 |
| 5 | dj-2314cdf1 | lexical | 1 |
| 6 | dj-0f4fff79 | lexical | 1 |

Tasks 1 and 2 are adjacent upstream commits about cache keys and are not independent of each other.

## Deviation from the request

The request said ten agents and an equal number of indirect and lexical cases. Five tasks cannot be split
equally, so this run uses six tasks and twelve agents.

## Arms and budget

- Twelve agents, one per task and arm, same model (Sonnet), fresh context, same prompt apart from one
  paragraph describing `./amu`.
- **Budget: 180 seconds of tool wall-clock time per task, the same for both arms.** It is enforced by a
  ledger in the workspace. `./rt` charges the wall time of every test run and stops a run when the budget is
  reached. `./amu` charges its whole running time to the same ledger: building its index from cold, copying
  the tree, and every test it executes on either side. Agents may not run tests or Python any other way.
- The arm without AMU may run any Django test label through `./rt`, including whole modules and classes.
- Time an agent spends reading and thinking is not limited for either arm.

## What each arm is given

Both: a git repository holding the parent tree with the production change applied and uncommitted,
`change.diff`, and `./rt`.

AMU arm only: `./amu`, and a per-test coverage recording of Django's suite made at commit 920b43eb, the
parent of the oldest task's commit, so it predates every task as a CI artefact would. Making that recording
(about eleven minutes) is not charged to the budget. It is charged nowhere, and that is a real cost of the
AMU arm that this trial does not measure. The index and graph are not pre-built; AMU builds them on the clock.

## Change to this record before the start

This record first named commit d2aa9dc. Before any agent started, the trial mechanics were dry-run on the
five exposed Phase 0 cases (never on the six tasks). That run showed the tool running out of budget before
reaching a breaking test, and two general changes followed: the changed side runs first and only failures
are confirmed on the unchanged side, and a recording's line numbers are carried through edits made after it.
The tested commit is therefore b9123df. The dry run's output after those changes is in
`dry-run-exposed-cases.log`: all five indirect tests found, 24 to 132 of 180 budget seconds used.
`./amu` gives its test execution up to 110 s, or what is left of the budget minus 45 s for its own overhead.

## Ground truth and scoring

Ground truth is the frozen `breaking` list: tests in the test modules the upstream commit touched that pass
on the parent and fail with only the production change applied. Every test an agent reports as failing is
run on both trees before it counts. A reported test that fails on both, or passes with the change, is wrong.

Reported per task and arm: frozen breaking tests found, other verified breaking tests found, wrong reports,
budget used. The headline is how many of the six tasks each arm finds at least one breaking test in, and for
the three indirect tasks, whether an indirect test was found.

## What would count

AMU wins the claim if it finds breaking tests on more tasks than the other arm within the same budget, or
the same number with fewer wrong reports. A tie or a loss is reported as a tie or a loss.

## Prompt

Both arms:

> You are working in the directory {dir}. It is a git repository holding a copy of the Django web framework
> (source in django/, tests in tests/). A colleague's change is applied and uncommitted; `git diff` or
> change.diff shows it. The tests have NOT been updated.
>
> Your task: identify which existing test methods under tests/ now FAIL because of this change.
>
> Rules:
> - Run tests only with ./rt LABEL [LABEL ...], where a label is a Django test label such as
>   mail, mail.tests, mail.tests.MailTests or mail.tests.MailTests.test_ascii. Do not invoke runtests.py,
>   pytest, python or git commands that change files in any other way.
> - You have a hard budget of 180 seconds of tool wall-clock time for this task. Every ./rt run is charged
>   its running time and is stopped when the budget runs out. Each tool prints the budget used.
> - You may read any file inside this directory. Do not read anything outside it. Do not modify any file.
>
> When finished, reply with ONLY a JSON object on the last line:
> {"failing": ["app.module.Class.test_method", ...], "notes": "one sentence on how you chose"}
> "failing" must list only test methods you saw fail in tool output.

AMU arm, added before "When finished":

> - A tool is available: ./amu (no arguments). It runs candidate tests before and after the change and
>   prints JSON whose first fields are `flips` and `predicted_failures`: tests that passed before the change
>   and fail after it, each as a command naming the test. Its running time, typically half a minute to two and a
>   quarter minutes, is charged to the same 180 second budget, so decide whether and when to use it.

## Result (added after the run; nothing above this line was changed afterwards)

| Task | Kind | Breaking tests | Without AMU: found, wrong, budget used | AMU arm: found, wrong, budget used | AMU arm ran `./amu` |
|---|---|---:|---|---|---|
| 1 | indirect | 6 | 6, 0, 1 s | 6, 0, 1 s | no |
| 2 | indirect | 4 | 4, 0, 63 s | 4, 0, 13 s | no |
| 3 | indirect | 1 | 1, 0, 1 s | 1, 0, 2 s | no |
| 4 | lexical | 2 | 2, 0, 19 s | 2, 0, 7 s | no |
| 5 | lexical | 1 | 1, 0, 1 s | 1, 0, 3 s | no |
| 6 | lexical | 1 | 1, 0, 1 s | 1, 0, 2 s | no |
| Total | | 15 | 15, 0 | 15, 0 | 0 of 6 |

**A tie.** Both arms found every frozen breaking test on all six tasks with no wrong report. By the rule
written before the start, a tie is reported as a tie: AMU does not win the claim.

No agent in the AMU arm ran `./amu`. Each read the diff, searched the tests for callers of the changed
function or for the changed message, and ran the matching test modules, which took seconds. Told that the
tool would cost half a minute to two and a quarter minutes of a 180 second budget, every one of them judged
it not worth running. So this trial measured whether an agent that has AMU chooses it, and the answer was no.
It did not measure AMU's output against the other arm's.

The three "indirect" tasks were not hard for either arm. A breaking test outside the 40 tests most
lexically similar to the diff text is still found by searching for the changed function's name. The
indirect filter does not select what an agent finds difficult.

### The tool by itself, run after the trial

Because no agent used it, `./amu` was run once in each AMU-arm workspace after all agents had reported
(`tool-alone-after-trial.log`). This is an observation about the tool at commit b9123df, not an arm result.

| Task | Flips reported | Frozen breaking tests among them | Wrong flips | Budget charged |
|---|---:|---:|---:|---:|
| 1 | 6 | 6 of 6 | 0 | 13 s |
| 2 | 0 | 0 of 4 | 0 | 20 s |
| 3 | 1 | 1 of 1 | 0 | 48 s |
| 4 | 2 | 2 of 2 | 0 | 27 s |
| 5 | 1 | 1 of 1 | 0 | 75 s |
| 6 | 1 | 1 of 1 | 0 | 78 s |

Five tasks exactly right, no wrong flip anywhere, and one false all-clear. On task 2 the tool printed
"EXECUTED 3 candidate tests ... 0 now fail" while four tests break. Its ranking had the breaking tests
first; the failure was in execution. One candidate was a test method defined on a mixin class, which
Django's runner cannot load: it called the function directly, crashed, and ran nothing. AMU read that as
no failures. An agent that had trusted it would have been wrong, where plain search was right.

Fixed afterwards: a method on a mixin is sent to the runner as the concrete test classes that inherit it; a
batch that comes back short is split until the label that aborts it stands alone and is reported as not
run; the summary states how many of the candidates produced an outcome. With that, the tool reports all
four on task 2 and stays right on the other five (`tool-alone-after-fix.log`). Those six tasks are exposed
now, so that rerun shows the defect is repaired and nothing more.

### What executed upstream code

Django's own tests, in scratch copies: the screening of 226 commits (touched test modules, parent and
patched), one full-suite recording at 920b43eb, the dry run on five exposed cases, the twelve agents' test
runs through `./rt`, and the tool-alone runs.
