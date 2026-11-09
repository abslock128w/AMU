# How AMU is measured

This page says how every number in `evaluation/` is produced and labelled. Section 1 is the run protocol; sections 2–5
are the labeling rubric, versioned; sections 6 and 7 say how far the labels and the runs can be trusted. Every
labels.json entry carries a `rubric` field, and every results table says its rubric version under its heading.
Paths are relative to `evaluation/` unless they start with `evaluation/`.

## 1. Run protocol

- **Frozen engine.** Before a measurement, the engine is checked out by commit into its own folder (`engine-<sha>`) and
  runs from there with `PYTHONPATH`; no engine code changes while a run is live. Each report names the commit.
- **The Action's settings.** Python 3.14 locally (3.12 on the live forks, the Action's default), a 300 s test budget
  per side, a 1,200 s wall cap, model-written probes off (`--generate-dollars 0`).
- **Sets.** The 47 set: `performance-v042/freeze.json` (Django 17, sympy 20, Flask 10), replayed locally with
  `generalize/run47.py`. Django and sympy load the suite recording a nightly `amu-record.yml` would have made: the last
  commit before 03:23 UTC on the merge base's day, recorded in 4 shards (`record47.py`). Live pilots: 15 merged pull
  requests per repository replayed on a fork (`replay.py`): base = upstream merge base plus one commit adding the
  workflows, head = the pull request's own head. Nothing is opened or run on the upstream projects.
- **Raise measure** (`generalize/raise_missed.py`): for each hunk, the first changed statement raises while a test
  runs; the whole suite runs; a test that fails with the marker is a catcher; a catcher not among AMU's candidates is
  missed. A lower bound on what AMU should have run.
- **Approval to run a suite** is per project: Django, sympy, Flask, Click, Werkzeug, attrs, httpx, jinja, requests.
- **Labels** are written by hand after the run, against the posted comment and the pull request's diff, with the
  rubric version in force (below). A label that depends on a run fact is checked by rerunning it.

## 2. Rubric v3 (current: the rules applied for the v0.7.0 labels and later)

Inputs a labeler uses: the posted comment (first line, body, collapsed sections), the pull request's title,
description and diff, and its own tests. Where a label depends on a mechanical fact (a test failing on the base, a
line running in a test), the fact is rerun by hand (precedent: `performance-v042/REPORT.md` "Labels": "Mechanical
checks … were rerun by hand where a label depended on them (Flask #5928)"; v3: `generalize/v070-47/labels.json`
flask#6133, sympy#30741, django#22095 each state a run fact checked against the comment).

### 2.1 `first_line_correct` (hand-labelled, boolean)

The first line is the bold sentence that opens the comment ("**AMU: 0 flips** at `sha`, …"). Judge every clause in it,
not only its category. The line is **right** only when every clause is true of this pull request and none says
something a reader would take to be false. It is **wrong** when any clause below applies.

Header clause:

- **R1. No report = wrong.** "**AMU: no flip count** … amu exited with status 1 and no report: …" (a crash) is wrong,
  whatever the cause. Examples: django#22204, sympy#30776 (`generalize/v070-47/labels.json`: "Regressed: no report.
  RecursionError …").
- **R2. Category headers are right when the whole diff is of that kind.**
  - "docstring or comment changes only": right when every changed production line is a docstring, comment or layout
    (django#22046, sympy#30838, attrs 1625, httpx 3426, 3651 — httpx 3651 is a formatter-only change).
  - "type annotations only": right when the only code hunk changes an annotation (sympy#30825).
  - "adds new code only; N tests ran it": right when nothing that existed changes behaviour (sympy#29763: new
    `_print_conjugate` where printing raised before). It was wrong at v1 when a new method is found by name dispatch
    and changes existing output (sympy#29693, `performance-v042/labels.json`); at v3 the line for 29693 names the own
    test failing on the base and is right.
  - "every changed hunk that existed before ran on both sides; N add new code with no existing callers": right for a
    new function with no callers (attrs 1571, `pilot/attrs/v070/labels.json`).

Test-count clause:

- **R3. "ran all N tests" is wrong when a large part of the suite gave no outcome.** jinja 2099: "ran all 279 tests"
  while 400 of 679 tests gave no outcome → wrong (`pilot/jinja/labels.json`, `unrunnable` 400). A few unrunnable tests
  do not make it wrong: jinja PRs with 2–9 unrunnable, attrs 1469 with 1 (`pilot/attrs/v070/labels.json`) are right.
  Where the line between the two lies is not stated (inferred: hundreds versus a handful).
- "among N tests run" (selection mode) is not itself judged; no label turns on it.

Hunk-state clauses (one per state the line can print). Each listed hunk must be in the state the line gives it:

- **R4. "every changed hunk ran on both sides, in up to N tests"**: right when every code hunk ran in tests on both
  sides. A hunk counts as run when any line in it ran; a guarded branch inside a hunk that ran need not be named
  (django#22186 at v0.6.0: Windows line inside a hunk that ran; "The label stays 'right', as before",
  `generalize/REPORT.md`; v3 kept that label as `prior_first_line_correct: true`).
- **R5. "(at import)"** is right when the changed lines run when the module loads and nothing more needs a caller:
  version strings, `__all__` entries, module-level data, `__slots__`, imports (jinja 2066, 2077; httpx 3445, 3699;
  django#22187; sympy#30809; jinja 1880). It is **wrong when a hunk is a function body that ran only in the pull
  request's own tests**: the line must then say "ran only in the pull request's own tests" (flask#6133: "The new
  method's body ran only in the pull request's own test, so it should read 'ran only in the pull request's own
  tests'"). It is also wrong when the code under the def cannot run here at all (attrs 1606 at v0.6.0, def line ran at
  import but `add_replace` runs only on 3.13+; `pilot/attrs/v060/labels.json`).
- **R6. "in up to N tests" must count tests that ran the changed lines, not the def line.** flask#6133 at v0.6.0
  ("in up to 342 tests") "was labelled right but was not accurate either" (`generalize/v070-47/labels.json`); under
  v3 it counts as wrong (the "41 of 47" re-judgment).
- **R7. "ran only in the pull request's own tests: `path:line`"**: right when no other test reaches that hunk
  (flask#6096, flask#6168, django#22179, attrs 1529, 1603).
- **R8. "N of M changed hunks ran in no test: …"**: right when each named hunk is code that ran in no test of a run
  that finished. Wrong when:
  - **a named hunk ran in some test** (attrs 1454 at v0.6.0: a `nonlocal` line in a function nearly every test runs);
  - **the run was cut off** before every candidate ran: a cut run can say "not recorded", not "ran in no test"
    (sympy#30741: "after a head run cut at 640 of 13,207 candidates … test_Matrix_berkowitz_charpoly calls
    berkowitz_charpoly on the head"; `generalize/REPORT-v070.md` "A cut-off run cannot say 'ran in no test'");
  - **a named hunk is a docstring or comment edit read as code** where the code around it ran (flask#5812: "includes
    ctx.py:235/242/251, where a docstring or comment edit inside a block reads as code").
  
  Not wrong when the named hunks are true but low value: type-check examples (attrs 1454, 1592 at v3), an
  annotation on an uncalled method, a `TYPE_CHECKING` alias, an overload stub (jinja 2096: "true but type-only … should
  read 'type annotations only'" — labelled right). Inferred distinction: a non-code hunk listed under "ran in no test"
  makes the line wrong when the code around it did run (5812); it stays right when the code around it also ran in no
  test (2096). Ordering is not judged: leading with type-check examples ahead of evidence stays right (attrs 1592).
  A file outside the package counted as production code (jinja 2099 `scripts/…`) is recorded as a defect; 2099 is wrong
  on R3 and R8 anyway, so whether this alone makes a line wrong has no example.
- **R9. Gate sentences, "N of M changed hunks not executable on <Python X / platform> here (needs <guard>)"**: right
  when the named line cannot run in this job (django#22158 needs PY315; django#22186 needs `os.name == 'nt'`;
  flask#5928 `else` of `sys.version_info >= (3, 11)`; attrs 1507, 1600, 1606 on 3.12). **Wrong when the line itself
  runs and only one operand of an expression on it is gated** (django#22095: `_makedirs = os.makedirs if PY315 else
  makedirs` "ran in 151 tests … Only one operand is gated; the line itself runs"). A printing slip that does not change
  the claim is not wrong (django#22158: the phrase is printed twice; labelled right).

Evidence clause:

- **R10. "N tests of the pull request's own fail on the merge base and pass on the head"** must be true for each test
  counted. In v3 no line was judged wrong on this clause. Earlier examples that set the rule: flask#5928 at v0.4.2
  (13 of 14 were cascade failures; adds noise, line right — see §3), django#22176 (2 of 3 fail only because
  `trim_value` is new; adds noise, line right in all versions).

Behaviour clause:

- **R11. "N behaviour differences on the same input"** makes the line wrong when the rows it announces are false
  (django#22026 v3: 2 rows, both false: "The first line still announces 2 behaviour differences"; jinja 1664: 121
  false rows). Real rows, however many, keep it right (flask#5812 366 real: wrong only on R8; attrs 1509 650 real:
  right). No v3 example has a mix of real and false rows.

### 2.2 `adds` (hand-labelled: `something` / `nothing` / `noise`)

Stated rule (`performance-v042/REPORT.md`, "Labels"): "'Adds something' means something a reviewer cannot read from
the diff and description." Applied in v3 as:

- **`something`** — the comment shows at least one of:
  1. **A bug fix's own test failing on the merge base and passing on the head**, i.e. the test reproduces the bug,
     which project CI never shows (`performance-v042/REPORT.md` "What the reviewer already had": 15 of 15 "no").
     Examples: django#22102, #22137, #22154, #22169, #22179, #22189; flask#5917, #6096; jinja 1665, 1706, 1762, 1852,
     2062; attrs 1513, 1593; httpx 3412.
  2. **A real behaviour row the description does not make obvious**: flask#5898 (302 → 303, "No Flask test checks the
     default code, so this is the only signal of the change"); sympy#30803 at v1 (ZZ path changes, description says
     EXRAW only).
  3. **A hunk no test reaches that the diff does not show as unreached**: httpx 3571 ("a reviewer cannot see from the
     diff that no test reaches the fix"); flask#5818 v3 names `app.py:79` (also has evidence).
  4. **A hunk named as not executable on this job's Python** (attrs 1507, 1600, 1606 at v3 and v2 — "A reviewer learns
     the 3.14 branch was not exercised"). But see the inconsistency in §5: django#22158 and #22186 name the same kind
     of hunk and are `nothing`.
- **`nothing`** — the comment is right (or wrong only by omission) and says no more than the diff, description or a
  green CI run:
  - own tests that fail on the base **for want of a new feature** (attrs 1529, 1541, 1592; django#21855);
  - own tests whose base failure **is evident from the diff** (django#22095, #22171, sympy#30147, #30827, #30851);
  - real, intended rows **the description or diff already states** (flask#5812: "as the PR describes"; httpx 3519
    typo row "the diff says as much"; sympy#30741 at v1/v2: "the PR documents exactly that");
  - platform-gated code a reviewer knows does not run here (django#22204 at v1: "a reviewer knows Oracle-only code does
    not run here");
  - moves, version bumps, docstring-only, annotation-only (httpx 3387, 3389, 3445; jinja 2066, 2077).
- **`noise`** — the comment asserts something false that a reviewer would act on, or buries the true part:
  - **false behaviour rows announced** (django#22026; jinja 1664);
  - **false evidence tests** (django#22176: 2 of 3 "evidence" tests fail only because the code is new);
  - **a wrong count of tests run** (jinja 2099, "ran all 279 tests" with 400 without outcome).
  
  A wrong first line does **not** by itself make the comment noise in v3: django#22095, #22204, flask#5812, #6133 and
  sympy#30776 are wrong and `nothing`. sympy#30741 is wrong and was moved `nothing` → `noise`; its note gives no reason
  (inferred: the false "ran in no test" plus 179 rows read only through equal previews). See §5.

### 2.3 Behaviour rows: `rows`, `real`, `false` (47 set) / `real_rows`, `false_rows` (pilots)

- `rows` (47 set only): the number of rows the comment lists ("N behaviour differences on the same input"). Measured
  (copied from the comment / `data.json` `rows`).
- `real` / `real_rows`, `false` / `false_rows`: hand-labelled counts; `real + false = rows`.
- Each row (or each group of rows on one function) is judged against the pull request's diff ("Each was checked
  against its pull request's diff", `generalize/REPORT-v070.md`, "Numbers"):
  - **real** — the difference follows from what the PR changes in that function: the result type, value or
    error message the diff changes (flask#5812 `request_context()` returns `AppContext`; django#22154 queryset no
    longer pins `_db`; django#22169 XOR join promotion; sympy#30841 `FiniteSet{0}` ↔ `EmptySet`; attrs 1509 new
    `alias_is_default` slot; httpx 3519 error-message typo; jinja 1663 `TemplateNotFound` names the search path).
    "Real" rows are also labelled **intended** when the PR describes or tests that change. Every real row in v3 is
    intended; the fields do not record intended separately in v2/v3 (they did in v1, see §3.2).
  - **false** — the difference comes from how the two sides were run, not from the change: a value derived from each
    side's tree (`_django_version`, path parts), a lazily filled hash cache (`_mhash`), closure state missing from the
    input digest (jinja 1664), objects built by the old code (v1 Flask cases). The cause is written in the note.
  - **untraced** — a row whose 300-character previews are equal on both sides and that was not traced is **counted
    false** when the PR does not change what that function returns (django#22026 `ModelDetailView.get_context_data`:
    "not traced, counted false as in v0.6.0"; `generalize/REPORT.md`: "The PR doesn't change what that function
    returns, so it is counted false"). **Exception applied in v3:** sympy#30741's 179 rows with equal previews were
    "counted real (the PR changes charpoly's default symbol and alg_field_from_poly), not traced row by row". So the
    working rule is: untraced = false, unless the PR changes that function's result, in which case untraced rows were
    counted real. Inferred; flag for a decision (§5).
- Not judged in the labels: rows dropped as unstable or excluded base-vs-base never reach the comment; they are
  reported in notes only (sympy#30803 "677 differing calls were still dropped as unstable").

### 2.4 Flips (no field; no example in v1–v3)

No labels.json has a flips field, and every `data.json` in `generalize/` records `flips: 0` (the v0.7.0 pilot `data.json` files have
`null`). Rules on record:

- A **flip** is a test that passes on the merge base and fails on the head. Tests the PR edited, run in their old
  form, are listed as "updated" and never counted (`performance-v042/REPORT.md`, "Results"; `STATUS.md` §1).
- A counted flip is **false** when the fresh-process rerun on the merge base sees it fail there too
  (`retrospective/PROTOCOL.md`: "A flip it sees fail there too is a false flip"); `STATUS.md` adds "The fresh-job
  rerun and the maintainer's own reading decide which are real." Real = not false under that rerun.
- Flips have not been hand-labelled since the retrospective (0 false of 30, all edited-test flips,
  `retrospective/REPORT.md`, "The five numbers"). A v3 rubric for flips is therefore untested.

### 2.5 `raise_catchers`, `raise_missed` (measured by script, not labelled)

Measured by `generalize/raise_missed.py`: each hunk's first changed statement on the head raises only while a test
runs; the whole suite runs; a test failing with the marker is a catcher; `raise_missed` = catchers not among AMU's
candidates. Module- and class-level statements are not injected. A test that times out is not a catcher (httpx
report), so catchers are a lower bound. `null` means not measured: not re-run at v0.7.0 on attrs and httpx (whole suite
ran), not injected on jinja 2093 and 2096 (raise placed inside a multi-line expression).

### 2.6 `excluded_fields` (measured)

`generalize/v070-47/labels.json` only: number of record fields excluded because they differed between two runs of the
merge base (one under another hash seed). Copied from `data.json`. `null` for the two crashes (22204, 30776). Pilots
have no field; exclusions appear in notes (httpx 3519: "3 fields excluded").

### 2.7 Every other field

| Field | Files | Hand or measured |
|---|---|---|
| `repo` / `number` | `repo`: all except the three v3 pilot files (keyed by bare number); `number`: all | identifier |
| `note` | all | hand: the reason for the labels; v3 notes start "Same as v0.6.0", "Fixed", "Regressed", "Better" or "Now …" |
| `first` | `generalize/v070-47`, `pilot/*/v070`, `pilot/jinja` | copied verbatim from the posted first line |
| `prior_first_line_correct` | `generalize/v060-47` (= v1 label), `generalize/v070-47` (= v2 label, **not** the v3 re-judgment: 5812 and 6133 stay `true`) | copied hand label of the previous version |
| `prior_adds`, `prior_real`, `prior_false` | `generalize/v070-47` | copied v2 hand labels |
| `engine` | `pilot/attrs/v060` | constant `v0.6.0` |
| `rows` | v1 files: string (`"-"`, `"rows 77, real 17, intended 17"`, `"rows 0 (missed)"`, `"rows 0 (71 calls compared)"`); `generalize/*`: integer | v1: hand-written summary; v2/v3 47 set: measured count |
| `mode` | pilots v3 | measured (`all of the suite` on all 45) |
| `ran`, `unrunnable` | pilots v3 | measured from `report.json` (`executed.unrunnable`) |
| `fork_pr`, `job_s`, `open_to_comment_s` | pilots v3 | measured from the live runs (`data.json`) |
| `real_rows`, `false_rows` | pilots v3 | hand (same rule as `real`/`false`) |

---

## 3. Rubric v2 and v1: differences from v3 only

### 3.1 v2 (v0.6.0: `generalize/v060-47/labels.json`, `pilot/attrs/v060/labels.json`, `pilot/httpx/labels.json`)

Same rules as v3 except:

1. **A non-code hunk listed as code under "ran in no test" did not make the line wrong.** flask#5812 at v0.6.0:
   "4 of 67 changed hunks ran in no test: `src/flask/ctx.py:191`, `src/flask/ctx.py:235`, `src/flask/ctx.py:242` and 1
   more" (`generalize/v060-47/data.json`) → `first_line_correct: true` (`generalize/v060-47/labels.json`). v3 labels
   the same listing wrong (`generalize/v070-47/labels.json`, `generalize/REPORT-v070.md` "Losses").
2. **A reach count taken from the def line of a new method was accepted.** flask#6133 at v0.6.0: "every changed hunk
   ran on both sides, in up to 342 tests" when the new body ran only in the PR's own test → `true` in v2; v3: "was
   labelled right but was not accurate either".

   These two are the only v2 labels v3 re-judges ("Applying the same rubric, v0.6.0 is 41 of 47",
   `generalize/REPORT-v070.md`). `generalize/v060-47/labels.json` was not edited afterwards: it still holds 43.
3. **Field shape, not rule:** v2 pilots keep the v1 string `rows` (`"-"`, `"rows 0"`); the v2 47 set has integer
   `rows`/`real`/`false` and no `first`, `excluded_fields` or raise fields in labels (raise figures are in
   `generalize/v060-47/data.json`).

No evidence of a v2/v3 difference for: crashes (none at v0.6.0), "ran all N" with no outcome (no such case at
v0.6.0; attrs 1469 had 1 unrunnable test and was right in both), "ran in no test" after a cut-off run (sympy#30741 at
v0.6.0 did not print that clause), gate sentences (first printed at v0.6.0 for attrs, labelled the same way), untraced
rows (both versions count django#22026's untraced row false).

### 3.2 v1 (v0.4.2 and v0.5.x: `performance-v042/labels.json`, `performance-fixes/labels.json`, `pilot/attrs/labels.json`)

Stated rules (`performance-v042/REPORT.md`, "Labels"):
"A first line is 'correct' when its category (flips / behaviour difference / no test ran the changed code / adds new
code only / refactor only / 0 flips on tested code) matches what the pull request is." and "'No flip … whether any ran
the changed code was not seen' counts as correct only where nothing better was knowable."

Differences from v3:

1. **The first line was judged by category, not clause by clause.** flask#5812 at v0.4.2 announced "77 behaviour
   differences", 60 of them false, and was labelled right: "Category right, 78% of rows false"
   (`performance-v042/labels.json`). In v2/v3 a line announcing false rows is wrong (django#22026, #22171, sympy#30781
   at v0.6.0; django#22026 at v0.7.0). Note: v1 also labelled lines wrong when every announced row was false
   (django#22026, flask#5818 at v0.4.2; attrs 1509 at v0.5.1), so the v1 rule is "wrong when the category is wrong",
   and an all-false row set made the category wrong. Partly a format change: v0.4.2/v0.5.x lines had no per-hunk
   clauses ("0 flips … among N tests run, some of them on the changed code"), so R4–R9 had nothing to apply to.
2. **"Not seen" was its own category.** A "no flip … whether any ran the changed code was not seen" line was wrong
   only when something better was knowable: wrong on django#21887, #22158, #22187, sympy#30809 (and attrs 1571), right
   nowhere in the labels. v0.6.0 and later never print it, so v2/v3 have no such rule.
3. **Rows were labelled with "intended".** v1 `rows` strings record intended separately ("rows 17, real 16, intended
   16", `performance-fixes/labels.json` flask#5812; "rows 2, real 2, intended 2", sympy#30803). v2/v3 drop the
   separate count (all real rows since are intended per the notes). "rows 0 (missed)" (flask#5898 at v0.4.2) marked a
   change side-by-side should have shown and did not; v2/v3 have no equivalent marker.
4. **A row was counted per kind, not per input** — this is an engine change, not a rule change: "On Flask 5812 that
   is 338 rows where there were 16: each call pairs with its own input now, instead of one row per kind"
   (`generalize/REPORT.md`, "Numbers").
5. **Labels were carried over when a comment changed only in test counts.** `performance-fixes/REPORT.md`: "Labels
   were redone by hand for every pull request whose comment changed beyond test counts; the others keep their v0.4.2
   labels." (8 relabelled, 39 carried; plus django#22046 and sympy#30838 relabelled after #83.) v3 relabelled all 47 and
   wrote "Same as v0.6.0" where unchanged.

No evidence found of a v1/v2 difference in `adds`: both pilot reports declare the v1 rubric for their v2 labels, and
the `adds` rules in §2.2 have v1 examples for each branch (evidence → something; feature tests / diff-evident tests →
nothing; false rows / false evidence → noise). No evidence either way on untraced rows in v1: every v1 row note gives a
cause.

### 3.3 Before v1 (for completeness)

- `retrospective/labels.json` (v0.2.6, 2026-10-18) is a different scheme: per side-by-side row `real, intended` /
  `real, not intended` / `artifact`, and per generated probe `intended` / `not intended (cosmetic)` / `artifact`
  (`retrospective/PROTOCOL.md`, "Labels and samples"). No first-line or adds labels.
- `ci/REPORT.md` "Step 2: ten pull requests from the frozen 100" (v0.2.6 comments) and "Step 2 again" (0.4.0) are
  where the something / nothing / noise scale first appears; there an edited-test flip counted as noise (Django #21820
  "adds noise"). By v1 edited tests are "updated", not flips.

---

## 4. Which rubric each labels file and table uses

### 4.1 labels.json files (all 11 under `evaluation/`)

| File | Engine | Rubric | Note |
|---|---|---|---|
| `evaluation/retrospective/labels.json` | v0.2.6 | pre-v1 (row/probe scheme) | `side_by_side`, `probes` lists; not first_line/adds |
| `evaluation/performance-v042/labels.json` | v0.4.2 | v1 | |
| `evaluation/performance-fixes/labels.json` | v0.5.0 + #71–#79, #83 | v1 | 39 labels carried from v0.4.2 |
| `evaluation/pilot/attrs/labels.json` | v0.5.1 | v1 | report states "same rubric as performance-v042" |
| `evaluation/generalize/v060-47/labels.json` | v0.6.0 | v2 | `prior_first_line_correct` = v1 label; not updated to the v3 re-judgment (43, v3 says 41) |
| `evaluation/pilot/attrs/v060/labels.json` | v0.6.0 | v2 | report states "same rubric as the v0.5.1 labels" |
| `evaluation/pilot/httpx/labels.json` | v0.6.0 | v2 | report states "same rubric as performance-v042" |
| `evaluation/generalize/v070-47/labels.json` | v0.7.0 | v3 | `prior_*` fields hold v2 labels |
| `evaluation/pilot/attrs/v070/labels.json` | v0.7.0 | v3 | |
| `evaluation/pilot/httpx/v070/labels.json` | v0.7.0 | v3 | |
| `evaluation/pilot/jinja/labels.json` | v0.7.0 | v3 | keys are bare PR numbers |

Not a labels.json, listed because the name matches: `evaluation/extras-part4/mutation-labels.json` (mutant verdicts,
outside this rubric).

### 4.2 Results tables in the reports

| File | Table (heading › first header cells) | Rubric |
|---|---|---|
| `performance-v042/REPORT.md` | Results › "\| Django \| sympy \| Flask \| All" | v1 |
| `performance-v042/REPORT.md` | What the reviewer already had: project CI on the 15 … › "PR \| What AMU added" | v1 (the 15 `something`) |
| `performance-v042/REPORT.md` | All 47 › "PR \| Title \| First line (as posted) \| First line correct \| Adds \| Rows" | v1 |
| `performance-fixes/REPORT.md` | (title) › "Fix \| PR \| Defect" | no labels |
| `performance-fixes/REPORT.md` | Results › "\| Django \| sympy \| Flask \| All" | v1 (both sides of →) |
| `performance-fixes/REPORT.md` | Per pull request › "PR \| Before (v0.4.2) \| After \| Fix" | v1 |
| `performance-fixes/REPORT.md` | Follow-up: the three leftovers fixed › "Fix \| PR \| What it changes" | no labels |
| `performance-fixes/REPORT.md` | Follow-up: the three leftovers fixed › "\| Before #77–#79 \| After" | v1 |
| `generalize/REPORT.md` | Numbers › "\| Before (performance-fixes …) \| v0.6.0" | Before = v1; v0.6.0 = v2 |
| `generalize/REPORT.md` | Numbers › "PR \| Before \| Now" (first lines changed) | Before = v1; Now = v2 |
| `generalize/REPORT-v070.md` | Numbers › "\| v0.6.0 \| v0.7.0" | v0.6.0 = v2, except "(41 with the same rubric)" = v3 applied to v0.6.0; v0.7.0 = v3 |
| `generalize/REPORT-v070.md` | Numbers › "PR \| v0.6.0 \| v0.7.0" (first lines changed) | v0.6.0 = v2 ("right (labelled)" for 5812, 6133); v0.7.0 = v3 |
| `pilot/attrs/REPORT.md` | Results › "Fork PR \| attrs PR \| … \| Correct \| Adds \| False rows" | v1 |
| `pilot/attrs/REPORT.md` | Results › "\| attrs (15) \| v0.4.2 set (47)" | v1 (both columns) |
| `pilot/attrs/REPORT.md` | Re-run at v0.6.0 › What changed › "\| v0.5.1 \| v0.6.0" | v1 / v2 |
| `pilot/attrs/REPORT.md` | Re-run at v0.6.0 › Per pull request › "attrs PR \| v0.5.1 first line \| v0.6.0 first line \| Correct" | v1 → v2 |
| `pilot/attrs/REPORT.md` | Re-run at v0.7.0 › What changed › "\| v0.6.0 \| v0.7.0" | v2 / v3 |
| `pilot/httpx/REPORT.md` | Results › "Fork PR \| httpx PR \| … \| Correct \| Adds" | v2 |
| `pilot/httpx/REPORT.md` | Results › "\| httpx (15)" | v2 |
| `pilot/httpx/REPORT.md` | Re-run at v0.7.0 › What changed › "\| v0.6.0 \| v0.7.0" | v2 / v3 |
| `pilot/jinja/REPORT.md` | Numbers › "\| jinja, v0.7.0" | v3 |
| `pilot/jinja/REPORT.md` | Per pull request › "Fork PR \| Upstream \| … \| First line right \| Adds" | v3 |
| `STATUS.md` | Numbers on the 47-pull-request set › "\| v0.4.2 \| v0.5.1" | v1 |
| `STATUS.md` | The five pull requests where the first line is still wrong › "PR \| Cause" | v1 |
| `retrospective/REPORT.md` | The five numbers (row 3, precision); Side-by-side: the 18 labelled rows; The other results (probe labels) | pre-v1 row/probe scheme |
| `retrospective/REPORT.md` | Decision rules; Before and after; Per pull request | measured, no rubric labels |
| `ci/REPORT.md` | Step 2: ten pull requests from the frozen 100 › "… \| Label"; Step 2 again (both tables) | pre-v1 adds scale |
| `ci/REPORT.md` | Step 1; Step 1 again; Six throwaway pull requests on 0.4.0 | pre-v1 "came out as wanted" scheme |
| `tracer/`, `difference-finder/`, `generate/`, `before-tests/` REPORT.md | — | outside this rubric (flip-trust, likely_fail, mutant and row labels of their own) |

---

## 5. Open points in v3 (not settled; the blind labeling below hit three of them)

1. **Untraced rows.** django#22026 counts untraced equal-preview rows false; sympy#30741 counts 179 untraced
   equal-preview rows real. Both are v3. The task brief's "untraced = false" matches the first, not the second.
2. **Wrong first line vs `adds: noise`.** v3: 22095, 22204, 30776, 5812, 6133 wrong → `nothing`; 30741 wrong →
   `noise` (no reason stated). v2: attrs 1454 and 1606 wrong with 0 rows → `noise`; django#22158 wrong → `nothing`.
3. **Naming an unexecutable or unreached hunk.** attrs 1507, 1600, 1606 and httpx 3571 → `something`; django#22158,
   #22186, #21887, sympy#30571, #30797 → `nothing`. Inferred split: the attrs and httpx cases are hunks a reviewer would
   not know were unexercised; the Django/sympy ones are platform or optional-dependency code a reviewer expects not
   to run here (stated for django#22204 at v1). Not written down anywhere.
4. **Non-code listed as code** (R8): wrong for flask#5812, right for jinja 2096. The inferred split (was the code around
   it run?) is not stated.
5. **"ran all N" threshold** (R3): 400 of 679 without outcome is wrong, ≤ 9 is right; nothing in between has been seen.
6. **`generalize/v060-47/labels.json` still says 43 of 47**; under v3 it is 41. Either add the v3 re-judgment as a field
   or note it in that file.

## 6. Blind second labeling (how much the hand labels can be trusted)

`blind/` holds 20 comments drawn at random (seed 20261109) from the 92 labelled at v0.7.0 under rubric v3: the 47 set
and the attrs, httpx and jinja pilots. `make.py` strips the pull request number and every commit id from the comment and
the diff; `RUBRIC.md` is rubric v3 without its worked examples (they name pull requests from the pool); `label.py`
records a labeler's answers without reading `key.json`; `score.py` compares them with the existing labels per field.

The second labeler was a fresh agent session given only `RUBRIC.md`, `items/` and `label.py` in a folder outside the
repository, and told to open nothing else. Results are in `blind/agreement-blind.json` and in `STATUS.md`, "How much to
trust these numbers".

## 7. Stability (how much one run can be trusted)

10 of the 47 pull requests, drawn at random (seed 20261109), are run three times each on the same frozen engine with
the same recordings. Compared across the three trials: the first line (byte for byte after replacing durations), the
number of behaviour rows, the number of tests run, and the flip count. Results in `generalize/v071-47/stability.json`.
