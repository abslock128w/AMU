**AMU: 0 flips** at `e348330`, among 16081 tests run, some of them on the changed code

<details><summary>What ran and what was not seen</summary>

### Tests this pull request updated (117)

Pass on the merge base and fail on the head as they were before this pull request edited them; as edited, they pass. The edit is in the diff, so they are not flips.

- `tests/async/test_async_queryset.py::AsyncQuerySetTest::test_aiterator_prefetch_related_chunk_size_none`
- `tests/deprecation/test_middleware_mixin.py::MiddlewareMixinTests::test_deprecation_import_compatibility`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_raises_type_error_with_safe_arg`
- `tests/mail/test_backends.py::BaseEmailBackendTests::test_unknown_kwargs_ignored`
- `tests/mail/test_backends.py::ConsoleBackendTests::test_unknown_kwargs_ignored`
- `tests/mail/test_backends.py::DummyBackendTests::test_unknown_kwargs_ignored`
- `tests/mail/test_backends.py::FileBackendPathLibTests::test_unknown_kwargs_ignored`
- `tests/mail/test_backends.py::FileBackendTests::test_unknown_kwargs_ignored`
- `tests/mail/test_backends.py::LocmemBackendTests::test_unknown_kwargs_ignored`
- `tests/mail/test_deprecated.py::DeprecatedEmailSettingsTests::test_warn_when_defining_deprecated_settings`
- `tests/mail/test_deprecated.py::DeprecatedEmailSettingsTests::test_warn_when_using_deprecated_settings`
- `tests/pagination/tests.py::PaginationTests::test_orphans_value_larger_than_per_page_value`
- `tests/signing/tests.py::TestSigner::test_base64_hmac_default_algorithm_deprecation`
- `tests/utils_tests/test_crypto.py::TestUtilsCryptoMisc::test_salted_hmac_default_algorithm_deprecation`
- `tests/utils_tests/test_html.py::TestUtilsHtml::test_urlize_http_default_warning`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_custom_encoder`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_list`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_non_ascii`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_passing_arguments_to_json_dumps`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_text`
- `tests/httpwrappers/tests.py::JsonResponseTests::test_json_response_uuid`
- `tests/async/test_async_queryset.py::AsyncQuerySetTest::test_aiterator_invalid_chunk_size`
- `tests/async/test_async_queryset.py::AsyncQuerySetTest::test_aiterator`
- `tests/async/test_async_queryset.py::AsyncQuerySetTest::test_aiterator_prefetch_related`
- `tests/httpwrappers/tests.py::HttpResponseSubclassesTests::test_invalid_redirect_repr`
- `tests/httpwrappers/tests.py::HttpResponseSubclassesTests::test_redirect_lazy`
- `tests/httpwrappers/tests.py::HttpResponseTests::test_lazy_content`
- `tests/httpwrappers/tests.py::QueryDictTests::test_basic_mutable_operations`
- `tests/httpwrappers/tests.py::QueryDictTests::test_mutable_copy`
- `tests/httpwrappers/tests.py::QueryDictTests::test_mutable_delete`
- `tests/httpwrappers/tests.py::QueryDictTests::test_create_with_no_args`
- `tests/httpwrappers/tests.py::QueryDictTests::test_duplicates_in_fromkeys_iterable`
- `tests/httpwrappers/tests.py::QueryDictTests::test_fromkeys_empty_iterable`
- `tests/httpwrappers/tests.py::QueryDictTests::test_fromkeys_mutable_override`
- `tests/httpwrappers/tests.py::QueryDictTests::test_fromkeys_with_nondefault_encoding`
- `tests/httpwrappers/tests.py::QueryDictTests::test_fromkeys_with_nonempty_value`
- `tests/httpwrappers/tests.py::QueryDictTests::test_immutability`
- `tests/httpwrappers/tests.py::QueryDictTests::test_immutable_basic_operations`
- `tests/httpwrappers/tests.py::QueryDictTests::test_immutable_get_with_default`
- `tests/httpwrappers/tests.py::QueryDictTests::test_missing_key`
- `tests/httpwrappers/tests.py::QueryDictTests::test_multiple_keys`
- `tests/httpwrappers/tests.py::QueryDictTests::test_non_default_encoding`
- `tests/httpwrappers/tests.py::QueryDictTests::test_pickle`
- `tests/httpwrappers/tests.py::QueryDictTests::test_querydict_fromkeys`
- `tests/httpwrappers/tests.py::QueryDictTests::test_single_key_value`
- `tests/httpwrappers/tests.py::QueryDictTests::test_update_from_querydict`
- `tests/httpwrappers/tests.py::QueryDictTests::test_urlencode`
- `tests/httpwrappers/tests.py::QueryDictTests::test_urlencode_int`
- `tests/httpwrappers/tests.py::HttpResponseTests::test_encoded_with_newlines_in_headers`
- `tests/httpwrappers/tests.py::StreamingHttpResponseTests::test_async_streaming_response_warning`
- and 67 more in the artifact

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 0 call(s) of 0 of 47 comparable changed function(s). Only differences both versions repeat are listed.

### Change evidence: the pull request's own tests (0)

The 123 tests this pull request adds or edits ran on the head and on the merge base `5180f82` with the tests as the pull request leaves them.

### New tests for new code (11)

Fail on the merge base only because what they use is not there yet (an ImportError, AttributeError or NameError on a name this pull request adds), and pass on the head. They show the new code exists, not what it does, so they are not counted as change evidence.

- `tests/defer/tests.py::DeferTests::test_defer_foreign_keys_are_deferred_and_not_traversed` (`ImportError on RemovedInDjango2028Warning`)
- `tests/defer/tests.py::TestDefer2::test_defer_proxy` (`ImportError on RemovedInDjango2028Warning`)
- `tests/pagination/tests.py::PaginationTests::test_orphans_value_larger_than_per_page_value` (`ImportError on RemovedInDjango2028Warning`)
- `tests/select_related/tests.py::SelectRelatedTests::test_list_with_select_related` (`ImportError on RemovedInDjango2028Warning`)
- `tests/select_related/tests.py::SelectRelatedTests::test_select_related_no_arguments_deprecated` (`ImportError on RemovedInDjango2028Warning`)
- `tests/signed_cookies_tests/tests.py::SignedCookieTest::test_expired_legacy_cookie_raises_signature_expired` (`ImportError on RemovedInDjango2028Warning`)
- `tests/signed_cookies_tests/tests.py::SignedCookieTest::test_legacy_salt_namespace_is_accepted` (`ImportError on RemovedInDjango2028Warning`)
- `tests/signed_cookies_tests/tests.py::SignedCookieTest::test_expired_new_style_cookie_does_not_fallback_to_legacy_salt` (`ImportError on RemovedInDjango2028Warning`)
- `tests/signing/tests.py::TestSigner::test_base64_hmac_default_algorithm_deprecation` (`ImportError on RemovedInDjango2028Warning`)
- `tests/template_tests/syntax_tests/test_basic.py::BasicSyntaxTests::test_double_dot_lookup` (`ImportError on RemovedInDjango2028Warning`)
- `tests/test_utils/tests.py::SetupTestEnvironmentTests::test_email_backend_override` (`ImportError on RemovedInDjango2028Warning`)

106 tests do not load on the merge base for a reason AMU did not read, and pass on the head: not counted as change evidence.

6 tests skipped on one side only, so they show nothing about the change.

### Gaps (10)

Not run or not seen:

- `1832 lower-ranked candidate(s) not run: about 17 tests/s per process fit 16668 in the 300 s allowed`
- `2 candidate(s) could not be run by django-runtests and are not covered by this result`
- `583 candidate(s) in gis_tests not run: django-runtests refuses them before any test runs: Aborting: A GIS database backend is required to run gis_tests.`
- `98 flip(s) past the first 20 were rerun together in one fresh process; their reach was not measured`
- `99966 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `Skipped 15 non-Python file(s)`
- `replaying the change's own tests (after the change, django-runtests): no outcomes read: Aborting: A GIS database backend is required to run gis_tests.`
- `side-by-side run (django-runtests): could not start the test runner: Argument list too long`
- `side-by-side: none of the 47 comparable changed function(s) was called by the tests run on the code before the change`
- `tests/test_runner_apps/tagged/tests_syntax_error.py: SyntaxError`

### What ran

16081 of 16668 candidate tests on the head, 399.3 s with `django-runtests`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run. Order: `the same explicit order on both sides (fixed hash seed)`.
On the merge base, 3 outcomes came from the cache of earlier passes and 299 ran.

<sub>AMU `0.3.0` from `967db59`, report `d3414689be9d`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->