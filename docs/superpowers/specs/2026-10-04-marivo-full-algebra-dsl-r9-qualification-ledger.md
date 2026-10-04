# R9.1 scenario qualification freeze

Date: 2026-10-04

Authority: static only. The [machine index](2026-10-04-marivo-r9-evidence/index.json)
owns the frozen IDs, counts and shard hashes. The [R9 implementation plan §2.1.1](2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md#211-已接受的-r91-场景粒度修订2026-10-04)
records the user's accepted method-family and critical-risk granularity.

## Coverage responsibility

All 9,109 R8.6 seed hashes are checked and retained. Many seeds map to the same
method scenarios; mapping is traceability, not proof that all seeds are qualified.
Their exact input types/domains, key/time profiles, numeric policy and parts remain
available to construct representative fixtures and investigate counterexamples.
The catalogue does not replicate every seed across every backend and form.

Each of the nine statistical methods has six source-capture goals, shared numeric,
identity/domain/state and algorithm risks, and one fixed continuation/cold/exact-hit
goal. The R0 matrix separately retains mandatory native/preparation and terminal
routes for the other capability families. Shared algorithm assertions need not be
repeated at every backend; real capture, ordered inputs and output binding still
need method-specific evidence on each backend.

Special source forms have independent metadata/read/decode/range/empty/close/fault
goals: DuckDB file formats and HTTP authentication, PostgreSQL namespace collisions,
MySQL views, SQLite main tables/views, Trino connector differences and ClickHouse
Distributed topology. Per-backend numeric/time/composite-identity risks are separate.
When an actual backend/form changes semantics, decoding or resources, append a
precise counterexample goal instead of assuming the shared test qualifies it.

Fixed goals have no execution backend. Every actual producer still needs its own
schema, receipt, parts and offline binding evidence, attached under the shared
goal. A single producer's success does not qualify other producer profiles.

DS01–DS22, AN01–AN33, V01–V17 and pre-Run/pre-read refusals remain explicit.
Six source cost baselines retain both source routes at 1k/100k with warm-up and
three samples; fixed kernel/exact-hit costs are separate. Algorithm and pressure
cost scenarios are shared goals, with applicable route/environment samples bound
by R9.6. No cost tool execution, speed claim or capacity gate is introduced here.

## Source mapping and consumers

Original owner table rows retain path, line and raw-line hash. Capability rows map
to current goals; other historical rows remain references outside the denominator.
Historical text is provenance, not current authorization or passed qualification.
Stable scenario IDs contain family, backend, profile, route and case. Later scope
changes must retain the previous accepted bundle and publish ID differences with
accepted basis. The 352,049-record draft was never an accepted denominator.

The actual connected method semantics and builtin implementation declarations are
snapshotted, including checks, parts, precision, resource ownership, contract versions
and declaration authority. They are static product declarations, not R9 passes.
The test-function inventory is likewise a location aid, not proof of cell assertions.

Exact consumer/test/oracle/environment bindings are explicit unverified gaps with
R9.2–R9.6 owners. Deployment details such as the Trino non-Iceberg connector and
ClickHouse shard/replica observations remain unresolved. Missing routes cannot be
reclassified as permanent unsupported capability. Unknown service availability is
not reported as an observed failed connection.

## Evidence and initial state

All scenario goals start unverified; executed=passed=failed=blocked=0. Per-goal
proof obligations avoid demanding cost measurements for a refusal or source SQL
for a fixed kernel. Separate result indexes bind candidate/owner digests, exact
collected test owners, commands, input/oracle/environment, timestamps and hashed
proof attachments. The verifier rejects unknown IDs, incomplete bindings, false
static/compile/skip Runtime passes and success versus rejection confusion. It checks
structure and authority; independent test assertions own numerical conclusions.

The initial source HEAD is `68a35c03e3db49d919c223035bb84d4f0cbab2b0` on `panda`.
The candidate also binds current source/test/script hashes, dirty diff, untracked
additions, owner inputs and installed site-packages dependency versions. Commit
metadata alone does not invalidate identical content; owner, implementation or
dependency changes require new evidence and impact review. No installed wheel,
real Agent, remote Runtime or release acceptance is granted by this freeze.
