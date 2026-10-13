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
