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
Simple source-declared pytest return fixtures can bind test arguments to exact
constructed receivers, supported returned values, and other return fixtures.
Module-local declarations precede ancestor conftest declarations. Unsupported
nearest declarations and imported aliases block fallback. Direct parametrized
arguments override fixtures; arguments with defaults are not fixture requests.
Dependencies share an allocation within one test,
so escape through an alias invalidates the same receiver. Factory and initializer
dependencies participate in summary recomputation, with located constructor,
factory and fixture-binding provenance attached to receiver evidence.

This model supports unshadowed `pytest.fixture` and explicit imported aliases,
default/function scope, literal names and non-autouse metadata, and straight-line
assignments followed by one return. It is bounded to 128 fixture declarations,
eight dependency hops, and the existing three factory-summary hops. Ambiguous
factories, unsupported initializer effects/exceptions, generator teardown,
class/inherited fixtures, implicit setup, plugins, arbitrary marker metadata and
framework invocation remain unresolved. Every requested fixture must resolve
before any fixture receiver is promoted. No collection or fixture body executes.

Pytest configuration (`pyproject.toml`, `pytest.ini`, `setup.cfg`, `tox.ini`) is
read as metadata and bound to report freshness, including absent-file identities.
Unmodeled `usefixtures`, `addopts`, required plugins, or pytest plugin/addopts
environment settings disable fixture binding. These checks do not model installed
third-party plugin behavior; evidence assumes no unmodeled runtime mutation and
does not establish that an assertion executes or detects the proposed change.

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
