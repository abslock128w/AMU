**AMU: 0 flips** at `<sha>`, ran all 667 tests; 1 of 1 changed hunk not executable on Python 3.12 here: `src/attr/_make.py:1111`

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 0 pair(s) compared, 0 not compared because the inputs differ, 0 call(s) without a partner, of 0 of 1 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 0 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Change evidence: the pull request's own tests (0)

The 1 test this pull request adds or edits ran on the head and on the merge base `<sha>` with the tests as the pull request leaves them.

1 test skipped on both sides, so they show nothing about the change: they did not run here.

### Changed hunks (1)

What ran each hunk of the change, from each side's own run:

- `src/attr/_make.py:1111` in `_ClassBuilder.add_replace`: cannot run here (`not executable on Python 3.12 here`)

### Gaps (1)

Not run or not seen:

- `Skipped 1 non-Python file(s)`

### What ran

All 667 tests of the suite on both sides, no selection: 14.2 s on the merge base and 13.4 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `v0.7.0`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->