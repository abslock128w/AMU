# Improvement status

Updated **11 October 2026**. The local Phase 2 development candidate is
`5655355c17ddef95e6dfa08dfd27cdf6272dc119`. Its package-import test
discovery defect is **closed locally**: the validated correction restores
supported paths through an inert literal-version initializer while rejecting
conflicting bindings. See the [quickstart](phase2-quickstart.md) and
[diagnostic evidence](../dist/git-enumeration-maintenance-2026-10-11/discovery-diagnosis/DIAGNOSIS.md).
Recommendations remain bounded source relationships, not executed coverage or
proof of an assertion's protection. Dynamic command/subprocess dispatch and
candidate-added tests remain outside baseline discovery. External graph checks
and environments represented by skipped tests remain unverified. Phase 1
external acceptance remains **PARTIAL**; this local candidate has not been
published or independently accepted for general production use.

The 10 October Phase 1 acceptance snapshot and September improvement ledger
below remain historical; their item states do not replace acceptance checks.

## Phase 1 replacement candidate (10 October snapshot)

The response-boundary repair belongs to the separate replacement candidate
`dist/phase1-wire-2026-10-10.EHpaIc`. Its external
[acceptance report](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-REPORT.md) and
[machine-readable evidence](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-EVIDENCE.json)
are the authority for final artifact/package identities, executed tests, protocol
receipts and remaining gates. These sidecars are outside the source archive:
source documentation cannot embed the hash of an artifact that contains itself.
Keep the candidate bundle and its evidence together when distributing it.

At this documentation snapshot, replacement full-suite, fresh-install and
independent wire checks have not yet supplied their final receipts. A candidate
directory or a test result from another build is not acceptance. Read the
replacement sidecars for the subsequently executed results; do not reuse the
earlier candidate's counts as replacement evidence.

The previous frozen candidate, `dist/phase1-candidate-2026-10-10.AHBtS2`, remains
unchanged. Its **2,016 passed / 12 skipped** full-suite results on Python 3.11.16
and 3.14.6, and the acceptance audit's **267** focused tests and 12-call native
inspection, identify that earlier build only. The audit independently reproduced
a **150,297-byte** response for a 150,000-byte request ID, above the declared
**131,072-byte** allowance. Handler budgeting alone did not close the SDK/stdio
boundary. That candidate must not be promoted as having passed the repaired
wire contract. See [MCP security](MCP-SECURITY.md) for the replacement contract.

The replacement's local checks must establish the unchanged four-tool manifest
(`amu_search`, `amu_graph`, `amu_impact`, `amu_docs_reviews`), matching source and
installed payload identities, truthful doctor exits, exact-root authorization,
complete hash-bound evidence paging and actual emitted-byte limits—including
SDK validation and exceptional responses. Passing a diagnostic with an explicit
unknown-freshness result is not a claim that the repository is ready.

Two external acceptance requirements remain **NOT EXECUTED**: an explicitly
authorized official NumPy checkout/index and exact-root grant, and a reversible
live ChatGPT MCP cutover followed by actual connected-client four-tool checks.
Neither is authorized by these local repairs. The old live pilot, permissions,
credentials and deployment remain separate from the new local candidate.
Historical NumPy examples, a local SDK run or a healthy tunnel cannot fill these
gates. See [release readiness](RELEASE-READINESS.md).

## 9 October 2026 status (historical)

Updated 9 October 2026. This tracks the 31-item September review. “Partial”
means the implemented safeguards are useful, but the full proposal is not done.
Passing tests are not a runtime-impact guarantee or an accuracy benchmark.

Latest frozen-source runs: **1,739 passed, 11 skipped on each of Python 3.14.6
and 3.11.16**, both on macOS 27.0.1 arm64 with pinned development dependencies.
The 3.11 runtime was obtained in an isolated temporary directory, not installed
globally. The exact frozen candidate also passed **1,739 tests with 11 opt-in
skips on each of Ubuntu 24.04.5 x64/Python 3.11.17 and 3.14.8**, plus all **30
Chromium desktop/mobile checks**, in private Linux CI. Both initial and
Node24-native-action confirmation runs passed; clean rebuilt/original frozen
wheel and installed MCP checks passed on both versions. See
[Linux evidence](evidence/github-linux-ci-2026-10-09.json). Windows, other Linux
architectures/distributions and live-client integration remain unverified.
Focused modules pass lint checks and
the diff passes whitespace checks. **30 desktop/mobile browser executions passed**.
The installed candidate passed base-package and real MCP stdio smoke checks,
**38 documentation recovery tests**, and a **52-step real-engine operator drill**
including current completion after reviewed docs, graph refresh, undo and
changed-document refusal. The source archive now carries its test/support/browser
files and passes bounded completeness checks. See [release readiness](RELEASE-READINESS.md)
for the candidate record, supported pilot scope and unresolved release gates.
The exact engine is still a custom `dev` build, not proof for an automatically
downloaded release. Earlier four-provider-refresh and curated NumPy records remain
historical; NumPy was not re-audited in this batch. No live model calls or
compiled NumPy numerical tests were performed.

## 9 October implementation changes (historical)

- Private manual Linux CI is now executed, not merely configured. The exact
  archive and all runtime files are checked before execution; a red-first pin
  regression and official source review guided the CI action-runtime update.
  Only three exact official action commits were allowed. The existing $0 Actions
  Stop usage budget was verified and left unchanged; CI was disabled again after
  both runs. Provider provenance now matches the installed dispatcher to official
  release bytes and traces the graph's source commit, without implying graph
  executable authentication. See [provider evidence](evidence/engine-provenance-2026-10-09.json).
- MCP access defaults are now fail-closed: explicit exact-root operator grants,
  inspection-only discovery and action opt-in, one active call, decoded-argument
  and callback-result budgets, safe exception responses and argument-free audit
  logs. Model discovery/client creation/outbound preparation can be disabled
  before credentials are loaded; every MCP CLI child enforces that switch and
  removes common sensitive/injection environment settings. See
  [local MCP security](MCP-SECURITY.md). These are local trusted-process bounds,
  not a public-service authentication layer or filesystem sandbox.
- Verified-engine installer mode checks two supplied binary digests, effective
  managed-plugin dispatch and preflight, then rechecks after install/doctor;
  it never auto-fetches an engine in this mode. Pins establish byte identity,
  not provenance or whole-repository semantic correctness.
- Declared test/build runners now share bounded stream capture. Four reproduced
  overflow false-greens are rejected; timeout/overflow cannot supply a partial
  passing pytest summary. The installed real-engine CLI workflow and a new
  **16-call MCP workflow** both passed, including current completion after a
  reviewed doc save. No live AI-client authentication/model calls are claimed.

- Release-readiness safeguards: all-skipped/xfail-only/collection-only or hidden
  pytest summaries cannot advance a phase; actual passing counts are recorded.
  Real MCP stdio validation rejects coerced consent and boolean indexes without
  adding optional SDK dependencies to base imports. Explicit repeatable
  `amu plan --docs-file` scope lets reviewed prose complete the same workflow
  without a manual contract patch or silent scope expansion. Archives include
  complete fixtures/scripts/viewers/licenses; clean installed probes are CI gates.
- Viewer hardening preserves the existing design while adding bounded byte/depth/
  schema validation, file-read race protection, previous-result labels and a complete
  text fallback for large graphs. D3 is local with its third-party notice and CSP;
  approval remains unavailable in the static pages. See
  [viewer verification](VIEWER-ACCESSIBILITY.md).

- Shared graph mapping/snapshot services: stable live collection, exact semantic
  baselines, source checks, persistence and HEAD/worktree/cache decisions moved
  out of CLI bodies. Existing adapters preserve behavior, including failed
  inventory/snapshot records and pre-edit freshness. Interactive `/map` now shares
  the CLI parser; legacy inferred provider paths receive a checked repo boundary.
  Direct/CLI/interactive/MCP parity and real source/Git race regressions are covered.
  See [application services](APPLICATION-SERVICES.md).

- Shared documentation application service: candidate/list/prepare/apply,
  review queue, recoverable saves/undo and docs-graph orchestration moved out of
  CLI callbacks. Immutable requests, explicit repository selection and typed
  errors serve the existing CLI/interactive/MCP path. Relative CLI paths remain
  supported; MCP consent and subprocess bounds remain unchanged.
  Reproduced state-error defects now preserve known saves after derived metadata
  failure, and return stopped partial auto-operation results without calling an
  unconfirmed operation definitely unsaved. See
  [application services](APPLICATION-SERVICES.md).

- Graph subprocess bounds and streamed snapshots: byte limits are enforced
  during receipt, stdout/stderr are drained together, and timeout/overflow cannot
  become partial JSON success. Snapshots validate one bounded record at a time
  before atomic publication. Failed syncs retain prior capture metadata separately.
  Chat/MCP child commands, completion reads and cache checks are bounded too;
  inventory failures no longer masquerade as empty lookup results. See
  [graph resource safeguards](GRAPH-RESOURCES.md).
  The pinned NumPy size check accepted 29,103 inventory records and a
  180,211-record snapshot. Snapshot-call traced Python peak was 874,515 bytes,
  excluding imports/native allocations/engine memory; see the evidence limits.

- Public API review signals: Python declarations/exports/re-exports/aliases,
  overloads, defaults and static behavior facets now feed manual docs reviews.
  Mapped JSON/TOML configuration keys are also supported. Review identities bind
  raw hashes even when displayed defaults are redacted. Unsupported/dynamic
  constructs remain unknown; automatic prose-write authority is unchanged.
  See [API detection and accuracy](API-ACCURACY.md).
- Versioned accuracy fixtures now report misses, false alarms, abstentions and
  independent raw-provider/source-validated coverage. The small 20-case Python
  corpus is a regression benchmark, not an estimate of NumPy-wide accuracy.
  A fresh pinned NumPy audit also checks direct same/cross-file calls, native
  boundary limits and API-to-reviewed-RST saving.
  It exposed source-checker defects in absolute imports and re-export handling;
  regression fixes preserve uncertainty instead of deleting unproved real calls.

- State validation and transactions: nested critical records are validated on
  reads/writes, JSON access uses bounded pinned descriptors and persistent process
  locks, and harness IDs/budgets are serialized with durable per-task counters.
  Navigation also uses whole read/update/write transactions. See
  [state recovery](STATE-RECOVERY.md).

- Separate code/docs revisions now retain code relationships across uncommitted
  prose edits, while full test/scope checks still expire. Docs graphs/listings and
  documentation answers bind their own revision. Completion can reuse a source
  snapshot only with matching code/configuration/provider/depth/checksum evidence.
  See [freshness behavior](FRESHNESS.md).

- Ranked model context and session bounds: the planner/chat share relevance
  selection with omission counts. Requests include prompt/protocol overhead in
  their byte allowance; chat compacts whole completed turns, enforces cumulative
  limits, and rolls back interrupted rounds. See [model context](MODEL-CONTEXT.md).
- Provider bounds: 60-second operations, 4 MiB response ceilings, typed errors,
  disabled SDK retries and one bounded planner retry for transient failures.
  Mocked SDK transport tests now cover the pinned dependency version.
- Graph-refresh correctness: executable examples/configuration under docs_dir,
  MDX and dependency/build inputs no longer bypass graph coverage checks.

- Documentation review queue: independent target selection, preview/status,
  recoverable discard and reprepare, CLI/REPL support, and MCP review tools with
  explicit save/discard/undo confirmation. See [the guide](DOC-REVIEWS.md).
  Apply/prepare/discard now share a lifecycle lock; draft reads use bounded,
  no-follow file descriptors; interrupted preparation/archive operations preserve
  developer edits, and saved drafts edited later show an accurate status.

- Python argument-binding assessment: `amu compatibility`, also reported during
  signature refreshes. Compares current supplied callers against the old Git
  signature and the current declaration without importing project code.
- Dependency-based phase ordering: only observed, resolved, direct dependencies
  constrain order. Cycles are explicit groups. Missing per-symbol coverage and
  excluded edges are disclosed; graph distance alone never proves a leaf.
- Isolated map targets: zero edges can be accepted with exact focus, complete
  sections, explicit complete coverage and no reported partial failures.
- Documentation identity: missing fragments stay unresolved, same-page anchors
  work, ambiguous short names are reported, and different modules' same-named
  functions cannot satisfy each other's references.
- Model boundaries: strict planner/tool-input schemas; malformed planner output
  cannot fall back to “proceed”; failed/exhausted chat rounds restore prior
  completed history. Model suggestions still require human confirmation.
- State/workspace: existing corrupt JSON is not missing/default state; supported
  versions and key field types are checked; workspace and memory mutations use
  locked read-modify-write operations. CLI state errors are structured.
- Memory: forgetting survives regeneration, pins and creation times persist,
  repeated refresh cannot erase stale evidence, and timestamp-only changes no
  longer rewrite the managed instruction block.
- Watcher: reads the latest contract/map for every event batch. Uses native
  notifications with periodic reconciliation for hosts that silently miss
  events; fallback scans prune dependency/cache directories and include control
  files without feeding generated state back into the watcher.

## Try the new compatibility report

After editing a Python function's signature, with its old version committed:

```sh
amu compatibility --symbol pkg/api.py#function \
  --caller pkg/client.py#caller --base HEAD --json
```

Repeat `--caller` for each caller scope. Exit 1 means at least one current call
does not bind; exit 2 means unresolved analysis without a proven invalid binding.
Exit 0 describes only the supplied static call sites, not whole-program safety.
The `newly_invalid` field compares each **current** call against both signatures;
it is not a comparison of old and new caller implementations.

Supported: undecorated module-level Python targets, direct calls, static import
aliases, positional/keyword/default/keyword-only arguments. Methods/descriptors,
decorated or rebound targets, ambiguous imports, dynamic argument expansion,
unsupported source and missing coverage remain unknown. Limits: 100 supplied
caller scopes, 1,000 assessed calls, 2 MB per source file.

`amu refresh --symbol pkg/api.py#function --change signature --json` includes the
same report for direct callers in its pre/post graph evidence. A complete graph
refresh means relationships were compared—not that every call binds or runtime
behavior is compatible. Review its separate `compatibility` field and run tests.

## Audit ledger

| Item | State | Remaining scope |
|---|---|---|
| 1. REPL dispatch | Immediate defect fixed | Shared services tracked under 19 |
| 2. Positive phase evidence | Implemented | Continue regression coverage |
| 3. Unreadable-source freshness | Implemented | Continue regression coverage |
| 4. Answer provenance/binding | Implemented | Human confirmation remains mandatory |
| 5. Relation confidence vs compatibility | Partial | Binding checker is deliberately narrow; return/type/behavior/export contracts remain |
| 6. Dependency ordering | Partial | SCC ordering implemented; root-focused graph queries do not provide every peer's dependency coverage |
| 7. Regional coverage | Partial | Exact isolated-target rule implemented; region-specific partial-failure attribution and dynamic-boundary modeling remain |
| 8. Document structure | Implemented for supported formats | Ambiguous/unsupported document constructs remain manual |
| 9. Recoverable document saves | Implemented | Journaled saves and guarded undo; graph refresh reported separately |
| 10. Precise docs identity | Partial | Broken/local anchors and ambiguous names fixed; richer Sphinx/generated-page/alias resolution remains |
| 11. Complete review queue | Partial | Independent targets, preview/status, recoverable discard/reprepare implemented; conflict rebasing remains deliberately manual |
| 12. Public API manifests | Partial | Bounded Python and mapped JSON/TOML implemented; other-language adapters, inter-module API propagation and deeper behavior contracts remain |
| 13. Ranked context slice | Implemented | Shared ranking/omission budgeting in planner and chat; broader retrieval-quality benchmarks remain under 25 |
| 14. Outbound controls | Implemented | Redaction is best-effort, not a guarantee against all secrets |
| 15. Model/session resilience | Core safeguards implemented | Schemas, whole-turn compaction, cumulative request/byte counters, Ctrl-C rollback, bounded retry and transport limits; provider-specific cost/capability negotiation and total stream deadlines remain |
| 16. Scoped memory commits | Implemented | Preserve user staging and report failures |
| 17. Credential storage | Implemented | Restrictive creation, endpoint checks, diagnostics protection |
| 18. Typed transactional state | Core safeguards implemented | Nested critical shape checks, secure JSON access, durable handoff sequencing and per-task transactional budgets implemented; whole multi-file workflow transactions and history-retention policy remain |
| 19. Application-service split | Partial | Documentation, mapping and snapshot-sync orchestration extracted with parity checks; post-edit refresh, plan/check/done and navigation orchestration remain |
| 20. Durable memory | Implemented for reported defects | Tombstones intentionally retain suppression metadata, not secure erasure |
| 21. Code/docs freshness separation | Core workflow implemented | Versioned domains and guarded source reuse implemented; HEAD remains strict, including doc-only commits. Full test/scope proof remains required |
| 22. Caching and timing | Open | Content-keyed caches and measurement |
| 23. Resource bounds/watcher | Core graph/declared-runner safeguards implemented | Live byte/deadline limits, streamed snapshots, bounded wrapper/cache/completion reads and declared test/build output/deadline checks implemented; engine RAM/CPU, remaining subprocess callers and Windows descendant teardown remain |
| 24. Release CI/entry-point tests | Partial | Private Linux Python3.11/3.14 and clean installed-wheel/MCP checks passed, with 30 Chromium checks; the original full remote matrix and broader property/browser verification remain |
| 25. Accuracy benchmarks | Partial | Versioned API/relation fixtures and separate scorecards implemented; fresh focused NumPy audit, but broader language/repository and retrieval-quality benchmarks remain |
| 26. Reproducible distribution | Partial | Pins, exact archive/wheel identity, installed smokes and supplied-engine digest validation implemented; graph clean-build authentication, project license/release decisions and publication remain |
| 27. Accessibility | Partial | 30 Chromium desktop/mobile executions passed locally and in private Linux CI, including automated accessibility checks; other browsers and assistive-technology testing remain |
| 28. Viewer hardening | Core safeguards implemented | Bounded input/depth/schema checks, offline licensed assets, search and complete large-graph text fallback implemented and browser-tested; wider-browser/performance validation remains |
| 29. Onboarding/docs | Partial | Existing guides plus this current ledger; guided first run and unified demo remain |
| 30. MCP documentation parity | Core workflow implemented | Listing, prepare, preview, discard, reviewed save, graph and save/undo journals; prose editing uses the returned local file |
| 31. Build/runtime backends | Decision needed | Choose supported backend, licensing and evidence contract before integration |

## Next priority

Finish the replacement candidate's local Phase 1 checks and retain their actual
receipts. Then obtain separate authorization for the official NumPy fixture and
reversible ChatGPT cutover; execute those external checks with the accepted
installed candidate and actual connected client. **Do not begin Phase 2** or
declare Phase 1 complete while either gate remains unexecuted.

The eleven skipped external tests in the 9 October record are historical. The
replacement report must list its own skips and separately executed opt-in checks.
A passing suite with skipped integrations does not establish current NumPy
accuracy, live-client behavior, live model-provider behavior or broader platform
certification. Graph executable provenance, licensing/publication decisions and
the wider roadmap remain distinct from the scoped Phase 1 acceptance work.
