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
