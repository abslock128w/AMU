# AMU session brief 5: first real measurements, all local (written 2026-10-18)

Start in `/Users/Abslock128/Documents/ChatGPT/AMU/observed-tests-cycle-2026-10-13` on `main`
(github.com/abslock128w/AMU, latest release v0.2.6). Read first, in this order, and do not
re-derive what they record: `AMU-PRODUCT-PLAN.md` sections 9 to 13; `AMU-NEXT-PROMPT-4.md`;
`evaluation/difference-finder/REPORT.md`; `evaluation/tracer/REPORT.md`;
`evaluation/generate/REPORT.md`; `evaluation/ci/REPORT.md`.

## Where this stands

Everything brief 4 asked for is on main and in v0.2.6. The counter on AMU's own repository:
13 PRs, 20 flips rerun in fresh jobs, 0 false. Two things have never run for real: the
generated-test tier, which has no model credentials, and the comment on any change that is
not AMU's own. This brief measures both on 100 real pull requests from two other projects,
entirely on this machine. No GitHub Action runs, no API key, no fork, no new engine code.

You are the generated-test tier. Where AMU reports a changed function with no captured call,
you write the probe, and AMU's execution filter decides whether it counts.

## Standing rules

- Everything runs locally in scratch copies under `/Users/Shared/amu-retro-2026-10-18/`,
  which survives a restart. Nothing under `/private/tmp`.
- Approved suites: Click, Werkzeug routing plus `test_urls.py` and `test_http.py`, Django,
  sympy. Ask before any other.
- Freeze the PR list by script, hash-pinned and committed, before running anything on it.
  Run once. Report numbers as measured; losses first.
- Never edit engine code while a measurement run is live. If a defect is found mid-run,
  record it, finish the run on the frozen engine, fix afterwards, and say which numbers
  predate the fix.
- No row without an evidence label. A probe counts only if it passes the five-step filter:
  runs, passes five times on its target tree, covers a changed line, differs across trees,
  assertions derived from execution and not from what you expect.
- Open a PR for every change to the repository; merge your own PRs with a merge commit once
  CI is green; delete your own branches. Ask before deleting anything else, changing remotes,
  or running an unapproved suite.
- AMU's own suite: `pytest tests scripts/test_before_tests_harness.py` with
  `/Users/Shared/amu-slow-suite-2026-10-15/slow/env/bin/python`; one known failure,
  `test_mcp_verification`, passes when the package is installed.
- Report after each piece: before and after table, files changed, tried and rejected, what
  executed upstream code.

## Piece 1: freeze 100 real pull requests

- Clone Django and sympy with full history into the scratch root. Use the GitHub API
  read-only, through `gh`, to list the last 50 merged PRs of each whose changes touch
  production Python. For each record PR number, title, merge base, head SHA, merge date,
  and the test files the PR itself adds or edits.
- Write `evaluation/retrospective/freeze.json`, sha256-pinned, and commit it through a PR
  before any analysis runs on any of them.

## Piece 2: run AMU on each as the Action would, locally

- For every frozen PR: worktree at the merge base and at the head, same budgets and filters
  the Action uses in v0.2.6, the base-side cache on, the tracer on, side-by-side on.
- Save the comment AMU would have posted as `evaluation/retrospective/<repo>/<pr>.md`, with
  its JSON beside it. Save per-PR timing.
- Where the comment lists changed functions with no captured call, do the generated-test
  step yourself: for each such function, write one probe test, run it through AMU's filter
  on both trees, keep it only if all five steps pass, and append the kept probes and their
  outcomes to that PR's comment under the same heading the automated tier would use. Record
  attempted, kept, and minutes spent per PR. Cap yourself at 20 minutes per PR.
- Do not run two PRs' suites at the same time; timings must be clean.

## Piece 3: the numbers

Report per repository, and these five first in the message to the owner:

1. false-flip rate: confirmed flips that a fresh-process rerun contradicted, over all
   confirmed flips;
2. PRs with at least one confirmed flip;
3. PRs with a side-by-side difference, with a hand label on 20 of them as intended by the PR
   or not, and precision on that sample;
4. share of changed functions that got no captured call;
5. median and worst job time.

Then: PRs that would have been quiet; PRs where the PR's own replayed tests gave change
evidence; probes attempted, kept, and minutes per PR; and a hand label on every kept probe as
showing an intended or an unintended difference.

Write `evaluation/retrospective/REPORT.md` with a pointer section in `evaluation/ci/REPORT.md`,
through a PR.

## Piece 4: decision rules, set now, before the numbers exist

Apply these in the report's last section and name the one piece of engine work each triggers.

- False-flip rate above 5 percent: the flip-trust layer is next, nothing else; name every
  flip that got through and why.
- Share of changed functions with no captured call above 50 percent and kept probes
  showing at least one unintended difference: the generated-test tier earns automation, and
  the `claude -p` subscription backend is the next build. Below 25 percent, or no unintended
  difference found by any probe: the tier stays off by default and is not automated.
- Side-by-side precision on the hand sample below 70 percent: side-by-side rows move to the
  collapsed section until fixed, and the false rows are listed with their cause.
- Worst job time above 15 minutes on either repository: profile that PR before anything else.
- Everything else: no engine work. What is next is a maintainer who is not the owner, and
  that is the owner's to find.

## Not in scope

Any agent trial. A learned ranker. Dataflow engines. Mutation, CrossHair, Hypothesis, PyRef.
The GitHub Action, forks, API keys, and any change to how the comment reads.
