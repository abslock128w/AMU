# AMU session brief 5: first real measurements (written 2026-10-18)

Start in `/Users/Abslock128/Documents/ChatGPT/AMU/observed-tests-cycle-2026-10-13` on `main`
(github.com/abslock128w/AMU, latest release v0.2.6, PyPI name `anti-messup`). Read first, in
this order, and do not re-derive what they record: `AMU-PRODUCT-PLAN.md` sections 9 to 13;
`AMU-NEXT-PROMPT-4.md`; `evaluation/difference-finder/REPORT.md`; `evaluation/tracer/REPORT.md`;
`evaluation/generate/REPORT.md`; `evaluation/ci/REPORT.md`; `evaluation/ci/INSTALL-NOTE.md`.

## Where this stands

Everything brief 4 asked for is on main and in v0.2.6: replay of the PR's own tests, the
flip-trust layer, selection by subtraction, the base-side cache, the `sys.monitoring` tracer,
side-by-side execution of changed functions on captured inputs, execution-gated generated
tests, hunk isolation with inline notes, the informational check, quiet mode, per-kind
counters. The counter on AMU's own repository: 13 PRs, 20 flips rerun in fresh jobs, 0 false.

Two things have never run for real. The generated-test tier has no model credentials. The
comment has never been produced for a repository other than AMU's own. This brief fixes both
with no new engine work beyond one backend, and ends with the first numbers measured on real
pull requests from other projects.

## Standing rules

- Open a PR for every change; merge your own PRs with a merge commit once CI is green; cut
  tags and releases as needed; delete your own branches after merge or close.
- Ask before: deleting anything that is not your own branch or worktree, changing remotes,
  running a suite not on the approved list. Approved: Click, Werkzeug routing plus
  `test_urls.py` and `test_http.py`, Django, sympy, all in scratch copies.
- Freeze every set of commits or PRs by script, hash-pinned and committed, before running
  anything on it. Run once. Report numbers as measured; losses first.
- No row without an evidence label. The comment never claims more than ran.
- AMU's own suite: `pytest tests scripts/test_before_tests_harness.py` with
  `/Users/Shared/amu-slow-suite-2026-10-15/slow/env/bin/python`; one known failure,
  `test_mcp_verification`, passes when the package is installed.
- Never edit engine code while a measurement run is live.
- Report after each piece: before and after table, files changed, tried and rejected, what
  executed upstream code.

## Piece 1: a subscription backend for the generated-test tier

The owner has no API credits and a Max subscription. Claude Code's headless mode runs on the
subscription.

- Add a second backend to `amu/generate`: shell out to `claude -p` with JSON output, same
  prompt, same five-step execution filter, same minute cap. Select it when no
  `ANTHROPIC_API_KEY` is set and either the `claude` command is on PATH or
  `CLAUDE_CODE_OAUTH_TOKEN` is set. The dollar cap does not apply to this backend; the
  comment's "what ran" line says which backend ran and that cost is plan usage.
- In `action.yml`, an optional input that installs Claude Code on the runner and passes
  `CLAUDE_CODE_OAUTH_TOKEN` through from a repository secret of that name.
- Unit test the backend with a fake `claude` executable on PATH: a well-formed response, a
  malformed one, a non-zero exit, and a timeout. Do not call the real CLI in tests.
- Release as v0.3.0 through a release PR.
- Then stop and tell the owner the two things only they can do: run `claude auth login` on
  this Mac, and run `claude setup-token` and add its output as the `CLAUDE_CODE_OAUTH_TOKEN`
  repository secret. Do not run the tier against anything real until the owner confirms both.

## Piece 2: retrospective on 100 real pull requests

- Freeze by script: the last 50 merged PRs of Django and the last 50 of sympy whose changes
  touch production Python. Record PR number, merge base, head SHA, merge date. Commit the
  freeze before running anything.
- For each PR, run AMU exactly as the Action does: merge base against head, the Action's
  budgets and filters, v0.3.0, in a scratch copy, with the generated-test tier on only if
  the owner has confirmed the CLI login, otherwise off and recorded as off. Save the comment
  it would have posted under `evaluation/retrospective/<repo>/<pr>.md`.
- Report per repository, and first in the message to the owner, these five numbers:
  1. false-flip rate: flips that a fresh-job rerun contradicted, over all confirmed flips;
  2. PRs with at least one confirmed flip;
  3. PRs with a side-by-side difference, with a hand label on 20 of them as intended by the
     PR or not, and precision on that sample;
  4. share of changed functions that got no captured call;
  5. median and worst job time.
  Then: PRs that would have been quiet; PRs where the PR's own replayed tests gave change
  evidence; if the tier ran, probes attempted, kept by the filter chain, and minutes per PR.
- Write it in `evaluation/retrospective/REPORT.md` with a pointer section in
  `evaluation/ci/REPORT.md`.

## Piece 3: delivery to a repository that is not AMU's own

- Fork sympy into abslock128w. Install AMU on the fork following `INSTALL-NOTE.md` word for
  word, as an outside maintainer would, reading nothing else. Every place the note is wrong,
  missing or ambiguous goes in a list, and the note is fixed afterwards.
- Replay ten of the frozen sympy PRs as PRs into the fork, from branches cut at each merge
  base, two of them sharing a merge base.
- Report: whether every comment posted; whether the base-side cache hit on the second PR
  against the shared base; job time per PR; every difference between the Action's comment
  and the local retrospective comment for the same PR. Close the PRs, delete their branches,
  keep the fork.

## Piece 4: what the numbers decide

Write one section at the end of `evaluation/retrospective/REPORT.md` that applies these
rules, set now, before the numbers exist:

- False-flip rate above 5 percent: the flip-trust layer is the next engine work, nothing
  else, and the report names the flips that got through and why.
- Share of changed functions with no captured call above 50 percent: the generated-test tier
  is worth its plan usage, and its filter-chain acceptance on the retrospective is the next
  number to improve. Below 25 percent: the tier stays off by default.
- Side-by-side precision on the hand sample below 70 percent: side-by-side rows leave the
  default comment and go into the collapsed section until fixed.
- Worst job time above 15 minutes on either repository: profile that PR before anything else.
- Everything else: no engine work. The next thing is external maintainers, which is the
  owner's.

## Not in scope

Any agent trial. A learned ranker. Dataflow engines. Mutation, CrossHair, Hypothesis, PyRef,
which wait on piece 4's rules. Any change to how the comment reads, unless piece 3 shows the
install note or the comment is wrong on the fork.
