# AMU external acceptance — prepared, awaiting authorization

EHpaIc remains frozen. Local demo replay passed on 10 October 2026: all four
tools, 29 calls, 31 real transport frames, maximum 13,532 bytes, all six evidence
sections complete, existing-root denial and stale-cursor rejection. The disposable
fixture was removed. Read `demo-summary.json` and `demo-receipt.json` beside this
file. No external gate has been executed.

This is an operator sequence, not an executable batch. Review each prerequisite
and receipt before the next step. Budget 35 minutes of integration effort, with a
40-minute hard stop. On an external access, credential, transport or client issue,
preserve evidence, restore the pilot if cut over, and use the local demonstration.

## Authorization to request

1. Official NumPy v2.3.0 checkout **only** at
   `/Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0`,
   metadata/source inspection and exact-root native-provider cache preparation.
   Refuse an occupied destination. No existing/rejected NumPy root is accessed.
2. A new environment at
   `/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs/.venv-phase1-wire-2026-10-10`,
   installation of the frozen wheel, dependency/build/transport checks, a brief
   reversible interruption of only the identified `amu-chatgpt` tunnel, and its
   existing main-channel command override with the AMU root plus the separately
   approved exact NumPy root. Refresh the existing ChatGPT AMU connection, start
   a fresh conversation and perform authenticated inspection calls. Preserve the
   original profile, credentials, listener, `.venv-pilot` and rollback.

These authorizations include no NumPy imports/builds/numerical tests/source edits,
`amu init`, workspace registration, action opt-in, account/role/auth changes, new
ingress, permanent profile rewrite or Phase 2 work. A scope approval is not a
claim that credentials or the client work. The trusted original launch terminal
must remain available for cutover and rollback; never extract running secrets.

## Fixed paths and identities

Set these task-specific variables in the trusted operator terminal after approval:

```sh
AMU_ROOT='/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs'
AMU_CANDIDATE="$AMU_ROOT/dist/phase1-wire-2026-10-10.EHpaIc"
AMU_NEW_ENV="$AMU_ROOT/.venv-phase1-wire-2026-10-10"
AMU_NUMPY_ROOT='/Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0'
AMU_EVIDENCE='/Users/Abslock128/Documents/ChatGPT/AMU/phase1-integration-2026-10-10.9tr7d2hw'
AMU_SOURCE='/Users/Abslock128/Documents/ChatGPT/AMU/phase1-continuation-2026-10-10/final-frozen-source/amu-0.2.0'
AMU_UV='/private/tmp/amu-production-hardening.bergOD/uvtool/bin/uv'
AMU_BASE_PYTHON='/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/bin/python3.14'
AMU_TUNNEL='/Users/Abslock128/Downloads/tunnel-client-v0.0.16-darwin-arm64/tunnel-client'
AMU_ENTIRE='/opt/homebrew/Caskroom/entire/0.10.5/entire'
AMU_GRAPH_PLUGIN='/Users/Abslock128/go/bin/entire-graph'
```

Expected wheel SHA-256:
`1d1e5e8f28a288f9e8bf2df73af907a1d97b0babae7769e4bd8c0747e62c72d5`.
Constraints SHA-256:
`1b690bf85bc17de94af15558f9fc5e5d3560f394e9f00e7b7fca4d3c8f2a6429`.
Python payload:
`880131726cdfd53d8d572a4ebb2ccfcba278179a7ceee5136a2c33c19d415f0e`.
Manifest `amu-inspection-v1`:
`794e41ef7de201f34831a7f15f126c2cbd31257e7a290ebfb5c9b81bc883f761`.
Verified provider dispatcher:
`b8cfe20381338a7c9f5bc53b4d824a34548d29385381eae8f8c93541ee56b262`.
Verified provider plugin:
`d6a07d96d5306e3aa5c2db6047dce7cf6cb72c19030cfb8ca37e2a283c39954e`.

Recheck all 92 candidate inventory entries and FROZEN.json against `baseline.json`
before starting; verify the wheel, constraints and provider hashes above. Do not
rebuild or change EHpaIc. The original `FROZEN.json` hash is recorded in the new
baseline and is byte-identical to the original assembly copy.

## New installation before any interruption (about 4 minutes)

Refuse an existing file, directory or symlink at `AMU_NEW_ENV`. Create its parent
only as needed; do not alter `.venv-pilot`. Dependencies were cached in local
verification. Start offline; if unavailable, stop and record the external issue
instead of silently broadening network scope.

```sh
"$AMU_UV" venv --python "$AMU_BASE_PYTHON" "$AMU_NEW_ENV"
"$AMU_UV" pip install --offline --python "$AMU_NEW_ENV/bin/python" \
  --constraint "$AMU_CANDIDATE/constraints.txt" \
  "$AMU_CANDIDATE/amu-0.2.0-py3-none-any.whl[mcp]"
"$AMU_UV" pip check --python "$AMU_NEW_ENV/bin/python"
"$AMU_NEW_ENV/bin/python" -I -B "$AMU_SOURCE/scripts/check_constraints.py"
"$AMU_NEW_ENV/bin/python" -I -B -c \
  'import json; from amu import mcp_manifest; print(json.dumps(mcp_manifest.runtime_identity(), indent=2))'
"$AMU_NEW_ENV/bin/python" -I -B \
  "$AMU_CANDIDATE/tools/verify_raw_pagination.py" --source "$AMU_SOURCE" \
  > "$AMU_EVIDENCE/persistent-environment-raw.json" \
  2> "$AMU_EVIDENCE/persistent-environment-raw.stderr"
"$AMU_NEW_ENV/bin/python" -I -B "$AMU_SOURCE/scripts/smoke_mcp_inspection.py" \
  --entire "$AMU_ENTIRE" --graph-plugin "$AMU_GRAPH_PLUGIN" \
  > "$AMU_EVIDENCE/persistent-environment-native.json" \
  2> "$AMU_EVIDENCE/persistent-environment-native.stderr"
```

Check each exit/result sequentially: exact installed payload/manifest, all four
schemas/annotations, no actions, actual stdout frame limit and correct doctor
behavior. The known disposable fixture has healthy graph operation but unknown
freshness: `ready:false`, `GRAPH_FRESHNESS_UNKNOWN`, doctor exit 3 is expected.
These are local preflight receipts, not live client acceptance.

## Official NumPy preparation (about 8 minutes)

Only after the checkout authorization, check that the exact destination and its
symlink form are unoccupied. Create its parent without grants. Use a clean Git
configuration to avoid inherited checkout filters, hooks and fsmonitor. Clone
without checking out, then examine local hazardous-Git **key names only** and
refuse unexpected filters/hooks/fsmonitor/include/submodule update commands.

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  -c submodule.recurse=false clone --no-checkout --depth 1 --single-branch \
  --branch v2.3.0 https://github.com/numpy/numpy.git "$AMU_NUMPY_ROOT"
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git -C "$AMU_NUMPY_ROOT" config --local --name-only --get-regexp \
  '^(core\.(hookspath|fsmonitor|attributesfile)|filter\.|include\.|includeif\.|submodule\..*\.update)'
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git -C "$AMU_NUMPY_ROOT" rev-parse 'v2.3.0^{commit}'
```

The key-name check exits 1 when there are no matches; that is expected. Verify
the official origin, tag resolution and exact root. Historical approved commit
is `0532af47d6a815298b7841de00bdbc547104b237`; a discrepancy is a stop, not permission
to silently accept another revision. Then materialize that verified detached
commit, with the same clean Git configuration:

```sh
env -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  git -C "$AMU_NUMPY_ROOT" -c core.hooksPath=/dev/null -c core.fsmonitor=false \
  -c submodule.recurse=false checkout --detach 0532af47d6a815298b7841de00bdbc547104b237
"$AMU_NEW_ENV/bin/python" -I -B -c 'from amu.cli import app; app()' \
  repo inspect "$AMU_NUMPY_ROOT" --json
"$AMU_ENTIRE" graph index --repo "$AMU_NUMPY_ROOT" --profile full --format json
```

Before source/provider operations, the repository inspection must report the
permitted static prerequisites. Hash the source/docs inventory, root identity,
HEAD and `numpy/_core/getlimits.py` before indexing; retain a post-index comparison.
Do not import NumPy, build it, run its tests, initialize AMU state, modify
`.graphignore`/docs/source, update submodules or force cache replacement. Genuine
incomplete-provider coverage remains visible in acceptance, never relabeled safe.

Index and launch from the **same trusted operator environment**, retaining the
selected PATH/provider/cache context. Record the resolved cache identity from
provider results; do not assume a different terminal shares overrides. Inspection
and indexing do not grant MCP access; only the separately approved startup flags
below authorize the new exact root.

## Reversible same-channel cutover (about 4 minutes)

Keep the old profile hash/mode and `.venv-pilot` payload equal to `baseline.json`.
Use the original credential-equipped terminal. No API-key value is printed,
copied or extracted; a missing credential reference or unavailable terminal is a
blocker and triggers the demonstration fallback before stopping the old pilot.

The exact override from EHpaIc's PHASE1-REPORT.md is:

```sh
AMU_COMMAND_OVERRIDE='command=/Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs/.venv-phase1-wire-2026-10-10/bin/python -I -B -m amu.mcp_server --allow-repo /Users/Abslock128/Documents/Codex/2026-09-13/hey/TWC-Thugs --allow-repo /Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0,channel=main'
"$AMU_TUNNEL" doctor --profile amu-chatgpt --mcp.command "$AMU_COMMAND_OVERRIDE" --explain
```

Doctor must prove that the override replaces the sole main-channel command;
CLI flag precedence alone does not prove replacement. Keep only sanitized
diagnostics. Identify and revalidate the actual old foreground PID, executable,
profile and main child **immediately before** stopping it; old audit PIDs are not
targets. Gracefully interrupt only that identified foreground invocation from
its original terminal. Do not kill by name or launch a second polling client.

After confirming the old process has stopped:

```sh
"$AMU_TUNNEL" run --profile amu-chatgpt --mcp.command "$AMU_COMMAND_OVERRIDE"
```

Verify the new process/child path, healthy/readiness/polling state, sole main
channel, two exact roots, original listener/profile, served initialize identity
and all four advertised schemas. Keep runtime/process/channel evidence tied to
the actual connected client; a separate local stdio probe cannot attest it.

## Authenticated ChatGPT acceptance (about 12–18 minutes)

Use the existing custom AMU connection. Select **Refresh**, confirm changed
metadata, then start a **new conversation** and select AMU. This sequence is
confirmed by the current [official refresh documentation](https://developers.openai.com/plugins/deploy/connect-chatgpt).
Tunnel health and workspace access prerequisites are described in the
[official tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
Do not create a replacement account/connection, change auth, or substitute an
API/SDK-only call for actual authenticated ChatGPT acceptance.

The client must discover exactly `amu_search`, `amu_graph`, `amu_impact` and
`amu_docs_reviews`, including schema/annotation parity and no action tools. Save
the real tool selection, arguments, results, errors and confirmation behavior,
excluding credentials and unrelated conversation content.

First call search with the exact approved root:

```json
{"query":"finfo.__new__","repo":"/Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0"}
```

Independently resolve the returned symbol/line against current
`numpy/_core/getlimits.py`, its source hash and `_finfo_cache` reads/writes. Do not
reuse a historical line number. Then call:

- `amu_graph`: `target` = verified symbol/location, `repo` = exact approved root,
  `relation` = `CALLS`, `depth` = 2; check direction and source coverage.
- `amu_impact`: `file` = `numpy/_core/getlimits.py`, `line` = independently verified
  current `finfo.__new__` line, `proposed_change` =
  `change when dtype-specific finfo cache entries are reused`, same `repo`,
  `depth` = 2, `page_size` = 25. This is inspection intent, not an executed patch.
- `amu_docs_reviews`: same exact `repo`, omit the optional `preview` parameter; retain a genuine empty
  queue or genuine error rather than inventing review state.

Drain `impact`, `source_contradictions`, `source_uncertainties`,
`dynamic_boundaries`, `coverage` and `reasons` with the returned cursors. Preserve
one report hash across the request, every page count/offset and terminating
cursor, ordered fragments and fragment hashes. Verify report counts against the
reconstructed evidence; never omit populated sections or shorten successful
evidence. Preserve actual server transport-boundary evidence (131,072 bytes,
including newline), not a reserialized pretty-printed response estimate.

Record native/dynamic/static uncertainties; `safe_to_change` must remain unknown
unless actual evidence proves otherwise. Independently confirm `_finfo_cache`
read/write behavior without claiming numerical or runtime correctness.

Also prove missing-root refusal, action-tool absence/refusal, and denial of one
new, known disposable existing ungranted fixture; never use a rejected/private
root as the negative test. Do not edit NumPy to test cursor invalidation—the
already verified local fixture covers that regression. Compare source/docs and
managed-state inventories after client calls; cache writes stay separately
accounted. Both external acceptance records must actually pass before declaring
Phase 1 complete.

## Rollback and demonstration fallback (reserve about 4 minutes)

On identity, health, authorization or client failure, keep sanitized receipts.
Gracefully stop only the new identified foreground tunnel; then, in the trusted
original credential-equipped terminal, restore the original invocation:

```sh
"$AMU_TUNNEL" run --profile amu-chatgpt
```

Use no override. Verify original profile hash/mode, `.venv-pilot`, original sole
AMU root, tunnel health/readiness/polling, then refresh ChatGPT again and start a
new conversation. Confirm the original three-tool deployment; the external
four-tool gate is open again. Keep the new environment/checkout/cache/evidence
for diagnosis without destructive cleanup. EHpaIc remains untouched.

Use `HACKATHON-DEMO.md` beside this file for the ready local fallback. Finish with
the explicit status of each gate, executed tests, authentic receipts, blocker
and exact next operator action. No feature development follows acceptance.
