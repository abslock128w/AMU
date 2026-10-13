# Before tests: what a change reaches and what to run first

```sh
amu                                    # an edit is waiting: run candidate tests on both sides, print what flipped
amu before-tests --static              # the same change, never executing anything
amu before-tests --patch-file fix.diff # a supplied diff: static unless --run
git diff main | amu before-tests --patch-file - --run
amu before-tests --full-evidence       # every ranked test, dependant and consequence
```

For your own uncommitted change, in a tree with tests, AMU executes: it copies the tree, rebuilds the code as
it was before the edit in the copy, runs up to 150 candidate tests (300 with a recording), in chunks, on both sides with the project's runner
(60 s per side, `--budget` to change), and prints `flips` and `predicted_failures` first, then `gaps`. It
uses `$VIRTUAL_ENV`, `.venv` or `venv` when present, else the interpreter running AMU; `--python` overrides.
If nothing could be run, `flips` is null and the summary says NOT EXECUTED, never "0 fail". Bare `amu` opens
the interactive terminal only when no Python edit is waiting; `amu repl` always opens it.

With `--static`, or for a supplied patch without `--run`:
nothing is imported or executed, and the answer is one JSON object under 2 KB:

| Field | Meaning |
|---|---|
| `summary` | Read this first: likely failures, untested functions, how many tests reach the change. |
| `changed_symbols` | Declarations holding the changed lines, narrowest first. |
| `tests` | Tests expected to fail, with the evidence in `why`. Tests that only reach the change are listed with `--tests`. |
| `predicted_failures` | Only with `--run`: tests that passed before the change and fail after it. |
| `consequences` | A changed exception, shared attribute or used return value, and the located consumer. |
| `gaps` | What could not be resolved. A gap never hides the results that were found. |

`expect` is `likely_fail` (the test asserts text, or uses a name, warning or exception the change removes),
`ran_changed_lines` (a recording shows it executing them) or `reaches`. Two agent trials showed that a list
of tests which merely reach a change does not help an agent choose, so the default packet omits those rows.

```sh
amu before-tests --run                 # execute candidates before and after the change; report what flips
amu before-tests --run --python .venv/bin/python --budget 120
```

`--run` copies the tree, builds the other side of the change in the copy, and runs up to 150 candidate tests
on both sides with the project's own runner. The runner is detected from the tree (`pytest`, `unittest`,
`django-runtests`, `django-manage`) and `--runner` overrides it. Outside pytest, each listed test has a `run`
field with the exact command to paste. It is exact for what it runs and silent about tests outside the candidate set.

`confidence` on a test is `direct` (calls the changed symbol), `transitive` (reaches it through resolved
calls), `heuristic` (a receiver was guessed, or only the class or names match) or `observed` (a recording
shows the test executing it).

## Using recorded execution

```sh
amu before-tests --observe             # run the whole suite once under coverage; store a per-test recording
```

`--observe` needs the `coverage` package in the project's interpreter. It records which test ran which line,
including code run in child processes a test starts, and stores it beside the index, one shard per source
file keyed by content hash. Later runs use it automatically:

- shadow execution takes its candidates from the tests recorded running the changed code, most specific
  first, so tests reached through fixtures, signals, registries or subprocesses are included;
- a file edited since the recording is still used, judged by function name instead of line number.

Without a stored recording, `.amu/observed-tests.json`, `coverage.json` or `.coverage` is read if present and
`--coverage` names another file. Recorded execution raises a test's rank and adds candidates. It never
removes a test.

## How it ranks

Each test gets a score per changed symbol from call distance (up to five hops; a nested definition costs
none), how specific the reference is, whether a receiver was guessed, name affinity between test and
symbol, and overlap between the changed lines' words and the test's own vocabulary. The top list is picked
so that every changed symbol is represented before one symbol repeats.

## Index

Facts are stored per file under `.amu/index/`, keyed by content hash; only changed files are parsed again,
and the graph of an unchanged tree is reused. Set `AMU_INDEX_DIR` to keep the index elsewhere.

## Known limits

In a ten-run agent trial on Click, agents with this tool did not find more breaking tests than agents who
read the diff and searched; see the report. Treat the list as reachability, not as a prediction of failures.


See `evaluation/before-tests/REPORT.md`. The main one: when hundreds of tests reach a hub class, the top 10
is a reasoned guess. Dictionary dispatch, plugins and `getattr` are counted as unresolved, not followed.
