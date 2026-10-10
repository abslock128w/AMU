# Authenticated ChatGPT acceptance prompt

Use only after the user reports the doctor-validated restart running and the
served process/build/main channel have been correlated. Refresh the existing
AMU connection, verify four advertised tool schemas/annotations, and start a new
conversation with AMU selected. This prompt performs inspections only. Preserve
actual tool cards/arguments/results, not just the final model-written summary.

Paste:

> Use the connected AMU inspection tools for the approved official NumPy root
> `/Users/Abslock128/Documents/Codex/2026-09-15/hey-so-this-is-a-project-2/integration-fixtures/numpy-v2.3.0`.
> This is an acceptance check, not permission to change source, execute a patch,
> import/build/test NumPy, initialize AMU state, enable actions or expand grants.
> First call `amu_search` with `query: "finfo.__new__"` and that explicit `repo`.
> The independently verified source is `numpy/_core/getlimits.py`, line 494,
> commit `0532af47d6a815298b7841de00bdbc547104b237`, file SHA-256
> `df641eeed94115dca20ef7528c6d5ca766b4349d2b48cc5e0d18a3dda8223eb8`.
> Confirm the returned source identity. Call `amu_graph` with
> `target: "numpy/_core/getlimits.py#finfo.__new__"`, `depth: 2`,
> `relation: "CALLS"` and the same `repo`; check direction and uncertainties.
> Call `amu_impact` with `file: "numpy/_core/getlimits.py"`, `line: 494`,
> `proposed_change: "change when dtype-specific finfo cache entries are reused"`,
> `depth: 2`, `page_size: 25` and the same `repo`. Retrieve every section:
> `impact`, `source_contradictions`, `source_uncertainties`, `dynamic_boundaries`,
> `coverage`, `reasons`. Follow any returned cursors to completion, keeping the
> same report hash and checking offsets, totals, all evidence and fragment hashes.
> Do not omit populated sections or substitute a model summary for unreturned
> evidence. Call `amu_docs_reviews` with that same `repo`; omit optional `preview`.
> Record its genuine queue/error. Keep `safe_to_change` unknown; the initial
> local report found seven potential impacts, two source uncertainties, twelve
> dynamic boundaries, ten coverage items, five reasons and zero contradictions.
> Different current counts must be explained from actual evidence, not copied.
> Report all selected tool names, exact arguments, results, limitations and
> authorization behavior. The committed HEAD cache is incomplete and repository
> readiness is unestablished; working current-source inspection is not proof of
> full index readiness, runtime safety or numerical correctness.

Then call `amu_docs_reviews` with the explicit `repo`
`/Users/Abslock128/Documents/ChatGPT/AMU/phase1-integration-2026-10-10.9tr7d2hw/client-denial-fixture`.
This existing harmless Git fixture was prepared and recorded in
`client-denial-fixture.json`; it is deliberately ungranted. Expect
`REPOSITORY_NOT_AUTHORIZED`, not an invalid-path refusal. Check
missing-root schema refusal and that action tools are absent. Do not use any
rejected/private root as a negative test.

Acceptance also requires served package/build/manifest correlation, actual
transport bounds, source/docs/managed-state preservation, and complete raw
tool-result evidence. If the client cannot expose or perform the required
calls/evidence, the gate remains open and the local demonstration takes priority.
