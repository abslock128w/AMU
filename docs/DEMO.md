# amu · 3-minute demo script (TWC Thugs)

Begin with no graph-relevant source edits. The graph map remains viewable on a dirty source tree, but it cannot be approved as a pre-edit baseline.

1. `amu` — banner and REPL. `/tree`, `/find <words>`, and `/open <file|symbol>` are the quick navigation commands. `/help` groups them by task.
2. `amu map --symbol amu/classify.py#classify_consumers --change signature` → a map of ● sound / ◐ guessed / ○ unknown relationships, each non-sound item carrying a reason and a way to verify it. Resolved callers, callees, type consumers, and data flows can cross file boundaries.
3. `amu plan --targets amu/classify.py#classify_consumers:signature --approve` → four edit phases and an approved contract. Approval stops if the live graph is incomplete, the target is not exact, or source code had already changed before mapping.
4. Make the planned change, then run `amu refresh --symbol amu/classify.py#classify_consumers --change signature`. It compares the live post-edit graph with the exact pre-edit map and reports added/removed semantic edges. It refuses to claim a complete delta if either graph is incomplete or the worktree moved while it was running.
5. `amu check --phase <N>` → verifies the appropriate planned phase. A graph-relevant change without a current matching refresh blocks `check` and `done`; repeat edit → refresh → check for each phase.
6. `amu docs` → review documentation candidates. `amu docs --mode auto` only writes sound, mapped committed-diff candidates that are not shadowed by worktree evidence; all uncertain candidates remain manual. `amu done` is available only after every planned phase is green, then performs sync, sweep, documentation review, memory refresh, and a report.
7. `entire amu doctor` — the same tool through Entire plugin dispatch. For a second repo, use `amu repo add numpy/numpy`, then `amu graph numpy/_core/numeric.py#ones --format mermaid` to inspect resolved cross-file callers and callees.
8. In the REPL the banner reads `○ model off`; type `/key add`, pick a provider (Anthropic key, Gemini API key, or local Ollama), and it flips to `● model on`. Then ask: `what breaks if I rename classify_consumers?` — the assistant reads graph evidence and proposes the next amu command; `amu watch --phase 1` in a second pane shows planned nodes move to `↯` when relevant files drift.

Fallback: `docs/evidence/*` holds recorded outputs.
