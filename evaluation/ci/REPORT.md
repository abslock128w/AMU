# AMU on pull requests: build report

Built from `AMU-CI-BRIEF.md`, in the owner's order, one commit per piece. Adoption bars, set by the owner on
2026-10-15:
- installed and commenting on AMU's own repository this week, on every pull request;
- running on three external repositories with slow suites within four weeks of release;
- across the first 50 pull requests it comments on: a false-flip rate under 5%, measured by rerunning each flip on
  the unchanged side in a fresh job, and a push that cleared the flip before merge in at least half of the pull
  requests that got a flip comment.

## 1. The composite action (`action.yml`, `amu/ci/run.py`)

**A composite action that installs the project with the caller's `install` command and installs AMU from a pinned
tag of this repository. It runs bare `amu` on the diff against the merge base and saves the JSON as an artifact. The
step always succeeds.**

- **Inputs:**
  - `install`, default `python -m pip install -e .`;
  - `python-version`, default 3.12;
  - `runner`, default detected;
  - `amu-ref`, the tag AMU is installed from, default `ci-v0.1.0`;
  - `base-sha` and `head-sha`, defaulting to the pull request event's;
  - `artifact-name`.
- **Outputs:** `merge-base` and `flips`.
- **Isolation:** the project goes into the job's Python. AMU goes into its own venv and drives the project's
  interpreter with `--python`, so neither installation changes the other.
- **What gets compared:** `git diff <merge base> <head>`, so commits that reached the base branch after the pull
  request was opened are not in it. If the checkout is not at the head, no merge base can be found, or the diff is
  empty, `report.json` holds an `error` and nothing runs.
- **Never fails the job:** an install that fails is a warning plus `install_ok: false` in `meta.json`, and AMU still
  runs and reports what could not run. If AMU itself cannot be installed, `report.json` says so. Every path writes a
  report, and `main` returns 0.
- **Artifact contents:** `report.json` (full evidence), `meta.json` (PR, base, head, merge base, AMU version and ref,
  seconds, status), `patch.diff` and the install logs. Kept 14 days.
- **Tests:** `tests/ci/test_run.py` (4), on a real git repository whose branch turns a test red.
  - The flip is found against the merge base, and a commit added to main later stays out of the diff.
  - A wrong checkout, a missing base and the CLI exit code are covered.
- **Not yet tested:** a GitHub run. That is piece 6.

## 2. The comment (`amu/ci/render.py`, `amu/ci/post.py`)

**One Markdown comment per pull request. The first line is the flip count and the head SHA. Then come flips, a runner
abort, gaps and what ran. The comment is found by a hidden marker and edited on each push.**

- **First line, one of:**
  - `**AMU: 2 flips** at abcdef1, among 40 tests run`;
  - `**AMU: no flip count** at abcdef1: <why>`, when nothing ran, the runner aborted or AMU produced no report.
- **It never claims more than ran.** A 0 is "0 flips among N tests run", and a closing line says tests outside the
  candidates were not run. No word in it calls the change good.
  - To support this, the full-evidence report now carries `executed` (ran, candidates, unrunnable, seconds, runner,
    budget). The compact agent packet is unchanged.
- **Untrusted text.** Test names and error text come from the pull request's code, so each goes into an inline code
  span with backticks and line breaks removed, cut to 300 characters. No heading, HTML, @mention or copied marker can
  take effect.
- **Size cap:** 60,000 characters, under GitHub's 65,536. Lists are halved until the comment fits, with "and N more in
  the artifact".
- **Find-or-create-then-edit.** Comments are paged through the REST API. Only a comment with the marker *and* written
  by the posting account (`github-actions[bot]` by default) is edited, so a marker copied into someone else's comment
  is ignored. The poster never fails the job.
- **Preview:** the action also saves `comment.md` in the artifact. The trusted posting job (piece 3) renders the
  comment again from the JSON and does not use that file.
- **Tests:** `tests/ci/test_render_post.py` (9):
  - the order of the sections, and that 0 is not a pass;
  - nothing ran, a runner abort, an error with a failed install;
  - code-span escaping against a hostile test name;
  - the size cap with 2,000 flips;
  - editing only our marked comment, and creating one when there is none.

## 3. Fork safety (`ci/amu-pr.yml`, `ci/amu-pr-comment.yml`, `amu/ci/post.py`)

**Untrusted code runs only under `pull_request`, with a read-only token. The comment is posted by a `workflow_run`
job that never checks out or runs the pull request's code. Neither the templates nor the action use
`pull_request_target`.**

- **`ci/amu-pr.yml`** (copy to `.github/workflows/`) is the untrusted job.
  - It runs on `pull_request` (opened, synchronize, reopened) with `permissions: contents: read`.
  - It checks out the head SHA with full history and `persist-credentials: false`.
  - Concurrency cancels an older run on the same pull request.
  - It calls the action pinned at `ci-v0.1.0`. It cannot post.
- **`ci/amu-pr-comment.yml`** is the trusted job.
  - It runs on `workflow_run` of that workflow, from the default branch, with `permissions: {}` at the top level and
    only `actions: read` and `pull-requests: write` on the job.
  - It has no checkout step. It downloads the artifact and installs AMU from the pinned tag with `--no-deps`; the
    renderer and poster need only the standard library.
  - It runs `amu.ci.post --event $GITHUB_EVENT_PATH`.
- **The artifact is data, never trusted.**
  - The pull request number and head SHA come from the `workflow_run` event and the API, not from `meta.json`.
  - A fork's pull request, which the event does not list, is found by the head repository's owner and branch. It
    counts only if it is open and its current head is the run's head.
  - A stale run (an older push finishing late), a cancelled run or a run not triggered by a pull request posts
    nothing, so it cannot overwrite a newer comment.
  - Untrusted strings are still escaped by the renderer (piece 2).
- **Tests:** `tests/ci/test_fork_safety.py` (6).
  - There is no `pull_request_target`.
  - The untrusted job has no write permission and no posting.
  - The posting job has no checkout and pins its AMU install.
  - Fork resolution by owner and branch.
  - Stale, cancelled and non-PR runs post nothing.
  - A forged `meta.json` (another pull request, another head) cannot redirect or relabel the comment.

## 4. Caching, budget and wall cap (`action.yml`, `amu/ci/run.py`)

**`.amu/` is cached, keyed on the merge-base SHA. It holds the index and any per-test recording. The default budget
is 300 s of test time per side. A wall cap bounds the whole run, and past it the comment says AMU was stopped.**

- **Cache.** A new step finds the merge base before anything is installed. `actions/cache@v4` saves `.amu` under
  `amu-<os>-py<version>-<merge base>`, and falls back to the latest cache for that OS and Python.
  - The index is incremental by file hash, so a fallback cache still saves work.
  - GitHub scopes a cache written by a pull request to that pull request, so one PR cannot poison another's.
  - `cache: 'false'` turns it off.
  - `meta.json` records whether an index was already there (`cached_index`).
- **Recording** (`observe: 'true'`, default off).
  - When the cache has no recording, the suite is recorded under coverage on the merge base, in a temporary git
    worktree that is removed afterwards. The recording is then copied next to the head's index.
  - It may use only what the wall cap leaves after twice the budget plus a minute. If that is too little, it is
    skipped, and the skip is listed as a gap.
- **Budget:** `budget` (default 300 s per side) is passed to `amu --budget`.
- **Wall cap:** `wall-cap`, default 1,200 s. It bounds recording plus comparison, with 60 s kept back for reading
  results.
  - Each run starts in its own process group, and at the cap the whole group is killed, so pytest workers die too.
  - The report becomes `error: stopped at the wall cap of N s; amu reported nothing`. The comment's first line then
    gives no flip count.
- **Tests:** `tests/ci/test_run.py` gains 2 tests (6 in the file; 21 CI tests in all).
  - A suite whose `conftest.py` sleeps 60 s is stopped at an 8 s cap within 30 s, and the report says so.
  - A second run finds the index in place and records on the merge base. The flip is still found, no recording gap
    is listed, and the worktree is gone.

## 5. Opt-in counters (`amu/ci/counters.py`, `amu/ci/verify.py`)

**With `counters: 'true'`, the comment carries a hidden block of counts and SHAs, one record per push. A fresh job
reruns each flip on the merge base. A collector computes both adoption bars from those comments. Nothing is sent
anywhere, and no source, test name or error text is kept.**

- **One record per push**, built by the trusted posting job from the artifact, every field typed and nothing else
  kept:
  - the head SHA (from the event) and the merge-base SHA, each checked to be a SHA;
  - flips, tests run, candidates and number of gaps, as integers;
  - whether the runner aborted or AMU errored;
  - seconds;
  - from the fresh-job rerun, flips checked, held and false.
- **Storage:** the block is an HTML comment inside AMU's own comment, so it stays in the user's repository. The
  history comes from AMU's previous comment only (marker plus author), keeps the last 100 pushes, and a rerun of the
  same head replaces its record.
- **False flips, measured as the owner set.**
  - `ci/amu-pr.yml` has an optional `verify-flips` job. It runs only when the run job reported flips, checks out the
    merge base in a fresh runner, and calls the action with `mode: verify`.
  - That job reruns every flipped test there with the project's runner and classes each one:
    - `held`: it passes again, so the flip stands;
    - `false`: it fails there too, so it was not the change;
    - `other`: skipped, could not run, or gone.
  - The posting job downloads that second artifact. The comment shows one line, "Rerun on the merge base in a fresh
    job: H passed again, F failed there too, so not the change, O could not be rerun". It has integer counts only and
    is shown whether or not counters are on.
- **Cleared before merge.** A pull request counts if a push with flips is followed by a push with 0 flips among tests
  that ran, and the pull request was merged.
- **Collector:** `python -m amu.ci.counters OWNER/NAME ... [--first 50]` reads those blocks across repositories. It
  prints, over the first 50 pull requests commented on in order of the first comment:
  - pull requests with a flip, cleared before merge, and the cleared share (bar: at least 0.5);
  - flips rerun, false flips, and the false-flip rate (bar: under 0.05).
- **Tests:** `tests/ci/test_counters.py` (5).
  - A record built from a report holding a secret test name and source text keeps neither.
  - History round-trips through the rendered comment, and a rerun replaces its push.
  - The summary computes both bars on a mixed set, and stops at the first 50.
  - The poster adds the block only when opted in.
  - `verify` classes passed, failed and missing tests on a real tree as held, false and other.
- Also in this commit: import order in `amu/ci/run.py`, which ruff flagged after piece 4 was pushed.

## 6. Dogfood on this repository

### Defects found before the first comment, each fixed with a test

1. **The checkout's own `amu` package shadowed the installed AMU** (fixed in 5e9bf30, `ci-v0.1.1`).
   - `python -m` puts the current directory first on `sys.path`. In this repository the checkout holds `amu/` from
     main, with no `amu.ci`, so the first run on PR #3 (`ci-v0.1.0`) reported "AMU could not be installed".
   - Every AMU invocation now uses `python -P`. A test with a decoy `amu` package in the checkout fails without the
     flag.
2. **The cache could overwrite tracked files** (b739937, `ci-v0.1.2`).
   - This repository tracks files under `.amu/`, and the action cached the whole folder. Restoring the cache could
     have changed them.
   - Only `.amu/index` is cached now. It holds both the index and the recording.
3. **`ci-v0.1.2` merged two steps into one** (2eeb1f2, `ci-v0.1.3`).
   - The edit for defect 2 dropped the `- uses: actions/cache@v4` line.
   - GitHub refused the action ("'if' is already defined"); PyYAML had accepted it.
   - A test now checks that each action step has unique keys and exactly one of `uses` and `run`. It fails on
     `ci-v0.1.2`'s file.
   - The YAML is also loaded locally with a parser that rejects duplicate keys.

### Enabled on this repository

- PR #3 added `.github/workflows/amu-pr.yml` and `amu-pr-comment.yml` to main (merged as 77fe972, on the owner's
  go-ahead). They are pinned to `ci-v0.1.3`, with counters on, the fresh-job rerun on, and this repository's install
  command (`pip install -c constraints.txt -e '.[dev]'`, Python 3.11).
- PR #3's own AMU run (`ci-v0.1.3`) succeeded. The pull request changes only YAML, so the report says no candidate
  test could run. That is the expected result, and the comment's first line says "no flip count" rather than 0.
- `workflow_run` workflows run only from the default branch, so pull requests opened before the merge get a comment
  from their next push on.

### The deliberate flip (PR #4, closed, not merged)

PR #4 moved the `manage.py` check in `runners.detect` ahead of explicit pytest configuration, which contradicts the
function's docstring. On its first push (36d7037), with everything running on GitHub:
- **The run job took 49 s:** project install, AMU install, cold index, then 42 candidate tests in 5.3 s.
- **The verify job took 38 s.** It reran the flip on the merge base in a fresh runner, where it passed again.
- **The posting job took 13 s.**

The comment, verbatim apart from the hidden blocks:

> **AMU: 1 flip** at `36d7037`, among 42 tests run
>
> ### Flips (1)
>
> Pass on the merge base `77fe972`, fail on the head. Each was rerun on the merge base to confirm it passed there.
>
> - `tests/core/test_reachability.py::test_runner_is_detected_from_the_tree_and_labels_are_pasteable`
>
> Rerun on the merge base in a fresh job: 1 passed again, 0 failed there too, so not the change, 0 could not be rerun.
>
> ### Gaps (1)
>
> Not run or not seen:
>
> - `15035 call sites unresolved repo-wide; dynamic dispatch may reach more`
>
> ### What ran
>
> 42 of 42 candidate tests on the head, 5.3 s with `pytest`, budget 300 s per side. Failures were rerun on the merge
> base. Tests outside the candidates were not run.

- **The flipped test is the right one.** It is the one test that asserts the detection order, and it is the one the
  local rehearsal found.
- **The counters block holds one record:** head and merge-base SHAs, flips 1, ran 42, candidates 42, gaps 1, checked 1,
  held 1, false 0, 11.2 s. It holds no test name or text.

**Second push (071a4a0, a revert of the flip): the same comment was edited in place, not duplicated.** It is still
comment 6197228987, the only one on the pull request. Its first line became "**AMU: no flip count** at `071a4a0`: `the
pull request changes nothing against its merge base; nothing was run`". The counters history now has two records: the
first push's, and one with `error: true` and no counts.

By the collector's rule, PR #4 does not count as cleared before merge, for two reasons:
- a push counts as clearing only if it shows 0 flips among tests that ran, and the revert ran nothing;
- PR #4 was closed, not merged.

PR #4 was then closed without merging.

### PR #2

- The push of 3484b1d to `accuracy/observed-tests` gave PR #2 its first comment: "**AMU: 0 flips** at `3484b1d`, among
  150 tests run". It ran 150 of 150 candidates in 72 s, and the job took 125 s.
- Its gaps say that the patch exceeds 2 MiB and only the first 2 MiB was inspected, and that 256 non-Python files
  were skipped. The comment does not present 0 as a pass.
- The verify job was skipped, as it should be with no flips.

### Where the adoption bars stand

- **Commenting on every pull request here this week: started.** PR #2 and PR #4 got comments. PR #3, the one that
  enabled AMU, ran the job but could not post: the posting workflow was not on main until PR #3 merged it. From now
  on every pull request into main gets the comment.
- **Three external repositories within four weeks: not started.** It waits on the owner's introductions.
  `INSTALL-NOTE.md` has the one-paragraph install note and the sample comment.
- **First 50 pull requests (false-flip rate under 5%, cleared share at least half): measurable now.**
  - The counters, the fresh-job rerun and `python -m amu.ci.counters` work end to end on this repository.
  - Counted so far, across PR #2 and PR #4: 1 flip rerun, 0 false, and no pull request merged after a flip.

### Known limits, not fixed

- GitHub warns that the Node 20 actions used here (`checkout`, `setup-python`, `cache`, `upload-artifact` and
  `download-artifact`, all v4 or v5) are being run on Node 24. They work, and nothing in the action depends on the
  Node version.
- On PR #2 the diff is over 2 MiB, so AMU inspected only the first 2 MiB. The comment lists that as a gap.

## Status (2026-10-16)

### What main contains

- **PR #2 is merged** with a merge commit (4b2d51d), after CI was green on its last head (56053a8): Ubuntu and
  macOS on Python 3.11 and 3.14, the viewer check, and AMU's own check. It brings in everything above:
  - the `amu/core` engine with shadow execution;
  - the slow-suite trial record and its post hoc re-analysis;
  - the composite action `action.yml` and the `amu.ci` package (run, render, post, verify, counters);
  - the workflow templates in `ci/`;
  - this report and `INSTALL-NOTE.md`.
- **The distribution is `anti-messup`, version 0.1.0.** The PyPI name `amu` belongs to an unrelated project, and the
  owner chose `anti-messup`. The import name stays `amu`.
- **Workflows:**
  - `.github/workflows/amu-pr.yml` and `amu-pr-comment.yml` pin `abslock128w/AMU@v0.1.0` (cb67c27);
  - `ci.yml` holds the existing release checks;
  - `publish.yml` uploads a tag to PyPI, only when started by hand.

### The release

- **Tag and release:** `v0.1.0` is on 4b2d51d, and the GitHub release "AMU 0.1.0" has the install note as its body
  (https://github.com/abslock128w/AMU/releases/tag/v0.1.0).
- **The action installs AMU** from that tag (`anti-messup @ git+https://github.com/abslock128w/AMU@v0.1.0`).
- **Checked on a fresh pull request:** PR #5, a one-word code-comment change, got AMU's comment from the v0.1.0 pins.
  Its footer reads "AMU `0.1.0` from `v0.1.0`". No test reaches a comment, so the first line correctly gave no flip
  count. PR #5 was closed without merging.
- **PyPI: not uploaded yet.** Two runs of `publish.yml` with a token secret built the package and passed the smoke
  test, but got no token, because the repository's Actions secrets list stayed empty. Nothing was published.
  `publish.yml` now uses PyPI trusted publishing, so no token is stored. It waits on the owner's pending publisher on
  PyPI (project `anti-messup`, owner `abslock128w`, repository `AMU`, workflow `publish.yml`, no environment).
  - Building from the tag was rehearsed locally: sdist and wheel `anti_messup-0.1.0`, the archive check, and the
    installed-wheel smoke all pass.
  - Once the package is on PyPI, the action can install `anti-messup==0.1.0` instead of the git URL, in a v0.1.1.

### The three adoption bars, with current numbers

1. **Commenting on every pull request in this repository this week: on track.**
   - Every pull request opened since the workflows reached main got the comment: PR #4, PR #5, and PR #2 on its
     next push.
   - PR #3, which added the workflows, could not get one, because the posting workflow was not yet on the default
     branch.
2. **Three external repositories with slow suites within four weeks of release: 0 of 3.** The owner is bringing the
   names. The install note is the release body.
3. **Across the first 50 pull requests commented on: 3 of 50 so far.**
   From `python -m amu.ci.counters abslock128w/AMU` on 2026-10-16:
   - with a flip: 1 (PR #4, the deliberate one);
   - flips rerun on the merge base in a fresh job: 1, false: 0, so a false-flip rate of 0 of 1 (bar: under 5%);
   - cleared by a push before merge: 0 of 1 (bar: at least half), because PR #4 was closed, as intended.
   - These are dogfood pull requests. External ones have not started, so neither bar says anything yet.

## Brief 4, Phase A: the difference finder (v0.2.0, 2026-10-16)

Full record: `evaluation/difference-finder/REPORT.md`. The comment now also has:
- **Change evidence: the pull request's own tests.** Tests the pull request adds or edits are run on the merge base
  with the tests as the pull request leaves them; those that fail there and pass on the head are listed.
- **Flips not counted.** A flip counts only after the same order on both sides, a reach check (it ran a changed line)
  and three reruns on the head. The others are listed as `unstable` or `not caused by this change`, with the reason.
- **What ran** names the ordering, and says when the merge base side was answered entirely from the cache of earlier
  passes.

Counters gain `evidence`, `replayed`, `unstable` and `not_caused` per push; the fresh-job rerun covers counted flips
only, so its false-flip rate is the trust layer's. Collector on 2026-10-16, before the v0.2.0 comments: 3 pull
requests, 1 flip rerun, 0 false.
