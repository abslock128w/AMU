# Graph output and snapshot safeguards

AMU now bounds graph output while it is being received. A timeout, byte limit,
invalid record or transport failure cannot become an empty graph or an approved
edit baseline. Partial captured output is withheld rather than clipped into a
seemingly complete JSON response.

## Current limits

| Boundary | Output limit | Additional limits |
|---|---|---|
| Graph query/index/definition/diff | 4 MiB stdout | 64 KiB stderr; existing command timeout |
| Graph symbol inventory | 16 MiB stdout | 100,000 records; 1 MiB per record; 300 seconds |
| Graph snapshot | 128 MiB streamed stdout | 1,000,000 records; 1 MiB per record; 300 seconds |
| Chat/MCP child AMU commands | 16 MiB stdout | 64 KiB stderr; 600 seconds |
| Declared project tests/builds | 16 MiB stdout | 64 KiB stderr; one 600-second deadline; overflow cannot certify a pass |
| MCP decoded arguments / callback results | 64 KiB / 128 KiB | One active call; oversized results withheld, not truncated; SDK/client overhead is separate |
| Snapshot completion suggestions | First 2,000,000 bytes | Up to 5,000 names; presentation only |
| Snapshot reuse checksum | 128 MiB file | Streaming read; larger files cannot be reused |

Every stdout/stderr limit is independent. Exceeding stderr's allowance also
invalidates an otherwise successful stdout response. Graph commands retain their
existing timeouts: generally 180 seconds, with shorter installation/capability
queries and longer snapshot/symbol/verification operations. These fixed limits
are not repository-controlled environment overrides.

The shared runner drains both pipes without an unbounded `communicate` buffer.
Its deadline includes descendants retaining inherited pipes after the command
leader exits. On macOS/Linux failure or interruption, it kills that process
group and reaps the direct child. It does not launch a shell and supplies no
interactive stdin. Windows uses bounded reader queues but currently terminates
only the direct child; complete descendant cleanup needs a Windows job object
and has not been certified here. Descendants deliberately escaping a POSIX
process group are also outside this guarantee.

## What happens on failure

Snapshot output is written into a private temporary file while each NDJSON
record is checked. Both compact and supported verbose records remain accepted.
Invalid UTF-8, duplicate JSON keys, nonfinite numbers, malformed records and
exhausted limits are failures. Only a completely received, validated and flushed
snapshot replaces the old file. Failed or interrupted attempts remove their
temporary file and preserve previous snapshot bytes.

`sync-state.json` records an unsuccessful attempt as `snapshot_ref: unavailable`
with no current snapshot hash/line count. When available, `prior_snapshot`
retains the last successful capture's identity separately. Its existence does
not authorize cache reuse after failure. Completion/refresh still require
current successful evidence; an overflow cannot be interpreted as removed edges.

Failed symbol queries now remain explicit in `open`, `where`, `tree`, free-text
interactive lookup and verification fallback. The interactive session stays
open and can retry without caching a failed empty inventory. Workspace sync
also refuses failed graph diffs instead of recording zero changed entities.
Unavailable capabilities may supply conservative display hints, but cannot
silently authorize a map/plan. Graph search/definition model tools retain these
error and coverage fields before their separate context-budget ranking.

## Recovering from a limit

For a query limit, choose an exact `path#symbol` and reduce traversal depth where
appropriate, for example:

```sh
amu map --symbol pkg/api.py#function --depth 1 --json
```

A snapshot/index scans its repository scope, so reducing query depth does not
fix a snapshot-size limit. Review the repository's generated/vendor inclusion
policy or use a deliberately narrower project scope. Graph exclusions reduce
coverage: document that scope and reevaluate the baseline before planning.
An excluded caller is not evidence that the relationship does not exist.
Rerun the failed operation after addressing the cause. A preserved old snapshot
is recovery data, not a replacement for successful current refresh evidence.

## Verification and remaining limits

The resource regressions use real disposable subprocesses for stdout/stderr
floods, exact boundaries, simultaneous streams, inherited-pipe deadlines,
descendant teardown, invalid UTF-8 and staged-write failures. Integration tests
exercise mapping/approval, init, refresh, sync, completion, workspace and
interactive/MCP boundaries. The real-provider refresh suite remains opt-in:

The [recorded 9 October verification](evidence/graph-resources-2026-10-09.json)
includes 1,200 deterministic tests passed (11 opt-in skips), four real-provider
refresh checks passed, and a rebuilt installed-wheel smoke pass. A pinned NumPy
v2.3.0 checkout supplied 29,103 symbol records and a 180,211-record, 17,284,919-byte
snapshot successfully under the default limits. Snapshot-call traced Python
peak allocation was 874,515 bytes; this excludes imports, native allocations,
total process RSS and the graph engine. The two large-output checks ran
concurrently, so their durations are not a controlled performance benchmark.

To rerun the real-provider refresh suite:

```sh
AMU_RUN_EXTERNAL_GRAPH_REFRESH=1 \
AMU_EXTERNAL_ENTIRE_BIN=/absolute/path/to/entire \
pytest -q tests/test_external_graph_refresh.py
```

Byte caps bound the received payload, not exact Python object memory, graph
engine RAM/CPU or total repository analysis cost. JSON query/inventory objects
still occupy memory within their bounded input sizes. Snapshot parsing holds
only a bounded record and chunk rather than all records at once. Regular-file
sink writes/flushes must complete promptly: a synchronous filesystem operation
cannot be safely preempted by the subprocess deadline.

Git commands and configured build/test runners still have separate existing
policies and are not covered by this graph-output change. Live model requests
have their own [provider and context limits](MODEL-CONTEXT.md). These safeguards
do not establish runtime impact, numerical correctness or whole-repository
semantic coverage.
