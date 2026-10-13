# Real-patch usefulness: development results

Report organization is implemented. **The behavioral capability target remains unmet.** Neither unchanged patch gained a supported production-to-assertion connection. No resolver was added just to promote these tests. These are development results, not independent validation or evidence of improved coding-agent effectiveness.

Starting commit: `a2d6e151a0f451c8cc67bbb4d4fa8ddd2855b734`, clean checkout. The preserved Phase 3 analyzer was reproduced before editing on the exact archived parents; both complete logical reports match the historical final-release reports. The original build, target snapshots, patches, frozen cases and labels remain intact. All 54 holdout hashes were rechecked.

## Root cause and stopping decision

The analyzer generates expression obligations for all changed Python entities, including tests, and used one undifferentiated summary pool. That explains the 35 callback and 34 Git test-source obligations. Both historical summaries already happened to choose three production obligations first; this release makes that policy explicit and exposes the full role counts and verification changes.

The broad recommendations are chiefly legitimate missing/unsupported observation paths, not a demonstrated resolver defect. Callback tests call `execute`, which calls `_boundaries`, but assert graph diagnostics, declaration selection, paging, cursor behavior or request failures. They do not observe the new callback-boundary records. The callback-parent source has no new callback helper or new focused tests. Git candidates call `git_head` to prepare persisted refresh metadata, then assert a separate CLI invocation. A wrapper-return rule or literal-key projection would incorrectly join setup data with another call's asserted output. CLI dispatch, fixture/helper effects, serialized state and cross-module packet construction remain unresolved.

The [diagnostic table](phase3-real-patch-diagnostic.md) records baseline candidates, call/input/assertion lines, exact gaps, and why the small suggested extensions cannot responsibly close them. New callback tests in the later Git snapshot are not callback-parent evidence. No claim is made that no suitable test could exist outside the bounded search.

A genuine display defect was fixed: summary code used `behavioral_rank < 4`, which labeled rank-3 broad evidence as behavioral. It now reserves that label for actual input or assertion evidence. The underlying evidence levels and candidate source paths are unchanged.

## Product behavior

| Patch | Production obligations, before → after | Test-source obligations, before → after | Initial production/test obligations, before → after | Existing candidates | Stronger connections | Lost relationships |
| --- | --- | --- | --- | ---: | ---: | ---: |
| Callback | 8 → 8 | 35 → 35 | 3/0 → 3/0 | 15 → 15 | 0 | 0 |
| Git enumeration | 13 → 13 | 34 → 34 | 3/0 → 3/0 | 4 → 4 | 0 | 0 |

Before counts were externally classified during review; after counts and their bases are in the report. Callback keeps all 20 source associations; Git keeps all four. Their 20 and 44 obligation-evidence records remain broad. All 19 existing candidates have unchanged baseline test bodies. No candidate-added assertion is credited as improved discovery.

Callback's changed test file adds four declarations and preserves 15 baseline declarations. Git's changed test files add five, modify four and preserve 56; counts concern declarations in changed files, not the whole repository. The added `tests/test_git_changed_files.py` remains separate from baseline candidates. Changed helpers and their obligations remain retrievable. Each changed test has before/after AST hashes and assertion references in `changes`; baseline recommendations also carry provenance. Formatting-only edits are distinguished from modified test syntax. Unchanged source is not automatically a protected executed result.

The default report now exposes production/verification/documentation/uncertain roles, classification basis and uncertainty. Production obligations show source operations, possible effects, strongest available evidence, a next inspection step and shared unresolved guidance. Shared groups reference existing checks without placing the same broad list beneath every obligation. Test-only patches still show their own obligations. All underlying findings, associations and uncertainties remain retrievable through the existing sections, with complete fragment reconstruction and explicit omissions.

See [schema-5 migration](phase3-report-migration.md). Role classification uses baseline configuration where supported plus corroborated source conventions; it does not expand discovery or infer execution from filenames. Package/source identities, grants, source/config freshness, deterministic ranking and response limits remain enforced.

## Verification and limitations

Ordinary regressions cover mixed/test-only/documentation/uncertain patches, corroborated helpers, configured test roots, unchanged/modified/added/removed tests, configuration-bound cursors, symlink refusal, shared guidance and complete pagination. The existing behavioral suite retains valid direct assertion paths, unrelated assertions, multiple-call separation, rebinding, ambiguity, mocks and unsupported wrapper/subprocess boundaries. Two opt-in integration regressions replay the exact real patches and verify unchanged target hashes. No historical target program was executed.

Replays preserve the five ranking cases (30 candidates), fourteen earlier development cases (24 candidates) and thirteen holdout-derived cases (13 candidates), with no lost relationships, changed behavioral levels or changed initial selection. Historical labels and original holdout results were not rewritten.

The behavioral target is explicitly unmet. Unsupported wrapper/result propagation, arbitrary dispatch, fixture effects, mapping contents and runtime reachability remain limits. Configured role support is limited to baseline pyproject pytest test-path arrays and setuptools include patterns plus existing AMU conventions; custom collection and other config formats remain unverified. A role is an inference with a preserved basis. The added metadata increases output size; it is not a speed or precision improvement.

Raw before/after packets, every detail page, reconstructed evidence, source identities, diagnostic, regression logs, offline packages and measurements live in sibling `phase3-real-usefulness-2026-10-11`. Its `comparison.json` enumerates exact initial obligation IDs, selected test IDs, provenance, and relationship preservation. `integrity.json` binds analyzer, installed package, wheel and frozen inputs. Earlier development receipts remain separate and unchanged.

## Final checks and cost

The final integrated Python suite passed **2,363 tests, zero failures, 12 skips in 185.63 seconds** using the matching offline installation. Eleven skips need separate external-graph opt-ins; one is filesystem filename support. Installed CLI and actual base/patch MCP stdio checks passed, including source identity, explicit repository grants, strict inputs, fresh cursors, complete obligation retrieval and bounded wire responses. Offline wheel/source-archive checks, dependency constraints, package identity, lint and whitespace checks passed. Web dependencies remain unavailable; no network installation was attempted.

| Measurement | Callback Phase 3 → candidate | Git Phase 3 → candidate |
| --- | ---: | ---: |
| Initial compact service packet | 15,112 → 20,080 B | 18,281 → 26,357 B |
| Complete paginated evidence, compact page envelopes | 673,740 → 833,867 B | 1,313,192 → 1,596,770 B |
| Installed CLI median | 0.973 → 1.187 s | 2.825 → 2.140 s |
| Installed CLI stdout | 15,113 → 20,081 B | 18,282 → 26,358 B |
| Installed MCP call median | 1.203 → 1.189 s | 2.257 → 2.135 s |
| Installed MCP text | 15,943 → 21,101 B | 19,252 → 27,610 B |
| Compact SDK envelope | 34,623 → 45,639 B | 41,801 → 59,585 B |

Three serial trials per version/interface, on the same two exact parent snapshots and patches, ran after other validation. CLI includes startup; MCP includes transport and the installed CLI subprocess, with initialization recorded separately in raw measurements. Every case/version has identical report hashes across all six CLI/MCP trials. Order/cache effects and sample size prevent a speed claim. Output growth is a real cost: approximately 33%/44% for initial packets and 24%/22% for complete callback/Git evidence. Repeated page metadata contributes to full-evidence totals; these are not single-response sizes. The largest real-patch SDK envelope remains below the existing 65,536-byte limit.

## Reproduction

From the development checkout, use the preserved installation for the before comparison and the matching candidate installation for tests. These commands only execute reviewed AMU analyzers/tests; the archived patch targets are source inputs.

```sh
# Full eligible AMU suite, including the two exact real-patch regressions.
AMU_REAL_PATCH_INPUTS="$PWD/../phase3-validation-2026-10-11/real-patches/inputs" \
  ../phase3-real-usefulness-2026-10-11/installed/bin/python -m pytest -q

# Exact baseline replay; choose a new output directory.
../phase3-validation-2026-10-11/installed-release/bin/python \
  ../phase3-validation-2026-10-11/real-patches/collect_real_patches.py collect \
  --evidence ../phase3-validation-2026-10-11/real-patches \
  --source-root ../phase3-validation-2026-10-11/real-patches/final-release-analyzer-source \
  --output ../phase3-real-usefulness-2026-10-11/reproduce-before

# Exact candidate replay; source changes are applied in memory to archived parents.
../phase3-real-usefulness-2026-10-11/installed/bin/python \
  ../phase3-validation-2026-10-11/real-patches/collect_real_patches.py collect \
  --evidence ../phase3-validation-2026-10-11/real-patches --source-root . \
  --output ../phase3-real-usefulness-2026-10-11/reproduce-after

# Installed measurements; run after other checks finish.
../phase3-real-usefulness-2026-10-11/installed/bin/python \
  ../phase3-real-usefulness-2026-10-11/measure_real_installed.py

# Installed interface and offline package checks.
../phase3-real-usefulness-2026-10-11/installed/bin/python -I scripts/smoke_installed.py
../phase3-real-usefulness-2026-10-11/installed/bin/python -I scripts/smoke_mcp_patch.py \
  --python "$PWD/../phase3-real-usefulness-2026-10-11/installed/bin/python"
../phase3-real-usefulness-2026-10-11/installed/bin/python scripts/check_constraints.py
```

The local commit identity is recorded in the sibling evidence directory's `commit.json`. The diagnostic stopping rule is honored: organization is complete; stronger behavioral evidence on these real patches is not.
