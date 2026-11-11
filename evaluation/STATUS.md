# AMU status (v0.5.1, 2026-11-02)

Building has stopped at v0.5.1. This page says what AMU does, how it scored on the last test set, what it still gets wrong, and what real use would have to show before the engine changes again. To install it, see [ci/INSTALL-NOTE.md](ci/INSTALL-NOTE.md).

## What AMU does on a pull request

Two workflow files run on every push to a pull request, and AMU edits one comment each time. It never blocks a merge.

1. **Flips.** It runs the tests the diff reaches on the merge base and on the head, up to 300 s of test time per side. A test that passes on the base and fails on the head is a flip. Each flip is rerun on the base before it counts, and again in a fresh job when the optional verify job is installed. Tests the pull request itself edited are listed apart, not counted.
2. **Change evidence.** The pull request's own new or edited tests are run on the base. One that fails there, passes on the head, and fails again alone in a fresh process shows the change does what it checks. A test that fails only because the code it calls is new is filed as "new test for new code".
3. **Behaviour differences.** For each changed function the tests call, side-by-side runs the code before and after the change on copies of the same captured arguments. It lists only results that differ and that both versions repeat.
4. **First line.** One of:
   - "N flips"
   - "0 flips, some of them on the changed code"
   - "no test ran the changed code in …"
   - "adds new code only"
   - "refactor only" (no tests run)
   - "docstring or comment changes only"
   - "no flip count", with the reason

   Anything not run or not seen is listed under Gaps.

## Numbers on the 47-pull-request set

The set is 17 Django, 20 sympy and 10 Flask pull requests merged after the earlier evaluation windows (`performance-v042/freeze.json`). Each comment was hand-labelled (`performance-fixes/labels.json`).

Labels: rubric v1 (rubric in `evaluation/PROTOCOL.md`).

| | v0.4.2 | v0.5.1 |
|---|---:|---:|
| First line correct | 37 of 47 | 42 of 47 |
| Adds something a reviewer would want | 15 | 19 |
| Adds nothing (correct, but no more than green CI says) | 25 | 26 |
| Adds noise | 7 | 2 |
| Behaviour-difference rows: false | 102 of 125 | 1 of 34 |
| Counted flips | 0 | 0 |

Every "adds something" case is change evidence: the pull request's own test fails on the base and passes on the head, which project CI never shows, or a behaviour row the description does not mention. No pull request in the set broke an existing test, so the flip count, AMU's main signal, was never tested here. The earlier 100-PR retrospective found 0 false flips among 30 counted (`retrospective/REPORT.md`).

## The five pull requests where the first line is still wrong

Labels: rubric v1 (rubric in `evaluation/PROTOCOL.md`).

| PR | Cause |
|---|---|
| Django #21887 | A class attribute turned into a property is not a side-by-side target, so whether a test reached it is unknown (engine). The code only runs on MySQL with a GIS backend, which AMU cannot provide. |
| Django #22158 | A new class plus a registry entry is not a side-by-side target (engine). The code only runs on Python 3.15, which AMU cannot know. |
| Django #22187 | A change to module-level data (`LANG_INFO`) has no measure of whether any test read it (engine). The first line is true but does not say the change is data (judgment). |
| sympy #30809 | `__slots__ = ()` on a mixin is a class attribute with no reach measure, and the PR's own test is filed as "new test for new code" because its error message names `__slots__` (engine). |
| sympy #29693 | "Adds new code only" treats a new method as unused, but this one is found by name lookup and changes `latex()` output (engine, judgment on which new methods count as reachable). |

Other known limits:
- Flask #5812 keeps one false behaviour row (`make_default_options_response`).
- Django `subTest` failures are not parsed (Django #22176, noise).

## What would justify the next engine change

After a month on at least one real repository with `counters: 'true'` (per-push counts and SHAs only, kept in the comment), a maintainer would need to see:

1. **Flips that are real.** At least 10 posted flips, with no more than 1 in 10 not caused by the change. The fresh-job rerun and the maintainer's own reading decide which are real.
2. **Flips acted on.** At least 3 pull requests where a flip AMU named was fixed by a later push before merge, visible in the counters as a flip that disappears with no edit to that test.
3. **Comments read.** The maintainer can name pull requests where the change evidence or a behaviour row changed what they reviewed or asked for. Without that, the 19 "adds something" labels above are only this evaluation's judgment.
4. **The misses matter there.** A class of the five misses above shows up in at least 3 of their pull requests before it is worth an engine change.

If flips stay at zero for the month, the flip path is still unmeasured. The question then is whether change evidence alone earns the job time: a median of 2 to 5 minutes on Django and sympy, and under 20 s on Flask.

## How much to trust these numbers (added 2026-11-10, v0.7.1)

The tables above are from v0.5.1. The current figures are in `generalize/REPORT-v071.md`: first line right on 44 of
the 47, and on 60 of 60 across four pilot repositories; 3 false rows; 0 counted flips.

- **One labeller, checked once.** A second, blind labelling of 20 comments agreed on the first line for 19 (kappa
  0.64) and on "adds something / nothing / noise" for 16 (kappa 0.6). Treat a difference of one or two pull requests
  between versions as within the labelling error.
- **Run-to-run stability: measured on 10 pull requests, three runs each** (`generalize/v071-47/stability.json`).
  The first line was word for word the same on 6, the same apart from test counts on 9, and differently worded on 1
  (Django 22176: two runs hit the time budget and said "not recorded", the third finished and said "ran in no test").
  Row counts were the same on all 10, but all 10 have 0 rows, so this sample says nothing about rows. The one known
  case is against: sympy 30841 showed 2 rows, then 0, then 2 on the same engine. Traced, both rows were false, and
  had been labelled real for two versions. A label can be wrong where the row reads plausibly.
- **Real regressions: 1 of 20 caught** (`regressions/REPORT.md`). A pull request that breaks something no existing
  test checks gets "0 flips", and that line is true. It is not evidence the change is harmless.
  With probes on (off by default, `claude-code` or an API key), a call that worked before and now fails in the
  project's code showed on 9 of the 20, and on 6 of the 47 normal pull requests, 3 of them from contrived inputs
  (`regressions/probes/README.md`). One run each, one reader, and the model may know the fixes.
- **Seven repositories, all pure-Python libraries with pytest or Django's runner.** Nothing here says how AMU does on
  applications, compiled extensions, or suites that need services.

## Known limit: coverage that depends on test order

Some suites share state across tests, sympy's cache for one. Whether a test reaches a line then depends on which tests
ran before it, and a suite recording made in one order can miss a test that reaches the changed code when it runs in
another. On the 47 set at v0.7.0 this hid 77 of 3,027 tests that would catch a raise on sympy 30815. Recorded on their
own, 2 of the 3 tried did reach the changed lines.

This is not fixed. When a test that this job ran on the merge base reaches a changed line the recording says it did not,
the comment says "coverage is order-dependent in this suite" and names the first such test. The candidates may then miss
a test that would catch the change.
