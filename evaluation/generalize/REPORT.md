# The three causes, measured on the 47 set (2026-11-03)

The 47 set was re-run locally on engine `43d8b82`, the code released as v0.6.0. It is the same 47 pull requests from
Django (17), sympy (20) and Flask (10) as `evaluation/performance-v042` and `evaluation/performance-fixes`: Python 3.14, a
300 s budget per side and a 1,200 s wall cap. Each pull request also got the raise measure (`raise_missed.py`). A
`raise` is injected into a changed function and fires only inside a running test. The tests that fail with it are the
tests that would catch a raise; "missed" counts those that AMU did not run. The live pilots on attrs and httpx are in
`evaluation/pilot/attrs/REPORT.md` and `evaluation/pilot/httpx/REPORT.md`.

Files: `v060-47/labels.json` (hand labels, with the prior label beside each), `v060-47/data.json` (per pull request:
mode, candidate sources, rows, raise catchers and misses, first line, seconds), `v060-47/comments/`, and
`v060-47/reports.tar.xz` (report, raise and meta files).

## Losses

- **41 false behaviour rows on three pull requests. The goal was 0.** They come from three causes, and none is a
  per-repo rule:
  - **A version string read from each side's checkout.** On Django 22026 (6 rows, all on `Q.identity`) and 22171
    (3 rows), a model instance in the result carries `_django_version`. It is `'6.2'` on one side and `'6.2.dev<date>'` on the other,
    because the version is computed from each tree's own metadata. The fingerprint strips each side's tree root from
    strings, but not values derived from the tree.
  - **The tree root held as path parts.** On Django 22026 (1 row), pathlib's cached `_tail_cached` list holds each
    side's root one part per item, so the `<root>` replacement, which works on whole strings, misses it.
  - **Not traced.** One more row on Django 22026 is on `ModelDetailView.get_context_data`. Its 300-character preview
    shows no difference. The PR doesn't change what that function returns, so it is counted false.
  - **A lazy cache held in a slot.** On sympy 30781 (30 rows), `as_dummy` returns the same expression on both sides
    (same `srepr`; checked by running both trees). The old code had filled the hash cache `_mhash`, a `__slots__`
    field; the new code leaves it `None`. The fingerprint skips caches that a class descriptor serves, but a slot
    filled on first `hash()` is read as state.
- **The first line regressed on 3 pull requests.** Django 22026, Django 22171 and sympy 30781 were right before and
  are wrong now: each announces behaviour differences, and all of them are false. Four others are fixed, so the count
  rises from 42 to 43 of 47. "Nothing may regress" does not hold.
- **9 of the 33 real rows of before are gone.** All 9 are on sympy:
  - **sympy 30803 (3 rows):** `_rebuild_expr` returns FracElement instead of PolyElement. 343 differing calls did not
    repeat on a second run and were dropped as unstable. sympy results carry `_mhash` values, and those change with
    each process's hash seed, so a real difference can never repeat. This is the same slot-cache cause as the false
    rows, seen from the other side.
  - **sympy 30741 (6 rows):** `charpoly(x)` with an explicit `x` returns Poly instead of PurePoly. These rows are
    gone, most likely for the same reason. The 2 default-symbol rows (`lambda` becomes `_0`) are back, as 40 rows.
- **On Django and sympy, selection falls back to the static graph.** The suite never fits the budget there. The
  merge-base recording is cut off (Django ran 18,658 of 19,322 planned tests; sympy 30803 ran 2,000 of 13,226), so it is
  not complete, and every hunk takes its candidates from the static graph. No candidate came from coverage on any of the
  37 pull requests, although `mode` reads "coverage". That fallback is where the raise misses are:
  - Django 22046: 41 of 113 missed (a docstring-only change, so they cost nothing here).
  - Django 22154: 11 of 69.
  - Django 22095: 9 of 157.
  - sympy 30808: 11 of 2,805.
- **Django 22158 is still wrong.** It adds a serializer for Python 3.15 only, and the first line reads "every changed
  hunk ran on both sides (at import)". The guard (a name that exists only on 3.15) is not one the evaluator knows, so
  nothing marks the hunk as not executable here.
- **A hunk counts as run when any line in it ran.** On Django 22186, the `os.name == "nt"` branch and its POSIX
  `else` sit in one hunk. The hunk reads "ran on both sides (63 tests)", but the Windows line ran in none. The label
  stays "right", as before, but a guarded branch inside a hunk that ran is not named.
- **sympy 30780 has no raise measure.** Both sympy streams ran it in the same work folder, and one removed the tree
  while the other was reading it. It counts in neither column below.
- **Cost.** Median job time: Django 393 s, sympy 724 s, Flask 61 s. The whole-suite run on Flask costs no more than
  before.

## Numbers

| | Before (performance-fixes, v0.5.0 + #77–#79, #83) | v0.6.0 (`43d8b82`) |
|---|---:|---:|
| First line right | 42 of 47 | 43 of 47 (4 fixed, 3 regressed) |
| Adds something / nothing / noise | 19 / 26 / 2 | 19 / 24 / 4 |
| Behaviour rows: real / false | 33 / 1 | 384 / 41 |
| Real rows of before still found | – | 24 of 33 (Flask 22 of 22, sympy 2 of 11) |
| Whole suite run on both sides | 0 of 47 | 10 of 47 (all of Flask) |
| Tests that would catch a raise, missed: Flask | not measured | 0 of 1,490 (10 PRs) |
| … Django | not measured | 61 of 495 (17 PRs) |
| … sympy | not measured | 11 of 11,237 (19 PRs) |

Real rows are counted per distinct input. On Flask 5812 that is 338 rows where there were 16: each call pairs with its
own input now, instead of one row per kind. The 16 rows of before are all among them. The 1 false row of before
(`make_default_options_response`) is gone.

First lines changed against the prior labels:

| PR | Before | Now |
|---|---|---|
| Django 21887 | wrong: "not seen" | right: "1 of 1 changed hunk ran in no test" (MySQL-only property) |
| Django 22187 | wrong: "not seen" | right: "every changed hunk ran on both sides (at import)" (one data entry) |
| sympy 29693 | wrong: "adds new code only" | right: names the PR's own test failing on the base |
| sympy 30809 | wrong: "not seen" | right: "(at import)" for `__slots__ = ()` |
| Django 22026 | right | wrong: "8 behaviour differences on the same input", all false |
| Django 22171 | right | wrong: "3 behaviour differences", all false |
| sympy 30781 | right | wrong: "30 behaviour differences", all false |

## What would fix the losses (not done here)

1. **Fingerprints must not read values derived from each side's own tree or process.** That covers version strings,
   root paths split into parts, and hash values. The comparison should normalise them generically. Take each side's
   own version and root from its interpreter, and drop `hash()`-valued slots that hold a lazily filled cache: a slot
   that is empty on one side and filled on the other, or that differs between two runs of the same side, is a cache.
2. **Large suites need a whole-suite recording per merge base, made outside the pull request job.** For example, a
   scheduled job on the default branch writing the coverage cache that `Coverage` already reads. One job can't record
   them within its budget. A smaller step: tests a cut-off recording did see running a hunk should still count as
   candidates, ahead of the static graph.
3. **Name guarded lines inside a hunk that ran.** Do it at line level, not hunk level, for the guards the evaluator
   knows. A guard on a name that exists only on a later Python is not one it knows yet.
