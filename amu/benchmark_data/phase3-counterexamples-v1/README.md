# Phase 3 reviewed counterexamples

This bundle contains two independent AMU-owned behavior families. Each has an
original source, literal contract, protected checks, and three canonical patches:
`compatible`, `regression`, and `corrected`. Every patch applies to its family's
original source. The corrected patch repairs the behavior exposed by that
family's regression patch; it does not start from the broken candidate.

| Family | Regression | Protected observation |
| --- | --- | --- |
| `explicit-zero` | Uses truthiness for a requested limit | Explicit `0` becomes the default `5`; `False` is not rejected |
| `stable-order` | Sorts a set of inputs | First-seen order changes for numeric and text inputs |

`manifest.json` records the SHA-256 digest of every executable fixture asset.
`tests/test_phase3_counterexamples.py` pins the manifest digest independently,
validates canonical patches, runs only these six reviewed named candidates and
their originals, and checks `evidence.json` against the actual protected results.
The evidence records original and candidate observations, literal inputs and
expectations, outcomes, and source, patch, candidate, contract, checks, and runner
digests. The protected checks are the same bytes for every variant in a family.

From the repository root, reproduce the evidence with:

```sh
python3 -m unittest tests.test_phase3_counterexamples -v
```

The test uses AMU's existing `process.run` runner with isolated Python mode,
clean environment, a ten-second deadline, and 64 KiB output limits. This
process boundary is for the reviewed, hash-pinned fixture only; it is not a
security sandbox for arbitrary code.
