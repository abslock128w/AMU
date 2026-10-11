# Shared application services

Documentation workflows now live in `amu.services.documentation`, separate from
terminal parsing and display. CLI, interactive and MCP commands retain the same
user-facing syntax and reach this implementation. Mapping and snapshot sync are
also extracted below; post-edit/lifecycle orchestration still needs migration.

## Documentation: direct Python use

```python
from amu.services.documentation import (
    DocsRequest, DocumentationError, execute, graph, review_queue, saves,
)

repo = "/absolute/path/to/project"
try:
    listing = execute(repo, DocsRequest(mode="draft"))
except DocumentationError as exc:
    print(exc.exit_code, exc.payload)
```

Every entry point requires an explicit existing absolute repository. The service
canonicalizes that path but never chooses a workspace or falls back to the
current directory. The CLI adapter retains relative `--repo` support by resolving
the selected path first. `DocsRequest` is immutable; operation execution validates
runtime field types, allowed modes, mutually exclusive actions and positive
1-based target indexes. Invalid requests cannot start the docs workflow.

`execute` returns the existing candidate/review payload. Use `prepare="d1"` with
an optional `target_index` to prepare a listed candidate, edit the returned local
draft, then explicitly request `apply="d1", review=True` to save. Candidate IDs
still bind the current listing, source evidence and target. Preparing is not a
save; supplying an ID does not waive freshness or conflict checks.

`review_queue(repo, preview=...)` previews a draft; `discard=...` archives one.
`saves(repo)` lists recovery records; `undo=...` explicitly requests a guarded
undo. `graph(repo)` refreshes the derived docs graph and comparisons. Queue and
history inspection remain independent of a current code graph query. These APIs
return data and do not print or import terminal frameworks.

## Write authority and side effects

These are application operations, not pure functions: listing can refresh
derived indexes and candidate metadata, prepare creates a draft, and explicit
apply/auto/undo operations can change documentation. Direct callers are
responsible for choosing and authorizing the operation. A service call is not
independent evidence of human consent.

MCP still requires `confirm=true` after user approval for reviewed saves,
discard and undo. It retains bounded subprocess invocation of the CLI; the CLI
then invokes the service. The interactive session retains the shared CLI parser.
No new general-purpose filesystem write tool or source-code write authority was
introduced. Auto updates remain restricted to sound, mapped, unshadowed committed
diff evidence; worktree API signals remain manual review only.

## Failure and recovery

Expected application errors carry `DocumentationError.exit_code` and `payload`.
Untrusted/corrupt state remains `StateReadError`, which the CLI renders as a
nonzero structured state error rather than an ordinary successfully skipped
edit. Queue/history may retain per-record unavailable statuses supplied by their
underlying recovery modules.

Document replacement and derived graph refresh remain separate outcomes. If
prose was saved but final graph metadata/comparisons could not be read, the result
retains known saves, marks `docs_graph.status` as `refresh_pending`, reports its
error and suppresses stale comparisons. Inspect `amu docs-saves` and repair the
reported state before retrying graph refresh; do not repeat the document write
just because graph bookkeeping failed.

The save journal and optimistic conflict checks remain unchanged. This extraction
does not make multiple document saves atomic or roll back completed writes after
a later failure. A critical state error during a later auto operation stops
further writes and returns an exit-2 partial error payload with `complete: false`,
earlier known `applied`/`saves`, and a separate `unconfirmed_operations` entry for
the operation that failed. Later candidates are marked `not_attempted`. That
unconfirmed operation might have saved before failing; inspect its journal rather
than treating it as definitely unsaved. No automatic rollback is performed.

A transport timeout/output-limit can also occur after an
operation performed side effects; an error is not proof that nothing was saved.
Inspect recovery records before retrying a mutating request. See
[documentation reviews](DOC-REVIEWS.md), [state recovery](STATE-RECOVERY.md) and
[resource safeguards](GRAPH-RESOURCES.md).

## Scope of verification

Direct-service and adapter tests exercise matching listings, selected targets,
review/edit/save/undo, graph updates, stale IDs, conflicts, recoverable archive,
explicit MCP confirmation, resource failures and saved-outcome reporting.
They use temporary repositories and controlled graph responses. Installed-wheel
checks verify that the service ships and that direct/CLI/MCP error and queue
behavior agree. They do not establish every language/runtime relationship,
compiled numerical behavior, live model-provider behavior or complete migration
of the remaining CLI orchestration.

The [recorded 9 October verification](evidence/documentation-services-2026-10-09.json)
contains 1,365 deterministic tests passed with 11 opt-in skips, rebuilt-wheel
checks, and the separately passing real NumPy API-to-reviewed-RST workflow.
That single external test was rerun after this extraction; the full seven-case
NumPy accuracy audit belongs to the earlier recorded batch.

## Mapping and snapshot sync

```python
from amu.services import mapping, graph_sync

repo = "/absolute/path/to/project"
request = mapping.MapRequest(symbol="pkg/api.py#function", depth=2, change="body")
impact = mapping.execute(repo, request)
snapshot = graph_sync.execute(repo)
```

The mapping service requires one explicit file or symbol target, validates input
types, safe repository-relative paths, positive non-boolean depth and supported
change classes. `MapInputError` has an exit-2 payload; `GraphDataError` means
required graph evidence could not be collected. Missing inventory is a failure,
not a successful empty graph. Useful ambiguous/partial inspection maps remain
explicitly unapprovable. Legacy bare symbols can be inspected, but missing focus
stays unknown and unsafe provider-inferred file paths are rejected before saving.
Prefer exact `path#qualified_symbol` requests.

Mapping brackets capabilities, symbols, impacts and source checks with HEAD and
code/configuration captures. Drift prevents replacement of the old map. Dirty
graph-relevant inputs make the result unsuitable as a pre-edit baseline. Stored
baselines still bind exact targets, depth, pagination and map identity. The
existing source policy is unchanged: a contradicted call blocks approval, while
an inconclusive source resolver is disclosed separately from a provider-resolved
relation. Neither a resolved static edge nor an approvable static map certifies
runtime impact or argument/behavior compatibility.

Successful mapping writes map state, bounded navigation history and workspace
recency, as before. An optional `progress` callable receives presentation
messages and is not included in the evidence hash. Core services themselves
print nothing and import no CLI/Typer/Rich modules. These writes are not one
atomic multi-file transaction.

Snapshot sync always distinguishes the committed `HEAD` index from the worktree
snapshot. Normal failures return an `error` attempt record; invalid repository
or cache options raise `GraphSyncError` with an exit-2 payload. Corrupt critical
saved state remains fatal rather than becoming defaults. Failed attempts retain
previous snapshot bytes/identity without treating them as current evidence.

`graph_sync.execute(repo, allow_doc_cache=True)` is the narrow completion opt-in
for unchanged code/configuration with an inert-prose edit. Provider version,
HEAD, graph depth, prior readiness, source capture and snapshot checksum must
all match. It does not renew test/scope evidence. Ordinary `amu sync` continues
to rebuild rather than silently choosing this cache path.

The existing CLI commands, MCP map/refresh/completion wrappers and interactive
map paths reach these services through adapters. `/map pkg/api.py#function
--depth 1` and `/map "file with space.py"` now use the CLI parser. Invalid options
leave the session open and do not replace the prior map. Relative CLI `--repo`
paths remain supported; direct core and MCP repository selection remains
explicit. No standalone MCP sync tool or interactive `/sync` command was added.

The [graph-service verification](evidence/graph-services-2026-10-09.json) records
direct/CLI/parser/MCP parity, real source/Git drift checks, cache/failure behavior,
the ordinary full suite, installed-wheel checks and four real-provider refresh
scenarios. Most provider responses in ordinary tests are controlled fixtures;
this batch did not rerun NumPy's full accuracy/numerical suite or live models.
