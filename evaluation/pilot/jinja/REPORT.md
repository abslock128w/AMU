# AMU v0.7.0 on jinja (seventh repository, untuned), 2026-11-03

The 15 most recent merged pull requests of pallets/jinja that change Python under `src/jinja2/` were replayed on
the fork abslock128w/jinja. Each base is the upstream merge base plus one commit adding the AMU workflows, and each
head is the pull request's own head commit. They ran through the live Action, pinned to v0.7.0, on GitHub's
`ubuntu-latest` runners with Python 3.12. Nothing was opened, commented or run on pallets/jinja itself. The install
line is `python -m pip install -e . pytest trio`. No engine code changed between the replay and this report: the
defects below are recorded, not fixed.

Files: `labels.json` (hand labels and per pull request numbers), `data.json` (the live timings), `comments/` (the
posted comments), `reports.tar.xz` (each job's report.json and meta.json, and the raise measure).

## Losses

- **Jinja 2099: 400 of 679 tests got no outcome, and the first line still says "ran all 279 tests".**
  - What happened: on the job, the first chunk of 400 tests gave no outcome on either side. The runner's output
    ended in `AttributeError: 'NoneType' object has no attribute 'get'`. The comment lists this as a gap.
  - Cause not established: the same run locally, on Python 3.14, gave outcomes for 669 of 679.
  - The first line counts the whole suite as run even though most of it had no outcome (defect 1).
- **Jinja 1664: 121 false behaviour rows.**
  - All are on `Parser.parse_tuple.<locals>.parse`, a nested function without arguments.
  - Its input digest is empty: the closure's free variables are not part of the input. So calls with different
    closure state pair as equal inputs, and each side's n-th call is compared with the other's (defect 2).
  - The first line announces them.
- **A comment or docstring change can read as code.** Within one diff hunk, changes separated by fewer than four
  unchanged lines form one block. Re-applying such a block to check for "not code" ignores the unchanged lines
  inside it, so the result is wrong (defect 3).
  - Jinja 2099: a moved comment reads "ran in no test".
  - Jinja 2096: two annotation-only hunks read as code, so "3 of 251 changed hunks ran in no test" names type-only
    lines. That is true, but it is not what the line should say.
  - The same defect also hits Flask 5812 in the 47 set; see `evaluation/generalize/REPORT-v070.md`.
- **A script outside the package counts as production code.** In jinja 2099,
  `scripts/generate_identifier_pattern.py` is listed as a hunk that ran in no test (defect 4).
- **The raise measure could not inject on 2096 and 2093.** The harness placed its `raise` inside a multi-line
  expression. Both ran the whole suite, so no test was left outside the candidates.

## Numbers

| | jinja, v0.7.0 |
|---|---:|
| Comments posted | 15 of 15 |
| First line right | 13 of 15 (2099, 1664 wrong) |
| Adds something / nothing / noise | 6 / 7 / 2 |
| Behaviour rows: real / false | 3 / 121 |
| Whole suite run on both sides | 15 of 15 by mode; 9,677 of 10,131 test outcomes (95.5 %), 2099 at 279 of 679 |
| Tests that would catch a raise, missed | 0 of 665 (13 measured) |
| AMU job | 35 to 331 s, median 80 s |
| Pull request opened to comment | 132 to 397 s, median 187 s |

Useful (adds something): 1665, 1762, 2062, 1706, 1852 and 1663. In each, the pull request's own test fails on the
merge base and passes on the head, so it reproduces the bug or the missing behaviour. In 1663 the 3 behaviour rows
are also real and intended: `TemplateNotFound` now names the search path.

## Per pull request

| Fork PR | Upstream | Job | Open to comment | First line right | Adds | False rows | Real rows | Raise missed / catchers | Tests with an outcome / suite |
|---|---|---:|---:|---|---|---:|---:|---:|---:|
| 1 | 2099 | 35 s | 143 s | no | noise | 0 | 0 | 0 / 0 | 279 / 679 |
| 2 | 2098 | 90 s | 159 s | yes | nothing | 0 | 0 | 0 / 0 | 675 / 677 |
| 3 | 2096 | 331 s | 397 s | yes | nothing | 0 | 0 | not injected | 671 / 675 |
| 4 | 2093 | 191 s | 235 s | yes | nothing | 0 | 0 | not injected | 673 / 676 |
| 5 | 2077 | 80 s | 139 s | yes | nothing | 0 | 0 | 0 / 0 | 674 / 676 |
| 6 | 2066 | 69 s | 194 s | yes | nothing | 0 | 0 | 0 / 0 | 673 / 675 |
| 7 | 1665 | 62 s | 162 s | yes | something | 0 | 0 | 0 / 73 | 673 / 675 |
| 8 | 1664 | 100 s | 213 s | no | noise | 121 | 0 | 0 / 554 | 670 / 673 |
| 9 | 1762 | 54 s | 132 s | yes | something | 0 | 0 | 0 / 8 | 661 / 668 |
| 10 | 2062 | 57 s | 167 s | yes | something | 0 | 0 | 0 / 2 | 664 / 669 |
| 11 | 1880 | 87 s | 218 s | yes | nothing | 0 | 0 | 0 / 0 | 663 / 668 |
| 12 | 1706 | 84 s | 194 s | yes | something | 0 | 0 | 0 / 9 | 669 / 671 |
| 13 | 1663 | 80 s | 160 s | yes | something | 0 | 3 | 0 / 5 | 668 / 670 |
| 14 | 2061 | 57 s | 230 s | yes | nothing | 0 | 0 | 0 / 2 | 652 / 661 |
| 15 | 1852 | 77 s | 187 s | yes | something | 0 | 0 | 0 / 12 | 658 / 664 |

## Defects (recorded, not fixed)

1. **"ran all N tests" when part of the suite gave no outcome.** Mode "all of the suite" is read from the plan, not
   from what ran. When a chunk ends with no outcomes, the first line should say how many tests of the suite gave none.
   Reproduction: fork PR #1 (jinja 2099); `report.json` has `executed.unrunnable` = 400, and the gaps give the runner
   error.
2. **Nested functions pair on an empty input.** A function defined inside another reads its free variables from the
   closure, and the input digest leaves them out. Two calls with different closure state count as the same input.
   Reproduction: fork PR #8 (jinja 1664); all 121 rows are on `Parser.parse_tuple.<locals>.parse`, input `["list", [], 0]`.
3. **Blocks with unchanged lines inside them are re-applied as if contiguous.** `states._one_hunk` drops the unchanged
   lines between a block's removed and added lines, so a comment, docstring or annotation edit is compared as a code
   change. Reproduction: jinja 2099 `_identifier.py` (a comment moved two lines); Flask 5812 `ctx.py:235`, where the
   added lines are 235, 236 and 238.
4. **Files outside the package count as production code.** `scripts/` holds a generator no test runs. Reproduction:
   fork PR #1.
