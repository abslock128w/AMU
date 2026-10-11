# Phase 3 results: behavioral patch inspection

Baseline: `369c65c25d2a02831e178c04428c56d6e1245465` (initially clean checkout; implementation ancestor `5655355c17ddef95e6dfa08dfd27cdf6272dc119`). The integrated Python payload is identified by SHA-256 `321492f0a49fd95150ebfe78ef001735bd1b6255b91e92982c05153c645accff`. The final local commit identity is recorded alongside the validation artifacts.

## What AMU can explain now

Supported edits produce expression-level verification obligations with exact before/after spans and hashes, changed operations, bounded conditions, observables, supported consumers, limits, and a verification plan. Equality boundaries, simple enclosing guards, omitted defaults, returned comparisons, exception types, sentinel returns, lexical shared state and stable local key definitions have specific check targets. Type annotations and an unchanged method's reclassification into class-state access do not become changed-key claims.

Recommendations distinguish matching input plus assertion, assertion alone, input alone, broad reference and unresolved relationship. Input and assertion must concern the same supported call. Scalar literal parameter cases, local result assertions, simple `pytest.raises`, bound local instances and stable test aliases are supported. Stronger evidence ranks first; broad candidates remain completely retrievable. Enclosing guard matches explicitly leave actual reachability unproved.

Plans separate existing baseline tests from candidate-added test files. They state what to observe and propose an input where justified. Intended expectations default to an unresolved hypothesis, with execution marked not run. Existing source paths, inferred consequences, test assertions and protected executed results remain distinct. General repository code is never executed by patch inspection.

Packets are schema **4**, with a new paginated `obligations` section; opted-in MCP uses **amu-inspection-v4**. Existing sections, omitted counts, fragment reconstruction, source freshness, repository grants and stale-cursor rejection remain. See [usage and migration](phase3-quickstart.md).

## Recommendations and independent evaluation

Exact test IDs are matched against fixed labels. Initial selection means at most three summary checks per case; complete results include every retrievable candidate. Relevant tests are the label's affected behavior or useful adjacent preservation check. Source references alone do not earn behavioral evidence credit.

| Evidence set | Relevant initial / selected, baseline → candidate | Relevant complete / candidates, baseline → candidate | Relevant tests expected | Lost baseline relevant |
| --- | --- | --- | ---: | ---: |
| Five development ranking families | 0/15 → 10/15 | 10/30 → 10/30 | 10 | 0 |
| Fourteen preserved development cases | 13/24 → 13/24 | 13/24 → 13/24 | 16 | 0 |
| Thirteen original frozen holdout cases | 10/13 → 10/13 | 10/13 → 10/13 | 11 | 0 |

The five development families deliberately place four broader tests before two affected tests to test ranking under competition. Their boundary, guarded-return, exception, class-state and omitted-default cases all move the affected tests into the summary. Six relevant tests gain paired input/assertion evidence and four gain assertion evidence. This is development evidence, not an estimate of production accuracy.

The preserved set retains 13 relevant and 11 irrelevant candidates, with three relevant misses and one uncertain label not selected. The original holdout does **not improve selection**: all emitted cases already fit in the summary. It adds full supported input/assertion evidence for one of eleven labeled tests (omitted default), assertion-only partial evidence for seven, and improves complete source-supported paths from 5/8 to 7/8 cases. Strict changed-behavior descriptions improve from 8/12 to 9/12; baseline findings receive credit for factual distinctions even though they had no explicit obligation records.

A fresh-context subagent authored and froze the thirteen cases and separate labels before seeing analyzer outputs. All 54 frozen hashes remain intact. This is procedural separation in a shared workspace, not an external blinded study. Original outputs and scoring are preserved. Returned-predicate and local-key misses informed one bounded development pass, which improves strict descriptions to 11/12 and paired evidence to 2/11. A later review found and removed a false key-removal record in an unchanged method. These replays are **development evidence** and never replace the original holdout result. Shared-state movement remains partial, and the complete wrapper path remains missing.

The exact callback patch `bac5dbc710f2c5e9a8b8854e6f0f2bbdda1c5ab8` and Git-enumeration patch `bf313a9caca5d7cd6dc0d4ab9b9fe5e45a0b720b` were inspected from their archived parents. Their candidate sets and initial selections stay unchanged: 15 and four tests respectively. Final reports add 43 and 47 obligations, of which 35 and 34 concern changed test source. All behavioral evidence remains broad. Two annotation-as-key records exposed by the callback patch were corrected; unfavorable intermediate outputs remain preserved. Actual project test setup supplies reviewable commands, but these target snapshots were not executed.

## Source relationships and actual failures

New source-supported paths include a locally constructed, stably bound instance followed by its direct method call, stable local test aliases, and plain unambiguous `self` reads of class state. Existing import and bounded source-wrapper relationships are reused. Rebinding, member replacement, inherited lookup, decorated/dynamic classes, ambiguous receivers and mocks prevent stronger evidence. The longer wrapper → class factory → instance → helper-alias holdout path remains unresolved.

Two reviewed AMU-owned fixture families have actual protected failures:

| Family | Original protected observation | Regression observation |
| --- | --- | --- |
| Explicit zero | Requested limit `0`, default `5`: returns `0` | Truthiness substitution returns `5`; also accepts a prohibited boolean |
| Stable order | `[3, 1, 3, 2]` deduplicates to `[3, 1, 2]` | Sorting produces `[1, 2, 3]`; text order also changes |

Both originals, compatible refactors and corrected candidates pass the unchanged protected checks; both regression candidates fail. Source, canonical patch, candidate, contract, literal input, checks and runner hashes plus original/candidate observations are bundled in [fixture evidence](../amu/benchmark_data/phase3-counterexamples-v1/evidence.json). Reproduce only these reviewed cases with `python -m unittest tests.test_phase3_counterexamples -v`. Process deadlines, output caps and temporary directories are not a security sandbox.

## Remaining limits and what is not proved

Mapping input contents and repeated state/identity sequences are not fully interpreted. The shared-state holdout recognizes direct instance method paths and a result assertion without proving cross-instance storage. Temporary instance expressions, full alias/wrapper chains, inheritance and arbitrary fixtures remain broad or unresolved. An earlier return can defeat an enclosing guard match. Mutation-only changes such as `pop` to `clear` can have no obligation; exception-message changes and unchanged tuple-element consumers can retain overly broad recommendations. The preserved cases still miss nested callback and inherited tests.

No static finding certifies safety, coverage, intended behavior or a regression. Passing fixture results apply only to their explicit observations. General production precision, broad Python coverage and coding-agent effectiveness remain unproved; no controlled agent trials were run.

## Evidence location

Raw evidence is preserved in the sibling `phase3-validation-2026-10-11` directory: `development-scorecard.json`, `prior-development/RELEASE-REPORT.md`, `holdout-scoring/REPORT.md`, its separate case-05 correction, `real-patches/report.md`, full packets and fragments, source identities, original failed/interrupted validation logs and offline installations. Frozen author inputs and separate labels are in sibling `phase3-holdout`. The small committed [release summary](evidence/phase3-release.json) identifies final checks and measurements; it does not replace those raw artifacts.

## Installed cost and final engineering checks

Twenty static cases (five development, thirteen holdout-derived development, two real patches) were measured serially after other validation finished, with three trials per installed version and interface. Both offline installations use the same available dependency versions; baseline package bytes match the archived baseline exactly. CLI measurements include startup and actual stdout; MCP measurements include transport and its installed CLI subprocess, after separately measured initialization. Two batches of ten exact repository grants respect the existing sixteen-root cap. No target source was imported or executed. All 80 case/interface/version groups keep identical report hashes across their three trials.

| Installed measurement | Baseline | Phase 3 |
| --- | ---: | ---: |
| CLI median, 60 trials | 112.4 ms | 112.9 ms |
| MCP call median, 60 trials | 112.9 ms | 113.6 ms |
| CLI median stdout | 5,978 B | 7,783 B |
| MCP median text | 6,283 B | 8,186.5 B |
| Largest CLI stdout | 13,997 B | 18,282 B |
| Largest compact SDK result envelope | 32,299 B | 41,801 B |
| Callback patch CLI median | 0.597 s | 0.843 s |
| Git-enumeration patch CLI median | 1.205 s | 1.485 s |

The near-equal overall medians do not hide the larger-patch regression: callback adds 0.247 seconds (41%) and Git enumeration adds 0.280 seconds (23%). Profiling the same static requests identifies additional test-call discovery, AST walking and lexical binding work; the candidate scans 1,323 and 1,341 test functions respectively, although only 15 and four become candidates. This is a remaining cost of the current bounded inventory. Median initial text grows about 30%; complete paginated real-patch evidence also grows (callback 490,130 → 673,740 B; Git enumeration 1,063,967 → 1,313,192 B, summed compact page envelopes). These totals are not single responses. MCP initialization medians were 0.585 and 0.294 seconds across two batches; ordering/cache effects prevent a startup-speed claim. Single-machine samples with three trials are not universal latency estimates.

Final frozen Python suite: **2,353 passed, zero failures, 12 skipped in 246.92 seconds**. Eleven skips require external graph opt-ins; one is due to filesystem filename support. Installed CLI, actual base and patch MCP stdio, obligation paging, authorization, source freshness, strict inputs, package resources, dependency constraints and lint checks pass. Offline wheel/source archive checks validate package contents. Browser checks were not eligible locally because web dependencies were unavailable and network installation was not authorized.

An earlier full run reported 2,340 passes, 12 skips and one build-identity failure. Reproduction showed the final314 interpreter launched an older installed AMU while pytest imported current source; a source-edit race was not established. Failed and interrupted runs remain preserved. The final suite uses the matching installed release. No tests were weakened, historical labels changed, dependencies downloaded, arbitrary candidate code run, or changes pushed or deployed.
