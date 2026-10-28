# AMU v0.4.2 on 47 fresh pull requests (2026-10-28)

Performance test, no engine change. Fresh pull requests only: frozen by `freeze.py` and committed (308c0b5) before any
run. Each was run as the v0.4.2 Action runs it (`run.py`), one at a time, and labelled by hand after reading its
description and diff.

## Results

| | Django | sympy | Flask | All |
|---|---:|---:|---:|---:|
| Pull requests | 17 | 20 | 10 | 47 |
| First line correct | 12 of 17 | 17 of 20 | 8 of 10 | 37 of 47 |
| Adds something a reviewer would want | 6 | 7 | 2 | 15 |
| Adds nothing | 9 | 12 | 4 | 25 |
| Adds noise | 2 | 1 | 4 | 7 |
| Counted flips (unedited tests) | 0 | 0 | 0 | 0 |
| … contradicted by the fresh-job rerun | — | — | — | 0 of 0 (never triggered) |
| Edited tests failing in their old form, set apart as "updated" | 8 | 32 | 2 | 42 |
| Behaviour-difference rows | 9 | 6 | 110 | 125 |
| … real | 0 | 6 (all intended) | 17 (all intended) | 23 (18%) |
| Median job time | 276 s | 295 s | 13 s | |
| Worst job time | 691 s | 452 s | 15 s | |

- **Flips:** none of the 47 pull requests broke an existing test it did not also edit. The 42 tests that passed AMU's
  flip checks were all tests the pull request edited, run in their old form. They are listed as "updated" and never
  counted, so the verify-flips rerun had nothing to check.
- **What adds something:** all 15 rest on change evidence. They are bug fixes whose new or edited tests AMU shows
  failing on the merge base and passing on the head, i.e. the tests reproduce the bug. Three add more:
  - Django #22137: a moved helper labelled as a move.
  - sympy #30803: behaviour rows showing that an EXRAW fix also changes the ordinary ZZ path.
  - Flask #5917: picks out the 2 of 11 edited tests that pin the fix.
- **Behaviour rows** were mostly wrong. Of 125, 23 were real (all intended by the author) and 102 were side-by-side
  artifacts (defects 1–3 below). Every behaviour row on Django and Flask that was not an intended API change was false.

## The comment was wrong or noisy on 13 of 47; the ten that matter most

1. **Flask #5818: noise, wrong first line.** "33 behaviour differences", all false. The PR inserts a leading `ctx`
   parameter, so calls captured from the old code bind an exception into `ctx`.
2. **Flask #5812: noise.** "77 behaviour differences", 60 false. The new context methods were run against context
   objects the old code built. The 17 real rows are the intended `AppContext` return type.
3. **Django #22026: noise, wrong first line.** "9 behaviour differences", all `NameError`. The changed function uses an
   import the PR adds at module level, and side-by-side runs it in the old module.
4. **Flask #5898: noise, wrong first line.** `redirect()`'s default code changed from 302 to 303. The first line reads
   "0 flips … some of them on the changed code", but side-by-side replays the bound default (302) and cannot see the
   change; no Flask test checks the default.
5. **Flask #5928: noise.** "14 tests of the pull request's own fail on the merge base". 13 are false: they only error
   after the new `test_robust_teardown` breaks teardown in the same process. The confirming rerun runs them together
   again.
6. **sympy #29693: noise, wrong first line.** "adds new code only", but the new `_print_OrdinalZero` is reached by name
   dispatch and changes `latex(OrdinalZero)` from `''` to `'0'`. It is a regression fix.
7. **Django #22176: noise.** "3 tests … fail on the merge base". 2 only fail because `trim_value` is new. Django
   `subTest` failure blocks are not parsed, so the new-code check misses them.
8. **Django #22046: wrong first line.** Docstring-only change: "no flip … not seen" after 3,903 tests and 231 s.
9. **sympy #30838: wrong first line.** Docstring-only change that side-by-side itself recognised: "no flip … not seen"
   after 3,732 tests and 245 s.
10. **sympy #30809: wrong first line.** `__slots__ = ()` on a mixin changes every subclass: "no flip … not seen". The
    PR's own test is filed as "new test for new code".

The other three wrong first lines are "no flip … not seen" where "no test ran the changed code" was the right answer or
could have been known:
- Django #22158: Python 3.15-only code.
- Django #21887: a MySQL-only property.
- Django #22187: one entry in a data dict.

## What the reviewer already had: project CI on the 15 pull requests where AMU added something

| PR | What AMU added | Project CI on the PR's head | Would CI have shown it? |
|---|---|---|---|
| Django #22102 | the new test fails on the base (mutation bug reproduced) | GitHub checks green; 25 statuses green | no: CI runs the tests on the head only |
| Django #22137 | 2 new tests reproduce the bug; the moved helper labelled as a move | green | no |
| Django #22154 | 2 new tests reproduce the routing bug | green | no |
| Django #22169 | 2 new tests reproduce the XOR join bug; 18 calls of the rewritten `JoinPromoter.__init__` unchanged | green | no |
| Django #22179 | 5 new tests reproduce the fail-open CSP bug; no existing test reached that case | green (1 unrelated check failed) | no |
| Django #22189 | 2 new tests reproduce the lexer bug | green | no |
| sympy #30779 | the new test reproduces the empty-intersection error | green | no |
| sympy #30780 | the new test reproduces the symbolic-vector pinv bug | green (6 unrelated failures) | no |
| sympy #30803 | 2 rows: the EXRAW fix also changes the ZZ path; the new test reproduces the recursion | green | no |
| sympy #30808 | the new test reproduces the assumption bug; 10 calls unchanged | green | no |
| sympy #30815 | the new test reproduces the exponent bug | green | no |
| sympy #30646 | 3 new tests reproduce the Lie heuristic errors | green | no |
| sympy #30841 | the edited tests (and an un-xfailed one) reproduce the singularities bug | green | no |
| Flask #5917 | names the 2 of 11 edited tests that pin the fix | green (1 unrelated failure) | no |
| Flask #6096 | 2 new tests reproduce the IPv6 parsing bug | green | no |

- **Coverage:** 15 of 15 are "no". None of the three projects' CI runs a pull request's new tests against the code
  before the change, and none of the 15 descriptions claims the tests were seen failing without the fix.
- **What that leaves:** the product's place is mostly this single thing: confirming that a fix's tests reproduce the
  bug. It is real, and on 15 of 47 pull requests. Elsewhere the comment adds nothing (25) or noise (7). The noise comes
  almost entirely from side-by-side and from replay's rerun.

## Found, not fixed

1. **Side-by-side runs the as-changed function in the old module.** A name the pull request adds at module level (an
   import, a helper) is missing there, and every run raises `NameError` (Django #22026: 9 false rows).
2. **Side-by-side runs as-changed methods against objects the old code built.** When a change reshapes those objects
   (Flask #5812's merged contexts), the rows are artifacts: 60 false rows. The existing "input built before the change"
   filter only covers one stateful case.
3. **Changed-signature filter.** It only catches a `TypeError` raised at the call. A parameter inserted before others
   misbinds the captured arguments and fails later (Flask #5818: 33 false rows).
4. **Default-value changes are invisible to side-by-side.** Arguments are captured after defaults are bound (Flask
   #5898).
5. **Replay's evidence rerun runs the failing tests together.** One test that corrupts shared state on the base makes
   the others fail there, and the rerun repeats it (Flask #5928: 13 false evidence).
6. **Django `subTest` failures are not parsed by #53's runner text reader.** The `ERROR: name (label) (params)` line is
   not matched, so new-code tests count as evidence (Django #22176).
7. **`adds_only` treats every new method as inert**, including methods found by name dispatch or overriding an
   inherited one (sympy #29693).
8. **Docstring-only, comment-only and annotation-only changes run the full candidate set** and get "no flip … not seen"
   or "0 flips". Side-by-side already knows a function's change is docstring-only (Django #22046, sympy #30838,
   sympy #30825: 231–281 s each).
9. **Module-level data, class attributes and properties have no reach measure**, so the first line falls back to "not
   seen" (Django #22187, #21887, #22158; sympy #30809).
10. **Candidates outside the project's configured `testpaths`.** Flask's tutorial tests (`examples/tutorial`, needing
    `flaskr` installed) are picked and unrunnable: 19 per Flask PR.
11. **"No test ran the changed code" is asserted when side-by-side's own run read no outcomes.** Seen in a discarded
    first Flask run, where an incompatible pytest made the base side collect nothing.
12. **The new-code check matched `__slots__`**, an attribute the change assigns, on a test that checks behaviour (sympy
    #30809).

## How it was run

- **Freeze (`freeze.py`, before any run):**
  - Django and sympy: the 20 most recent pull requests merged into the default branch since the Brief 5 cutoff
    (2026-10-18T00:00Z) that touch production Python.
  - Flask: the 10 most recent merged into `main` touching `src/flask/*.py`, any date.
  - Django had only 30 merges since the cutoff, 17 with production Python; the GitHub search total confirms 30. So
    Django has 17, not 20, and the test covers 47 pull requests, not 50.
- **Run (`run.py`):** `git archive v0.4.2` engine; merge base against head; budget 300 s per side; wall cap 1200 s;
  recording off; no generated tests; released defaults (rename labels on, other extras off); verify-flips when flips
  were counted (never); comment rendered quiet. Each repository's index cache was carried forward: Django and sympy
  from the Brief 5 cache, Flask cold. Job times are this machine's.
- **Environments:**
  - Django and sympy: the Brief 5 environments.
  - Flask: two venvs with its declared dependencies. PRs whose `uv.lock` pins pytest below 9.1 got pytest 9.0.2,
    because their conftest uses `_pytest.monkeypatch.notset`, which 9.2 removed. The rest got pytest 9.2.
  - A first Flask run on pytest 9.2 for all ten was discarded: 272 of 389 candidates unrunnable.
  - `tests/test_cli.py::test_get_version` fails on both sides because Flask is not installed as a package. It is
    never counted.
- **Labels:** `labels.json`, written after reading each description, diff and comment.
  - A first line is "correct" when its category (flips / behaviour difference / no test ran the changed code / adds new
    code only / refactor only / 0 flips on tested code) matches what the pull request is.
  - "No flip … whether any ran the changed code was not seen" counts as correct only where nothing better was knowable.
  - "Adds something" means something a reviewer cannot read from the diff and description.
  - Mechanical checks, such as an evidence test failing on the base, were rerun by hand where a label depended on them
    (Flask #5928).
- **Files:**
  - Comments: `<repo>/<n>.md`.
  - Report, meta and verify JSON per pull request: `reports.tar.xz`.
  - Upstream CI conclusions: `ci.json`.

## All 47

| PR | Title | First line (as posted) | First line correct | Adds | Rows | Job s | Why |
|---|---|---|---|---|---|---:|---|
| django #21855 | Fixed #37232 -- Added support for the HTTP QUERY method. | AMU: 0 flips at `e85b3c0`, among 4203 tests run, some of them on the changed code; side-by-side got to 8 of 8 changed functions before its time limit; 4 tests of the pull… | yes | nothing | - | 691 | New HTTP QUERY method: 20 new tests correctly 'new test for new code', 4 behaviour evidence tests, 155 captured calls of 8 changed functions unchanged; correct, but no more than a green CI run says. 691 s job. |
| django #21887 | Fixed test_asgeojson() on MySQL 9.5. | AMU: no flip at `b550194` among 9 tests run; whether any ran the changed code was not seen | no | nothing | - | 68 | MySQL 9.5-only GIS feature flag (class attribute turned property): nothing here can run it (gis_tests abort, no MySQL); headline says 'no flip among 9 tests, not seen' rather than 'no test ran the changed code' (a new property is not comparable, so side-by-side had no target). |
| django #22026 | Fixed #30949 -- Deprecated django.utils.functional.cached_pr | AMU: 0 flips at `be3dd9e`, among 5610 tests run, some of them on the changed code; side-by-side got to 2 of 3 changed functions before its time limit; 1 test of the pull … | no | noise | rows 9, real 0 | 559 | First line reports 9 behaviour differences; all are side-by-side artifacts: get_prefetcher as changed uses a module-level import the PR adds (from django.utils import functional), which the code before does not have, so every as-changed run raises NameError. The real change (deprecating cached_property) shows only in the 1 evidence test. |
| django #22046 | Doc'd ContentType.model_class behaviour for stale content ty | AMU: no flip at `1103845` among 3903 tests run; whether any ran the changed code was not seen | no | nothing | - | 231 | Docstring-only change (plus docs): read as a code change, 3903 tests and 231 s to say 'no flip, not seen'; no first-line category for docstring/comment-only changes. |
| django #22095 | Fixed #37401 -- Used os.makedirs() for safe_makedirs() on Py | AMU: 0 flips at `6ab7f90`, among 7953 tests run, some of them on the changed code; side-by-side got to 2 of 2 changed functions before its time limit; 4 tests of the pull… | yes | nothing | - | 386 | Intended behaviour change (mode now combined with umask, vendored chmod dropped); the edited and new permission tests failing on the base say what the diff and description already say. |
| django #22102 | Fixed #37407 -- Prevented UUIDTextMixin from mutating the lo | AMU: 0 flips at `610888b`, among 7743 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | something | - | 342 | Bug fix (lookup mutated in place) with one new test; AMU confirms the test fails on the merge base and passes on the head, i.e. it reproduces the bug. CI shows only that it passes. |
| django #22137 | Fixed #37419 -- Included CSP nonces in headers for streaming | AMU: 0 flips at `013eb3a`, among 1358 tests run, some of them on the changed code; side-by-side got to 1 of 1 changed functions before its time limit; 2 tests of the pull… | yes | something | - | 351 | CSP nonce for streaming responses: both new tests fail without the fix (evidence), and the helper moved from checks to utils is labelled as a move, which lets a reviewer skip 14 lines. |
| django #22154 | Fixed #37427 -- Kept generic relation queryset routing lazy. | AMU: 0 flips at `3da22de`, among 7019 tests run, some of them on the changed code; side-by-side got to 1 of 1 changed functions before its time limit; 2 tests of the pull… | yes | something | - | 452 | Routing fix for generic relation managers: both new tests fail on the base and pass on the head (they reproduce the bug); 5 captured calls of the changed method show no difference on existing tests. |
| django #22158 | Fixed #37431 -- Preserved frozendict values in migration ser | AMU: no flip at `40224a0` among 1355 tests run; whether any ran the changed code was not seen | no | nothing | - | 133 | Python 3.15-only frozendict serializer: under 3.14 the new code is inert and its tests skip; headline says 'no flip, not seen' instead of 'no test ran the changed code' (no comparable changed function: the change is a new class plus a registry entry). |
| django #22169 | Fixed #37438 -- Fixed join promotion for XOR conditions. | AMU: 0 flips at `4ea250d`, among 11443 tests run, some of them on the changed code; side-by-side got to 1 of 1 changed functions before its time limit; 2 tests of the pul… | yes | something | - | 277 | XOR join promotion: both new tests fail on the base (they reproduce the bug), and 18 captured calls of the restructured JoinPromoter.__init__ (AND/OR branches rewritten) show no difference for existing callers. |
| django #22171 | Fixed #37432 -- Made `select_for_update` raise after combine | AMU: 0 flips at `62ed9ed`, among 16311 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 252 | One-line guard; the edited test failing on the base is evident from the diff (the operation is added to the list of unsupported ones). No captured call (nothing calls select_for_update on a combined query). |
| django #22176 | Fixed #37433 -- Escaped database error messages logged by Q. | AMU: 0 flips at `64cbef6`, among 7071 tests run, some of them on the changed code; side-by-side got to 2 of 3 changed functions before its time limit; 3 tests of the pull… | yes | noise | - | 414 | First line counts 3 tests as change evidence, but 2 (test_trim_value, test_trim_value_non_string) only fail before because trim_value is new: Django subTest failure blocks ('ERROR: name (label) (value=...)') are not parsed, so #53's new-code check misses them. Their sibling without subTest was classified right. |
| django #22179 | Fixed #37395 -- Emitted 'none' for CSP directives left empty | AMU: 0 flips at `bd2e265`, among 1139 tests run, some of them on the changed code; 5 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 99 | Fail-open CSP fix: 5 new tests fail on the base and pass on the head (they reproduce the bug); side-by-side also saw 24 calls of build_policy from existing tests with no difference, i.e. no existing test reached the empty-directive case. |
| django #22186 | Fixed #37447 -- Fixed makemessages with non-ASCII file names | AMU: 0 flips at `cf0752b`, among 76 tests run, some of them on the changed code | yes | nothing | - | 84 | Windows-only branch (os.name == 'nt') plus a POSIX bytes path; 76 tests and 19 captured calls show no difference on POSIX. The comment does not say the Windows branch could not run here. |
| django #22187 | Fixed #37449 -- Corrected local name for Punjabi in django/c | AMU: no flip at `1b0f826` among 3711 tests run; whether any ran the changed code was not seen | no | nothing | - | 186 | One string in the LANG_INFO data dict: 3711 tests run, 136 s, headline 'no flip, not seen'; module-level data changes have no reach measure, so the comment cannot say whether any test read the changed entry. |
| django #22189 | Fixed #37450 -- Fixed parsing of comment and verbatim tags w | AMU: 0 flips at `d13621c`, among 7129 tests run, some of them on the changed code; 2 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 276 | Template lexer regex fix: both new tests fail on the base and pass on the head (they reproduce the bug). The change is to class-level regexes, so side-by-side had nothing to watch; the evidence carries the first line. |
| django #22204 | Fixed #35225 -- Skipped JSONObject ordering tests on Oracle  | AMU: no test ran the changed code in `django/db/backends/oracle/features.py` at `8b6119e`; 662 tests run | yes | nothing | - | 38 | Oracle 21c test skips: 'no test ran the changed code in oracle/features.py' is correct, and a reviewer knows Oracle-only code does not run here. |
| sympy #29693 | Fix LaTeX printer for `OrdinalZero` returning empty string | AMU: adds new code only at `f1a8fd9`; 1 test of the pull request's own fail on the merge base and pass on the head | no | noise | - | 296 | Says 'adds new code only', but the new LatexPrinter._print_OrdinalZero is found by name dispatch and changes latex(OrdinalZero) from '' to '0' (a regression fix; its test fails on the base). adds_only treats any new method as inert, including ones that override or are dispatched to. |
| sympy #29763 | printing: Add _print_conjugate to PythonCodePrinter and NumP | AMU: adds new code only at `7ef1993`; 3 tests of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 294 | New _print_conjugate methods where printing raised PrintMethodNotImplementedError before: 'adds new code only' fits (nothing that worked changes); the 3 evidence tests fail on the base with that error, as the description says. |
| sympy #30147 | Fix parse_expr(evaluate=False) for factorial | AMU: 0 flips at `1c4db0a`, among 2520 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 353 | 'factorial' added to a class-level tuple; the edited test failing on the base is evident from the diff. |
| sympy #30571 | Lark LaTeX parser: function argument readings, implicit prod | AMU: no test ran the changed code in `sympy/parsing/latex/lark/latex_parser.py`, `sympy/parsing/latex/lark/transformer.py` at `609fe85`; 1480 tests run | yes | nothing | - | 452 | Lark LaTeX parser rewrite: lark is not installed in this environment, so its 14 tests skip and nothing runs the parser; 'no test ran the changed code' is correct but says nothing about the change. |
| sympy #30646 | fix(solvers.ode): handle exceptions in Lie group heuristics | AMU: 0 flips at `13bc927`, among 4404 tests run, some of them on the changed code; 3 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 293 | Lie group heuristic exception fixes: 3 new tests fail on the base and pass on the head (reproduce the errors); no existing test call of the 3 changed heuristics was captured. |
| sympy #30741 | PurePoly canonical generators | AMU: 0 flips at `8b3b314`, among 2728 tests run, some of them on the changed code; 30 tests of the pull request's own fail on the merge base and pass on the head; 4 behav… | yes | nothing | rows 4, real 4, intended 4 | 426 | PurePoly canonical generators: the 4 rows (charpoly with an explicit x returns Poly, not PurePoly) are real and the PR documents exactly that; 30 evidence tests match the description. |
| sympy #30776 | feat(integrals): add Integral.reverse_limits | AMU: adds new code only at `fa5f268`; its 1 new test fails on the base with AttributeError, as expected | yes | nothing | - | 251 | New Integral.reverse_limits: 'adds new code only; its 1 new test fails on the base with AttributeError, as expected' is right and is what the diff shows. |
| sympy #30779 | geometry: fix Plane.distance() for parallel Segment3D and Ra | AMU: 0 flips at `97623a0`, among 2112 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | something | - | 372 | Plane.distance fix for parallel segments/rays: the new test fails on the base (ValueError unpacking an empty intersection) and passes on the head; no existing test called distance with such input (0 captured calls). |
| sympy #30780 | matrices: use _pinv_full_rank directly for vectors in _pinv_ | AMU: 0 flips at `f7d25f0`, among 2846 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | something | - | 360 | pinv fix for vectors: the new test fails on the base and passes on the head. The new early return changes the path for every row/column vector, numeric too, and no existing test call was captured (0 calls), which the first line does not say. |
| sympy #30781 | `as_dummy` guards against variable capture; IdentityFunction | AMU: 0 flips at `3cef4d4`, among 3391 tests run, some of them on the changed code; 4 tests of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 378 | as_dummy rewrite: 4 evidence tests, and 4 existing tests whose expected outputs the PR edits are listed as 'updated' (visible in the diff); 33 captured calls of the one comparable target unchanged. |
| sympy #30797 | parsing: remove math and numpy imports from autolev parser o | AMU: no test ran the changed code in `sympy/parsing/autolev/__init__.py`, `sympy/parsing/autolev/_listener_autolev_antlr.py`, `sympy/parsing/autolev/_parse_autolev_antlr.… | yes | nothing | - | 34 | autolev ANTLR parser: antlr4 is not installed here; 'no test ran the changed code' is correct and expected. |
| sympy #30803 | Fix infinite recursion in FracField.from_expr with EXRAW dom | AMU: 0 flips at `476ec0e`, among 4069 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head; 2 behavio… | yes | something | rows 2, real 2, intended 2 | 330 | EXRAW recursion fix: the 2 rows show the wrap also changes the ordinary ZZ path (_rebuild_expr returns FracElement instead of a raw ground element), which the description presents as EXRAW-only; intended (mirrors PolyRing). New test reproduces the recursion. |
| sympy #30808 | assumptions: use fuzzy_or in Mul._eval_real_imag | AMU: 0 flips at `fbb6334`, among 3664 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | something | - | 328 | fuzzy_or fix in Mul._eval_real_imag: the new test fails on the base (reproduces the issue) and 10 captured calls from existing tests are unchanged. |
| sympy #30809 | Add slots to NotIterable | AMU: no flip at `9ba2802` among 48 tests run; whether any ran the changed code was not seen | no | nothing | - | 54 | __slots__ = () on the NotIterable mixin changes every subclass (no instance __dict__); headline 'no flip, not seen' (class attribute: no side-by-side target), and the PR's own test is filed as 'new test for new code' because the AttributeError names __slots__, though it checks a behaviour change. |
| sympy #30815 | core: accumulate repeated base exponents in as_terms | AMU: 0 flips at `3ad8ec3`, among 3591 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | something | - | 334 | as_terms exponent accumulation: the new test fails on the base (reproduces the bug); 12 captured calls from existing tests unchanged. |
| sympy #30825 | matrices: accept explicit matrices in MatrixBase.cross annot | AMU: 0 flips at `9830c37`, among 3580 tests run, some of them on the changed code | yes | nothing | - | 281 | Type-annotation-only change: '0 flips, some on the changed code' (17 calls, no difference) is true, but 3580 tests and 281 s were spent on a change with no runtime effect; refactor-only does not cover annotation-only edits. |
| sympy #30827 | geometry: direct symbolic formulas for Plane.distance | AMU: 0 flips at `29ef543`, among 3978 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 290 | Plane.distance rewritten with closed forms: 1 updated test and 1 evidence test, both expectation changes visible in the diff; no existing call of Plane.distance captured. |
| sympy #30838 | Fix docstring typo in Poly.reduce (f_i -> g_i) | AMU: no flip at `16d8de2` among 3732 tests run; whether any ran the changed code was not seen | no | nothing | - | 245 | Docstring typo fix: side-by-side itself recorded 'only its docstring, comments or layout changed', yet the headline is 'no flip, not seen' after 3732 tests and 245 s. Docstring-only changes are not read like refactor-only ones. |
| sympy #30841 | fix singularities variable exp | AMU: 0 flips at `23a11c6`, among 4385 tests run, some of them on the changed code; 2 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 291 | singularities() Pow fix: the edited regression tests (and one whose xfail the PR removes) fail on the base and pass on the head; 3 captured calls from other tests unchanged. |
| sympy #30851 | Fix Heaviside, DiracDelta, and elliptic_f in Mathematica pri | AMU: 0 flips at `9b1263b`, among 4462 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head | yes | nothing | - | 246 | Mathematica printer fixes (EllipticF name, Heaviside/DiracDelta methods): the edited test fails on the base, as the edit shows. |
| flask #5812 | merge app and request context | AMU: 0 flips at `c2705ff`, among 370 tests run, some of them on the changed code; 4 tests of the pull request's own fail on the merge base and pass on the head; 77 behavi… | yes | noise | rows 77, real 17, intended 17 | 14 | Request context merged into app context: 17 rows are real and intended (request_context()/test_request_context() now return AppContext); 60 are side-by-side artifacts (as-changed methods run against context objects the old code built: AttributeError '_request', RuntimeError, has_request_context True->False). Category right, 78% of rows false. |
| flask #5818 | pass context through dispatch methods | AMU: 0 flips at `6a64969`, among 368 tests run, some of them on the changed code; 2 tests of the pull request's own fail on the merge base and pass on the head; 33 behavi… | no | noise | rows 33, real 0 | 14 | Dispatch methods gained a leading ctx parameter; calls captured from the old code bind positionally (exception into ctx), so all 33 rows are artifacts (AttributeError ... has no attribute 'request', TypeError invalid response). The changed-signature filter only catches TypeErrors raised at the call. |
| flask #5898 | redirect defaults to 303 | AMU: 0 flips at `eca5fd1`, among 366 tests run, some of them on the changed code | no | noise | rows 0 (missed) | 12 | redirect() default code 302 -> 303: side-by-side captures arguments after defaults are bound, so it replays code=302 explicitly and cannot see a default-value change; '0 flips, some of them on the changed code' reads as tested and unchanged, while no Flask test checks the default code. |
| flask #5899 | deprecate `should_ignore_error` | AMU: 0 flips at `c77a520`, among 365 tests run, some of them on the changed code | yes | nothing | - | 12 | Deprecates should_ignore_error: 17 captured calls unchanged, 0 flips; nothing beyond the diff. |
| flask #5917 | fix provide_automatic_options override | AMU: 0 flips at `e82db2c`, among 373 tests run, some of them on the changed code; 2 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 14 | provide_automatic_options fix inside a large test rewrite: of 11 edited tests, AMU names the 2 (attr_enable, in test_basic and test_views) that fail without the fix and pass with it, the ones that pin the bug. |
| flask #5928 | all teardown callbacks are called despite errors | AMU: 0 flips at `fbb6f0b`, among 374 tests run, some of them on the changed code; 14 tests of the pull request's own fail on the merge base and pass on the head | yes | noise | - | 15 | 14 'evidence' tests, 13 false: the new test_robust_teardown fails on the base and leaves teardown broken, so every later test in the process errors (ValueError: app_teardown); alone, each passes on the base. The fresh-process confirmation reruns them together, so the cascade repeats. |
| flask #6013 | autoescape selection uses case-insensitive comparison | AMU: 0 flips at `9368fb3`, among 330 tests run, some of them on the changed code | yes | nothing | - | 9 | Case-insensitive autoescape check: 14 captured calls, all lower-case names, no difference; no test uses an upper-case extension, which the comment does not say. |
| flask #6096 | Fix `.partition(":")` usage on potential IPv6 addresses | AMU: 0 flips at `7203fea`, among 366 tests run, some of them on the changed code; 2 tests of the pull request's own fail on the merge base and pass on the head | yes | something | - | 15 | IPv6 host parsing fix: the 2 new/edited tests fail on the base and pass on the head (they reproduce the bug); 24 captured calls from other tests unchanged. |
| flask #6133 | add `app.query` route decorator | AMU: adds new code only at `8999295`; its 1 new test fails on the base with AttributeError, as expected | yes | nothing | - | 11 | New app.query decorator: 'adds new code only; its 1 new test fails on the base with AttributeError, as expected' is right and is what the diff shows. |
| flask #6168 | drop support for Python 3.10 | AMU: 0 flips at `7689f98`, among 372 tests run, some of them on the changed code | yes | nothing | - | 12 | Drop Python 3.10 (version-gated code removed): 17 captured calls unchanged, 0 flips; nothing beyond the diff. |
