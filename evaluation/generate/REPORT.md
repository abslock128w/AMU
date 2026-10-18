# Brief 4, Phase C: generated probes (C1) and hunk isolation with presentation (C2), 2026-10-17 and 18

Built on main through PRs #9 to #15 and released as v0.2.3 (C1) and v0.2.4 (C2). No agent trial.

## Summary

| Item | Check in the brief | Result |
|---|---|---|
| C1 change-directed probes | on 50 PRs from the counter: acceptance rate of the filter chain and cost per PR, beside DiffTestGen's 78% and $0.04 | **Unmeasured.** No Anthropic credentials: none on this machine (no `ANTHROPIC_API_KEY`, no `ant` CLI) and no `ANTHROPIC_API_KEY` repository secret as of 2026-10-18. The counter fields and the collector are in place; 0 probes recorded so far |
| C2 hunk isolation | name the hunk of a confirmed flip | Built; unit-tested (one cause, a pair, unresolved subsets, a cut-short search, an end-to-end flip). Not yet seen on a pull request with a flip |
| C2 presentation | informational status check, inline notes on the hunk, quiet mode | Built in the posting job (v0.2.4); not yet exercised on a pull request, because each pull request's comment is posted by main's workflow and v0.2.4 reached main only with PR #15 |
| C2 acted-on per row kind | the counter says which row kinds get acted on | Collector reports it for flips, behaviour differences and generated tests. On AMU's own 13 pull requests: 5 got flips, 0 cleared before merge (below) |

## C1: probes written by a model, kept on execution evidence (`amu/core/generate.py`)

**Targets.** These are the changed functions the tests never called on the code before the change: side-by-side
execution saw no input for them. Below Python 3.12, where side-by-side does not run, every comparable changed function
is a target.

**The request.** One request per five functions, with `claude-opus-5-5` by default (the `generate-model` input):
- the function as it was and as changed, plus one existing test from the project, for how its tests set things up;
- a JSON schema for the reply, effort `low`, and Anthropic's default server-side fallback on a refusal;
- the model returns probes: setup statements and one call expression each. It never writes an assertion or an
  expected value.

**The filter chain.** Each probe becomes a test in the project's own runner (pytest, unittest, or a Django
`TestCase` under `tests/amu_generated/`). That test records what the call returned or raised. A probe is kept only if
it passes every step:
1. it compiles;
2. its setup runs on the changed code;
3. it gives the same outcome five times there, each run in a fresh process;
4. it runs a changed line (the reach tracer);
5. on the code before the change it gives one outcome twice, and that outcome differs.

**What a kept probe reports.** It is reported as `generated test shows a difference`, with both outcomes. The test
shown in the comment, collapsed, asserts what executing the changed code produced.

**Caps.** Action inputs `generate-dollars` (default 0.10) and `generate-minutes` (default 20).
- Each request is priced from the response's own token counts, at the model's list price.
- A request that could cross the dollar cap is not made. Its `max_tokens` is cut to what the remaining dollars
  allow, and when that leaves under 1,500 tokens, nothing is sent and a gap says why.
- With the defaults on `claude-opus-5-5` ($4 / $20 per million tokens), one request of about 5,000 input tokens
  leaves room for about 4,000 output tokens. The cap allows about one request per pull request.

**Credentials and trust.**
- The key is an Action input, `anthropic-api-key`. When it is empty, nothing is generated and nothing is installed.
- The job that uses it runs the pull request's code. Forks never receive secrets, but a same-repository pull request
  could read the key, for example from its parent process's environment.
- That is no more than a collaborator can already do by editing a workflow in their pull request. The template ships
  the line commented out, with this note.
- The project's tests, the whole-suite recorders and the generated probes all run without any `ANTHROPIC_*` variable
  in their environment.

**Counters.** Each push records probes proposed, probes kept and dollars spent. `python -m amu.ci.counters` prints
`generated_acceptance` (kept over proposed, all pull requests) and `generated_cost_per_pr` (mean over pull requests
where generation ran).

**Tested.** `tests/core/test_generate.py` covers the filter chain with a fake model client:
- five probes proposed: one that differs, one that does not, one whose setup fails, one that does not compile, and
  one for a function not in the change (dropped);
- exactly one is kept, with outcomes taken from execution;
- the dollar cap stops a request before it is made;
- a missing client and a refusal each become a gap;
- the key does not reach the project's environment.

The test runs on 3.11 and 3.14 in CI.

**Not measured, and what the measurement needs.** The brief's check is the acceptance rate and cost per PR over 50
pull requests from the counter. With the repository secret in place, every AMU pull request will record them, and
the collector prints both. Until then there is no number to put beside DiffTestGen's 78% and $0.041. What the 50 PRs
would show also depends on how many of them change a function no test calls.

## C2: which hunk, and how it is shown (`amu/core/isolate.py`, `amu/ci/post.py`, `amu/ci/render.py`)

**Hunk isolation.**
- For up to three counted flips, Zeller's `ddmin` runs over the production hunks. Each trial writes a subset of them
  into a copy of the code before the change, with every test-file hunk applied, and reruns the flipped test once.
- A subset that does not run (a hunk that needs another to import, say) is unresolved, never a pass.
- A change with a single production hunk names it without running anything.
- Limits: 16 trials or 120 s per flip. This time is charged to the trust clock, not to the budget for finding flips.
- The flip's row reads, for example, "caused by hunk 2 of 3 in `lib.py` at line 13", or "narrowed to, search cut
  short" when a limit ends the search.
- `tests/core/test_isolate.py`: one cause found in at most eight trials out of eight hunks; a pair that only fails
  together; an unresolved subset kept rather than guessed; a search cut short; and an end-to-end flip naming hunk 2
  of 3 with its line on the head.

**The check.**
- The trusted posting job creates an `AMU` check run on the head: `neutral` when anything was found, `success` when
  nothing was, never `failure`.
- Its inline notes sit on the isolated hunks. Paths and lines come from the untrusted artifact, so only a file the
  pull request changes (from the pull request's file list) with a positive line range is kept.
- At most 50 notes, as plain text.
- The job needs `checks: write`, added to the posting workflow only; the job that runs pull request code still has
  read-only contents.
- If the check cannot be created, the comment is still posted.

**Quiet mode,** on by default (`AMU_QUIET` in the posting job). When nothing was found, the comment is its first line
plus a collapsed "What ran and what was not seen". Nothing found means:
- 0 flips among tests that ran;
- no runner abort, no change evidence, no behaviour difference, no generated difference;
- no flip labelled unstable or not caused.

**Acted on, per row kind.** For flips, behaviour differences and generated tests, the collector counts the pull
requests that got such rows and those where a later push, among tests that ran, had none and the pull request was
merged.

## Defects found on AMU's own pull requests during Phase C, each fixed with a test

| Found on | Defect | Fix |
|---|---|---|
| PR #9 | AMU's own check ran on Python 3.11, where B1 and B2 switch off, so no AMU PR showed them | the repository's check runs on 3.14 (PR #9) |
| PR #11 CI (3.11) | a C1 test assumed side-by-side execution, which needs 3.12 | the test states the fallback below 3.12 |
| PR #11 comment | the side-by-side hook and the trust tracer raised `KeyError: 'sitecustomize'` on interpreters with no `sitecustomize` of their own (GitHub's setup-python); Homebrew's Python ships one, which hid it locally | the hooks put themselves back in `sys.modules`; the test runs with `python -S` and fails on the old code (PR #12). The first version of that test passed on the broken code too and was rewritten |
| PR #12 | the repin pull request's job could not resolve `v0.2.3`, because the tag was pushed after the job started | push the tag before opening the repin pull request (done for v0.2.4) |
| PR #13 comment | side-by-side reported a difference on `shadow.run` that was the comparison's own: the as-changed run could not import `amu/core/isolate.py`, a module new in that pull request | modules the change adds are loaded from their changed source for the as-changed runs (PR #14) |

## AMU's own pull requests in Phase C

| PR | What it was | First line of AMU's comment | AMU job |
|---|---|---|---|
| #9 | workflow on Python 3.14 | no flip count: no candidate test ran (YAML only) | 6 s |
| #10 | deliberate break, closed | 1 flip, with call path `... > render > plural`; side-by-side difference on `plural(0, 'flip')`; held on the fresh-job rerun | 256 s |
| #11 | C1 | 2 flips (counter tests edited for the new fields: "fails as the test was before it was edited"); 5 of its own tests are change evidence | 219 s |
| #12 | sitecustomize fix, release 0.2.3 | 0 flips among 600 run; the new test is change evidence | 308 s |
| #13 | C2 | 2 flips (the same edited counter tests); 9 of its own tests are change evidence; 1 side-by-side difference, an artifact (fixed in #14) | 339 s |
| #14 | new-module fix | 0 flips among 118 run | 194 s |
| #15 | release 0.2.4 | 0 flips among 920 run | 307 s |

**Counter on 2026-10-18** (`python -m amu.ci.counters abslock128w/AMU`):
- 13 pull requests;
- 5 with a flip;
- 20 flips rerun on the merge base in a fresh job, 0 false (false-flip rate 0 of 20);
- 63 change-evidence rows;
- 0 generated probes.

**Cleared before merge: 0 of 5,** and that says nothing about reviewers yet:
- the flips came from a deliberate break (closed);
- or from tests edited for an intended change and merged as they were, which the comment labels "test edited, not
  fixed".

The behaviour-difference count per push exists only from v0.2.4 on, so earlier pull requests show none.

**Time:** the AMU job on AMU's own pull requests now takes 3 to 6 minutes for 52 to 920 tests. Most of that is AMU's
own suite run on the head (the candidate lists are long since selection by subtraction), plus the trust reruns and
the side-by-side run. It stays within the 300 s per-side budget and the 1,200 s wall cap; it has not been profiled.

## Files

- New: `amu/core/generate.py`, `amu/core/isolate.py`.
- Changed:
  - `amu/core/shadow.py`, `amu/core/__init__.py`, `amu/core/__main__.py`;
  - `amu/core/runners.py`, `amu/core/observe.py`, `amu/core/edges.py`: no `ANTHROPIC_*` in test environments;
  - `amu/core/sidebyside.py`, `amu/core/trust.py`;
  - `amu/ci/render.py`, `amu/ci/post.py`, `amu/ci/counters.py`, `amu/ci/run.py`;
  - `action.yml`, `ci/amu-pr.yml`, `ci/amu-pr-comment.yml`, and both workflows.
- Tests: `tests/core/test_generate.py` (3), `tests/core/test_isolate.py` (3), `tests/core/test_sidebyside.py` (2 added),
  `tests/ci/test_render_post.py` (2 added), plus updated counter and fork-safety tests.

AMU's core and CI tests at v0.2.4: 161 passed, plus the new-module test. Upstream code executed in Phase C: none
beyond AMU's own suite, locally and in CI.

## What is left

- **C1 measurement:** add the `ANTHROPIC_API_KEY` repository secret. Every AMU pull request then records probes
  proposed and kept and the cost; after 50, the collector gives the brief's two numbers.
- **First sight of the check and quiet mode:** the next pull request after v0.2.4.
- **Profiling the AMU job on AMU's own pull requests** (3 to 6 minutes), if that matters for adoption.
