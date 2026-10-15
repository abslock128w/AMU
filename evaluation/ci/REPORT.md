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
