# amu — Anti-Messup Agent

> Track 2 · Graph Intelligence · Bengaluru Tech Week Buildathon 2026

`amu` is a CLI guardrail that wraps the `entire` graph engine to give AI coding agents (and humans) a structured pre-edit impact brief **before** touching any symbol.

## Concrete patch inspection

The bounded patch workflow accepts a Python unified diff, reports source-backed
changes, behavioral check targets, and recommended existing tests. Start with
the [Phase 3 quickstart](docs/phase3-quickstart.md) and [schema-8 input suggestions](docs/phase3-input-suggestions-v8.md); the original
[Phase 2 cache walkthrough](docs/phase2-quickstart.md) remains available.
`amu analyze-patch` only inspects candidate source in memory; `amu verify-fixture`
requires an explicit flag to run the pinned example checks.

## Setup / required edit loop

Start this loop **before changing graph-relevant source code**. A map made after
source edits is still useful for inspection, but AMU will not let it become the
approved pre-edit baseline.

```bash
./scripts/install.sh --local .       # requires pipx; installs this checkout, Entire CLI + graph plugin
amu init                             # index + snapshot + config/feature-map/doc-map/skills + memory seed
amu map --symbol amu/classify.py#classify_consumers --change signature
amu plan --targets amu/classify.py#classify_consumers:signature --docs-file docs/guide.md --approve
# make the planned code change
amu refresh --symbol amu/classify.py#classify_consumers --change signature
amu check --phase 1                  # repeat edit → refresh → check for later phases
amu docs                             # review documentation candidates
amu check                            # renew evidence if anything changed after the phase checks
amu done                             # only after every planned phase is green
pytest -q                            # deterministic suite; graph cases use tests/fake_entire.py
```

`entire amu map --file amu/plan.py` works the same way: the `entire-amu` entrypoint is dispatched by the Entire CLI.

The documentation path above is an example: select an existing Markdown/RST file
under your configured `docs_dir`, or omit `--docs-file` for a code-only task.
Repeat it for each document you explicitly approve for this change. The draft
shows the selected scope; saves still require separate prose review. A later
documentation save never silently expands the contract, and unrelated documents
remain out of scope.

### Release checks

AMU remains a hardened alpha. The [release-readiness checklist](docs/RELEASE-READINESS.md)
defines the supervised local pilot, its evidence and the remaining release gates;
passing a local suite is not general production certification.

The static viewers now include labeled inputs, keyboard node inspection and
movement, pan/zoom buttons, bounded JSON input, local diagram assets, inline
errors, and readable table alternatives. Large accepted graphs use complete
text inspection rather than a partial diagram.
See [viewer accessibility coverage](docs/VIEWER-ACCESSIBILITY.md) for controls,
browser-test commands, and verification limits.

The installer requires an explicit source: `sh scripts/install.sh --commit <full-40-character-commit>` for a fixed Git revision, or `sh scripts/install.sh --local /absolute/path/to/package.whl` (also accepts a project directory). There is no moving-branch default and no assumed published release tag. Review/trust the chosen source first; installing Python projects can execute build code. Repeat the command with a new commit or wheel to upgrade. Local directories are development inputs, not immutable releases. Entire and its graph plugin are not pinned.

Install pipx with support for `install --backend pip` before using the installer; it will not modify system Python as a fallback. Explicitly selecting pip ensures its constraint policy is used even when an existing pipx environment used another backend. The script checks Homebrew/graph setup, pipx installation, dependencies, the installed command, and `entire amu doctor`. Success is printed only after every check passes. It uses pipx's executable directory for those checks and prints a PATH reminder for future shells. A failed install or health check exits nonzero, but may leave changed packages or prerequisites: inspect pipx state and rerun a known-good source to recover. Automatic rollback is not implemented. Installer regressions use offline command doubles; the separate wheel smoke test exercises real package installation.

`constraints.txt` pins direct and transitive Python dependencies for the base app, optional integrations, and development tools. Commit installs load that file from the **same commit**. Local installs require it in the project directory or beside the wheel; missing constraints fail rather than install unlocked. Upgrades reinstall under those pins. Keep the wheel and its matching constraints file together when sharing a release. The source archive includes the constraints; CI also copies them beside the wheel. These are version constraints, not artifact hashes or a complete environment lock: Python, pip/pipx, the OS, external graph tools, and download availability remain outside the guarantee. Plain `pip install amu` does not automatically apply constraints.

For constrained development use `python -m pip install -c constraints.txt -e '.[dev]'` in a new virtual environment. Run `python -m pip check` and `python scripts/check_constraints.py` to catch incompatible requirements, version drift, or unpinned installed packages. Build with `python -m build --no-isolation` in that environment; the setuptools backend is also pinned in package metadata. Update pins deliberately with uv 0.12.17: `uv pip compile pyproject.toml --all-extras --universal --upgrade --no-header --no-annotate -o constraints.txt`, review the diff, and rerun the full release matrix. The file supports one version per package; platform markers determine whether it is installed.

The GitHub Actions release-check workflow runs the deterministic suite on Linux and macOS with Python 3.11 and 3.14. It builds a source archive, builds the wheel from that archive, and installs it in a fresh virtual environment. No publishing or artifact uploads are configured. The matrix is a verification target, not a claim that every platform has already passed; Windows is not covered.

The source archive includes test helpers/fixtures, smoke scripts, documentation,
browser assets/tests and the local D3 license. Run `python scripts/check_source_archive.py
/path/to/amu.tar.gz` to check completeness and bounded, safe archive structure
without extracting or executing it. Current candidate hash sidecars stay outside
the archive to avoid self-referential hashes.

`scripts/smoke_installed.py` runs with the installed environment's `python -I`, checks that imports come from that environment, and exercises both console entry points, version agreement, bundled skills, missing-engine errors, a real piped REPL session (including recovery from invalid arguments), and MCP-wrapper/CLI error parity. It uses a temporary home without inherited model credentials. This is not a browser test, a live-provider test, or a full MCP protocol handshake. Real graph/NumPy integration tests remain opt-in and their skips are visible in CI output.

After the base-package smoke, install the optional MCP SDK with the same constraints
and run `python -I /absolute/path/to/scripts/smoke_mcp_installed.py` in the installed
environment. It checks the actual stdio handshake, tool listing, safe calls,
strict confirmation/index types and shutdown without saving documentation or
making model requests. Wire approvals require a JSON boolean, not `1` or `"true"`.

For an opt-in real-engine operator drill, run the installed environment's
`python -I /absolute/path/to/scripts/smoke_pilot_installed.py --entire
/absolute/path/to/entire --graph-plugin /absolute/path/to/entire-graph`. Both
executables must be deliberately selected and trusted. The drill hashes them,
uses a disposable repository/home/cache, blocks model requests and reports the
edit/check/completion plus reviewed-save/undo/conflict results. It is not an
installer, a NumPy accuracy estimate or a runtime-safety certificate.

To reproduce locally, build as above, install the resulting wheel into a fresh virtual environment using `python -m pip install -c constraints.txt /path/to/amu.whl`, run that environment's `python -m pip check` and `python /absolute/path/to/scripts/check_constraints.py`, then run its `python -I /absolute/path/to/scripts/smoke_installed.py`. The smoke test requires Git and does not require Entire. Package versions come from `amu/__init__.py`. Optional integrations can be installed with `amu[model]` (Anthropic SDK) or `amu[mcp]` (MCP server); pass `-c constraints.txt` for those installs too. The base package supports offline commands and the built-in HTTP model adapters.

Run `amu` with no arguments for the interactive terminal. Start with `/help`: it groups simple commands into **Explore**, **Plan a change**, and **Workspace**, and accepts commands with or without the slash. A bare path or symbol opens it; with a model connected, other free text is answered through read-only graph tools and ends with the next AMU command.

### What counts as a green phase

A check records explicit success, the approved contract's content hash, HEAD, a worktree fingerprint, runner configuration, and test/build outcomes. Missing fields, malformed history, failed/interrupted checks, changed approvals, and outdated source evidence cannot certify a green phase. Legacy history is retained but requires a fresh `amu check`; it is not automatically upgraded.

`amu check --phase N` rechecks cumulative contract scope and tests through phase N. Earlier successful checks are milestones permitting the next check to run, not proof that an edited worktree is still green. Changing HEAD or runner configuration requires an all-phase `amu check`. The unqualified command also provides recovery after a failed later-phase check without pretending that earlier checks covered the final code.

`--no-tests` is diagnostic only: skipped tests are recorded explicitly and the check exits 1 without advancing phases. An unconfigured build is recorded as `not_configured`; a configured build must actually succeed. If source or configuration changes while a check runs, repeat it on a stable worktree. `amu done` requires successful check evidence matching the current worktree, in addition to the existing graph-refresh gate.

For pytest, exit 0 alone is insufficient: the final summary must report at least
one passed test. All-skipped, xfail-only, collection-only or suppressed/unreadable
summaries cannot advance a phase. Configure `pytest -q` (or an equivalent visible
summary); a mixed passed/skipped run records both counts. Other supported runners
retain exit-status checks; independent review of test adequacy is still required.

If a changed file cannot be read or disappears during fingerprinting, AMU returns no freshness digest and reports the failing path and reason. Broken symlinks and symlinks outside the repository also block verification. Ordinary deletions already reported by Git remain supported. Resolve the reported file problem, then rerun the affected map, refresh, or check.

## Run this checkout directly (macOS)

macOS names the interpreter `python3`. To run the code in this checkout (including local changes) without installing a separate global `amu` command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m amu.cli
```

## Graph integrity gate

Maps query the live worktree by default. A map can be approved only when it began with no graph-relevant source edits, the worktree stayed stable during collection, its focus resolved exactly, and its semantic graph evidence is complete enough for the requested depth. For direct Python `CALLS` candidates, AMU also reads the relevant source as an AST (without importing project code): a relationship found only in a docstring example is removed from the actionable map, recorded in `graph_evidence.contradicted_python_calls`, and blocks approval.

`amu graph`, the MCP graph tool, and the AI chat graph tool apply that same narrow source check before displaying direct Python calls. They keep an audit trail in `source_contradictions` and attach `graph_evidence`; `approvable: false` means the display is useful for investigation but must not authorize an edit plan.

After an agent edits code, run the guarded refresh before relying on the graph again:

```bash
amu refresh --symbol path/to/file.py#function_name --change body
```

For a rename or move, make the old/new identity explicit:

```bash
amu refresh --symbol old/path.py#old_name --change rename --new-symbol old/path.py#new_name
amu refresh --symbol old/path.py#old_name --change move --new-symbol new/path.py#new_name
```

Refresh saves a snapshot of the **current worktree**, queries the target's live graph, and reports resolved semantic edges added or removed relative to an exact pre-edit map. It refuses to label a delta when the old map was truncated, the provider resolves a different focus, its live response is incomplete/degraded, a direct Python call is contradicted by source, or the worktree changes during the refresh. The long-lived Entire index is explicitly a `HEAD` cache; it is not presented as proof that uncommitted edges were cached. `amu check` and `amu done` require a current successful target refresh after graph-relevant code changes; their ordinary snapshot step is not a substitute. Use `/refresh path/to/file.py#function_name` in the interactive terminal for a body-change refresh. It also accepts the CLI options, for example `/refresh old/path.py#old_name --change rename --new-symbol old/path.py#new_name`. Quote targets containing spaces.

### Cross-file relationships

AMU works at symbol level, not just file level. When the graph provider resolves a semantic `CALLS`, type-consumer, or data-flow edge across files, it appears in the map, `amu graph`, and the post-edit refresh delta. A missing edge is **not** treated as proof that no relationship exists: dynamic dispatch, generated code, unsupported languages, partial parses, and incomplete provider responses remain unknown or degrade the evidence. In those cases AMU refuses an approved baseline or a claimed complete edge delta instead of inventing certainty.

Relationship confidence and compatibility are separate: a resolved edge means the provider reports a relationship, not that the proposed change breaks it. Maps mark these nodes `might_break` (potentially affected), recommend review, and record `compatibility.status: not_assessed`. This applies to callers and callees, including signature changes. AMU does not yet compare proposed argument binding, defaults, return contracts, or public exports to certify compatibility.

Relation records preserve provenance across map ripples, graph JSON, and refresh baselines: `parser`, `compiler/indexer`, `heuristic`, or `runtime`. Missing or unsupported provenance is `unknown`, never inferred to be compiler-verified. Provenance describes the evidence source, not proof of runtime impact.

### Reviewing unknowns

Run `amu answer` to list unresolved questions and saved answers. Submit your own answer with `amu answer --node n1 --text "Your explanation"`. Chat-generated answers are model suggestions, not human decisions: inspect their text with `amu answer`, then explicitly accept one with `amu answer --confirm <answer-id>`. Its original model authorship is retained. Inside the interactive terminal, use `/answer` with the same options.

Answers bind to the exact question, map evidence, and source version. Changes invalidate them; legacy unbound answers cannot clear current questions. Each unknown needs its own current confirmation. Confirmation does not upgrade graph confidence, authorize edits to unknown nodes, or override mandatory graph requirements.

### Documentation suggestions

`amu docs` produces candidates for review; it does not certify that documentation is current. `amu docs --mode auto` writes only a sound, mapped candidate from the committed graph diff when no worktree evidence shadows it. Worktree-only, private/protocol, malformed, unsupported, or source-parse-uncertain evidence stays manual. To apply a listed candidate yourself, first run `amu docs`, review it, then use `amu docs --apply dN`; AMU rejects that display id if `HEAD` or the worktree changed since the listing.

Documentation impact is decided by the public API/behavior diff first. The code graph then scopes changed callers, callees, types, and data flow; the maintainer-owned doc map and a derived docs index locate sections that may describe them. `amu docs-graph` records mapped symbols, exact symbol mentions, Markdown links, and Sphinx/RST `:doc:`/`:ref:` links in `.amu/docs-graph.json`. Comparing a changed code edge with that index produces `requires_verification` review guidance—never a claim that every internal edge belongs in user documentation or that an unmentioned edge proves the docs are current. Accepting a docs edit refreshes the derived index immediately.

Documentation operations now share a transport-independent application service.
CLI syntax is unchanged; the interactive parser and bounded MCP wrapper reach
that same implementation. Direct Python callers can use immutable requests and
explicit repository selection. Saved outcomes remain visible when later graph
bookkeeping fails; partial auto errors distinguish confirmed saves, unconfirmed
operations and candidates not attempted. See [application services](docs/APPLICATION-SERVICES.md).

`amu api-manifest --file pkg/api.py --base HEAD --json` exposes the before/after
static public surface directly. Python exports, aliases, re-exports, overloads,
defaults and static behavior hints now feed into the manual docs review queue.
JSON/TOML keys can opt in through exact file/key mappings; unsupported and
dynamic constructs remain explicit unknowns. `amu benchmark` runs the bundled
versioned fixture scorecard without a graph provider or model. See
[API detection and accuracy checks](docs/API-ACCURACY.md) for scope, limits and
separate raw/source-validated relation scoring. Exit zero is not a completeness
or accuracy guarantee.

To edit a proposed replacement before saving it:

```bash
amu docs --mode draft
amu docs --prepare d1
# Edit the section body in the printed .amu/doc-reviews/ file.
amu docs --apply d1 --review
```

Inside the interactive terminal, use `/docs --mode draft`, `/docs --prepare d1`, then `/docs --apply d1 --review` after editing the printed draft file. `/docs --help` and `/refresh --help` show the shared CLI options; invalid arguments report an error and leave the session open. These commands use the selected interactive repository.

This replaces the selected section body and preserves its heading. Markdown, MDX, and RST are supported. An exact old signature in the section is updated in the proposed text when the change evidence supports it; uncertain behavior changes retain the original prose with a separate recommendation for human review. Saving checks that the candidate and original document are still current, then refreshes the docs graph. For multiple targets, choose a section with `--target-index N` on prepare and reviewed apply. `amu docs-reviews` lists drafts; `--preview REVIEW_ID` shows edits and `--discard REVIEW_ID` archives a recoverable copy. See [the review guide](docs/DOC-REVIEWS.md). Plain `--apply d1` retains the earlier append-a-change-note behavior.

Section edits use parsed boundaries: Markdown fences, indented code, HTML blocks, and quoted/list headings do not become editable top-level headings. ATX and setext headings are supported. RST top-level underline/overline headings use document-defined levels; indented directive contents are excluded. A section edit preserves child sections and labels attached to the next section. Duplicate heading names require a unique generated anchor (for example `#api-2`); malformed RST adornments and MDX expressions/JSX require manual editing. Plain-Markdown MDX remains supported. Old review drafts without recorded section boundaries must be moved aside and prepared again; retain any prose you want to reuse.

Documentation saves (review replacement and append-note) now use a shared recovery journal in `.amu/doc-saves/`, a flushed temporary file, an immediate source-content/identity recheck, and atomic replacement. AMU saves are serialized. External editors do not share that lock: a narrow race remains between the final check and replacement, so this is optimistic conflict detection, not a filesystem-wide transaction. Recovery records retain original/proposed prose and use owner-only permissions; keep them until you no longer need recovery.

The result distinguishes `saved` from `graph_refreshed`. If prose was saved but graph refresh failed, do not reapply the candidate: run `amu docs-graph`. Run `amu docs-saves` to inspect recovery records, including interrupted operations. To undo one, run `amu docs-saves --undo <save-id>`; undo refuses if the document changed after that save and creates its own recovery record. A `prepared` record whose current content `matches_saved` indicates the replacement happened but final bookkeeping may have been interrupted. Save history records graph refresh at the time of that operation, not a guarantee that the graph remains current forever.

After saving documentation, run `amu check` before `amu done`: tests and scope still bind the whole worktree. Uncommitted prose-only edits can retain their code refresh and reuse a validated source snapshot during completion; the docs graph is refreshed separately. Code, executable examples, MDX, configuration or provider changes still require current code evidence. Existing legacy captures and new commits remain conservative refresh boundaries. See [freshness behavior](docs/FRESHNESS.md). Documentation files must be within the approved edit scope; accepting a draft does not widen that scope. A default `amu plan --approve` expands the map's resolved symbols into exact refresh targets.

Graph commands now enforce byte limits while receiving output, and snapshots
are validated/streamed into a temporary file before atomic publication. Overflows
remain explicit errors and cannot become empty graphs, approved maps or removal
deltas. Failed sync attempts preserve the old snapshot with separate prior-capture
metadata. Chat/MCP child commands are bounded too. See
[graph resource safeguards](docs/GRAPH-RESOURCES.md) for exact limits, recovery and
remaining platform/resource constraints.

The normal suite is deterministic and uses recorded graph responses. To audit the real graph provider against a local NumPy checkout without importing or executing NumPy, run:

```bash
AMU_RUN_EXTERNAL_GRAPH=1 \
AMU_NUMPY_REPO=/absolute/path/to/numpy \
AMU_EXTERNAL_ENTIRE_BIN=/absolute/path/to/entire \
pytest -q -rxX tests/test_external_numpy_graph.py
```

Set `AMU_EXTERNAL_GRAPH_STRICT=1` when you want any provider false positive to fail the audit rather than appear as an expected failure.

This NumPy check is a focused live-provider integration proof, not a claim that every relationship in a large mixed-language repository is parseable. Provider coverage gaps are surfaced as incomplete evidence rather than silently treated as no relationship.

The banner tells you whether a model is connected — `● model on · anthropic · claude-opus-5 · via file` or `○ model off · no API key added · type /key add`. `/key add` is a guided, in-session setup (pick a provider, enter only what it needs; keys are hidden and stored 0600); `/key remove` forgets it. Everything except free-text questions works with the model off.

Web (Vercel, static): `web/public` — `/` install page, `/card` agent-card viewer for `amu map --json`, `/graph` d3-force viewer for `amu graph --format json`. Same five gold tokens as the terminal. Import the repo with root dir `web/`.

Bring your own model:
```bash
amu key set                                              # Anthropic key, hidden prompt (or export ANTHROPIC_API_KEY)
amu key set --provider gemini                             # Gemini key, hidden prompt (default: gemini-flash-latest)
amu key set --provider ollama --model qwen2.5-coder      # local, no key
amu key set --provider openrouter --model anthropic/claude-opus-5   # prompts for the key
amu key set --provider bedrock --region us-east-1        # AWS credentials from the environment
amu key set --provider vertex --project my-gcp --region global
OPENAI_BASE_URL=http://localhost:8000/v1 AMU_MODEL=my-model amu ask "…"   # env override, no file
GEMINI_API_KEY=... amu ask "…"                             # Gemini Developer API, no file
```

### Model outbound-data policy

Credential storage uses a private (0600) temporary file, flushes its contents, and atomically replaces `credentials.json`. Failed writes before replacement preserve the previous credential. New AMU home directories are 0700; existing directories must be owned by you and not writable by other users. Credential reads/writes reject symlinked homes and symlinked or multiply hard-linked credential files. Parent directories must be trusted; this does not protect against another process running as your own user. An abruptly killed process can leave a private `.credentials-*.tmp` file; remove that specific leftover after checking no credential update is running. Atomic replacement is not a guarantee of durability after power loss.

Provider endpoints must use HTTP(S), include a host, and omit embedded usernames/passwords, query strings, and fragments. Validation also runs before client creation for environment/legacy settings. Explicit HTTP remains supported for local or deliberately configured compatible servers; use HTTPS for remote providers because HTTP does not encrypt credentials or prompts. Remote provider error bodies remain withheld.

Chat and planner requests share a last-step sanitizer, including follow-up tool results and conversation history. File reads block environment files, common credential/key files, AMU/Git/cloud credential directories, symlinks, and Git-ignored paths. Reads are bounded and require a regular UTF-8 file. Structured graph/memory records referencing blocked files are withheld. Definition lookup uses path-bearing search candidates followed by policy-checked reads, not unstructured provider source output. Provider error bodies/URLs are withheld from diagnostics.

Configure repository policy under `outbound` in `.amu/config.json` (merge with existing settings):

```json
{"outbound": {"enabled": true, "deny_paths": ["private/*", "customer-data/*"], "max_file_bytes": 262144, "max_payload_bytes": 262144}}
```

Set `enabled` to `false` to block model requests while retaining local deterministic workflows. Additional deny patterns cannot remove built-in exclusions. Limits must be positive integers no larger than 1 MiB; oversized requests are rejected rather than silently dropping safety evidence. Malformed policy blocks requests. A repository without readable Git ignore rules cannot supply source through the model's file-read tool.

Run `amu model-preview "your question" --repo /path/to/repo --json` to inspect the sanitized **initial chat request without sending it**. Later tool responses are not known at preview time; every subsequent call passes through the same policy again. The preview itself may contain allowed proprietary source/context, so do not publish it blindly.

Redaction covers recognized credential assignments, common token forms, private-key blocks, URL passwords, bearer credentials, and the configured API key. It is best effort: arbitrary confidential prose, unknown secret formats, or source snippets without file provenance cannot be reliably recognized. Use repository exclusions and the off switch for sensitive work. Repository content is explicitly labeled untrusted in model instructions; this is defense in depth, not a guarantee against prompt injection. These controls apply to AMU's built-in model calls, not the separate data-sharing policy of an external MCP client.

MCP (local stdio clients such as Claude Code / Cursor) — `.mcp.json`:
```json
{"mcpServers": {"amu": {"command": "/absolute/path/to/installed/python", "args": ["-I", "-m", "amu.mcp_server", "--allow-repo", "/absolute/path/to/project"], "cwd": "/absolute/path/to/project"}}}
```

Startup requires operator-selected `--allow-repo` roots and defaults to inspection
tools only. Repeat the flag for up to 16 exact repositories. Old commands without
this flag intentionally fail closed. Add `--enable-actions` only for trusted
repositories after reviewing their test/build commands and client approval policy.
Built-in model discovery/requests are disabled in MCP children even when ambient
credentials exist. The executable uses stdio only; it does not start an HTTP server.
The current source candidate's inspection tools are `amu_search`, `amu_graph`,
`amu_impact` and `amu_docs_reviews`; an older installed server may expose only three.
Use `amu doctor --repo /absolute/path/to/project --mcp --mcp-python
/absolute/path/to/installed/python --json` to check actual installed identities and
full discovery parity without updating live settings. See [line-impact inspection
and evidence paging](docs/IMPACT-ANALYSIS.md).

Every repository-operating MCP tool still requires its own absolute `repo` input,
for example `{"repo":"/absolute/path/to/numpy"}`. Existence alone no longer grants
access: it must match an authorized root, and directory replacement invalidates
the startup grant. `cwd` is not an implicit repository selection. See
[MCP security and operation](docs/MCP-SECURITY.md) for bounds, audit logs,
verified-engine install flags and the limits of this local trust boundary.

With actions explicitly enabled, an MCP coding agent should use `amu_search` →
`amu_map_target` → `amu_plan`, supplying that same `repo` each time. Plan expected
prose using `docs_files` before edits. After code edits, call `amu_refresh_graph`,
then check every phase and review/save docs before a fresh check and `amu_done`.
For a rename/move supply `new_target` as well as `target`. Inspection mode does not
offer this action loop. Neither confirmation booleans nor graph output prove
independent human consent or runtime correctness.

## Commands (v0.2)

| Command | What it does |
|---------|-------------|
| `amu init` / `amu doctor [--mcp --mcp-python /absolute/python]` | explicit initialization; read-only health/build/index diagnostics with nonzero failure status and optional actual stdio manifest parity |
| `amu map --file F` / `--symbol path#name --change C` | blast map: sound (resolved edge) / guessed (co-change, siblings) / unknown (unresolved, partial parse). Map before source edits to create an approvable baseline. |
| `amu plan --targets sym:class --approve` | additive → leaves → interior → removal; `--approve` writes `.amu/contract.json` |
| `amu check --phase N` | `entire graph diff` + working tree vs contract: PHASE_DRIFT / OUT_OF_SCOPE / FROZEN_SIGNATURE / UNDECLARED_REMOVAL / UNKNOWN_TOUCHED block; red ⇒ delta brief |
| `amu verify --node nX [--attempt-fallback]` | run a node's verify path; fallback via `entire graph neighbors --internal-only` |
| `amu answer [--node nX --text "answer" \| --confirm answer-id]` | review unknowns, record a human answer, or explicitly confirm a model suggestion against current evidence |
| `amu refresh --symbol path#name [--new-symbol path#name]` | post-edit safety point: save a stable worktree snapshot, validate complete exact live evidence, and report added/removed resolved relationships without replacing the approved pre-edit map |
| `amu sweep` / `amu sync` / `amu done` | findings outside the contract (never "nothing found"); refresh the HEAD cache plus a worktree snapshot; the whole close-the-loop |
| `amu docs --mode flag|draft|auto` | documentation candidates; drafts are SUGGESTED. Auto writes only sound, mapped committed-diff candidates that are not shadowed by worktree evidence. |
| `amu docs-graph` | rebuild the derived documentation section/symbol/link index and show review-only comparisons with post-edit code relationship deltas |
| `amu docs-saves [--undo save-id]` | inspect recoverable documentation saves; undo only when no later edit would be overwritten |
| `amu docs-reviews [--preview review-id\|--discard review-id]` | review draft status/diffs or archive a draft for re-preparation |
| `amu api-manifest --file F [--base HEAD]` | compare Python/JSON/TOML static surfaces and report review signals plus coverage gaps |
| `amu benchmark [--corpus P] [--relations raw.json --validated validated.json]` | versioned labeled scorecards, with raw and validated coverage kept separate |
| `/session` (interactive) | show model request/byte counters and remaining session limits; see [model context](docs/MODEL-CONTEXT.md) |
| `amu memory refresh|show|pin|forget` | self-renovating managed block in CLAUDE.md / AGENTS.md |
| `amu repo add <path\|owner/repo\|url>` / `list` / `use` / `remove` / `sync` / `amu cd <name>` | workspace: many repos, one active; `cd "$(amu cd twc-thugs)"` |
| `amu repo inspect /absolute/local/root` | metadata-only prerequisites, no registration/indexing/grant change; local add/use validate the exact Git root first |
| `amu find "<sentence>"` / `open <path\|sym>` / `tree [path]` / `neighbors <sym>` / `where <sym>` / `back` / `recent` | navigation, all backed by `entire graph search / def / symbols / neighbors` |
| `amu graph <path#sym> [--depth 2] [--relation CALLS] [--format text\|json\|dot\|mermaid]` | relation graph: callers above, callees below, glyph per node; Mermaid/dot for PR descriptions |
| `amu key set [--provider …]\|status\|remove` · `amu ask "<question>"` | **Bring your own provider**: `anthropic` (default, `claude-opus-5`), Google `gemini` (default, `gemini-flash-latest`), `bedrock`, `vertex`, `foundry` via the official SDK clients, or any OpenAI-compatible endpoint (`ollama`, `lmstudio`, `openrouter`, `openai-compatible --base-url …`). Stored 0600, never printed. `ask` answers through read-only graph tools |
| `amu watch [--phase N]` | live tree: `· planned ▸ editing ✓ green ✗ red ↯ drift` while you edit; never runs tests |

### Memory commits

`amu memory refresh` remains local-only by default: it neither stages files nor commits. With `--commit`, AMU builds the memory commit in a temporary Git index and preserves unrelated staged additions, deletions, and partially staged files. Only generated changes to `CLAUDE.md` and `AGENTS.md` are selected. If either managed file already has staged, unstaged, or untracked edits, AMU refreshes locally but refuses the automatic commit so those edits can be reviewed manually.

Automatic memory commits require an existing Git baseline, the repository root, and no active merge/rebase/cherry-pick or custom Git index environment. An existing index lock is respected. Git hooks and signing still run normally; custom hooks retain their normal ability to perform repository side effects. Git failures produce a nonzero exit and a structured `commit_error`, not a false success. Generated edits remain in the working tree for review after a rejected commit.

Results include `committed`, `commit_status`, and a commit SHA on confirmed success. If the commit succeeds but updating the real index fails, the result says `committed_index_update_failed`; inspect staging manually instead of retrying. An indeterminate Git outcome reports `committed: null` and `outcome_unknown`. An interrupted process can leave Git's index lock in place; verify no Git process is active and inspect HEAD/staging before recovering it. AMU does not remove another process's lock or automatically roll back a commit.

## REPL Slash Commands

`amu` (no args) drops into an interactive REPL. Type `/help` at the prompt to see this list:

| Slash command | What it does |
|---|---|
| `/map <file>` | Blast map for a file |
| `/plan` | Draft refactor plan |
| `/approve` | Write `.amu/contract.json` |
| `/refresh <path#symbol> [options]` | Compare relationships after an edit; supports `--change`, `--new-symbol`, and `--depth` |
| `/check N` | Check phase N |
| `/done` | Full close-the-loop |
| `/docs [options]` | List doc candidates, `--prepare dN`, or save with `--apply dN --review` |
| `/verify nX` | Run a node's verify path |
| `/answer [options]` | Review unknown answers or explicitly confirm a suggestion |
| `/why nX` | Explain a node (raw JSON) |
| `/feature X` | Look up a feature in the feature map |
| `/skills` | List available skills |
| `/repo` | List workspace repos |
| `/use <name>` | Switch active repo |
| `/find <words>` | Graph search |
| `/open <sym>` | Open a symbol or path |
| `/tree [path]` | Directory tree (depth 2) |
| `/graph <sym>` | Relation graph for a symbol |
| `/back` | Navigation history — go back |
| `/ask <question>` | Ask the AI model (requires `amu key set`) |
| `/key` | Show model key/credential status |
| `/help` | Show this command list |

A bare path or symbol triggers `explore` (opens it). Any other free text routes to the model when a key is set.

## Architecture

```
amu/
├── graph.py      # subprocess wrappers → entire graph impact / diff / capabilities
├── classify.py   # 3-bucket classifier (handles dynamic dispatch / unknown)
├── plan.py       # graphlib.TopologicalSorter → 4-phase plan
├── contract.py   # entity-level diff parser + violation checker
└── cli.py        # Typer entrypoint
tests/
├── test_classify.py   # unknown bucket + safe-fallback tests
├── test_plan.py       # topological phase assignment + cycle recovery
└── test_contract.py   # violation detection + CLI-unavailable fallback
```

## Curveball Compliance (Noon Track 2)

- **Unknown bucket** always emits `unresolved_callsites`, `dynamic_dispatch_sites`, `out_of_coverage`, and `notes` – never silently drops partial analysis.
- **Contract check** gracefully degrades when `entire` CLI is absent (`allow_cli_unavailable=True`).
- **Cycle detection** in `plan.py` surfaces cycles as warnings rather than crashing.
