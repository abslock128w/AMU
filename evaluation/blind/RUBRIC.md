# Labeling rubric v3, for the blind second labeling

This is rubric v3 from `evaluation/PROTOCOL.md` without its worked examples (they name pull requests from the pool).
For each item you have the AMU comment (`comment.md`) and the pull request's diff (`patch.diff`). Commit ids are
replaced by `<sha>` and the pull request number by `<pr>`. Label four fields.

## AMU in one paragraph

AMU runs the tests a diff reaches on the merge base and on the head. A **flip** is a test that passes on the base and
fails on the head. **Change evidence**: the pull request's own new or edited tests that fail on the base and pass on
the head. **Behaviour differences**: calls into changed functions, captured with the same input on both sides, whose
results differ. Each changed hunk gets a state (ran on both sides, ran only in the PR's own tests, ran in no test, not
executable here, at import, not recorded...). The **first line** (bold, first line of the comment) summarises it.

## 1. first_line_correct: right or wrong

Judge every clause of the first line, not only its category. It is right only when every clause is true of this pull
request and none says something a reader would take to be false. It is wrong when any of these holds:

1. No report (the line says AMU exited or crashed with no report).
2. A category header that does not fit the whole diff: "docstring or comment changes only", "type annotations only",
   "adds new code only" are right only when every changed production line is of that kind ("adds new code only" is
   wrong if the new code changes existing behaviour, e.g. found by name dispatch).
3. "ran all N tests" when a large part of the suite gave no outcome (hundreds of tests). A handful unrunnable is fine.
4. "every changed hunk ran on both sides, in up to N tests" when some code hunk did not run on both sides. A hunk
   counts as run when any line in it ran.
5. "(at import)" is right for lines that run when the module loads (version strings, `__all__`, module data,
   `__slots__`, imports). It is wrong when a hunk is a function body that ran only in the pull request's own tests
   (the line should then say so), or code under a def that cannot run here.
6. "in up to N tests" must count tests that ran the changed lines, not only the def line.
7. "ran only in the pull request's own tests: path:line" is wrong if another test reaches that hunk.
8. "N of M changed hunks ran in no test: ..." is wrong when a named hunk ran in some test; when the test run was cut off
   before every candidate ran (a cut run can only say "not recorded"); or when a named hunk is a docstring or comment
   edit inside code that did run. It stays right when the named hunks are true but of low value (type-check example
   files, annotations on uncalled code, overload stubs), and ordering is not judged.
9. "not executable on <Python/platform> here (needs <guard>)" is right when the named line cannot run in this job. It is
   wrong when the line itself runs and only one operand of an expression on it is gated. A repeated phrase that does not
   change the claim is not wrong.
10. "N tests of the pull request's own fail on the merge base and pass on the head" must be true for each test counted.
11. "N behaviour differences on the same input" makes the line wrong when the rows it announces are false. Real rows,
   however many, keep it right.

Where a fact can't be checked from the diff and comment (did this test run that line?), judge from what the comment
reports and the diff shows; do not assume a defect you can't see.

## 2. adds: something, nothing or noise

"Something" is something a reviewer cannot read from the diff and description.

- **something**: at least one of: a bug fix's own test failing on the merge base and passing on the head (the test
  reproduces the bug, which CI never shows); a real behaviour row the description does not make obvious; a hunk no
  test reaches that the diff does not show as unreached; a hunk named as not executable on this job's Python or
  platform when that is not obvious from the diff.
- **nothing**: the comment is right (or wrong only by omission) and says no more than the diff, description or green
  CI: own tests failing on the base only for want of a new feature, or whose base failure is evident from the diff;
  real, intended rows the description or diff already states; platform-gated code a reviewer knows doesn't run here;
  moves, version bumps, docstring-only, annotation-only changes. A wrong first line does not by itself make it noise.
- **noise**: the comment asserts something false a reviewer would act on, or buries the true part: false behaviour
  rows announced; "evidence" tests that fail only because the code is new; a wrong count of tests run.

## 3. false_rows and real_rows (integers; 0 when the comment lists no behaviour rows)

Count the rows the comment lists under behaviour differences (a grouped line "K calls show this change" counts K).
- **real**: the difference follows from what the diff changes in that function (its result, type, value or message).
- **false**: the difference comes from how the two sides were run, not from the change (values derived from each
  side's tree such as version strings or paths, lazily filled caches, closure state, objects built by old code).
- A row whose before and after previews look equal and that you can't trace: false, unless the diff changes what that
  function returns, then real.
real_rows + false_rows = the number of rows listed.
