# AMU Phase 1 candidate — 10 October 2026

**Local Phase 1 engineering: VERIFIED. Overall Phase 1: PARTIAL.**
The official NumPy scenario and authenticated ChatGPT acceptance remain
**NOT EXECUTED**. This candidate is ready for the scoped local demonstration.
It has not upgraded the live connection or begun Phase 2.

Candidate: `dist/phase1-wire-2026-10-10.EHpaIc`, in the existing project
`/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs`.
Git HEAD remains `73aa0f609406dfb23a31f0639cba4f5c0aed7383`; the existing dirty
working tree was preserved. [Machine-readable evidence](PHASE1-EVIDENCE.json)
and [freeze inventory](FROZEN.json) bind the actual artifacts and receipts.

| Verified identity | SHA-256 |
| --- | --- |
| Source archive, 719,534 bytes | `921c134206829cad25401ae27e092e1bc8b20a499b2a792258c507fad11c4d0c` |
| Wheel, 286,141 bytes | `1d1e5e8f28a288f9e8bf2df73af907a1d97b0babae7769e4bd8c0747e62c72d5` |
| Constraints, 1,517 bytes | `1b690bf85bc17de94af15558f9fc5e5d3560f394e9f00e7b7fca4d3c8f2a6429` |
| Python payload, 60 files; source/archive/three fresh installs agree | `880131726cdfd53d8d572a4ebb2ccfcba278179a7ceee5136a2c33c19d415f0e` |
| Unchanged `amu-inspection-v1` manifest | `794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761` |

The continued repair admits the original request ID before SDK parsing and
measures the final serialized UTF-8 JSON plus newline before emitting bytes.
String IDs allow 1,024 UTF-8 bytes; strict integer IDs allow 1,024 JSON bytes.
Oversized or ambiguous IDs receive explicit, undispatched `id: null` errors;
admitted IDs remain unchanged. The incoming frame allowance is 262,144 bytes.
Every outgoing frame is capped at 131,072 bytes. SDK-generated errors and
serializer failures use the same boundary. Oversized results become explicit
`response_limit` failures with `evidence_withheld: true` and unknown side effects,
never shortened successful evidence. Stray stdout is diverted, including during
exceptional shutdown. The codec requires POSIX descriptors; Windows startup
refuses an unsupported transport.

| Newly executed final verification | Result |
| --- | --- |
| Exact final source archive, macOS arm64 / Python 3.14.6 | **2,065 passed / 12 skipped**, exit 0 |
| Exact final source archive, macOS arm64 / Python 3.11.16 | **2,065 passed / 12 skipped**, exit 0 |
| Original baseline failures retained | All **19** passed on both final versions |
| Focused transport/authorization/doctor/impact/archive checks | **299 passed**, no skips |
| Doctor and model-policy lane | **95 passed**, no skips |
| Wire regressions within each final full suite | **35 actual-pipe tests + 14 codec tests** |
| Actual-pipe limit boundary | Exactly **131,072 bytes accepted**; **131,073 bytes** withheld explicitly |
| Fresh installed raw wire smoke, each Python | **13 original frames measured**, maximum **6,574 bytes** |
| Fresh installed native inspection, each Python | **12 calls passed**, all four tools and full schema/annotation parity |
| Independent installed raw native inspection and complete paging | **29 tool calls / 31 frames**, maximum **13,532 bytes**; all six sections reconstructed |
| Installed MCP action-workflow regression in a disposable trusted fixture | **16 calls passed**; no live action opt-in |
| Native provider refresh lane | **4 passed**, no skips; isolated provider HOME/cache |
| Fresh base package and optional SDK installations | Passed; MCP/Pydantic absent in base; all dependency/constraint checks passed |
| Source archive completeness | **253 files**, including all **76 required support files** |
| Lint and whitespace checks | Passed |

The 12 ordinary-suite skips are seven explicitly unexecuted NumPy tests, four
opt-in refresh tests executed successfully in the separate lane, and one
non-UTF8 filename case unsupported by this filesystem. No changed-code Linux,
Windows or browser certification is inferred from historical receipts.

Discovery is exactly `amu_search`, `amu_graph`, `amu_impact`, and
`amu_docs_reviews`. Source, archived and non-editable installed payload identities
match. A separate on-disk old-pilot probe correctly returns three tools and
mismatch exit 2, despite the shared `0.2.0` package version. That separate probe
does not attest the in-memory live tunnel.

Doctor checks prove healthy native-provider operation and matching MCP metadata,
while the disposable fixture's unknown worktree freshness produces
`GRAPH_FRESHNESS_UNKNOWN`, `ready: false`, exit 3. The base smoke also verifies
missing-provider failure. Availability has not been relabeled readiness.

The independent raw-pipe run reconstructs all impact, contradictions,
uncertainties, dynamic-boundary, coverage and reason sections from the same report.
Three impact entries and ten coverage entries match complete reference pages;
the other sections are genuinely empty for this fixture. Every small page has
the expected offset/count/hash and terminating cursor. Existing-root denial and
stale-cursor refusal pass. Native rows do not fragment; oversized-row fragment
reconstruction and SHA-256 verification are covered separately by the executed
service regressions. No empty graph result is called proof of runtime safety.

The old frozen candidate independently reproduced **150,297-byte** and
**161,103-byte** wire failures in the retained red receipt. Failed provisional
smokes are retained too: the raw smoke initially selected a nonexistent denied
fixture, so the correct invalid-path refusal failed its authorization assertion.
The fixture was corrected, the archive/wheel rebuilt, fresh environments created,
and both complete final suites and installed lanes executed again. Earlier
provisional results are labeled separately in the evidence receipt. An initial
doctor lane launched with `AMU_DISABLE_MODELS=1` was also retained as a harness
failure; the prescribed-environment rerun passed. None is hidden as a green run.

Preservation checks compare 299 existing tracked/untracked files: 296 remain
byte/mode identical, and the three scoped edits are the actual-pipe boundary
regression, the raw-smoke fixture correction, and accurate protocol error-code
documentation. Managed `.amu` state, `.entire` graphs, the October 9 bundle,
earlier frozen candidate, acceptance audit, live-profile checksum/permissions,
Git HEAD and Git status are unchanged. The existing transport repair and all
other pending work remain intact. The improvement ledger and release-readiness
source documents identify this candidate and point to these post-freeze sidecars;
their pre-build snapshot does not embed a self-containing artifact hash.

The exact recorded native dispatcher is Entire CLI 0.10.5, SHA-256
`b8cfe20381338a7c9f5bc53b4d824a34548d29385381eae8f8c93541ee56b262`.
The custom `dev` graph binary is SHA-256
`d6a07d96d5306e3aa5c2db6047dce7cf6cb72c19030cfb8ca37e2a283c39954e`.
These identify tested bytes; upstream authenticity/reproducible-build provenance,
population accuracy, runtime effects and numerical correctness are not certified.

**External authorization required — none of the following has been executed.**

1. Authorize one official server-local NumPy checkout and its provider-cache
   preparation. The proposed unused destination is
   `/Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0`.
   Authorize fetching from `https://github.com/numpy/numpy.git`, checking the
   approved `v2.3.0` revision and recording the actual commit/root/source hashes.
   Historical receipts record commit `0532af47d6a815298b7841de00bdbc547104b237`;
   verify it after authorization rather than treating it as a current checkout.
   Refuse an occupied destination. Alternatively, explicitly supply/authorize
   one existing Mac-local checkout. Allow metadata-only hazardous-Git preflight,
   `amu repo inspect`, and any required exact-root `entire graph index` cache
   writes. Use the same selected cache for indexing and the eventual server.
   This scope includes no NumPy imports, builds, numerical tests, source/doc
   edits, `amu init`/workspace registration, rejected-path retries or broad
   parent/home grants. Add only the approved exact root's `--allow-repo` flag
   when the separately approved server cutover occurs.

2. Authorize a reversible runtime cutover and actual connected-client acceptance.
   Create an unused persistent environment at
   `/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs/.venv-phase1-wire-2026-10-10`,
   install this exact wheel with the matching constraints and MCP extra, and
   recheck its payload, dependencies, full manifest and initialization identity.
   Retain the existing `.venv-pilot`. Authorize a brief interruption of only the
   identified foreground `amu-chatgpt` tunnel, then a command override on its
   existing main channel. Retain its profile, tunnel, credentials, loopback
   listener and original AMU root; append only the separately approved NumPy
   root. Use the operator's existing credential-equipped launch environment
   without reading/copying secrets. No permanent profile rewrite, account/auth
   change, extra polling process or `--enable-actions` is included.

Prospective override, after both authorizations and persistent-install checks:

```sh
/Users/Abslock128/Downloads/tunnel-client-v0.0.16-darwin-arm64/tunnel-client run \
  --profile amu-chatgpt \
  --mcp.command 'command=/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs/.venv-phase1-wire-2026-10-10/bin/python -I -B -m amu.mcp_server --allow-repo /Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs --allow-repo /Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0,channel=main'
```

First validate that the same override replaces the sole main-channel command
with `tunnel-client doctor --profile amu-chatgpt --mcp.command '<same override>' --explain`
in the operator environment. If it does not prove replacement, stop. Revalidate
the original PID/command immediately before a graceful stop; historical audit
PIDs are not targets. Preserve the unchanged profile checksum and permissions.

Then authorize refreshing the existing ChatGPT custom AMU connection, confirming
changed metadata, starting a fresh conversation, and retaining actual authenticated
calls to all four tools. Through that client, resolve `finfo.__new__` in current
`numpy/_core/getlimits.py`, independently inspect `_finfo_cache` source behavior,
and execute search/graph/intent-impact/docs-review checks. Drain all relevant
same-report pages/fragments and verify counts/hashes, response bounds, uncertainty
and separate disposable-root denial. Correlate the served build/process/channel
with these real client calls. Compare source/docs/managed-state inventories before
and after approved cache preparation. A separate SDK probe, a tool screenshot or
tunnel health is insufficient. An unavailable authenticated surface remains an
open gate, never a simulated pass.

Rollback on identity, health, authorization or client failure: retain sanitized
failure receipts; gracefully stop only the new identified tunnel; restart the
original binary with `tunnel-client run --profile amu-chatgpt` and **no override**;
verify the preserved profile, original sole root, old pilot and tunnel health;
refresh ChatGPT again. Retain candidates, fixtures, graphs and history. Restoring
the old three-tool deployment reopens the four-tool gate.

Both external acceptance records must genuinely pass before declaring Phase 1
complete. Broader production/platform/model gates remain separate. Local
engineering stopped at this verified candidate; use the [demo runbook](DEMO-RUNBOOK.md).
