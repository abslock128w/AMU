# Before-tests core: results

Date 2026-10-13. Baseline build: commit 68d82c8 (legacy `analyze-patch`). Candidate: the new `amu/core`
tree in this working copy (uncommitted). Ground truth in every table is derived from the upstream commit
itself: the test functions it added or modified. "In parent" counts those that already existed, which is
the most an existing-test recommender can find. A case hit means at least one ground-truth item in the
top 10 of the default packet.

## Summary

| Set | Role | Function hits (before → after) | File hits (before → after) | Largest packet | Slowest case |
|---|---|---:|---:|---:|---:|
| Werkzeug, 16 scorable of 23 | development | 5/16 → 10/16 | 8/16 → 16/16 | 19303 → 1999 B | 7.67 → 0.50 s |
| Click, 10 | holdout 1, run once | 0/10 → 1/10 | 0/10 → 9/10 | 22482 → 1994 B | 2.59 → 0.49 s |
| requests + itsdangerous, 8 | holdout 2, run once | 2/8 → 5/8 | 2/8 → 7/8 | 14457 → 1997 B | 3.32 → 0.17 s |

Holdout 1 missed its 6/10 function bar. Only 5 of its 10 commits modify a test that exists in the parent,
so 5/10 was the ceiling; the core reached 1 of those 5. Holdout 2 was frozen after that result was read and
before any fix, with a rule that makes every case attainable. It met its bars (60% function, 75% file).
The legacy build failed outright on 3 of 8 holdout-2 cases (one file-limit refusal, two crashes).

Werkzeug and Click are development evidence now. Holdout 2 is the only untouched number, it is 8 cases
from two small libraries, and it must not be read as general accuracy.

## What the Click miss showed (root cause)

1. Real call paths were five to six hops, for example test → nested decorated command → `click.option` →
   inner closure → `Parameter.__init__` → `Option._parse_decls`. The three-hop limit never reached them.
2. Hub symbols defeat structure. 300 to 480 tests reach `Option`/`Parameter`; nothing structural separates
   the test about option names from the rest.
3. The ranking tail was arbitrary. Once the first picks covered every changed symbol, remaining tests
   scored near zero and fell back to alphabetical order.

Fixes, all general: nested definitions cost no hop and reach extends to five hops with decay; the words of
the changed lines are matched against each test's name, identifiers and literals; coverage of a symbol is
discounted but keeps a floor. On Click afterwards the attainable tests moved from about rank 300 to ranks
11 to 84, which is still outside the top 10 in 4 of 5 cases. Hub classes remain the open problem.
Two Click commits delete tests that guard the behaviour they revert (38 tests in one, 1 in the other).
In the recorded holdout run, 7 and 1 of those were in the core's top 10. The label rule does not count
deleted tests. That is an observation, not a rescoring.

## Later the same day: execution-backed evidence, a third holdout and an agent trial

Everything above describes commit b3747c8. This section covers the work after it (through 9599611 and the
trial). The owner authorised local test execution; all runs used disposable copies with plugin autoload off.

### Static ranking on unseen Click commits (holdout 3, run once)

Eight older Click commits, frozen by the same backwards walk before anything was run on them.

| Build | Function hits | File hits | Largest packet | Slowest case |
|---|---:|---:|---:|---:|
| Legacy 68d82c8 | 1/8 | 2/8 | 38804 B | 3.21 s |
| Core | 8/8 | 8/8 | 1997 B | 0.67 s |

Same repository as holdout 1, so this is independent by commit, not by project. Werkzeug (10/16, 16/16) and
holdout 2 (5/8, 7/8) were rerun with the final code and did not move.

### Ground truth from execution

`scripts/before_tests_exec.py` records per-test coverage on the parent tree, reruns the same tests with the
production patch applied, and takes the existing tests whose outcome changed as ground truth. Click's
`tests/test_stream_lifecycle.py` (a garbage-collection stress file) was left out because it is extremely slow
under per-test coverage. About 3,000 tests ran per case.

Only 5 of 18 Click commits break any existing test.

| Set | Cases with a breaking test | Breaking test in top 10, static | With coverage |
|---|---:|---:|---:|
| Click holdout 1 (development for the coverage ranking) | 3 | 2 | 3 |
| Click holdout 3 (unseen) | 2 | 2 | 1 |

Coverage ranking is line-aware and rarity-weighted: a test that ran a changed line few other tests share
ranks highest. It was tuned on the first row. On the unseen row it was worse than static in one case, where
the breaking tests fell to ranks 9, 14 and 17 behind sibling tests that ran the same lines, and the packet
showed 8 tests because a consequence row took the space. Coverage is therefore not shown to beat static
ranking. Static stays the default signal; coverage adds the "no recorded test ran these changed lines" gap.

Werkzeug M5 check on the full `tests/test_routing.py` (117 tests): all 44 tests recorded running the changed
code rank above the 320 static-only ones, the 12 that ran the changed lines are within the first 13, and an
unrelated edit keeps the evidence. The gap line correctly said no existing test ran the changed branch of
`StateMachineMatcher.match`, which is the branch the upstream commit added a test for.

### Defects found by using the tool on an already-edited tree

- A real suite's `coverage.json` was 92 MB and was rejected by a 64 MiB guard.
- A recording made before the edit was compared with post-edit line numbers. Recorded lines are now moved
  through the patch.
- A deletion-only edit mapped to no declaration and returned no tests, silently.
- `git apply` inside a subdirectory of another repository exits 0 and changes nothing; the first execution
  run therefore reported no outcome changes at all. The harness now uses `patch` and verifies the files differ.

### Agent trial (smoke, 10 runs)

Five tasks, each an edited Click tree whose change breaks existing tests. Instruction: name the existing test
functions that now fail, running at most 15 test functions through a logging runner. Same model (Sonnet) and
prompt in both arms; one arm also had `./amu` with the coverage recording. One run per cell.

| Task | Breaking tests | Found without AMU (test runs) | Found with AMU (test runs) |
|---|---:|---:|---:|
| 1 click-v2-9f0bcdf0 | 1 | 1 (4) | 1 (8) |
| 2 click-v2-d0368817 | 11 | 10 (12) | 8 (10) |
| 3 click-v2-f67c2bb6 | 1 | 1 (4) | 1 (9) |
| 4 h3-click-bc32a92c | 3 | 2 (8) | 2 (15) |
| 5 h3-click-8ee83ddb | 1 | 1 (1) | 1 (3) |
| Total | 17 | 15 (29) | 13 (45) |

AMU did not help. Without it the agents found more breaking tests with fewer runs, at about the same token
cost (50 to 63 thousand per run in both arms). No arm reported a false failure.

Why, from the agents' own notes:

1. The agents without AMU read the diff and searched the tests for what it removed or reworded (a warning
   text, a tuple shape). That is a semantic question, and AMU's ranking has no notion of what a test asserts.
2. The agents with AMU ran its list top-down. A list of tests that reach the code is longer than the list
   of tests likely to fail, so it spent budget on passes.
3. Click is small enough to search in seconds. Nothing here needed a graph.

Neither arm found the third breaking test of task 4, which lives in another test file with no lexical link
to the diff. That kind of case is where a reachability tool should earn its place, and AMU ranked it tenth
in the unedited-tree run but did not show it in the trial packet.

Limits of this trial: 5 tasks, one run each, one small library, three tasks from cases the coverage ranking
was tuned on, and a task that hands the agent the diff. It cannot show AMU is useless on large repositories
or on indirect breakage. It does show that on small, greppable changes a capable agent does not need it.

### What this means for the product

- The engine now does what the original brief asked on four sets, and the old analyzer does not.
- The claim "an agent picks tests better with AMU" is not supported, and on this evidence is false for small
  repositories with the diff in hand.
- The places left where it could matter are specific: breakage with no lexical link to the diff, repositories
  too large to search cheaply, and telling the agent which changed lines no test executes. Each needs its own
  trial before more engine work.

## After the agent trial: exact outputs first (plan v3, actions 4 to 7)

The trial showed a ranked reachability list loses to an agent that reads the diff and searches. Four additions
answer that. None needed new test execution approval except the last, which only runs on request.

| Addition | What it does |
|---|---|
| `summary` | First field of the packet: likely failures, untested functions, reach count, and "not a run-all list". |
| `expect` per test row | `likely_fail`, `ran_changed_lines`, or `reaches`; with `--run`: `fails` or `unchanged`. |
| `untested[]` | Changed functions whose changed lines no test runs (`coverage`) or no test reaches at all (`static_only`). |
| `--run` (shadow execution) | Runs up to 150 candidate tests on the code before and after the change and reports the ones that flip. |

`likely_fail` is static and has three sources: the test shares a run of at least three removed word-triples
with a literal the patch deletes; it uses a function or class the patch removes from the whole package; or it
reaches a function and expects a warning or exception that function no longer raises.

Measured on the 18 Click commits with execution ground truth, static only:

| Measure | Result |
|---|---|
| `likely_fail` labels | 13, of which 12 truly fail |
| Breaking tests labelled | 12 of 17 |
| Labels on the 13 commits that break nothing | 0 |
| Shadow execution on the five trial workspaces | 17 of 17 breaking tests named, 0 wrong, 0.5 to 1.4 s each |

For comparison, the trial agents found 15 of 17 without AMU and 13 with the old packet, using 29 and 45 test
runs. Shadow execution found the third breaking test of task 4, which neither arm found.

These are development numbers. The label rules were shaped on these 18 commits (the first versions were 40%
precise), and the five workspaces are the trial tasks. On Werkzeug and on requests plus itsdangerous, where
there is no execution truth, the label fired once in 31 cases, so it is narrow: it speaks when a patch
removes wording, names, warnings or exceptions, and is silent otherwise. An untouched measurement needs the
Phase 0 holdout. The full Werkzeug suite does not finish in this environment, so it could not serve.

Static ranking did not move: Werkzeug 10/16 and 16/16, holdout 2 5/8 and 7/8, holdout 3 8/8 and 8/8.

## Phase 0: indirect breakage on Django (the kill test for the test list)

Owner approved Django on 2026-10-13. `scripts/freeze_holdout.py` walked back 250 commits from the head. For
each commit that changes `django/` and modifies an existing test, it ran the test modules the commit touched
on the parent tree, then again with only the production change applied. A test that passed and then failed
is breaking. A breaking test outside the 40 tests most lexically similar to the diff is indirect.

| Screening step | Commits |
|---|---:|
| Screened | 250 |
| Change production code and an existing test | 40 |
| Break an existing test | 11 |
| Break a test lexical search would not surface (kept) | 5 |

Indirect breakage occurred in 1 commit in 50. The plan's risk threshold for keeping the test list was 1 in 20.

Trial: the five kept cases, ten agents (Sonnet), same prompt and 15-run budget, one arm with AMU's static
packet as it stood at commit 5b0a4e9. One run per cell.

| Case | Indirect test found without AMU (runs) | With AMU (runs) | AMU's rank for the indirect test |
|---|---|---|---:|
| 1 warning stack level | yes (2) | yes (4) | 13 |
| 2 reworded settings error | yes (2) | no (4) | not ranked |
| 3 renamed constant | yes (12) | yes (12) | 3 |
| 4 management import errors | no (15) | no (15) | not ranked |
| 5 `import_string` modules | no (3) | no (5) | 129 |
| Total | 3 of 5 (34) | 2 of 5 (40) | |

**Gate: failed.** The gate asked for the AMU arm to find indirect breakage the search arm missed on at least
half the cases. It did so on none. In case 3 the AMU agent reported that the packet named no likely failure
and that it searched for the removed name itself.

Decision applied, as the plan specified: tests that only reach the change are no longer in the default
packet. `--tests` and `--full-evidence` still list them. The default packet is now the summary, untested
functions, tests expected to fail, consequences and gaps.

Two defects the trial exposed, fixed afterwards (so the numbers above do not include them):

- A removed module-level constant (`RAISE`) was not treated as a removed name, because only functions and
  classes of six or more letters were. With the fix, case 3's packet lists 12 likely failures, the same 12
  tests the agents confirmed.
- A test method inherited by a subclass had no identity in AMU, so the indirect test of case 2 could not be
  ranked at all. Likely failures now include the inheriting subclasses.

What stays unsolved on Django:

- Static `likely_fail` fires in 1 of the 5 cases even after the fixes. A reworded message (case 2), a changed
  stack level (case 1), and behaviour reached through a subprocess (case 4) are invisible to it.
- Shadow execution would answer these exactly, but it drives pytest and Django uses its own runner. A runner
  adapter is the next piece of work, and it is the only AMU output with evidence of beating search.
- Speed: 16 s cold and 2.7 s warm on Django, over the 2 s warm budget.

Limits of this phase: five cases, one run each, one repository. The frozen ground truth only covers test
modules the commit touched, and it undercounted case 3 (3 tests) because tests in modules that fail to import
were not parsed; the agents' 12 were verified by hand on both trees. Only Django's two required dependencies
were installed, so tests needing optional packages were skipped.

## Runner adapter, shadow execution on Django, and speed

### Runner adapter

`amu/core/runners.py` detects the project's runner from the tree and `--runner` overrides it: `pytest`,
`unittest`, `django-runtests` (a `tests/runtests.py`), `django-manage` (a `manage.py` without pytest
configuration). Each listed test carries a `run` command that can be pasted, for example
`cd tests && python runtests.py mail.tests.MailAdminsAndManagersTests.test_wrong_admins_managers`.
A unittest-style batch that one unloadable label aborts is retried one test file at a time, and tests under
a module that fails to import are counted as failing.

### Shadow execution on the five Django cases: a debugging run, not a trial

Same five frozen cases as Phase 0, unedited parent tree plus the production diff, `--run` with a 120 s
budget per side. This run was used to find and fix defects, so it proves nothing about new cases.

| Case | Phase 0 agents without AMU | Phase 0 agents with static packet | Shadow execution (debug run) | Wall time |
|---|---|---|---|---:|
| 1 | found | found | found | 13 s |
| 2 | found | missed | found | 29 s |
| 3 | found | found | found | 44 s |
| 4 | missed | missed | missed | 15 s |
| 5 | missed | missed | found | 46 s |
| Indirect test found | 3 of 5 | 2 of 5 | 4 of 5 | |

Two defects surfaced and were fixed during this run:

- The first pass found 3 of 5. Case 2's indirect test is a method inherited by a subclass, and inherited
  copies were not candidates. A failing test defined on a base class is now rerun in every inheriting subclass.
- That fix did nothing until a second defect was found: a graph loaded from its snapshot had no subclass map,
  so inherited tests disappeared on every warm run. The map is now saved with the graph.

Case 4 stays out of reach: the breaking test drives `manage.py` in a subprocess, and no call path exists
for static analysis to put it among the 150 candidates.

### Speed on Django (2,923 Python files)

| Path | Before | After |
|---|---:|---:|
| Warm, unchanged tree | 2.05 to 2.7 s | 1.2 s |
| First call after editing a file | about 8.4 s | 2.9 s |
| Cold | 16.2 s | 10.2 s |

- Warm: half the time was decoding 2,923 per-file fact files. One snapshot per tree state now holds the graph
  and only the record fields analysis reads afterwards.
- Edited tree: the previous graph is reused and only the changed files are relinked (up to 25 files). On the
  Django case this returned the same tests as a full rebuild; a unit test checks the same on a small tree.
- Cold: files are parsed in parallel from 300 uncached files up. The remaining time is graph construction.

Tried and rejected: storing facts with `marshal` or `pickle` for faster loading. The index can sit inside a
repository someone else controls, and neither format is safe to load from there. The first call after an
edit is still over the 2 s target on Django.

Static ranking after these changes: Werkzeug 10/16 and 16/16; requests plus itsdangerous 6/8 and 8/8 (was
5/8 and 7/8); Click holdout 3 8/8 and 8/8. All are exposed sets.

## untested[] scored from history (Django, no hand labels)

`scripts/score_untested.py`, static only, nothing executed. Of the 239 screened commits that broke no existing
test, 102 change production code. AMU analysed each production-only diff on the parent tree: 282 changed
functions, 31 of them reported untested (all `static_only`; there is no coverage recording for Django).

| Score | Untested rows (31, from 16 commits) | Covered rows (251, from 85 commits) |
|---|---:|---:|
| (a) The same commit added or modified a test that reaches the function | 4 (13%) | 104 (41%) |
| (b) A later bug-fix commit changed the function | 3 (10%) | 8 (3%) |

- (a) Precision of an untested row against the author's own decision is 13%, and it is lower than for covered
  rows (Fisher exact p = 0.002). Part of that is built in: a function AMU cannot connect to any test is also
  hard to connect to the author's new test.
- (b) 10% against 3% is 3 rows against 8 and is not significant (p = 0.11). Median exposure was 105 later
  commits in both groups, not the 500 asked for, because the screened commits are the newest 250 and the
  fetched history is 400 deep.
- The rows themselves show the cause. Most are code Django does test but reaches dynamically: descriptors
  (`SpatialProxy.__get__`), signal receivers (`reset_cache`), template tags (`now`, `items_for_result`),
  module `__getattr__`, and constructors called through class tables (`RelatedPopulator.__init__`).

**Decision, by the rule set beforehand:** untested rows are not meaningfully more likely to be fixed later, so
the `static_only` rows are demoted. They are out of the default packet and remain in `--full-evidence`. Rows
with basis `coverage` (a recording shows no test ran the changed lines) stay in the default packet; they were
not scored here because no Django recording exists, and that is an open item.

How (b) was computed: a commit counts as a bug fix when its message, after stripping Django's
"Fixed #NNNN --" prefix, contains fix, bug, regression or revert (98 of 400 commits). "Changed the function"
is function-level: the fix's diff touches lines inside the same qualified name in the same file. Line-level
tracking across intervening edits was not attempted. The optional model judge was skipped: (a) and (b) did not
tie, so there was nothing for it to break.

## Piece 1: shadow execution is the default for the developer's own change

| Invocation | Before | After |
|---|---|---|
| bare `amu`, uncommitted Python change | opens the interactive terminal | runs candidates on both sides, prints flips first |
| bare `amu`, no change waiting | interactive terminal | unchanged; `amu repl` also opens it |
| `amu before-tests` on the working tree | static packet | executes; `--static` gives the static packet |
| `amu before-tests --patch-file` | static packet | unchanged; `--run` executes |
| nothing could be run | not applicable | `flips` is null, summary starts NOT EXECUTED |

"The developer's own tree" is defined operationally: no patch was supplied, so the change is the working
tree's uncommitted diff. A supplied patch can describe a tree that is not the caller's to execute.

Check on a real repository: a git copy of Click at the parent of bc32a92c with that commit's production
change uncommitted. Bare `amu` printed `flips: 3` and the three breaking tests (all three in the frozen
ground truth) in 6.1 s wall time, 147 candidates per side. That case is an exposed one; this is a functional
check, not a measurement.

Files: `amu/core/__init__.py`, `amu/core/runners.py`, `amu/core/__main__.py`, `amu/cli.py`,
`tests/core/test_reachability.py`, `docs/before-tests.md`. Rejected: executing for supplied patches by
default, and reporting zero failures when the runner could not start. Upstream code executed: Click's tests,
147 candidates on each side, in a scratch copy.

## Piece 2: a coverage recording as the candidate source

`amu before-tests --observe` runs the project's whole suite once under Coverage.py with a context per test
and stores the result beside the index (`.amu/index/<version>/observed/`): one shard per source file, named
by the file's content hash, holding which test ran which line and which test ran which function. A file
edited since the recording is judged by function name; a file whose only difference is the patch being
analysed has its recorded lines moved through the patch. An existing `.coverage`, `coverage.json` or AMU
artefact is still read when no stored recording exists.

Child processes: the recorder installs its own start-up hook in the interpreter for the duration of the run
and passes the running test's name to children through the environment. Coverage's standard
`COVERAGE_PROCESS_START` could not be used on Django, whose runner overwrites it with its own configuration.

Django recording (state 7ab2d803, the parent of the oldest screened commit; sqlite, two required dependencies):

| Measure | Value |
|---|---:|
| Suite time under recording | 686 s |
| Tests attributed | 16,213 |
| Source files with attribution | 2,004 |
| Child processes recorded | 235 |
| Test-to-function links seen only through child processes | 60,023, for 216 tests |
| Stored size | 86 MB |

### Use 1 and 3: shadow candidates and dynamically reached tests (debugging run, exposed cases)

Same five Phase 0 cases. Not a trial: these cases have been used for development since Phase 0.

| Case | Rank of the indirect test, static | Rank with recording | Shadow, static candidates | Shadow, recording candidates | Wall time |
|---|---:|---:|---|---|---:|
| 1 | 13 | 13 | found | found | 22 s |
| 2 | not ranked | found via its base class | found | found | 51 s |
| 3 | 3 | among 12 likely failures | found | found | 136 s |
| 4 subprocess | not ranked | 74 | missed | found | 125 s |
| 5 | 129 | 42 | found | found | 117 s |
| Indirect test found | | | 4 of 5 | 5 of 5 | |

Case 4 is the test that drives `manage.py` in a child process. It had no static call path; the recording
attributes `ManagementUtility.execute` to it through the child. Shadow execution now runs candidates in
chunks of 40, each chunk on both sides before the next, so reaching the 60 s per-side budget keeps
everything already compared and reports how many candidates were left. Before this, one timeout discarded
the whole run, which is what happened to case 4 on the first attempt. With a recording the candidate cap is
300 instead of 150, which is why these runs are slower than the static ones (13 to 46 s).

### Use 2: coverage-backed untested rows, scored from history (run once)

Same 102 non-breaking Django commits and the same two scores as the static rows. The recording predates all
of them; files changed since are judged by function name.

| Score | Untested rows (48, from 13 commits) | Covered rows (234) | Static untested rows, for comparison (31) |
|---|---:|---:|---:|
| (a) Author added or modified a reaching test | 11 (23%) | 97 (41%) | 4 (13%) |
| (b) A later bug-fix commit changed the function | 2 (4%) | 9 (4%) | 3 (10%) |

(a) is lower for untested rows than for covered ones (p = 0.022) and (b) is identical (p = 1.0).
**Decision by the same rule: demoted.** `untested[]` is no longer in the default packet at all; both kinds
remain in `--full-evidence`. Twenty of the 48 rows come from one sweeping commit, and nine sit in code whose
tests need optional packages the recording environment lacked; without those nine the picture is the same
(11 of 39 against 85 of 177 for (a); 2 of 39 against 3 of 177 for (b), p = 0.22).

### Two runs lost to my own edits

The first recording and the first claim-set screening were both invalid. Each started before I edited
`amu/core/index.py`; their parallel parser workers are fresh interpreters and imported the edited module
with the running process's old job format, so every file failed to parse silently. The recording stored
line data without function maps, and the screening's lexical ranking came back empty, which made every
breaking test look indirect (22 "indirect", 0 lexical). Both were discarded and rerun with no core edits in
flight. The recording was caught by an impossible number (235 child processes, zero child-only links) and
the screening by a ratio that contradicted Phase 0.

Files: `amu/core/observe.py` (new), `amu/core/coverage.py`, `amu/core/shadow.py`, `amu/core/__init__.py`,
`amu/core/index.py`, `amu/core/__main__.py`, `amu/cli.py`, `scripts/score_untested.py`,
`scripts/freeze_holdout.py`. Rejected: `COVERAGE_PROCESS_START` for children; one JSON file for the whole
recording (the Click one was 92 MB and Django's would be far larger and slow to load); keeping
coverage-backed untested rows in the packet. Upstream code executed: Django's full suite twice under
recording (once discarded), and the shadow runs on the five cases.

## Piece 4: speed after an edit (Django, 2,923 files, quiet machine, three runs each)

| Path | Before | After |
|---|---:|---:|
| First call after editing a file | 2.9 s | 1.5 to 1.65 s |
| Warm, unchanged tree | 1.2 s | 1.1 to 1.3 s |

What the profile showed after the bounded relink, and what was done:

| Cost | Before | Change |
|---|---:|---|
| Decoding all 2,920 per-file fact files | 1.2 s | Untouched files keep the slim records from the last snapshot; a file's full facts load only when changed code refers into it |
| Rebuilding one dictionary per saved graph link | 0.5 to 0.9 s | Links stay in the list form they are stored in |
| Re-reading and hashing every file | 0.5 s | A size-and-modification-time ledger skips unchanged files; files modified within two seconds of the last scan are re-hashed, as git does |
| Class tables for every record | part of the above | Built per class on first use |

Checked on Django: the relinked graph ranks the same tests as a full rebuild of the edited tree. Rejected:
writing the new snapshot in a background process, which would make a repeat call race its own cache.

## Piece 3: the claim run (first number that counts as a claim)

Full record, written before the start and extended with the result: `claim/TRIAL-RECORD.md`.

Six unseen Django tasks, frozen by script from history older than every Phase 0 commit: three indirect, three
lexical. Twelve agents (Sonnet), one per task and arm. Both arms had 180 seconds of tool wall-clock time,
enforced by a ledger; the AMU arm's `./amu` charged its index build, tree copy and every test it ran to the
same ledger. Twelve agents instead of ten, to keep the equal split.

| | Without AMU | AMU arm |
|---|---:|---:|
| Tasks with every breaking test found | 6 of 6 | 6 of 6 |
| Breaking tests found | 15 of 15 | 15 of 15 |
| Wrong reports | 0 | 0 |
| Tool time used per task | 1 to 63 s | 1 to 13 s |
| Agents that ran `./amu` | not available | 0 of 6 |

**Result: a tie, so AMU does not win the claim.** No agent given AMU chose to run it. Each searched for
callers of the changed function or for the changed message and ran the matching test modules in seconds.

The tool run alone on the same six tasks after the trial, at the tested commit b9123df: five exactly right,
no wrong flip, and one false all-clear (task 2: "0 now fail" while four tests break). A test method on a
mixin class crashed Django's runner before anything ran and AMU read the empty result as no failures. That
is fixed, with tests, and the summary now says how many candidates produced an outcome.

What this run established:

- With a time budget instead of a run count, a capable agent on a repository whose test modules run in
  seconds does not need AMU and does not reach for it.
- The indirect filter did not select hard cases. "Outside the lexical top 40" is still inside "tests that
  call the changed function", which is what agents search for.
- AMU's execution result was right on five of six unseen tasks and silently wrong on one. A tool whose
  worth is an exact answer cannot afford a silent wrong one; the specific cause is fixed, the class of
  failure (a runner that produces nothing) is now reported instead of read as a pass.

What it did not establish: whether AMU helps when the agent is not offered a choice (a hook that runs it
after every edit), on repositories where relevant tests take minutes instead of seconds, or on changes whose
breaking tests cannot be found by searching, such as Phase 0 case 4. That last kind exists but was 1 commit
in 250.

Also changed during this piece, from a dry run on the exposed Phase 0 cases before the trial: shadow
execution runs the changed side first and reruns only failures on the unchanged side, and a recording's
lines are carried through edits made after it.

## Milestones (state at b3747c8)

- **M1 harness.** `scripts/before_tests_harness.py`; protocol and manifests are hash-pinned.
- **M2 discovery.** Met on Werkzeug: 10/16 function, 16/16 file, never more than 10 shown or 40 ranked.
- **M3 performance.** Werkzeug cold 0.5 to 0.8 s, warm 0.06 to 0.08 s. A 2,815-file tree: 28 s → 3.2 s warm.
  The legacy `overload_nodes` walk is computed once per tree; legacy report hashes are unchanged.
- **M4 consequences.** Case 15 emits `NumberConverter.to_python raises ValidationError` →
  `StateMachineMatcher.match` catches it, raises `NoMatch` → `MapAdapter.match` catches it, raises
  `MethodNotAllowed | WebsocketMismatch | NotFound`, each hop with file and line. Cases 08 and 10 now rank
  the behaviour-specific tests first; the four `sansio/test_request.py` class references are no longer in the top 10.
- **M5 coverage.** Per-file staleness and ranking boost are implemented and checked against the recording
  from the already-approved five-test Werkzeug run: the four tests that executed the changed code rank
  1 to 4; editing an unrelated file, or the changed file itself, keeps the evidence. **Not done:** the full
  `tests/test_routing.py` run, which needs approval.
- **M6 holdout.** Reported above.
- **M7 agent trial.** Not started. It needs approval to execute upstream tests, and M6 holdout 1 did not pass.

## Per-case tables

### Werkzeug (development)

| Case | Truth (in parent) | Function R@10 | File R@10 | Seconds | Bytes |
|---|---:|---:|---:|---:|---:|
| 01-4da3786f | 2 (0) | 0% → 0% | 100% → 100% | 5.07 → 0.47 | 13156 → 1999 |
| 02-0c419f95 | 1 (1) | 100% → 100% | 100% → 100% | 7.67 → 0.48 | 12128 → 1860 |
| 03-594452f6 | 1 (1) | 0% → 100% | 0% → 100% | 5.57 → 0.47 | 12757 → 1965 |
| 04-fbbbf745 | 0 (0) | N/A → N/A | N/A → N/A | 4.81 → 0.46 | 13552 → 1875 |
| 05-2c453bf3 | 1 (1) | 100% → 100% | 100% → 100% | 4.26 → 0.48 | 11788 → 766 |
| 06-54b7596d | 0 (0) | N/A → N/A | N/A → N/A | 4.26 → 0.47 | 12677 → 1912 |
| 07-42f3ccf9 | 0 (0) | N/A → N/A | N/A → N/A | 4.04 → 0.47 | 11864 → 1825 |
| 08-23002fd6 | 0 (0) | N/A → N/A | N/A → N/A | 4.65 → 0.47 | 15169 → 1958 |
| 09-f85f3807 | 0 (0) | N/A → N/A | N/A → N/A | 4.15 → 0.48 | 13401 → 1948 |
| 10-ae7f2163 | 0 (0) | N/A → N/A | N/A → N/A | 5.09 → 0.47 | 16816 → 1981 |
| 11-b2a2c097 | 0 (0) | N/A → N/A | N/A → N/A | 4.01 → 0.46 | 12549 → 1898 |
| 12-6d7c0b4e | 1 (0) | 0% → 0% | 0% → 100% | 4.18 → 0.49 | 12954 → 1946 |
| 13-1b8bc581 | 1 (0) | 0% → 0% | 0% → 100% | 4.51 → 0.47 | 9153 → 1949 |
| 14-c678a231 | 1 (0) | 0% → 0% | 0% → 100% | 4.25 → 0.48 | 13148 → 1982 |
| 15-ab16e62d | 1 (0) | 0% → 0% | 0% → 100% | 4.14 → 0.47 | 15118 → 1924 |
| 16-658bf5ed | 1 (1) | 100% → 100% | 100% → 100% | 4.09 → 0.46 | 12868 → 1991 |
| extra-5b0e3209 | 1 (0) | 0% → 0% | 100% → 100% | 4.36 → 0.45 | 15065 → 1579 |
| extra-6b495d0d | 7 (6) | 71% → 71% | 100% → 100% | 4.00 → 0.45 | 13059 → 1333 |
| extra-97d9f22a | 1 (1) | 0% → 100% | 0% → 100% | 3.93 → 0.46 | 12411 → 677 |
| extra-0743179b | 3 (2) | 0% → 67% | 50% → 100% | 5.58 → 0.48 | 19303 → 1944 |
| extra-45768395 | 2 (2) | 0% → 100% | 0% → 100% | 5.47 → 0.50 | 17543 → 1994 |
| extra-dc199349 | 1 (1) | 100% → 100% | 100% → 100% | 4.10 → 0.46 | 12077 → 1804 |
| extra-f359a67e | 5 (5) | 0% → 20% | 0% → 100% | 4.68 → 0.50 | 18049 → 1959 |

### Click (holdout 1, run once before the root-cause fixes)

| Case | Truth (in parent) | Function R@10 | File R@10 | Seconds | Bytes |
|---|---:|---:|---:|---:|---:|
| click-v2-9f0bcdf0 | 8 (1) | 0% → 0% | 0% → 67% | 2.22 → 0.48 | 22482 → 1994 |
| click-v2-b90faad1 | 39 (2) | 0% → 0% | 0% → 33% | 2.30 → 0.45 | 22401 → 1945 |
| click-v2-d0368817 | 1 (1) | 0% → 0% | 0% → 100% | 2.10 → 0.46 | 22437 → 1945 |
| click-v2-e2bddbc8 | 39 (1) | 0% → 0% | 0% → 33% | 1.91 → 0.48 | 22387 → 1945 |
| click-v2-f67c2bb6 | 1 (1) | 0% → 100% | 0% → 100% | 1.90 → 0.39 | 12691 → 1241 |
| click-v2-05f6fd0d | 1 (0) | 0% → 0% | 0% → 100% | 2.03 → 0.44 | 22383 → 1939 |
| click-v2-405a5481 | 2 (0) | 0% → 0% | 0% → 100% | 2.27 → 0.49 | 22325 → 1990 |
| click-v2-f25f697a | 4 (0) | 0% → 0% | 0% → 0% | 2.15 → 0.42 | 21853 → 1990 |
| click-v2-22177f85 | 1 (0) | 0% → 0% | 0% → 50% | 2.15 → 0.43 | 19674 → 1890 |
| click-v2-271effb3 | 1 (0) | 0% → 0% | 0% → 100% | 2.59 → 0.44 | 22413 → 1967 |

### requests and itsdangerous (holdout 2, run once)

| Case | Truth (in parent) | Function R@10 | File R@10 | Seconds | Bytes |
|---|---:|---:|---:|---:|---:|
| h2-requests-f0198e6d | 2 (2) | 100% → 100% | 100% → 100% | 3.32 → 0.15 | 14422 → 1963 |
| h2-requests-bc7dd0fc | 1 (1) | 0% → 0% | 0% → 100% | 1.95 → 0.14 | 14237 → 1891 |
| h2-requests-f8bec2f7 | 2 (2) | error → 0% | error → 33% | — → 0.17 | — → 1997 |
| h2-requests-01353d3b | 1 (1) | 0% → 0% | 0% → 0% | 1.84 → 0.15 | 14457 → 1920 |
| h2-itsdangerous-7f4dcf83 | 1 (1) | 100% → 100% | 100% → 100% | 0.43 → 0.03 | 12949 → 1978 |
| h2-itsdangerous-177196d3 | 1 (1) | 0% → 100% | 0% → 100% | 0.34 → 0.03 | 11829 → 1960 |
| h2-itsdangerous-aed5b260 | 12 (12) | error → 25% | error → 80% | — → 0.05 | — → 1611 |
| h2-itsdangerous-526b1ea0 | 4 (3) | error → 75% | error → 100% | — → 0.03 | — → 1986 |

## Tried and rejected

- Reusing `value_flow.py` for consequences: its repository pass alone takes seconds, which breaks the budget.
  Consequences are derived from indexed raise sites, handlers, state writes and call sites instead.
- Treating every same-named method as a candidate for an unresolved receiver: 6 million guessed edges on a
  large tree. Names with more than 8 candidates are now counted as unresolved.
- A fixed 0.5 weight for the owning-class fallback: it let class mentions outrank behaviour-specific tests.
  The weight now falls with the number of tests that merely mention the class.
- Rescoring Click with tests the commits deleted: rejected as a label change after seeing results.

## Limits

- Hub classes: when hundreds of tests reach a symbol, the top 10 is a guess with reasons, not a selection.
- New tests an upstream commit adds cannot be found by an existing-test recommender; 6 Werkzeug and 5 Click
  cases have only such tests.
- Dynamic dispatch through dictionaries, plugins and `getattr` is reported as a count of unresolved call
  sites, not resolved.
- Coverage read from a raw `.coverage` or `coverage.json` judges staleness by modification time.

## Execution of upstream code

None was run on purpose. One mistake: an unscoped `pytest` at the repository root began collecting the
frozen case trees and imported test modules from `evaluation/before-tests/cases/click-029bbeda` (a
superseded tree, not in the sealed sets) before stopping on a collection error. No test ran.
`evaluation/conftest.py` now blocks collection below that directory.

Index caches that the first core runs wrote into `external-amu-evaluation-2026-10-12/cases/*/baseline/.amu`
were removed; the harness now keeps its index under its own output directory.

## AMU's own suite

`pytest tests scripts/test_before_tests_harness.py`: 2,545 passed, 14 skipped, 1 failed. The failure is
`tests/test_mcp_verification.py`, which compares the installed package's build hash with the source; it
passes once the current source is installed. `tests/core`: 28 passed. This suite shows the old commands
still work. It is not evidence of accuracy.
