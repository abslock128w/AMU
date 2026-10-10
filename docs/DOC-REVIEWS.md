# Documentation review queue

AMU proposes documentation updates; it does not claim prose is correct just
because a code relationship changed. Prepare and preview do not modify docs.

## Approve documentation scope before editing

If the same change will update prose, include each existing Markdown/RST file
under the configured `docs_dir` when approving the pre-edit plan:

```sh
amu plan --docs-file docs/guide.md --docs-file docs/api.rst --approve --json
```

Replace these example paths with your documents. The draft lists `docs_files`,
and the final phase/contract includes only the explicitly selected paths.
Omitting the option leaves a code-only plan unchanged. MDX and executable examples
are not prose-only scope: they require their own code-impact review. Saving a
review draft does not silently expand the contract. After a save, run a fresh
`amu check` before `amu done`; prior successful test evidence is stale.

## Review one affected section

1. Run `amu docs --mode draft --json` to issue a current candidate listing.
2. Inspect a candidate's `targets` array. Positions are **1-based**.
3. Run `amu docs --prepare d1 --target-index 2 --json` to prepare its second target.
   For candidates with exactly one target, omit `--target-index`.
4. Edit the file returned in `prepared.file`. It contains the section body, not
   its heading. Unknown behavior changes need human prose; AMU does not invent
   their semantics.
5. Run `amu docs-reviews --preview REVIEW_ID --json`, substituting the returned
   `prepared.review_id`. Review the diff and status.
6. Run `amu docs --apply d1 --review --target-index 2 --json` to explicitly save.

Use `--repo` with an explicit repository path from outside the repository. The
same commands work in the interactive session with a `/` prefix.

Each selected target has its own draft identity. A change affecting a reference
page and a migration guide can therefore have two independent drafts. The
existing candidate-list freshness checks still apply: `d1` is not a durable ID.
After another save or source edit, list candidates again before applying the
next draft, and check that its candidate/target still match.

## Queue and conflict recovery

`amu docs-reviews --json` lists prepared, edited, conflicted, saved and unavailable
drafts without needing a current graph query. A preview reports document drift
as `conflict`; saving still refuses to overwrite it.

Run `amu docs-reviews --discard REVIEW_ID --json` to archive a draft and its
metadata. The returned `archive` path contains a recoverable copy; the source
document is unchanged. Then refresh the candidate listing and prepare again.
Compare the archived prose with the new draft and carry over edits manually.
There is intentionally no automatic merge or silent overwrite of a draft.

Saved documents use the existing save journal. `amu docs-saves --json` lists
saves; `amu docs-saves --undo SAVE_ID --json` undoes one only while its document
still matches the saved version. Document save and graph refresh are reported
separately. If graph refresh failed, retry with `amu docs-graph`.

## MCP tools

- `amu_docs`: list current candidates.
- `amu_docs_prepare`: prepare a candidate and optional `target_index`.
- `amu_docs_reviews`: list drafts, or pass `preview` for one diff.
- `amu_docs_apply_review`: save an edited draft, with `confirm=true`.
- `amu_docs_discard`: archive a draft, with `confirm=true`.
- `amu_docs_graph`: refresh the documentation graph.
- `amu_docs_saves`: list journals; an `undo` also requires `confirm=true`.

Every tool requires an explicit absolute `repo`. Clients must set `confirm=true`
only after the user approves the exact operation. This is an explicit opt-in
parameter, not independent proof of human consent. Save/freshness checks now
live in the shared documentation application service reached by the CLI,
interactive parser and bounded MCP adapter. Editing draft prose still uses the returned local
file; the MCP server does not provide a general-purpose filesystem write tool.

Before editing, `amu_plan` accepts optional `docs_files: ["docs/guide.md"]` with
explicit boolean `approve=true`. Scalar/index inputs are strict at registration:
`1`/`"true"` cannot stand in for confirmation, and a boolean cannot stand in for a
target index. This still does not independently establish human consent.

See [application services](APPLICATION-SERVICES.md) for direct Python use and
partial-save error reporting. Corrupt selected review state is a nonzero state
error. If a document was already saved but derived graph reads fail, known save
results remain visible with graph refresh pending; inspect recovery records
before retrying writes.
