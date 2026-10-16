# Brief 4, Phase B, B1: first-hit edge tracer on sys.monitoring (2026-10-16)

Branch `tracer-b1`, from main at v0.2.1 (cc943c4). No agent trial; Django's suite and Click's suite in scratch copies.

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

`amu/core/edges.py` (new); `amu/core/trust.py`, `amu/core/shadow.py`, `amu/core/__init__.py`, `amu/core/__main__.py`,
`amu/ci/render.py`, `amu/ci/run.py`; `tests/core/test_edges.py` (5 tests); `evaluation/tracer/*`.

AMU's suite (`pytest tests scripts/test_before_tests_harness.py`): 2,665 passed, 14 skipped, 1 failed (the known
`test_mcp_verification`).

## Tried and rejected

- `getattr(target, '__code__')` in the CALL callback: it runs code on mocks and lazy objects (defect 1 above).
- Exception events during a whole-suite recording: they cannot be switched off per location before 3.15, so every
  raise in the suite would pay for them. They run only on a flipped test's rerun.
- A call path ending at the first code object whose line range holds the changed line: module and class bodies span
  their functions. The path now ends at the narrowest code holding the line.

## Not done in B1

- Scoring the recording as a candidate source on the frozen sets, as the coverage recording was scored.
- Replacing the coverage recorder for untested-function rows, which need line-level data.
- A release tag with the tracer: v0.2.1 does not have it.
