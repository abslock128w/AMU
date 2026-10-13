# Check specifications and draft tests: schema 7

`concrete-patch-impact` packets use schema **7**. The opted-in MCP patch manifest is **amu-inspection-v7**; intent inspection remains v1. Analysis and recommendation identities are `python-markdown-concrete-patch-v6` and `behavioral-test-evidence-v6`. Begin paged retrieval with a fresh summary; schema-6 cursors and report layouts are incompatible.

The general drafting path supports three deliberately small patterns for a plain, unambiguous top-level Python function: a scalar direct-return equality check, equality at one literal key of a returned mapping, and a specific built-in exception from a direct call. It uses scalar literal arguments only and checks that one invocation binds the signatures in both source versions. A baseline test must establish the import path and a plain pytest function convention. A missing import example, changed signature, decorated or dynamically rebound target, unresolved expectation, or unsupported observation leaves `draft` null with an explanation. These are static gates, not proof that an import or behavior works at runtime.

First inspect the summary for the exact production `obligation_id`, then pass one `check_spec` JSON object to `amu analyze-patch --check-spec='…'` or MCP `amu_analyze_patch`. Specifications are capped at 4,096 encoded bytes, reject unknown fields and nonliteral input, and cannot supply a test body or executable expression. All three patterns use `args` (up to six scalar literals) and `kwargs` (up to six named scalar literals). An expectation must designate either a `user-supplied-task-requirement` or `user-designated-baseline-preservation` and cite the exact requirement in `reference`. AMU does not infer an expected result from candidate code or independently verify a supplied contract.

A returned mapping field uses a top-level string key and the explicit `getitem` operator:

```json
{
  "obligation_id": "catalog/records.py#lookup_record:obligation:<selected-id>",
  "invocation": {"kind": "mapping-field", "args": ["sample"], "kwargs": {},
                 "key": "status", "operator": "getitem"},
  "expectation": {"basis": "user-designated-baseline-preservation",
                  "reference": "The sample record keeps its active status.",
                  "predicate": "equals", "value": "active"},
  "intent": "preservation"
}
```

The draft calls the resolved function and asserts `result['status'] == 'active'`. Indexing is intentional: a missing key raises `KeyError`, whereas a present key with value `None` compares as `None`. Key presence checks, `.get()`, nested traversal, and return-type inference are unsupported. The mapping observation is a **supplied requirement** until reviewed execution establishes behavior for the literal input.

An expected exception uses a supported built-in class and an explicit matching rule:

```json
{
  "obligation_id": "filters.py#parse_window:obligation:<selected-id>",
  "invocation": {"kind": "expected-exception", "args": [-1], "kwargs": {}},
  "expectation": {"basis": "user-supplied-task-requirement",
                  "reference": "Negative windows must raise ValueError.",
                  "predicate": "raises", "value": "ValueError", "match": "subclass"},
  "intent": "bug-fix"
}
```

`subclass` generates `with pytest.raises(ValueError): ...`; `exact` additionally checks `type(caught.value) is ValueError`. The supported types are `ValueError`, `TypeError`, `KeyError`, `IndexError`, `LookupError`, `OverflowError`, `ZeroDivisionError`, `RuntimeError`, `NotImplementedError`, and `FileNotFoundError`. Broad `Exception`, custom exception imports, message matching, and inference from a `raise` statement are unsupported.

The default summary offers up to two compact `proposed_checks` showing the observable, expectation source, supplied versus discovered details, draft availability, review status, and unresolved limits. Without a selected specification, obligation details retain `verification_plan` guidance; they no longer carry a verbose `proposed_check` each. Only the selected production obligation gets a full `proposed_check` in the paged `obligations` section. It records source-discovered, explicitly supplied, deterministic, and unresolved authorship separately. A source-derived suggested input is not a discovered test. Full draft bodies and hashes stay in that retrievable detail. Counts and omissions identify records outside the preview.

Inspection does not import target code, write a draft, or execute a test. Drafts remain `review-required` and `not-run`. Their identity contains patch, source, configuration, invocation, expectation, and draft hashes; a changed source or specification invalidates previous retrieval. Review the complete draft, target module and import path before any separate authorized run. A failing reviewed test may reveal a protected behavior violation, a bug being fixed, or a setup issue; AMU inspection alone cannot choose among them.

The schema-6 `ast-boundary` callback template was a demonstration-specific adapter and is retired from the general interface. Schema-7 requests using that kind receive an explicit versioned rejection. The [schema-6 example and receipt description](phase3-check-drafts.md) remain historical evidence, including their original specification and result; they are not a supported schema-7 template.
