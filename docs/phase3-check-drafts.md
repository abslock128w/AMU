# Check proposals and draft tests: schema 6

`concrete-patch-impact` reports now use schema **6**. The opted-in MCP patch-inspection manifest is **amu-inspection-v6**; intent inspection stays v1. Analysis identity is `python-markdown-concrete-patch-v5` and recommendation identity is `behavioral-test-evidence-v5`. Restart paged retrieval with a fresh summary: older cursors and schema-5 summary layouts are not compatible.

For each production obligation, the paged `obligations` section now includes `proposed_check`. Its separate `evidence_gap`, `check_specification`, `draft`, `review_status`, and `execution_status` fields prevent a source suggestion from looking like a test result. The default summary puts up to two compact `proposed_checks` beside its prioritized production obligations. Their full specifications and draft bodies remain in the obligation detail. Summary previews are smaller: detailed role rationale remains in `identity.file_coverage`, and full candidate paths, test provenance, uncertainty rows, source hashes, and omitted evidence remain in the paged sections. Counts and retrieval cursors account for undisplayed records.

The first draft templates support an undecorated top-level Python function with a bounded scalar direct-return assertion, and the AMU AST boundary-helper shape with a direct call through a named function parameter. They require an unambiguous function in both source versions, a baseline test import for its module or function, and a plain pytest function convention. The direct-return template checks argument binding in both versions. Drafts use literal arguments only, no inferred fixtures or cleanup, and remain `review-required` and `not-run`. Syntax and import-path evidence do not prove a draft runs or tests the intended behavior.

Without an explicit expectation, `expected_result` and `draft` are null. A bounded source-derived input or observable can still be proposed, with limits and existing candidate tests to inspect. `no-matching-assertion-established` means AMU's recorded static search did not establish one; it is not proof that no test exists. Search inventory, unavailable baseline tests, source-call limits, candidate counts, and unresolved reasons stay visible. Candidate code never defines its own expected result. The initial interface accepts only a supplied task requirement or a user designation to preserve baseline behavior; reviewed contracts and specification assertions are not automatically interpreted yet.

To request a draft, pass one JSON object with `obligation_id`, `invocation`, and optionally `expectation` and `intent` to `amu analyze-patch --check-spec='…'`, or supply the same object as MCP `check_spec`. First inspect the summary for the exact production obligation ID. The interface is limited to 4,096 encoded bytes, rejects unknown fields and non-JSON constants, and accepts only scalar literal direct-call arguments or a 1,024-byte AST source example. It never accepts executable source for a test body. Inspection does not write a draft file, import target code, or run a command. A draft includes patch, source, configuration, invocation, expectation, and body hashes; changing any of these invalidates the proposal or cursor.

For the preserved callback patch, the explicit task requirement is: “A call through a function parameter should be reported as an unresolved callback boundary within the implemented supported scope.” The exact input is `def target(callback):\n    return callback()\n`. The resulting reviewed draft calls `amu.services.impact._boundaries(ast.parse(source).body[0])` and asserts that a row at line 2 has an unresolved reason. It does not use the candidate-added tests as its expectation. The separate review receipt records the exact source and one isolated run on each pinned AMU target version. The original fails and the candidate passes: missing behavior reproduced and fix verified, not a candidate regression. This establishes that one supported engineering workflow works; it is not a general accuracy estimate.

The exact submitted check specification was:

```json
{
  "obligation_id": "amu/services/impact.py#_boundaries:obligation:2a2b1f1b2f840ba9",
  "invocation": {
    "kind": "ast-boundary",
    "source_example": "def target(callback):\n    return callback()\n"
  },
  "expectation": {
    "basis": "user-supplied-task-requirement",
    "reference": "A call through a function parameter should be reported as an unresolved callback boundary within the implemented supported scope.",
    "predicate": "unresolved-boundary",
    "value": true
  },
  "intent": "bug-fix"
}
```

The returned draft body, unchanged in both isolated runs, was:

```python
import ast
from amu.services.impact import _boundaries

def test_proposed_check():
    node = ast.parse('def target(callback):\n    return callback()\n').body[0]
    rows = _boundaries(node)
    assert any(row.get('line') == 2 and 'unresolved' in row.get('reason', '') for row in rows)
```
