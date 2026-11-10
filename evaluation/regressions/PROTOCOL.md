# Protocol: real regressions

Fixed before the replays ran, with one correction made after: rule 2 first matched a row by method name alone, which counted a row on another class's method of the same name. It now needs the full qualified name. The correction lowered the result from 2 caught to 1.

## Finding candidates

A candidate is a merged pull request (the culprit) that a later commit names as the cause of a regression, with a fix commit that adds or edits a test. Sources: commit messages that say "Regression in <sha>", issue threads with a bisect, changelog entries. Each candidate records the culprit's merge base and head, the fix commit, the tests the fix adds or edits, and the functions the fix changes in the culprit's diff.

## Confirming

The fix's tests are run three times: laid over the culprit's merge base, over the culprit's head, and at the fix commit.

- **confirmed**: at least one test fails on the culprit's head and passes at the fix.
- **rejected**: no test fails on the culprit's head.
- **unrunnable**: the old commit fails at import or collection here.

A test that passes on the base, fails on the head and existed before the fix is recorded as an existing flip.

## Replaying

AMU runs on the culprit as a pull request: merge base and head as GitHub gave them, the suite as it was at the head, no knowledge of the fix.

## Caught

A regression is caught when either holds:

1. AMU counts a flip.
2. A behaviour row's target is a regressed function, by its full qualified name.

Anything else is missed, with one cause:

- **suite**: no existing test flips on the head.
- **selection**: an existing test flips and AMU did not select it.
- **budget**: an existing test flips and AMU's run was cut before it ran.
- **trust**: an existing test flips and AMU set it aside as unstable.
- **ran**: an existing test flips, AMU ran it, and did not count it.

A first line that only says the hunk ran is not a catch.
