# AMU product plan v2: use everything

Written 2026-10-13 against commit b3747c8 plus the uncommitted line-aware coverage work.
v1 used only what the repo already had. v2 uses every signal and every technique that can
move the four adoption conditions, ordered by evidence per hour of work. Every phase still
has a gate that can fail, and no number counts unless it comes from a holdout frozen by a
script and run once.

## 0. Where AMU stands

| Evidence | Number | Meaning |
|---|---|---|
| Werkzeug dev set, 16 cases | 10/16 function, 16/16 file | Development set, exposed |
| Click holdout 1, 10 cases | 1/10 function (ceiling 5), 9/10 file | Hub classes defeat static ranking |
| requests + itsdangerous holdout 2, 8 cases | 5/8 function, 7/8 file | Only untouched number |
| Packet, latency | under 2 KB, under 1 s warm | Budgets met |
| M5 Werkzeug routing coverage | 44 observed tests outrank 320 static | Plumbing works |

Not yet real: any evidence a person or agent made fewer mistakes with AMU. That is Phase 4.

Debt: 55 commands, 3,578-line `cli.py`, 27.5k lines, 1,645 refusal-pinning tests, a
three-part install, a README that sells ceremony. The core under `amu/core/` is under 5k
lines and does everything that matters.

## 1. The adoption thesis

A developer uses a tool every time only when all four hold:

1. **It is already there.** Runs from the diff they have, inside the tool they use.
2. **It is right often enough to trust.** Failing test in the top ten four times in five.
   An `untested` claim that is never wrong on the static side.
3. **It is faster than the alternative.** AMU plus its ten tests beats the whole suite.
4. **It shows them something they did not know.** A test they would not have run, a line
   nothing exercises, an exception that reaches another module, a test that would fail
   before they run anything.

The fourth condition is where "god tier" lives. v1 stopped at recommending tests. v2 goes
to predicting which test fails, saying why, and writing the test that is missing.

## 2. The signal stack

Every signal is a ranked, labelled evidence source. None is a gate. The packet shows the
best label for each row. Signals in order of cost, cheapest first.

| Label | Signal | Cost | Status |
|---|---|---|---|
| `direct` | Import, call, re-export, fixture reachability | static, built | done |
| `transitive` | Five-hop call graph with decay, nested defs free | static, built | done |
| `lexical` | Changed-line words against test names, ids, literals, assertion strings | static, partly built | widen |
| `historical` | Co-change mining from git log: which tests changed with which symbols | one git pass | new |
| `failed_before` | pytest `lastfailed` cache, CI failure history per test | read cache | new |
| `observed` | Coverage contexts from existing `.coverage`, CI artefacts, or `--observe` | read or opt-in run | built, extend sources |
| `typed` | Pyright/mypy call resolution for attribute calls, protocols, overloads | one pyright run, cached | new |
| `traced` | `sys.monitoring` call trace from `--observe`, exact call chains per test | opt-in run | new |
| `mutated` | Mutation of the changed lines: which tests kill the mutant | opt-in run, bounded | new |
| `learned` | Local ledger of which recommended tests failed on which symbols | free after `--run` | new |
| `model` | LLM re-rank of the top 40 with the diff and test bodies, cached by hash | one API call, opt-in | new |

Rank by a learned combination, not hand weights. Train a small gradient-boosted ranker
on the development sets with the holdout script providing features and labels; evaluate on
fresh holdouts only. Fall back to the hand-weighted sum when the model file is absent.

## 3. The four products inside the one command

### 3a. `tests[]`: run these first

What exists today, plus the signal stack. Target on fresh holdouts: function recall@10
above 70 percent, file recall@10 above 90 percent.

### 3b. `untested[]`: these changed lines have no test

For every changed line: reached statically, historically, by observation, or by nothing.
"Nothing" is the row that makes a developer stop. Static reachability is sound for this
purpose when it says "something reaches it"; when it says nothing reaches it, confirm with
coverage if available, else label `static_only`.

### 3c. `predicted_failures[]`: this test will fail, here is why

The step from recommender to oracle. Three mechanisms, each labelled:

- **Consequence paths**, already built: changed raise or return reaches a caller that a
  test asserts on. Label `consequence`.
- **Assertion matching**: a test asserts a literal that the diff removes or changes
  (status code, message, default value, key name). Pure static diff-versus-assertion. Label
  `assertion`.
- **Shadow execution**: with `--observe`, run the top ten tests on the parent tree and the
  patched tree in two subprocesses, diff outcomes. Exact. Label `executed`. This is what
  `--run` does anyway; it just does it on both sides so the packet can say "fails after
  your change, passed before".

### 3d. `missing_tests[]`: the test you should write

For every `untested` line or new function with no reaching test: generate a candidate
test. Three tiers, in order:

- **Template from siblings**: find the nearest test of a sibling symbol in the same module
  and rewrite the target name and literals. Static, no model.
- **Property-based**: for a function with type annotations, emit a Hypothesis test skeleton
  with strategies from the types. Static, no model.
- **Model-drafted**: with the diff, the sibling tests and the consequence path as context,
  ask the model for one test. Run it on both trees. Keep it only if it passes on the patched
  tree, fails on the parent tree, or exercises the untested line under coverage. Label
  `model`, show the evidence. Never emit a draft that was not executed.

The last tier is the moment where a developer says the tool wrote the test they were about
to skip. It is also the easiest place to ship garbage, so the execution filter is mandatory.

## 4. Phases and gates

### Phase 0: close the cycle (this week)

- Record the Click execution result: static versus coverage-assisted on outcome-changing tests.
- Run holdout 3 once with line-aware coverage. Record. Commit everything.
- Six-task M7 smoke trial to debug the harness.

Gate: four tables recorded. Holdout 3 below 5/8 means root-cause first.

### Phase 1: the signal stack, static half (weeks 1 to 4)

In order, each measured on the dev sets, then all together on three fresh holdouts.

1. `scripts/freeze_holdout.py`: N recent qualifying commits from a named repo after a
   date, hash-pinned, no analyzer run before selection. Every number from here cites it.
2. Co-change mining, `historical`. Weight by inverse commit size. Store under
   `.amu/index/cochange.json` keyed by HEAD.
3. `lastfailed` and CI history, `failed_before`. GitHub Actions log parsing for the
   repository's last 50 runs when a token is present, else the local cache only.
4. Existing coverage sources, `observed`: `.coverage` with contexts, `coverage.xml`,
   Codecov and Coveralls API when configured. Per-file staleness as today.
5. Pyright-backed resolution, `typed`: run pyright once in library mode, cache the
   resolved call targets per file sha. This is what turns attribute calls on typed objects
   into edges, which the AST graph cannot do, and it is what makes hub classes smaller.
6. Lexical widening: parametrize ids, assertion strings, fixture names, docstrings.
7. `untested[]` with `static_only` and confirmed labels.
8. Assertion matching for `predicted_failures[]`.
9. Learned ranker: features from the signals, labels from the dev-set truth. Fresh
   holdouts only for evaluation.

Pool: Flask, Jinja, httpx, attrs, rich, pydantic, a Django app, a monorepo. Werkzeug,
Click, requests, itsdangerous are development sets from here on.

Gate: three fresh holdouts, 10 cases each, function recall@10 at or above 7/10 on each,
file at or above 9/10, under 2 KB, under 2 s warm, zero wrong `untested` claims on a
30-line hand sample against full coverage.

### Phase 2: the signal stack, dynamic half (weeks 3 to 6)

All opt-in, all on the developer's own tree.

1. `--observe`: run the top 40 under per-test coverage contexts and a `sys.monitoring`
   call tracer, re-rank, record `traced` chains.
2. `--run`: execute recommended tests in rank order on the patched tree, stop on first
   failure, print the consequence path beside the failure.
3. Shadow execution for `predicted_failures[]`: parent and patched tree, outcome diff.
4. Bounded mutation on the changed lines: mutmut-style operators on only those lines,
   top 20 tests, 60-second budget. Tests that kill mutants rank first and are labelled
   `mutated`. This is the strongest possible "this test actually guards your change".
5. `missing_tests[]` tiers one and two: sibling template, Hypothesis skeleton. Executed
   under coverage before emission.
6. The local ledger, `learned`: every `--run` records (changed symbols, recommended test,
   outcome). Feeds the ranker. Private, never leaves the machine.

Gate: on a fresh holdout with `--observe`, function recall@10 above 8/10. Shadow execution
names the upstream commit's failing test in at least 7/10 cases where the commit's own test
fails on the parent tree.

### Phase 3: presence everywhere (weeks 5 to 8)

1. `amu` with no arguments: working tree, staged, or `HEAD~1`. Human output on a TTY, JSON
   when piped. Flags: `--json`, `--full`, `--observe`, `--run`, `--model`, `--write-tests`.
2. `pipx install amu` or `uvx amu`, standard library only on the core path. Entire plugin,
   pyright, coverage, hypothesis, anthropic all optional extras that unlock labels.
3. Claude Code plugin: one MCP tool `before_tests`; a `PostToolUse` hook on Python edits
   that injects the packet; a `PreToolUse` hook on unscoped `pytest` calls that prefixes the
   selection; a `Stop` hook that refuses nothing but prints `untested[]` and
   `predicted_failures[]`. Same for Cursor rules, Codex skills, Aider.
4. Pre-commit hook. GitHub Action that comments once per PR with tests to run, untested
   lines, predicted failures and the missing-test drafts, using the workflow's own coverage
   artefact. Watch mode: `amu watch` re-runs on save, under one second, in the terminal
   beside the editor.
5. VS Code extension: gutter marks for `untested` lines, a code lens on changed functions
   with "run 7 tests", inline predicted failure. Thin client over `amu --json`.
6. Delete the legacy surface: `amu legacy` for one release, then gone. Under 8k lines,
   under 400 tests, every test pins a developer-visible behaviour. README leads with the
   sentence and a 20-line transcript.

Gate: three strangers install and get a useful packet on their own repo in five minutes.
The watch loop is under one second on Werkzeug.

### Phase 4: prove it changes outcomes (weeks 7 to 11)

1. Agent study, 12 to 20 tasks, two arms, two trials, hidden upstream tests, AMU as one
   MCP tool with hooks. Record solved, regressions, tests chosen, time, tokens. Needs budget
   approval.
2. Would-have-caught ledger: after every full CI run, which failing tests were in the top
   ten, which `untested` lines later got a bug fix, which predicted failures were real.
   The developer sees "contained the failing test in 23 of your last 27 runs".
3. Model re-rank and model-drafted tests, `--model`, behind an explicit flag with the
   execution filter. Measure on a fresh holdout whether the model tier beats the learned
   ranker. If it does not, it stays off by default.

Gate: fewer regressions in the AMU arm on hidden tests, reported with sample size. If not,
root-cause which tasks ignored the packet and fix the hook timing before anything else.

### Phase 5: hardening (weeks 10 to 14)

- Scale: 50k-file monorepo, cold under 30 s, warm under 2 s, parallel parse, shareable
  committed index cache.
- Dynamic Python: `getattr`, registries, `importlib`, signal frameworks, pytest plugins.
  Each a `gap` row with the exact site. Pyright and `traced` chains close most of them.
- Frameworks: unittest, doctest, `src/` layouts, namespace packages, Django test runner.
- Test ids: every printed id pasteable into pytest and selects exactly one test, checked on
  every holdout run.
- Platforms: Windows, CRLF, non-UTF-8, submodules, shallow clones.
- Schema v1 frozen with `schema_version`, one-page spec, major version to break.
- Weekly fresh-holdout CI on eight repositories; a drop blocks merge.

### Phase 6: breadth (quarter 2)

TypeScript with the same stack: import and call graph from the TS compiler API, co-change,
`c8` coverage, `vitest`/`jest` ids, mutation via Stryker. Then Go. Only after the Phase 4
gate passes in Python.

## 5. Standing rules

- Recall first, rank, label. No refusal path, ever. Unknowns are low-confidence rows or `gaps`.
- Never run an upstream repository's tests without a per-repository yes. `--observe`,
  `--run`, mutation and shadow execution are the developer's call on their own tree.
- Never report accuracy from AMU's own suite or fixtures. Holdouts frozen by the script,
  run once. A number that did not move is reported as not moving.
- Never emit a generated test that was not executed. Never emit a predicted failure without
  its label and evidence.
- Model calls are opt-in, cached by content hash, and the packet works without them.
- Budgets are tests: over 2 KB, over 2 s warm, over 10 s cold on Werkzeug fails the suite.
- Dogfood: AMU runs on every AMU commit through its own pre-commit hook.
- Every milestone report: table before and after, files changed, tried and rejected, what
  executed upstream code.

## 6. Risks

| Risk | Signal | Response |
|---|---|---|
| Hub classes still unrankable statically | Phase 1 gate misses | Pyright edges and co-change are primary; static is recall only |
| Co-change noisy on squash merges | One commit touches 40 tests | Inverse size weight, low confidence |
| Nobody runs `--observe` | Phase 3 watch sessions skip it | Lean on CI artefacts, Codecov, GitHub Action |
| Mutation too slow | Over 60 s on a small repo | Cap operators and tests; it is a label, not a requirement |
| Generated tests are wrong | Any emitted draft fails the execution filter | The filter is mandatory; drafts never bypass it |
| Model re-rank hurts | Fresh holdout worse with `--model` | Off by default, documented |
| Agent study shows nothing | Phase 4 gate | Fix hook timing; the packet must arrive before the agent decides what to run |
| Scope creep to ceremony | A PR adds a non-`before-tests` command | Reject |
| Reused holdout | Number without a freeze record | Invalid |

## 7. What "never codes without it" means, measurably

- Share of commits where AMU ran before tests, from the ledger, above 90 percent.
- Would-have-caught rate above 80 percent.
- Edit to first relevant test result under 30 s.
- One `untested`, predicted failure or accepted missing-test draft acted on per week.

Ten developers, their own repositories, one month. Then the title is true.

## 8. First fifteen actions

1. Record the Click execution table.
2. Run holdout 3 once. Commit everything.
3. `scripts/freeze_holdout.py`; freeze Flask, Jinja, httpx.
4. Co-change mining, `historical`.
5. `lastfailed` and `.coverage` readers, `failed_before` and `observed`.
6. Pyright-backed call resolution, `typed`, cached per file sha.
7. `untested[]` with spot check.
8. Assertion matching, `predicted_failures[]`.
9. Learned ranker over the signals. Run the three fresh holdouts once. Phase 1 gate.
10. `--observe` with coverage contexts and `sys.monitoring` trace.
11. `--run` and shadow execution.
12. Bounded mutation on changed lines.
13. Sibling-template and Hypothesis missing-test tiers with the execution filter.
14. `amu` no-arg, argparse core, optional extras, Claude Code plugin with hooks, README cut.
15. M7 smoke trial, then budget request for the full study.
