# Before tests: what a change reaches and what to run first

```sh
amu before-tests                       # uncommitted changes of the repository in the current directory
amu before-tests --patch-file fix.diff # a proposed diff, applied or not yet applied
git diff main | amu before-tests --patch-file -
amu before-tests --full-evidence       # every ranked test, dependant and consequence
```

Nothing is imported or executed. The default answer is one JSON object under 2 KB:

| Field | Meaning |
|---|---|
| `changed_symbols` | Declarations holding the changed lines, narrowest first. |
| `impacted` | Non-test code that depends on them, with hop count and path. |
| `tests` | Up to 10 existing tests to run first. `why` is the path or the matching names. |
| `consequences` | A changed exception, shared attribute or used return value, and the located consumer. |
| `gaps` | What could not be resolved. A gap never hides the results that were found. |
| `caveats` | One sentence. The list is a priority order, not permission to skip other tests. |

`confidence` on a test is `direct` (calls the changed symbol), `transitive` (reaches it through resolved
calls), `heuristic` (a receiver was guessed, or only the class or names match) or `observed` (a recording
shows the test executing it).

## Using recorded execution

If `.amu/observed-tests.json`, `coverage.json` or `.coverage` exists it is read automatically; `--coverage`
names another file. Record with per-test contexts, for example
`pytest --cov=your_package --cov-context=test`. Reading a raw `.coverage` file needs the `coverage` package.

A recording is judged per file. Files unchanged since the recording keep exact evidence. A file edited
since is still used and marked as recorded before the edit; when the edit is the patch being analysed, the
recorded line numbers are moved through it. A test that ran a changed line few other tests share ranks
highest, and a gap names changed functions whose changed lines no recorded test ran. Recorded execution raises a test's rank and can
add tests static analysis missed. It never removes a test.

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
