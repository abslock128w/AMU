# Real regressions replayed through AMU v0.7.1

Date: 2026-11-10. Engine: tag v0.7.1 (5440308), local Python 3.14, macOS. Method: [PROTOCOL.md](PROTOCOL.md). Per-regression labels: [labels.json](labels.json).

## Result

AMU caught 1 of 20 confirmed regressions, and that one is doubtful.

| | Count |
|---|---|
| Candidates found in the seven repositories' histories | 76 |
| Confirmed by running the later test on base, head and fix | 20 |
| Rejected: the later test does not fail on the culprit's head | 44 |
| Unrunnable: the old commit fails at import here | 12 |
| Confirmed and caught by a flip | 0 |
| Confirmed and caught by a behaviour row on the regressed function | 1 |
| Confirmed and missed | 19 |

Every miss has the same cause: no test that existed when the pull request was opened fails on its head. Only the test added later, with the fix, shows the regression. AMU's flip count runs the tests that exist, so it reports 0 flips, and that report is true.

The one catch is sympy 26358: a row on `heurisch` reads "as it was raise PolynomialError, as changed return None". It is on the regressed function, so the rule counts it. Whether that input is one the regression breaks was not established. It reads like the change the pull request intended.

## Why this number is what it is

The sample is biased against any tool that runs the existing suite. A regression only reaches the default branch if the project's own CI passed. So every regression in this set had, by construction, no failing existing test. A flip can never catch these.

What could catch them is the second kind of evidence: a behaviour row on the regressed function, or a hunk named as not run. Those results:

| Evidence on the regressed function | Regressions |
|---|---|
| Hunk state "ran on both sides" | 12 |
| Behaviour row on it | 1 |
| Hunk named as ran in no test | 0 |
| Hunk not recorded, run cut short | 1 |
| Hunk not matched to the function by the labelling script | 7 |

In the 12, the regressed code ran in existing tests on both sides and gave the same results on the inputs those tests supply. The inputs that break are ones no existing test passes.

## Per repository

| Repository | Candidates | Confirmed | Rejected | Unrunnable | Caught |
|---|---|---|---|---|---|
| attrs | 5 | 4 | 1 | 0 | 0 |
| requests | 4 | 4 | 0 | 0 | 0 |
| flask | 2 | 0 | 0 | 2 | 0 |
| httpx | 4 | 0 | 0 | 4 | 0 |
| jinja | 4 | 0 | 3 | 1 | 0 |
| django | 40 | 6 | 34 | 0 | 0 |
| sympy | 17 | 6 | 6 | 5 | 1 |
| Total | 76 | 20 | 44 | 12 | 1 |

## Each confirmed regression

| Regression | Regressed function | Caught | Rows on it | Other rows | Seconds | Note |
|---|---|---|---|---|---|---|
| attrs #1009 (fix #1085) | `_ClassBuilder._make_getstate_setstate.slots_setstate` | no | 0 | 0 | 45.8 |  |
| attrs #1267 (fix #1328) | `Converter.__init__, pipe.pipe_converter` | no | 0 | 3 | 177.5 |  |
| attrs #1401 (fix #1417) | `_transform_attrs` | no | 0 | 0 | 74.9 |  |
| attrs #759 (fix #806) | `evolve` | no | 0 | 0 | 121.6 | 57 of 406 tests could not be run by pytest at this old commit on Python 3.14; stated in the first line. |
| django #18270 (fix #20459) | `RoutePattern.match` | no | 0 | 0 | 409.5 |  |
| django #18314 (fix #19472) | `QuerySet.union` | no | 0 | 1 | 446.0 |  |
| django #18620 (fix #19372) | `MinimumLengthValidator.validate, MinimumLengthValidator.get_error_message` | no | 0 | 0 | 254.6 |  |
| django #19073 (fix #19494) | `Aggregate.__init__, Aggregate.resolve_expression` | no | 0 | 89 | 531.8 | 89 rows, none on the regressed Aggregate methods: 84 on CombinedExpression.resolve_expression, 5 on BaseExpression.resolve_expression. The harness first matched them by method name alone; corrected. |
| django #19395 (fix #19945) | `Index.check` | no | 0 | 0 | 445.1 |  |
| django #19494 (fix #20303) | `BaseExpression.replace_expressions` | no | 0 | 0 | 437.5 |  |
| requests #6644 (fix #7315) | `HTTPAdapter.request_url` | no | 0 | 0 | 309.0 |  |
| requests #7272 (fix #7433) | `PreparedRequest.prepare_body` | no | 0 | 10 | 483.2 | One typing pull request with three later fixes. 7 rows on super_len are false at v0.7.1 (defect T5, fixed in PR #108); 3 on api.post not traced. |
| requests #7272 (fix #7502) | `RequestEncodingMixin._encode_files` | no | 0 | 10 | 473.2 | One typing pull request with three later fixes. 7 rows on super_len are false at v0.7.1 (defect T5, fixed in PR #108); 3 on api.post not traced. |
| requests #7272 (fix #7505) | `RequestEncodingMixin._encode_params, RequestEncodingMixin._encode_files, PreparedRequest.prepare_body` | no | 0 | 10 | 483.1 | One typing pull request with three later fixes. 7 rows on super_len are false at v0.7.1 (defect T5, fixed in PR #108); 3 on api.post not traced. |
| sympy #24303 (fix #24826) | `opt_cse._find_opts` | no | 0 | 0 | 969.2 |  |
| sympy #25753 (fix #26429) | `Quaternion._eval_subs` | no | 0 | 2 | 921.1 |  |
| sympy #25913 (fix #26713) | `_EvaluatorPrinter._preprocess` | no | 0 | 0 | 814.6 |  |
| sympy #26358 (fix #26935) | `heurisch` | yes | 1 | 0 | 990.8 | 1 row on heurisch itself: as it was raise PolynomialError, as changed return None. Counted caught by the rule. Whether that input is one the regression breaks was not established; it reads like the change the pull request intended. |
| sympy #27373 (fix #30930) | `MatrixBase.norm` | no | 0 | 0 | 1140.2 | AMU reported nothing: stopped at the harness wall cap of 1200 s on a loaded machine. No existing test flips, so a full run would not have flipped either. |
| sympy #30882 (fix #30986) | `_monotonic_sign` | no | 0 | 0 | 1026.9 | Several tests failed once and passed in both reruns, labelled unstable. None is the regression test and no existing test flips, so the cause is the suite, not flip trust. |

## Defects found on the way

1. **False rows when an input holds a per-run value (requests 7272).** A multipart body holds a random boundary, so the field was left out of the input whole. Two bodies of different length paired as equal and gave 7 rows on `super_len`. Fixed in PR #108: the length of a long text stays in the input.
2. **A row that shows the same text on both sides (Django 22026, Django 19073).** Results alike in the 300 characters kept were printed twice with "from character 301". Fixed in PR #108: the row names the field where the results part.
3. **The harness matched rows by method name alone.** Django 19073 first counted as caught by rows on `CombinedExpression.resolve_expression`; the regression is in `Aggregate.resolve_expression`. Corrected in `replay.py` and in the labels.
4. **One sympy replay gave no report.** sympy 27373 stopped at the harness wall cap of 1200 s on a loaded machine.

## What this says about the product

- The flip count does what it says. It does not find regressions the suite does not assert. Nothing in this set contradicts a first line.
- The behaviour comparison is limited to inputs existing tests supply. On this set that is not enough: 1 doubtful catch in 20.
- A tool that is to catch this class needs inputs the suite does not have. That is a different feature from anything in v0.7.1 and is not built.

## Limits

- 20 regressions, 4 repositories with any confirmed. Flask, httpx and jinja have none: their old commits do not import on Python 3.14 or with current dependencies.
- Old commits run on today's Python and dependencies, not those of their time.
- Django and sympy replays had no suite recording for the old base, so tests were selected statically, as on a first run in a new repository.
- One labeler. The caught rule is mechanical: a flip, or a behaviour row whose target is the regressed function.
