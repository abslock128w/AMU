# Input suggestions from changed expressions: schema 8

`amu analyze-patch` and MCP `amu_analyze_patch` now attach a few input probes to supported source obligations, with production probes prioritized. The packet is schema **8**, the opted-in patch manifest is `amu-inspection-v8`, and the source-analysis identity is `python-markdown-concrete-patch-v7`. The recommendation-evidence identity remains `behavioral-test-evidence-v6`. Start with a fresh summary after upgrading: schema-7 cursors and report hashes cannot be reused. The [schema-7 draft patterns](phase3-check-drafts-v7.md) remain supported, including manually supplied scalar arguments.

AMU derives probes only from paired, changed Python expressions. It parses source; it never imports, evaluates, or runs target code during inspection. A local predicate difference is **not** a whole-function result, a regression, or proof that the expression is reached. Every suggestion has an exact before/after source span and AST hash, derivation, parameter binding, local observation under stated assumptions, reachability status, whole-function observable reference, and `expected_result: "unknown"`.

The first rules are deliberately narrow:

- **Integer boundaries:** one direct name and one integer literal, in either operand order, with `<`, `<=`, `>`, or `>=` in both versions. AMU normalizes reversed operands, proposes each old/new threshold and its immediate integer neighbors, deduplicates values while retaining both derivation reasons, and ranks predicate-distinguishing values first. It accepts integer literals with absolute value at most one billion; Python booleans, floats, chained comparisons, and symbolic thresholds are outside this rule. A direct `int` annotation is type intent, not proof of runtime type. Unannotated integer probes are explicitly type-uncertain, and all local outcomes assume a built-in `int`, not `bool` or an overloaded type.
- **`None` versus truthiness:** `x is None` or `x is not None` changes to `x` or `not x`, or the reverse, on the same direct name. AMU considers `None`, `0`, `False`, and `""`. Direct scalar annotations present in both versions filter values outside their declared intent; without such evidence the probes are type-uncertain explorations. Local observations describe only built-in scalar identity and truthiness. Calls such as `bool(x)`, custom truth methods, and arbitrary values are not modeled.
- **Literal mapping key:** a direct `data['key']` changes to `data.get('key')` (or the reverse) in a returned or direct condition expression, using the same name and a safe literal string key of at most 128 bytes. The `.get()` default may be omitted or explicitly `None`; other defaults are unsupported. AMU proposes key absent and present with `None`. When both signatures directly declare `dict[str, int]`, `dict[str, str]`, or `dict[str, bool]`, it also offers one type-intent representative scalar. These observations assume a built-in dictionary and only the stated key. Other required fields, API setup, subclass behavior, and nested content remain unresolved. All mapping probes are partial: the current draft templates accept scalar arguments, not mapping containers or key-presence assertions.

At most **five** suggestions are retained per obligation and **40** per report. Production and expression-distinguishing probes have priority; counts include omitted candidates. The summary previews only two ranked suggestions and links each to `obligations` detail. The detail holds complete derivations. A large detail item may be split into hash-bound fragments; retrieve every page and reconstruct all fragments before relying on it.

`probe_status` separates `expression-probe`, `partial-invocation`, and `complete-source-bound-invocation`. A complete scalar call requires the same established direct parameter in both versions, no assignment or shadowing of it, one stable plain top-level function target, a call accepted by both signatures, and no missing required argument or unsupported default. A method receiver, changed signature, rebinding, or missing argument lowers readiness or causes abstention. Reachability remains `unestablished` even for a complete call; an earlier return is named explicitly. Signature previews longer than 300 characters are marked truncated and retain a hash of the complete signature; full source identities remain bound to every selection. If no supported probe is derived, AMU leaves the obligation without input suggestions.

To use a complete probe, retrieve its `id` and submit an independent expectation. Omit literal `args` and `kwargs`; AMU resolves them from the selected hash-bound suggestion:

```json
{
  "obligation_id": "threshold.py#admits:obligation:<id>",
  "suggestion_id": "threshold.py#admits:obligation:<id>:input:<id>",
  "invocation": {"kind": "direct-return"},
  "expectation": {"basis": "user-supplied-task-requirement",
                  "reference": "The range requirement includes the boundary ten.",
                  "predicate": "equals", "value": true},
  "intent": "bug-fix"
}
```

The check specification selects an input; it does not turn the candidate's result into an oracle. An omitted expectation yields no assertion. A stale, omitted, partial, or mismatched suggestion ID is rejected. An explicit argument override is also rejected. The selected proposal links the suggestion and records its hash; the draft identity includes suggestion, patch, source, configuration, invocation, expectation, and draft hashes. Drafts remain `review-required` and `not-run` until separately reviewed execution.

The implementation-guiding predeclared cases are in [`tests/fixtures/input_suggestions_v8.json`](../tests/fixtures/input_suggestions_v8.json). The reviewed AMU-owned boundary and truthiness fixtures are in [`tests/fixtures/input_probe_execution`](../tests/fixtures/input_probe_execution). Their saved preparation, static analysis, complete generated drafts, review receipts, execution output, and evaluation are in the local sibling `input-suggestions-2026-10-11` evidence directory. These demonstrations show that AMU chose values from patches; the expectations were supplied separately.

See [local validation, reviewed executions, costs, and reproduction](phase3-input-validation-v8.md) for the measured evidence and its limits.
