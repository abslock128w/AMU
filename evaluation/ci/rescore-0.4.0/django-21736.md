**AMU: 0 flips** at `7c3dc67`, among 12904 tests run, some of them on the changed code; 9 tests of the pull request's own fail on the merge base and pass on the head; 8 behaviour differences on captured input, collapsed at the end

### Change evidence: the pull request's own tests (9)

The 20 tests this pull request adds or edits ran on the head and on the merge base `2b30f62` with the tests as the pull request leaves them.

Fail on the merge base and pass on the head, so the change does what they check. Each failure on the merge base repeated in a fresh process.

- `tests/validators/tests.py::EmailValidatorTests::test_invalid_domain_error`
- `tests/validators/tests.py::EmailValidatorTests::test_invalid_username_error`
- `tests/validators/tests.py::EmailValidatorTests::test_validate_domain_override`
- `tests/validators/tests.py::EmailValidatorTests::test_validate_username_override`
- `tests/validators/tests.py::EmailValidatorTests::test_validate_override`
- `tests/validators/tests.py::EmailValidatorTests::test_parse_address_override`
- `tests/validators/tests.py::EmailValidatorTests::test_validate_domain_override_can_raise_custom_error`
- `tests/validators/tests.py::EmailValidatorTests::test_validate_username_override_can_raise_custom_error`
- `tests/validators/tests.py::EmailValidatorDeprecationTests::test_legacy_validate_domain_part_override_with_super_warns_once`

### New tests for new code (5)

Fail on the merge base only because what they use is not there yet (an ImportError, AttributeError or NameError on a name this pull request adds), and pass on the head. They show the new code exists, not what it does, so they are not counted as change evidence.

- `tests/validators/tests.py::EmailValidatorTests::test_parse_address` (`AttributeError on parse_address`)
- `tests/validators/tests.py::EmailValidatorTests::test_validate_domain_allows_allowlisted_domain` (`AttributeError on validate_domain`)
- `tests/validators/tests.py::EmailValidatorDeprecationTests::test_legacy_validate_domain_part_override_warns` (`AttributeError on validate_domain_part_deprecated_msg`)
- `tests/validators/tests.py::EmailValidatorDeprecationTests::test_call_override_using_stock_validate_domain_part_warns` (`AttributeError on validate_domain_part_deprecated_msg`)
- `tests/validators/tests.py::EmailValidatorDeprecationTests::test_validate_domain_part_direct_call_warns` (`AttributeError on validate_domain_part_deprecated_msg`)

6 tests pass on the merge base too, so they show nothing about the change.

### Gaps (6)

Not run or not seen:

- `100189 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `2 candidate(s) could not be run by django-runtests and are not covered by this result`
- `485 candidate(s) in gis_tests not run: django-runtests refuses them before any test runs: Aborting: A GIS database backend is required to run gis_tests.`
- `Skipped 3 non-Python file(s)`
- `side-by-side run stopped at its time limit; calls captured until then are compared`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

12904 of 13393 candidate tests on the head, 275.9 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.
On the merge base, 6 outcomes came from the cache of earlier passes and 28 ran.

<details><summary>Behaviour differs on captured input (8)</summary>

Collapsed until side-by-side's false rows are fixed: of 18 such rows hand-labelled on Django and sympy pull requests, 11 were real differences and 7 came from how side-by-side runs the changed code.

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 26 call(s) of 2 of 2 comparable changed function(s). Only differences both versions repeat are listed.

- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'somewhere.org'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'example.com'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'test.com'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'email.com'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'mail.com'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'bar.com'], {})`: as it was `["return", "True"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.atm.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa…`: as it was `["return", "False"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)
- `django/core/validators.py#EmailValidator.validate_domain_part` on `([<django.core.validators.EmailValidator object at 0x?>, ''], {})`: as it was `["return", "False"]`, as changed `["raise", "RemovedInDjango2029Warning", "EmailValidator.validate_domain_part() is deprecated. Migrate to validate_domain(), raising ValidationError for invalid …` (differs in: `result`; shown: result)

</details>

<sub>AMU `0.3.0` from `967db59`, report `70fd03adbb42`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->