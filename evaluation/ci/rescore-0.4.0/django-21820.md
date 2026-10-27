**AMU: 0 flips** at `152c456`, among 965 tests run, some of them on the changed code; 1 test of the pull request's own fail on the merge base and pass on the head

### Tests this pull request updated (1)

Pass on the merge base and fail on the head as they were before this pull request edited them; as edited, they pass. The edit is in the diff, so they are not flips.

- `tests/urlpatterns/tests.py::SimplifiedURLTests::test_invalid_view`

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 40 call(s) of 1 of 1 comparable changed function(s). Only differences both versions repeat are listed.

### Change evidence: the pull request's own tests (1)

The 1 test this pull request adds or edits ran on the head and on the merge base `03988c5` with the tests as the pull request leaves them.

Fail on the merge base and pass on the head, so the change does what they check. Each failure on the merge base repeated in a fresh process.

- `tests/urlpatterns/tests.py::SimplifiedURLTests::test_invalid_view`

### Gaps (3)

Not run or not seen:

- `99833 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `Skipped 1 non-Python file(s)`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

965 of 965 candidate tests on the head, 52.7 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run. Order: `the same explicit order on both sides (fixed hash seed)`.
On the merge base, 1 outcomes came from the cache of earlier passes and 1 ran.

<sub>AMU `0.3.0` from `967db59`, report `cd0e06b03d07`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->