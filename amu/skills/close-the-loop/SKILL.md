# Finish a task

## When to use
close-the-loop

## Steps
- make every planned phase green with `amu check --phase N`
- make sure every graph-relevant edit has a current matching `amu refresh` record
- run `amu done` for sync → sweep → docs → memory refresh → report

## Done when
- `amu done` completes; it deliberately refuses to run when phases are incomplete or required post-edit graph evidence is stale
