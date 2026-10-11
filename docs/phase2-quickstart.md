# Concrete patch inspection and a reproducible cache regression

This bounded Phase 2 workflow inspects a real diff, recommends existing tests,
and separately verifies four reviewed AMU-owned fixture candidates. Syntax
findings describe possible effects; only the fixture check supplies executed
failure evidence. It does not establish improved coding-agent effectiveness.

## Install

Use a stable local copy of this checkout, Python 3.11 or newer, and a POSIX host
that supports secure directory-relative reads (`O_NOFOLLOW`). This release's
fresh validation uses Python 3.14 on macOS. Other interpreter/OS combinations
are not newly certified by that run.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -c constraints.txt .
```

Patch inspection and fixture verification require no model, graph provider,
pytest, or optional AMU dependency. A suggested `python -m pytest` command
requires pytest if you choose to run it independently. Existing graph/search
commands and optional `--include-graph` need the native providers described in
the main README. MCP additionally requires `python -m pip install '.[mcp]'`.
This guide does not require a paid API, container, or external repository.

## Inspect the bad cache key

Choose an absent output directory. Export verifies the bundled asset hashes
and writes the baseline, independently specified contract, protected tests,
and all four diffs.

```sh
amu example-fixture --output "$PWD/cache-example" --json
amu analyze-patch --repo "$PWD/cache-example" --base worktree \
  --patch-file "$PWD/cache-example/patches/wrong-key.diff" \
  --json
amu analyze-patch --repo "$PWD/cache-example" --base worktree \
  --patch-file "$PWD/cache-example/patches/wrong-key.diff" \
  --section findings --json
amu analyze-patch --repo "$PWD/cache-example" --base worktree \
  --patch-file "$PWD/cache-example/patches/wrong-key.diff" \
  --section recommended_checks --json
```

The bad patch replaces a key containing both type and value with `str(value)`.
The key construction feeds the shared cache's get/set operations. The report
preserves both source locations and identifies real test declarations that
reference the affected class. Losing the type discriminator creates a possible
collision for integer `1` and string `"1"`; inspection alone does not prove it.

`--base worktree` identifies the actual source bytes by their hashes. In a Git
repository, `--base HEAD` (the default) or the full current HEAD SHA also checks
the Git anchor. It still applies to live working-tree bytes, including local
changes. Arbitrary historical revisions and fuzzy context are rejected.

The default `summary` answers which files changed, which bounded source paths
support existing checks, and which uncertainties matter most. Its ranked
previews include total and omitted counts. Repeated uncertainty reasons show
before/after counts, file counts and located samples; a relationship present in
both phases is not presented as a new risk. Candidate test edits are listed
separately and cannot inflate baseline existing-test recommendations. The JSON
response uses compact whitespace. Full evidence is available in `changes`,
`findings`, `state_dependencies`, `recommended_checks`, and `uncertainties`.
Repeat the same patch/repository and
use the section's `retrieval` cursor or `page.next_cursor` with `--cursor`.
Changing source, tests, Git identity, configuration or graph evidence invalidates
the cursor. For fragmented items, retrieve all fragments, concatenate decoded
bytes in order, and verify the supplied hash before parsing.

## Reproduce the failure and verify the correction

Review `cache_case.py`, `test_cache_case.py`, `runner.py`, `contract.json`, and
each candidate diff before opting into execution. Verification accepts only
named, hash-pinned bundled cases; it does not execute arbitrary exported or
user-supplied code.
It runs the original and candidate in disposable local copies with bounded
process time/output. These controls are not an OS security sandbox.

```sh
amu verify-fixture --case wrong-key --execute-reviewed-fixture --json
amu verify-fixture --case corrected --execute-reviewed-fixture --json
amu verify-fixture --case compatible-refactor --execute-reviewed-fixture --json
amu verify-fixture --case class-state --execute-reviewed-fixture --json
```

The contract protects repeated lookup identity, separation of integer `1` and
string `"1"`, original values/types, and rejection of `None`. The bad key should
fail the separation check and the class-state change should fail because the
unchanged consumer attempts to use `None` as a cache. Compatible and corrected
candidates should pass the same protected checks. The corrected diff preserves
the type/value key; the tests and expectations are unchanged.

Failure output includes the deterministic input, independent expected behavior
and contract hash, observed values/error, protected-test results, source/patch
hashes and a reproduction command. Passing checks establish only those covered
behaviors. Verification without `--execute-reviewed-fixture` supplies an
inspection receipt and launches no fixture process.

To record all four fixed cases with actual local timings, initial response
bytes, complete paged evidence bytes, and both protected check outcomes:

```sh
python -m scripts.phase2_fixture_evidence --output "$PWD/phase2-measurement" \
  --execute-reviewed-fixture
```

Use an absent output directory. The script saves each analysis section/page,
each verification receipt, and a workflow scorecard. Timings vary by host;
the source, patch, candidate, contract, test, and runner hashes identify the
reproducible task. This fixed-case run does not request the optional graph
provider, so its cost is marked unavailable in this measurement.

## MCP

The default server retains the four-tool `amu-inspection-v1` contract. Explicit
patch opt-in adds `amu_analyze_patch` under `amu-inspection-v3`:

```sh
python -m amu.mcp_server --allow-repo /absolute/path/to/cache-example \
  --enable-patch-analysis
```

Supply `repo`, literal `patch`, `base: "worktree"`, and optionally the same
paging/graph arguments. The MCP tool accepts no patch-file path or execution
flag. `--enable-patch-analysis` and the existing action opt-in are separate.
Exact repository grants, startup payload identity, argument/framing limits and
bounded evidence retrieval still apply.

## Supported scope and limits

Diffs are UTF-8 LF, at most 48 KiB, eight total `.py`/`.pyi` Python and
`.md`/`.markdown` Markdown files, and 128 hunks. Markdown must be ordinary
UTF-8 LF text without controls. Its paths, baseline bytes and hunks are
validated exactly, and its changed content is recorded with hashes; Python
behavioral analysis does not cover Markdown prose or fenced code.
Ordinary modifications, additions and deletions are supported; an addition
requires existing nonsymlink parent directories. Binary/combined diffs,
renames/copies, quoted or ambiguous headers, mode changes, path traversal,
managed paths, symlinks and non-UTF-8 declarations are rejected. Inspection
does not modify the working tree or import candidate modules.

The analyzer covers changed calls/arguments, returns, branches, exceptions,
direct module/class state and recognized cache operations. State consumers and
key definitions are connected only in the documented static scope. Aliases,
conditional/rebound keys, dynamic dispatch and arbitrary descriptors remain
uncertain. Existing-test discovery is bounded to 200 baseline Python files and
10,000 directory entries. It follows direct unshadowed import references and,
when an unchanged package initializer contains only a docstring/pass and no
competing root, `src/` or package-module path exists or changes, a
`from package import module` reference to that module. A directly called,
unshadowed module function can connect through at most two direct local calls
to a changed function. That optional path is capped at 128 module functions
and 256 local call edges per source file; dynamic namespaces and shadowed or
decorated bindings are excluded. These are lexical paths, not proof that a
test ran the path or asserted the changed behavior.

Recommendation evidence distinguishes specificity without hiding broad
candidates. Full `recommended_checks` rows keep lexicographic test-ID order;
the default summary ranks supported paths ahead of broad candidates and states
how many checks it omits. Every candidate remains retrievable.
The additive `associations` records describe individual source relationships:

- `source-member-path` connects a direct member reference, or a directly called
  declared consumer and its existing lexical state access, to that member.
  `source-state-path` is the equivalent module-state consumer path. Each has
  `resolution: "resolved"`, baseline test/declaration/access locations, and
  source hashes. Resolved means supported within the lexical resolver's limits;
  it does not prove execution or that the test asserts affected behavior.
- `broad-class-reference` identifies the containing class with
  `resolution: "unresolved"` for a member path. A class import, constructor use,
  class-object reference, or unrelated method does not establish a dependency on
  all its members. Unresolved does not mean there is no dependency.
- `direct-source-reference` preserves other direct source references;
  `broad-source-reference` preserves unresolved containing scopes outside classes.
- `source-call-path` connects a directly called baseline module function to a
  changed baseline function through up to two located direct calls in the same
  file. Its test reference, declarations and call sites are hash-bound to
  baseline source. Rebinding, decorators and runtime dispatch are not resolved.

`entities` contains only the entities supported by these specific associations,
including a containing class for a broad reference, never its members merely by
name prefix. A finding's `recommended_test_ids` uses exact entity associations;
separate `broad_class_test_ids` retain its containing-class candidates without
asserting member dependencies. Complete provenance remains paginated. The patch
packet schema is version 3; explicit detail sections retain their existing
shape with additive coverage, phase and source-call evidence. The identity
configuration records `recommendation_evidence: "member-and-local-call-v2"`
for cursor freshness. The original version 2 build and receipts remain separate.

Member paths reuse baseline declarations and supported lexical state accesses;
they do not infer paths through aliases, instance construction, inheritance,
arbitrary decorators or graph-provider assertions. For example, a supported
direct classmethod call that reads `cls.limit` can supply a path. A call through
`Container().method()` stays broad: this workflow does not add instance-dispatch
analysis. Optional graph evidence cannot promote a broad reference to a path.

Optional graph queries describe the baseline, query at most eight changed
declarations, and retain provider incompleteness and unresolved endpoints.
Package-submodule and established stdlib boundary resolution have focused
provider evidence. A plain stdlib `@dataclass(frozen=True)` declaration also
resolves when its source proves that exact form. Other decorators and remaining
ambiguous relationships can still block graph approval. No approval gate is
weakened.

The historical agent comparison remains unchanged: both agents succeeded,
while AMU took longer (100.224 s baseline, 143.353 s AMU-assisted). Fixed fixture
results establish the protected checks and exercise the stated static categories;
static accuracy is not formally scored. Real agent comparisons, external
execution, broader dynamic/state analysis and production readiness remain
separate gates.
