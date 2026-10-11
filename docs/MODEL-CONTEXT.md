# Model context and session limits

The planner and interactive assistant select a bounded portion of graph data.
Changed symbols, unresolved boundaries, direct calls, tests, entry points and
import/inheritance neighbors rank ahead of distant supporting rows. Each chosen
row includes its selection reason. `context_selection` records included and
omitted counts so a partial packet cannot appear exhaustive.

Approval verdicts, warnings and resolution summaries are retained. Large raw
fingerprint/baseline records are machine validation inputs and are omitted from
model context with that omission recorded. If required evidence itself is too
large, AMU requests a narrower target instead of cutting the verdict.

## Configure limits

Add a `model_limits` object to the existing `.amu/config.json`:

```json
{
  "model_limits": {
    "context_bytes": 65536,
    "session_input_bytes": 4000000,
    "session_requests": 100
  }
}
```

These are the defaults. Preserve the other repository settings. Unknown keys,
non-integer values and out-of-range settings fail validation. The repository's
outbound-data policy still applies and can impose a smaller request ceiling.

`context_bytes` measures a local serialized request, including system text,
tools, graph context and conversation history. `session_input_bytes` adds that
measure across requests, including repeated history. These are byte limits,
not token limits or a dollar-cost guarantee; provider request formats and
billing vary. The planner has at most two attempts per decision and obeys the
request ceiling. Cumulative session limits apply to the interactive assistant.

## Long conversations

When history exceeds the request ceiling, AMU removes older completed user
turns. A tool call and its results stay together. The active question and its
tool chain are retained; if those alone exceed the ceiling, the request stops.
The model receives current repository context again and a notice that earlier
conversation was omitted. AMU does not invent a summary of removed history.

Failed, interrupted and exhausted tool rounds restore the prior completed
conversation. A failed network attempt still consumes the request/input
allowance because it may have reached the provider. Interrupt with Ctrl-C.
Completed history compaction is retained only when the new turn completes.

`amu model-preview "question" --json` shows sanitized outgoing context without
consuming request allowance. `amu ask "question" --json` includes `usage`;
`/session` shows the current interactive session counters. Provider token counts
are accumulated only when supplied by the provider, with
`usage_reported_requests` indicating coverage. Missing usage is not a zero bill.

## Provider failures

Provider operations use a 60-second timeout and a 4 MiB decoded response limit.
Streaming chat also caps emitted text at 64 KiB. The timeout covers individual
network operations, not the total duration of a stream that keeps sending data.

The SDK's automatic retries are disabled. Non-streaming planner requests can
retry once after a classified connection, timeout, rate-limit or server failure,
with a short delay. Authentication/input errors and oversized responses are not
retried. Interactive streams are not automatically retried after partial output.
Invalid planner JSON can use the same second-attempt allowance. Remote error
bodies, URLs and credentials are withheld from diagnostics.

Tests use fake providers and mocked HTTP transports, including the installed
SDK's streaming path. No paid model request is required for the normal suite.
