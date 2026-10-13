# Integrated static analysis

The patch report uses one source identity index for runtime implementations,
recognized overload declarations, imports/reexports, methods, property accessors,
call sites and supported constructed receivers. A source identity is not a claim
that an arbitrary runtime object dispatches to that implementation. Unknown
receiver/factory types, unsupported class hierarchies and namespace mutation abstain.

The existing graph runs before flow analysis and recommendation composition. Its
incoming CALLS entries become candidates in the same identity namespace. Source
resolution validates candidates; source-unverified display edges are downgraded.
They do not create value propagation, regression protection or safety evidence.

Dependency-driven summaries describe returns, tuple/literal dictionary slots,
ordinary object writes/reads, supported property setters, and exact exception routes.
Joins retain collection dependence and bounded alternative paths; differing slots
or receiver types stay unresolved. Scalar string/f-string operations use explicit
Python contracts. These models assume stable builtins and supported scalar semantics;
arbitrary descriptors, external methods and scalar customization remain boundaries.

Unshadowed literal pytest parametrization and `pytest.param(..., id=...)` metadata
are inspected without collection. A bounded matcher can bind baseline literal
parameters to a changed return-controlling condition. This means one baseline
assertion is relevant to one condition side. It supplies no candidate-derived
business expectation, executed reachability, observed coverage or regression proof.
Dynamic fixture factories, arbitrary marker metadata, framework invocation and
unmodeled lifecycle hooks remain unresolved.

Evidence levels stay distinct: broad reference; resolved source call; supported
value/state use; assertion on a related result; assertion relevant to a changed
condition. Execution and demonstrated detection remain separate future evidence.
Broad references retain full inspection records but are excluded from the primary
summary and tagged `primary_verification=false`. Source-wide uncertainties remain
retrievable; path-specific guards and unresolved endpoints accompany useful paths.

Report snapshots are private JSON, bound to source contents/inventory/HEAD, role
configuration, runtime limits, analyzer code, Python/dependencies and provider binary,
plugin discovery and worktree configuration. Reads revalidate bindings without graph
or flow recomputation. Unsafe/corrupt/missing snapshots fall back to secure analysis;
stale cursors are refused. Retention: at most 16 reports, 64 MiB total, 16 MiB per
report and one hour. Default storage is a private temporary directory; `AMU_HOME`
overrides it. This is report reuse, not a persistent semantic/type oracle.
