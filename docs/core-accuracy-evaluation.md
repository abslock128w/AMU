# AMU core accuracy: implementation and static validation

2026-10-12. Implementation: isolated `core-accuracy-2026-10-12` checkout, branch `accuracy/bounded-python`, starting at evaluated commit `228c87e9045c8904f61a219e4883f62dcfdfa34b`. The original checkout was clean and had no newer work. The final implementation commit is recorded in `commit-receipt.json`.

## Decision

Core accuracy improved in the declared subset: ordinary package imports and property identities are repaired, and located return/exception paths recover useful verification evidence. **Do not advance to a broad regression-assistant or execution/AI trial stage yet.** The untouched holdout still missed its two labeled assertion links, and controlled median initial latency increased 5.7×. The next work should remain narrow: establish changed-behavior evidence for unsupported loop exits, reduce irrelevant analysis/retrieval cost, and independently review the static labels. The current approach need not be replaced, but its scope and cost need further work.

## Established causes and repairs

1. **Ordinary imports:** `_inert_initializer_bindings` rejected every initializer statement except a docstring, pass, or literal version. Werkzeug's four ordinary reexports therefore blocked `from werkzeug import urls`. Explicit imports now allow an unbound submodule name. A separate bounded source resolver follows explicit reexports, relative imports and stable aliases. Competing layouts, wildcard/dynamic exports, shadowing and reassignment remain unresolved; target modules are never imported.
2. **Accessors:** the AST collector keyed getter, setter and deleter by the same qualified name, marked the repeated name ambiguous, and removed it. IDs now distinguish `@getter`, `@setter`, and `@deleter`, while preserving normal qualified names and both source locations. A callback nested in an accessor keeps its own name and is not mislabeled as an accessor. Arbitrary descriptor dispatch is not inferred.
3. **Downstream effects:** the old optional lexical caller join inspected only changed-file, same-module functions. Its test evidence explicitly lacked argument/result propagation. Request-local summaries now compose resolved cross-file return and exact-identity exception paths, track stable local aliases, indexing/iteration, explicit `None` guards and located handler results, with finite call/import/step/output limits. Before/after summaries and consumer operations remain static possibilities rather than observed failures.
4. **Test relevance:** containing-class references were deliberately broad candidates, rather than member-specific protection. Those candidates remain visible with a separate category and exact verification gap. New assertion evidence is attached to related value paths; an unused call result, reassigned local, or unchanged literal return field cannot promote an unrelated assertion. Candidate-added tests keep separate provenance. No execution or detection of a specific regression is claimed.

The existing AST parser and graph provider were retained. A parser replacement prototype was unnecessary because the established import/accessor faults were repairable in the existing code. State-summary expansion, drafting patterns and UI changes were deferred. There is no persistent summary cache: dependency source hashes, inventories, configuration and analyzer version bind report identity, and dependencies are rechecked on every request. This avoids stale dependency reuse at a measurable cost.

## Frozen evidence and provenance

There are 19 development cases: the 16 exposed historical Werkzeug cases; two fresh historical Werkzeug changes (`b9761b5b`, strict integer parsing, and `ea77c357`, LimitedStream buffer handling); and one reviewed, assistant-authored self-contained flow fixture. The holdout is historical Werkzeug change `0ac9f126`, handling an unclosed quote. Frozen selection, rubric and all source hashes are in `selection.json`, `rubric.json`, and `freeze.json`.

**The same assistant selected and source-labeled the fresh cases. This is not independent or blinded validation.** Labels preceded output inspection. The holdout's source was necessarily inspected for labeling, but its analyzer output remained sealed until implementation and development validation ended. Its first failure was recorded in `holdout-first-inspection.json`; implementation hashes confirm no subsequent tuning. Additional performance runs are exposed replays, not fresh holdouts.

The unchanged installed baseline was rerun. The first replay setup accidentally resolved the virtual-environment interpreter symlink; a later setup also inherited an older same-version package until installation was forced. Those failed setup logs remain preserved and are excluded from scores. The valid comparisons use `baseline-valid` and the verified release installation. Earlier candidate receipts remain separate. The final property case was replayed after the nested-name correction; all retained development evidence was verified against the release build's full content hash in `final-development-results/attestation.json`.

Only AMU code, AMU tests and reviewed self-contained fixtures were executed. External repository source was inspected as data. No external-repository tests or generated drafts, symbolic execution or mutation trials ran. The AMU test suite exercised its own reviewed, self-contained verification fixtures. No push, publication or deployment occurred.

## Before / after

| Measure | Unchanged baseline | Candidate | Denominator / meaning |
|---|---:|---:|---|
| Selected development entity names | 27/28 | 28/28 | Predeclared names; not complete semantic recall |
| Original Werkzeug selected names | 24/25 | 25/25 | The Response mimetype setter is recovered |
| Fresh labeled development source relationships | 0/5 | 5/5 | Four fixture caller pairs and `_plain_int` → `parse_age` |
| Resolved negative source pairs | 0/2 | 0/2 | Lower is better; shadowed/rebound callers |
| Fresh relevant assertion IDs established | 1/4 | 4/4 | Three fixture assertions plus existing `test_plain_int` |
| Irrelevant assertion IDs promoted | 0/6 | 0/6 | Lower is better; includes unrelated, reassigned and shadowed results |
| Development cases with any recommendation | 7/19 | 10/19 | Includes broad references; not protection or relevance accuracy |
| Development recommendation occurrences | 20 | 44 | Unlabeled additions are exploratory, not precision evidence |
| Holdout selected names | 1/1 | 1/1 | `parse_options_header` |
| Holdout labeled source relationship | 0/1 | 1/1 | `parse_options_header` → `Request._parse_content_type` |
| Holdout relevant assertions established | 0/2 | 0/2 | Both predeclared assertions remain missed |
| Holdout irrelevant assertion promoted | 0/1 | 0/1 | `test_dump_options_header` |

All 20 selected supported-file projections and complete evidence retrievals succeeded. Full-patch RST support remains unchanged: the original evaluation accepted only 3/16 exact Werkzeug patches. The fresh five source relationships quantify a tiny supported subset, including authored fixtures. Practical applicability across all selected cases is weaker: many methods, descriptors and dynamic paths remain unresolved. No general precision/recall percentage is justified.

## Exact recoveries and avoided promotions

- URL case 01 now recommends all **13 existing baseline URL tests**, including `tests/test_urls.py::test_iri_support` and the URI/IRI round-trip tests. Candidate-added credential/port-zero tests do not count as baseline discovery. Core source relationships now include `iri_to_uri` → `EnvironBuilder.__init__`, `Response.get_wsgi_headers`, and `get_current_url` callers. These source/return relationships do not prove a URL regression or reaching edge-case input.
- Case 10 now records `Response.mimetype@setter` as modified, `Response.mimetype@deleter` as added, and the getter separately. Its nested `mimetype_params.on_update` callback retains a distinct identity and location.
- Fresh case `b9761b5b` recovers `_plain_int` → `http.parse_age`, including its identified ValueError handler and handler-result path. The authored fixture recovers `load` → `consume`, a `not-none` constraint at `guarded` indexing, and an exact ValueError handler at `handled`; the candidate's TypeError is a different exception identity.
- `test_unrelated_assertion`, `test_wrapper_unrelated`, `test_shadowed`, and `test_reassigned` are not promoted. Additional regression tests cover similarly named methods, another module's same-named function, imported-receiver reassignment, unchanged return fields, container aliasing, unsupported control flow, cycles and dependency cursor invalidation.
- The eight previously noted broad Request test occurrences remain visible as broad references; **none is promoted as a behavior-specific assertion**. Four additional LimitedStream class references are also broad candidates, not evidence of protection for the buffer change. Thus the recommendation-count increase must not be interpreted as pure relevance recall.

## Holdout failure and remaining boundaries

The holdout finds the labeled production caller and four baseline source-reference tests, but emits **zero behavioral obligations** for the newly added `while ... else: break`. It therefore does not connect the two labeled parameterized assertions to changed behavior. An unrelated unparameterized test has a raw located result-assertion path, but no changed-obligation attachment. The gap wording can be too coarse: the missing link is a supported changed-behavior obligation, rather than universally absent assertion syntax. This failure was recorded before any later work and was not repaired against the holdout.

Werkzeug routing case 15 still has no test recommendation or resolved NumberConverter → matcher → NotFound chain. Converter lookup and matcher dispatch are dynamic; exact static exception construction does not resolve those receivers. The graph-on diagnostics return 13 URL recommendations and zero routing recommendations, with the existing provider's provenance and uncertainty retained. Their timings are diagnostics, not a controlled provider comparison.

Other limits include decorated and async APIs, descriptor result propagation, nested dispatch, mutation/state summaries, destructuring, unsupported container aliasing/control flow, and finite composition. Exception handlers are composed by exact identity; superclass handling and opaque call side effects are outside this subset. `may_return_none=false` means no supported possibility was established, not proof of a non-null return. Reports are explicitly incomplete at unresolved boundaries and at output limits. The repository-wide scan also produces many unrelated uncertainty records; some detail reaches the 200-record-per-phase cap. This is both a reporting and cost limitation.

## Cost

120 sequential isolated CLI calls covered all 20 cases: three baseline and three candidate runs per case, alternating order, with graph/models off and identical reviewed dependency paths. No other validation jobs remained active. First runs are fresh Python processes, not flushed OS filesystem caches; repeated runs are also fresh processes because there is no persistent summary cache.

| Controlled measure | Baseline | Candidate |
|---|---:|---:|
| Median first process | 0.389 s | 2.203 s |
| Median repeated process | 0.365 s | 2.178 s |
| Maximum first process | 0.626 s | 3.867 s |
| Median child peak RSS | 64.4 MiB | 93.6 MiB |
| Maximum child peak RSS | 72.6 MiB | 102.5 MiB |
| Median compact summary | 12,134.0 bytes | 13,242.5 bytes |

Development exhaustive retrieval: 4,019,714 → 10,690,897 bytes total; median 206,514 → 540,741. Holdout retrieval: 159,769 → 745,574 bytes. These counts include repeated protocol metadata and fragments across page calls; they are audit cost, not one logical report's size. The source/dependency scan and summaries are recomputed on every page request. All 20 report hashes stayed stable over repeated controlled calls for both builds. No memory reduction or caching benefit is claimed.

## Validation and reproduction

- Final full AMU suite: **2,447 passed, 14 skipped**, in 155.85 s. The 15 new focused regressions also pass; original import/accessor failures and later completeness failures are preserved separately.
- Focused final adapter/identity tests: **119 passed**. Lint and diff-whitespace checks pass. The wheel build succeeds, and installed source bytes match the checkout.
- Real MCP initialize/list/static `amu_analyze_patch` calls match the reviewed manifest and the installed CLI's complete report hash. No target code runs.
- Preservation checks: original source 372 files, installed package 101 files, 16 manifests, 4,667 baseline files and 582 old diagnostic source files unchanged; original Git HEAD/status unchanged. New selection hashes and post-holdout implementation hashes are also verified.

Receipts: `release-full-tests.txt`, `validated-focused-tests-final.txt`, `release-build.txt`, `release-lint.txt`, `installed-mcp-receipt.json`, `preservation.json`, `development-scores.json`, `holdout-scores.json`, `holdout-first-inspection.json`, `final-development-results/attestation.json`, and `controlled-performance/summary.json`. Per-case directories retain exact commands, environments, raw outputs, page/fragment hashes and measurements. Create-only replay directories protect earlier results.

```sh
AMU_CHECKOUT=/Users/Abslock128/Documents/ChatGPT/AMU/core-accuracy-2026-10-12
AMU_EVAL=/Users/Abslock128/Documents/ChatGPT/AMU/core-accuracy-evaluation-2026-10-12
AMU_PYTHON="$AMU_EVAL/final-installed-v2/bin/python"
cd "$AMU_CHECKOUT"
"$AMU_PYTHON" -m pytest -q
"$AMU_PYTHON" -I "$AMU_EVAL/check_mcp.py"
"$AMU_PYTHON" -I -B -m amu.cli analyze-patch --repo "$AMU_EVAL/cases/bounded-flow/baseline" --base worktree --patch-file "$AMU_EVAL/cases/bounded-flow/supported.patch" --json
"$AMU_PYTHON" "$AMU_EVAL/run.py" --python "$AMU_PYTHON" --output rerun-development --split development --workers 2
"$AMU_PYTHON" "$AMU_EVAL/score.py" --candidate rerun-development --split development --output rerun-development-scores.json
"$AMU_PYTHON" "$AMU_EVAL/run.py" --python "$AMU_PYTHON" --output rerun-holdout --split holdout
"$AMU_PYTHON" "$AMU_EVAL/score.py" --candidate rerun-holdout --split holdout --output rerun-holdout-scores.json
"$AMU_PYTHON" "$AMU_EVAL/performance.py" --output performance-replay
```

The holdout replay command reuses an exposed case; it cannot produce a new untouched holdout. The installed baseline remains at `../input-suggestions-2026-10-11/installed/bin/python`. The source-only preparation script uses already-preserved bare Git objects and must use a fresh output directory to recreate inputs.
