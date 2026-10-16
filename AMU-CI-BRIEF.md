# AMU for pull requests: brief (2026-10-15, no code yet)

**A GitHub Action that runs bare `amu` on a pull request against its base. It posts one comment listing flips,
runner aborts and gaps, and edits that comment on every push. It is for human reviewers on repositories whose full
suite is slower than a review. Its measure is adoption, not a trial.**

## Scope

- **Trigger:** `pull_request` (opened, synchronize, reopened), with concurrency set to cancel an older run on the
  same PR.
- **Run:** check out the PR head with history and find the merge base with the base branch. Then run
  `python -m amu.core --repo . --patch-file <git diff merge-base...HEAD> --run`, with the runner detected or set as
  an input. Diffing against the merge base, not the base tip, keeps changes already on main out of the comment.
- **Comment:** one per PR, found by a hidden marker and edited in place. It names the head SHA it covers and then
  gives, in order:
  1. flips: tests that pass on the base and fail with the change, confirmed on the unchanged side, unstable ones
     marked;
  2. a runner abort: the suite starts on the base and not with the change;
  3. gaps: what was not run or not seen (budget reached, non-Python files, unresolved calls).
  Static candidates go in a collapsed section, or are left out.
- **Never a gate in v1.** The step always succeeds. Whether a flip should fail the check is decided after adoption
  data, not before.
- **Out of scope:** other CI systems, non-Python projects, auto-fixes, and anything aimed at agents.

## What exists already

- **Engine (`amu/core`, branch `accuracy/observed-tests`):**
  - shadow execution of up to 150 candidates, 300 with a coverage recording, on both sides of a supplied patch;
  - flips confirmed on the unchanged code, with an unstable rerun;
  - runner abort reported as a failure (22f91ef);
  - gaps listed rather than hidden;
  - runners: pytest, unittest, Django `runtests.py` and `manage.py`;
  - a per-side time budget;
  - `report_hash` for spotting identical reports;
  - an on-disk index (`.amu-index`) that can be cached between runs;
  - `--observe` for a per-test coverage recording;
  - the caller-walk bound that stopped the sympy hang (087e832).
- **Evidence it sees the right tests when it runs:** 17/17 on the Click development tasks. In the sympy trial its hook
  listed a frozen breaking test in 14 of 16 runs. Those are the same flips this comment would show a reviewer.
- **Cost seen so far:** cold index 11 s on Django and 46.5 s on sympy, 1 to 3 s warm. Then each side's test time up to
  the budget.
- **Not shown by any of it:** that a human acts on the output. Seven agent trials never turned flips into fewer shipped
  regressions. A reviewer is a different reader, and this is untested.

## What is missing

1. **Action wrapper** (`action.yml`, composite). It sets up Python and installs the project with its test
   dependencies (an `install` input, because the Action cannot guess these). It installs AMU from a pinned tag (AMU is
   not on PyPI), computes the diff, runs `amu` and saves the JSON as an artifact.
2. **Comment renderer:** JSON to Markdown, with a size cap, plus find-or-create-then-edit through the REST API. The
   comment should name its budget and what it skipped. It must not claim more than ran.
3. **Fork PRs.** A fork's `pull_request` token cannot comment. The safe pattern is two workflows: run untrusted code
   under `pull_request`, and post from a `workflow_run` job that only reads the saved JSON. PR code must never run
   under `pull_request_target`.
4. **Caching:** `actions/cache` for `.amu-index` and any recording, keyed on the merge-base SHA.
5. **CI defaults:** a larger budget than the hook's 60 s per side (proposed: 300 s), and a total wall-time cap.
6. **Environment fidelity:** a flip caused by the CI environment rather than the change. The rerun on the unchanged
   side catches most; the rest need a measured false-flip rate.
7. **Opt-in counters** for the measures below: counts and SHAs only, never source.

## How adoption would be measured

Not a trial: no arms, no hidden tests. The numbers below are proposals for the owner to set before the first install.

- **Who:** maintainers of public Python repositories whose default suite takes over 5 minutes, who are not the owner
  and installed it themselves. Target: 5 repositories within 4 weeks of release.
- **Retention (headline):** still installed and running after 8 weeks. Proposed bar: 3 of 5.
- **Acted on:** share of PRs with a flip where a later push, before merge or close, changes the flipped test or the
  code on its path. Measured from PR history, not from what people say.
- **Noise:** flips that maintainers mark wrong (a reaction or reply), and flips that the next push reverses with no
  related change. Proposed ceiling: 1 in 5.
- **Cost:** wall time the job adds per PR, as a median and a 90th percentile.
- **Exit:** uninstalls, each with a reason asked once.
- **Decision rule:** if retention is under the bar at 8 weeks, the CI line closes as the agent line did. If it is met,
  the next question is whether flips should gate a merge, and that gets its own brief.
