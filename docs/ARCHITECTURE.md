# AMU architecture

## Current application boundary (9 October 2026)

`amu/services/documentation.py` owns the documentation application workflow:
listing candidates, preparing/reviewing selected drafts, applying reviewed or
eligible auto updates, queue preview/discard, save history/undo and docs graph
refresh. Its immutable `DocsRequest` is validated before workflow state access.
The service requires an explicit existing absolute repository and returns data
or a typed `DocumentationError`; it imports no CLI, Typer, Rich or rendering code.

CLI callbacks select/normalize the repository, construct a request, translate
expected errors and render the result. The interactive session still uses that
CLI parser. MCP keeps its explicit repository/consent checks and bounded CLI
subprocess adapter, which reaches the same service. It has not been converted
to unbounded in-process execution. Direct Python callers can use the service
without a terminal framework or implicit workspace selection.

Low-level modules retain their responsibilities: `docs.py` supplies API review
evidence and issued-candidate freshness; `docreview.py` manages draft identity,
conflicts and archive operations; `docsave.py` journals recoverable writes;
`docgraph.py` supplies the derived documentation index. The core does not bypass
their safeguards or grant broader write authority. CLI `docreview` remains a
compatibility re-export, not a second workflow implementation.

`amu/services/mapping.py` now owns stable live map collection, source-evidence
filtering, exact pagination/baselines, map identity and persistence. Its frozen
`MapRequest` and explicit repository validation do not import terminal code;
progress notifications are optional. The CLI retains a thin `_do_map` compatibility
adapter. Actual interactive `/map` now shares the CLI parser, including exact
symbols, quoted filenames, options and nonfatal parser errors.

`amu/services/graph_sync.py` owns index-readiness recording and guarded
HEAD-index/worktree-snapshot sync. `init`, `sync`, post-edit refresh and completion
reach it through thin adapters. It preserves the versioned code/docs cache gates,
failed-attempt metadata and separate prior-snapshot identity. There is no new
public MCP sync tool or interactive `/sync` command; existing workflows use the
service when they need snapshot sync.

This is an incremental extraction, not a finished application rewrite. Post-edit
refresh, planning/checking/completion and parts of navigation still have
orchestration in `cli.py`. Completion's read-only docs scan is not replaced with
the mutating/list-issuing documentation operation. Multi-file atomic workflow
transactions, direct MCP service dispatch and common transport serialization
remain future work. See [the service guide](APPLICATION-SERVICES.md) and
[the audit ledger](IMPROVEMENT-STATUS.md).

## Historical Phase 0 design (6 September 2026)

The tables below record the initial build plan, not the current implementation.

## What each module does today
| Module | Today | After BUILD_PLAN |
|---|---|---|
| `amu/graph.py` | subprocess wrappers: `impact()` (text), `capabilities()` (json). Never raise; return error strings / fallback dicts. | kept as a shim over `amu/entire.py`, which owns every `entire …` call (impact/neighbors/diff/search/def/symbols/snapshot/verify/doctor/checkpoint). |
| `amu/classify.py` | `classify_consumers(symbol, raw_impact_text, caps)` → `{will_break[], might_break[], unknown{unresolved_callsites, dynamic_dispatch_sites, out_of_coverage, notes}}`. Line-based heuristic over impact text. | kept verbatim (noon-Curveball proof); new `classify_nodes()` adds the confidence axis (sound/guessed/unknown) + reason/verify per node from impact JSON. |
| `amu/plan.py` | `build_plan()` graphlib topo sort → 4 phases (additive/migration/interior/removal); cycles → warnings. `plan_from_buckets()` bridges the 3 buckets. | kept; `plan_from_map()` builds phases from map nodes and `--approve` writes `.amu/contract.json`. |
| `amu/contract.py` | `check_diff(changes, frozen, scope, removals)` → FROZEN_SIGNATURE / OUT_OF_SCOPE / UNDECLARED_REMOVAL; `run_contract_check()` retains a legacy degraded mode when `entire` is absent. | Adds PHASE_DRIFT and UNKNOWN_TOUCHED as blocking human-review gates, UNGUARDED_CHANGE as a warning, and a real-shape parser for `entire graph diff --json` (`files[].changes[]{type,kind,name,…}`). |
| `amu/cli.py` | Typer: `brief`, `plan`, `check --base-ref --head-ref`. | Typer: `init map plan check verify sync sweep done docs skills doctor memory`; `brief` stays as hidden alias. Also exposed as `entire-amu` so `entire amu …` dispatches. |

## What the graph told us (docs/evidence/)
- `01-search-classifier.json`: `entire graph search` ranks `amu/classify.py#classify_consumers` first, with signals `graph:callers`, `complete-symbol`, and a narrow verify command `python -m pytest tests/test_classify.py -k …`.
- `03-impact-classifier.txt`: 30 direct callers (cli.brief, cli.plan, 28 tests), 2 data flows, 0 co-change, 0 siblings.
- Real `entire graph impact --format json` shape: `focus, callers{total,direct,entries[{endpoint{name,file_path,start_line},relation,direction,depth,call_site}]}, callees, type_consumers, data_flows, co_changes, siblings, warnings, partial_failures, completeness`.
- Real `entire graph diff --json` shape: `{base, head, files[{path,status,language,changes[{type: added|removed|body_changed|signature_changed|renamed, kind, name, new_signature?, dependents_count}]}]}`. No worktree mode — amu commits nothing, so `check` diffs `contract.base_sha..HEAD` and overlays `git status` for uncommitted files.

## What stays invariant (tested)
- unknown bucket never drops partial analysis; `check` degrades when `entire` is absent; cycles become warnings.
- No output summarises with safe / clean / verified / OK.

## Phase 9 additions (navigation, live rendering, gold)
| Module | Role |
|---|---|
| `amu/workspace.py` | `~/.amu/workspace.json` (override `AMU_HOME`): add local/GitHub repos (clone into `~/.amu/repos/<owner>__<repo>`, reuse with fetch), use/remove/sync, `resolve()` = `--repo` → repo containing `$PWD` → active → error with the add hint. Cloning is the only network call. |
| `amu/graphview.py` | `payload_from_impact()` → `{focus, nodes, edges, counts, status, candidates}`; `to_ascii / to_mermaid / to_dot`, deterministic (sorted), ids hashed for uniqueness. Ambiguous names list candidates with the exact `amu graph path#name` to run. |
| `amu/watch.py` | pure `resolve_change(path, contract, map, phase)` → node states + warning lines; `run()` via watchfiles (300 ms debounce, stop_event) with stat-polling fallback. Never runs tests. |
| `amu/render.py` | `LiveTree` (Rich Live, 4 fps, state column), `render_find`, `render_repo_table`, `render_dir_tree`; still computes nothing. |
| `amu/brand.py` | gold palette tokens + Rich theme; confidence colours are semantic and never gold; `progress()` spinner names the relation and is silent off-TTY. |

## Phase 10 additions (bring your own model)
| Module | Role |
|---|---|
| `amu/keys.py` | credential record `{provider, family, api_key, base_url, model, region, project_id, resource}` in `~/.amu/credentials.json` (0600). Order: `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / OpenAI-compatible env → file → `ant auth login` profile. `GOOGLE_API_KEY` is accepted only with `AMU_PROVIDER=gemini`. `masked()`/`source()` are the only things callers may print. |
| `amu/harness/providers.py` | `make_client(cred)`: Anthropic family via the official SDK (`Anthropic`, `AnthropicBedrockMantle`, `AnthropicVertex`, `AnthropicFoundry`); OpenAI-compatible adapter and native Gemini Developer API adapter (both stdlib `urllib`) duck-type `client.messages.create` with tool/message/stop-reason mapping. Gemini follows `models/<model>:generateContent` with `x-goog-api-key`. Anthropic-only params (`output_config.effort`, `cache_control`) are sent only to the Anthropic family. |
| `amu/harness/model.py` | `available()`, `credential()`, `model_id()` (credential → config → `claude-opus-5`), `ask_json()` for the planner; manual mode on missing credential, auth error, refusal, or provider error. |
| `amu/harness/chat.py` | `Assistant`: tool-using loop (streamed on Anthropic, single print elsewhere), first turn carries repo memory + map/contract context, system prompt cached. Tools are read-only wrappers over existing functions; role `assistant` in `roles.py` has no write tool. |
| `amu/cli.py` | `amu key set|status|remove`, `amu ask`, REPL: change requests → deterministic router → map; other free text → `Assistant` when a credential exists, else the router verdict. |
