**AMU: 0 flips** at `8b66f7d`, among 4776 tests run, some of them on the changed code

<details><summary>What ran and what was not seen</summary>

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 5 call(s) of 1 of 1 comparable changed function(s). Only differences both versions repeat are listed.

### Gaps (4)

Not run or not seen:

- `43 candidate(s) in gis_tests not run: django-runtests refuses them before any test runs: Aborting: A GIS database backend is required to run gis_tests.`
- `99833 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `side-by-side run stopped at its time limit; calls captured until then are compared`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

4776 of 4819 candidate tests on the head, 242.9 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.

<sub>AMU `0.3.0` from `967db59`, report `5ffc8a8431ad`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->