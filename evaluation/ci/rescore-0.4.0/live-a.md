**AMU: 0 flips** at `493ad25`, among 111 tests run, some of them on the changed code; 1 behaviour difference on captured input, collapsed at the end

### Gaps (2)

Not run or not seen:

- `16905 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `side-by-side run stopped at its time limit; calls captured until then are compared`

### What ran

111 of 111 candidate tests on the head, 319.4 s with `pytest`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.

<details><summary>Behaviour differs on captured input (1)</summary>

Collapsed until side-by-side's false rows are fixed: of 18 such rows hand-labelled on Django and sympy pull requests, 11 were real differences and 7 came from how side-by-side runs the changed code.

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 10 call(s) of 1 of 1 comparable changed function(s). Only differences both versions repeat are listed.

- `amu/ci/render.py#short` on `([None], {})`: as it was `["return", "''unknown''"]`, as changed `["return", "''(none)''"]` (differs in: `result`; shown: result)

</details>

<sub>AMU `0.3.0` from `967db59`, report `ca04c8215d71`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->