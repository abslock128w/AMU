**AMU: 0 flips** at `<sha>`, ran all 372 tests; 1 of 3 changed hunks ran only in the pull request's own tests: `src/flask/helpers.py:679`

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 1507 pair(s) compared, 0 not compared because the inputs differ, 22 call(s) without a partner, of 3 of 3 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 1 field differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Change evidence: the pull request's own tests (0)

The 5 tests this pull request adds or edits ran on the head and on the merge base `<sha>` with the tests as the pull request leaves them.

5 tests pass on the merge base too, so they show nothing about the change.

### Changed hunks (4)

What ran each hunk of the change, from each side's own run:

- `src/flask/cli.py:877` in `SeparatedPathType.convert`: docstring, comment or layout only
- `src/flask/helpers.py:679` in `_CollectErrors.raise_any`: ran only in the pull request's own tests (1 test)
- `src/flask/sessions.py:8`: ran on both sides (`at import`)
- `src/flask/sessions.py:230` in `SessionInterface.get_expiration_time`: ran on both sides (2 tests)

### Gaps (2)

Not run or not seen:

- `1 candidate(s) could not be run by pytest and are not covered by this result`
- `Skipped 5 non-Python file(s)`

### What ran

All 372 tests of the suite on both sides, no selection: 50.5 s on the merge base and 24.6 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `<sha>`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->
