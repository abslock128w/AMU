**AMU: no test ran the changed code in `amu/ci/counters.py`** at `6ba09f5`; 29 tests run

### Behaviour differs on captured input (0)

Each changed function the tests called on the merge base ran there twice as it was and twice as changed, on copies of the same arguments: 0 call(s) of 0 of 1 comparable changed function(s). Only differences both versions repeat are listed.

### Gaps (3)

Not run or not seen:

- `16905 call sites unresolved repo-wide; dynamic dispatch may reach more`
- `No existing test reaches the change within five source hops`
- `side-by-side: none of the 1 comparable changed function(s) was called by the tests run on the code before the change`

### What ran

29 of 29 candidate tests on the head, 2.8 s with `pytest`, budget 300 s per side. Failures were rerun on the merge base. Tests outside the candidates were not run.

<sub>AMU `0.3.0` from `967db59`, report `b9c4fcd727f1`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->