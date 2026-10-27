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

## Brief 4, Phase C (v0.2.3 and v0.2.4, 2026-10-17 and 18)

Full record: `evaluation/generate/REPORT.md`.
- **C1 (v0.2.3): model-written probes for changed functions no test calls,** kept only on execution evidence.
  - Caps: `generate-dollars`, default 0.10, and `generate-minutes`, default 20.
  - Turned on by an `anthropic-api-key` input.
  - **Unmeasured:** the repository has no `ANTHROPIC_API_KEY` secret yet.
- **C2 (v0.2.4):** for counted flips:
  - hunk isolation;
  - an informational `AMU` check (neutral or success, never failure) with inline notes on the hunk;
  - quiet mode when nothing was found;
  - per-kind acted-on counters.
- **Counter on 2026-10-18:** 13 pull requests, 20 flips rerun in a fresh job, 0 false.

## AMU's job time on its own pull requests, profiled (v0.2.5, 2026-10-18)

The job took 3 to 6 minutes on AMU's pull requests. One run was profiled locally:
- the diff of PR #13, on a warm index, with Python 3.14;
- every runner call, tree copy and index load was timed and attributed to its caller.

| Step | Before | After PR #20 |
|---|---:|---:|
| Side-by-side run on the merge base (192 tests; capped at 120 s) | 121.8 s | 48.0 s |
| Candidate run on the head (216 tests) | 109.3 s | 84.7 s |
| Tree copies | 3.0 s | 3.0 s |
| Trust reruns, replay, index | about 7 s | about 7 s |
| **Total** | **240.3 s** | **141.5 s** |

**Where the time went:** AMU's own tests, run twice, once per side.
- One test, `test_comparisons_made_before_the_time_limit_are_kept`, slept 30 s and so took 34 s of the 144 s of
  per-test time. It now sleeps 8 s, which still outlasts the 4 s limit it checks.
- The rest is subprocess-heavy reachability tests, at 4 to 8 s each.

**Cut in PR #20:**
- the sleep;
- the trust triad's third rerun. It replayed every other candidate before the flips; it now replays the tests that ran
  before each flip in its own batch, which is the order the flip failed in.

The third-rerun cut does not show on PR #13, whose flips were edited-test flips (2.6 s). With ordinary flips, that
rerun had taken about 140 s.

**Tried and dropped:**
- running side-by-side in chunks of 40 with an early stop;
- skipping a function whose one call takes over 2 s.

Neither saved time on that diff, so neither shipped.

**Found on PR #20's own job, fixed in PR #21.** PR #20's job took 714 s, and side-by-side took 505 s of it, past its
120 s cap. Reproduced locally:
1. The side-by-side batch on the merge base named a test the pull request adds.
2. pytest rejected the whole batch.
3. `runners.execute` split the batch to isolate that label and gave every split the full limit again.

Fixes:
- a runner call's limit is now one deadline, splits included;
- side-by-side no longer names the change's own tests.

PR #20's diff, locally: 497.8 s before, 217.7 s after. Both new tests fail on the old code. The release pull request
(PR #22), the first job on v0.2.5, ran 1,040 tests in 306 s; the 0.2.4 release pull request (PR #15) ran 920 in 307 s.

## Quiet mode and the inline note on real pull requests (2026-10-18)

Two throwaway pull requests, both closed unmerged with their branches deleted. AMU ran from `v0.2.4`.

**PR #18: a same-behaviour refactor of `plural()` in `amu/ci/render.py`.** 62 tests ran and nothing was found.
- The comment, in full apart from its collapsed part:
  > **AMU: 0 flips** at `76819fc`, among 62 tests run
- The collapsed "What ran and what was not seen" holds:
  - side-by-side: 5 calls of 1 comparable changed function, 0 differences;
  - one gap: unresolved call sites repo-wide;
  - the run: 62 of 62 candidates, 212 s.
- The `AMU` check is `success`, titled "AMU: 0 flips at 76819fc, among 62 tests run", with 0 annotations.

**PR #19: two hunks in `amu/ci/render.py`.** Hunk 1 is a same-behaviour rewrite of `short()`. Hunk 2 is a deliberate
break of `plural()`: `count <= 1`, so 0 reads as singular.
- The comment's first line:
  > **AMU: 2 flips** at `98279f1`, among 62 tests run
- Both flips, `test_zero_flips_is_a_count_among_what_ran_not_a_pass` and
  `test_quiet_mode_folds_a_report_with_nothing_found_into_one_line`, read:
  - `path ... > render > plural`;
  - `caused by hunk 2 of 2 in amu/ci/render.py at line 43`.

  Hunk 1 was not blamed.
- The fresh-job rerun on the merge base found 2 passed again, 0 failed there too.
- Side-by-side listed one difference: `plural(0, 'flip')` returned `'0 flips'` as it was and `'0 flip'` as changed.
- The `AMU` check is `neutral`, with 2 inline notes (warning level). Both sit at `amu/ci/render.py` line 43, the
  broken line, and are titled "AMU: a test that passed on the merge base fails with this hunk". Each note names its test.
- The repository's own `verify` jobs failed on it, as intended.

Both presentation pieces have now been seen on a pull request. Phase C's "what is left" no longer lists them.

**Seen on PR #20, not fixed: behaviour differences can hide where the outputs differ.** PR #20 changed comment
wording, and the 10 behaviour-difference rows for `render` and `shadow.run` cut both outcomes at the same prefix,
before the words that differ. The difference was intended, but a reader cannot see it in the row.

## Brief 5: the comment on 100 pull requests that are not AMU's own (2026-10-18)

Full record: `evaluation/retrospective/REPORT.md`; the comment AMU would have posted on each pull request is in
`evaluation/retrospective/django/` and `.../sympy/`. Run locally, as the v0.2.6 Action would, on the last 50 merged pull
requests of Django and sympy that touch production Python.

| | Django | sympy | Both |
|---|---:|---:|---:|
| False-flip rate (fresh-process rerun on the merge base) | 0 of 8 | 0 of 22 | 0 of 30 |
| Pull requests with a counted flip | 5 | 11 | 16 |
| Pull requests with a side-by-side difference; precision on all 18 rows | 4; 82% | 2; 29% | 6; 61% |
| Comparable changed functions with no captured call | 76% | 64% | 71% |
| Median, worst job time | 24 s, 439 s | 357 s, 1,140 s | |

- Every counted flip is a test the pull request edited, run in its old form. None was a regression in a test the pull
  request left alone.
- On 36 of 50 Django pull requests a `gis_tests` label aborted Django's runner and cut the run short; 4 ran no test.
- Sympy's tracer recording cannot fit the wall cap. On sympy #30476 it left AMU stopped at the cap with a one-line
  comment, and the tracer was off for the rest of sympy.
- This session wrote the generated-test tier's probes: 344 attempted, 105 kept by AMU's filter. 102 show intended
  differences, 2 unintended but cosmetic, and 1 is an artifact of the filter.
- Decision rules triggered:
  - profile sympy #30476 first;
  - move side-by-side rows to the collapsed section;
  - the generated-test rule, as written.

## Does the comment tell a reviewer something the diff does not? (2026-10-27)

A short test, no engine change. Live part on this repository with the Action as a maintainer gets it today: the
workflows on main pin `v0.3.0`, so the features merged behind `--extras` are off, there is no `ANTHROPIC_API_KEY`
secret (no generated tests), and the recording is off (the default).

### Step 1: three throwaway pull requests on this repository

Each was a one-commit change with no test touching it, opened into main, closed unmerged, branch deleted
(#48, #49, #50, 2026-10-27).

| Case | Change | Wanted | Comment posted | Came out as wanted |
|---|---|---|---|---|
| (a) tests call it, no test asserts on the edge | `amu/ci/render.py#short`: a missing sha renders `(none)` instead of `unknown`. Tests call `short(None)`; none checks the text. | a side-by-side row with the input and both outcomes | "0 flips among 111 tests run". Collapsed: side-by-side compared 5 calls of `short`, no difference; side-by-side stopped at its time limit. | no |
| (b) no test reaches it | `amu/ci/counters.py#collect` skips pull requests closed without merging. No test calls `collect` or `counters.main`. | an untested-change row or a probe, not silence | "0 flips among 29 tests run". Collapsed gaps: "No existing test reaches the change within five source hops"; the changed function was not called. No probe (no API key; generated tests need one). | no: the fact is there, but only as a gap line under a "0 flips" headline |
| (c) pure rename of a private helper | `amu/core/coverage.py#_relative` renamed `_repo_path` (3 sites in one file). | quiet, or marked as a refactor | "0 flips among 160 tests run", 160 of 182 candidates, nothing else. Not marked as a refactor (that labelling is behind `--extras`, not in v0.3.0). | yes (quiet) |

Wanted: 1 of 3.

- (a) The headline reads as all-clear on a pull request that changes behaviour. Why no row: side-by-side captures at
  most 5 calls per changed function per process (`CALLS` in `amu/core/sidebyside.py`), and it compared exactly 5. Run
  locally, the CI tests (`tests/ci`) call `short` 46 times, 3 of them with `None`, so the 5 captured were most likely real shas, where
  both versions agree. The report JSON does not list which calls were captured, so this is the likely cause, not a
  read one.
- (b) The comment is not silent, but a reviewer sees "0 flips" and must open the collapsed section to learn that
  nothing tested the change. The gap line is the only place that says so.
- (c) Spent 7 minutes and 160 tests to say nothing about a rename. Quiet is the right answer; it is not cheap.

### Step 2: ten pull requests from the frozen 100

Picked by rule, not by reading them first: in the order the Brief 5 run processed them (its local results table,
not in the repository), the first three Django and first two sympy pull requests with a flip or a side-by-side row ("reported"), and the first
three Django and first two sympy pull requests whose comment was quiet. The comments are the v0.2.6 ones from Brief 5
(`evaluation/retrospective/<repo>/<n>.md`); v0.3.0 renders these sections the same way. "Diff alone" is what a
reviewer gets from the diff and the description.

| Pull request | AMU said | Diff alone | What the comment adds | Label |
|---|---|---|---|---|
| Django #21820 | 1 flip | A `TypeError` message for `path()` gains "but got str."; the one test asserting the message is edited to match. | The flip is that edited test run in its old form, which fails by design. The evidence row (new test fails on the base) is true but obvious for a message-string change. | adds noise |
| Django #21805 | 3 flips | Deprecation warnings renamed across 30+ files, old names kept as aliases, many tests edited. | 3 flips, all edited tests in their old form; 28 "evidence" tests only fail on the base because they import the new names; 40 of 4,128 candidates ran, runner errors fill the gaps. | adds noise |
| Django #21736 | 1 side-by-side row | `EmailValidator.validate_domain_part()` deprecated in favour of `validate_domain()`; docs and deprecation timeline say so. | The row shows `validate_domain_part('example.com')` returned `True` and now raises the deprecation warning: the deprecation the diff announces. 14 evidence tests fail on the base because they call the new method. | adds nothing |
| sympy #30540 | 1 flip | `nonlinsolve` keeps denominator restrictions (fixes two issues); 9 new tests, one older test's expected answer edited. | The flip is the edited older test (visible in the diff). But all 9 new tests fail on the base and pass on the head, checked by running them: the tests pin the fix, which the diff cannot show. | adds something a reviewer would want |
| sympy #30572 | 2 flips | Printing of elementwise functions changes (`exp_o(A)`); the expected strings in 2 printing tests are edited, 2 tests added. | Both flips are the edited expected strings in their old form; the 4 evidence tests fail on the base because the output text changed. | adds noise |
| Django #21853 | quiet | One Oracle 23ai skip entry added for a GIS test. | 0 flips among 14 tests; the changed code is Oracle-only and no test ran it. | adds nothing |
| Django #21714 | quiet | One line in `RelatedIn` (`self.prepare_rhs = False`); no test added. | 0 flips among 2,393 tests and no difference on 5 captured calls; Django's own CI says the first part. That no test reaches the case the line fixes is implied, not said. | adds nothing |
| Django #21659 | quiet | `DISTINCT ON` ordering fix in the SQL compiler with 8 new tests and a 6.1.1 release note. | The collapsed section says the 8 new tests "pass on the merge base too, so they show nothing about the change". They were skipped on both sides (SQLite has no `DISTINCT ON`), so the sentence is wrong. | adds noise |
| sympy #30515 | quiet | `function_range` decomposes composed functions so `sin(exp(x))` stops hanging; one regression test. | 0 flips among 552 tests; the changed function was not called by any test that ran. Replaying the new test on the base timed out, which is the hang the PR fixes, but the comment lists it as a gap, not as evidence. | adds nothing |
| sympy #30477 | quiet | LaTeX (lark) grammar so `\sinh x` parses; 3 tests added or edited. | The collapsed section says the 3 tests "pass on the merge base too". They were skipped on both sides (lark is not installed), and none of the 73 tests run touched the parser. | adds noise |

Count: adds something 1, adds nothing 4, adds noise 5.

- Every flip in the five "reported" pull requests is a test the pull request edited, run in its old form. The headline
  counts it as a flip, so the first line of the comment flags an intended, visible change.
- The one useful thing in the ten is change evidence on a fix whose new tests could have passed without it (#30540).
  It is useful only when the tests are not trivially new (new names, new message text, new methods).
- Defect, not fixed here: `amu/ci/render.py` counts a replayed test with verdict `none` as "pass on the merge base
  too". A test skipped on both sides gets that verdict, so a skipped test is reported as passing (#21659, #30477).

### Step 3: time from pull request opened to comment posted

From the pull request's `created_at` to the AMU comment's `created_at` (GitHub API).

| Case | Opened (UTC) | Comment posted (UTC) | Minutes | AMU's own run time (report) |
|---|---|---|---:|---:|
| (a) #48 | 10:25:54 | 10:33:34 | 7.7 | 399 s |
| (b) #49 | 10:25:56 | 10:27:11 | 1.3 | 13 s |
| (c) #50 | 10:25:57 | 10:34:09 | 8.2 | 428 s |

- (a) and (c) went past the 300 s per-side budget: the side-by-side run on the merge base hit its own time limit, and
  in (c) the shadow run stopped at 160 of 182 candidates.

### What this says

- Live: 1 of 3 came out as wanted, and that one is the case where saying nothing is right.
- Frozen: the comment added something a reviewer would want on 1 of 10 pull requests; 5 of 10 it added noise.
- The headline is the problem in both parts. It counts tests the pull request edited as flips, and it says "0 flips"
  over a change no test reached or a behaviour change no captured call exercised.

## The five comment fixes, re-scored on the same 13 cases (0.4.0, 2026-10-27)

Fixes, each with a test that fails on the code before it, each its own PR: #52 skipped is not passed; #53 new test for
new code; #54 the first line (edited tests are not flips; "no test ran the changed code"; "0 flips" only when something
ran it); #55 side-by-side sampled by argument shape, compared calls recorded; #56 refactor only. The rename-labelling
extra had not met its ship rule (see the last section), so #56 runs AMU's own rename-and-move match by default.
Released as `v0.4.0`. While re-scoring, #58 fixed a slip in #54: the first line counted the pull request's own tests
skipped on the head as having run (sympy #30477). The tables use the renderer with #58.

How each case was re-run: the engine at `967db59` (main after #56; its footer still says `0.3.0`, the version bump came
with the release PR), as the Action runs it: budget 300 s per side, wall cap 1200 s, no generated tests, comment
rendered quiet. The three live cases ran locally at their original heads against merge base `a51c07e`, recording off
as on the Action. The ten frozen pull requests were rerun locally, not re-rendered, because #53 and #55 change what is
captured. They used the retrospective's SHAs and its recording setting (Django on, sympy off), each starting from a copy
of the index cache the retrospective left. Driver: `evaluation/ci/rescore-0.4.0/rescore.py`. Every comment is in that
folder.

### Step 1 again: the three live cases

| Case | Wanted | 0.3.0 first line (CI) | 0.4.0 first line (local) | Wanted? before → after |
|---|---|---|---|---|
| (a) edge-case change, tests call it, none asserts | a side-by-side row | `AMU: 0 flips … among 111 tests run`; no row | `AMU: 0 flips … among 111 tests run, some of them on the changed code; 1 behaviour difference on captured input`. The row: `short(None)`, `'unknown'` before, `'(none)'` after; 10 calls compared over 3 shapes | no → yes |
| (b) change no test reaches | not silence | `AMU: 0 flips … among 29 tests run`; the fact in a collapsed gap | `AMU: no test ran the changed code in amu/ci/counters.py … ; 29 tests run` | no → yes |
| (c) pure rename of a private helper | quiet or refactor | `AMU: 0 flips … among 160 tests run`, 7 min | `AMU: refactor only … every change renames or moves 1 definition without changing it; no tests run` | yes (quiet) → yes (refactor) |

Wanted: 1 of 3 before, 3 of 3 after. AMU's own run time locally: 321 s, 5 s, 3 s. This was measured on this machine,
so it does not compare directly with the CI minutes before, but (c) no longer runs tests at all.

### Step 2 again: the ten frozen pull requests

Same picks, same three labels, same reading: what a reviewer gets from the diff and description, against what the
comment adds.

| Pull request | Before (v0.2.6) | After (0.4.0): what the comment says now | After | Job s before → after |
|---|---|---|---|---:|
| Django #21820 | adds noise | `0 flips` among 965; the edited message test is under "Tests this pull request updated", not a flip; its new form fails on the base on the message (evidence). The diff already shows the message change. | adds nothing | 435 → 65 |
| Django #21805 | adds noise | `0 flips` among 16,081 (40 before: the `gis_tests` abort was fixed in 0.3.0). 117 edited tests under "updated"; 11 "new tests for new code"; 106 "do not load on the merge base for a reason AMU did not read". No false claim; nothing a reviewer needs. | adds nothing | 42 → 423 |
| Django #21736 | adds nothing | `0 flips`; 9 evidence tests (overrides of the new hooks fail on the base by assertion); 5 new tests for new code; 8 collapsed rows, all the deprecation warning the diff announces | adds nothing | 17 → 289 |
| sympy #30540 | adds something | `0 flips`; the edited older test is under "updated"; 10 evidence tests pin the fix; one collapsed row the diff does not make obvious: `substitution` used to empty the `result` list its caller passes and now leaves `[{y: 1}]` in it | adds something a reviewer would want | 54 → 319 |
| sympy #30572 | adds noise | `0 flips`; the 2 edited printing tests under "updated"; 4 evidence tests fail on the base because the printed text changed | adds nothing | 225 → 332 |
| Django #21853 | adds nothing | `no test ran the changed code in django/contrib/gis/db/backends/oracle/features.py` | adds nothing (true now, but a reviewer knows an Oracle skip list is not run here) | 16 → 29 |
| Django #21714 | adds nothing | `0 flips` among 4,776, some on the changed code; no difference on the captured calls; nothing says the fixed case went untested | adds nothing | 68 → 266 |
| Django #21659 | adds noise | `0 flips`; collapsed: "8 tests skipped on both sides … they did not run here" (was "pass on the merge base too") | adds nothing | 23 → 415 |
| sympy #30515 | adds nothing | `no test ran the changed code in sympy/calculus/util.py, apart from the 1 test this pull request adds or edits; 2896 tests run`: the new branch for every composed function is exercised only by the test the pull request edits (9,978 lower-ranked candidates not run) | adds something a reviewer would want | 360 → 352 |
| sympy #30477 | adds noise | `no test ran the changed code in` the two lark parser files; collapsed: the 3 new tests were skipped on both sides (lark not installed) | adds nothing | 380 → 382 |

| | adds something | adds nothing | adds noise |
|---|---:|---:|---:|
| Before (v0.2.6) | 1 | 4 | 5 |
| After (0.4.0) | 2 | 8 | 0 |

Target was noise 0 of 10 (met) and adds something at least 3 of 10 (not met: 2). The fixes removed the noise. On these
ten they found one thing a reviewer would want that the earlier comment did not show (#30515's first line). #30540's
mutation row is a second, but it is collapsed.

Job time went up on most Django pull requests because 0.3.0 runs what `gis_tests` used to abort (21805: 40 tests then,
16,081 now), not because of these fixes. The flip counts held: no flips among the ten after, as before once edited
tests are set apart.

### Found while re-scoring, not fixed

- **Django #21805, 106 tests "not read".** `tests/mail/__init__.py` imports the new warning class. On the merge base
  Django cannot import that test package, and the whole replay batch aborts. The existing split rule ("half a batch
  aborts the same way: every remaining candidate is unrunnable") then marks all of them unrunnable, so the runner never
  prints a per-test error for #53 to read. Run directly on the merge base, each of the 106 fails with its own
  `ImportError` on `RemovedInDjango2028Warning`. The fix belongs in the split rule, not in the comment.
- **Django #21805, side-by-side did not start: "Argument list too long".** 16,081 labels on one runner command line.
  This existed before 0.4.0 and was hidden while `gis_tests` aborted the runs.
- **The footer** shows the engine's `__version__`; a run from an unreleased commit shows the last release.

### The part 4 measurement of the extras did not finish

`/Users/Shared/amu-retro-2026-10-18/suite.lock` was still held when the re-score started. Holder: the part 4
measurement (`q4/measure.py`), "sympy 30477", pid 6741, taken 2026-10-27 16:14 IST. That process is gone, and the lock
was not released. Its checkpoints (`out-q4/<repo>/<n>/done.json`) show 53 of the 100 pull requests finished (all 50
Django, sympy #30476, #30511 and #30515). Sympy #30477 started and has only its patch. The owner's note says 49 PRs; the
checkpoint files say 53. `measure.log` holds the Django rows twice: a second launch on 2026-10-27 re-read the
checkpoints and died on #30477.

So the extras' numbers cover Django and three sympy pull requests: refactor 4 rows (all on Django #21805), mutation
survivors on 11 Django PRs, equivalence 0. They are not enough for the ship rule (at least one true row and under 10%
false on a hand sample of 10). The sympy half has to be restarted from its checkpoints (`measure.py` skips finished pull
requests) before any ship-rule decision on the extras. The stale lock was left in place; it must be removed before that
restart, because the driver waits on it. This re-score ran without taking it (nothing else was running).
