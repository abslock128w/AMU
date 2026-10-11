# Docs that follow the graph

## When to use
doc-follow

## Steps
- before code edits, explicitly plan each intended existing Markdown/RST document with `amu plan --docs-file <path> --approve`; a save does not silently expand contract scope
- `amu docs` lists review candidates from the committed graph diff plus manual worktree evidence
- drafts are SUGGESTED, not proof that documentation is current
- `amu docs --mode auto` writes only sound, mapped committed-diff candidates not shadowed by worktree evidence
- prepare an editable draft with `amu docs --prepare dN`, edit its returned file, preview the review, then explicitly save with `amu docs --mode draft --apply dN --review`; re-list if `HEAD` or the worktree changes
- after saving, renew evidence with `amu check` before `amu done`; inspect save journals before retrying a failed/uncertain operation
- MCP inspection mode does not expose save tools; actions require an operator-authorized repository and `--enable-actions`, plus actual human approval for each save

## Done when
- each proposed documentation update is reviewed, applied deliberately, or recorded as manual follow-up
