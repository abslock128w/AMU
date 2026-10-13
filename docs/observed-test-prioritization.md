# Prioritize tests using recorded execution

AMU can combine static source relationships with per-test Coverage.py evidence.
This is useful when fixtures, constructors, dynamic calls or CLI dispatch stop
static tracing. It prioritizes existing test IDs; it never decides other tests
are safe to skip, proves an assertion protects a change, or runs a test for you.

## Baseline workflow

Run the following in the repository whose baseline you intend to analyze. Collect
only where you have permission to execute the repository. AMU's snapshot and
import commands are read-only and print JSON; the shell redirections save it.

```sh
mkdir -p .amu
amu coverage-snapshot --repo "$PWD" \
  --environment 'your-CI-image-and-dependency-lock-identity' \
  --command 'COVERAGE_CORE=ctrace python -m pytest --cov=your_package --cov-context=test --cov-report=' \
  --json > .amu/coverage-source.json

# A separately authorized test run. Start fresh, without --cov-append.
COVERAGE_CORE=ctrace COVERAGE_FILE=.amu/baseline.coverage \
  python -m pytest --cov=your_package --cov-context=test --cov-report=

amu coverage-import --repo "$PWD" --snapshot .amu/coverage-source.json \
  --coverage-data .amu/baseline.coverage --json > .amu/observed-tests.json

amu analyze-patch --repo "$PWD" --base worktree --patch-file proposed.patch \
  --coverage-evidence .amu/observed-tests.json --json
```

Coverage data import needs the optional `amu[coverage]` dependency (Coverage.py
7.16.x). Test collection additionally needs pytest-cov in the test environment.
Analysis of an imported artifact adds no mandatory dependency. There is no custom
tracing engine or use of Coverage.py's private database schema.

An alternative import accepts `--coverage-json coverage.json`, exported using
`coverage json --show-contexts`. Do not pass both inputs. Full JSON reports can be
much larger than the underlying recording; data-file import avoids report source
analysis and duplicate function/class details. Custom coverage file tracers are
refused. Standard Python line and branch recordings use `contexts_by_lineno`.

Python 3.14's default sysmon core warned of incomplete dynamic contexts during
validation. Use a supported tracing core such as `COVERAGE_CORE=ctrace`, and check
collection warnings. A source match does not establish complete instrumentation.

## Reading the result

- `observed-baseline-execution` means the supplied recording attributes execution
  in a changed entity's baseline span to that test. Setup/run/teardown phases and
  exact parameterized node IDs remain available in `recommended_checks`.
- Matching covers the entire baseline entity span, so changing an unexecuted
  branch can still prioritize tests that executed another branch. It does not
  compare candidate line numbers with baseline coverage positions.
- Added functions and functions with no attributable execution stay unobserved.
  Method-body-only edits do not expand to all execution inside their enclosing
  class. Structural class edits can still produce broad candidates. Collection-
  time imports without a test context cannot identify a particular test.
- `observed_execution.status=stale` removes execution-based prioritization and
  retains static analysis. Missing/malformed explicitly requested files fail with
  an actionable input error. Neither case means no useful tests exist.
- The initial summary prioritizes observed checks. Existing static relationships
  remain retrievable, including function-level selectors alongside recorded
  parameter instances. These are not a deduplicated runnable suite.
- Analysis continues to report `executed=false` because inspection did not run
  anything. `coverage_proven=false` remains: execution does not establish assertion
  relevance or changed-behavior protection.

## Binding and limits

Create the snapshot BEFORE collecting. Import checks that the bounded Python
source inventory, tests, and listed dependency/pytest configuration files still
match. Analysis and cursor reuse bind the artifact bytes and those sources.
Line movement in the proposed patch does not invalidate baseline positions.

The recording is operator-supplied, not authenticated. AMU cannot establish that
it came from the declared command or environment, that old data was erased, that
the suite passed, or that all tests ran. Environment labels are retained, not
independently verified. Native libraries, arbitrary files/services, ignored
folders, external plugins and additional dependency manifests can affect runs.
Do not use this evidence for automatic deselection or safety certification.

Current bounds: the existing Python inventory (512 production files and the
configured test limit), 8 MiB total source/configuration, 2 MiB per input artifact,
100,000 context/line entries, and 1,024 measured files for data import. Exceeding a
bound is an explicit error, never silent success. Artifacts must be normalized
repository-relative regular files without symbolic links. Import reads a private
copy of the coverage database and never loads target modules or file tracers.

The patch schema remains 8 with additive optional fields. The opt-in MCP
`amu_analyze_patch` accepts `coverage_evidence` with the same repository-relative
restriction; creation/import remain explicit CLI operations. Static-only requests
and the default MCP tool set remain available.

## Technology decision

- **Coverage.py/pytest-cov:** use their supported per-test data and phase identities
  to bypass AMU's inability to statically interpret all test setup.
- **pytest-testmon:** a useful established option for running/selecting tests, but
  this delivery needs ingestion and explanations without assuming execution
  authority. Do not reimplement its deselection engine.
- **Pyright:** the previous pinned comparison recovered navigation/type evidence;
  it did not supply runtime dispatch or assertion semantics. Preserve that result
  rather than repeat its integration or treat types as execution proof.
- **Static flow and graph:** retained for possible relationships, missing coverage,
  and explanations. More graph edges alone cannot establish assertion relevance.
- **Mutation/differential checks:** useful for testing whether checks detect a
  specific fault, after execution review. They complement recorded execution;
  they do not turn it into universal regression protection.

Sources: [CoverageData public API](https://coverage.readthedocs.io/en/latest/api_coveragedata.html),
[pytest-cov contexts](https://pytest-cov.readthedocs.io/en/latest/contexts.html),
[Testmon dependency selection](https://www.testmon.org/blog/determining-affected-tests/).
