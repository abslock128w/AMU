# Edit exactly one phase

## When to use
phase-edit

## Steps
- `amu plan --approve`, then edit only the current phase's files
- after the edit, run `amu refresh --symbol <path#name> --change <approved-class>` before relying on the graph again
- for a rename or move, include `--new-symbol <new-path#name>`
- finish with `amu check --phase N`

## Done when
- the matching refresh completed against the current worktree and the phase check has no blocking violation
