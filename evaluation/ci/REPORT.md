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
