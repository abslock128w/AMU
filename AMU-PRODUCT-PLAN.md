# AMU product plan v3: after the agent trial

**Phase 0 outcome, 2026-10-13, commits 988b44e to b05244e.** The gate failed. Of 250 Django
commits, 40 touched production and an existing test, 11 broke one, 5 broke one outside the
lexical top 40. Indirect breakage is about 1 in 50. On those five cases the static packet
found the indirect test in 2 of 5 and plain search in 3 of 5. The reach list has left the
default packet and lives behind `--tests`. The default packet is now: summary, untested
functions, tests expected to fail, consequences, gaps. Section 9 below is what comes next.

Written 2026-10-13 against commit e6598c0. v2 was written before the ten-run agent trial
reported. The trial lost, and it lost for a reason that changes what AMU is. v3 keeps every
lever from v2 and reorders the product around what the trial showed a capable agent cannot
do on its own.

## 0. What the trial proved

Five Click changes that break existing tests, ten agents, same model, same instructions,
15-test budget, half with AMU as a tool.

| Arm | Breaking tests found of 17 | Test runs used |
|---|---:|---:|
| Without AMU | 15 | 29 |
| With AMU | 13 | 45 |

Beside it, the engine's own numbers, so the two are not confused:

| Set | Legacy 68d82c8 | Core | Role |
|---|---:|---:|---|
| Werkzeug, 16 scorable | 5/16 function, 8/16 file | 10/16, 16/16 | development |
| Click holdout 1 | 0/10, 0/10 | 1/10 (ceiling 5), 9/10 | development now |
| requests + itsdangerous holdout 2 | 2/8, 2/8 | 5/8, 7/8 | run once |
| Click holdout 3, unseen | 1/8, 2/8 | 8/8, 8/8 | run once |

The engine is not the problem. The trial is about what to show and when.

Three facts come out of it, and each one is a design constraint from now on:

1. **An agent handed the diff searches the tests for the removed or reworded text and
   wins.** A ranked list of tests that reach the code cannot beat that on a small,
   searchable change, because tests that reach the code outnumber tests that fail. "Run
   these first" is not the product. It is an internal step.
2. **The one test neither arm found sits in another file with no wording in common with
   the diff.** That is indirect breakage, and it is the only place a reachability tool can
   win. The product must be built around that case and measured on it.
3. **Only 5 of 18 Click commits break any existing test.** On the other 13 the right output
   is not a test list. It is "no existing test runs these changed lines", and the agents'
   own search had no answer to that. The trial's own author called it the one output search
   did not replace.

Four more findings from the full report, each with a consequence:

4. **The indirect test was findable and got crowded out.** AMU ranked task 4's third test
   tenth on the unedited tree, but the trial packet did not show it because a consequence
   row took the space. Packet layout is a product defect, not a cosmetic one. Rule: the JSON
   packet carries all 40 ranked candidates; the 2 KB budget applies to the human summary,
   and no row type may displace another.
5. **Coverage ranking is not shown to beat static.** Tuned on holdout 1 it won; on unseen
   holdout 3 it was worse in one of two cases, with breaking tests at ranks 9, 14 and 17
   behind siblings that ran the same lines. Coverage stays as the "no recorded test ran
   these changed lines" gap and as one feature for the learned ranker, never as the default
   order. The M5 Werkzeug check still holds: 44 observed tests above 320 static.
6. **Eleven cases have only new tests.** Six Werkzeug and five Click commits add tests that
   no existing-test recommender can find. That is a quarter of the cases, and it is the
   reason `missing_tests[]` is a first-class output rather than a nicety.
7. **Harness defects were silent.** `git apply` inside a subdirectory exited 0 and changed
   nothing, so a whole execution run reported no outcome changes. A 92 MB coverage file hit a
   guard. Pre-edit recordings were compared against post-edit line numbers. Rule: every
   execution harness step asserts its effect (files differ, tests collected, outcome count
   non-zero where expected) and fails loudly otherwise.

Built after the trial, at commit 5b0a4e9, on the 18 Click commits with execution ground truth:

| Measure | Result |
|---|---|
| `likely_fail` static labels | 13, of which 12 truly fail |
| Breaking tests labelled statically | 12 of 17 |
| Labels on the 13 non-breaking commits | 0 |
| Shadow execution, five trial tasks | 17 of 17 named, 0 wrong, about 1 s each |
| Agents in the trial, for comparison | 15 of 17 without AMU, 13 of 17 with the old packet |

Shadow execution found the task 4 test neither agent arm found. These are development
numbers: the label rules were shaped on these commits, the first version was 40 percent
precise, and on Werkzeug, requests and itsdangerous the label fired once in 31 cases. It is
narrow by design: it speaks when a patch removes something and is silent otherwise.

Everything in v2 that still stands is kept below. What moves: the test list stops being the
headline, exact failure prediction and uncovered-line reporting become the headline, and
AMU absorbs the agent's winning strategy so it is never worse than plain search.

## 1. The adoption thesis, corrected

A developer or agent uses the tool every time only when all four hold:

1. **It is already there.** Runs from the diff they have, inside the tool they use.
2. **It is never worse than what they would do by hand.** The trial shows the hand method
   is lexical search of the test tree. AMU must include that search as a signal, so its
   list is a superset of the agent's own list, ordered better.
3. **It answers a question search cannot.** Which reachable test will actually fail. Which
   changed line nothing exercises. Which test in another file breaks through three hops
   with no shared words.
4. **It is faster than running the suite.** Including the time it costs to read the packet.

## 2. The four outputs, re-ranked by what the trial showed

### 2a. `predicted_failures[]`: this test fails after your change

The headline. Three mechanisms, exact first.

- **Shadow execution.** When execution is allowed, run the candidate tests on the parent
  tree and the patched tree in two subprocesses and diff the outcomes. This is exact, it
  needs no ranking to be right, and it replaces "run these first" with "these fail". Budget:
  up to 150 candidates from the union of reachability and lexical, under 60 s. Label
  `executed`. On a suite that runs in seconds this is "run everything" and that is fine;
  it only earns its place over the whole suite when the suite is slow, which is one more
  reason Phase 0 is a large repository. The candidate cap is a budget, not a ranking claim:
  a cap of 40 missed two of task 2's eleven failures.
- **Assertion matching.** A test asserts a literal that the diff removes or changes. Static.
  Label `assertion`. This is the agent's search strategy, made systematic.
- **Consequence paths.** Changed raise or return reaches an assertion through located hops.
  Label `consequence`. Already built.

### 2b. `untested[]`: these changed lines have no test

For every changed line: reached by a test statically, historically, by observation, or by
nothing. Thirteen of eighteen real commits need this row and nothing else. Sound when it says
something reaches; confirmed by coverage when available, else `static_only`.

### 2c. `indirect[]`: tests that break with no shared wording

The case neither arm found. A test in another file, reaching the change through hops, with
no identifier or literal from the diff in its body. This is the reachability graph's whole
reason to exist, so it gets its own row with the full path, and the holdout script gets a
filter that selects exactly these cases so the number is measured on its own.

### 2d. `tests[]`: run these first

Still emitted, now as the fallback when execution is not allowed, and as the candidate set
for shadow execution. Ranked by the learned combination of every signal in section 3. The
packet labels each row `will_fail`, `reaches_changed_lines` or `reaches_symbol` so an agent
does not run the list top-down. That top-down behaviour is why the AMU arm used 45 runs.

### 2e. `missing_tests[]`: the test you should write

For every `untested` line: sibling-template, Hypothesis skeleton from annotations, then a
model draft. Executed before emission, kept only if it exercises the line under coverage or
fails on the parent and passes on the patch. Never emitted unexecuted.

## 3. The signal stack

Unchanged from v2, with one addition at the top because the trial showed it is the one the
agents actually use.

| Label | Signal | Cost |
|---|---|---|
| `lexical` | Removed and changed tokens, literals, message strings from the diff against every test body, name, parametrize id and assertion | static |
| `direct` | Import, call, re-export, fixture reachability | static, built |
| `transitive` | Five-hop call graph with decay | static, built |
| `historical` | Co-change mining from git log | one git pass |
| `failed_before` | pytest `lastfailed`, CI failure history | read cache |
| `observed` | Coverage contexts from `.coverage`, CI artefacts, Codecov, or `--observe`; a gap signal and a ranker feature, not the default order (holdout 3) | read or opt-in |
| `typed` | Pyright-resolved call targets for attribute calls, protocols, overloads | one run, cached |
| `traced` | `sys.monitoring` call chains from `--observe` | opt-in run |
| `mutated` | Mutants on the changed lines, which tests kill them | opt-in, bounded |
| `learned` | Local ledger of what failed for which symbols | free after `--run` |
| `model` | LLM re-rank of the candidate set with diff and test bodies | opt-in, cached |

Combined by hand weights until there are hundreds of labelled commits. A learned ranker on
a few dozen cases overfits; it is listed for later, not for Phase 1. The holdout script
accumulates labelled commits toward that threshold as a side effect of every run.

## 4. Phases and gates

### Phase 0: the experiment the trial asked for (this week)

Before any product work: one trial on a large repository with changes that break tests the
diff's wording does not point to. This is the kill test for the test-picking half.

- Freeze a holdout with `scripts/freeze_holdout.py` from a large repository (Django,
  pandas, or Home Assistant), filtered to commits where the upstream test that fails on the
  parent tree shares no identifier or literal with the production diff. Run the full suite
  on parent and patch to establish ground truth, with your per-repository yes first.
- Run the same ten-agent design on six of those cases.
- Record indirect recall for both arms.

Gate: if the AMU arm finds indirect breakage the plain-search arm misses on at least half
the cases, `indirect[]` and `tests[]` stay. If not, `tests[]` is dropped from the default
packet and AMU ships as `untested[]` plus `predicted_failures[]` plus `missing_tests[]`.
Either outcome is a product. Neither is a failure of the engine, which is already far ahead
of the old build on every holdout.

### Phase 1: exact over ranked (weeks 1 to 4)

1. `lexical` as a first-class signal, so AMU's candidate set always contains what the agent
   would have found by hand.
2. Assertion matching for `predicted_failures[]`, static.
3. Shadow execution: parent and patch, outcome diff, 60 s budget, candidate set from the
   union of reachability and lexical. `--run` becomes shadow execution with stop-on-first.
4. `untested[]` with `static_only` and coverage-confirmed labels. Spot check 30 lines.
5. Row labels `will_fail`, `reaches_changed_lines`, `reaches_symbol` in the packet, and a
   one-line instruction at the top telling an agent not to run the list top-down. All 40
   candidates in the JSON; no row type displaces another (the task 4 defect).
6. Co-change, `lastfailed`, existing coverage readers, pyright edges, as in v2.

Gate on three fresh holdouts: on commits that break a test, shadow execution names it in 9
of 10 cases within budget. On commits that break nothing, `untested[]` is non-empty and has
zero wrong claims on a hand sample. `indirect[]` is measured separately on the filtered set.

### Phase 2: dynamic signals (weeks 3 to 6)

`--observe` with coverage contexts and a `sys.monitoring` tracer. Bounded mutation on the
changed lines. The local ledger. Missing-test tiers one and two with the execution filter.
As in v2.

Gate: mutation-labelled tests are the failing test in 8 of 10 breaking cases on a fresh
holdout. Missing-test drafts pass the filter on at least half of `untested` lines.

### Phase 3: presence (weeks 5 to 8)

As in v2: no-argument `amu`, `pipx`/`uvx` install with a standard-library core, Claude Code
plugin with a `PostToolUse` hook on Python edits and a `PreToolUse` hook on unscoped pytest,
pre-commit, PR-commenting Action, watch mode under one second, thin VS Code client, legacy
surface deleted.

One change from the trial: the hook injects the packet before the agent decides what to run,
and the packet's first line is the `predicted_failures[]` count and the `untested[]` count,
not the test list. The trial's AMU arm lost runs because the list was the first thing it saw.

Gate: three strangers, five minutes, useful packet on their own repository.

### Phase 4: prove it (weeks 7 to 11)

The 12 to 20 task study, two arms, hidden upstream tests, with the Phase 3 hook and packet
layout. Tasks drawn half from breaking commits and half from non-breaking ones, so the
`untested[]` output is measured as well as the failure prediction. The would-have-caught
ledger. The model tier behind `--model`, kept only if a fresh holdout shows it beats the
hand-weighted order.

Gate: fewer regressions in the AMU arm on hidden tests, and fewer test runs, reported with
sample size. The trial already showed what losing looks like, so the comparison is honest.

### Phase 5: hardening, Phase 6: breadth

As in v2. Monorepo scale, dynamic Python as `gap` rows, unittest and `src/` layouts,
pasteable test ids, Windows, frozen schema v1, weekly fresh-holdout CI that blocks merge.
TypeScript only after Phase 4 passes.

## 5. Standing rules

- Recall first, rank, label. No refusal path. Unknowns are low-confidence rows or `gaps`.
- Never run an upstream repository's tests without a per-repository yes. Shadow execution,
  `--observe`, `--run` and mutation are the developer's call on their own tree.
- Never report accuracy from AMU's own suite or fixtures. Fresh holdouts, frozen by script,
  run once. A number that did not move is reported as not moving. A trial that lost is
  reported as lost, as this one was.
- Never emit a generated test that was not executed. Never emit a predicted failure without
  its label and evidence.
- AMU's candidate set always includes what lexical search would find. It is never worse than
  the hand method.
- Model calls opt-in, cached, off by default unless a holdout shows they help.
- Budgets are tests: over 2 KB for the summary, over 2 s warm, over 10 s cold on Werkzeug
  fails the suite. The JSON carries the full candidate set.
- Every harness step asserts its own effect and fails loudly. No silent zero.
- Dogfood through AMU's own pre-commit hook.

## 6. Risks

| Risk | Signal | Response |
|---|---|---|
| Indirect breakage is rare enough not to matter | Phase 0 holdout filter finds under 1 in 20 commits | Drop `tests[]` from the default packet; ship the other three outputs |
| Shadow execution too slow on big suites | Over 60 s on the candidate set | Shrink the candidate set with the ranker; it is still exact on what it runs |
| Agents ignore the packet | Phase 4 arm uses as many runs as control | Hook timing and packet layout, measured again |
| Hub classes stay unrankable | Phase 1 `tests[]` recall flat | Irrelevant once shadow execution is the headline |
| Generated tests are wrong | Any draft fails the filter | Filter is mandatory |
| Reused holdout | Number without a freeze record | Invalid |
| Hub classes make the top 10 a guess | Hundreds of tests reach one symbol | Shadow execution on the candidate set; pyright edges; hub cases get their own holdout filter |

## 7. What "never codes without it" means, measurably

- Share of commits where AMU ran before tests above 90 percent, from the ledger.
- Predicted failures confirmed by the full suite above 90 percent of the time.
- An `untested[]` row acted on, or a missing-test draft accepted, once a week.
- Edit to first confirmed failure or confirmed "nothing fails, these lines are uncovered"
  under 30 s.

Ten developers, their own repositories, one month.

## 8. First twelve actions

1. Push the branch so e6598c0 is not only local.
2. `scripts/freeze_holdout.py` with the indirect-breakage filter. Freeze six cases from a
   large repository. Ask for the per-repository yes to run its suite on parent and patch.
3. Run the ten-agent design on them. Phase 0 gate decision.
4. Done at 5b0a4e9: `likely_fail` from removed wording, removed package names, removed
   warnings and exceptions.
5. Done at 5b0a4e9: shadow execution under `--run`, 150-candidate cap.
6. Done at 5b0a4e9: untested functions by changed lines and by reach. Still to do: the
   30-line hand spot check against full coverage, and the `static_only` label.
7. Done at 5b0a4e9: one-line summary first, per-row expectation labels.
8. Co-change, `lastfailed`, coverage readers, pyright edges.
9. Hand-weighted combination; three fresh holdouts; Phase 1 gate.
10. `--observe`, mutation, ledger, missing-test tiers.
11. No-argument `amu`, standard-library core, Claude Code plugin with hooks, README cut.
12. The 12 to 20 task study, after budget approval.

## 9. After Phase 0: what AMU is now

The test-picking product is gone from the default. Two things remain with evidence behind
them, and one thing has none yet.

- **Shadow execution** is the only output that beat both trial arms, 17 of 17 on the Click
  tasks. It cannot run on Django because it drives pytest and Django has its own runner.
- **Untested functions** is the output on the 13 of 18 Click and 239 of 250 Django commits
  that break nothing. It has not been judged by anyone yet.
- **Static `likely_fail`** fires in 1 of the 5 Django cases even after the removed-constant
  and inherited-test fixes. It misses a reworded message, a changed stack level, and
  behaviour reached through a subprocess. It is a narrow label and stays narrow.

### Next three pieces, in order

1. **Runner adapter.** Shadow execution over `unittest`, Django's `manage.py test` and
   `runtests.py`, and plain `python -m pytest`. Detect the runner from the tree. Accept a
   `--runner` override. Test ids must be pasteable into that runner.
2. **Django speed.** 16 s cold and 2.7 s warm is over budget. Profile the warm path first;
   the cold path can parallelise parsing. Budget stays 2 s warm.
3. **Rerun the five Django cases with shadow execution on.** These cases are exposed now,
   so this is a debugging run for the adapter, not a claim. The claim needs a fresh freeze.

### The fairness rule for any trial with shadow execution

Shadow execution runs tests. If the AMU arm gets it for free, the comparison is rigged.
Every test shadow execution runs counts against the AMU arm's 15-run budget, or both arms
get the same wall-clock budget instead of a run count. State which in the trial record.

### The untested-lines judgement

Nobody has yet said whether an `untested[]` row changed what they did. Before more engine
work on it: take ten of the non-breaking Django commits, show the row to a reviewer blind to
the commit, and ask whether they would have asked for a test. That is a half-day and it is
the only evidence that output deserves to be the headline.

## 10. After the runner adapter, the Django rerun and the untested scoring (2026-10-14)

Three results landed together, commits 7689306 to ac5ec78, merged to main.

| Result | Number | Status |
|---|---|---|
| Shadow execution on the five Django cases, Django runner | 4 of 5 indirect tests, 13 to 46 s each | debugging run, cases exposed |
| Agents in Phase 0, same cases | 3 of 5 without AMU, 2 of 5 with static packet | trial |
| Warm analysis on Django, 2,923 files | 2.7 s to 1.2 s | budget met |
| First call after an edit | 8.4 s to 2.9 s | over budget |
| Cold | 16 s to 10 s | at budget |
| Static `untested` rows on 102 Django commits | 13% author agreement, later-fix rate not significant | demoted by the rule set beforehand |

### What they mean together

AMU's default packet without execution is now close to empty on most commits: `likely_fail`
fires on 1 in 5 breaking cases, static `untested` is out, and the reach list is out. That is
the honest state and it is correct. The product is shadow execution. Everything static is
candidate selection for it, plus a fallback when execution is not allowed.

The untested scoring found the same root cause as case 4 and as the hub-class problem on
Click: **Django tests this code, but reaches it dynamically.** Descriptors, signal receivers,
template tags, module `__getattr__`, constructors called through class tables, a
`manage.py` subprocess. Static analysis cannot see those edges and never will. A coverage
recording sees every one of them except the subprocess. That is why the `observed` signal
moves from "nice to have" to the next piece of work: it is the only thing that turns
`static_only` rows into rows with evidence, and the only thing that puts dynamically-reached
tests into the candidate set for shadow execution.

### Next four pieces, in order

1. **Make shadow execution the default** when a runner is detected and the tree is the
   developer's own. `amu` with no arguments runs it with a 60 s budget per side and prints
   what flipped, then the gaps. `--static` gives the old packet. The packet's first line is
   the flip count.
2. **Coverage recording as the candidate source.** One recording per repository state,
   per-test contexts, kept under `.amu/index/` keyed by file sha with per-file staleness as
   already built. Record it on demand with `--observe`, or read an existing `.coverage` or
   CI artefact. Use it for three things: candidate selection for shadow execution, the
   coverage-backed `untested` row, and recall on dynamically reached tests. For subprocess
   tests, set `COVERAGE_PROCESS_START` in the recording environment and report what it adds.
   Score it the same way static was scored: author agreement and later-fix rate on the same
   102 commits, run once.
3. **The claim run.** Fetch Django history deeper than 400 commits. Freeze a fresh set by
   the Phase 0 script, indirect cases plus an equal number of breaking-but-lexical cases, no
   case from the exposed five. Ten agents, same model, same wall-clock budget per arm,
   shadow execution's runs inside the AMU arm's budget. The record states the budget and
   the rule before the first agent starts. This is the first number that can be called a
   claim about the product that exists now.
4. **Edited-tree speed.** 2.9 s after an edit on Django, target 2 s. Relink is already
   bounded to changed files; profile what remains.

Presence (Phase 3 of v3) starts after the claim run reports, whichever way it goes, because
the no-argument command and the Claude Code hook are the same work either way.

### A rule added after the merge

The builder merged the pull request into main when the decision said not to, under a CI
auto-fix event it read as standing authorization. The merge was safe: all checks passed, a
merge commit preserved cited hashes. Rule from here: a CI auto-fix authorization covers
fixing checks and conflicts on the branch. Merging, deleting branches and changing remotes
need the owner's words in chat.

## 11. After three trials: what was actually measured (2026-10-14, commit 8262822)

| Trial | Design | Result |
|---|---|---|
| Click smoke, 5 tasks | agent told breakage exists, given the diff, static packet | AMU lost, 13 vs 15 of 17 |
| Django Phase 0, 5 indirect cases | same, static packet | AMU lost, 2 vs 3 of 5 |
| Django claim run, 6 tasks | same, shadow execution available, wall-clock budget | tie, 15 vs 15 of 15; 0 of 6 AMU agents ran the tool |

Engine state: shadow execution with the coverage recording found all five exposed indirect
tests including the subprocess case; alone on the six claim tasks it was exactly right on
five and gave one false all-clear, since fixed. Warm 1.1 s, after-edit 1.5 s on Django.
Coverage-backed untested rows scored 23 percent author agreement and no later-fix signal,
so `untested` is out of the default packet entirely.

**All three trials measured the same thing, and it is not the product moment.** Each agent
was told that existing tests break and was handed the diff. Finding the failing test under
those conditions is a search task, and a capable agent searches callers and runs modules in
seconds. It never needed a tool, so it never ran one. The moment AMU exists for is the one
where the agent does not know anything broke: it has just implemented a change, believes it
is done, and is about to run whatever tests it thinks of. No trial has measured that yet.

Three untested hypotheses remain, in order of how cheaply they can be killed:

1. **Unasked.** AMU runs from a hook after every Python edit and the flip count is in front
   of the agent before it decides what to run. The claim-run agents never chose the tool;
   a hook removes the choice.
2. **Blind tasks.** The agent is given the upstream issue or commit message, not the diff,
   and asked to implement the change. The measure is regressions on the hidden upstream
   tests at the moment the agent declares done, and whether it ran the breaking test before
   declaring. This is the original M7 design and it has never been run.
3. **Slow suites.** Where the relevant modules take minutes, shadow-executing 150
   candidates in 60 s is a real saving. Django modules ran in seconds, so this was never
   exercised.

The next trial combines 1 and 2. Section 12 is the brief for it.
