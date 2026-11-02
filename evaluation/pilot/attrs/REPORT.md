# AMU v0.5.1 on 15 attrs pull requests, live through the Action (2026-11-02)

Fifth codebase, a different shape: attrs is small and pure Python, its suite runs in seconds, and most of its code
builds classes at run time. No engine change and no new feature. Everything ran on the owner's fork
[abslock128w/attrs](https://github.com/abslock128w/attrs). Nothing was opened, commented or pushed on python-attrs.

## Losses first

- **Two noisy comments, four wrong first lines (11 of 15 correct).**
  - Fork #1 (attrs 1636) says no test ran the changed `astuple` line. Eight attrs tests run it; AMU did not pick them
    (defect 1).
  - Fork #12 (attrs 1509) reports 62 behaviour differences, and all 62 are false (defect 3).
  - Fork #2 (attrs 1625) calls a fix to a typo in a comment "adds new code only" (defect 2).
  - Fork #5 (attrs 1571) says "whether any ran the changed code was not seen" where "adds new code only" was knowable
    (defect 4).
- **Side-by-side made no real row on attrs' generated-class code.** On 12 of the 13 pull requests where it ran, it
  compared 818 captured calls and found 0 differences, and none was expected. Its only rows were the 62 false ones.
- **Selection costs coverage here and saves nothing.** AMU ran 10 to 658 of attrs' ~1,400 tests: 0.7 % to 47 %, median
  34 %. The whole suite takes 3.9 s with plain pytest on a laptop. The 300 s budget was never the limit: the most any
  side used was 97 s.
- **Useful on 2 of 15.** Both are bug fixes whose new tests AMU shows failing on the merge base: attrs 1593 and 1513.
  attrs 1509 also had 13 such tests, but its 62 false rows make it noise.

## Results

| Fork PR | attrs PR | AMU job | Open → comment | Posted | First line (shortened) | Correct | Adds | False rows | False flips | Ran / suite |
|---|---|---:|---:|---|---|---|---|---:|---:|---:|
| [#1](https://github.com/abslock128w/attrs/pull/1) | 1636 astuple nested atomics | 37 s | 62 s | yes | no test ran the changed code in `_funcs.py`; 10 tests run | **no** | **noise** | 0 | 0 | 10 / 1413 |
| [#2](https://github.com/abslock128w/attrs/pull/2) | 1625 comment typo | 34 s | 68 s | yes | adds new code only; no test of it ran | **no** | nothing | 0 | 0 | 468 / 1413 |
| [#3](https://github.com/abslock128w/attrs/pull/3) | 1606 evolve dunders | 64 s | 104 s | yes | no test ran the changed code in `_make.py`; 473 tests run | yes | nothing | 0 | 0 | 473 / 1413 |
| [#4](https://github.com/abslock128w/attrs/pull/4) | 1603 drop Python 3.9 | 132 s | 157 s | yes | 0 flips among 599, some on the changed code | yes | nothing | 0 | 0 | 599 / 1412 |
| [#5](https://github.com/abslock128w/attrs/pull/5) | 1571 `ne` validator | 55 s | 78 s | yes | no flip among 648; whether any ran the changed code was not seen | **no** | nothing | 0 | 0 | 648 / 1413 |
| [#6](https://github.com/abslock128w/attrs/pull/6) | 1592 generator on_setattr | 64 s | 98 s | yes | 0 flips among 658, some on the changed code | yes | nothing | 0 | 0 | 658 / 1408 |
| [#7](https://github.com/abslock128w/attrs/pull/7) | 1593 ClassVar forward refs | 55 s | 102 s | yes | 0 flips among 468; 1 own test fails on the merge base, passes on the head | yes | something | 0 | 0 | 468 / 1397 |
| [#8](https://github.com/abslock128w/attrs/pull/8) | 1600 defer imports | 59 s | 87 s | yes | 0 flips among 584, some on the changed code | yes | nothing | 0 | 0 | 584 / 1394 |
| [#9](https://github.com/abslock128w/attrs/pull/9) | 1541 converter decorator | 57 s | 84 s | yes | 0 flips among 473; 3 own tests fail on the merge base | yes | nothing | 0 | 0 | 473 / 1392 |
| [#10](https://github.com/abslock128w/attrs/pull/10) | 1529 fields() on instances | 68 s | 101 s | yes | 0 flips among 584; 2 own tests fail on the merge base | yes | nothing | 0 | 0 | 584 / 1387 |
| [#11](https://github.com/abslock128w/attrs/pull/11) | 1513 nested disabled() | 34 s | 70 s | yes | 0 flips among 101; 1 own test fails on the merge base | yes | something | 0 | 0 | 101 / 1384 |
| [#12](https://github.com/abslock128w/attrs/pull/12) | 1509 aliases before transformer | 92 s | 126 s | yes | 0 flips among 484; 13 own tests fail on the merge base; 62 behaviour differences | **no** | **noise** | **62** | 0 | 484 / 1383 |
| [#13](https://github.com/abslock128w/attrs/pull/13) | 1507 update dev | 71 s | 116 s | yes | 0 flips among 467, some on the changed code | yes | nothing | 0 | 0 | 467 / 1372 |
| [#14](https://github.com/abslock128w/attrs/pull/14) | 1454 ClassProps | 104 s | 134 s | yes | 0 flips among 595, some on the changed code | yes | nothing | 0 | 0 | 595 / 1372 |
| [#15](https://github.com/abslock128w/attrs/pull/15) | 1469 astuple common case | 43 s | 72 s | yes | 0 flips among 63, some on the changed code | yes | nothing | 0 | 0 | 63 / 1366 |

Totals:

| | attrs (15) | v0.4.2 set (47) |
|---|---:|---:|
| Comment posted | 15 of 15 | n/a (local) |
| First line correct | 11 | 42 (after v0.5.x) |
| Adds something a reviewer would want | 2 | 19 |
| Adds nothing | 11 | |
| Adds noise | 2 | 2 |
| Counted flips | 0 | 0 |
| False flips | 0 (verify job never triggered) | 0 |
| Behaviour rows: all / real | 62 / 0 | |

- **"Ran / suite":** tests AMU ran on the head, against tests pytest collects at that head. The collection was done
  locally on Python 3.14. That differs from the job's 3.12 only in which tests skip, not in how many are collected.
- **Full comments:** `comments/<attrs PR>.md`.
- **Per-pull-request numbers:** `data.json`.
- **Hand labels:** `labels.json`, same rubric as `evaluation/performance-v042/labels.json`.
- **Report, meta and patch per pull request:** `reports.tar.xz`. The workflow artifacts expire after 14 days.

## Job time and cost

- **AMU job:** 34 to 132 s, median 59 s. That includes checkout, `pip install`, indexing and both sides.
- **Posting job:** 13 to 19 s.
- **Wait for a runner:** 3 to 23 s. All 15 pull requests were opened within 40 s of each other.
- **Pull request opened → comment:** 62 to 157 s, median 98 s. On the sympy fork it was 8.6 to 27.1 minutes.
- **Cost per pull request:** 47 to 151 s of runner time, both jobs together.
  - Billed, with each job rounded up to a minute: 2 to 4 minutes, 38 minutes for all 15.
  - On a public repository those minutes are free. On a private one they count against the plan's Linux minutes.
- **Cache:** every job started cold, because the 15 merge bases were all different. The index took about 1 s to build
  (`seconds_before_running` 0.96 on #1), so the cache does not matter at this size.
- **Where AMU's time goes:** the test runs themselves.
  - AMU ran 599 tests in 96.9 s on attrs 1603. Plain pytest runs all 1,413 in 3.9 s.
  - So AMU's way of running a test list costs more than the whole suite would.
  - This is recorded only, not investigated: the job machine and the laptop differ, and the tests were run in
    AMU's fixed order.

## Budget coverage

- **The budget was never the limit.** The most test time used on one side was 96.9 s, out of 300 s.
- **AMU ran only its candidate tests:** 10 to 658 per pull request, 0.7 % to 47 % of the suite, median 34 %.
  - Tests outside the candidates were not run. Every comment says so.
  - On attrs this costs more than it saves. With the whole suite run, attrs 1636 would have run the 8 tests that
    reach the changed line, and its first line would have been right.

## Side-by-side on generated-class code

**Answer: it runs and agrees, but it produced no real row.**
- Side-by-side ran on 13 of 15 pull requests. It compared 906 captured calls of changed functions: attrs 1603 alone had
  533 calls of 5 functions, and 1600 had 111 calls of 8.
- On 12 of those pull requests it found 0 differences. That is right: none of them meant to change what a function
  returns.
- On attrs 1509 it reported 62 differences, all false:
  - The pull request adds a field to the slotted `Attribute` class.
  - AMU runs the changed methods on objects built by the old class, which has no slot for the new field (defect 3).
- attrs builds `__init__`, `__eq__`, `__repr__` and similar methods with `exec`. Those generated methods have no
  source lines in the diff, so side-by-side never targets them. Its targets are the builder functions in `_make.py`.

## Defects (recorded, not fixed)

1. **Candidate selection misses the tests that call the changed function through the package.** Seen on fork #1
   (attrs 1636).
   - **What happened:** `tests/test_funcs.py::TestAsTuple` calls `astuple` 20 times, imported as
     `from attr import astuple`. `src/attr/__init__.py` re-exports it with `from ._funcs import astuple`. AMU's 10
     candidates reach `astuple` only through `VersionInfo._ensure_tuple`, and none of them is in `TestAsTuple`.
   - **The first line is wrong:** it reads "no test ran the changed code".
   - **Reproduction:**
     1. Check out attrs at `ce52bb2`.
     2. Make line 304 of `src/attr/_funcs.py` raise.
     3. Run `pytest tests`: 8 `TestAsTuple` tests fail.
     4. The AMU report for fork #1 (`reports.tar.xz`, `1636/report.json`, key `tests`) lists none of them.
   - **Likely cause, not confirmed:** imports through a package `__init__` re-export in a `src/` layout are not
     resolved. The report says "1518 call sites unresolved repo-wide".
   - **Also seen on attrs 1469 (same function):** only the 4 `TestAsTuple` tests that pull request edits were
     candidates.
2. **A comment-only edit is called "adds new code only".** Seen on fork #2 (attrs 1625).
   - **What happened:** the diff fixes one comment line inside `Attribute.evolve`. The report has `adds_only: true` and
     `changed_functions: [Attribute.evolve]`.
   - **Why it shows:** `amu/ci/render.py` checks `adds_only` before `docs_only`, so the "docstring or comment changes
     only" line from #83 never shows.
   - **Reproduction:** `1625/report.json` in `reports.tar.xz`. Render it with `python -m amu.ci.render`.
3. **Side-by-side false rows on a slotted class that gains a slot.** Seen on fork #12 (attrs 1509).
   - **What happened:** the pull request adds `alias_is_default` to `Attribute.__slots__` and sets it in `__init__`,
     `from_counting_attr`, `evolve` and `_transform_attrs`. Run as changed on `Attribute` objects from the old class,
     those raise `AttributeError: 'Attribute' object has no attribute 'alias_is_default'`, or `TypeError`.
   - **The result:** 62 rows, all false.
   - **Why #78 did not help:** `input_built_before_the_change` is 0, so #78's set-aside for "failures on objects the
     old code built" did not fire.
   - **Reproduction:** `1509/report.json`, key `behaviour_differences`: 32 `TypeError` from `from_counting_attr`, 21
     `AttributeError` from `_transform_attrs`, 5 from `__init__`, 4 from `evolve`.
4. **Docstring edits next to a new function block "adds new code only".** Seen on fork #5 (attrs 1571).
   - **What happened:** the pull request adds `validators.ne` and edits docstrings in 12 existing validators. The first
     line falls through to "whether any ran the changed code was not seen".
   - **Why:** `adds_only` is false because existing functions changed, although each one changed only in its docstring.
   - **Reproduction:** `1571/report.json`.
5. **Unrunnable candidates are not named.** Seen on fork #5, #6, #10 and #14.
   - **What happened:** 15, 17, 41 and 15 candidates "could not be run by pytest". Neither the report nor the job log
     says which ones or why.
   - **On fork #10** a gap also reads "shadow run (after the change, pytest): no outcomes read: 1 error in 0.07s",
     with no further detail.
   - **Reproduction:** `1529/report.json`, keys `executed.unrunnable` and `gaps`.
6. **Code that only runs on another Python version is not called out.** Seen on fork #3 (attrs 1606) and #13
   (attrs 1507).
   - **What happened:** `add_replace` runs only on Python 3.13+, and the Action ran 3.12. On fork #3, "no test ran the
     changed code" is literally right, but the comment does not say the version is why.
   - **What it hid:** attrs 1507 is the pull request that started mutating `evolve`'s dunders, which 1606 later fixed. A
     3.12 job could not see it, and its comment says "some of them on the changed code" because other changed
     functions ran.

## Setup and install note

- **Install note:** followed as written, pinned to `v0.5.1`, both files on `main` and on each base branch. The Action
  ran on the first try on all 15, so there was no install-note error to fix.
- **Install line for attrs:** attrs keeps its test dependencies in `[dependency-groups]`, so the line is
  `python -m pip install -U pip && python -m pip install -e . --group tests`. `--group` needs pip 25.1 or later.
- **Suggested for the note, not changed here:** a sentence on `python-version:` for projects whose code branches on
  the Python version (defect 6), and the `--group` form of the install line.
- **The fork ran attrs' own workflows:**
  - Actions were already on for the fork, so the first push to `main` started attrs' CI, Zizmor and PyPI-package
    workflows. I cancelled them.
  - I then disabled attrs' 6 workflows on the fork, so only AMU's two ran. This is unlike the sympy fork, where the
    disable was refused. Runner waits here are therefore AMU's alone.
- **Replays:**
  - Each fork pull request's base is a branch `amu-base-<merge base>`: the upstream merge base plus one commit adding
    the two workflow files.
  - Its head is `amu-pr-<n>`, the upstream pull request's own head commit.
  - No two of the 15 shared a merge base.
  - Bodies name the upstream pull request as "upstream attrs pull request N", with no `#` and no link.
  - The pull requests are left open on the fork for reading.
- **Choice of pull requests:** the 15 most recent merged into attrs `main` that change a `.py` file under `src/attr/`
  or `src/attrs/`, from 1636 (2026-10-13) back to 1469 (2025-09-08).
  - Pull requests that changed none were skipped: dependabot, docs, CI, lockfile, and 1649 (`.pyi` only).
  - 1625 (a comment typo) and 1507 (`update dev`) change `src/attr/_make.py`, so they count.
- **Runs on this machine:** with the owner's approval for attrs, I collected the suite at each head to count it, and
  ran it a few times at `ce52bb2`: under coverage, and with line 304 made to raise (defect 1). Nothing AMU does was
  run locally.

## Verdict

**attrs is not a fit for a maintainer ask.** AMU is pitched at slow suites. attrs' suite runs 1,400 tests in seconds,
and its CI already runs all of them on every Python it supports. That leaves AMU nothing to save and two ways to add
noise:
- **Selection:** it ran a third of the suite and missed the 8 tests that mattered on attrs 1636.
- **Side-by-side:** attrs' slotted, generated classes are where side-by-side is weakest. It produced 62 false rows on
  1509 and no real ones.

It added something on 2 of 15 pull requests, both by showing that a bug fix's new test fails on the merge base. On attrs
that is a few lines anyone can check in seconds.

**What this pilot does show:** the Action installs and posts correctly on a fresh fork with no help. A comment arrives
in under 3 minutes, at 2 to 4 billed minutes per pull request. It counted no false flips.

**For attrs-shaped projects, AMU would need two changes first:**
- run the whole suite on both sides when it fits the budget;
- set aside side-by-side failures caused by a slot or field the old class lacks.

Both are engine changes, outside this pilot.
