**AMU: 0 flips** at `<sha>`, ran all 667 tests; every changed hunk that existed before ran on both sides; 1 add new code with no existing callers

<details><summary>What ran and what was not seen</summary>

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 34 pair(s) compared, 0 not compared because the inputs differ, 8 call(s) without a partner, of 6 of 6 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 0 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Change evidence: the pull request's own tests (0)

The 5 tests this pull request adds or edits ran on the head and on the merge base `<sha>` with the tests as the pull request leaves them.

### New tests for new code (5)

Fail on the merge base only because what they use is not there yet (an ImportError, AttributeError or NameError on a name this pull request adds), and pass on the head. They show the new code exists, not what it does, so they are not counted as change evidence.

- `tests/test_validators.py::TestLtLeGeGtNe::test_in_all` (`ImportError on ne`)
- `tests/test_validators.py::TestLtLeGeGtNe::test_retrieve_bound` (`ImportError on ne`)
- `tests/test_validators.py::TestLtLeGeGtNe::test_check_valid` (`ImportError on ne`)
- `tests/test_validators.py::TestLtLeGeGtNe::test_check_invalid` (`ImportError on ne`)
- `tests/test_validators.py::TestLtLeGeGtNe::test_repr` (`ImportError on ne`)

### Changed hunks (8)

What ran each hunk of the change, from each side's own run:

- `src/attr/validators.py:35`: ran on both sides (`at import`)
- `src/attr/validators.py:160` in `matches_re`: docstring, comment or layout only
- `src/attr/validators.py:372` in `deep_iterable`: docstring, comment or layout only
- `src/attr/validators.py:517` in `ge`: docstring, comment or layout only
- `src/attr/validators.py:532` in `gt`: docstring, comment or layout only
- `src/attr/validators.py:539`: new code, no existing callers (4 tests)
- `src/attr/validators.py:576` in `max_len`: docstring, comment or layout only
- `src/attr/validators.py:605` in `min_len`: docstring, comment or layout only

### Gaps (2)

Not run or not seen:

- `Skipped 2 non-Python file(s)`
- `replaying the change's own tests (before the change, pytest): no outcomes read: ImportError: cannot import name 'ne' from 'attr.validators' (/tmp/amu-shadow-26avjr38/tree/src/attr/validators.py)`

### What ran

All 667 tests of the suite on both sides, no selection: 15.3 s on the merge base and 15.3 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `v0.7.0`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->