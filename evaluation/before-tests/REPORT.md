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
