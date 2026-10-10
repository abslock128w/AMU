# amu — Claude Code BUILD PLAN

> Paste this file into the repo root as `BUILD_PLAN.md`, open Claude Code in the repo, and say:
> **"Read BUILD_PLAN.md. Execute Phase 0, then stop and show me `git log --oneline -5` and `entire status`."**
> Then advance one phase at a time: **"Execute Phase N."** Every phase ends in a green test run, a commit, and a push.

Deadline: **3:00 PM IST, Sun 6 Sep 2026**. Repo: `https://github.com/fayasfarmisofficial-cyber/TWC-Thugs`. Track 2 · Graph Intelligence.

---

## 0. Ground truth (read before writing a single line)

### What already exists in the repo (do not throw away)
```
amu/
├── graph.py      # subprocess wrappers → entire graph impact / diff / capabilities
├── classify.py   # 3-bucket classifier (will_break / might_break / unknown)
├── plan.py       # graphlib.TopologicalSorter → 4-phase plan
├── contract.py   # entity-level diff parser + violation checker
└── cli.py        # Typer entrypoint: amu brief / amu plan / amu check --base --head
tests/  test_classify.py  test_plan.py  test_contract.py
BUILDATHON.md  README.md  pyproject.toml  docs/
```
The v0 already covers the noon Curveball (unknown bucket never drops partial analysis; `check` degrades when `entire` is absent; cycles become warnings). **Every one of those behaviours must survive every phase below** — they are tested, and the tests stay green.

### What Entire gives us (verified against docs.entire.io, 6 Sep 2026)
| Need in PRD | Real `entire` command | Notes |
|---|---|---|
| S1 index | `entire graph index --repo .` · `entire graph snapshot --repo . --format compact-ndjson` | snapshot → `.amu/snapshot.ndjson`; `snapshot-query` reads it without rebuilding |
| find a symbol (router rule: never invent) | `entire graph search --repo . --profile full --query "…" --format json` · `entire graph def SYM --repo .` | search results carry `signals[]`; def confirms the declaration |
| S2 map | `entire graph impact --repo . --symbol SYM --depth 2 --limit 50` · `entire graph neighbors --symbol SYM --relation CALLS --direction in` | impact lists callers/callees/type consumers/data flows/co-change files |
| S3/S4 check & sweep | `entire graph diff --base <sha> --head HEAD` (add/remove/rename/signature/body, with dependent counts) | this *is* the "semantic diff" the checklist wants |
| tests as verify | `entire graph verify --repo . --test "<cmd>" --record-baseline / --pre-edit-baseline` | gives "which tests changed state", not just pass/fail |
| checkpoint evidence | `entire graph checkpoint <ID>` · `entire checkpoint list` | analyze the commit behind an Entire checkpoint |
| health | `entire graph doctor --json` · `entire graph capabilities --json` | `amu doctor` wraps these |
| plugin dispatch | any executable `entire-<name>` on `$PATH` runs as `entire <name>` | so we ship **`entire-amu`** and `entire amu map` just works |

Install: `entire plugin install graph` (needs Entire CLI ≥ 0.10, Git ≥ 2.36). Fallback if the managed install fails: `go install github.com/entireio/entire-graph/cmd/entire-graph@latest` then `entire plugin install "$(go env GOPATH)/bin/entire-graph" --force`.

### Non-negotiables (from PRD §10 — enforced by tests, not prose)
1. Nothing is `sound` without a resolved graph edge from `impact`/`neighbors`/`edges`. Name match or co-change ⇒ `guessed`. Unresolved/dynamic ⇒ `unknown`.
2. Every `guessed`/`unknown` node carries `reason` **and** `verify` (a test, `amu verify --node`, or a human question).
3. No output ever summarises with the words **safe / clean / verified / OK**. Counts and buckets only. A `0 unknown` line always carries the qualifier that makes it true.
4. `amu` never writes source code. Docs are written only by a human confirming a `SUGGESTED` draft, or by `docs --mode auto` in one commit titled `docs: amu auto-update`.
5. Anything an agent reads in source is `heuristic` or `requires_verification`, never `sound`; it can widen a map or ask, never enlarge a contract.
6. Symbols come **verbatim** from `entire graph search` / `def`. The router returns `not_found | ambiguous | multi_target` rather than guessing.
7. We build *on* Entire. No re-implementation of parsing, indexing, or checkpointing. If a feature needs graph data, it calls `entire graph …`.

---

## 1. Target layout (end state)

```
TWC-Thugs/
├── amu/
│   ├── cli.py            # Typer app: init map plan check verify done sync sweep docs skills doctor shadow(stub) ; `amu` = interactive
│   ├── entire.py         # ALL subprocess calls to `entire …` live here (was graph.py; keep graph.py as a shim)
│   ├── classify.py       # keep; extend with confidence axis + feature axis + verify lines
│   ├── plan.py           # keep; phases additive→leaves→interior→removal; writes contract.json on --approve
│   ├── contract.py       # keep; violations FROZEN_SIGNATURE OUT_OF_SCOPE UNDECLARED_REMOVAL PHASE_DRIFT UNKNOWN_TOUCHED (block) UNGUARDED_CHANGE (warn)
│   ├── state.py          # .amu/ read/write, schemas, hashes, checkpoint.json, stale
│   ├── render.py         # Rich tree (§9 terminal view) + agent-card JSON; NEVER computes anything
│   ├── brand.py          # "TWC Thugs" banner, header line, prompt, colours — stderr only, never in --json
│   ├── memory.py         # self-renovating CLAUDE.md / AGENTS.md managed block; .amu/memory.json; stale detection
│   ├── router.py         # intent router (§5/§8.8) — deterministic first, model second
│   ├── docs.py           # Feature 2: doc-map, candidates from `entire graph diff`, flag/draft/auto
│   ├── harness/
│   │   ├── orchestrator.py   # S1→S4 loop, budgets, handoff JSON in .amu/run/
│   │   ├── roles.py          # builder, checker, sweeper, doc_agent, mapper, planner — fixed tool sets
│   │   ├── prompts.py        # §8 system prompts, verbatim from PRD
│   │   └── model.py          # Anthropic client; reads ANTHROPIC_API_KEY; no key ⇒ roles run in "manual" mode
│   ├── skills/               # 7 built-in SKILL.md folders (§7)
│   └── mcp_server.py         # exposes map/plan/check/done as MCP tools for Claude Code / Cursor
├── .amu/                 # per-repo state; git-ignored except config.json, doc-map.json, feature_map.json, skills/
├── tests/                # existing 3 + new (see Phase 5)
├── web/                  # Next.js app for Vercel: agent card viewer, install page, /api/report
├── scripts/install.sh    # curl-able installer: entire CLI → graph plugin → pipx install → entire-amu on PATH
├── BUILDATHON.md  README.md  CLAUDE.md  AGENTS.md  pyproject.toml  .gitignore
```

`pyproject.toml` `[project.scripts]`: `amu = "amu.cli:app"` **and** `entire-amu = "amu.cli:app"`. That single line makes `entire amu …` work.

---

## 2. Phases

Each phase: **(a)** do the work, **(b)** `pytest -q` green, **(c)** `ruff check .` clean, **(d)** commit with the given message, **(e)** `git push origin main`, **(f)** if the phase names a checkpoint, run the checkpoint protocol in §3 immediately after the push.

### Phase 0 — Bootstrap, understand, checkpoint `init-understanding`  (≈25 min)

1. `git clone https://github.com/fayasfarmisofficial-cyber/TWC-Thugs && cd TWC-Thugs` (or `git pull` if present). `rm -f .DS_Store` and add `.DS_Store` to `.gitignore`.
2. Install toolchain, in this order, and record versions in `BUILDATHON.md → Environment`:
   ```bash
   # Entire CLI (macOS/Linux)
   brew tap entireio/tap && brew install --cask entire   # or the documented install for the host OS
   entire version
   entire plugin install graph && entire graph version   # fallback: go install path in §0
   python -m pip install -e ".[dev]"                     # add [dev] = pytest, ruff, anthropic, rich, typer, mcp
   ```
3. Enable Entire **in this repo** so Claude Code sessions produce checkpoints:
   ```bash
   entire enable --agent claude-code
   entire agent add claude-code
   entire status            # must say enabled + claude-code hooked
   entire graph init-agents --repo .   # writes .entire/graph-agent.md + managed blocks in AGENTS.md / CLAUDE.md
   ```
4. Understand the code with the graph, and **save the raw output** — this is the "definition/search lookup" evidence:
   ```bash
   mkdir -p docs/evidence
   entire graph search --repo . --profile full --query "where is the will_break might_break unknown classifier" --format json > docs/evidence/01-search-classifier.json
   entire graph def classify --repo . > docs/evidence/02-def-classify.txt        # use the real symbol name from search
   entire graph impact --repo . --symbol <classifier fn> --depth 2 > docs/evidence/03-impact-classifier.txt
   entire graph capabilities --json > docs/evidence/00-capabilities.json
   ```
5. Read every file in `amu/` and `tests/`. Write `docs/ARCHITECTURE.md` (≤60 lines): what each module does today, what the PRD changes, what stays.
6. Append the CLAUDE.md block in §6 (project rules for the agent).
7. Commit: `chore(p0): bootstrap entire + graph, evidence, architecture notes`. Push. **Checkpoint → `init-understanding`** (§3).

**Done when:** `entire status` shows enabled; `entire graph version` prints; `pytest -q` green; 4 evidence files exist; checkpoint ID + link recorded in BUILDATHON.md.

### Phase 1 — PRD command surface on the existing core  (≈75 min)  ← the MVP loop

Keep `amu brief` as a hidden alias of `amu map --symbol`. Add/replace:

| Command | Implementation notes |
|---|---|
| `amu init` | verify `entire` + `entire graph` (else print the exact install line and exit 3); `entire graph index --repo .`; `entire graph snapshot --format compact-ndjson > .amu/snapshot.ndjson`; write `.amu/config.json` (language from `capabilities`, `test_cmd`/`build_cmd` detected from pyproject/package.json/go.mod, `docs_dir`, `docs.mode: draft`); draft `.amu/feature_map.json` from top-level dirs; draft `.amu/doc-map.json` from `docs/**` headings; draft repo skills `run-tests`, `build`, `conventions` into `.amu/skills/`. Print counts: files, symbols, relations, **and the unparsed list** (first honesty line). |
| `amu map --file F` / `--symbol path#name [--depth 1\|2] [--change C]` | For `--file`: list exports via `entire graph symbols` filtered by file (or `snapshot-query`); for each export run `impact`. Build nodes `n1…` with `{id,path,symbol,confidence,risk,feature,order,action,via,reason,verify}`. `confidence`: resolved edge ⇒ sound; co-change/name ⇒ guessed; unresolved call site / dynamic dispatch ⇒ unknown. `risk` per `--change` class (signature/remove/rename ⇒ consumers `will_break`; body ⇒ `might_break`; unknown stays unknown). `feature` from `feature_map.json` else path-derived (label `(derived)`). Emit `features / radius / resolution / next` lines; `next` is always a runnable command. `--json` prints the same object. |
| `amu plan [--targets sym:class,…] [--approve]` | reuse `plan.py`; 4 phases, each phase lists node ids + reason + verify. `--approve` writes `.amu/contract.json` `{targets, phases[], frozen_signatures[], allowed_files[], base_sha, created_at, map_hash}`. Exit 2 if map hash stale. |
| `amu check [--phase N]` | `entire graph diff --base <contract.base_sha> --head HEAD` (working tree) → violations via `contract.py`; scope check = changed files ⊄ phase files ⇒ `OUT_OF_SCOPE`; edits in a later phase ⇒ `PHASE_DRIFT`; `UNKNOWN_TOUCHED` if a changed file is an unknown node. Run `entire graph verify --test "<phase tests>"` when available; else `test_cmd`. Writes `.amu/state.json` per-node `planned/editing/green/red/drift`. Red ⇒ **delta brief** (node, its consumers, why). Exit 1 on blocking violation. Keep `--base/--head` for backwards compatibility. |
| `amu verify --node N [--attempt-fallback]` | prints the node's verify path; runs the named test; `--attempt-fallback` tries `entire graph neighbors --internal-only` at depth 2 and, if `scip-typescript` is present, a SCIP lookup. Never changes the contract; may downgrade confidence only via a new map. |
| `amu sync` / `amu sweep` / `amu done` | sync: re-index + re-snapshot + `sync-state.json`. sweep: diff since `contract.base_sha`, every changed public export **not** in the contract is a finding with bucket/confidence/reason/verify; group by feature. If none: print "swept N files at depth D, 0 findings outside the contract" — never "nothing found". done = sync → sweep → docs → **memory refresh** (Phase 3b) → report (declared vs actual, findings, docs, memory lines added/stale, next). done/sweep never exit 1. |
| `amu docs [--mode flag\|draft\|auto] [--apply id]` | Phase 4. |
| `amu doctor` | `entire graph doctor --json` + `capabilities` + `ANTHROPIC_API_KEY` present? (never print it) + unparsed list. |
| `amu` (no args) | interactive Rich REPL: free text → `router.py`; slash commands `/map /plan /approve /check /done /docs /verify n5 /why n4 /feature X /skills`. Router is deterministic keyword rules first; model only if `ANTHROPIC_API_KEY` set. |

**Branding — "TWC Thugs" must be visible the way Claude Code shows its own name.** Implement in `amu/brand.py`, used by `render.py` only:
- `amu` (interactive) opens with a Rich panel banner: ASCII wordmark `TWC THUGS` (generate with `pyfiglet`, font `slant`, then paste the literal text into `brand.py` so pyfiglet is not a runtime dependency), subtitle `amu · Anti-Messup · built on the Entire CLI`, version, repo name, and the Entire status (`enabled · graph vX.Y`). The REPL prompt is `twc-thugs ❯ `.
- Every non-JSON command prints a one-line header first: `TWC Thugs · amu map · <repo>` in the brand colour (bold magenta; plain text under `NO_COLOR`). The status line during the phase loop starts with `TWC Thugs ·` then phase, spend, open questions.
- `amu --version` prints `amu <ver> · TWC Thugs · entire <ver> · entire-graph <ver>`. `amu --help` epilog: `Built by TWC Thugs for the Bengaluru Tech Week Buildathon 2026`.
- `--json` output is **never** touched by branding (agents parse it). The banner goes to stderr so pipes stay clean.
- The agent card (`web/card`) and MCP server description also carry the `TWC Thugs` name; the Vercel page `<title>` is `TWC Thugs · amu`.
- Test: `test_brand.py` — banner appears on interactive launch and on `amu map` stdout/stderr, is absent from `--json`, and reads correctly with `NO_COLOR=1`.

Cross-cutting: global `--json`; exit codes `0/1/2/3` exactly as PRD §4; every command reads `.amu/config.json`; `render.py` does zero computation; `NO_COLOR` respected; under 100 cols drop `feature` and `via`, never the glyph or `verify`.

Commit: `feat(p1): amu init/map/plan --approve/check --phase/verify/sync/sweep/done + rich tree`. Push.

**Done when:** on this very repo, `amu init && amu map --file amu/classify.py && amu plan --approve && amu check --phase 1 && amu done` runs end-to-end and the terminal output matches PRD screenshot 1 in shape.

### Phase 2 — Ship as an Entire plugin + one-line install  (≈15 min)

1. `pyproject.toml` scripts: `amu` and `entire-amu` → `amu.cli:app`. Confirm `entire amu map --file amu/plan.py` works (Entire dispatches to `entire-amu` on `$PATH`).
2. `scripts/install.sh`: idempotent; installs Entire CLI if missing (brew / documented curl path per OS), `entire plugin install graph`, `pipx install git+https://github.com/fayasfarmisofficial-cyber/TWC-Thugs`, then `entire amu doctor`. Print next steps.
3. `README.md` "Setup/Run from a clean checkout" — exactly the commands a judge runs, nothing else.
4. Commit: `feat(p2): entire-amu plugin entrypoint + install.sh`. Push.

### Phase 3 — Skills + harness roles  (≈60 min)

1. Create `amu/skills/<name>/SKILL.md` for the built-ins (§7): `map-a-change, phase-edit, red-triage, close-the-loop, doc-follow, unknowns, feature-rollup` (+ `memory-renovate`, step 7). Each: when to use, steps, exact commands, done-criteria. `amu skills list|add|run`.
2. `harness/prompts.py`: the PRD §8 prompts verbatim (shared preamble + 8 roles). Every prompt ends with the same 3 lines.
3. `harness/roles.py`: dataclass per role with `tools: frozenset` — a role literally cannot call a tool outside its set (raise `ToolNotGranted`). Tool matrix from §6.
4. `harness/orchestrator.py`: intent → S2 map → planner → `plan --approve` on `proceed` → per-phase builder → `check --phase` → checker on red (max 2 retries, max 2 widenings, 1 sweep) → final check → done (sweeper ∥ doc-agent) → report → `checkpoint.json`. Every handoff is one JSON object `{decision, confidence, escalate, evidence[], next}` written to `.amu/run/<role>-<n>.json`; nothing passed by prose.
5. **Builder is the user's agent.** In this repo the builder role produces a *phase brief* (contract, phase nodes, reasons, frozen signatures, repo skills) as JSON and as a markdown block for Claude Code / Cursor; with `ANTHROPIC_API_KEY` set it can also drive an Anthropic model that edits only the allowed files (write guard in `roles.py` refuses any other path).
6. `harness/model.py`: model `claude-sonnet-4-6` (or whatever key works — read from `config.json → model`), 1 JSON object out, retries on non-JSON once. No key ⇒ orchestrator prints the brief and waits for the human (`manual` mode) — the loop still closes.
7. Add an eighth built-in skill `memory-renovate` (used by orchestrator + sweeper): what goes into memory, what never does, how to write an entry, when to mark stale.
8. Commit: `feat(p3): built-in skills, role tool-sets, orchestrator with budgets + JSON handoffs`. Push.

### Phase 3b — Memory: the self-renovating CLAUDE.md  (≈40 min)

The idea: Claude Code reads `CLAUDE.md` every session. Today it's static. amu owns **one managed block** in `CLAUDE.md` and `AGENTS.md` and rewrites it from what the graph, the checks, the sweeps, and the humans taught it. The rest of the file stays human-owned and is never touched (same pattern as entire-graph's `init-agents` managed blocks).

**Managed block markers** (idempotent; created if absent, replaced in place if present, everything outside untouched):
```markdown
<!-- amu:memory start · do not edit inside · regenerate with `amu memory refresh` -->
## What amu has learned about this repo  (TWC Thugs · updated 2026-09-06 14:02 · checkpoint 01J…)
### Hot spots (history: went red in past checks)
- `amu/contract.py#check_violations` — red 3/5 · last: PHASE_DRIFT (chk 01J…) · verify: pytest tests/test_contract.py
### Frozen / risky interfaces (from approved contracts)
- `amu/classify.py#classify` — signature frozen in 2 of last 3 tasks · 6 sound consumers
### Unknowns a human already answered (do not re-ask)
- `router/registry.ts` string-keyed dispatch → "internal, not a public protocol" (human, 2026-09-06, task t3)
### Features → paths
- Core: amu/classify.py, amu/plan.py · Docs: amu/docs.py · Harness: amu/harness/**
### Conventions the graph confirmed
- tests live in tests/, one file per module · barrel exports: none · run tests: `pytest -q`
### Open questions (carry into the next task)
- n5 registry.ts dynamic dispatch site still unresolved → `amu verify --node n5 --attempt-fallback`
### Stale (decisions whose evidence changed — re-verify before trusting)
- "songs.ts is co-change only" — file hash changed since chk 01J…
<!-- amu:memory end -->
```

**Sources → `.amu/memory.json`** (git-ignored; the rendered block is what gets committed):
| Entry kind | Comes from | Carries |
|---|---|---|
| hot_spot | `state.json` red history across tasks | file#symbol, red/total, last violation, checkpoint id, verify |
| frozen_interface | every `contract.json` ever approved | symbol, frozen count, sound consumer count |
| answered_unknown | human answers in `.amu/run/*.json` and `amu verify` results | question, answer, who (human/fallback), date, task id — **never promoted to sound** |
| feature_path | `feature_map.json` + derived features from maps | feature → globs, `(derived)` label kept |
| convention | repo skills `conventions`, `run-tests`, `build` | one line each, source file cited |
| open_question | planner/sweeper `questions_for_human[]` not yet answered | question + the command that would resolve it |
| stale | any entry whose cited file hash or checkpoint no longer matches | original entry + why stale |

Every entry has `{kind, text, evidence:[file:line | node id | checkpoint id], sha, file_hash, created, last_seen, pinned}`. **Checkpoint ids come from `entire checkpoint list --json`**, so each memory line is traceable to an Entire checkpoint — say this explicitly in BUILDATHON.md under "Use of Entire Checkpoints".

**Commands**
| Command | Does |
|---|---|
| `amu memory refresh` | rebuild `memory.json` from sources → render block → write into `CLAUDE.md` + `AGENTS.md`. Runs automatically as step 5 of `amu done` (after docs, before the report). Caps: 40 lines total, ≤8 per section, newest/most-red first; overflow goes to `.amu/memory.md` (full) and the block says `+N more in .amu/memory.md`. |
| `amu memory show [--json]` | print current entries with evidence and staleness |
| `amu memory pin <id>` / `unpin` | pinned entries never age out of the block |
| `amu memory forget <id>` | remove (kept in `memory.json` history with `forgotten_at` — never silently) |
| `amu stale` | already in PRD; now also lists memory entries whose evidence hash moved |

**Rules (tested)**
- Memory is instructions, not code: writing `CLAUDE.md`/`AGENTS.md` is the only file write memory ever does, and only inside its markers. Nothing else on disk changes.
- Each refresh that changes the block is its own commit `chore: amu memory refresh` (like docs auto mode) so it is reviewable and revertible as a unit. `--no-commit` leaves it staged.
- Never store prompts, transcripts, secrets, or source text — only pointers, counts, hashes, one-line answers. Grep the rendered block for key patterns before writing (same regex as Phase 8 step 2).
- Honesty rules apply: no `safe/clean/verified/OK`; answered unknowns keep the label `(human)` or `(fallback)`; nothing in memory can raise a node's confidence in a later map — the map is recomputed from the graph, memory only pre-fills `verify` and `reason` and skips questions already answered.
- Harness rehydration: the orchestrator loads `memory.json` (few hundred tokens) at task start; the router uses `answered_unknown` to skip questions; the planner reads `hot_spot` as attention, never proof.
- `amu init` seeds the block with features, conventions, and the unparsed-files list so a fresh clone already has a non-empty memory.

**Tests** (`test_memory.py`): markers created once and replaced in place; human text outside markers is byte-identical after refresh; stale detection flips when a cited file's hash changes; caps enforced with overflow note; secret pattern in an answer is rejected; a refresh with no changes makes no commit; `--json` of `memory show` validates against schema.

Commit: `feat(p3b): self-renovating CLAUDE.md memory block, amu memory refresh/show/pin/forget`. Push.

### Phase 4 — Docs that follow the graph  (≈30 min)

1. `.amu/doc-map.json` schema: `{ "path#symbol": [{"file":"docs/x.md","section":"## Heading"}] }`.
2. `docs.py`: candidates = `entire graph diff` entries whose class ∈ `add_export|remove|rename|signature|move` on public exports; `body` ⇒ candidate marked `unknown`. Modes: `flag` (list), `draft` (default; old/new signature → SUGGESTED paragraph; `y/n/e` per file; never claims "up to date"), `auto` (apply sound+mapped only, single commit `docs: amu auto-update`, list what was not applied and why).
3. Doc-agent packet `{candidate, doc_file, section, old, new, consistent_with_diff, concerns[], other_mentions[], next}`; `other_mentions` via grep of docs dir.
4. Commit: `feat(p4): doc-map, doc candidates from entire graph diff, flag/draft/auto`. Push.

### Phase 5 — Tests that judges can run  (≈30 min)  → then checkpoint `pre-curveball-stable` if not already recorded

Keep the 3 existing test files untouched (they are the noon-Curveball proof). Add, using a **recorded-fixture fake of `entire`** (`tests/fake_entire.py` on `$PATH`, replaying JSON from `tests/fixtures/`) so tests never need the real binary:

- `test_map.py` — sound/guessed/unknown assignment; every non-sound node has reason+verify; unresolved call site ⇒ unknown; `0 unknown` line carries its qualifier.
- `test_honesty.py` — scan every rendered/`--json` output in the suite for `\b(safe|clean|verified|OK)\b` as a summary word ⇒ fail. Schema check on map/plan/state/contract JSON.
- `test_contract_phase.py` — `check --phase` raises `PHASE_DRIFT` when a later-phase file changes; `FROZEN_SIGNATURE` on signature change of a frozen export; `UNKNOWN_TOUCHED` requires human review.
- `test_done.py` — sweep reports a finding outside the contract with verify; empty sweep prints depth, never "nothing found"; `done` exits 0 even with findings.
- `test_docs.py` — signature change on a mapped export ⇒ draft labelled SUGGESTED; body change ⇒ unknown candidate; unmapped ⇒ flag only; auto mode never applies unknown.
- `test_router.py` — never invents a symbol; silent-on-params ⇒ `signature`; two targets ⇒ `multi_target`.
- `test_roles.py` — a role calling a tool it lacks raises; checker cannot downgrade a blocking violation; 3rd retry escalates.
- `test_cli_smoke.py` — `amu --help`, `entire-amu --help`, `amu doctor --json` when `entire` missing ⇒ exit 3 with the install hint.

Commit: `test(p5): critical path + curveball behaviour`. Push. **Checkpoint → `pre-curveball-stable`** (see §3; if the noon Curveball checkpoints already exist from the morning, link those and skip).

### Phase 6 — Curveball response protocol  (whenever it lands; ≈45 min budget)

1. **Before touching code:** `entire graph impact --repo . --symbol <affected symbol> --depth 2 > docs/evidence/10-impact-pre-curveball.txt` and `amu map --symbol <…> --change <class> --json > docs/evidence/11-amu-map-pre-curveball.json`. This is the required "impact analysis before the Curveball change".
2. `amu plan --targets <sym>:<class> --approve` → contract. Work **one phase at a time with `amu check --phase N`** between phases. Dogfood: the tool must gate its own change. Save the red delta brief if one happens (`docs/evidence/12-delta-brief.json`) — a real red is *better* demo evidence than a green run.
3. `amu done` → report saved to `docs/evidence/13-done-report.md`. `entire graph diff --base <pre-curveball sha> --head HEAD > docs/evidence/14-semantic-diff.txt` = the required final semantic diff.
4. Add a test named `test_curveball_<slug>.py` for the new behaviour.
5. Commit: `feat(curveball): <one line what changed>`. Push. **Checkpoint → `curveball-response`**.

### Phase 7 — Web on Vercel + MCP  (≈40 min; only after Phase 5 is green)

Truthful framing for BUILDATHON.md: *the CLI runs locally next to the repo; Vercel hosts the install endpoint, the agent-card viewer, and a report API.*

1. `web/` Next.js (app router, TypeScript, Tailwind). Pages: `/` (what amu is + `curl -fsSL https://<vercel>/install.sh | sh`), `/card` (PRD screenshot 2: paste or upload `amu map --json` / `state.json` → tiles sound/guessed/unknown/ripples, rows with badges, buttons View JSON / Answer nX / Approve — Approve enabled only if planner `proceed` is in the JSON), `/report/[id]` (renders a `done` report JSON stored via `POST /api/report`, in-memory/KV; no secrets). `public/install.sh` served statically.
2. `amu done --publish` posts the report JSON to `config.json → web.report_url` if set and prints the link. Off by default.
3. `amu/mcp_server.py` (python `mcp` package, stdio): tools `amu_map`, `amu_plan`, `amu_check`, `amu_done`, `amu_verify` — thin wrappers that shell out to the CLI with `--json`. Add the Claude Code `.mcp.json` snippet to README.
4. Commit: `feat(p7): vercel web (install, agent card, report) + mcp server`. Push. Vercel: import repo, root dir `web/`, no env vars needed. Paste the deployed URL into BUILDATHON.md and README.

### Phase 8 — Submission hardening  (≈30 min)  → checkpoint `final-verification`

1. **Clean-checkout rehearsal**: `cd /tmp && git clone <repo> x && cd x && ./scripts/install.sh && amu init && amu map --file amu/classify.py && amu check`. Fix anything that fails. This is the checklist's "launches from a clean checkout".
2. BUILDATHON.md complete per §5 template; grep the repo for keys: `git grep -nE "(sk-ant-|ANTHROPIC_API_KEY=|ghp_|AKIA)"` must be empty.
3. `docs/DEMO.md` — the 3-minute script: `amu map` → `amu plan --approve` → edit → `amu check --phase 1` (show a red + delta brief, then green) → `amu done`. Record it: `asciinema rec docs/demo.cast` (fallback: screenshots to `docs/screenshots/`).
4. Final `entire graph diff --base <init-understanding sha> --head HEAD > docs/evidence/20-final-semantic-diff.txt`.
5. Commit: `chore(p8): buildathon submission`. Push. **Checkpoint → `final-verification`**. Record final SHA: `git rev-parse HEAD`.

---

## 3. Checkpoint protocol (do exactly this, four times)

Entire creates checkpoints from the Claude Code session + commit hooks. To make a *named, linkable* checkpoint:

```bash
git add -A && git commit -m "checkpoint: <name> — <one line>"      # name ∈ init-understanding | pre-curveball-stable | curveball-response | final-verification
git push origin main                                                # pushes entire/checkpoints/v1 alongside
entire checkpoint list --json | tee -a docs/evidence/checkpoints.jsonl
entire status                                                       # copy the entire.io link it prints for this repo/session
entire graph checkpoint <CHECKPOINT_ID> > docs/evidence/checkpoint-<name>.txt   # graph analysis of that checkpoint = extra evidence
```
Write into BUILDATHON.md the row: name · commit SHA · checkpoint ID · entire.io link · what it proves. If `entire checkpoint list` shows nothing, run `entire doctor`, confirm the Claude Code hooks fired (`entire status`), then `entire checkpoint explain` before retrying — do not fabricate an ID.

---

## 4. Time box for today

| IST | Do |
|---|---|
| now → +0:25 | Phase 0 (checkpoint 1) |
| +0:25 → +1:40 | Phase 1 (the loop) |
| +1:40 → +1:55 | Phase 2 |
| +1:55 → +2:55 | Phase 3 |
| +2:55 → +3:35 | Phase 3b (memory) — this is the demo's "wow" moment, keep it |
| +3:35 → +4:05 | Phase 4 |
| +4:05 → +4:35 | Phase 5 (checkpoint 2) |
| Curveball window | Phase 6 (checkpoint 3) — drop Phase 7 first if time is short |
| last 70 min | Phase 7 (if time) → Phase 8 (checkpoint 4) → submit by **2:45 PM IST** |

**Priority if behind:** Phase 8 > Phase 6 > Phase 1 > Phase 5 > Phase 3b > Phase 2 > Phase 4 > Phase 3 > Phase 7. The four checkpoints and graph evidence are worth 30 points; the web page is worth a fraction of 10.

---

## 5. BUILDATHON.md template (replace the existing file's body; keep any morning content that's still true)

```markdown
# amu — Anti-Messup · BUILDATHON

Track: Entire main challenge · Track 2 Graph Intelligence
Fork/repo: https://github.com/fayasfarmisofficial-cyber/TWC-Thugs
Final commit SHA: <fill at Phase 8>
Project URL (Vercel): <fill at Phase 7>   Demo recording: docs/demo.cast (+ docs/screenshots/)

## Problem and innovation
One paragraph: agents break what they can't see; amu builds the blast map from entire-graph, turns it into an
ordered contract, gates every phase, and closes the loop with a sweep + docs. Honesty model: sound/guessed/unknown,
every non-sound node has a verify action, no "safe/clean" verdicts ever.
Memory: amu owns a managed block in CLAUDE.md/AGENTS.md and rewrites it after every task from hot spots,
approved contracts, answered unknowns and sweep findings — each line cites the Entire checkpoint it came from,
and goes stale automatically when its evidence hash moves. The agent's instructions improve with every run.

## Setup / Run (clean checkout)
    ./scripts/install.sh            # entire CLI + graph plugin + amu (as `amu` and `entire amu`)
    amu init
    amu map --file amu/classify.py
    amu plan --targets amu/classify.py#classify:signature --approve
    amu check --phase 1
    amu done
Run tests: `pytest -q`

## Entire Checkpoints (4 required)
| Checkpoint | Commit | Checkpoint ID | Link | Proves |
|---|---|---|---|---|
| init-understanding | … | … | … | graph-first read of the codebase before edits |
| pre-curveball-stable | … | … | … | loop closes, tests green |
| curveball-response | … | … | … | change made under amu's own contract |
| final-verification | … | … | … | clean-checkout run + final semantic diff |

## Entire Graph evidence
- Definition/search lookup: docs/evidence/01-search-classifier.json, 02-def-classify.txt
- Impact analysis before the Curveball: docs/evidence/10-impact-pre-curveball.txt, 11-amu-map-pre-curveball.json
- Final semantic diff: docs/evidence/14-semantic-diff.txt, 20-final-semantic-diff.txt
- Commands used: search, def, impact, neighbors, diff, verify, snapshot, snapshot-query, checkpoint, doctor, capabilities

## Response to the Curveball
What landed · what amu mapped (radius/resolution counts) · the contract · any red + delta brief · what the sweep found · test added.

## How amu builds on the Entire CLI (not beside it)
`entire-amu` plugin ⇒ `entire amu …`; every graph fact is an `entire graph` call; checkpoints are Entire's; MCP tools wrap the CLI.

## Tests
List files + what each protects (critical path + Curveball behaviour).

## Databricks
Not applicable.

## What's next
shadow, verify --attempt-fallback with SCIP, docs --mode auto in CI, agent card via MCP in-editor, mapper ripple review.

(No secrets. Model key is read from ANTHROPIC_API_KEY at runtime; roles run in manual mode without it.)
```

---

## 6. CLAUDE.md block (append below the managed entire-graph block)

```markdown
## amu project rules
- Search first: `entire graph search --repo . --profile full --query "…"` before opening files. Never guess a symbol name.
- Before editing any symbol: `amu map --symbol <path#name> --change <class>`; then `amu plan --approve`; edit one phase; `amu check --phase N`.
- Never write "safe", "clean", "verified" or "OK" as a verdict in code, tests, docs or commit messages.
- All `entire` subprocess calls go through `amu/entire.py`. No other module shells out.
- Tests use `tests/fake_entire.py` fixtures; never require the real binary.
- Each phase of BUILD_PLAN.md ends with: `pytest -q` green → `ruff check .` clean → commit (given message) → `git push origin main`.
- Checkpoints: follow BUILD_PLAN.md §3 exactly; record IDs in BUILDATHON.md; never invent an ID or link.
- Never commit keys. `amu doctor` reports whether ANTHROPIC_API_KEY is present, never its value.
```

---

## 7. Schemas (put in `amu/state.py`; validate on read and write)

```jsonc
// map (amu map --json)
{ "root": {"file": "src/hooks/useLikedSongs.ts", "exports": 3, "internal": 2, "feature": "Liked", "feature_source": "derived"},
  "nodes": [ {"id":"n4","path":"api/songs.ts","symbol":null,"confidence":"guessed","risk":"might_break","feature":"Core",
              "order":3,"action":"edit","via":null,"reason":"co-change 38/50 · red in 2 of last 4","verify":"run LikedList.test.tsx",
              "evidence":["impact:co-change"]} ],
  "ripples": [], "features": {"Liked":3,"Player":1,"Core":2},
  "radius": {"files":7,"packages":3,"exports":3,"consumers":5,"tests":3},
  "resolution": {"sound":4,"guessed":1,"unknown":1,"qualifier":"3 unresolved call sites, 1 dynamic dispatch"},
  "next": "amu plan --targets useLikedSongs:signature --approve", "map_hash": "…" }

// contract.json
{ "targets":[{"symbol":"…","change_class":"signature"}], "base_sha":"…", "map_hash":"…",
  "phases":[{"n":1,"kind":"additive","nodes":["n2"],"files":["…"],"tests":["…"]}],
  "frozen_signatures":["…"], "allowed_files":["…"], "created_at":"…" }

// state.json (per node): planned | editing | green | red | drift ; plus violations[] with code, file, line, blocking
// role handoff: {"decision":"…","confidence":0.0,"escalate":false,"evidence":["file:line"|"n4"],"next":"…"}
```

---

## 8. Definition of "live" for the demo owner

`amu map --file <f>` and `amu check --phase 1` run on the demo owner's machine from a clean clone using README instructions, with `entire amu map` also working. The Vercel URL opens `/card`, and pasting `amu map --json` output renders the agent card. Fallback: `docs/demo.cast` and `docs/screenshots/`.

---

# Phase 9 — Navigation, live graph rendering, and the gold identity

Read `BUILD_PLAN.md` and `docs/ARCHITECTURE.md` before writing anything. This phase adds three things to the existing `amu` CLI: **workspace navigation**, **live graph rendering while work happens**, and a **gold visual identity**. It changes no honesty rule, no exit code, and no `--json` shape. Existing tests must stay green.

## 9.1 — Workspace and repo navigation

Right now `amu` only works on the current directory. Make it a workspace tool the way Claude Code is: you can add repos, switch between them, and every command runs against the active one.

Add `amu/workspace.py` and a global `~/.amu/workspace.json`:

```jsonc
{ "active": "twc-thugs",
  "repos": [ { "name":"twc-thugs", "path":"/Users/x/code/TWC-Thugs", "origin":"https://github.com/…",
               "indexed_at":"…", "graph_hash":"…", "last_map":"amu/classify.py", "branch":"main" } ] }
```

Commands (all take `--json`):

| Command | Behaviour |
|---|---|
| `amu repo add <path>` | Register a local repo. Verify it is a Git repo, run `entire graph index --repo <path>`, snapshot it, write `.amu/` inside it, make it active. Reject a path that is not a Git worktree with exit 2 and the reason. |
| `amu repo add <owner/repo>` or a GitHub URL | `git clone` into `~/.amu/repos/<owner>__<repo>` (respect an existing clone: `git fetch && git status` instead of re-cloning), then the same index path as above. Use the user's existing git credentials; never prompt for or store a token. `--branch <b>`, `--depth 1` supported. |
| `amu repo list` | Table: name · branch · files/symbols/relations · last indexed (relative) · active marker. |
| `amu repo use <name>` | Switch active repo. Print the new banner header. |
| `amu repo remove <name> [--delete-clone]` | Deregister; only delete files if the flag is passed and the repo lives under `~/.amu/repos`. |
| `amu repo sync [--all]` | `git fetch` + re-index + re-snapshot; report what changed at entity level via `entire graph diff --base <old sha> --head HEAD`. |
| `amu cd <name>` | Print the path only (so `cd "$(amu cd twc-thugs)"` works in a shell). Do not try to change the parent shell's directory. |

Every existing command resolves its target as: `--repo` flag → repo containing `$PWD` → workspace active repo → error with the `amu repo add` hint.

Also add in-repo navigation, all backed by `entire graph`, none of it re-implementing search:

| Command | Backed by |
|---|---|
| `amu find "<plain sentence>"` | `entire graph search --profile full --format json`; render ranked hits with their `signals[]` (path / body / symbol-name / graph:callers) so the user sees *why* each ranked. `--open n2` prints the file:line. |
| `amu open <path\|symbol>` | `entire graph def` + the surrounding declaration; then the one-line "next" (`amu map --symbol …`). |
| `amu tree [path] [--depth N]` | Directory tree annotated per file with export count and feature label from `feature_map.json`. |
| `amu neighbors <symbol> [--relation CALLS] [--direction in\|out]` | thin wrapper over `entire graph neighbors`. |
| `amu where <symbol>` | def site + the file's feature + whether it is frozen in the current contract. |
| `amu back` / `amu recent` | Navigation history stack in `.amu/nav.json` (last 20 targets), so `amu back` returns to the previous map. |

In the interactive session, add slash commands `/repo`, `/use`, `/find`, `/open`, `/tree`, `/back`, and make bare text with a slash-free path or symbol resolve through `router.py` as an `explore` intent. Tab-completion for repo names, and for symbols from the snapshot, via `prompt_toolkit`.

Rules: `amu` never invents a symbol — every name comes verbatim from `entire graph search` or `def`, and an unresolved one returns `not_found | ambiguous | multi_target`. Cloning is the only network operation; if `git clone` fails, print the exact command that failed and exit 3.

## 9.2 — Live graph rendering while the work happens

The graph should be visible during work, not only in a static `amu map` dump.

1. **Live tree.** During the phase loop, `render.py` redraws the same PRD §9 tree in place (Rich `Live`, ~4 fps, `--no-live` to disable and `NO_COLOR`-safe) with a state column per node: `· planned  ▸ editing  ✓ green  ✗ red  ↯ drift`. State comes from `.amu/state.json` only — the renderer still computes nothing.
2. **`amu watch`.** Watch the working tree (`watchfiles`); on each save, re-resolve the changed file against the active contract and update node states: file in this phase → `editing`; outside the contract → `↯ drift` warning line; an unknown node touched → `UNKNOWN_TOUCHED` warning. Debounce 300 ms. Never runs tests on its own — it renders, it does not verify.
3. **`amu graph <symbol> [--depth 2] [--relation CALLS]`.** An ASCII relation graph rendered from `entire graph impact` / `neighbors`: the target in the centre column, callers above, callees below, type consumers and data flows in side sections, each edge labelled with its relation type and each node carrying its confidence glyph (`● sound · ◐ guessed · ○ unknown`). Deterministic layout — same input, same drawing. `--format dot` emits Graphviz and `--format mermaid` emits a Mermaid graph so it can go straight into a PR description.
4. **Progress that names the relation.** While indexing or mapping, the spinner line reports the real work — `resolving callers of classify · 34 edges` — not a generic bar. Counts come from the graph, never estimated.
5. **Web view (extends Phase 7).** `/graph` renders the same `amu graph --json` payload as an interactive node-link diagram (d3-force), nodes coloured by confidence, edges labelled by relation, click a node to see its reason and verify line. It reads a pasted or uploaded JSON file. No repo data is ever uploaded automatically.

Everything here reads from the same JSON the terminal reads. If a rendering needs a fact the graph did not give, it shows `unknown` with a verify line — it does not infer.

## 9.3 — Visual identity: gold

Replace the current brand colours in `amu/brand.py`. Keep the `TWC THUGS` wordmark and the stderr-only rule; only the palette and layout change.

```
gold      #F5B32E   primary — wordmark, headers, active repo, selected row
amber     #E08A1E   secondary — borders, rules, the prompt caret
ember     #C2410C   accents on risk (will_break)
sand      #FDF6E3   body text on dark
ash       #8A8578   muted — via, paths, timestamps
```

- Wordmark in gold, subtitle in ash, a single amber rule under the header. Prompt: `twc-thugs ❯` with the caret in amber.
- Confidence glyphs keep their own semantics and are **never** gold: sound green, guessed amber, unknown ash-hollow, risk ember. Colour stays redundant with the glyph so `NO_COLOR=1` still reads correctly — test it.
- Panels use rounded borders in amber at 30% weight, generous padding, section labels in small-caps ash. No boxes inside boxes. One accent per screen region.
- Degrade cleanly: truecolor → 256 → 16 → none. Under 100 columns drop `feature` and `via`, never the glyph or `verify`.
- The Vercel pages and the agent card use the same five tokens as CSS variables; dark background `#161412`.

Update `test_brand.py` to assert the palette constants, the `NO_COLOR` render, and that no ANSI escapes reach `--json`.

## Done when

`amu repo add <a GitHub URL>` clones, indexes, and becomes active; `amu find "…"` returns ranked hits with signals; `amu graph <symbol>` draws the relation graph and `--format mermaid` is valid Mermaid; `amu watch` updates node states live during an edit; the whole surface is gold and passes `NO_COLOR=1`; `pytest -q` and `ruff check .` are clean.

Commit: `feat(p9): workspace + repo navigation, live graph rendering, gold identity`. Push. Then show me `amu repo list` and `amu graph classify --depth 2`.


---

# Phase 10 — Bring your own model (executed 6 Sep 2026)

`amu key set [--provider anthropic|bedrock|vertex|foundry|openai-compatible|ollama|lmstudio|openrouter] [--base-url] [--model] [--region] [--project] [--resource]`,
`amu key status|remove`, `amu ask "<question>"`, and REPL free text → a tool-using assistant over read-only graph tools. See README ("Bring your own model")
and docs/ARCHITECTURE.md (Phase 10). Rules kept: amu never writes source; the assistant cannot enlarge a contract or promote confidence; secrets are never printed;
Anthropic-only request parameters are sent only to the Anthropic family.
