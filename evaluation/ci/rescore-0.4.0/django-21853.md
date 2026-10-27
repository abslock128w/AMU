**AMU: no test ran the changed code in `django/contrib/gis/db/backends/oracle/features.py`** at `cd896ed`; 14 tests run

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 0 call(s) of 0 of 1 comparable changed function(s). Only differences both versions repeat are listed.

### Gaps (3)

Not run or not seen:

- `99838 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `side-by-side: none of the 1 comparable changed function(s) was called by the tests run on the code before the change`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

14 of 14 candidate tests on the head, 7.1 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.

<sub>AMU `0.3.0` from `967db59`, report `dca0b83de2d9`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->