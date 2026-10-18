# Brief 5 retrospective: protocol, fixed before any frozen pull request is run (2026-10-18)

The brief is `AMU-NEXT-PROMPT-5.md`. This file fixes how pieces 2 and 3 run, so nothing is chosen after the numbers
are seen. A change to it after the run starts is listed in the report as a change, with the reason.

## The set

`freeze.json`, written by `freeze.py`, pinned by `freeze.sha256` (sha256 of the canonical JSON of its `prs`). Django
`main` and sympy `master`, the last 50 merged pull requests of each, before 2026-10-18 00:00 UTC, whose files include
production Python. `run.py` refuses to start when the pin does not match. Each pull request runs once.

## The engine

AMU v0.2.6 (tag commit 572f76f), the release the Action pins, installed from `git archive v0.2.6` into its own venv
under the scratch root (`/Users/Shared/amu-retro-2026-10-18/amu-venv`), as the Action's "Install AMU" step does. The
run never imports AMU from this checkout. `run.py` hashes the installed package before the first pull request and
checks the hash before every one; a mismatch stops the run.

## Each pull request, as the Action would run it

- Two worktrees of the full-history clone: the head SHA and the merge base (`git merge-base <base.sha> <head.sha>`,
  the Action's own step).
- In the head worktree, the Action's run step, with the Action's v0.2.6 defaults and the inputs the brief names:
  `python -P -m amu.ci.run --base <base.sha> --head <head.sha> --pr N --python <project python> --install-ok true
  --amu-ref v0.2.6 --budget 300 --wall-cap 1200 --observe`. Runner detected from the tree. No `--counters`: the counter
  block is an opt-in that changes nothing in the comparison. No generation from AMU (`--generate-dollars 0`): there is no
  key; this session is the generated-test tier (below).
- **The tracer on:** `--observe`, which on Python 3.12 and later records per-test call edges with the B1 tracer on the
  merge base when the cache holds no recording, inside what the wall cap leaves.
- **The base-side cache on:** `.amu/index` is carried from one pull request to the next, in merge order (oldest
  first), per repository. This is the Action's `actions/cache` restore-key fallback ("the latest cache for that OS and
  Python") on a repository whose cache is shared from the default branch. The first pull request of each repository
  starts with no cache, so its time is the cold time.
- **Side-by-side on:** it runs whenever the interpreter is 3.12 or later. Python 3.14.6 (Homebrew) for both projects.
- Environments, one venv per project in the scratch root: Django with its install requirements (`asgiref`,
  `sqlparse`), sympy with `mpmath`, `pytest`, `hypothesis`. The tree under test is first on `PYTHONPATH` (AMU's
  runner environment), so neither project is installed into its venv.
- **The fresh-process rerun:** when the run reports counted flips, the Action's `verify-flips` job runs:
  `python -P -m amu.ci.verify` in the merge-base worktree, a tree AMU never touched, new processes. A flip it sees
  fail there too is a false flip.
- **The comment:** rendered from the JSON the way the posting job renders it (`amu.ci.render.render` with the verify
  counts and quiet mode on), saved as `<repo>/<pr>.md`, with `report.json`, `meta.json` and `verify.json` combined as
  `<repo>/<pr>.json`.
- **Timing:** job time is the run step's wall time (`meta.seconds`: recording, analysis, both sides, trust, isolation,
  side-by-side). The verify job is timed apart. Checkout and install are not in it. One pull request at a time; nothing
  else of this work runs a suite while a pull request is running (`run.py` holds a lock that the probe step also takes).

## The generated-test tier: this session writes the probes

- Targets: exactly the automated tier's: the comparable changed functions that side-by-side saw no call of
  (`sidebyside.targets` minus `side_by_side.called`), recomputed from the same two trees with the frozen engine.
- One probe per target, written from the function before and after the change, the pull request's title and
  description, and one test file of the project, which is what the model is given. Setup statements plus one call
  expression, never an assertion or an expected value.
- The probes go through AMU's own filter unchanged: `amu.core.generate.run` from the frozen engine, with a client that
  returns the probes written here in place of a model reply (cost 0). Kept only if all five steps pass: compiles and
  runs on the head, the same outcome five times there in fresh processes, runs a changed line, one stable outcome on the
  merge base that differs.
- One attempt per target. A probe whose own setup fails (step 1 or 2) may be rewritten once, and both attempts count.
  A probe is never rewritten after its outcome on either tree is seen.
- At most 20 minutes per pull request, from first reading its targets to the last filter run. Attempted, kept and
  minutes are recorded per pull request.
- Kept probes go into the comment under the automated tier's heading ("Generated tests showing a difference").
- Each kept probe gets a hand label: the difference it shows is intended by the pull request, or not.

## Labels and samples

- **Side-by-side hand sample:** if more than 20 pull requests show a side-by-side difference, the 20 with the lowest
  `sha256("<repo>#<number>")`; otherwise all of them. Every row of a sampled pull request is labelled `real, intended`,
  `real, not intended`, or `artifact` (with its cause). Precision is real rows over all rows labelled.
- **Hand labels** use only the pull request's title, description, linked ticket, diff and its own tests.

## The numbers, in this order

1. false-flip rate: counted flips the fresh-process rerun saw fail on the merge base, over all counted flips;
2. pull requests with at least one counted flip;
3. pull requests with a side-by-side difference, the hand sample and its precision;
4. share of comparable changed functions with no captured call (over all comparable changed functions, pooled; and the
   median per pull request);
5. median and worst job time, per repository.

Then: quiet pull requests (`render.nothing_found`); pull requests with change evidence from their own replayed tests;
probes attempted, kept and minutes per pull request; the label of every kept probe. Then the brief's decision rules.

## Defects found during the run

Recorded with the pull request where they showed, the run finishes on the frozen engine, fixes come after, and the
report says which numbers predate a fix.

## Changes to the protocol after the run started

- **2026-10-18, after sympy #30476, the owner's decision: the tracer is off for the other 49 sympy pull requests.**
  Sympy's whole suite cannot be recorded inside what the 1200 s wall cap leaves (the pilot on sympy #30525 and #30476
  both stopped it). On #30476 the recording used its share and AMU was then stopped at the wall cap: the comment said
  only "no flip count: stopped at the wall cap of 1200 s; amu reported nothing". The tracer gives sympy nothing either
  way, so the comparison runs without it from #30511 on. Recorded as a product defect, with the hook files a stopped
  recording leaves in the project's site-packages as a second, both to fix after the retrospective.
