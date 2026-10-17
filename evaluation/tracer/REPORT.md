# Brief 4, Phase B: the edge tracer (B1) and side-by-side execution of changed functions (B2), 2026-10-16 and 17

Branch `tracer-b1`, from main at v0.2.1 (cc943c4). No agent trial. Django, Click and sympy suites ran in scratch copies.

## Summary

| Item | Check | Result |
|---|---|---|
| B1 tracer | overhead on Django's suite beside coverage contexts | +14% against +48%; 4.2 MB against 104 MB |
| B1 tracer | static graphs hold about half of runtime edges (DyPyBench 49%) | 53% on Click (56% for Click's own code) |
| B2 side-by-side | Django's 102 non-breaking commits: share with a stable difference; precision on a hand check | 4 of 102 commits (4 of the 59 where the tests called a changed function); precision 30% in pass 1 (6 of 20 rows, measured), 100% in pass 2 (8 of 8) after fixing the artifacts pass 1 showed |
| B2 on breaking commits | each difference: intended or not | 20 rows on 5 of 13 commits: 13 real, all intended; 7 comparison artifacts, since fixed |

The ChangeGuard target (above 70% precision on the hand sample) was missed by the first measurement and met by the second. The second pass ran on code fixed after seeing the first, so it is development evidence, not a held-out measurement.

## What was built (`amu/core/edges.py`)

- **The tracer:** one `sys.monitoring` tool, two events.
  - PY_START: a Python function begins, however it was reached.
  - CALL: a call site calls a Python function.
  - Each callback records the first-party edge (caller → callee) and returns DISABLE, so a location costs one event
    per test. `restart_events()` switches everything back on at each test boundary.
  - Test boundaries come from pytest's setup and teardown hooks and from a `unittest.TestCase.run` wrapper, so Django's
    runner is covered.
  - The hook loads in every interpreter the suite starts, through a `.pth` file for the duration of the run, as the
    coverage recorder does. A child process inherits the running test's name.
- **Python 3.12 and later only.** Below that, `amu.ci.run` keeps recording coverage contexts. With `observe: 'true'`,
  the Action picks by interpreter: call edges (`--observe-calls`) on 3.12+, coverage below.
- **Exceptions:** RAISE, RERAISE and PY_UNWIND are switched on only for the fresh-process rerun of a flipped test.
- **What uses it:**
  - **Candidates.** With a recording, tests seen entering a changed function become candidates, matched by function
    name so the recording survives edits made since. A unit test shows a test that reaches the code only through
    `python -c` in a child process: it is not a candidate without the recording, and it is with it.
  - **The flip row in the comment.** It shows the call path from the test to the changed code, for example
    `path test_work > helper > work > check`. When an exception passed through changed code, it also shows
    `` `ValueError` raised through `check > work > helper > test_work` ``.

## Check 1: overhead on Django's suite, beside coverage contexts

Django at 7ab2d803 (the recording base of the earlier work). The whole suite ran with `runtests.py --parallel 1`,
19,655 tests, Python 3.14, one mode after another on the same machine. Suite seconds are the runner's wall time only;
storing the result is reported apart.

| Mode | Suite time | Overhead | Stored | Tests with data | Suite result |
|---|---:|---:|---:|---:|---|
| No hook | 330.7 s, 326.3 s | | | | OK |
| Edge tracer (fixed hook) | 377.8 s, 377.7 s, 378.1 s | **+14%** | 4.2 MB (gzip JSON), 4.44 M edges | 17,980 | 1 failure, below |
| Coverage contexts with the child hook (`--observe`) | 488.8 s | **+48%** | 104 MB | 16,213 | OK |

- **Set aside:** one no-hook run took 606 s, against 326 to 331 s in two other runs, and the tracer runs before and
  after it were steady. The machine was busy with something else at the time.
- **Store time:** writing the result took 10 s after a tracer run and 19 s after a coverage run.
- **Child processes:** 236 processes wrote edge records, against 235 child coverage files.

**Defects found by running Django under the tracer, both fixed:**
1. **14 tests errored.**
   - Cause: the CALL callback read `__code__` from whatever was called. On a `mock.call(...)` object that returns
     another, unhashable, call object, and the error escaped into the test.
   - Fix: the callbacks take a code object only from an exact `FunctionType` or `MethodType`, never call `getattr`
     on what they observe, and never raise.
   - A unit test covers a mock and a lazy object whose `__getattr__` counts touches.
   - All 14 pass under the fixed hook.
2. **Click's suite aborted at start.**
   - Cause: Click's settings turn warnings into errors, and pytest warned that the hook module was imported before it
     could rewrite it.
   - Fix: the module carries `PYTEST_DONT_REWRITE`.

**Not fixed, and labelled:** `view_tests.tests.test_debug.ExceptionReporterTests.test_reporting_frames_for_cyclic_reference`
fails in every whole-suite run under the tracer (3 of 3), and passes without it and under coverage.
- The test starts a thread inside `assertWarns` and joins it only after the block has ended. The thread's warning
  must therefore arrive before the main thread leaves the block.
- It passes under the tracer alone, in small batches, and with either event switched off (12 runs).
- Tracing makes a thread's first run of each code location slower, which is the likeliest reason it loses the race.
  This is not proven.
- A recording of a suite with such a test can list one test that fails only while being recorded; the recording
  keeps the suite's result line.

## Check 2: static graphs hold about half of the edges tests exercise

DyPyBench found 49% of dynamically observed edges in PyCG's static graph. AMU's own figure, on Click at
`click-v2-f67c2bb6`'s parent:
- the whole suite: 2,069 passed, 5.4 s under the tracer, 547 test functions, 52,208 per-test edges;
- compared as first-party function-to-function edges between distinct functions, with a class body running at import
  left out (that is a definition, not a call).

| Callers | Calls | Distinct edges | In AMU's static graph | Share |
|---|---|---:|---:|---:|
| All | direct (the caller is the immediate frame) | 4,032 | 2,147 | **53%** |
| All | every edge | 4,112 | 2,172 | 53% |
| Click's own code only | direct | 1,074 | 597 | **56%** |
| Click's own code only | every edge | 1,118 | 607 | 54% |

- **The finding holds:** AMU's static graph holds about half of what tests execute, 53% against DyPyBench's 49%.
- **The cause differs:** every missing callee exists as a record in AMU's graph; only the edge is missing. DyPyBench's
  dominant cause, missing callee nodes and methods of built-in types, does not apply, because only first-party code is
  compared.
- **What the missing edges are,** from a sample:
  - Click's `Context.invoke` calling user-registered command callbacks;
  - `super().__init__` chains;
  - progress-bar and pager callbacks.
  These are the edges this tracer exists to supply.

## Files

`amu/core/edges.py`, `amu/core/sidebyside.py` (new); `amu/core/trust.py`, `amu/core/shadow.py`, `amu/core/__init__.py`,
`amu/core/__main__.py`, `amu/ci/render.py`, `amu/ci/run.py`; `tests/core/test_edges.py` (5 tests),
`tests/core/test_sidebyside.py` (10 tests); `evaluation/tracer/*`.

Upstream code executed:
- Django's whole suite, six times (overhead);
- Click's whole suite, twice (edges);
- Django's tests for 102 commits, twice in full plus nine reruns, after three discarded partial starts (B2);
- the 5 Django Phase 0 cases and the 8 sympy tasks once each.

All of it ran in scratch copies.

AMU's suite (`pytest tests scripts/test_before_tests_harness.py`) at the end of Phase B: 2,675 passed, 14 skipped,
1 failed (the known `test_mcp_verification`).

## Tried and rejected

- `getattr(target, '__code__')` in the CALL callback: it runs code on mocks and lazy objects (defect 1 above).
- Exception events during a whole-suite recording: they cannot be switched off per location before 3.15, so every
  raise in the suite would pay for them. They run only on a flipped test's rerun.
- A call path ending at the first code object whose line range holds the changed line: module and class bodies span
  their functions. The path now ends at the narrowest code holding the line.

## B2: side-by-side execution of changed functions (`amu/core/sidebyside.py`)

**How it works**
- After the comparison of test outcomes, the tests that ran are run once more on the code before the change, with a
  sys.monitoring hook watching the changed functions.
- When a changed function starts, its arguments are copied, by pickle or else a deep copy. The function then runs
  right there, in the test's own process and state: twice as it was and twice as changed, each time on a fresh copy.
- Compared: the return value or the exception raised; the arguments afterwards (mutation); what was printed; and the
  sequence of calls from first-party code to code outside it.
- A difference is kept only when both versions repeat themselves exactly. It is reported as
  `behaviour differs on captured input`, with the input and both outcomes, and gets its own section in the comment.
- Limits per call site and in time:
  - 5 calls per changed function;
  - 120 s for the run, at most the per-side budget;
  - every comparison is written the moment it is made.

**What the as-changed run sees: everything the change brings.** This was built from what the Django runs showed:
- every comparable changed function, swapped into its module or class, and into modules that imported it by name;
- the top-level statements and class members the change adds, in every edited production file, put on the real
  objects for the run and taken off after.

**What is set aside and counted, not reported**
- **Nondeterministic:** a version that does not repeat itself.
- **Input built before the change:** an object built by the code before the change, when another changed method of
  its class now gives it state.
- **Call does not fit:** a call captured from old code that no longer fits a changed signature.
- **Arguments that cannot be copied** are used live, only when no run changes them.

**Not compared:** coroutines, nested functions, functions using a private name a class body would mangle, and
functions that exist on one side only.

### Check: Django's 102 non-breaking commits

These are the 102 commits of the untested-row scoring (Phase 0 screening, production-only change, no existing test
broken). For each, bare amu ran with execution on the parent tree, with the commit's production diff, the Django
runner and 60 s per side.

| | Pass 1 (1e9bee9, measured) | Pass 2 (bf6bfae; 9 commits rerun on 81b5bc0) |
|---|---:|---:|
| Commits with a comparable changed function | 87 | 87 |
| Commits where the tests run called one | 50 | 59 |
| Calls compared | 450 | 491 |
| Nondeterministic, not reported | 9 | 9 |
| Set aside: input built before the change | 0 | 5 |
| Side-by-side run reaching its limit | 11 (results lost, see below) | 6 (results kept) |
| **Commits with a stable difference** | **4 of 102** | **4 of 102** |
| Difference rows | 20 | 8 |
| **Real behaviour changes (hand check)** | **6 (30%)** | **8 (100%)** |
| Of them intended by the commit | 6 | 8 |

Hand check of pass 1, all 20 rows:

| Commit | Rows | Verdict |
|---|---:|---|
| 7b8504ea, migrations track all changes to unmanaged models | 2 | real: the autodetector now keeps unmanaged model keys; intended |
| 2936a0a9, ordering by `Model.__str__` in the admin | 1 | real: `label_for_field` returns the model's `__str__` rather than `str`; intended |
| d992705f, old-signature `from_db()` overrides | 3 | real: `RelatedPopulator.__init__` now stores `from_db`; intended |
| d992705f | 5 | artifact: `populate` as changed ran on an object the old `__init__` built |
| 0398417c, new `qualname()` helper | 9 | artifact: `NameError` on `qualname`. The edited import of a helper added in another file ran before that helper was there |

Hand check of pass 2, all 8 rows, all real and intended:
- 7b8504ea, 2 rows, as above;
- d992705f, 3 rows (`__init__`), as above;
- 2936a0a9, 1 row, as above;
- 6ade6258, 2 rows: `RelatedPopulator.__init__` now starts a `peers` list, enabling FETCH_PEERS batching for
  `select_related`, as the commit says.

The 0398417c refactor compares with no difference in pass 2, which is right for a change that keeps behaviour.

**Found during these runs and fixed (one commit each, with a unit test built from the Django case)**
- **Changed functions that call each other** ran one as changed against the other as it was (7b8504ea, before pass 1).
- **New module-level names, and arguments that do not pickle** (27137e65; 17 functions).
- **Arguments that copy neither way** (Django admin objects with locks and modules) are now used live, when unchanged.
- **New class members, and names added to other modules,** were missing from the as-changed run (4ea38d54, 27137e65).
- **After pass 1:** the order of additions across files (0398417c), and objects built by the old `__init__` (d992705f).
- **During pass 2:** results were written only at exit, so a run stopped at its limit lost them. That is how pass 2
  first missed 2936a0a9's real difference; its 9 limit-reaching commits were rerun with the fix. Pass 1's 11
  limit-reaching commits were not rerun: pass 1 stands as measured.

Three earlier starts on the 102 commits were stopped and discarded, after 27 or 28 commits each, when each showed one
of the defects above.

**What B2 does not reach**
- 43 of the 102 commits had no changed function that the tests run in 60 s called.
- 15 had no comparable changed function at all: coroutines, nested or new functions.
- Of the 59 commits it did compare, 4 show a stable difference.
- Recall against all real behaviour changes in the 102 is not measured: there are no labels for that.

### B2 on breaking commits: the 5 Django Phase 0 cases and the 8 sympy tasks, run once (81b5bc0)

| Case | Commit message, short | Flips | Calls compared | Differences | Verdict |
|---|---|---:|---:|---:|---|
| dj-f1d11117 | UnorderedObjectListWarning skips Django frames | 1 | 5 | 0 | |
| dj-80ea222c | allow `Address` objects as email addresses | 2 | 15 | 1 | real, intended: the error message now names `Address` objects |
| dj-7dee35f5 | rename the RAISE fetch mode to FETCH_RAISE | 18 | 0 | 0 | no comparable function (a method of a renamed class) |
| dj-537c6df3 | suppress fewer ImportErrors in management commands | 0 | 19 | 5 | real, intended: `ManagementUtility.__init__` now keeps a `style` attribute |
| dj-65a9f141 | `import_string()` supports modules and submodules | 2 | 20 | 7 | **artifacts**: 4 calls of `cached_import`, whose parameters the change altered, did not fit; 3 via `password_validation`, which held the old `import_string` by name |
| sy-434184cd | `as_base_exp` literal | 1 | 23 | 0 | `Mul.as_base_exp` removed: not comparable |
| sy-aa1004e2 | precision in `rs_nth_root` | 1 | 5 | 0 | |
| sy-30eb1f6c | `trigsimp` more targeted | 2 | 15 | 0 | |
| sy-1837fd2a | isympy keeps the original input | 1 | 5 | 5 | real, intended: `int_to_Integer` now keeps the original spacing |
| sy-97cf00f4 | `ArrayAdd` refuses scalar arguments | 2 | 40 | 0 | |
| sy-94f14043 | `acot`, `acoth` keep the sign at a branch point | 0 | 2 | 0 | |
| sy-253f564d | elementwise application notation in printing | 2 | 6 | 2 | real, intended: `sin_o(X)` printed instead of a lambda |
| sy-c3882307 | algebraicity of negative powers of zero | 1 | 5 | 0 | |

- **Overall:** 20 difference rows on 5 of the 13 commits: 13 real and all intended, 7 artifacts.
- **No unintended difference was found.** The breaking tests in these commits are ones upstream rewrote for the new
  behaviour (Phase 0 and the sympy trial records), so "intended" is the expected answer.
- **Where B2 found nothing,** the breaking behaviour sat on inputs the tests did not pass to the changed function
  before the change, or in functions B2 does not compare.
- **The 7 artifacts are fixed after this run (one commit, with a unit test)**, unmeasured on these sets:
  - a call that does not fit a changed signature is set aside and counted;
  - modules that imported a changed function by name get the changed one during the as-changed run.

## Not done in Phase B

- **Scoring:** the call recording has not been scored as a candidate source on the frozen sets, as the coverage
  recording was.
- **Untested-function rows** still need line-level data from coverage.
- **B2 does not compare** coroutines or nested functions, and gets no inputs when the tests run within the budget
  never call the changed function. Generating inputs is Phase C.
- **No release tag:** v0.2.1 has neither B1 nor B2.
