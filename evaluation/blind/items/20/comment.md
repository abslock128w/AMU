**AMU: 0 flips** at `<sha>`, ran all 539 tests; 1 of 1 changed hunk ran in no test: `httpx/_transports/default.py:358`

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 30 pair(s) compared, 0 not compared because the inputs differ, 0 call(s) without a partner, of 1 of 1 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 0 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Changed hunks (1)

What ran each hunk of the change, from each side's own run:

- `httpx/_transports/default.py:358` in `AsyncHTTPTransport.__init__`: ran in no test

### What ran

All 539 tests of the suite on both sides, no selection: 8.5 s on the merge base and 8.0 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `v0.7.0`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

<!-- amu-ci-comment v1 -->