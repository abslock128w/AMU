**AMU: 0 flips** at `<sha>`, ran all 538 tests; every changed hunk ran on both sides, in up to 289 tests

<details><summary>What ran and what was not seen</summary>

### Behaviour differs on the same input (0)

Each changed function was recorded in each side's own run of the same tests, on the objects that side's code built. Calls are paired by test and by their order in it, and compared only where both inputs are equal: 781 pair(s) compared, 90 not compared because the inputs differ, 0 call(s) without a partner, of 2 of 2 changed function(s). The tests behind a difference ran again on each side, and the merge base's twice, the second time in another checkout: 3 fields differed between the merge base's two runs, so they belong to the process or checkout and are left out of the comparison.

### Changed hunks (5)

What ran each hunk of the change, from each side's own run:

- `httpx/_models.py:401` in `Request.__init__`: ran on both sides (270 tests)
- `httpx/_models.py:540` in `Response.__init__`: ran on both sides (289 tests)
- `httpx/_types.py:18`: ran on both sides (`at import`)
- `httpx/_types.py:69`: ran on both sides (`at import`)
- `httpx/_types.py:86`: ran on both sides (`at import`)

### What ran

All 538 tests of the suite on both sides, no selection: 9.6 s on the merge base and 9.4 s on the head with `pytest`, budget 300 s per side.

<sub>AMU `0.7.0` from `v0.7.0`, report `<sha>`. Full JSON is in the workflow run's artifact.</sub>

</details>

<!-- amu-ci-comment v1 -->