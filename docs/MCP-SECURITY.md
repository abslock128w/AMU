# Local MCP security and operation

AMU's shipped server uses **stdio only**. It does not open an HTTP listener.
Its supported trust boundary is one operator, trusted local repositories and a
trusted MCP client. It is not an OS sandbox or an authenticated multi-user web
service. Account/tunnel authorization and external client data-sharing remain
separate requirements for ChatGPT use.
The replacement transport requires POSIX file-descriptor/nonblocking-I/O
support. Windows startup is explicitly unsupported; it does not substitute an
unbounded fallback. The replacement receipts identify which POSIX platforms
were actually executed, not merely accepted at startup.

This contract belongs to the Phase 1 replacement candidate
`dist/phase1-wire-2026-10-10.EHpaIc`, not the unchanged earlier candidate or the
currently running pilot. Its external
[report](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-REPORT.md) and
[evidence receipt](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-EVIDENCE.json)
identify the actual installed bytes and protocol executions. Overall Phase 1
acceptance remains PARTIAL until the separately authorized NumPy and live
ChatGPT checks pass. A source change does not alter a running tunnel.

## Startup authority (intentional compatibility change)

Startup now requires at least one explicit absolute repository root:

```sh
/path/to/installed/python -I -m amu.mcp_server \
  --allow-repo /absolute/path/to/project
```

This candidate's default inspection profile exposes only `amu_search`, `amu_graph`,
`amu_impact` and `amu_docs_reviews`. It does not expose planning, project test/build execution,
refresh/completion mutations or documentation saves. Commands may still update
AMU/provider caches, so tool metadata conservatively does not claim that they
change no state whatsoever.
MCP search suppresses CLI navigation-history writes, and empty review inspection
does not initialize managed state. Native provider caches may still change.
Review listing and draft reads pin managed-directory descriptors, refuse aliases
and linked/special drafts, and reject changes during capture. An editor may
replace a regular draft before a new inspection begins; no outside-path fallback
is used when a parent directory changes.

Use repeated `--allow-repo` flags for at most 16 exact roots. Passing an absolute
path in a tool call does **not** authorize it: parent directories, nested projects
and symlink escapes that resolve to another root are rejected. Directory identity
is checked on each call; replacement invalidates the grant. This is not atomic
filesystem confinement against an attacker who can swap OS paths while a native
provider runs. Choose specific project roots, not a home directory or disk.

Only after reviewing and trusting the project's commands should the operator
start with `--enable-actions`. It exposes the existing workflow, including writes
to managed state, approved contracts, reviewed docs and repository commands.
Separate per-action confirmation, pre-edit evidence, freshness, save journals and
scope checks remain. A `confirm=true` value and tool annotations are not
independent proof that a human consented. Keep client approval prompts enabled.

CLI/direct-Python wrappers retain their existing behavior; these startup rules
apply to the MCP executable. Old server commands lacking `--allow-repo` now exit
with setup guidance rather than implicitly granting the whole computer.

## Request, result and execution boundaries

- One active tool call per server process. Overlapping requests receive
  `server_busy` instead of starting another graph/test process; there is no
  unbounded application-level queue.
- Decoded tool arguments have a 64 KiB JSON-byte allowance. This is a handler
  limit after SDK parsing, separate from the raw frame bound below and not a
  model-token guarantee.
- Incoming newline-delimited frames are limited to **262,144 bytes including
  the newline**. An oversized frame is discarded using bounded buffer space
  until its delimiter, then receives an explicit `-32600`
  `request_frame_limit` error with `id: null`; the next valid frame can proceed.
  Duplicate members in any JSON object are rejected with an explicit `-32600`
  `duplicate_json_member` / `id: null` error rather than selecting a last value.
- Before SDK model parsing, the stdio boundary admits string request IDs of at
  most **1,024 UTF-8 bytes**. Strict integer IDs must serialize to at most
  **1,024 JSON bytes**. Admitted IDs are echoed unchanged. An oversized ID is
  refused before tool dispatch with an explicit `-32600` error, `id: null`,
  `error_kind: request_id_limit`, `dispatched: false` and the admitted limit.
  The error explains that the original ID was not admitted/echoed; the server
  does not shorten it or execute an uncorrelatable request. The connection can
  accept a subsequent valid request. A client must use an admitted ID to obtain
  an ordinarily correlated response. Boolean, floating-point, null and malformed
  Unicode request IDs are rejected, not coerced into an admitted identifier.
- Every emitted stdio frame is measured after final SDK serialization as UTF-8
  JSON **plus its terminating newline** and is limited to **131,072 bytes**.
  This boundary covers ordinary results, SDK-generated validation/protocol
  errors and exceptional response paths, not just callback payload estimates.
  The codec uses public SDK JSON-RPC/session models and the low-level server
  runner. Native protocol descriptors are pinned before ordinary stdin is
  diverted to the null device and stdout to stderr; normal application output
  must not become an MCP frame. Raw JSON admission precedes SDK model parsing
  so ambiguous/coerced IDs cannot bypass the contract.
- An oversized or unserializable response becomes an explicit `-32010`
  `response_limit` failure with the same admitted ID, `evidence_withheld: true`
  and `side_effects: unknown`. No shortened success, changed ID or partial
  evidence is presented as complete. Retry with a narrower operation or the
  supported impact section/page retrieval; do not invent a cursor from a
  withheld response. An oversized/unserializable server-initiated request or
  notification fails the stream closed rather than silently disappearing.
  Malformed JSON returns bounded `-32700` / `id: null` errors; invalid
  JSON-RPC envelopes return `-32600` / `id: null`. Neither diagnostic reflects
  raw input or exception text.
- Callback result limits and conservative envelope budgeting remain early
  checks, not the final enforcement point. The impact operation separately
  pages reports at 64 KiB escaped payload and the envelope budget, with explicit
  counts and hash-bound same-tool retrieval. See
  [impact retrieval](IMPACT-ANALYSIS.md). A client's context limit may be smaller.
- CLI child capture remains limited to 16 MiB stdout, 64 KiB stderr and 600
  seconds. Declared test/build runners now use those same bounded streams and
  one deadline; overflow/timeout cannot supply a passing partial pytest summary.
- Unexpected handler errors are structured failures without raw exception text.
  Operational stderr audit records contain a correlation ID, tool name and
  outcome/error kind, not arguments, repository paths, source or raw results.
- After post-execution response failure, an action may already have changed
  state. Inspect the contract/review/save journal before retrying. Response failure
  does not imply rollback, and a returned payload is not necessarily success.

The independent acceptance audit reproduced a 150,297-byte output on the old
`phase1-candidate-2026-10-10.AHBtS2` using a 150,000-byte request ID. That old
candidate budgets application results but does not enforce this transport
contract. Its frozen artifacts and the 9 October release records remain intact;
use the replacement's actual wire receipts, not their test totals, for the
response-size decision.

MCP children remove common credential/token/model/Python-injection environment
settings and always set `AMU_DISABLE_MODELS=1`. This switch wins before credential
discovery, client creation and outbound preparation. Stored credentials are not
deleted or rewritten. It does not prevent arbitrary trusted project commands
from reading files or making their own network calls, nor stop the external MCP
client from receiving the tool data you request.
All inherited `GIT_*` settings are also removed so they cannot redirect an
explicit repository selection to another worktree.

## Local onboarding and diagnostics

For an existing local checkout, inspect prerequisites before registration:

```sh
amu repo inspect /absolute/path/to/project --json
amu doctor --repo /absolute/path/to/project --json
amu doctor --repo /absolute/path/to/project --mcp \
  --mcp-python /absolute/path/to/candidate/python --json
```

`repo inspect` reads bounded Git metadata, detects configured executable filters
or fsmonitor helpers, and reports missing index/provider/freshness prerequisites.
It does not clone, register, import, run project tests or grant MCP access. A
current indexed HEAD alone does not prove a current dirty worktree. To initialize
or register a trusted checkout, the local operator must explicitly run `amu init
--repo /absolute/path/to/project` or `amu repo add /absolute/path/to/project`.
These write operations are not implied by a failed inspection.

Doctor reports nonzero status for actual provider failures, unknown required
health or failed requested MCP parity. The optional MCP probe only initializes
and lists tools in a separate stdio process; it does not change live configuration
or certify the connected ChatGPT/Claude session. It compares full semantic tool
schemas and annotations to the versioned four-tool manifest, plus actual package
byte hashes. A matching `0.2.0` version string alone is insufficient. Package hashes
are build identities, not signatures, provenance or a correctness guarantee.
Use the matching `constraints.txt` for installation; startup refuses an SDK that
cannot expose the required public version/schema contract.

Missing repositories, rejected exact-root grants, invalid Git roots, missing
index, unavailable provider, stale graph and unknown freshness are distinct
diagnostics with next actions. An unauthorized result does not reveal configured
grants or inspect an unselected path. Stored credentials are inspected only for
local presence/redaction; no authentication command or live model request runs.

## Verified-engine installation

The installer offers an explicit existing-binary mode:

```sh
sh scripts/install.sh --local /absolute/path/to/amu.whl \
  --entire-bin /absolute/path/to/entire --entire-sha256 EXPECTED_CLI_SHA256 \
  --graph-bin /absolute/path/to/entire-graph --graph-sha256 EXPECTED_GRAPH_SHA256
```

Replace the placeholders with digests from a trusted release/build record. Both
are checked before execution, effective managed-plugin/PATH dispatch is verified,
and identities are rechecked after install and health checks. Strict mode never
installs or switches the engine. Unknown dispatch semantics, different providers
or changed binaries fail closed. Its preflight is tied to documented Entire
external-plugin semantics, not a guess that all future dispatchers behave alike.

Computing a digest from an arbitrary local binary proves only that you pinned
those bytes. It does not establish provenance, distribution rights, semantic
accuracy or compatibility of the engine a new recipient downloads. Default
installation remains unpinned and explicitly not production verified. Installation
failure may leave Python packages changed; package rollback is an operator drill.

See [release readiness](RELEASE-READINESS.md) for executed checks and remaining
client/platform/provider gates. Do not expose this stdio process through an
anonymous public proxy. Private ChatGPT tunnels still need account authorization;
public HTTP use requires a separately designed authorization boundary.

Installed-wheel checks include `scripts/smoke_mcp_installed.py` for default
inspection/allowlist/action opt-in, and opt-in `scripts/smoke_mcp_workflow.py`
for the real-engine workflow over stdio. The latter requires explicit `--entire`
and `--graph-plugin` executables, creates disposable Git/home/cache state, and
uses scripted fixture-operator approval—not a live AI or proof of human consent.

Older development wheels may share version `0.2.0` but lack these controls.
Install the exact new candidate artifact with its matching constraints, restart
clients, and verify inspection discovery and unauthorized-root rejection before
connecting it. Startup flags alone do not prove an older executable enforces them.
