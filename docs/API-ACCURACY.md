# API review signals and accuracy checks

AMU compares static public-surface manifests before using the code/docs graphs
to locate related code and documentation. An internal edge change alone does
not establish a user-facing change. All manifest findings require review;
they do not authorize automatic documentation edits or prove runtime behavior.

## Inspect a changed file

With a committed baseline and your edits in the worktree:

```sh
amu api-manifest --file pkg/api.py --base HEAD --json
amu docs --mode draft
```

Use `--repo /absolute/path/to/project` from another directory. The report
contains before/after entries, change classifications, changed facets, parser
provenance, coverage gaps and baseline/current source fingerprints. Verified
absent files can represent additions/removals; unsafe, unreadable or unavailable
inputs remain unknown. These commands do not execute the project or contact a
model. `/api-manifest` and `/benchmark` also work in the interactive terminal.
Exit zero means a report was produced, not that its coverage is complete or
that no documentation change is needed. Inspect `complete` and `unknowns`.

The Python adapter covers declarations, explicit static `__all__`, public
imports/re-exports, constants, aliases, exported class methods and overload
groups. It records defaults, annotations, async/generator contracts, decorators,
and static return/exception/deprecation/CLI hints. Explicit export intent is not
a claim that other Python attributes are inaccessible. Dynamic or conditional
exports, star imports, ambiguous/rebound aliases and repeated property accessors
retain uncertainty. No inter-module type inference or runtime evaluation is
performed. Unsupported languages remain unknown, not a silent “no change.”

Limits include 2 MB source files, 50,000 Python AST nodes, 2,000 Python entries,
100 items per summary, an 8 MiB Python manifest, and bounded alias resolution.
Configuration manifests are also limited to 8 MiB, 5,000 entries and 2 KiB key
identities; reaching a bound reports partial coverage instead of dropping keys
while claiming completeness.
Recognized secret literals are redacted from displayed evidence; hashes still
detect changes. Redaction is best-effort, not a guarantee that arbitrary private
data can be safely published.

## Configuration and schema keys

JSON and TOML adapters compare static keys/defaults/types without asserting
that a key is public or that a response schema is enforced at runtime. Direct
`api-manifest` reports work for either format. To include a configuration change
in the docs review queue, explicitly map the file/key in `.amu/doc-map.json`:

```json
{
  "settings.json#timeout": [{"file": "docs/config.md", "section": "Timeout"}],
  "settings.toml#*": [{"file": "docs/config.md", "section": "Settings"}]
}
```

Unmapped configuration files are not automatically treated as public APIs.
An exact leaf mapping takes precedence; otherwise a nested key uses its closest
mapped parent, then the file's `#*` mapping. Mapping `settings.json#response`
therefore includes changed keys beneath that response, but not unrelated siblings.
Explicit mappings can include configuration examples under docs/tests or dot
filenames; internal AMU/Git state, dependency caches and credential paths remain
excluded from automatic docs candidates.
Nested keys use dotted paths; a literal dot or backslash in a key is backslash
escaped (`a.b` is a nested key; `a\.b` is a literal dotted key). An empty key
segment is `\e`; a literal backslash is doubled so it cannot collide. Duplicate JSON
keys, nonfinite numbers, malformed data and exhausted limits remain unknown.
Arrays are compared as bounded values rather than individual addressable keys.

The normal reviewed-save workflow is unchanged:

```sh
amu docs --prepare d1 --target-index 1
# Review/edit the printed local draft file.
amu docs --apply d1 --target-index 1 --review
```

Candidates bind compact before/after hashes and changed facets, including
changes hidden by redaction. A stale listing/draft cannot be reused. Source
manifest candidates stay manual; `--mode auto` has not gained new write
authority. See [the documentation review guide](DOC-REVIEWS.md).

## Run the versioned fixture benchmark

```sh
amu benchmark
amu benchmark --json
```

The shipped `public-api-v1` corpus contains 20 hand-labeled synthetic cases:
14 changes, three no-change controls and three required-uncertainty cases.
The report includes corpus/source/tool revisions, true/false positives and
negatives, abstentions, coverage and per-case evidence. Missing a positive
because of uncertainty still counts as a false negative. Required-uncertainty
cases are scored separately, and undefined metrics are `null`, not perfection.
The command reports scores; exit zero does not imply every label matched.
The [recorded scorecard](evidence/public-api-v1-score-2026-10-08.json) contains
14 true positives, three true negatives, no false positives/negatives, and all
three required uncertainty warnings. Two cases abstained outright; the third
reported a known change alongside an uncertainty warning. This is the measured
fixture result, not a promised score for real projects.
These small fixtures are regression controls, not a repository-wide accuracy
estimate or evidence that every necessary prose edit can be detected.

For relation scores, supply observations rather than expected labels:

```sh
amu benchmark --relations raw.json --validated validated.json --json
```

Each file is either an edge list or an object of this form:

```json
{
  "edges": [{"source": "pkg/caller.py#caller", "relation": "CALLS", "target": "pkg/api.py#function"}],
  "completeness": {"complete": false},
  "abstentions": []
}
```

Endpoints must be normalized repository-relative `path#qualified_symbol`
identities. Use `--corpus /path/to/relations-v1.json` for your own independently
labeled corpus, or a directory containing the versioned corpus filenames.
The default relation labels describe only the shipped tiny synthetic sources;
they must not be used to score arbitrary NumPy edges. Supply raw and validated
observations separately. Their completeness is independent; an edge list alone
does not certify either. True negatives require explicit negative labels and
complete coverage. Unlabeled observations count as false positives within the
supplied evaluation universe, so define a bounded, independently reviewed scope.

## Real-repository limits

The live NumPy audit samples same-file and cross-file direct calls, a target
with no direct Python edges, and a reviewed API-to-RST edit. It never imports,
builds or executes NumPy. A native caller of the nominal Python-isolated target
illustrates why an empty static graph is not proof of runtime isolation.
See [the current NumPy evidence](TESTCASE-NUMPY.md) for its exact revision,
provider, observations, coverage gaps and reproduction instructions.
