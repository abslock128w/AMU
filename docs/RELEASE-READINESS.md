# Release readiness

Current assessment: **10 October 2026 — Phase 1 PARTIAL. Do not begin Phase 2.**
AMU remains a hardened alpha, not a generally production-ready release.

## Current Phase 1 candidate and evidence contract

The replacement candidate is `dist/phase1-wire-2026-10-10.EHpaIc`. Its external
[Phase 1 report](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-REPORT.md) and
[evidence receipt](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-EVIDENCE.json)
must identify the final archive, wheel, constraints, source/installed package
payloads, interpreter/SDK/provider builds, each execution and every omitted check.
They are not included in the source archive and must accompany the candidate
bundle. Final self-containing artifact hashes and post-freeze test totals belong
in those sidecars, not in a pre-build source document.

At this documentation capture, the replacement's full-suite and installed
protocol results are pending their actual executions. Read the sidecars for
the final local outcome. Do not infer acceptance from a candidate name, a shared
`0.2.0` version string, this checklist or another build's passing tests.

The prior frozen `dist/phase1-candidate-2026-10-10.AHBtS2` and all 9 October
release artifacts/evidence remain unchanged. The prior candidate's 2,016-pass
full suites and the later audit's 267 focused tests are historical relative to
this replacement. The audit reproduced its 150,297-byte response on an oversized
request ID; that result fails the declared 131,072-byte wire allowance. This
replacement must close that defect at the SDK/stdio boundary, not merely revise
an application estimate. See [MCP security](MCP-SECURITY.md).

| Phase 1 requirement | Required replacement evidence | Acceptance boundary |
| --- | --- | --- |
| Source and installed identity | Hash-bound source payload, archive/wheel/constraints and fresh installed payload comparison outside checkout imports | Package version alone is not identity; preserve the prior frozen bundle |
| Four-tool MCP parity | Actual installed `initialize`/`tools/list`, full approved schemas/annotations and manifest identity | Exactly `amu_search`, `amu_graph`, `amu_impact`, `amu_docs_reviews`; no default action tools |
| Doctor behavior | Required-health failures, unknown freshness and requested MCP mismatch produce truthful report/exit status | An available executable or indexed HEAD is not readiness |
| Response wire boundary | Protocol regressions and installed emitted-byte capture covering admitted identifiers, SDK-generated errors, exceptional paths and result overflow | Every emitted JSON-plus-newline frame is at most 131,072 bytes; no identifier rewriting or unmarked evidence loss |
| Evidence retrieval | Drain same-report pages/fragments, validate hashes/counts and reject stale cursors | Application overflow must not masquerade as complete empty impact |
| Repository authority and preservation | Exact-root refusal, source/docs/managed-state baselines and historical artifact comparison | No rejected-path access, grant expansion, live configuration/credential change or Phase 2 work |
| Full regressions and independent installed check | Both recorded supported Python suites, fresh installed base/optional-SDK checks and separate real MCP/native-provider execution | Record failures, skips and platform/provider limits; historical receipts are not new executions |
| Official NumPy integration | Separately approved checkout/preparation/root grant; current-source `finfo.__new__` / `_finfo_cache` evidence through the accepted candidate | **NOT EXECUTED**; historical NumPy checks are not substituted |
| Live ChatGPT integration | Separately approved reversible cutover, refreshed client metadata and actual connected-client calls to all four tools | **NOT EXECUTED**; local SDK success or tunnel health is insufficient |

The existing live pilot has not been upgraded by building a new local candidate.
The only proposed external changes are separately authorized, exact-root NumPy
preparation/access and a reversible tunnel-command cutover plus metadata refresh.
No action opt-in, new account permissions, credential change or permanent profile
edit is implied. The final replacement report must include the precise proposed
operations, preflight, verification and rollback, and must retain overall PARTIAL
status until both external acceptance checks actually pass.

The replacement's bounded stdio codec requires POSIX I/O. Windows startup is
explicitly unsupported, not a platform accepted through an unbounded fallback.
Historical Ubuntu checks below do not certify this changed codec on Linux; the
replacement sidecars must name the platforms/interpreters actually exercised.

## 9 October pilot assessment (historical)

Assessment date: **9 October 2026**. AMU is a hardened alpha, not a generally
production-ready release. This checklist defines a smaller, supervised local
CLI pilot; it does not certify every feature, repository, operating system or
graph relationship.

## Proposed pilot scope

- One developer operating a local, trusted Git repository on macOS arm64 with
  Python 3.14.6, the environment used by the recorded local checks.
- Deterministic CLI mapping, planning, post-edit refresh, checking and completion,
  plus explicit human-reviewed documentation saves and guarded undo.
- Python code and supported Markdown/RST documentation. Static JSON/TOML API
  manifests are review signals only; unsupported or dynamic constructs remain
  uncertain. Select a bounded task whose critical relationships can also be
  inspected independently.
- The exact graph-provider build must be recorded and approved for the pilot.
  A matching version string alone is insufficient for a provider reporting `dev`.
- Built-in model requests disabled for the pilot. Model assistance remains
  experimental pending a separately authorized live provider/model smoke test.
  MCP and the static viewers have their own gates below; passing CLI checks does
  not establish their readiness.

Do not apply this scope to untrusted repositories, unattended agent operation,
shared writable state, production deployment decisions or security-critical
changes. AMU's graph and documentation suggestions are evidence for review,
not proof of runtime impact or correctness.

## Existing evidence, not a new candidate certification

The [recorded graph-service verification](evidence/graph-services-2026-10-09.json)
reports **1,541 deterministic tests passed and 11 opt-in tests skipped** on macOS
arm64/Python 3.14.6, with dependencies matching `constraints.txt`. It also records
four separately executed real-provider refresh scenarios and an installed-wheel
smoke pass. These are historical results for the implementations identified in
that record, not results for every subsequent edit or a frozen pilot artifact.

The [dated NumPy audit](evidence/numpy-v2.3.0-relations-2026-10-08.json) uses NumPy
v2.3.0 at commit `0532af47d6a815298b7841de00bdbc547104b237`. Its four selected
positive Python call relationships were retained; three were independently
source-verified and one remained explicitly uncertain. The isolated-function
and docstring-negative cases expose coverage/native-boundary limits. This is a
curated sample with partial coverage and abstentions, not a NumPy-wide accuracy
estimate. NumPy was not imported, built or numerically tested.

The provider in those records was a custom graph engine reporting `dev`, with
SHA-256 `d6a07d96d5306e3aa5c2db6047dce7cf6cb72c19030cfb8ca37e2a283c39954e`.
The installer does **not** pin Entire or its graph plugin. Therefore these results
do not automatically transfer to the plugin a new installation downloads.

The historical records above do not establish the current frozen candidate.
No live model call or general platform certification is claimed. Historical
results must not be substituted for the current checks below.

A [private GitHub candidate snapshot](https://github.com/abslock128w/amu-production-check-2026-10-09)
has now been uploaded with exact archive/wheel identity verified. Its separate
manual-only Linux workflow has started after authenticated billing verification:
0 of 3,000 included Actions minutes used, and a $0 Actions budget with Stop usage
enabled. No spending settings changed. The
[first Linux run](https://github.com/abslock128w/amu-production-check-2026-10-09/actions/runs/37923792739)
passed **1,739 tests with 11 opt-in skips on each of Ubuntu 24.04.5 x64/Python
3.11.17 and 3.14.8**, plus **30 Chromium desktop/mobile tests**. Both environments
also passed rebuilt-wheel runtime identity, clean installed base/original frozen
wheel checks and actual installed MCP protocol smokes. See the
[Linux evidence receipt](evidence/github-linux-ci-2026-10-09.json). The
[confirmation run](https://github.com/abslock128w/amu-production-check-2026-10-09/actions/runs/37924972565)
also passed the same totals with newer pinned, Node24-native GitHub actions and
without the previous runtime warning; AMU artifact/runtime bytes did not change.
Repository Actions are verified disabled again. This establishes
these bounded Linux checks, not a fully production-ready product.

The [read-only provider provenance audit](evidence/engine-provenance-2026-10-09.json)
now matches the installed Entire dispatcher bytes to its official v0.10.5 release
archive. The graph engine's embedded module and exact source commit are traceable,
but its executable has not been authenticated against an upstream artifact or
reproduced from a clean build. That provider gate therefore remains open.

## 9 October frozen candidate verification (historical)

The [MCP-hardening candidate record](evidence/release-candidate-mcp-2026-10-09.json)
superseded the earlier candidate for that dated installation. It does not validate
the subsequent Phase 1 source changes. Frozen source passed
**1,739 tests with 11 external-integration skips on each of macOS arm64/Python
3.11.16 and 3.14.6**. Installed base/optional-MCP and documentation recovery
checks passed; a **52-step real-engine CLI drill** and a **16-call real-engine
MCP tool workflow** completed reviewed docs, current checks/completion and undo.
The MCP workflow used the pinned SDK, not an authenticated Claude/ChatGPT client.
See [MCP security](MCP-SECURITY.md) for the intentional startup compatibility
change and the verified-engine install option. Earlier results below remain
historical evidence; their old wheel must not be assumed to enforce new flags.

The [candidate evidence sidecar](evidence/release-candidate-2026-10-09.json)
records the exact artifact digests and environment. The checkout remains modified
and version `0.2.0` is unchanged: this is a local pilot candidate, not a Git-tagged
or published release. The source archive captures the package/test/support inputs;
candidate hash sidecars are deliberately kept outside it.

- **1,644 source regression tests passed; 11 opt-in integration cases skipped**
  on macOS 27.0.1 arm64/Python 3.14.6 under the checked constraints.
- **30 desktop/mobile browser executions passed**, including axe checks, input
  bounds/schema, file loading races, missing diagram-library recovery and full
  text inspection of accepted large graphs. Local Chromium launch used approved
  access; it does not establish Firefox/WebKit or assistive-technology coverage.
- A wheel built from the source archive passed a fresh base-environment smoke,
  dependency/constraint checks and import without MCP/Pydantic installed.
- The installed optional SDK passed real MCP stdio initialization, listing, safe
  calls, required repository selection, strict confirmation/index inputs and
  clean shutdown. A boolean opt-in is not independent proof of human approval.
- **38 installed documentation recovery tests passed** with checkout imports
  excluded. A **52-step installed real-engine drill** completed the code loop,
  selected same/cross-file callers and isolated-function inspection, human-edited
  documentation save/graph update, stale completion refusal, current post-save
  check/completion, guarded undo and later-edit protection. It used temporary
  Git/HOME/cache state, explicitly hashed binaries and blocked model requests.
- The archive completeness/safety check passed with all 45 representative
  support files. Test helpers/fixtures, release scripts, docs, frozen browser
  assets/tests and the local D3 license are included; private/generated state is
  excluded. This is not a trusted-source signature or a project-license grant.

Documentation must be explicitly planned before edits, for example
`amu plan --docs-file docs/guide.md --approve`. Substitute an existing Markdown/RST
file under `docs_dir`; repeat the option as needed. Only these selected files
join the final phase. A save never silently expands the contract, and a fresh
check remains required after saving. The end-to-end probe no longer modifies
contract JSON to force this workflow through.

## Broader pilot gates (historical evidence, not replacement acceptance)

The current-boundary column below records the 9 October pilot evidence and wider
release requirements. It is not execution evidence for the Phase 1 replacement.
Use the current Phase 1 matrix and its external candidate sidecars above for that
decision. No historical platform, browser, NumPy or workflow result transfers
automatically to changed runtime bytes.

| Gate | Acceptance evidence | Current boundary |
| --- | --- | --- |
| Frozen candidate | Named source revision or captured source manifest; wheel, source archive and matching constraints hashes; exact Python/OS/provider identity | Local artifact digests and captured source archive recorded in the sidecar; no immutable Git release/version decision |
| Candidate installation | Build the wheel from the source archive, install it with the matching constraints in a fresh environment, run dependency checks and installed-package smoke outside checkout imports | Local Mac3.11/3.14 and private Ubuntu x64/Python3.11/3.14 suites plus installed base/optional MCP checks passed; other architectures/platforms remain unverified |
| Exact graph provider | Resolve the actual engine, record its digest/capabilities, and pass real refresh plus independently reviewed same-file/cross-file/isolated examples with that build | Exact dispatcher matches official release bytes; traceable custom graph build passed the tiny-fixture drill, but graph executable authentication/clean build and approved distribution remain unresolved |
| Full CLI edit loop | On a disposable repository, demonstrate pre-edit map → approved plan → source edit → refresh → project tests/check → completion, including reviewed docs and current graph evidence | 52-step installed drill passed with explicit docs scope and current post-save completion; not proof for every real repository |
| Recovery and concurrency | Demonstrate stale/conflicted-save refusal, guarded undo, corrupt-state refusal, interrupted-operation recovery and preservation of the prior snapshot | Installed recovery tests and disposable undo/conflict drill passed; each pilot operator must rehearse repository/package recovery |
| Pilot accuracy scope | Independently inspect critical relationships and expected documentation changes for the selected task; record misses, false alarms, uncertainty and excluded boundaries | Curated fixtures/NumPy results do not establish other repositories' coverage |
| Operational consent | Developer trusts the repository and its test/build commands, keeps a recoverable checkpoint and explicitly reviews every documentation save | Required for each pilot repository; AMU is not a sandbox for project commands |
| MCP, if offered | Actual installed SDK stdio initialize, listing, safe calls, unauthorized-root rejection, action opt-in, bounded failures and shutdown | Pinned SDK2.2.0/local stdio plus 16-call real-engine tool loop passed; actual Claude/ChatGPT connection/consent still needs verification |
| Viewers, if offered | Execute browser tests against the frozen pages, including validation, resource bounds, offline assets, keyboard/mobile behavior and accessibility checks | 30 Chromium desktop/mobile executions passed both locally and on private Linux CI; broader browsers/accessibility/performance remain unverified |
| Models, if offered | Explicitly authorized smoke for a named provider/model, sanitized request review, timeout/error behavior and human-confirmation boundaries | Not performed; remain disabled/experimental, with no implied permission for paid calls |

A failure stops the relevant gate. A passing test command with skipped cases must
state what was omitted. Unsupported platform or integration claims cannot be
filled in by a passing result from a different environment.

The archived main workflow targets Linux/macOS with Python 3.11/3.14 and Chromium.
The separate private manual wrapper executed Linux-only checks against the exact
archive; its results and retained logs identify that snapshot and scope. Local
Mac3.11/3.14 and remote Ubuntu x64/Python3.11/3.14 now have executed evidence.
This does not establish the automatic archived workflow's macOS jobs, all Linux
distributions/architectures, real-provider Linux integration or Windows support.

## Operator rollback drill

Use a disposable copy, not a valuable working checkout. Preserve a known Git
checkpoint and any uncommitted files first. Review the repository's configured
test/build commands: they execute code with your user permissions.

1. Complete a small CLI edit loop and inspect the actual project test results.
   An AMU completion report does not replace the project's tests or review.
2. Prepare a documentation draft, inspect its preview and explicitly save it.
   Inspect `amu docs-saves --json`, then exercise
   `amu docs-saves --undo SAVE_ID --json` before changing the saved document again.
   Confirm the guarded undo refuses to overwrite later independent edits.
3. Exercise a stale draft or document conflict. Confirm save refuses; archive it
   with `amu docs-reviews --discard REVIEW_ID --json`, prepare again and manually
   carry over intended prose. Keep the recoverable archive until resolved.
4. Rehearse interrupted/failed refresh recovery. The old snapshot must survive,
   but must not become successful current evidence. Repair the cause and rerun
   refresh/check. For a documentation save whose graph refresh failed, inspect
   the save journal before repeating a write, then retry `amu docs-graph`.
5. Rehearse restoring a known-good package plus its matching constraints.
   Installer failure can leave changed packages/prerequisites; automatic package
   rollback is not implemented. Never recover by blindly deleting `.amu` state
   or discarding the developer's working changes.

For sensitive repositories, merge `{"outbound": {"enabled": false}}` into the
existing `.amu/config.json` without replacing unrelated settings. Do not configure
model credentials for this pilot. Redaction is best-effort, and an external MCP
client has its own data-sharing policy. Restrict access to captures/drafts: they
can contain proprietary source and prose.

See [state recovery](STATE-RECOVERY.md), [documentation reviews](DOC-REVIEWS.md),
[graph resource limits](GRAPH-RESOURCES.md) and [API accuracy limits](API-ACCURACY.md).

## Public-release decisions and non-gating work

A public release additionally needs a user-selected license and distribution
rights, a release version, a publication destination and an immutable artifact
with the supported-platform evidence above. No LICENSE file/package license
metadata currently resolves these choices. No license is selected and no
publication is authorized by this document. The external engine's supported
distribution and commercial terms must also be resolved before making a
commercial-product claim.

Completing every shared-service refactor, adding caching, expanding to other
languages, integrating build/runtime backends, hash-locking every downloaded
artifact and broader retrieval benchmarks are valuable roadmap work. They are
not substitutes for the gates above, and their unfinished status does not by
itself prevent a tightly scoped supervised pilot. Engine RAM/CPU limits,
whole-workflow multi-file atomicity and broader real-repository accuracy remain
important boundaries: narrow pilot tasks and human oversight must account for
them instead of implying they are solved.
