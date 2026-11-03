**AMU: 0 flips** at `e45bc74`, ran all 661 tests; every changed hunk ran on both sides, in up to 1 test

<details><summary>What ran and what was not seen</summary>

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 0 pair(s) compared, 1 not compared because the inputs differ, 3 call(s) without a partner, of 1 of 1 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 0 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Change evidence: the pull request's own tests (0)

The 1 test this pull request adds or edits ran on the head and on the merge base `48b0687` with the tests as the pull request leaves them.

1 test pass on the merge base too, so they show nothing about the change.

### Changed hunks (2)

What ran each hunk of the change, from each side's own run:

- `src/jinja2/environment.py:409`: ran on both sides (1 test)
- `src/jinja2/environment.py:422` in `Environment.overlay`: docstring, comment or layout only

### Gaps (2)

Not run or not seen:

- `9 candidate(s) could not be run by pytest and are not covered by this result`
- `Skipped 1 non-Python file(s)`

### What ran

All 661 tests of the suite on both sides, no selection: 17.0 s on the merge base and 15.6 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `v0.7.0`, report `e730d62bc13b`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->