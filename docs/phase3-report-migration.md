# Real-patch report organization: schema 5

`concrete-patch-impact` packets now use schema **5**; opted-in MCP uses **amu-inspection-v5**. Intent inspection remains v1. The analysis identity is `python-markdown-concrete-patch-v4`, and recommendation metadata is `behavioral-test-evidence-v4`. Restart retrieval instead of reusing older cursors.

The change separates production, verification, documentation and uncertain source roles. Each changed file, entity, finding and obligation retains a `source_role` with its basis and uncertainty. Classification reads baseline `pyproject.toml` pytest test paths and setuptools include patterns where available, then corroborates AMU's existing discovery conventions with parsed test declarations. A test-like filename alone produces an uncertain role. `tests/` helpers require parsed baseline tests in that directory. Executable declarations outside corroborated verification scope have an explicitly inferred production role. Custom collection, plugins, other configuration formats and actual execution remain unverified. Configuration contents and absence join freshness checks and report identity; classification does not broaden test discovery.

The summary's `obligations` prioritizes production when present and still shows verification obligations for test-only patches. `obligation_counts` accounts for every role; `omitted_obligations` still counts all undisplayed obligations, including test-source obligations. `verification_changes` reports all test/helper entity and obligation counts and test declaration statuses in changed files. Documentation remains content-only. Full `changes`, `findings`, `obligations`, `recommended_checks`, `state_dependencies`, and `uncertainties` remain paginated and reconstructable.

Each preview includes what changed, why it may matter, the strongest evidence category, a next inspection step and a `verification_group` reference. `verification_groups` shares candidate references and unresolved reasons across matching plans instead of repeating a broad list under every obligation. Group omissions are explicit; full plans remain in `obligations`. Candidate recommendations retain their exact source paths, hashes and all associations. The summary's `kind` no longer calls broad evidence behavioral.

Recommendation associations retain `level` for compatibility and add `evidence_category`: supported behavior-related assertion, supported input, supported source path only, broad reference or unresolved relationship. These describe source evidence, not confidence or coverage. The behavioral resolver is unchanged. No real-patch relationship gains assertion or input credit in this release.

`test_provenance` on test changes and recommended candidates distinguishes unchanged, modified, added and removed declarations using before/after AST hashes and assertion locations/expressions. Formatting-only edits retain unchanged syntax status. File source hashes still capture exact bytes. Changed imports, fixtures and setup can alter execution even when test syntax is unchanged. An unchanged test is not automatically a protected executed check. Recommendation evidence always comes from the baseline; modified or added assertions never become independent proof of their own expectations. Candidate-added test functions inside existing files are included in test-change counts, not baseline discovery.

There is no new detail section or CLI argument. Use the usual commands:

```sh
amu analyze-patch --repo /absolute/parent-snapshot --base worktree --patch-file /absolute/saved.diff --json
amu analyze-patch --repo /absolute/parent-snapshot --base worktree --patch-file /absolute/saved.diff --section obligations --json
amu analyze-patch --repo /absolute/parent-snapshot --base worktree --patch-file /absolute/saved.diff --section changes --json
amu analyze-patch --repo /absolute/parent-snapshot --base worktree --patch-file /absolute/saved.diff --section recommended_checks --json
```

Follow every `page.next_cursor` to retrieve complete detail. No target module is imported or executed by inspection. No stronger runtime, intended-contract, safety or regression claim follows from these reports.
