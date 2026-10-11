# Current report format

The current packet schema is 5. See [role separation and migration](phase3-report-migration.md). The Phase 3 launch description below records schema 4 behavior.

# Phase 3: behavioral patch inspection

AMU can now turn supported Python edits into **verification obligations**: a changed expression, its before and after spans, a possible observable, a justified check target, and explicit analysis limits. An obligation says a behavior deserves review; it does not label a regression. Test recommendations distinguish source paths from bounded input and assertion evidence. Static inspection never imports or executes the proposed code.

```sh
amu analyze-patch --repo /absolute/path/to/repo --base worktree \
  --patch-file /absolute/path/to/change.diff --json
amu analyze-patch --repo /absolute/path/to/repo --base worktree \
  --patch-file /absolute/path/to/change.diff --section obligations --json
amu analyze-patch --repo /absolute/path/to/repo --base worktree \
  --patch-file /absolute/path/to/change.diff --section recommended_checks --json
```

The default summary previews the first obligations and ranked existing checks, with omitted counts. Each obligation has a `verification_plan`: baseline tests worth inspecting, candidate-added test files separately, a proposed boundary input when source reasoning supports one, what to observe, the basis of any expectation, and `execution_status`. The default expectation is an **unresolved hypothesis** until an independent protected contract or explicit specification supplies one. Running general project tests remains a separate authorized action.

The MCP `amu_analyze_patch` tool uses the same `section` values and cursor paging, with the existing repository grant and explicit patch-analysis startup opt-in. `obligations` is a new detail section. Every detail section remains completely retrievable through `retrieval[section].cursor` and `page.next_cursor`; changing source, tests, Git identity, or analysis evidence rejects a stale cursor.

## Contract migration

`concrete-patch-impact` packet `schema_version` is **4**, and the opted-in MCP manifest is **amu-inspection-v4**. Version 3 clients can continue reading their known fields, but should handle the new `obligations` section, `summary.obligations`, `summary.omitted_obligations`, and behavioral evidence on recommendation associations. The configuration identity now records `python-markdown-concrete-patch-v3` and `behavioral-test-evidence-v3`; previously issued cursors cannot be reused. Existing `findings` and other detail sections remain available. The MCP tool arguments add the `obligations` section choice; its authorization and execution boundaries are unchanged.

Supported static patterns are deliberately narrow: simple changed comparisons (including returned predicates), return/sentinel and exception expressions, shared-state declarations and cache accesses, simple subscript keys with stable local definitions, simple defaults, and scalar literal test calls with straightforward assertions. Type annotations do not become mapping-key obligations. Constructed local instances, plain `self` or `cls` state reads, and stable local test aliases are resolved only when baseline source establishes their identity. The report leaves dynamic dispatch, rebinding, inheritance, descriptors, fixtures, mocks, temporary receivers, and longer alias/wrapper chains unresolved. Mapping inputs and repeated state or identity sequences are not interpreted as proved behavioral coverage. A test call without an assertion about the changed observable stays a broad candidate. An enclosing guard match does not prove that earlier branches permit the call to reach the edited expression.

For reproducible execution evidence, see the reviewed [Phase 3 counterexamples](../amu/benchmark_data/phase3-counterexamples-v1/README.md). Those checks exercise only hash-pinned AMU-owned fixtures and do not authorize execution of arbitrary repository code.

The [Phase 3 results](PHASE3-RESULTS.md) separate development selection gains, unchanged holdout selection, reviewed execution evidence, installed cost and remaining misses.
