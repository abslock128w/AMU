**AMU: refactor only** at `3de936f`: every change renames or moves 1 definition without changing it; no tests run

### Renamed or moved, nothing else (1)

The same code under a new name or in a new place: refactor, behaviour preserved if tests pass.

- `amu/core/coverage.py#_relative` -> `amu/core/coverage.py#_repo_path` (rename)

Read from the syntax of the change: with the old names spelled the new way, the code before and after is the same. Callers outside this repository, and names looked up from strings, are not checked.

### Gaps (1)

Not run or not seen:

- `16905 call sites unresolved repo-wide; dynamic dispatch may reach more`

### What ran

No test ran: the change only renames or moves code.

<sub>AMU `0.3.0` from `967db59`, report `f10577645b10`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->