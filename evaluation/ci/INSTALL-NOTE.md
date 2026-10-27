# AMU on pull requests: install note and sample comment

For the owner's introductions to maintainers of Python repositories with slow suites.

## Install note (one paragraph)

AMU runs the tests that a pull request's diff reaches on both sides of the change, the merge base and the head, and
posts one comment listing the tests that flip from passing to failing. It edits that comment on every push, and it
never blocks a merge. To try it, copy two workflow files from
[github.com/abslock128w/AMU/tree/v0.4.0/ci](https://github.com/abslock128w/AMU/tree/v0.4.0/ci) into
`.github/workflows/` on your default branch. In `amu-pr.yml`, set both `install:` lines (the AMU job's and the optional
verify job's) to the command that installs your project and its test dependencies, test runner included: for sympy,
`python -m pip install -e . pytest hypothesis`.
- The first file runs your pull request's code under `pull_request`, with a read-only token, and only saves a JSON
  report. It must also be on any other branch pull requests target.
- The second posts the comment from `workflow_run` and never checks out or runs pull request code. GitHub runs it only
  from the default branch. Fork pull requests are covered without `pull_request_target`.
- By default AMU spends up to 300 s of test time per side, inside a 20-minute cap. On sympy the job took 9 to 12
  minutes. The comment follows when the posting job gets a runner, which took up to 15 minutes more while the
  repository's own CI held the runners.
- It caches its index keyed on the merge base. A cache saved by one pull request's job is visible only to that pull
  request's later pushes: a second pull request on the same merge base starts without it.
- With `counters: 'true'` it also keeps per-push counts and SHAs (never source or test names) in a hidden part of its
  own comment. Those let us measure whether its flips are real and whether they get fixed before merge. Nothing is
  sent anywhere else.

Remove the two files to uninstall.

## Sample comment

What a pull request that breaks one test sees (from a local run on a two-file repository):

> **AMU: 1 flip** at `9f3c2ab`, among 1 test run
>
> ### Flips (1)
>
> Pass on the merge base `41d07c2`, fail on the head. Each was rerun on the merge base to confirm it passed there.
>
> - `tests/test_calc.py::test_add`
>
> Rerun on the merge base in a fresh job: 1 passed again, 0 failed there too, so not the change, 0 could not be rerun.
>
> ### What ran
>
> 1 of 1 candidate tests on the head, 1.9 s with `pytest`, budget 300 s per side. Failures were rerun on the merge
> base. Tests outside the candidates were not run.
>
> <sub>AMU `0.2.0` from `v0.1.0`, report `1446af514737`. Full JSON is in the workflow run's artifact.</sub>

When nothing ran, the first line says so instead of giving a count. For example: "**AMU: no flip count** at
`9f3c2ab`: no candidate test ran". A runner that aborts on the head gets its own section, and anything AMU did not run
or could not see is listed under Gaps.
