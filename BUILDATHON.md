# amu — Anti-Messup · BUILDATHON (TWC Thugs)

Track: Entire main challenge · Track 2 Graph Intelligence
Fork/repo: https://github.com/fayasfarmisofficial-cyber/TWC-Thugs
Final commit SHA: the latest commit on `main` (`git rev-parse HEAD`); submission commits are `chore(p8): buildathon submission` and `feat(p7): static web card + install page`.
Project URL (Vercel): not deployed by the deadline — `web/` is a zero-config static site (import the repo in Vercel, root dir `web/`, output `public/`): `/` install page, `/card` agent-card viewer that renders `amu map --json`, `/install.sh`. The CLI itself runs locally next to the repo; the MCP server `amu/mcp_server.py` wraps it for Claude Code / Cursor.
Demo: docs/DEMO.md · text captures of every command in docs/screenshots/ (asciinema not available on the build machine) · raw graph outputs in docs/evidence/

## Environment
Entire CLI 0.10.5 · entire-graph plugin v0.4.0 · Python 3.13.9 · macOS arm64 · installed via `brew tap entireio/tap && brew trust entireio/tap && brew install --cask entire && entire plugin install graph`.

## Problem and innovation
Agents break what they can't see. amu builds the blast map from `entire graph impact`, turns it into an ordered contract
(additive → leaves → interior → removal), gates every phase with `entire graph diff` + the working tree, and closes the loop
with a sweep + doc candidates. Honesty model: **sound** only with a resolved graph edge, **guessed** for co-change/siblings,
**unknown** for unresolved/partial parse; every non-sound node carries `reason` and `verify`; no output ever summarises with
safe/clean/verified/OK (tested in `tests/test_honesty.py`); a `0 unknown` line always carries its qualifier.

Memory: amu owns one managed block in `CLAUDE.md`/`AGENTS.md` and rewrites it after every `amu done` from hot spots,
approved contracts, answered unknowns, features, conventions, open questions — each entry cites its evidence and the Entire
checkpoint id (`entire checkpoint list --json`), and goes **stale** automatically when the cited file's hash moves. Human text
outside the markers is byte-identical after a refresh (tested).

## Setup / Run (clean checkout)
    ./scripts/install.sh
    amu init
    amu map --file amu/classify.py
    amu plan --targets amu/classify.py#classify_consumers:signature --approve
    amu check --phase 1
    amu done
Run tests: `pytest -q` (the deterministic suite hides the real `entire`, isolates `~/.amu` via `AMU_HOME`, and replays fixtures via `tests/fake_entire.py`).

## Entire Checkpoints
| Checkpoint | Commit | Checkpoint ID | Link | Proves |
|---|---|---|---|---|
| init-understanding | 34f6324 | none listed by `entire checkpoint list` (see note) | — | graph-first read before edits: docs/evidence/00–03 |
| pre-curveball-stable | cc1c73b | none listed (see note) | — | loop closes end-to-end, 118 tests green |
| curveball-response | — | — | — | not reached before the 15:00 deadline |
| final-verification | 093c3df | **01M1TZXJP22S864ME7QTF1MNRN** (session d4f30598…) | `refs/entire/checkpoints/RN/01M1TZXJP22S864ME7QTF1MNRN` on origin | fix found by dogfooding on numpy, committed under the live hooks; `entire graph checkpoint` analysis in docs/evidence/checkpoint-numpy-fix.txt; clean-checkout rehearsal passed earlier (docs/evidence/20-final-semantic-diff.txt) |

**Note (honest):** `entire enable --agent claude-code` was run at 14:12 IST *inside* an already-running Claude Code session.
`entire doctor` reported hooks OK, but `entire checkpoint list --json` returned `[]` for the first three protocol runs; the first checkpoint
(`01M1TZXJP22S864ME7QTF1MNRN`) only appeared on the commit made at 14:43 after the session had cycled through several turns
(docs/evidence/checkpoints.jsonl, docs/evidence/checkpoint-diagnostic.txt). We did not fabricate IDs. The named checkpoint
commits exist on `main` (`git log --grep checkpoint:`); if the session hooks flush later, `entire graph checkpoint <ID>` can
be run against them.

## Entire Graph evidence
- Definition/search lookup: docs/evidence/01-search-classifier.json (search ranks `classify_consumers` #1 with `graph:callers` signal), 02-def-classify.txt
- Impact analysis: docs/evidence/03-impact-classifier.txt (30 direct callers, 2 data flows), 11-amu-map-classify.json (amu's map: 35 sound · 9 guessed · 0 unknown + qualifier)
- A real red: docs/evidence/12-delta-brief.json — `amu check --phase 1` blocked on OUT_OF_SCOPE files with a delta brief
- Semantic diff: docs/evidence/20-final-semantic-diff.txt (`entire graph diff --base 34f6324 --head HEAD`)
- Commands used: search, def, impact, neighbors, diff, verify, symbols, snapshot, index, checkpoint, doctor, capabilities, init-agents

## Response to the Curveball
The noon Curveball (partial analysis / dynamic dispatch) is covered by the v0 modules kept verbatim: unknown bucket never
drops partial analysis, `check` degrades when `entire` is absent, cycles become warnings (tests/test_classify.py,
test_plan.py, test_contract.py, test_amu.py untouched, still green). The afternoon Curveball response protocol (BUILD_PLAN.md
Phase 6) was not executed — time ran out.

## Tested on numpy (dogfood on a real repo, 6 Sep 14:35–14:45 IST)
Shallow clone of numpy/numpy (`65b30cf`, 495 .py files + C/Fortran). `amu init`: 1666 files · 27,709 symbols · 97,968 relations ·
**323 unparsed files reported up front** (Fortran, YAML, C headers). Mapping `numpy/_core/numeric.py#ones` found three real bugs, all fixed in `093c3df`:
1. `build_map` crashed on `"entries": null` in empty impact sections.
2. `ones` is defined twice (numeric.py and matlib.py) → impact returned `disambiguation_required` and zero edges; amu now passes `--file` and
   marks a still-ambiguous symbol as an unknown node instead of silently reporting 0 consumers.
3. Repo-wide parse failures were emitted as 323 unknown nodes; they now fold into the qualifier (`323 files unparsed repo-wide`) and only
   syntax-error files in the root's own package *and* language become nodes.
Also: querying the cached committed tree (`--head`) took the per-symbol impact from ~80 s to ~1 s.
Result: `ones` → 6 sound consumers across 4 files, 0 guessed, 0 unknown with the qualifier; `plan --approve` → proceed; `check --phase 1` → 0 blocking;
editing a phase-3 file (`numpy/lib/_polynomial_impl.py`) → PHASE_DRIFT + delta brief, exit 1; `amu done` → 26 memory entries written to numpy's CLAUDE.md.

## After the deadline: Phases 9–10 (same honesty rules, no `--json` shape changed)
- **Workspace + navigation** (`amu/workspace.py`): `amu repo add <path|owner/repo|url> | list | use | remove | sync`, `amu cd`; `find` (ranked hits with `signals[]`), `open`, `tree`, `neighbors`, `where`, `back`/`recent`. Repo resolution: `--repo` → repo containing `$PWD` → active → exit 2 with the add hint.
- **Live graph rendering**: `amu graph <path#sym>` ASCII / `--format json|dot|mermaid` (deterministic; an ambiguous name lists its candidates with the exact command instead of guessing), `amu watch` (node states `· ▸ ✓ ✗ ↯` from `.amu/state.json`, never runs tests), Rich `Live` tree in `check`, spinner that names the relation and count, `web/public/graph.html` d3-force viewer.
- **Gold identity** (`amu/brand.py`): five tokens, confidence colours semantic and never gold, `NO_COLOR` reads identically, nothing branded reaches `--json`.
- **Bring your own model** (`amu/keys.py`, `amu/harness/providers.py`, `amu/harness/chat.py`): `amu key set [--provider anthropic|bedrock|vertex|foundry|openai-compatible|ollama|lmstudio|openrouter]` (0600 file, env, or `ant auth login`; value never printed); the REPL banner shows `● model on` / `○ model off` with a guided in-session `/key add`; `amu ask` and REPL free text run a tool-using loop over **read-only** graph tools (`graph_search`, `graph_def`, `amu_map`, `amu_graph`, `amu_where`, `amu_state`, `read_lines` labelled heuristic, `answer_unknown`). The assistant role has no write tool; it can widen a map or ask, never enlarge a contract or promote confidence. Default model `claude-opus-5`; OpenAI-compatible endpoints get the same loop through a `/v1/chat/completions` adapter.
- **Numpy dogfood** (docs/TESTCASE-NUMPY.md): three real bugs found and fixed on numpy/numpy (null `entries`, silent 0 consumers on an ambiguous symbol, 323 parse failures as nodes); per-symbol impact 80 s → 1 s with `--head`.

## How amu builds on the Entire CLI (not beside it)
`entire-amu` console script ⇒ `entire amu map …` dispatches through Entire's plugin mechanism (verified: `entire amu doctor`).
Every graph fact is an `entire graph …` call routed through `amu/entire.py`; no parsing/indexing is re-implemented.
`amu check` uses `entire graph verify` for the test verdict when available. Memory lines cite `entire checkpoint list` ids.

## Tests
- test_classify / test_plan / test_contract / test_amu — v0 noon-Curveball behaviour (untouched)
- test_map — sound/guessed/unknown assignment, reason+verify on every non-sound node, qualifier on 0 unknown
- test_honesty — banned verdict words across map/plan/contract/agent-card/rendered output; schema validation
- test_contract_phase — PHASE_DRIFT, FROZEN_SIGNATURE, OUT_OF_SCOPE, and UNKNOWN_TOUCHED block; real diff shape parses
- test_done — sweep finding outside contract with verify; depth line never "nothing found"; done exits 0 with findings
- test_docs — SUGGESTED draft for mapped signature change; body ⇒ unknown; unmapped ⇒ flag; auto never applies unknown
- test_router — never invents a symbol; silent-on-params ⇒ signature; multi_target; ambiguous
- test_roles — tool not granted raises; write guard; checker cannot downgrade a block; 3rd retry escalates
- test_cli_smoke / test_brand — exit 3 + install hint without entire; key never printed; brand on stderr only, absent from --json
- test_memory — markers idempotent; human text byte-identical; stale flips on hash change; caps + overflow; secret rejected
- test_workspace / test_nav / test_graph / test_watch — repo add rejects non-git (exit 2), never deletes outside `~/.amu/repos`, resolution order; find/open/back/recent; Mermaid/dot shape + determinism + ambiguous focus; watch state transitions incl. a live watchfiles run
- test_key / test_providers / test_chat — 0600 store, env-over-file, value never printed; provider validation, OpenAI mapping both ways, full loop through the adapter, provider errors reported not raised; scripted-client tool loop, ungranted tool → `is_error`, no write tool, path jail, secret rejection, refusal handling

## Databricks
Not applicable.

## What's next
Deploy web/ to Vercel + `/api/report` for `amu done --publish`, live-endpoint verification of `amu ask` (no credential on the build machine), `verify --attempt-fallback` with SCIP, `docs --mode auto` in CI,
mapper ripple review, live checkpoint ids once the session hooks flush.

(No secrets. Model key is read from ANTHROPIC_API_KEY at runtime; roles run in manual mode without it.)
