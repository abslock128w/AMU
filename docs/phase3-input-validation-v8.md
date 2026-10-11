# Schema-8 input suggestions: local validation

The [supported rules and migration](phase3-input-suggestions-v8.md) describe the exact model. This work started from `07e2981575d240f01954c9c84efabce3d3ff4326` with a clean checkout. The implementation uses the existing paired source events, obligations, check specifications, draft gates, and lossless retrieval. It adds no drafting template. Inspection remains static.

## Input choice and reviewed execution

Both demonstrations start with a patch and no literal argument in the check specification. Preparation selects AMU's first ranked distinguishing, complete scalar suggestion, then supplies an independently authored fixture expectation through the existing interface.

| Patch | AMU-selected input | Narrow local observation | Separately supplied fixture expectation |
| --- | --- | --- | --- |
| `limit < 10` → `limit <= 10` | `limit=10` | Built-in integer predicate changes from false to true | The range includes ten; returned value equals `True` |
| `value is not None` → `value` as an `if` guard | `value=0` | Built-in scalar predicate changes from true to false | Only `None` is absent; zero remains selected; returned value equals `True` |

Before expectation submission, whole-function behavior is `unknown` and reachability is `unestablished`. The expectation references are agent-authored contracts for these AMU-owned fixtures, supplied explicitly outside the analyzer. The existing `user-supplied-task-requirement` and `user-designated-baseline-preservation` interface labels identify the caller's explicit designation; AMU did not discover or independently validate those contracts.

The complete generated tests, all module versions, import-evidence files, and execution runner were reviewed before running. Each execution copied the hash-identified module and unchanged returned test into a temporary directory and invoked offline pytest with plugin loading disabled. Temporary directories and timeouts are process controls, not security sandboxes.

| Fixture version | Boundary bug-fix check | Truthiness preservation check |
| --- | --- | --- |
| Baseline | Assertion fails: original fixture violates the supplied range requirement | Passes |
| Candidate | Passes: supplied bug-fix requirement satisfied | Assertion fails: designated preservation behavior violated |
| Compatible variant | Passes | Passes |
| Violating variant | Assertion fails | Assertion fails |

All four failures are the intended `False == True` assertion mismatch. There are no import, setup, or type failures. This establishes behavior for the eight identified fixture/test pairs. The mapping family contributes absent/present-key inspection probes and stays partial because scalar draft templates cannot represent its dictionary argument or key-presence observation.

## Predeclared development evidence

[`input_suggestions_v8.json`](../tests/fixtures/input_suggestions_v8.json) specified expected classes and abstentions before the new analyzer was run. It contains 17 miniature cases: 12 supported cases and five abstentions. The cases guided implementation and are development evidence.

- Missing required distinguishing/key-state probe classes: **0/16**; required adjacent checks also passed.
- Unnecessary or invalid suggestions observed under the declared model/forbidden-value checks: **0/37** returned suggestions.
- Incorrect declared readiness classifications: **0/34** probes with declared readiness expectations.
- Unjustified reachability claims: **0/37**; every probe retains `unestablished` reachability.
- Correct required abstentions: **5/5**.

Additional regressions cover patch/configuration-bound suggestion identities, changed specifications and cursors, per-obligation/report budgets, complete fragment reconstruction, literal defaults, full hashes for truncated signature previews, bool/int separation, local names, module rebinding, method receivers, typed mapping representatives, and scalar returns that are not truthiness conditions. No label or frozen historical case was changed to improve these counts.

## Skip reconciliation and final checks

The previous increase from 12 to 14 skips consists exactly of these two cases:

- `test_archived_patch_roles_and_candidates[callback-8-35-15]`
- `test_archived_patch_roles_and_candidates[git-enumeration-13-34-4]`

Both skipped with `requires explicitly selected preserved static patch inputs` because the prior invocation omitted `AMU_REAL_PATCH_INPUTS`. Selecting the preserved local `phase3-validation-2026-10-11/real-patches/inputs` directory produced **2 passed** without changing skip markers. The final full-suite invocation selects that directory.

The remaining 12 exclusions are four opt-in external graph-refresh checks, seven opt-in NumPy graph checks, and one filesystem case whose exact reason is `filesystem does not permit non-UTF8 filename bytes`. They do not exclude the supported local static suggestion/drafting workflow.

The initial reconciliation run crossed source edits and failed one runtime-build-hash comparison; it is recorded separately from final validation. The final eligible suite passed with **2,434 passed, 12 skipped in 199.86 seconds**. Installed-interface, package, and source-archive receipts are recorded in the local evidence directory described below.

Installed CLI and MCP checks agree on suggestion IDs and unchanged draft bodies, reject stale suggestions and argument overrides, and preserve target files. The installed v1/v8 MCP smoke verifies manifests, opt-in behavior, action denial, paging, wire limits, invalid inputs, repository grants, symlink boundaries, and stale cursors. Source archive checks require the new rule module, documentation, tests, labels, and fixture source.

## Fixed-input cost and compatibility

Measurements use the installed package with models disabled, fixed local sources, and compact UTF-8 JSON. Complete size is the initial summary plus every paged response in every section, including empty sections. This definition adds the initial summary to the older detail-only saved measurements. The two schema versions below use the same definition and inputs.

| Input | Initial bytes, v7 → v8 | Complete bytes, v7 → v8 | Complete increase | v8 suggestions |
| --- | ---: | ---: | ---: | ---: |
| Boundary fixture | 8,576 → 9,548 | 52,352 → 58,925 | 6,573 | 3 |
| Truthiness fixture | 7,749 → 8,672 | 46,896 → 51,915 | 5,019 | 2 |
| Preserved callback patch | 14,317 → 14,682 | 848,184 → 852,087 | 3,903 | 0 |
| Preserved git-enumeration patch | 18,566 → 18,931 | 1,615,336 → 1,621,279 | 5,943 | 0 |

Both real patches retain identical finding, obligation, and recommendation counts. Their edits do not match the three supported families, so zero suggestions is the expected compatibility result. Their added bytes are schema metadata and input-guidance fields. Fixture costs include the new source-bound derivations. Initial previews remain limited to two suggestions and complete detail remains paged.

Single v8 installed CLI summary calls, including startup, took 0.200 s, 0.122 s, 0.944 s, and 1.652 s respectively. Installed service summary calls took 0.026 s, 0.009 s, 0.726 s, and 1.930 s. Complete service retrieval took 0.066 s, 0.057 s, 18.464 s, and 77.477 s, because each stateless page request recollects and validates evidence. These single-run timings vary with machine load and support no speed-improvement claim.

## Local receipts and reproduction

The preserved sibling directory `/Users/Abslock128/Documents/ChatGPT/AMU/input-suggestions-2026-10-11` contains preparation scripts, patch/specification/proposal JSON, complete generated tests, pre-execution reviews, eight execution outputs, evaluation counts, skip accounting, installed-interface/smoke results, v7/v8 cost records, and offline builds. Historical directories are preserved.

From the checkout, with the existing local dependency environment:

```sh
AMU_REAL_PATCH_INPUTS=/Users/Abslock128/Documents/ChatGPT/AMU/phase3-validation-2026-10-11/real-patches/inputs \
  ../input-suggestions-2026-10-11/installed/bin/python -m pytest -q -rs
../input-suggestions-2026-10-11/installed/bin/python -I ../input-suggestions-2026-10-11/check_installed.py
../input-suggestions-2026-10-11/installed/bin/python -I scripts/smoke_mcp_patch.py \
  --python /Users/Abslock128/Documents/ChatGPT/AMU/input-suggestions-2026-10-11/installed/bin/python
../input-suggestions-2026-10-11/installed/bin/python ../input-suggestions-2026-10-11/prepare.py
```

Review the returned drafts, complete fixture sources, and `run_reviewed.py` before reproducing the separately authorized executions. The runner checks the review's source and draft hashes. `measure_installed.py` and `measure_cli.py`, invoked with the selected installation's Python `-I`, reproduce the fixed-input cost method without executing target code. Offline builds use `python -m build --no-isolation`; installation uses `pip --no-index --no-deps` with already available local dependencies.
