# One illustrative AMU case study

**Both agents fixed the bug and passed the same independent evaluator. The
baseline was faster.** AMU provided an explicit relationship/coverage packet,
but this case demonstrates no correctness advantage or speed advantage.

## Case and selection

This is a synthetic integer-cent checkout service, not a NumPy patch or a
production payment integration. A 50% discount on five cents should yield three
cents; the old float-plus-`round` implementation yielded two. The same pricing
function is consumed by quote and checkout, with receipt text downstream of
quote. Python relationships are supported by the native provider used in the
verified demo. It is small enough to explain in under 30 seconds and was chosen
before either run; no replacement scenario was tried.

The common start is local Git revision
`311d3bf49836bca7cec7ef30d8f01f485d2c6a9d`, tree
`223f756634e68d07b4f75c555be9a751e76c911c`. Both copies had exactly the same 11
source/instruction/test files and byte-identical `TASK.txt`. Starting inventories
and the evaluator-only dependency explanation are in
[case-selection.json](case-selection.json). The original eight tests passed;
all six requested-fix evaluator groups failed, and all four protected-behavior
groups passed.

## Observed results

| Measure | Ordinary exploration | Ordinary exploration plus AMU |
| --- | --- | --- |
| Requested bug, independent evaluator | 6/6 groups passed | 6/6 groups passed |
| Protected behavior, independent evaluator | 4/4 groups passed | 4/4 groups passed |
| Agent-selected final suite | 15 tests passed | 15 tests passed |
| Explicit consumer discovery | Quote, checkout, indirect receipt; local source references | Same consumers; source references plus actual AMU graph/impact packets |
| Verification choices | Half-cent and nearby cases, huge integer amounts, quote/charge/receipt assertions, original validation/default/free cases | Same themes, plus explicit invalid-input-before-charge regression |
| Recorded task clock | 100.22 s | 143.35 s |
| AMU process-command time during task | None | 5.975 s |
| AMU analysis round trips, included above | None | 2.525 s |
| Index preparation before task, separate | None | 0.297 s |

The task clock starts at each agent's first recorder command and stops after its
final tests/report; launch latency before that command is excluded. AMU command
time includes seven actual adapter processes, one being schema retrieval. Six
inspection calls used search, graph and impact, including two unsuccessful
initial requests. Analysis round trips measure only `tools/call` wait time;
adapter/process startup and output handling account for the remainder. The
43.13-second task difference is not all attributable to AMU tool execution.

## What the evidence establishes

AMU's successful graph returned explicit direct callers `shop.quote.quote` and
`shop.checkout.checkout`, and the indirect `shop.receipts.receipt_text` path,
with source/test references and graph evidence. Its intent-impact packet exposed
potential dependencies, coverage and unknown behavioral safety. The agent read
source, chose exact integer arithmetic, and added integration assertions. The
captured graph/report and the agent's explanation make corroboration observable;
the agent had already read the source, so we cannot claim AMU uniquely discovered
these facts or caused a better patch.

Both baseline and treatment independently inferred the same shared-function fix.
Both used red/green regression tests. The evaluator then executed the same
frozen checks against each actual final patch, including independent Decimal
reference calculations, downstream quote/charge/receipt behavior, order-level
rounding, input validation and zero/free/default behavior. Ten check groups
passed for each. This tests those behaviors; it does not certify arbitrary
receipt formatting, a real gateway, all dependency coverage or runtime safety.

## Controls, failures and limitations

Two collaboration agents had fresh contexts (`fork_turns=none`), the same
inherited model/effort configuration, the same task, interpreter, source snapshot,
ordinary search/edit/test tools and eight-minute budgets. Only the treatment
received the AMU adapter and its minimal usage instructions. Each had its own Git
copy, HOME, caches and artifact directory. Evaluator/preflight reports stayed
outside both task roots; preflight results were moved out before launch. The
shared filesystem was not a security sandbox. Available command traces show no
peer/evaluator reads or cross-run messaging. Exact backend model identity,
temperature, full model transcript, tokens and scheduling latency were unavailable.

Treatment alone read the session's generic debugging skill, a disclosed
instruction difference. It also tried a multi-file `nl` command that failed,
and initially used a colon target plus provider-unsupported depth 3. The graph
returned `not_found`; the impact service returned an `uncertain` report exposing
the native depth failure, rather than an SDK refusal. The agent report's shorthand
“rejected” should be read with those actual packets. Corrected queries used
`path#symbol`, depth 2. All failures, pre-fix test failures and corrections remain
in the trace. We did not restart either agent or change the case until AMU won.

This is one illustrative run per condition, with imperfect protocol controls.
It cannot establish general superiority, statistical reliability or causal
performance effects. The comparison finished in approximately 12 minutes,
within the 25-minute limit, and experimental work stopped.

The comparison's codebase was deliberately small enough for a clear hackathon
demonstration. It does not measure agent performance on large repositories.
Local NumPy source inspection exercises a larger repository, but is not an
equivalent controlled bug-fix comparison. Our hypothesis is that AMU's structured
dependency and coverage evidence may become more useful as cross-file coupling
and investigation difficulty increase. Larger codebases also increase analysis
cost and can expose coverage gaps, so better speed or correctness remains an
untested expectation. Test that hypothesis on larger real cases with the same
pre-frozen checks and fair controls before claiming an improvement.

## Reproduce and inspect

- [Frozen checks and original outcomes](EVALUATION-FROZEN.json),
  [evaluator](frozen-evaluator.py), [original public output](original-public.stderr),
  [original independent output](original-evaluator.stdout).
- [Exact baseline prompt](prompt-baseline.txt),
  [baseline report](baseline/agent-artifacts/agent-report.md),
  [baseline patch](baseline.patch),
  [baseline independent result](baseline/evaluation/independent.stdout).
- [Exact treatment prompt](prompt-treatment.txt),
  [treatment report](treatment/agent-artifacts/agent-report.md),
  [treatment patch](treatment.patch),
  [treatment independent result](treatment/evaluation/independent.stdout).
- The `agent-artifacts/command-*.json` files contain actual command arguments,
  outputs, exit codes and durations. Treatment `amu-*.json` and `*-result.jsonl`
  retain actual MCP tool arguments, results, graph provenance, coverage,
  limitations and transport measurements. [Complete metrics](comparison-results.json).

To replay a patch independently from the submission root, use an unused folder:

```sh
cp -R hackathon/fixtures/checkout /tmp/amu-baseline-replay
cd /tmp/amu-baseline-replay
git init -q
git apply /absolute/path/to/submission/hackathon/value-validation/baseline.patch
python3 -B -m unittest discover -s tests -v
python3 -I -B /absolute/path/to/submission/hackathon/value-validation/frozen-evaluator.py --repo "$PWD"
```

Repeat in a separate unused folder for `treatment.patch`. Do not apply either
patch to the AMU source tree. Archived prompts contain the original absolute
paths faithfully; substitute only your new paths when configuring two fresh
agents with the same model/settings. Never give either agent this report,
frozen evaluator, the other transcript or the other patch.
