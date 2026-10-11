# Code, documentation and completion freshness

An uncommitted prose edit no longer expires code relationship evidence.
AMU records separate code and documentation revisions, alongside the full
worktree capture used by tests and scope checks.

After an approved code change:

```sh
amu refresh --symbol path/to/api.py#function --change body
amu check
amu docs --mode draft
# Prepare/review/save the selected documentation update.
amu check
amu done
```

The second check covers the updated documentation and its allowed edit scope.
A fresh code refresh is unnecessary when only prose changed. Executable examples,
Sphinx configuration, MDX, dependency locks, requirements/constraints files and
build inputs remain code-relevant, even when stored under the docs directory.

Markdown/RST/plain notes and ordinary documentation assets receive the docs
revision. Code and docs revisions bind the current Git commit and AMU
configuration/feature mapping. Read failures, escaped symlinks, dirty submodules,
deletions, renames and mode changes retain explicit evidence or errors.

Code questions can retain their confirmed answers across prose edits.
Documentation questions also bind the docs revision and doc mapping; changing
either expires those answers. Documentation candidate listings and review drafts
still require their original document, section and listing to remain current.
The docs graph records both revisions and a hash of the document content it read.

## Reuse and its limits

After scoped prose edits pass another check, `done` can reuse the source snapshot
when source/configuration, provider version, graph depth and snapshot checksum
still match. Its output says `code_cache_reused=true` and labels the snapshot
`code-worktree-cache`: documentation records in that older snapshot may be stale.
The separate docs graph is rebuilt from current prose. An explicit `amu sync`
or `amu refresh` still requests a current provider snapshot.

Existing captures without versioned per-file evidence retain the old full
worktree rule. Renew their map/refresh normally; AMU does not silently reinterpret
old evidence as code-only proof. A repository without a first commit remains
inspectable, but cannot approve a code revision.

HEAD remains a strict boundary, including a documentation-only commit. Supporting
reuse across those commits requires a committed source manifest; this version
conservatively requires new evidence. Staging and configuration changes can also
change captured revision metadata.

Tests and completion still use the full worktree and runner configuration.
An edit after a green check, including a prose edit, invalidates that check.
Nothing about code-only reuse widens the approved edit scope or certifies that
documentation prose is correct.
