# Record the working AMU demonstration

**The recording package is ready; no video file has been produced.** The local
demonstration passed. Native recording controls were inaccessible in this
desktop session: QuickTime remained in its Open dialog after dismissal attempts,
and the recording shortcut exposed no controllable recording toolbar. No screen
recording or audio recording started. This is a concrete capture limitation,
not a claim that macOS lacks recording support.

The main story is the verified local workflow. The comparison is a short, honest
supporting result: both agents succeeded, the baseline was faster, and AMU
structured information the agents also found in source. No live ChatGPT
connection, deployment restart or provider/model network request is needed for
the saved-output presentation.

## Immediate recording setup

1. Open a **new terminal** in the isolated submission copy. Keep the existing
   credential-equipped tunnel terminal untouched. Use a large readable font,
   hide unrelated windows, and record only this terminal region.
2. Press **Shift–Command–5**, choose **Record Selected Portion**, select the
   terminal, and select your microphone if you will read the voiceover. Save
   into this `hackathon/recording/` folder. Recording requires the operator's
   desktop action because the recording controls were inaccessible to this agent.
3. Use the commands below in order, pausing on the important output while reading
   [voiceover.txt](voiceover.txt). The target duration is **2 minutes 40 seconds**.
4. Stop recording, play the saved movie, and check readable output, audible
   narration and a 2–3 minute duration. Rename the **actual** verified movie to
   `AMU-demo.mov`. Only then add a file/link to the submission. Uploading and
   publishing remain separate actions requiring explicit authorization.

No movie named `AMU-demo.mov` is currently included. The voiceover is a script,
not recorded speech. Do not claim a hosted video URL until an upload exists.

## Exact command order for this machine

Setup once, outside the recording. This local environment is created only in
the submission copy; it is not the live deployment environment.

```sh
cd /Users/Abslock128/Documents/ChatGPT/AMU/value-validation-2026-10-10.cn6u7g7p/submission-repository
```

The verified `.venv-demo` environment is available on this machine. For another
machine, follow the exact wheel/constraint install in the repository README,
then substitute `.venv/bin/python` for `.venv-demo/bin/python` below. The saved
presentation helper uses only Python's standard library; `python3` also works
without AMU or graph providers installed.

During recording, run these seven commands in order. They read actual saved
evidence; the terminal labels it as such. They do not simulate a new agent run.

```sh
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py bug
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py graph
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py impact
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py patch
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py tests
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py comparison
.venv-demo/bin/python -I -B hackathon/recording/show_evidence.py limits
```

## Storyboard and real output

| Time | Screen | Narration / point to show |
| --- | --- | --- |
| 0:00–0:25 | `bug`: real original source, observed 2 cents, original eight tests green | One shared function, a small rounding bug, downstream consequences. Synthetic fixture, not a production payment system. |
| 0:25–0:55 | `graph`: actual four shop nodes and source references | Quote and checkout call pricing; receipt uses quote. Raw edge provenance is unknown. Relationships identify investigation targets. |
| 0:55–1:15 | `impact`: six complete sections and stale-cursor refusal | AMU supplies intent-based evidence and coverage; safety remains unknown. Ten potential-impact entries are not ten proven regressions. |
| 1:15–1:35 | `patch`: actual agent-produced integer arithmetic and corrected total | The agent chooses the edit; AMU does not generate or semantically verify this patch. Seven tests were added. |
| 1:35–2:00 | `tests`: actual 15-test result and ten frozen evaluator groups | Executed tests verify requested and protected behavior. Four-tool manifest, authorization denial and freshness checks passed locally. |
| 2:00–2:25 | `comparison`: actual timings and setup overhead | Both agents passed. Baseline 100.22 s; plus AMU 143.35 s. Structured evidence, no observed performance advantage. |
| 2:25–2:40 | `limits`: scope and evidence attribution | One illustrative case with disclosed control differences. Frozen local candidate; external authenticated acceptance open. |

Natural voiceover is in [voiceover.txt](voiceover.txt). Adjust pauses to the target
duration; do not change the numbers or omit the outcome that the baseline was faster.

## Optional actual local replay before recording

If native providers are available, this executes all four MCP tools over their
real stdio transport, checks the frozen build/manifest, retrieves complete
evidence, checks exact-root authorization, applies the saved treatment patch
only to a disposable fixture, refuses the stale cursor and runs both suites:

```sh
.venv-demo/bin/python -I -B hackathon/replay_demo.py \
  --entire /opt/homebrew/Caskroom/entire/0.10.5/entire \
  --graph-plugin /Users/Abslock128/go/bin/entire-graph \
  --output hackathon/recording/runs/operator-replay.json
```

Use a new receipt filename for each replay; existing evidence is never
overwritten. This is a replay of a captured patch, not another comparison or an
authenticated ChatGPT acceptance check. It does not import/build NumPy, change
the live MCP profile, restart the tunnel or access credentials.

Actual verified receipts: [original replay](runs/verified-replay.json),
[original output](verified-replay.stdout), and the new-environment replay/setup
receipts linked from [submission status](../SUBMISSION-STATUS.md). The original
replay retained 16 wire frames with a largest response of **29,295 bytes**,
below the 131,072-byte boundary.

## Connectivity-free fallback

Run the seven `show_evidence.py` commands above with any Python 3.11+ interpreter.
They use only checked-in fixture source, the recorded transport receipt, actual
patch and comparison outputs. If terminal presentation fails, open
[verified-replay.stdout](verified-replay.stdout),
[the patch](../value-validation/treatment.patch),
[independent results](../value-validation/treatment/evaluation/independent.stdout)
and [the case study](../value-validation/CASE-STUDY.md) in that order. Clearly say
that these are recorded local outputs. No live ChatGPT connectivity is required.
