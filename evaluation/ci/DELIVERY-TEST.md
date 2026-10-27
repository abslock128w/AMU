# Delivery test: AMU v0.3.0 installed on a sympy fork from the install note (2026-10-20/21)

Build queue part 3.
- **Setup.** sympy was forked into `abslock128w/sympy`. AMU v0.3.0 was installed by following `evaluation/ci/INSTALL-NOTE.md` and reading nothing else.
- **Replays.** Ten frozen sympy pull requests from the brief 5 retrospective were replayed as pull requests into the fork:
  - each head is the upstream head commit;
  - each base is a branch at its merge base, plus a commit adding the two workflow files;
  - two of them share a merge base;
  - the recording (`observe`) was off.
- **Afterwards.** The pull requests were closed and their branches deleted. The fork is kept.

## Did every comment post

Yes: 10 of 10.

| Fork PR | sympy PR | AMU job | PR opened → comment | First line of the comment |
|---|---|---:|---:|---|
| #1 | 30690 | 490 s | 8.6 min | 0 flips among 1,760 tests run |
| #2 | 30693 (same merge base as 30690) | 539 s | 9.4 min | 0 flips among 2,600 tests run; 1 own test fails on the merge base and passes on the head |
| #3 | 30540 | 667 s | 17.7 min | 0 flips among 1,080; 10 own tests fail on the merge base, pass on the head |
| #4 | 30563 | 629 s | 11.0 min | 0 flips among 360; 1 own test |
| #5 | 30589 | 748 s | 26.5 min | 0 flips among 480; 18 own tests |
| #6 | 30590 | 602 s | 27.1 min | 2 flips among 760, both "fails as the test was before it was edited"; the verify job reran both on the merge base, both passed again |
| #7 | 30748 | 623 s | 25.2 min | 0 flips among 1,680; 1 own test |
| #8 | 30471 | 540 s | 26.0 min | 0 flips among 2,960 |
| #9 | 30701 | 621 s | 17.4 min | 0 flips among 1,760; 5 own tests |
| #10 | 30585 | 565 s | 11.4 min | 0 flips among 560; 2 own tests |

- **Job time:** median 612 s and worst 748 s, all under the 20-minute cap. Opened 8 at once, the jobs started within 4 minutes.
- **From open to comment:** 8.6 to 27.1 minutes. Opened 8 at once, the posting jobs queued behind sympy's own CI jobs on the account's runners (see below).
- **v0.3.0 fixes in delivery:**
  - On #30589, v0.2.6 executed nothing ("other side of the change could not be built"). Here it ran 480 tests and its 18 own tests gave change evidence: the patch-reverse fix (#32) at work.
  - On #30590, the two flips match the retrospective's.

## Cache on the shared merge base: miss

- #1 (30690) saved `amu-Linux-py3.12-319ea7a…`.
- #2 (30693) has the same merge base. Its log says "Cache not found for input keys: amu-Linux-py3.12-319ea7a…", and it saved the same key again.
- GitHub scopes a cache saved by a `pull_request` run to that pull request. Only caches saved on the base branch are shared, and the workflow never runs on the base branch. So the cache serves only a pull request's later pushes. The install note said "caches its index on the merge base", which reads as shared; it now says what happens.

## Where the note was wrong or unclear (fixed in this change)

1. **"set `install:`".** `amu-pr.yml` has two `install:` lines: the AMU job's and the optional verify job's. The note named one. Both need the project's install command, and for sympy that command must add the test runner (`pytest`, plus `hypothesis` for its tests): `pip install -e .` alone gives no runner.
2. **Where the files go.**
   - GitHub runs the posting workflow (`workflow_run`) only from the default branch.
   - The `pull_request` workflow runs from the pull request's merge commit, so it must be on the branch the pull request targets.

   The note said "copy … into `.github/workflows/`". It now says the default branch, and also any other target branch.
3. **The cache.** As above.
4. **Waiting time.** The note gave the time cap but not how long to wait. It now gives the job time seen here and the extra wait for a runner.

## What else happened, not the note's doing

- **sympy's own workflows ran too.** Its `test`, `Pyodide`, `Maintenance` and `zizmor` workflows run on every push to every branch, and on every pull request. Creating base and head branches started dozens of runs. I cancelled those started by the setup pushes. I tried to disable the workflows on the fork, and this session's permission check refused, so they also ran on the ten pull requests. That is the main reason the posting jobs waited.
- **`gh pr create` timed out several times** against GitHub's GraphQL API from this machine. The REST API worked.
- **Outside the fork:** nothing referenced the upstream pull requests. The bodies name them only as "upstream sympy pull request N", with no `#` and no link.
