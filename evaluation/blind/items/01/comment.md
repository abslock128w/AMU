**AMU: 0 flips** at `<sha>`, ran all 371 tests; every changed hunk ran on both sides, in up to 28 tests

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 98 pair(s) compared, 0 not compared because the inputs differ, 0 call(s) without a partner, of 1 of 1 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 0 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Changed hunks (2)

What ran each hunk of the change, from each side's own run:

- `src/flask/sansio/app.py:537` in `App.select_jinja_autoescape`: docstring, comment or layout only
- `src/flask/sansio/app.py:547` in `App.select_jinja_autoescape`: ran on both sides (28 tests)

### Gaps (2)

Not run or not seen:

- `1 candidate(s) could not be run by pytest and are not covered by this result`
- `Skipped 1 non-Python file(s)`

### What ran

All 371 tests of the suite on both sides, no selection: 29.5 s on the merge base and 24.7 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `<sha>`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->
