# Viewer accessibility pass

Scope: the static map card and relation graph. The existing dark/gold design and
candidate-only provenance warnings are preserved. These pages do not approve or
save repository changes.

## Barriers addressed

| Barrier | Change |
| --- | --- |
| Placeholder-only JSON fields and unlabeled file input | Visible, associated labels |
| Mouse-only graph inspection | Native node selector, text relationships, focusable nodes; Enter/Space opens details |
| Drag-only positioning and pan/zoom | Arrow keys move focused nodes; labeled pan, zoom and reset buttons |
| Automatic graph movement | Layout settles before display; dragging and keyboard movement remain direct |
| White-on-gold card buttons | Dark foreground, shared focus outline and clearer muted text |
| Alert dialogs and unannounced changes | Inline errors linked to inputs, focus returns to invalid input, concise status announcements |
| JSON disclosure without exposed state | `aria-expanded` and `aria-controls` |
| Narrow-screen overflow | Stacked graph controls, two-column metrics, keyboard-scrollable labeled table region |
| Oversized or malformed payloads | Shared 4 MiB/64-level/100,000-value limits; finite numbers, array schemas, unique IDs and valid edge endpoints; at most 1,000 nodes/5,000 edges or ripples |
| Old file reads replacing newer input | File size checked before reading, with request epochs cancelling stale file promises after another file, manual edit or render |
| A rejected input looking like an updated result | Input is preserved and retained results are explicitly labeled previous data |
| Large force layouts or lost relationships | Above 250 nodes/1,500 edges, the diagram is disabled explicitly; every accepted node and relationship remains in the text inspector |
| Remote diagram dependency and inline scripts | Local unmodified D3 7.9.0 with its ISC notice; external scripts and restrictive CSP, including disabled connections |

## Verification

In-app browser checks exercised invalid JSON recovery, Render/Answer/disclosure
by keyboard, native node selection, relationship text, and keyboard zoom. Card
and graph documents had no horizontal overflow at a 375px viewport. The graph's
text alternative remains independent of D3 loading. The card retains text-only
handling of untrusted payloads, and the graph detail panel also uses text content.

Automated tests: from `web`, run `npm ci`, `npx playwright install chromium`, then
`npm test`. Exact test dependencies are recorded in `package-lock.json`. Tests
cover desktop/mobile, reduced-motion preference, keyboard interaction, validation,
the diagram-library failure path, and axe WCAG A/AA checks. Tests also cover input
schema/resource limits, hostile text, file-read errors/races, no uploads and the
complete large-graph text fallback. D3 is served from the vendored local asset,
not a CDN or test-only replacement. The workflow targets Linux separately from
Python tests; no remote run is claimed here.

On 9 October 2026, **30 desktop/mobile executions passed** locally using pinned
Playwright 1.63.0 and Chromium 153.0.8010.12 (build 1243) on macOS arm64. Normal
sandbox launch was denied before any page opened; the passing run used approved
local browser-launch access. Both card/graph desktop/mobile captures were visually
inspected, preserving the existing design. The UI hardening detector reported no
findings. This does not replace VoiceOver/NVDA testing, Firefox/WebKit coverage,
performance profiling across devices or a full WCAG conformance audit. Bounds
limit work; they do not guarantee latency on every device.
