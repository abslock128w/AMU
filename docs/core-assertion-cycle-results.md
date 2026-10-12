# AMU assertion correction and performance cycle

2026-10-12. Isolated checkout: `core-assertion-cycle-2026-10-12`, branch `accuracy/assertion-performance`, starting exactly at `abfb905fde093929e0be4a86350c43a6a07974c7`. The original checkout was clean. The final commit and wheel hashes are recorded in `commit-receipt.json` and `build-identity.json`. Original builds, inputs, labels and receipts remain preserved.

## Recommendation

**Accept the narrow assertion-reporting correction and request-local syntax optimization; retain the documented semantic limits.** More result observations are now supported, with no newly promoted labeled negative and a measured 47.4% reduction in median initial request time against `abfb905`. This is not a general regression-detection result. The old loop-exit behavior and two fresh production relationships remain unsupported; no new expansion or tuning was performed after opening fresh results. The stronger goal of proving assertions verify the changed loop remains unmet.

## Each original miss, traced

Both labels name existing assertions correctly under the original function-result-observation rubric. No label correction was necessary. The original score remains **0/2** and is never overwritten.

| Stage | `test_parse_options_header` | `test_parse_options_header_empty` |
|---|---|---|
| Changed declaration | `src/werkzeug/http.py:454`, `parse_options_header`; added while-else break after old line 552 | Same declaration |
| Asserted value | Direct call result compared with `("v", expect)` at `tests/test_http.py:403` | Direct call result compared with `(expect, {})` at `tests/test_http.py:364` |
| Intermediate binding | The `value` and `expect` test parameters; no assigned call-result local | Same pattern |
| Resolution | Existing direct-import association resolves to the exact source function | Same |
| Flow defect | Generic decorator rejection excludes the parametrized body from value-flow observers | Same |
| Association defect | Assertion attachment is evaluated only for an obligation; the changed break emits none | Same |
| Reporting/scorer | No standalone result-observation field; rank 4 and empty obligation evidence make the old scorer count no link | Same |
| Corrected evidence | Located call-to-assertion path, independent of obligation evidence | Same; empty cases return before the loop |

The source caller is `Request._parse_content_type` at `src/werkzeug/sansio/request.py:328–332`, assigning the parse result. This establishes a call reference; arbitrary property/state propagation remains outside the model. The source has a `None` early return at lines 511–512 and a value/rest guard at lines 518–520. These show why the empty-input test is an API result assertion rather than demonstrated protection for the changed loop. The other test contains malformed quoted inputs, but static analysis did not establish execution of the new break or detection of a regression.

`diagnosis.json` preserves exact before-source locations and raw associations. The unchanged installed report hash was reproduced and matched the historical receipt. Full fragments were reconstructed before inspection. The old scorer's per-obligation measure is valid for what it counts; presenting it as all result-assertion evidence was too coarse. Corrected scores therefore keep two measures distinct:

- Original/strict obligation-attached score: **0/2 → 0/2**. No loop obligation was fabricated.
- Located function-result observations: **0/2 → 2/2**. No changed-input, execution or regression-detection claim accompanies them.

## What changed

Known, unshadowed `pytest.mark.parametrize` bodies are inspected when their marker metadata is bounded literal data, including tuple parameter names and literal string concatenation/repetition. Target modules, decorators and fixtures are never executed. Arbitrary wrappers, dynamic metadata, indirect fixtures and shadowed class/module marker names remain unresolved. Extra observers have a separate 512-test budget and do not displace the original 1,024-function summary universe.

Associations now expose `result_assertion` with source/call/assertion locations and guard constraints, independently of `obligation_evidence`. Summaries identify a related result assertion separately from behavioral evidence. Behavioral rank is not promoted when the changed-operation obligation is absent, and the verification gap now identifies that missing link instead of claiming no result assertion exists.

Focused negatives exposed two previous overconfident observations: an alias passed to an opaque call, and a call hidden in a short-circuited expression. Those stop conservatively; branch-dependent joins explicitly report uncertainty. Unrelated fields/values, reassignment, guards/handlers and dynamic dispatch are covered. There is no new parser, graph engine, language, drafting pattern, dashboard or AI trial.

## Profile and optimization

The unchanged candidate's `execute_patch` profile made 60.24 million calls: 8,192 flow scans, 17,041 binding walks and 8,497 dynamic-namespace walks. Instrumented cumulative flow time was 17.69 s of 20.65 s total; binding and dynamic-namespace work overlap in the AST traversal and must not be added. Uncertainty handling also repeatedly scanned the same 200 records/limit marker, including 7.29 million limit-prefix checks. Parsing, secure source inventory, import resolution and serialization were smaller costs.

The largest demonstrated avoidable cost was removed by reusing immutable binding/dynamic-namespace facts within a request, keyed by the already-parsed AST object. Local binding sets are copied before mutation. Symbol resolution and dependency-sensitive summaries remain freshly computed in each phase. A bounded hash set and one limit flag preserve the original uncertainty order/cap marker without repeated linear scans.

The performance-only prototype produced the **same complete logical packet, uncertainty and resource-limit evidence** as the uncached implementation. Its binding walks fell to 2,759 and dynamic walks to 1,356; total profile calls fell to 17.06 million. Instrumented times are diagnostic, not CLI latency comparisons. `profile-stages.json` provides parsing, inventory, resolution, flow/composition and serialization measurements with overlap caveats. Remaining parsing, full source/dependency checks and finite semantic passes were not pruned.

There is **no cross-request parsed-source or summary cache**. Each CLI/MCP request rereads and reparses source, rebuilds imports and summaries, and rechecks source/dependency inventories, configuration and analyzer version. Tests prove dependency/config changes invalidate cursors when a consumer is unchanged. Thus repeat requests do not obtain persistent-cache speedups or silently stale evidence.

## Frozen cases and results

Three fresh historical Werkzeug changes were source-selected/labeled before implementation or AMU output: `c96267b7` (starts/ends-with), `b5ca9067` (partition instead of split), and `5b0dc6d6` (invalid ETag values). The same assistant selected and labeled them; this is **not independent validation**. Selection was informed by known model scope. Baseline/corrected outputs were sealed until the frozen candidate passed validation. `fresh-first-inspection.json` records gaps before any subsequent work; no implementation tuning followed.

| Measure | Unchanged `abfb905` | Corrected | Meaning |
|---|---:|---:|---|
| Previous development source relationships | 6/6 | 6/6 | All predeclared pairs retained |
| Previous development result assertion IDs | 4/6 | 6/6 | Includes the now-exposed old holdout |
| Previous negative assertions promoted | 0/7 | 0/7 | Lower is better |
| Previous negative relationships promoted | 0/2 | 0/2 | Lower is better |
| Fresh result assertion IDs | 2/5 | 4/5 | Located observations, not demonstrated detection |
| Fresh obligation-attached assertions | 2/5 | 2/5 | Kept separate |
| Fresh negative assertions promoted | 0/3 | 0/3 | Lower is better |
| Fresh source relationships | 0/2 | 0/2 | Both remain unresolved |
| Fresh negative relationships promoted | 0/3 | 0/3 | Lower is better |
| Fresh selected entities | 5/6 | 5/6 | Overloaded unquote_etag remains ambiguous |

All 23 selected projections were processed with complete evidence retrieval. Counts describe these labels only; no overall precision percentage is justified. Source review confirms valid assertions at the fresh `test_dump_options_header`, `test_dump_header`, `TestRange.test_range_parsing`, and `TestRange.test_content_range_parsing` locations. The last has an observing `is None` assertion despite other later variable reassignments; location-sensitive flow retains that observation.

Remaining fresh gaps: `_unquote_etag`/`unquote_etag` overloaded declarations cannot be resolved as one callable, so `TestRange.test_if_range_parsing` stays unestablished. `unquote_header_value → parse_dict_header` is beyond the current control-flow/container traversal boundary. Two **unlabeled** old source pairs from `is_resource_modified` to `Response._is_range_request_processable` and `Response.make_conditional` were removed because they sit inside unsupported short-circuit expressions (lines 643–650 and 765–769). Their source syntax remains inspectable, but the previous unconstrained value path was overconfident. This is an explicit conservative correction, not an optimization scope reduction. All source/observation pairs in the previous 20-case replay were retained.

## Comparable performance

207 sequential CLI calls: 23 identical frozen cases × three builds × three repetitions, rotating build order. Same Python 3.14.6 and reviewed dependency paths; graph/models disabled; no concurrent evaluation jobs. First requests are fresh-process imports, not flushed OS caches; repeats are also fresh processes. Peak memory is child maximum RSS from an isolated parent's `getrusage(RUSAGE_CHILDREN)`, Darwin bytes; benchmark-parent RSS is excluded. Dispersion/min/max are in the raw receipts and summary.

| Controlled median | Frozen `228c87e` | Unchanged `abfb905` | Corrected |
|---|---:|---:|---:|
| Initial process | 1.547 s | 6.994 s | 3.681 s |
| Repeated process | 1.325 s | 7.216 s | 3.543 s |
| Peak child RSS | 64.7 MiB | 93.6 MiB | 94.2 MiB |
| Compact summary | 12,597 B | 13,281 B | 13,350 B |

The corrected initial median remains 2.4× the frozen baseline: broader source analysis still has an unavoidable cost in this design. The earlier 0.389/2.203 s and 64.4/93.6 MiB figures are historical, not substituted for these same-run measurements. Full retrieval grew slightly, rather than losing evidence: previous cases 11,436,471 → 11,554,200 bytes; fresh cases 4,374,373 → 4,477,983 bytes. Those are audit packet totals including repeated metadata, not one logical report's size.

Real MCP sessions also showed stable repeated report identities and matching installed CLI output. One persistent session per build sent three identical patch requests. Initialization was measured separately; each tool call starts an isolated CLI. Requests were 9.601, 10.553, 11.419 s unchanged and 3.925, 4.682, 5.578 s corrected. Those parity-session timings overlap other validation work and are supporting observations, not the controlled performance claim; memory metrics are not mixed with CLI child RSS.

## Validation, identity and reproduction

Final exact candidate: **2,457 tests passed, 14 skipped**, one final full-suite run. Focused checks: **145 passed**. Regression-before receipts establish the missing parameterized observation and the alias/short-circuit false links before correction. Lint, whitespace checks, wheel build, installed source-byte equality, and real MCP initialize/list/three-call/CLI parity passed. No external repository code/tests/generated drafts or symbolic/mutation trials ran; AMU's reviewed self-contained test fixtures were permitted. No push, publication or deployment occurred.

Receipts: `starting-identity.json`, `implementation-freeze.json`, `build-identity.json`, `diagnosis.json`, `regressions-before.txt`, `final-focused.txt`, `final-suite.txt`, `final-lint.txt`, `mcp-corrected.json`, `profile-stages.json`, `development-scores.json`, `fresh-scores.json`, `fresh-first-inspection.json`, `performance/summary.json`, and `preservation.json`. Per-case directories retain raw outputs, environments, complete reconstructed fragments and hashes. The original score and all labels remain untouched. No receipts became stale after the final freeze.

```sh
AMU_CYCLE=/Users/Abslock128/Documents/ChatGPT/AMU/core-assertion-evaluation-2026-10-12
AMU_SOURCE=/Users/Abslock128/Documents/ChatGPT/AMU/core-assertion-cycle-2026-10-12
AMU_PYTHON="$AMU_CYCLE/installed/bin/python"
cd "$AMU_SOURCE"
"$AMU_PYTHON" -m pytest -q
"$AMU_PYTHON" -I "$AMU_CYCLE/mcp_check.py" parity-replay
"$AMU_PYTHON" "$AMU_CYCLE/run.py" --python "$AMU_PYTHON" --output development-replay --split development --workers 2
"$AMU_PYTHON" "$AMU_CYCLE/run.py" --python "$AMU_PYTHON" --output fresh-replay --split fresh
"$AMU_PYTHON" "$AMU_CYCLE/performance.py" --output performance-replay
```

Replay directories are create-only. The fresh set is now exposed; a replay cannot be called untouched validation. The benchmark helper isolates each CLI process to measure comparable child RSS; the profile files have instrumentation overhead and are not latency receipts.
