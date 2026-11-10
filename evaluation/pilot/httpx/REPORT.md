# AMU v0.6.0 on 15 httpx pull requests, live through the Action (2026-11-02)

The sixth codebase, and the first one untuned: v0.6.0 was released before httpx was looked at, and no engine change
was made while it ran. Everything ran on the owner's fork [abslock128w/httpx](https://github.com/abslock128w/httpx).
Nothing was opened, commented or pushed on encode/httpx.

## Losses first

- **Static gaps shown even after the whole suite ran.**
  - On all 15 comments a gap says "2,3xx call sites unresolved repo-wide". On httpx 3401 another says "No existing
    test reaches the change within five source hops".
  - Both are static-graph statements. These runs selected nothing statically: the whole suite ran on both sides, and
    the hunks are recorded as run. On 3401 the gap contradicts the hunk list (defect 1).
- **A hang in the measurement, not in AMU.**
  - The raise measure's first try hung on httpx 3699: a raise inside the test server's code left a client waiting.
  - It was rerun with a 60 s per-test timeout. A test that times out that way does not count as a catcher, so the
    catcher counts are a lower bound.
- **The job is slower than a selective one would be.** The whole suite of about 540 test functions runs on both sides,
  so the AMU job took 50–109 s. That is still small next to httpx's own CI.
- **Adds something on 3 of 15 only.** For most of these pull requests the comment confirms, as a green CI run would,
  that every changed hunk ran and nothing flipped.

## Results

Labels: rubric v2 (rubric in `evaluation/PROTOCOL.md`).

| Fork PR | httpx PR | AMU job | Open → comment | Posted | First line | Correct | Adds | False rows | Raise catchers missed | Suite run |
|---|---|---:|---:|---|---|---|---|---:|---:|---:|
| [#1](https://github.com/abslock128w/httpx/pull/1) | 3699 Expose `FunctionAuth` in `__all__` | 50 s | 77 s | yes | AMU: 0 flips at `4b9f63e`, ran all 539 tests; every changed hunk ran on both sides (at import) | yes | nothing | 0 | 0 of 0 | all 539 |
| [#2](https://github.com/abslock128w/httpx/pull/2) | 3651 Upgrade Python formatter ruff | 66 s | 93 s | yes | AMU: docstring or comment changes only at `927b5ac`; no flip, ran all 539 tests | yes | nothing | 0 | 0 of 6 | all 539 |
| [#3](https://github.com/abslock128w/httpx/pull/3) | 3571 Display proxy protocol scheme on error | 59 s | 87 s | yes | AMU: 0 flips at `f7a257f`, ran all 539 tests; 1 of 1 changed hunk ran in no test: `httpx/_transports/default.… | yes | something | 0 | 0 of 22 | all 539 |
| [#4](https://github.com/abslock128w/httpx/pull/4) | 3519 fix typo | 56 s | 84 s | yes | AMU: 0 flips at `ac3408f`, ran all 539 tests; every changed hunk ran on both sides, in up to 1 test; 1 behavi… | yes | nothing | 0 | 0 of 65 | all 539 |
| [#5](https://github.com/abslock128w/httpx/pull/5) | 3445 Version 0.28.1 | 69 s | 94 s | yes | AMU: 0 flips at `c2a5f81`, ran all 539 tests; every changed hunk ran on both sides (at import) | yes | nothing | 0 | 0 of 0 | all 539 |
| [#6](https://github.com/abslock128w/httpx/pull/6) | 3442 Fix `verify=False`, `cert=...` case. | 89 s | 116 s | yes | AMU: 0 flips at `9d5e734`, ran all 539 tests; every changed hunk ran on both sides, in up to 1 test | yes | nothing | 0 | 0 of 1 | all 539 |
| [#7](https://github.com/abslock128w/httpx/pull/7) | 3426 Updating deprecated docstring Client() … | 62 s | 88 s | yes | AMU: docstring or comment changes only at `3bf53fa`; no flip, ran all 538 tests | yes | nothing | 0 | 0 of 0 | all 538 |
| [#8](https://github.com/abslock128w/httpx/pull/8) | 3419 Version 0.28.0. | 87 s | 112 s | yes | AMU: 0 flips at `5f8107b`, ran all 538 tests; every changed hunk ran on both sides, in up to 150 tests | yes | nothing | 0 | 0 of 199 | all 538 |
| [#9](https://github.com/abslock128w/httpx/pull/9) | 3380 Fix `extensions` type annotation. | 69 s | 92 s | yes | AMU: 0 flips at `fdd2e91`, ran all 538 tests; every changed hunk ran on both sides, in up to 289 tests | yes | nothing | 0 | 0 of 334 | all 538 |
| [#10](https://github.com/abslock128w/httpx/pull/10) | 3418 Error on verify as str. | 62 s | 86 s | yes | AMU: 0 flips at `8a40ebc`, ran all 538 tests; every changed hunk ran on both sides, in up to 1 test | yes | nothing | 0 | 0 of 1 | all 538 |
| [#11](https://github.com/abslock128w/httpx/pull/11) | 3412 Handle empty zstd responses | 73 s | 104 s | yes | AMU: 0 flips at `3b9b2b3`, ran all 538 tests; every changed hunk ran on both sides, in up to 3 tests; 1 test … | yes | something | 0 | 0 of 5 | all 538 |
| [#12](https://github.com/abslock128w/httpx/pull/12) | 3389 Move utility functions from _utils.py t… | 63 s | 90 s | yes | AMU: 0 flips at `433684f`, ran all 536 tests; every changed hunk ran on both sides, in up to 113 tests | yes | nothing | 0 | 0 of 35 | all 536 |
| [#13](https://github.com/abslock128w/httpx/pull/13) | 3401 Import ssl on demand | 61 s | 86 s | yes | AMU: 0 flips at `9acb535`, ran all 536 tests; every changed hunk ran on both sides (at import) | yes | nothing | 0 | 0 of 0 | all 536 |
| [#14](https://github.com/abslock128w/httpx/pull/14) | 3394 Graceful upgrade path for 0.28. | 109 s | 155 s | yes | AMU: 0 flips at `0f9cd3f`, ran all 536 tests; 1 of 63 changed hunks ran only in the pull request's own tests:… | yes | something | 0 | 0 of 199 | all 536 |
| [#15](https://github.com/abslock128w/httpx/pull/15) | 3387 Move remaining utility functions from _… | 70 s | 91 s | yes | AMU: 0 flips at `81bb8d1`, ran all 538 tests; every changed hunk ran on both sides, in up to 57 tests | yes | nothing | 0 | 0 of 66 | all 538 |

Labels: rubric v2 (rubric in `evaluation/PROTOCOL.md`).

| | httpx (15) |
|---|---:|
| Comment posted | 15 of 15 |
| First line correct | 15 |
| Adds something a reviewer would want | 3 (3571, 3412, 3394) |
| Adds nothing | 12 |
| Adds noise | 0 |
| Counted flips | 0 |
| Behaviour rows: all / false | 1 / 0 (3519: a typo fixed in an error message, real) |
| Tests that would catch a raise, missed | 0 of 933 |
| Suite run | 100 %: the whole suite on both sides on every pull request (536–539 test functions) |
| AMU job | 50–109 s, median 66 s |
| Pull request opened → comment | 77–155 s, median 91 s |

- **Install:** `install: python -m pip install -r requirements.txt`, from the install note, both lines. Python 3.12,
  the default.
- **httpx's own workflows:** the first push to the fork's `master` started its Test Suite. That run was cancelled, and
  Test Suite and Publish were disabled on the fork, so only AMU's workflows ran.
- **Replays:**
  - Each fork pull request's base is `amu6-base-<merge base>`: the upstream merge base plus one commit adding the
    v0.6.0 workflows.
  - Its head is `amu-pr-<n>`, the upstream pull request's own head.
  - The 15 are the most recent merged pull requests that change a `.py` file under `httpx/`, from 3699 back to 3387.
- **Tests that would catch a raise:**
  - The first changed statement of each hunk on the head was made to raise, only while a test runs. The whole suite
    then ran locally on Python 3.14, and a test failing with the marker counts as a catcher
    (`evaluation/generalize/raise_missed.py`).
  - This was compared with each live comment's candidates. Every candidate set was the whole suite, so 0 missed
    follows from it, and the measure confirms it.
- **Labels:** `labels.json`, same rubric as `evaluation/performance-v042/labels.json`.
- **Files:** `data.json`, `comments/` and `reports.tar.xz` (report, meta, patch and raise result per pull request).

## Defects (recorded, not fixed)

1. **Static-graph gaps after a whole-suite run.**
   - **What happened:** "N call sites unresolved repo-wide" appears on every comment. On 3401, "No existing test reaches
     the change within five source hops" is printed beside hunks that `hunks` records as run at import. Both come from
     the static graph, which this run did not use to select.
   - **Expected:** when the selection mode is the whole suite, or every hunk has a recorded state, these gaps should not
     be shown.
   - **Reproduction:** `3401/report.json` in `reports.tar.xz`, keys `gaps` and `hunks`.
2. **"at import" for hunks in function signatures.**
   - **What happened:** on 3419 and 3394, hunks that remove a parameter from a function's signature read "ran on both
     sides (at import)". A signature line runs when the module defines the function, but the change matters at every
     call, and the comment does not say whether a test called the function.
   - **Reproduction:** `3419/report.json`, the hunks at `httpx/_api.py` with `why: at import`.
3. **Annotation lines counted as code.**
   - **What happened:** changing a type annotation is reported by where it runs, never as no code change. On these 15
     every such hunk sat beside a code change, so no first line was wrong, but an annotation-only change would read
     "ran on both sides" rather than "docstring or comment changes only".
   - **Reproduction:** `3380/report.json`, the hunks in `httpx/_types.py`.

## Verdict

**v0.6.0 held on an untuned repository.**
- 15 of 15 comments posted, and 15 of 15 first lines were right.
- 0 false behaviour rows and 0 noise.
- Every pull request ran the whole suite, and no test that would catch a raise of the changed code was missed.

That is the opposite of the attrs pilot at v0.5.1, where 4 first lines were wrong, 62 rows were false and the
selection missed the tests that mattered.

The caveat is that httpx, like attrs, has a suite that fits the budget. These results show the whole-suite path
holding; they say nothing about the coverage path a large suite needs. On Django and sympy (`evaluation/generalize/`)
the whole suite did not fit and selection fell back to the static graph.

What AMU adds on httpx is modest: 3 useful comments out of 15, each from change evidence or an unrun hunk the diff
does not show.

## Re-run at v0.7.0 (2026-11-03): the same 15 pull requests, live on the fork

The Action was pinned to v0.7.0 on abslock128w/httpx. The same 15 heads were opened as fork pull requests #16–#30
against new base branches (the merge base plus the v0.7.0 workflows). Python 3.12. No engine code changed during the
run. Files are in `v070/`: `data.json`, `labels.json`, `comments/` and `reports.tar.xz`.

### Losses first

- **Pull request opened → comment is slower: 78–234 s, median 217 s (v0.6.0: 77–155 s, median 91 s).** The AMU job
  itself is about the same, 45–155 s with a median of 61 s (v0.6.0: 50–109 s, median 66 s). The difference is runner
  queueing: the 15 were opened together while the jinja fork's 15 were running.
- **Still useful on 3 of 15 only** (3571, 3412, 3394). For the rest the comment confirms what a green CI run would.
- **The raise measure was not re-run.** The whole suite ran on both sides on every pull request. The v0.6.0 measure
  (0 of 933 missed) stands.

### What changed

Labels: rubric v0.6.0: v2; v0.7.0: v3 (rubric in `evaluation/PROTOCOL.md`).

| | v0.6.0 | v0.7.0 |
|---|---:|---:|
| First line correct | 15 | 15 |
| Adds something / nothing / noise | 3 / 12 / 0 | 3 / 12 / 0 |
| Behaviour rows: real / false | 1 / 0 | 1 / 0 (3519; 3 fields excluded as differing between the two merge base runs) |
| Suite run | 100 % | 100 % (536–539 tests) |

The three httpx defects are fixed:

- **Defect 1: static gaps after a whole-suite run.** These are gone on all 15. On 3401 the "No existing test reaches
  the change" gap no longer contradicts the hunk list.
- **Defect 2: signature-only hunks read "(at import)".** On 3419 and 3394 they now count the tests that called the
  function: "in up to 150 tests".
- **Defect 3: annotation lines counted as code.** v0.7.0 has an `annotations-only` line state. None of these 15 shows
  it: the `httpx/_types.py` hunks on 3380 are module-level alias assignments (`RequestExtensions = Mapping[str, Any]`),
  which run at import, so "at import" is right there. On jinja, two annotation-only hunks of 2096 still read as code,
  through a different defect (`evaluation/pilot/jinja/REPORT.md`, defect 3).

3394's first line now leads with the new `URL.raw` property (`pragma: nocover`) that no test runs, which is true.
