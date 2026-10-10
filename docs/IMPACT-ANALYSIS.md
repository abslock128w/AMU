# Proposed-change impact inspection

This candidate adds a dedicated inspection operation for a proposed change at a
Python source line. It does not edit code, approve a plan, run project tests or
save documentation. Provider queries can still maintain their own caches; this
is not an OS sandbox for native graph tools.

## Graph updates and static analysis are different concepts

AMU's graph is versioned and must reflect changed source relationships. Static
analysis means deriving those relationships without executing the application;
it does not mean keeping an old graph forever. An impact request queries the
current worktree and checks source/worktree identity around that query. A stable
capture is evidence about that request, not a promise that all saved snapshots
are refreshed continuously in the background.

An edit such as changing `return first(value)` to `return second(value)` can
change a directly discoverable call edge. By contrast, a target chosen through
`getattr(obj, runtime_name)` can depend on data not present in the source graph.
The tool must report that boundary instead of turning absent edges into safety.

## CLI and MCP

For an installed candidate containing the feature:

```sh
amu impact --file pkg/api.py --line 12 \
  --proposed-change "Return None rather than raising ValueError" \
  --change body --depth 2 --repo /absolute/path/to/project --json
```

The corresponding `amu_impact` MCP tool accepts `file`, one-based `line`,
`proposed_change`, optional `depth` and `change_kind`, plus the required absolute
`repo`. `file` is relative to that allowed root. Its availability in inspection
mode does not enable planning, source writes, tests/builds or documentation saves.
Optional `section`, `cursor` and `page_size` retrieve bounded evidence pages.

For ChatGPT, select AMU and ask:

> Inspect the proposed change at pkg/api.py line 12 in my authorized repository:
> return None instead of raising ValueError. Call amu_impact, summarize the
> evidence-backed dependency footprint, and distinguish inferred effects from
> graph evidence. Include freshness, omissions and unsupported boundaries.
> Do not edit files, approve a plan or run project commands.

Only operator-granted repositories are accessible. A ZIP attached to ChatGPT is
not automatically a local repository or an additional server grant. Keep the
private tunnel running while testing its already approved installation. Installing
or cutting over a live server is a separate operator action: a new local test
environment does not upgrade a running tunnel. An older running process can
continue advertising the earlier three-tool set without `amu_impact`.

## Evidence retrieval

Reports cap their escaped JSON payload at 64 KiB and conservatively budget the
duplicated MCP result envelope at 128 KiB. `retrieval` gives logical-item counts,
fragment counts and a starting cursor for each section: `impact`,
`source_contradictions`, `source_uncertainties`, `dynamic_boundaries`, `coverage`
and `reasons`. `page.next_cursor` retrieves the next page in the same section.
Use the same repository, file, line, proposal, depth and change kind on each call.
CLI equivalents are `--section coverage --cursor CURSOR --page-size 20`.

A cursor is not a grant or a stored report. Every request repeats authorization
and live evidence collection. It binds the repository, source/worktree, detailed
evidence, proposal and retrieval format. Changed evidence rejects the cursor;
restart inspection instead of stitching incompatible pages. Changing page size
alone does not invalidate the analysis.

An individual large item is returned as `evidence_fragment` entries, not silently
dropped. Collect every fragment from the same report, concatenate its decoded
base64 bytes in `fragment_index` order, verify `item_sha256`, then parse UTF-8 JSON.
Incomplete fragments are not usable evidence. Coverage retrieval includes the
semantic edge count, source-check uncertainty, provider coverage and edge
baselines. A mandatory-header or bounded-item failure returns an explicit error.

The estimate is an early check; the replacement candidate's stdio boundary
enforces the final **131,072-byte JSON-plus-newline** frame allowance after SDK
serialization, including SDK-generated errors and exceptional responses.
String IDs are admitted up to 1,024 UTF-8 bytes; strict integer IDs are admitted
up to 1,024 serialized JSON bytes. Admitted IDs are never shortened or changed.
An oversized ID receives an explicit `request_id_limit` error with `id: null`
and `dispatched: false` before tool dispatch, explaining that the ID was not
admitted/echoed. The connection remains available for a valid request.

If a response still cannot fit or serialize, the boundary emits an explicit
`response_limit` failure with the admitted ID and `evidence_withheld: true`, not
a truncated successful report. Retry a smaller page or narrower request using
the documented section/paging interface. Do not treat withheld evidence as
empty, fabricate a cursor or assume a failed response rolled back an action.
The separate incoming-frame limit is 262,144 bytes including its newline;
duplicate JSON members and non-strict/malformed IDs are rejected before SDK
model parsing. These are protocol byte bounds, not guarantees about a client's
smaller context limit. The replacement transport requires POSIX I/O; Windows
startup is unsupported rather than using an unbounded fallback.
See [the full wire contract](MCP-SECURITY.md) and the replacement's external
[execution report](../dist/phase1-wire-2026-10-10.EHpaIc/PHASE1-REPORT.md).

## Reading the report

- The target identifies the containing source symbol and source revision.
- Ranked rows describe the current dependency footprint, with bounded traversal
  and explicit omitted rows. They do not prove every reachable caller will break.
- Provider coverage and independently checked source evidence are distinct.
  Contradicted edges are excluded; unresolved identities remain uncertain.
- A graph query failure, source/worktree race, missing coverage or unsupported
  scope cannot become a complete empty impact report.
- `safe_to_change` is not certified. A fully received graph result does not prove
  runtime completeness or numerical correctness.

The proposed-change text records intent. AMU does not apply or execute that edit
to simulate its result. Current relationship evidence alone cannot determine
all behavioral consequences. Exact expected outputs, API contracts and relevant
project tests remain needed for review. Runtime/build evidence may improve that
review, but requires a separately supported and authorized backend.

## Supported scope and production boundary

This operation is Python-focused. Unsupported source constructs, module-level
edits, malformed source and unresolved graph targets must be explicit rather
than guessed. Dynamic dispatch, reflection, generated code, native boundaries
and feature flags can leave gaps even when source-derived relationships are
fresh. The existing curated NumPy checks are not an accuracy estimate for every
function or repository.
The boundary detector is intentionally incomplete: a named parameter callback
can depend on runtime dispatch without receiving a special AST boundary tag.
Absence of tags does not establish runtime completeness.

Release evidence must identify the actual candidate and distinguish unit/provider
double tests, disposable real-engine tests, installed SDK protocol tests and a
real ChatGPT/Claude conversation. Passing one lane does not establish the others.
See [release readiness](RELEASE-READINESS.md) and [local MCP security](MCP-SECURITY.md).
