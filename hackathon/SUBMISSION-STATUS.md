# Local submission handoff — 10 October 2026

**The repository, README, evidence and working recording package are verified
locally. The user has authorized GitHub publication; the video is not recorded.** This work
adds submission documentation and disposable demonstrations, not AMU product
features. Overall Phase 1 remains partial because external authenticated
acceptance has not passed.

## Observed value

One synthetic checkout case was selected and its evaluator frozen before two
fresh agents ran. Both agents passed six requested-fix and four protected-behavior
groups; both selected suites passed 15 tests. The baseline took 100.22 seconds;
plus AMU took 143.35 seconds. AMU produced structured source/dependency/coverage
evidence, but this case showed no speed or correctness advantage. Both found
the same consumers in source. Controls, failures, unavailable measurements and
actual traces are disclosed in [CASE-STUDY.md](value-validation/CASE-STUDY.md).
No replacement scenario or fresh comparison was run after the timebox.

## Verified local package

- The [README](../README.md) explains actual implementation, a real NumPy
  inspection example, limitations and the preserved candidate's test evidence.
- [Fresh isolated setup](recording/submission-setup-private-cache.json) passed
  offline wheel/constraint installation, dependency checks, CLI startup and the
  exact 60-file build identity. Its AMU Python source also matches the frozen
  wheel byte-for-byte: [source comparison](evidence/submission-source-vs-wheel.json).
- The [new-environment replay](recording/runs/submission-environment-replay.json)
  passed the four actual tools and full manifest, all six complete evidence
  sections, an existing ungranted-root denial, stale-cursor refusal, captured
  patch replay, 15 selected tests and the same ten independent groups. It retained
  16 actual wire frames, largest 29,295 bytes including newline.
- All seven commands in the [recording package](recording/RECORDING.md) passed.
  [Actual saved screens](recording/screens/) and
  [natural voiceover](recording/voiceover.txt) work without live ChatGPT or providers.
- [Preservation](evidence/preservation-after-submission.json) passed: original
  Git HEAD/status, 299 existing work files, 12 managed files, 53 pilot Python
  files, 92 candidate files plus its freeze inventory, live-profile hash/mode and
  2,324 NumPy tracked records are unchanged. During that local verification pass,
  no deployment restart, network clone, repository grant, upload or push was performed.

The first offline install failed because the sandbox denied writes to the
installer's shared cache. [That attempt](recording/submission-setup.json) and its
outputs remain intact. A private copy of the existing cached packages fixed the
setup without network access or writes to the shared cache; see the
[cache-copy receipt](submission-cache-copy.json). The comparison itself was not
rerun or changed. Full product suites were not rerun for this documentation-only
package; the frozen candidate retains its 2,065 passed / 12 skipped evidence on
each of the two documented Python versions.

## Next operator action and remaining gates

**Record the demo now:** open a new terminal in this submission copy, press
Shift–Command–5, record only the terminal region, and follow
[RECORDING.md](recording/RECORDING.md). Play the saved 2–3 minute movie before
including it in the submission. [Recording status](recording/recording-status.json)
documents the inaccessible native controls; no completed video/audio is claimed.

This isolated branch preserves the original repository history and working
snapshot. The user has now authorized publishing the complete package to the
existing `fayasfarmisofficial-cyber/TWC-Thugs` GitHub repository. The submission
branch is `hackathon/submission`; the publication receipt is kept separately
from earlier verification evidence. No repository-visibility or live-runtime
change is included, and no completed movie is available to upload.

Authenticated ChatGPT four-tool acceptance and the NumPy `finfo.__new__` intent
scenario remain unexecuted. The NumPy checkout and new frozen environment were
already prepared under the prior scoped authorization; no new clone or grant is
needed. The agreed operator restart in the original credential-equipped terminal
has not occurred. The current submission task excludes live runtime changes.
When external acceptance is resumed, the operator must perform that prepared
restart and confirm replacement/health before the existing client is refreshed
and its checks run. No credential values should be shared.

The earlier [integration handoff](evidence/INTEGRATION-STATUS.md),
[restart/rollback procedure](evidence/RESTART-NOW.md) and
[client checks](evidence/LIVE-CLIENT-CHECKS.md) are preserved historical instructions,
not evidence that a cutover happened. Provider HEAD coverage/readiness remains
incomplete; numerical correctness and runtime safety are unestablished. EHpaIc
remains frozen, and the existing pilot and rollback remain intact.
