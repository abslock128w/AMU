# Brief 5: AMU on 100 real pull requests, run locally (2026-10-18)

The brief is `AMU-NEXT-PROMPT-5.md`; the rules fixed before the run are in `PROTOCOL.md`. The set is the last 50 merged
pull requests of Django and of sympy that touch production Python (`freeze.json`, pinned by `freeze.sha256`, merged as
PR #26 before anything ran on it). The engine is AMU v0.2.6 (572f76f), installed from the tag into its own venv,
unchanged throughout. Everything ran on this Mac under `/Users/Shared/amu-retro-2026-10-18/`. There was no GitHub
Action, no API key and no fork. This session wrote the generated-test tier's probes; AMU's own filter decided which
counted.

## The five numbers

| | Django (50) | sympy (50) | Both (100) |
|---|---:|---:|---:|
| 1. False-flip rate: counted flips the fresh-process rerun on the merge base saw fail there too | 0 of 8 | 0 of 22 | **0 of 30** |
| 2. Pull requests with at least one counted flip | 5 | 11 | **16** |
| 3. Pull requests with a side-by-side difference | 4 | 2 | **6** |
| 3. Side-by-side precision, hand label on all 18 rows of those 6 (fewer than 20, so all) | 82% (9 of 11) | 29% (2 of 7) | **61% (11 of 18)** |
| 4. Comparable changed functions with no captured call, pooled | 76% (207 of 274) | 64% (110 of 173) | **71% (317 of 447)** |
| 4. The same, production functions only (test-file functions left out) | 54% (80 of 147) | 53% (70 of 133) | 54% (150 of 280) |
| 4. Median per pull request | 0% | 56% | 45% |
| 5. Median job time | 24 s | 357 s | 154 s |
| 5. Worst job time | 439 s (#21806) | **1,140 s (#30476, tracer on, stopped at the wall cap)** | 1,140 s |

### What each number is made of, losses first

1. **0 false flips, but every one of the 30 counted flips is the same kind.** All 30 carry the label `test edited, not
   fixed`: a test the pull request edited, run in its old form against the new code. The old form fails because the
   behaviour changed on purpose, and the pull request updated the test to match. On the merge base each passed again
   (30 held), so none is false. But **no counted flip in 100 pull requests came from a test the pull request left
   alone**: AMU found no regression, and none of these merged pull requests is known to have shipped one. The flip
   rows say "fails as the test was before it was edited", which is true. On a reviewer's screen they are 30 reports of
   intended changes.
2. **The flip counts are depressed on Django by a runner defect.** In 36 of the 50 Django pull requests a `gis_tests`
   label in a batch made Django's runner abort ("A GIS database backend is required to run gis_tests"). AMU lost that
   batch and stopped there. Four Django pull requests ran no test at all (#21881, #21890, #22005, and #22041 with 9,600
   candidates). The comment then says "no flip count: no candidate test ran". A GitHub Ubuntu runner with Django's
   SQLite test settings has no spatial backend either, so the Action would do the same.
3. **Side-by-side precision is 61%, under the 70% bar.** Django's 11 rows are 9 real and intended, 2 artifacts; sympy's
   7 are 2 real and intended, 5 artifacts. No row showed an unintended real difference. The false rows and their causes
   are listed below.
4. **Number 4 depends on one pull request.** Django #21805 (the deprecation-cycle rename) alone has 180 comparable
   changed functions, 7 of them called. Without it the pooled share is 54% (144 of 267), and 42% (90 of 213) for
   production functions. Side-by-side's targets also include functions in test files (167 of the 447) and functions
   whose only change is the docstring (sympy #30530, #30697, #30734); the automated tier gets the same targets.
   Another 339 changed functions were not comparable at all (new, removed, nested, coroutines, or private names a
   class body mangles).
5. **The sympy worst case is the tracer.** On #30476 the call-edge recording of sympy's whole suite used the spare
   time the wall cap leaves (it cannot finish in it, as the pilot on #30525 showed). AMU was then stopped at the 1,200 s
   cap. The whole comment was one line plus its footer:
   > **AMU: no flip count** at `e2e41f9`: `stopped at the wall cap of 1200 s; amu reported nothing`

   By the owner's decision the tracer was off for the other 49 sympy pull requests. Their worst was 722.5 s (#30428).
   The pilot with the tracer on finished in 879 s. Django's recording fit, ran once on the first pull request (#21820,
   435 s cold), and was reused from the cache after that: all 50 Django jobs had it, and 34 drew candidates from it.

## The other results

| | Django | sympy | Both |
|---|---:|---:|---:|
| Pull requests that would have been quiet (`nothing_found`: the one-line comment) | 14 | 11 | 25 |
| Pull requests where the pull request's own replayed tests gave change evidence | 32 | 35 | 67 |
| Pull requests with probe targets | 23 | 35 | 58 |
| Probes attempted | 210 | 134 | 344 |
| Probes kept (all five filter steps) | 45, in 12 PRs | 60, in 27 PRs | 105, in 39 PRs |
| Probe minutes per pull request with targets: median, max | 0.6, 18.2 | 0.7, 5.3 | 0.6, 18.2 |
| Kept probes labelled intended | 45 | 57 | 102 |
| Kept probes labelled not intended | 0 | 2 (cosmetic) | 2 |
| Kept probes that are artifacts | 0 | 1 | 1 |

**Kept probes, by hand label.**
- **102 intended.** The difference is the change the pull request describes, or what its edited tests assert. 32 of
  them are Django #21805's renamed warning categories (`RemovedInDjango70Warning` → `RemovedInDjango2028Warning`),
  checked by normalising the names.
- **2 not intended, cosmetic (sympy #30428).** The Mod-parenthesization fix also wraps function-call forms in
  parentheses they do not need:
  - `mathematica_code(-2*Mod(x, y))` became `-2*(Mod[x, y])`;
  - the C++17 printer's `-2*std::fmod(x, y)` became `-2*(std::fmod(x, y))`.

  The output is still correct. The pull request describes one consequence of its `_print_Mul` change (the Rational
  case) and its edited tests assert only that one.
- **1 artifact (sympy #30701).** `update_args_with_paths` returns absolute paths, and the filter's copies of the two
  trees live in different folders (`c1-base`, `c1-head`). So the outcomes differ only by where the copy lives. The
  filter's fifth step cannot tell that from a behaviour change.

No kept probe showed a real, unintended behaviour change.

**What the probes reached that the tests did not.** 39 pull requests got at least one kept probe. Every probe target
is, by definition, a changed function side-by-side saw no call of, so each kept probe is behaviour evidence the run
of existing tests did not give. All 105 kept differences are ones the pull request's authors already knew about.

**The tier under the Action's own cap.** At the default `generate-dollars` of $0.10, AMU's request estimate allows one
request of 5 functions per pull request on most of these. Its estimate refused the request outright on sympy #30540
(nonlinsolve and substitution are long), so the automated tier would have generated nothing there. On #30589 and
#26912 it would have stopped after the first 5 targets. Counting only kept probes among each pull request's first 5
targets: 65 of the 105, in 38 of the 39 pull requests.

**How the probes were written and run.**
- One probe per target, from the function before and after, the pull request's text and one test file of the project.
- Run through `amu.core.generate.run` from the frozen engine, with a client that hands it the probes in place of a
  model reply.
- **Reruns:** 3 rewrites after a setup failure, each once (Django #21853, #21825, #22132), plus sympy #30540, rerun
  unchanged with the cap raised after the cap refused it.
- **Where setup could not succeed here:** GIS (no GDAL), PostgreSQL and Oracle drivers, PyYAML, lark, IPython,
  pycosat, minisat and z3 are not installed. Probes stubbed the database drivers where the code under test does not
  need them, and otherwise failed setup.
- **Minutes:** wall clock from first reading a pull request's targets to its last filter run, minus time queued behind
  a job. They are upper bounds: work on several pull requests overlapped.

## Side-by-side: the 18 labelled rows

| PR | Rows | Label | Cause |
|---|---:|---|---|
| Django #21357 `{% now %}` variables | 4 | real, intended | `now` keeps a FilterExpression, so variables work |
| Django #21736 EmailValidator parts | 1 | real, intended | `validate_domain_part` deprecated; Django's runner raises the warning |
| Django #21975 FETCH_PEERS for select_related | 2 | real, intended | `RelatedPopulator` starts a `peers` list |
| Django #22039 verbatim tokenizing | 2 | real, intended | `Lexer.__repr__` drops `verbatim=` |
| Django #22039 | 2 | **artifact** | the pull request deletes `DebugLexer._tag_re_split` and adds `Lexer._tag_re_split`. Side-by-side's as-changed run keeps the deleted subclass method, so the new `tokenize` calls the old splitter |
| sympy #30469 diffgeom docs | 1 | real, intended | the doctest blacklist gains the new page |
| sympy #30715 dpll2 with True/False | 1 | real, intended | `to_NNF(True)` becomes an empty `AND()` |
| sympy #30715 | 1 | **artifact** | the outcomes differ only in a generator's qualified name: the as-changed copy lives inside side-by-side's `__amu_outer__` wrapper |
| sympy #30715 | 4 | **artifact** | the as-changed `dpll_satisfiable` raises `TypeError: 'NoneType' object is not callable`. The real head does not: the pull request's replayed `test_issue_30714` calls it through `satisfiable(..., algorithm='dpll2')` and passes. The cause inside side-by-side is not isolated |

## Defects found during the run (engine not changed; every number above predates any fix)

1. **The runner aborts on one GIS label and the batch is lost** (36 Django pull requests, 4 with no test run). Django's
   runner refuses `gis_tests` without a spatial backend. AMU treats the batch's abort as the end of the run.
2. **A label for a mixin's test method crashes Django's runner** (#21805, #21983). For example
   `SharedEmailBackendTests.test_unknown_kwargs_ignored` on a class that is not a TestCase fails with "missing 1
   required positional argument: 'self'". #21805 ran 40 of 4,128 candidates.
3. **The tracer recording has no share of its own** (owner's words: a product defect, not a sympy quirk). A recording
   that cannot fit leaves AMU at the wall cap reporting nothing. AMU must give the recording its own budget share, skip
   it when it would not fit, and still run the rest and post. To fix after this retrospective.
4. **A stopped recording leaves its hook installed.** `amu_edges_hook.py` and `zzz_amu_edges.pth` stay in the
   project's site-packages after the recording is killed at its limit. To fix with 3.
5. **The remembered test rate decays across merge bases.** `load_memo` carries the measured tests-per-second to every
   merge base, and it caps the next run's candidates. On sympy it went 15 → 2 → 3 → about 0.2. #30540 ran 48 of 12,885
   candidates in 47 s and left 250 s of its 300 s budget unused; #30684 ran 8. The Action's restore-key cache fallback
   carries it the same way on GitHub.
6. **Side-by-side, as-changed run:**
   - a method the change deletes from a subclass still shadows the one it adds to the base class (#22039);
   - an unexplained `NoneType` call in `dpll_satisfiable` (#30715);
   - generator reprs include the wrapper's name (#30715).
7. **A patch that does not reverse cleanly stops everything** (sympy #30589: "other side of the change could not be
   built; nothing executed").
8. **Targets include test-file functions and docstring-only changes** (counted in number 4, and handed to the tier).
9. **The filter's fifth step accepts outcomes that depend on where the tree lives** (sympy #30701).

## Changes to the protocol after the run started, and slips

- **Driver restart before any result (16:38).** The first launch was stopped one minute into Django #21820: the driver
  re-took the suite lock at once and would have starved the probe step. The hand-off was added and #21820 started over
  from zero. Nothing had been recorded.
- **The tracer off for sympy from #30511 on** (owner's decision, after #30476). #30511 had been running 6 minutes with
  the tracer and was restarted from zero.
- **Probe dollar cap.** $0.10 as the Action, except:
  - #30540, where it refused the request before any probe ran: rerun at $1.00;
  - #30589 and #26912, where I passed $1.00 up front by mistake. AMU's own estimate shows $0.10 would have stopped the
    tier after the first 5 targets; the kept probes inside those 5 are counted above.
- **A setup checked before submitting.** On Django #21853 I ran a rewrite's setup by hand before submitting it, which
  the protocol does not allow. After that a setup was looked at only after the filter had failed it.
- **Probe hygiene.**
  - Django #21825: two probes in one file defined the same model, and the second failed setup. It was rewritten once.
  - Django #21610: a probe left a settings override enabled. It was the only probe in its process.

## Decision rules, set in the brief before the numbers

| Rule | Number | Triggered | The engine work it names |
|---|---|---|---|
| False-flip rate above 5% | 0 of 30 | no | |
| No-captured-call share above 50% and at least one kept probe with an unintended difference | 71% pooled (54% production only, 42% production without #21805); 2 unintended, cosmetic | **yes, as written** | the `claude -p` subscription backend for the generated-test tier |
| Side-by-side precision on the hand sample below 70% | 61% | **yes** | side-by-side rows move to the collapsed section until fixed; the false rows and causes are in the table above |
| Worst job time above 15 minutes on either repository | 1,140 s on sympy #30476 | **yes** | profile that pull request before anything else |

**Order, by the rules' own words.** The job-time rule says "before anything else", so the first piece of work is
profiling sympy #30476. That profile is already partly in hand: the job spent its spare time on a recording that could
not finish, which is defect 3 above. Then come the side-by-side rows into the collapsed section, then the `claude -p`
backend.

**On the second rule, for the owner.** It triggers on the letter of the brief:
- the share is over 50% on the measure fixed in the protocol;
- two kept probes show differences the pull request did not intend.

Both of those differences are cosmetic, though: redundant parentheses in generated code. The share falls to 42% for
production functions once the one rename pull request is left out. And at the Action's $0.10 cap the automated tier
would have reached only about the first five targets of each pull request. Whether that counts as "earns automation"
is yours to say.

## Before and after

| | Before (brief 4, AMU's own repository) | After (this run) |
|---|---|---|
| Pull requests with an AMU comment | 13, all AMU's own | 100 from Django and sympy, rendered locally |
| Counted flips rerun in a fresh process, false | 20, 0 | 30, 0 (all edited-test flips) |
| Generated-test tier | never ran (no credentials) | 344 probes, 105 kept, 1 artifact, 2 unintended (cosmetic) |
| Side-by-side precision | 30% (pass 1), 100% (pass 2, development) | 61% on 18 rows |

## Files

**New in `evaluation/retrospective/`:**
- `freeze.py`, `freeze.json`, `freeze.sha256` (PR #26);
- `PROTOCOL.md`, with its change log;
- `run.py` (the driver), `probe.py` (the probe step), `score.py` (the numbers), `assemble.py`;
- `labels.json`, the hand labels;
- `numbers.json`;
- `django/<n>.md` and `sympy/<n>.md`, the comments, with kept probes under "Generated tests showing a difference",
  and `<n>.json` beside each;
- `raw-reports.tar.xz`, the untouched reports and diffs;
- this report.

**Changed:** `evaluation/ci/REPORT.md` (a pointer section). **Engine:** no change.

## Tried and rejected

- **Full-history clones by plain `git clone`.** Both stalled at 17–75 KB/s, and a stalled clone cannot resume. Blobless
  clones deepened in chunks with retries took about 20 minutes. They have full commit history and fetch file contents
  on demand.
- **Seeding sympy from the slow-suite trial's clone.** It was a partial clone, so it could not seed a full one.
- **Running heavy test functions (e.g. `test_meijerint`) whole as probes.** One slow probe can push the shared run past
  the filter's 120 s limit and lose every outcome in the batch, so those targets got small targeted calls.

## What executed upstream code

- **Django:**
  - the tests AMU selected on 50 pull requests, each on its head and merge base;
  - the whole suite once under the tracer (#21820's merge base), and once in the pilot (#21802);
  - the probe tests on both trees.
- **sympy:**
  - the same for its 50 pull requests and its pilot (#30525);
  - its whole suite under the tracer, twice, both stopped at the wall cap (the pilot and #30476).

All in scratch worktrees under `/Users/Shared/amu-retro-2026-10-18/`, with Python 3.14.6.

## Per pull request

| PR | job s | ran / candidates | flips (held on rerun) | side-by-side diffs | own tests: evidence | quiet | probe targets | attempted | kept | probe min |
|---|---:|---|---|---:|---:|---|---:|---:|---:|---:|
| django #21820 | 435.1 | 965 / 965 | 1 (1) | 0 | 1 of 1 |  | 1 | 1 | 0 | 0.4 |
| django #21853 | 16.0 | 14 / 14 | 0 | 0 | 0 of 0 | yes | 1 | 2 | 0 | 2.4 |
| django #21803 | 24.1 | 237 / 10944 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| django #21754 | 39.5 | 596 / 3592 | 0 | 0 | 6 of 6 |  | 0 | 0 | 0 |  |
| django #21834 | 15.2 | 160 / 4843 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| django #21714 | 67.9 | 2393 / 4807 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| django #21861 | 24.3 | 280 / 7069 | 0 | 0 | 3 of 3 |  | 1 | 1 | 1 | 0.5 |
| django #21864 | 202.7 | 4960 / 10559 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| django #21867 | 18.3 | 198 / 16128 | 0 | 0 | 7 of 7 |  | 0 | 0 | 0 |  |
| django #21659 | 23.4 | 236 / 5568 | 0 | 0 | 0 of 8 | yes | 0 | 0 | 0 |  |
| django #21805 | 41.6 | 40 / 4128 | 3 (3) | 0 | 28 of 29 |  | 173 | 173 | 32 | 3.2 |
| django #21886 | 19.6 | 439 / 8256 | 0 | 0 | 22 of 22 |  | 1 | 1 | 0 | 0.3 |
| django #21875 | 49.6 | 1238 / 12768 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| django #21064 | 30.7 | 222 / 222 | 0 | 0 | 2 of 6 |  | 0 | 0 | 0 |  |
| django #21482 | 20.7 | 358 / 2304 | 0 | 0 | 1 of 2 |  | 0 | 0 | 0 |  |
| django #21881 | 9.2 | 0 / 1996 | - | 0 | 0 of 0 |  | 3 | 3 | 0 | 0.8 |
| django #21736 | 16.8 | 87 / 10080 | 0 | 1 | 14 of 20 |  | 0 | 0 | 0 |  |
| django #21872 | 167.9 | 2688 / 2688 | 0 | 0 | 1 of 1 |  | 2 | 2 | 2 | 0.8 |
| django #20817 | 37.8 | 558 / 2036 | 0 | 0 | 0 of 2 | yes | 3 | 3 | 1 | 2.6 |
| django #21801 | 35.0 | 472 / 1599 | 0 | 0 | 0 of 1 | yes | 1 | 1 | 1 | 0.3 |
| django #21983 | 24.9 | 116 / 8182 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| django #21975 | 118.9 | 2880 / 2880 | 0 | 2 | 1 of 1 |  | 0 | 0 | 0 |  |
| django #21956 | 168.9 | 4720 / 10047 | 0 | 0 | 4 of 4 |  | 0 | 0 | 0 |  |
| django #21995 | 42.6 | 1576 / 1577 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| django #21971 | 18.2 | 3 / 610 | 0 | 0 | 0 of 3 | yes | 6 | 6 | 2 | 0.4 |
| django #22005 | 8.5 | 0 / 603 | - | 0 | 0 of 0 |  | 1 | 1 | 0 | 0.1 |
| django #21523 | 28.4 | 1280 / 8917 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| django #22025 | 22.0 | 319 / 13920 | 0 | 0 | 6 of 6 |  | 0 | 0 | 0 |  |
| django #22044 | 17.6 | 119 / 8352 | 0 | 0 | 0 of 1 | yes | 0 | 0 | 0 |  |
| django #22030 | 55.6 | 759 / 1650 | 0 | 0 | 4 of 4 |  | 0 | 0 | 0 |  |
| django #21806 | 438.9 | 4640 / 9641 | 0 | 0 | 0 of 2 | yes | 0 | 0 | 0 |  |
| django #21610 | 31.3 | 400 / 14689 | 2 (2) | 0 | 3 of 7 |  | 1 | 1 | 1 | 0.2 |
| django #22057 | 6.6 | 9 / 9 | 0 | 0 | 0 of 1 | yes | 1 | 1 | 1 | 0.7 |
| django #22018 | 34.6 | 806 / 809 | 0 | 0 | 3 of 3 |  | 0 | 0 | 0 |  |
| django #22055 | 20.3 | 320 / 3402 | 0 | 0 | 1 of 4 |  | 0 | 0 | 0 |  |
| django #22051 | 17.7 | 702 / 702 | 0 | 0 | 2 of 3 |  | 0 | 0 | 0 |  |
| django #21890 | 11.0 | 0 / 164 | - | 0 | 0 of 0 |  | 2 | 2 | 0 | 0.3 |
| django #22096 | 10.7 | 41 / 778 | 0 | 0 | 0 of 1 | yes | 1 | 1 | 0 | 2.2 |
| django #21825 | 124.7 | 860 / 860 | 0 | 0 | 0 of 3 | yes | 2 | 3 | 0 | 0.6 |
| django #22094 | 24.4 | 702 / 702 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.3 |
| django #22064 | 13.8 | 160 / 11166 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| django #21357 | 9.0 | 81 / 939 | 0 | 4 | 3 of 3 |  | 0 | 0 | 0 |  |
| django #22112 | 10.4 | 141 / 142 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 1 | 16.8 |
| django #22139 | 7.9 | 62 / 62 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 0 | 18.2 |
| django #22061 | 153.2 | 2568 / 2569 | 1 (1) | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| django #22041 | 26.9 | 0 / 9600 | - | 0 | 0 of 0 |  | 1 | 1 | 0 | 0.3 |
| django #22039 | 316.5 | 5278 / 8545 | 1 (1) | 4 | 3 of 10 |  | 1 | 1 | 1 | 0.3 |
| django #22132 | 10.5 | 121 / 4783 | 0 | 0 | 2 of 3 |  | 1 | 2 | 0 | 7.9 |
| django #22141 | 21.2 | 280 / 9258 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| django #22144 | 388.3 | 3678 / 7277 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.6 |
| sympy #30476 | 1140.1 | - / - | - | 0 | 0 of 0 |  | 0 | 0 | 0 |  |
| sympy #30511 | 460.5 | 680 / 3600 | 0 | 0 | 2 of 2 |  | 1 | 1 | 1 | 1.1 |
| sympy #30515 | 360.4 | 552 / 552 | 0 | 0 | 0 of 0 | yes | 2 | 2 | 0 | 5.3 |
| sympy #30477 | 380.2 | 73 / 696 | 0 | 0 | 0 of 3 | yes | 3 | 3 | 0 | 0.8 |
| sympy #30540 | 53.6 | 48 / 48 | 1 (1) | 0 | 10 of 11 |  | 4 | 8 | 2 | 1.3 |
| sympy #30572 | 225.3 | 432 / 432 | 2 (2) | 0 | 4 of 4 |  | 4 | 4 | 2 | 0.6 |
| sympy #30567 | 435.7 | 640 / 1056 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30563 | 58.4 | 504 / 504 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #28882 | 427.9 | 1240 / 3168 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| sympy #30530 | 195.6 | 984 / 984 | 0 | 0 | 2 of 2 |  | 1 | 1 | 0 | 0.4 |
| sympy #30575 | 359.3 | 956 / 1704 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30569 | 397.2 | 768 / 768 | 0 | 0 | 1 of 1 |  | 2 | 2 | 2 | 0.4 |
| sympy #30585 | 176.7 | 672 / 672 | 0 | 0 | 2 of 2 |  | 0 | 0 | 0 |  |
| sympy #30396 | 509.7 | 998 / 1440 | 0 | 0 | 4 of 4 |  | 2 | 2 | 2 | 1.2 |
| sympy #28853 | 649.0 | 760 / 792 | 1 (1) | 0 | 1 of 2 |  | 1 | 1 | 1 | 0.4 |
| sympy #30601 | 430.9 | 600 / 600 | 0 | 0 | 2 of 2 |  | 11 | 11 | 2 | 1.6 |
| sympy #30590 | 92.1 | 480 / 480 | 2 (2) | 0 | 2 of 3 |  | 4 | 4 | 4 | 1.3 |
| sympy #30591 | 447.8 | 680 / 2952 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.4 |
| sympy #30596 | 279.6 | 552 / 552 | 0 | 0 | 3 of 3 |  | 0 | 0 | 0 |  |
| sympy #30453 | 582.8 | 480 / 624 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30607 | 459.2 | 384 / 384 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| sympy #30600 | 297.4 | 360 / 360 | 2 (2) | 0 | 5 of 6 |  | 11 | 11 | 4 | 0.8 |
| sympy #30469 | 271.4 | 552 / 552 | 0 | 1 | 0 of 0 |  | 0 | 0 | 0 |  |
| sympy #29930 | 525.9 | 1200 / 1992 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| sympy #30642 | 375.4 | 960 / 960 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.3 |
| sympy #30471 | 109.6 | 936 / 936 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 1 | 0.5 |
| sympy #30589 | 60.8 | 0 / - | - | 0 | 0 of 0 |  | 20 | 20 | 2 | 1.7 |
| sympy #30636 | 333.3 | 2664 / 2664 | 0 | 0 | 2 of 2 |  | 1 | 1 | 1 | 0.3 |
| sympy #30673 | 342.1 | 2400 / 2400 | 0 | 0 | 1 of 2 |  | 2 | 2 | 1 | 0.3 |
| sympy #30693 | 183.6 | 2640 / 2640 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30697 | 423.6 | 3920 / 4176 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 0 | 0.2 |
| sympy #30690 | 237.0 | 3144 / 3144 | 0 | 0 | 0 of 0 | yes | 0 | 0 | 0 |  |
| sympy #30684 | 11.1 | 8 / 8 | 1 (1) | 0 | 1 of 2 |  | 4 | 4 | 2 | 0.4 |
| sympy #30682 | 355.4 | 3960 / 3960 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 0 | 0.8 |
| sympy #30605 | 441.6 | 3760 / 4152 | 1 (1) | 0 | 2 of 5 |  | 2 | 2 | 2 | 0.3 |
| sympy #30680 | 265.3 | 3000 / 3000 | 0 | 0 | 4 of 4 |  | 8 | 8 | 7 | 0.6 |
| sympy #30667 | 396.4 | 3717 / 4032 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.3 |
| sympy #30699 | 359.3 | 2920 / 2976 | 1 (1) | 0 | 2 of 2 |  | 2 | 2 | 2 | 0.9 |
| sympy #30702 | 488.9 | 2200 / 2328 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 1.0 |
| sympy #26912 | 572.6 | 840 / 1752 | 5 (5) | 0 | 0 of 3 |  | 12 | 12 | 4 | 2.2 |
| sympy #30701 | 154.1 | 672 / 672 | 0 | 0 | 5 of 5 |  | 3 | 3 | 1 | 0.5 |
| sympy #30706 | 329.5 | 1560 / 1560 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30711 | 303.3 | 1488 / 1488 | 0 | 0 | 1 of 1 |  | 0 | 0 | 0 |  |
| sympy #30715 | 270.2 | 1320 / 1320 | 0 | 6 | 1 of 4 |  | 5 | 5 | 0 | 0.8 |
| sympy #30687 | 519.1 | 1632 / 1632 | 0 | 0 | 0 of 0 | yes | 1 | 1 | 0 | 0.8 |
| sympy #30428 | 722.5 | 1320 / 1560 | 3 (3) | 0 | 6 of 6 |  | 8 | 8 | 8 | 0.7 |
| sympy #30734 | 324.0 | 1056 / 1056 | 0 | 0 | 0 of 0 | yes | 2 | 2 | 0 | 0.3 |
| sympy #30748 | 105.2 | 1896 / 1896 | 0 | 0 | 1 of 1 |  | 2 | 2 | 1 | 0.4 |
| sympy #30752 | 373.0 | 1434 / 5328 | 0 | 0 | 1 of 1 |  | 1 | 1 | 1 | 0.9 |
| sympy #30558 | 343.8 | 1152 / 1152 | 3 (3) | 0 | 4 of 4 |  | 4 | 4 | 3 | 2.0 |
