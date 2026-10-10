# AMU integration handoff — 10 October 2026

**EHpaIc remains frozen. Local Phase 1 is verified; overall Phase 1 remains partial.**
The approved external preparation and local preflight are complete. The operator
reported that the tunnel has not been restarted, so the existing ChatGPT
connection has not been refreshed and no authenticated acceptance is claimed.
This engineering pass stops after approximately 31 minutes; the functioning
local demonstration is ready in [HACKATHON-DEMO.md](HACKATHON-DEMO.md).

## Verified

- The fresh demo replay passed: 29 tool calls, 31 original transport frames,
  largest frame 13,532 bytes. It checked all four tools and their full manifest,
  all six evidence sections, exact-root denial and stale-cursor refusal.
- The new persistent non-editable installation matches EHpaIc's payload and
  manifest. Dependency/constraint checks, native schema/annotation checks,
  doctor behavior and its independent raw-transport replay passed.
- Official NumPy v2.3.0 is checked out only at the authorized exact destination,
  commit `0532af47d6a815298b7841de00bdbc547104b237`. Static inspection independently
  confirmed `finfo.__new__` in `numpy/_core/getlimits.py`, line 494, and its dtype
  cache reads/writes. No NumPy import, build, numerical test or source edit ran.
- All four MCP tools passed local current-source NumPy preflight. Its six
  complete sections contain 7 impacts, 0 contradictions, 2 uncertainties,
  12 dynamic boundaries, 10 coverage entries and 5 reasons. They share report
  hash `37413e62ed1cdcb2b7d8cb125142efc2865669fb4d3652b5ae904d59985d87bd`.
  Eight original pagination frames were retained; the largest is 18,421 bytes,
  below the 131,072-byte boundary. No row fragmentation was needed in this case.
- Preservation checks passed for all 92 frozen candidate files plus its freeze
  inventory, 299 existing work files, 12 managed-state files, 53 old-pilot Python
  files and 2,324 NumPy tracked records. Git HEAD/status and the live profile's
  bytes/permissions are unchanged. The pilot and documented rollback remain intact.

The existing final candidate regression evidence remains 2,065 passed / 12
skipped on each of Python 3.14.6 and 3.11.16, including the actual wire-boundary
regressions. These full suites were not rerun during this integration pass.
Fresh receipts and their hashes are listed in
[integration-summary.json](integration-summary.json).

## Open gates and limits

The authenticated four-tool checks and real NumPy scenario through that client
are **not executed**. Local subprocess probes do not substitute for them. The
served build/process/channel, client metadata, actual calls, bounds, complete
evidence and authority checks still require connected-client acceptance.

The provider's committed HEAD cache is unsafe/incomplete and omits getlimits.py.
The current-worktree queries work and expose incomplete coverage; AMU metadata
readiness remains `REPOSITORY_NOT_INDEXED`, and `safe_to_change` stays unknown.
No initialization/workspace registration or provider repair was included in the
approved scope. Runtime safety and numerical correctness are unestablished.

An initial local docs-review request used an invalid boolean `preview`; the SDK
correctly rejected it. The corrected request omitted that optional string and
passed with a genuine empty review queue. Both receipts are preserved. A
Git-status formatting mismatch in the preservation harness was also retained
and corrected to use the audit's original flags; no bytes had changed.

## Exact next operator action

Both documented scopes are already authorized; no renewed authorization is
needed for their next step. In the **original credential-equipped launch
terminal**, follow [RESTART-NOW.md](RESTART-NOW.md): prove that doctor replaces
exactly one main-channel command with the new environment, gracefully stop only
the identified old `amu-chatgpt` job, and launch the prepared command override.
Keep it running and report only doctor/replacement and health/polling status.
Do not share credentials. The agent will then refresh the existing connection
and execute [LIVE-CLIENT-CHECKS.md](LIVE-CLIENT-CHECKS.md), including the prepared
harmless ungranted fixture.

If the operator step or authenticated surface is unavailable, present the
verified local demo immediately. The original rollback is preserved in
[RESTART-NOW.md](RESTART-NOW.md) and [OPERATOR-STEPS.md](OPERATOR-STEPS.md).
No Phase 2 or additional features have begun. Candidate contents and earlier
evidence have not been edited.
