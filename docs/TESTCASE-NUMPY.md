# Test case · amu on numpy/numpy

> Historical provider run from 6 September 2026, with a current workflow note below. It is a focused large-repository integration test, not proof that every NumPy relationship is parseable. Current AMU maps the live worktree and refuses approval or a complete refresh delta when the available graph evidence is incomplete.

**Purpose:** prove `amu` (TWC Thugs) works on a real, large, mixed-language repository — not just on itself — and record what broke.
**Date:** 6 Sep 2026, 14:35–14:45 IST · **amu** 0.2.0 · **Entire CLI** 0.10.5 · **entire-graph** v0.4.0 · macOS arm64
**Fix commit:** `093c3df` · **Entire checkpoint:** `01M1TZXJP22S864ME7QTF1MNRN`

## 1. Setup

```bash
git clone --depth 50 https://github.com/numpy/numpy.git /tmp/numpy   # HEAD 65b30cf
cd /tmp/numpy
amu init
```

| Metric (from `entire graph index`) | Value |
|---|---|
| files | 1,666 |
| symbols | 27,709 |
| relations | 97,968 |
| snapshot lines | 164,706 |
| **unparsed files reported by amu on the first line** | **323** (Fortran `.f90`, YAML workflows, C headers) |
| wall time | 2 min 40 s |

Pass criterion: `amu init` exits 0 and prints the unparsed count (honesty line) — **pass**.

## 2. Target

`numpy/_core/numeric.py#ones` — `def ones(shape, dtype=None, order='C', *, device=None, like=None)` — change class `signature`.
Chosen because it is public, widely called, and **defined twice** in the repo (`numpy/_core/numeric.py`, `numpy/matlib.py`), which is exactly the ambiguity the router/map must not paper over.

## 3. Steps, expected, actual

| # | Command | Expected | Actual (before fix) | Actual (after fix `093c3df`) |
|---|---|---|---|---|
| 1 | `amu map --symbol numpy/_core/numeric.py#ones --change signature` | tree of consumers; non-sound nodes carry reason + verify | **crash** `TypeError: 'NoneType' object is not iterable` | 6 sound · 0 guessed · 0 unknown, qualifier present, 1.2 s |
| 2 | same, after crash fix only | consumers of `ones` | **0 consumers, silently**; 323 unknown nodes (one per unparsed file repo-wide) | — |
| 3 | `amu plan --targets numpy/_core/numeric.py#ones:signature --approve` | 4 phases, contract written, planner decision | not reached | `proceed` (0.8); phase 3 = 6 nodes / 4 files; `.amu/contract.json` written |
| 4 | `amu check --phase 1 --no-tests` | 0 blocking, exit 0 | not reached | `blocking 0 · warnings 0`, exit 0 |
| 5 | `echo "# drift" >> numpy/lib/_polynomial_impl.py` (a phase-3 file) then `amu check --phase 1 --no-tests` | `PHASE_DRIFT` + delta brief, exit 1 | not reached | `✖ PHASE_DRIFT numpy/lib/_polynomial_impl.py` · delta brief: 3 consumers · **exit 1** |
| 6 | `git checkout numpy/lib/_polynomial_impl.py && amu check --phase 1 --no-tests` | green again, exit 0 | not reached | exit 0 |
| 7 | `amu done` | sweep summary, doc candidates, memory block written; exit 0 | not reached | 0 findings outside contract · 0 doc candidates · **26 memory entries** written into numpy's `CLAUDE.md`/`AGENTS.md` |

The historical run used `--no-tests` because NumPy's test suite needs a compiled build. **Those exit-0/green results no longer describe current behavior:** skipped tests now produce a diagnostic-only check, exit 1, and cannot advance phases or satisfy `done`. Current checks run the configured test command directly and record its outcome.

### Final map (after fix)

```
radius     4 files · 2 packages · 1 exports · 6 consumers · 0 tests
resolution 6 sound · 0 guessed · 0 unknown — graph reported 0 unresolved sites at depth 2;
           dynamic dispatch and files outside parser coverage (none listed) are not counted;
           323 files unparsed repo-wide (not counted as nodes)
next       amu plan --targets numpy/_core/numeric.py#ones:signature --approve

n1 ● sound will_break  numpy/lib/_polynomial_impl.py#roots         resolved CALLS edge at depth 1
n2 ● sound will_break  numpy/lib/_polynomial_impl.py#polyint       resolved CALLS edge at depth 1
n3 ● sound might_break numpy/lib/_polynomial_impl.py#poly1d.integ  resolved CALLS edge at depth 2
n4 ● sound will_break  (re-export, no file)#numpy.ones             resolved CALLS edge at depth 1
n5 ● sound will_break  numpy/_core/multiarray.py#copyto            resolved CALLS edge at depth 1
n6 ● sound will_break  numpy/matlib.py#empty                       resolved CALLS edge at depth 1
```

Raw payloads: `/tmp/numpy-ones-impact.json` (impact), `/tmp/numpy-ones-map.json` (map) at the time of the run; the memory block
written into numpy's `CLAUDE.md` starts with `## What amu has learned about this repo (TWC Thugs · updated 2026-09-06T09:13:45Z …)`.

## 4. Bugs found and how they were fixed

| # | Root cause | Why it matters | Fix (`amu/…`) | Regression test |
|---|---|---|---|---|
| B1 | `entire graph impact` returns `"entries": null` for empty sections on large repos; `build_map` iterated it | hard crash on any symbol with an empty callee/type section | `classify.py`: `.get("entries") or []`, `.get("endpoint") or {}` | `tests/test_map.py::test_ambiguous_symbol_is_unknown_not_silent` (uses `"entries": None`) |
| B2 | `ones` has two definitions → impact sets `disambiguation_required: true` and returns **no edges**; amu passed the bare name | the map reported **0 consumers** for a function numpy calls everywhere — the worst possible failure for a blast-radius tool, because it looks like a clean answer | `entire.py`: `impact_json(..., file=…)` passes `--file`; `cli.py` always sends the path from `path#name`; `classify.py`: a still-ambiguous symbol becomes an **unknown** node whose reason lists the competing definitions | `test_ambiguous_symbol_is_unknown_not_silent` |
| B3 | every repo-wide `partial_failures` entry became an unknown node | 323 (then 238 C/H) nodes drowned the 6 real consumers | `classify.py`: only `E_PARSE_ERROR` files in the root's own package **and** language become nodes; everything else is counted in the qualifier as `N files unparsed repo-wide (not counted as nodes)` | `test_syntax_error_file_in_root_package_becomes_unknown_node` |
| P1 (superseded) | impact ran against the working-tree snapshot (uncached) | ~80 s per symbol; `--file` mode multiplied that by the number of exports | Historical performance change: use the cached committed tree. **Current behavior is intentionally stricter:** an approvable map is a stable live-worktree pre-edit baseline; the `HEAD` cache is never treated as proof of uncommitted relationships. | The 80 s → 1.2 s timing is historical only. |

A fourth, environmental finding: the clean-checkout rehearsal's `pip install -e /tmp/amu-clean` had re-pointed the `amu` command at the
throw-away clone, so the first fix appeared not to work. `python3 -c "import amu; print(amu.__file__)"` is now part of the checklist.

## 5. Honesty checks that held on numpy

- The 323 unparsed files were reported on the first line of `amu init`, and again in every map's qualifier — never hidden, never inflated into fake nodes.
- After B2, ambiguity is an *unknown* node with the reason `ambiguous: 2 definitions of ones (numpy/_core/numeric.py, numpy/matlib.py); no edges returned` rather than a silent zero.
- No output contains safe / clean / verified / OK (`tests/test_honesty.py`).
- `PHASE_DRIFT` fired on a file in a later phase and exited 1; reverting returned exit 0; a re-mapped symbol made the contract stale and `check` exited 2 until re-approved.

## 6. Reproduce the current guarded workflow

```bash
./scripts/install.sh
git clone --depth 50 https://github.com/numpy/numpy.git /tmp/numpy && cd /tmp/numpy
amu init
amu map --symbol numpy/_core/numeric.py#ones --change signature
amu plan --targets numpy/_core/numeric.py#ones:signature --approve
# make the intended planned change
amu refresh --symbol numpy/_core/numeric.py#ones --change signature
# Configure a working NumPy test/build environment first; skipped tests cannot pass.
amu check --phase 1
# repeat edit → refresh → check for later phases, then:
amu docs
amu check  # renew current evidence after any accepted edits (refresh code targets as needed)
amu done
```

`amu refresh` is required after graph-relevant edits and before `check`/`done` can treat the current graph as reviewed. If a live response is partial, ambiguous, mismatched, or unstable, it refuses the delta instead of treating missing relationships as absent. For a focused real-provider cross-file audit without importing or executing NumPy, run `pytest -q -rxX tests/test_external_numpy_graph.py` with `AMU_RUN_EXTERNAL_GRAPH=1`, `AMU_NUMPY_REPO`, and `AMU_EXTERNAL_ENTIRE_BIN` set as in the README.

## 7. Historical focused relationship audit (15 September 2026)

The live audit now checks three deliberately different shapes without importing NumPy:

| Shape | Target | Source-backed expectation | Result |
|---|---|---|---|
| same-file | `numpy/lib/_shape_base_impl.py#_make_along_axis_idx` | called by `take_along_axis` and `put_along_axis` in the same file | pass |
| cross-file | `numpy/lib/_scimath_impl.py#_fix_real_lt_zero` | calls `numpy/lib/_type_check_impl.py#isreal` | pass |
| isolated | `numpy/_core/_internal.py#npy_ctypes_check` | no direct callers or callees | pass; provider reports an exact degenerate focus |

The raw provider also returned call edges found only in NumPy docstring examples. That known provider defect remains an explicit expected failure in the external audit; AMU's AST corroboration removes those edges while preserving the real `_make_along_axis_idx` calls. A second live suite uses isolated temporary repositories to verify added, removed, renamed, and cross-file relationships after an edit; all four cases pass.

NumPy's Sphinx/RST docs were also scanned as a derived index: 587 files, 4,740 sections, 817 resolved internal links, 12 exact code-symbol mentions, and no parser warnings. The checkout's empty maintainer doc map correctly produced zero `DOCUMENTS` edges rather than invented mappings. This index is deliberately secondary to the semantic/API diff and never decides by itself whether a user-facing documentation change is required.

The current external suite additionally clones the local NumPy revision into an isolated temporary checkout, introduces an experimental keyword argument to `numpy/lib/_scimath_impl.py#sqrt`, and maps it to the existing RST `Functions` section in `doc/source/reference/routines.emath.rst`. Real graph diff plus worktree analysis detects the change. The test runs docs listing → prepare → edit → apply with `--review` and verifies that the saved RST and docs graph both change. The source checkout is untouched. This is an API/documentation workflow test, not a compiled NumPy numerical test.

Latest results: 415 ordinary tests passed with 10 opt-in tests skipped; five live NumPy checks passed (the relationship audit and review/save test), with one explicit expected raw-provider failure. Four live graph refresh scenarios also passed. The ordinary suite covers the CLI lifecycle, phase checks, refresh gates, docs, memory, navigation, workspace, model adapters, MCP, and web-card logic; adapters and graph responses are simulated there. It does not certify every feature against a running NumPy build or a live model service.

## 8. Pinned source audit (8–9 October 2026)

Fresh source checkout: NumPy **v2.3.0**, commit
`0532af47d6a815298b7841de00bdbc547104b237`. The original project and any existing
NumPy checkout were untouched. All edits for the RST save test occurred in a
second temporary clone; the source audit checkout remained clean.

[The versioned evidence record](evidence/numpy-v2.3.0-relations-2026-10-08.json)
contains source/tool hashes, four independently inspected positive labels,
actual raw observations, total-versus-captured section counts, source-checker
results and limitations. This is a small selected sample, not a NumPy-wide
accuracy estimate. Unknown/unlabelled returned edges are disclosed separately.

The same-file checks cover `take_along_axis`/`put_along_axis` calling
`_make_along_axis_idx`. The cross-file check covers `_fix_real_lt_zero` calling
`isreal`. A fourth label follows `_nx.issubdtype` through `numeric.py`'s wildcard
re-export of `numerictypes.py`. The raw provider returned all four selected
positive relationships and no checked docstring-only calls. Unlike the older
run, the checked docstring case did not produce an expected failure.

The independent AMU checker initially rejected two real edges: an absolute
import was resolved relative to the caller's directory, and a re-export was
mistaken for a wrong-module contradiction. Both defects were reproduced with
regression tests. Absolute imports now resolve from the repository namespace;
re-export candidates remain visible but uncertain unless their endpoint identity
can be independently established. Source-backed validation and retained
uncertain candidates must not be combined into one “verified” accuracy score.
The final guard retained all four selected positive edges, independently
verified three, and explicitly abstained on the wildcard re-export identity.
The evidence therefore records 3/4 verified-label recall and leaves overall
precision undefined because other returned edges have no independent labels.

`npy_ctypes_check` has no direct Python AST callers/callees in the checked scope,
but `numpy/_core/src/common/npy_ctypes.h` dynamically imports and calls it through
the Python C API. Its empty static Python graph is therefore **not** evidence of
runtime isolation. Native boundaries and the provider's reported parse failures
remain important limits: full-profile queries reported 349 partial failures
repo-wide; the fast cross-file query reported 72. No selected focus file was
listed as a parse failure, and captured sections matched their reported totals;
neither observation proves whole-repository completeness.

The reviewed docs test adds a keyword parameter to `sqrt`, finds its existing
RST mapping, prepares and edits a draft, saves it with explicit review, and
checks that both prose and the derived documentation graph change. It is not a
compiled NumPy numerical test. The separate live refresh suite validates
addition, removal and rename deltas in disposable cross-file repositories.

Final frozen-code results (9 October): **1,106 deterministic tests passed,
11 external tests skipped in that default run**. Separately, all **seven strict
NumPy tests** and **four live graph-refresh tests** passed with no expected
failures. The rebuilt wheel also passed installed-package/CLI/REPL checks.
No live model calls, compiled NumPy numerical run or browser accessibility
certification are implied by these results.
