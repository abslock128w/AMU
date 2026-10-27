**AMU: 0 flips** at `4879ea9`, among 6114 tests run, some of them on the changed code

<details><summary>What ran and what was not seen</summary>

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 10 call(s) of 1 of 1 comparable changed function(s). Only differences both versions repeat are listed.

### Change evidence: the pull request's own tests (0)

The 8 tests this pull request adds or edits ran on the head and on the merge base `05aec0a` with the tests as the pull request leaves them.

8 tests skipped on both sides, so they show nothing about the change: they did not run here.

### Gaps (6)

Not run or not seen:

- `222 candidate(s) in gis_tests not run: django-runtests refuses them before any test runs: Aborting: A GIS database backend is required to run gis_tests.`
- `4376 lower-ranked candidate(s) not run: about 6 tests/s per process fit 6336 in the 300 s allowed`
- `99953 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `Skipped 2 non-Python file(s)`
- `side-by-side run stopped at its time limit; calls captured until then are compared`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

6114 of 6336 candidate tests on the head, 396.6 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.

<sub>AMU `0.3.0` from `967db59`, report `1fa6e34fe740`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->