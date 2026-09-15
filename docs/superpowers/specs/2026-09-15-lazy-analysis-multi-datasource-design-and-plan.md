# Lazy Analysis Across Datasources: Design and Implementation Plan

Date: 2026-09-15

Status: proposed; design and implementation plan only. No new backend is enabled
by this document. Each backend/method combination requires its own acceptance.

## 1. Outcome and scope

Enable governed lazy Dataset execution on PostgreSQL, MySQL, SQLite, Trino and
ClickHouse, using the current DuckDB implementation as the behavioral baseline.
Keep the existing public flow: construct a Logical Dataset, call `execute()`,
then inspect the Materialized Dataset. Datasource selection continues to come
from authored semantic sources; no public executor argument is introduced.

The deliverable is a tested set of exact source implementations, not a blanket
claim that every Ibis backend supports every Dataset method. Push the maximal
eligible contiguous calculation into the declared source. Use an existing
bounded local method only where its owner admits that exact input shape.
Reject unsupported source-required work before reading source rows.

First delivery targets useful scalar Metric journeys on all five engines.
Advanced methods follow in separately admitted slices. The minimum successful
journey is a scoped observation, grouped aggregation, filtering, deterministic
ranking and Top-N, followed by immutable publication and cold reads. Backend
restrictions on table engines, connectors, types and consistency are part of
the support claim, not hidden implementation assumptions.

### Preserved contracts

- Construction performs no source connection, credential resolution or query.
- Dataset meaning, definition identity, Session ownership and exact binding-hit
  behavior remain unchanged. Physical placement does not enter definition identity.
- One source stage uses one exact datasource binding. Trino catalogs reachable
  through that binding do not authorize rebinding another datasource.
- Selected implementation failures never trigger a different backend, local
  fallback, sampling, approximation, truncation or a second submission.
- Runtime publishes primary rows, all required parts, Evidence and Findings
  atomically. Database tables are not Artifact storage destinations.
- Local/object Parquet remains the configured retention format. The existing
  native DuckDB reader remains available for admitted retained-data computation.
- Source-private membership/distribution state keeps its current restrictions;
  introducing a remote backend does not authorize exporting it through pandas.

Out of scope: generic federation, a cost optimizer, backend auto-discovery,
arbitrary SQL execution through Dataset APIs, persistent remote scratch schemas,
new statistical methods, dependency upgrades unrelated to an admitted adapter,
and automatic rollout to existing projects.

### Authority and amendments

This proposal extends the [planner and pushdown contract](2026-09-01-lazy-analysis-planner-and-pushdown-design.md)
and [materialization Runtime contract](2026-09-01-lazy-analysis-materialization-runtime-design.md).
The Runtime's 2026-09-11 amendment permitting native retained-Parquet scans takes
precedence over older passages prohibiting internal DuckDB Artifact reads.
The [current analysis design](../../specs/analysis/python-analysis-design.md)
owns the public execution and persistence contract.

Upon acceptance, amend those owners for the multi-backend registration,
realization, single-statement validation ordering and remote termination rules
below. Do not change the older
Slice 9 acceptance status or treat this proposal as its completion evidence.

## 2. Verified baseline and required changes

The current `lazy-dataset` checkout supports six datasource engine profiles but
only DuckDB source execution for lazy analysis. The targeted Runtime economics
suite passed on 2026-09-15: one DuckDB success journey and five explicit
non-DuckDB rejections. Those rejection tests do not prove remote execution.

| Existing owner | Current coupling | Required change |
| --- | --- | --- |
| `marivo/datasource/engines/` | Connection, metadata, timezone and authoring timeout profiles already exist for six engines | Reuse physical connection facts; keep lazy operator eligibility in Analysis |
| `analysis/operators/registry.py` | `source_adapter` is one string; source versions default to DuckDB 1.5.3 / Ibis 12.0.0 | Register exact implementations per method and backend without an alternate registry |
| `analysis/compiler/placement.py` | Every source binding includes local DuckDB/Ibis versions | Bind the selected adapter's compatibility facts; preserve same-domain checks |
| `analysis/materialization/admission.py` | DuckDB `Backend` checks, `.con.interrupt()`, `SET`, transactions, schema lookup, temporary tables and batch readers | Route physical operations through typed execution adapters |
| `analysis/materialization/validation.py` | Validation cursor must be `DuckDBPyConnection` | Decode bounded typed validation rows independently of a concrete cursor |
| Statement paths in `materialization/admission.py` and `sampling.py` | `sqlglot`/`sge` rendering with the DuckDB dialect, sampling SQL parsing, `DESCRIBE`, temporary relation SQL, raw scalar checks and `TransactionException` handling | Inventory actual statements and error paths; move dialect behavior into concrete adapters, including diagnostics |
| `analysis/materialization/resources.py` | Execution cleanup is keyed to local DuckDB process lifetime | Add backend-owned remote query/session termination proof |
| `analysis/compiler/lifecycle.py`, `driver_numeric.py`, `distribution.py` | DuckDB SQL, BIGNUM macros, list aggregation and method-specific quantiles | Isolate exact backend lowerings; do not admit these by generic compilation success |
| `analysis/materialization/parquet_scan.py` and retained readers | Native retained scans are DuckDB-oriented | Preserve this domain; explicitly gate any retained-to-remote source binding |

Changing a backend name in the registry is insufficient. The extraction must
cover validation, preparation fences, primary output, retained output, cleanup
and recovery, not just `backend.compile()`.

## 3. Architecture and ownership

```text
Logical Dataset + captured semantic bindings
    -> existing method registry: exact backend eligibility
    -> deterministic placement: source stages / admitted local stages
    -> Runtime: bind and verify execution context and realization
    -> pure Ibis lowering + selected backend compilation
    -> guarded source queries and typed Arrow batches
    -> configured Parquet writer / existing bounded local worker
    -> validation + atomic publication
```

### 3.1 One method registry

Extend the existing `ImplementationRegistration` with a closed collection of
backend-specific registrations. The key remains the owning operator/method;
each backend has at most one admitted implementation for the exact invocation.
This is direct dispatch, not a list of candidate plans.

Each registration supplies concrete typed facts for:

- adapter identity and tested Ibis/driver/server compatibility;
- admitted Dataset shapes, value types and method parameters;
- source builder and, where needed, a declared preparation builder;
- required realization, validation, fence and retained-part capabilities;
- method-owned consistency requirements over exact input roles and contributing
  relations, including whether a common cross-relation snapshot is required;
- output and numerical conformance validators;
- the existing exact local method, independently of source eligibility.

Keep support at semantic-method granularity. Reusable private predicates may
check exact decimals, temporal precision or required window semantics. Do not
build a second inventory of all Ibis expression operations or copy the support
matrix into renderers. An unknown combination is ineligible.

### 3.2 Typed physical execution adapter

Introduce a small private Analysis execution-adapter module, with concrete
implementations per engine. It consumes datasource engine profiles and
`build_backend_with_secrets`-equivalent operation-scoped connection resolution.
Datasource profiles must not import Analysis or learn Dataset methods.

The following table assigns responsibilities, not eight mandatory methods on
one universal protocol. Common connection/schema/compile/transport/lifetime
operations are shared; realization-specific operations use closed variants:

| Operation | Required contract |
| --- | --- |
| Open and verify | Open the declared datasource; verify the selected compatibility scope and required server settings |
| Bind realization | A transaction variant opens its read context; a version-pinned variant resolves exact versions; a single-statement variant binds its read scope into the compiled statement without a separate snapshot call |
| Resolve table/schema | Use datasource-owned qualification and normalize physical types without losing precision |
| Compile | Produce the immutable statement inputs consumed by execution, as specified below, without executing them |
| Read assertions | Return bounded named integer/scalar checks; reject missing, duplicate or malformed results |
| Stream rows | Yield typed bounded Arrow batches and close the actual underlying cursor/response |
| Own fences | A separately admitted fence implementation creates its owned resources after reservation; variants without that capability cannot accept fence-requiring methods |
| Cancel and close | Stop work and return authoritative termination proof or an explicit unresolved state |

Use separate typed result variants for transaction realizations, version-pinned
realizations and single-statement realizations. Do not create a collection of
optional fields that permits an invalid combination. Public exports are unchanged.
Runtime exhaustively dispatches on the selected variant. Transaction and
version-pinned variants consume their respective bound read contexts and
ordered statement bundles; the single-statement variant consumes one envelope
statement and its decoder. Assertion decoding is shared, but its invocation
comes from separate queries or envelope records according to the variant.
There are no no-op snapshot methods, optional fence callbacks or sentinel
transaction handles. Unsupported requirements fail at admission, not by calling
an unimplemented operation after submission.
Concrete engine code may use its driver's APIs; shared Runtime code must not
depend on `.con`, a DuckDB cursor class or dialect-specific SQL.

**Compilation is an execution input.** Each selected statement is compiled once
into a private immutable value containing its exact SQL, typed parameter
bindings, expected schema/decoder, statement role and bound realization handle.
Execution submits that value through the adapter's guarded driver path; it must
not pass the original expression back to an Ibis API that recompiles it. This
applies to assertions, primary/part reads and preparation statements, especially
the single-statement envelope. SQL remains internal and is never an executable
semantic authoring body or a new public surface.

The installed DuckDB Ibis `to_pyarrow_batches()` currently runs pre-execute hooks
and calls `compile()` again. Adapter extraction must account for those hooks,
parameter handling, result conversion and default limits explicitly; bypassing
recompilation must not omit their required behavior. Resource-producing hooks
become declared reserved preparations, never hidden side effects of compiling.
Record the statement actually submitted, with existing redaction. A diagnostic
compile followed by expression-based execution is not sufficient. The compiled
value is action-local, not persisted as a replayable plan or added to identity.

Do not reuse `authoring_timeout` as a drop-in Analysis lifetime context. For
example, the current MySQL authoring helper starts and rolls back a transaction;
nesting it could destroy the Analysis realization. Extract shared low-level
controls only where their owners and lifetimes are identical.

### 3.3 Placement and execution order

1. Honor the existing same-Session binding hit before opening a new source.
2. On a miss, check locally known method, backend, type and shape eligibility.
   Known unsupported source roots retain their pre-Run rejection behavior.
3. Select the complete physical graph, including declared preparation boundaries.
   No source stage may acquire rows from an unregistered local upload path.
4. Admit the Run and reserve execution resources before connecting or submitting.
5. Verify live version, connector/table kind and required controls. A mismatch
   fails the selected path; it does not select another implementation.
6. Bind the selected realization variant per required source domain: establish
   its transaction/version context, or bind the single-statement read scope.
   Related branches share it where their contract requires a common observation.
7. Compile the selected expressions into execution inputs before business-row
   execution. Submit those exact statements in owner-defined order, including
   the variant's separate assertions or single-statement envelope.
8. Validate and publish through the existing writer and recovery protocol.

Catalog/version/settings metadata may be read during step 5 or 6 under Runtime
guards. It must verify a preselected supported scope, not trial analytical
queries to discover a working plan.

### 3.4 Domain identity and compatibility

Keep three facts distinct instead of extending `adapter_versions` into an
all-purpose identity tuple:

- **Source-domain authority:** the captured Session/store/catalog/semantic and
  datasource binding authority currently checked by `same_domain`. Separate
  bindings never become equal because server endpoints or versions match.
- **Implementation compatibility:** the selected adapter implementation and
  locally known Ibis/driver versions determine static eligibility. Live server
  version, connector/table scope and semantic-affecting settings are verified
  after connection against that selection. They are compatibility facts, not
  permission to merge two domains or modify the selected physical graph.
- **Bound realization authority:** contributing statements that require one
  realization share the same owned context/handle. A separate connection to
  the same datasource is insufficient without an admitted shared-read proof.

`same_domain` remains a pure pre-I/O authority check; it must not acquire a live
server version. Replace the existing version-tuple overload with explicit
compatibility validation and post-bind realization checks. Two distinct servers
or bindings remain distinct even at the same version. A version change behind
one declared endpoint does not create a new logical domain, but must pass live
compatibility on a new Run. Conflicting server facts or connection replacement
within one realization fail; they cannot silently replace its handle. Neither
case changes the existing immutable Artifact binding-hit rule.

## 4. Consistent source realization

DuckDB currently evaluates validation, output and required parts inside an owned
transaction, with explicit fences where single evaluation is required. A remote
implementation must preserve the needed consistency even when it cannot use
that mechanism.

Separate SELECTs at ordinary statement isolation can validate data A and publish
data B. A CTE name is not evidence of materialization or one evaluation. A repeated
seed does not prove identical sampling. A partition filter does not prove that a
partition is immutable.

Admit one of the following implementations for an exact method:

- **Transaction realization:** all contributing reads use the same verified
  snapshot/transaction context. Keep it alive through primary and part reads.
- **Version-pinned realization:** resolve immutable physical relation versions
  once and bind every contributing scan to those versions. Expiration or schema
  mismatch fails; never substitute the current version. A vector of table
  snapshots is repeatable, but is not automatically a globally atomic snapshot.
- **Single-statement realization:** lower the supported method, required source
  assertions and permitted outputs into one verified statement/read scope.
  This requires a backend-specific proof of shared source realization, not just
  one SQL string. Statements with independent unpinned scans are ineligible.

For the third variant, validation must still be observed when output is empty
or Top-N removes all offending rows. Use a private typed output envelope with
separate assertion, primary and retained-part records, stripped before writing
public rows. Fully consume and validate the envelope before publication. Stage
bytes remain uncommitted until then. Missing assertion records fail closed.
Each lowerer must demonstrate that envelope branches share the admitted read
basis; neither `UNION ALL` nor CTE reuse alone establishes this guarantee.

This variant changes when some assertions are consumed relative to output
staging. The owning validation contract must explicitly admit that order. A
check that must pass before row transfer still requires a proven server-side
barrier; an eventual failed publication does not satisfy it. The first
ClickHouse slice must identify these checks and prove their ordering before
activation. Source-private state cannot enter the envelope's client row stream.

Single-statement lowering is a bounded backend-specific method implementation,
not a generic query fusion engine. If it cannot preserve all checks and parts,
that shape requires another admitted realization or remains unsupported.

### 4.1 Single-evaluation fences

Repeatable source data does not make volatile expressions repeatable. Every
variant must meet the method owner's single-evaluation requirement for shared
sampling, volatile Population selection or other fenced preparations.

The initial admitted fence implementation is the existing DuckDB owned
connection-scoped resource. Transaction support alone does not grant a fence
capability to PostgreSQL, MySQL or SQLite; each must qualify resource creation,
read-only compatibility, shared consumption, cancellation and cleanup separately.
Initial Trino version-pinned and ClickHouse single-statement registrations have
no fence capability. Their group-A scope is deterministic and fence-free.
Reject a fence-requiring graph before row execution on those registrations.

A later slice may register an equivalent single-evaluation guarantee for either
variant, with explicit method scope and lifetime proof. Immutable snapshot IDs,
CTE reuse and a repeated seed are not that proof. No generic temporary-table
operation is assumed, and no permanent transaction-only restriction is added to
the semantic owner, which already permits an exact fence or equivalent guarantee.

### 4.2 Multi-relation consistency admission

The method owner declares the required consistency over its input roles. Physical
implementations only prove those requirements; they cannot weaken them. For this
proposal the initial rules are:

| Invocation shape | Required read relationship |
| --- | --- |
| One physical relation, including repeated references | The same relation version/read basis for all validation, value and part reads |
| One semantic evaluation spanning relations, including a two-table ratio, membership joins or temporal enrichment | A common snapshot across those relations unless the owning method explicitly establishes a weaker sufficient contract |
| An admitted operation on independently realized Dataset inputs, such as aligned bounded compare | Preserve each input's own realization and the existing alignment contract; do not infer a shared current snapshot from a shared backend |

Compose requirements through the graph without weakening a child's requirement.
Group-A Trino starts with one physical table; group B is not enabled wholesale.
For `numerator(A) / denominator(B)`, independently captured `A@t1` and `B@t2`
are ineligible under the common-snapshot rule, even if both IDs remain readable.
A version-pinned multi-table implementation needs authoritative common-snapshot
evidence, or an explicit owner-approved method contract permitting independent
input realizations. Matching timestamps or resolving IDs close together is not
sufficient evidence. The backend cannot declare tolerance of arbitrary skew.
No new user-facing consistency switch or implicit business-time policy is added.

## 5. Backend-specific delivery scope

The following are proposed activation scopes, not current support claims.
Exact server and driver versions must be frozen from real qualification runs.

| Backend | Initial realization and useful scope | Important restrictions |
| --- | --- | --- |
| DuckDB | Existing source transaction, fences, native retained scans and registered methods | Preserve current results, resource limits and rejection behavior |
| PostgreSQL | Read-only repeatable-read transaction; ordinary relational scalar Metric chains | Verify cursor lifetime within the transaction, exact decimal/time decoding and ordering; no inherited DuckDB macros |
| MySQL | Read-only repeatable-read realization over verified InnoDB tables; scalar Metric chains | No nontransactional tables in that scope; window and join rewrites need exact parity; audit buffering and timeout coverage |
| SQLite | One owned read transaction over one verified database; conservative scalar types and simple Metric chains | Do not advertise exact Decimal or timezone semantics that the declared physical representation cannot supply; interrupt during fetch as well as execute |
| Trino | First qualify one Iceberg connector scope with explicitly pinned table snapshot IDs | Engine name alone is insufficient; do not imply every connector, catalog combination or SQL transaction provides the same guarantees |
| ClickHouse | First qualify single-statement scalar chains on a precisely tested local MergeTree table scope | No assumption of multi-statement snapshot transactions; Distributed tables, dictionaries and independently changing joins require separate qualification |

### Trino

Use the existing datasource table qualification, including catalog/schema/table.
The first implementation resolves the current physical snapshot once per source
table and compiles all relevant scans against its exact ID. This is physical
repeatability, not a replacement for the semantic time scope or a user-selected
historical business snapshot. Section 4.2 governs multi-table admission; the
initial one-table scope cannot be expanded using independently resolved IDs.

Implement version-qualified table expressions through a bounded Ibis backend
extension if the installed Ibis surface cannot express them. Do not regex-rewrite
generated SQL. Failure to implement this seam is a Trino activation blocker.
Other connectors can be added only with an equally explicit realization proof.
Iceberg documents snapshot-ID time travel; actual adapter support still needs
integration tests. [Trino Iceberg time travel](https://trino.io/docs/current/connector/iceberg.html#time-travel-queries)

### ClickHouse

Do not base ordinary support on experimental multi-statement transactions.
Official transaction support has deployment and table-engine restrictions.
[ClickHouse transaction scope](https://clickhouse.com/docs/concepts/features/operations/insert/transactions)

The first qualification spike must demonstrate the single-statement envelope
for a simple sum/count Metric with all required identity and source assertions,
including empty output. It must prove the exact read-sharing behavior under
concurrent writes on the selected server version. Also verify effective JOIN
null behavior, finite-value handling, Decimal decoding and timezone conversion.

If that proof fails, this ClickHouse slice remains blocked. Do not substitute a
quiet test table, skip validations or claim general support from a raw aggregate.
A broader solution using explicitly provisioned source snapshots would need a
separate design amendment; remote persistent scratch tables are outside this plan.

### Transaction-based engines

PostgreSQL repeatable read and MySQL InnoDB consistent reads provide relevant
transaction mechanisms; their normal read-committed behavior is insufficient
for a multi-query realization. SQLite read transactions also require explicit
lifetime ownership. These documented mechanisms guide the implementation, but
do not prove driver streaming or Marivo compatibility.
[PostgreSQL isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
[MySQL consistent reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html),
[SQLite isolation](https://www.sqlite.org/isolation.html)

## 6. Method coverage and semantic portability

| Delivery group | Target scope | Admission evidence |
| --- | --- | --- |
| A: basic source analysis | Unversioned supported Entity/Metric shapes; sum/count/min/max; scoped filters, dimensions, aggregation, projection, rank and limit | Identity, null/empty rules, exact output types, deterministic tie ordering and source-side reduction |
| B: relational and temporal analysis | Relationships, ratio/mean/weighted mean, time buckets, version resolution and supported comparison/attribution arithmetic | Fanout, temporal anchors, spatial-before-temporal folds and atomic sufficient-state parts |
| C: admitted local suffixes | Forecast, Kendall, time-based discovery and existing non-Entity local methods after supported source preparation | Exact complete inputs, alignment, combined budgets and no raw semantic-source collection |
| D: advanced source methods | Exact distinct state, quantiles, Entity candidates, driver screening, Event and Lifecycle | Per-method private-state, numerical, single-evaluation and retained-continuation proofs |

Groups are dependency scopes, not backend-wide capability flags. Every group-A
case must state its accepted type and row shape. Group A does not automatically
admit arbitrary Metric bodies or all join paths. Shared compiler code is reused
only where it expresses the same semantics on the target backend.

Specific risks requiring fixtures and explicit registration:

- Exact versus approximate distinct and quantile methods. Existing datasource
  `quantile` metadata is not permission to replace a requested exact method.
  `duckdb_tdigest@v1` stays DuckDB-specific unless its owner admits an equivalent
  implementation; a similarly named remote aggregate is not sufficient.
- Decimal precision/scale, integer division and overflow, floating cancellation,
  NaN/infinity, null-safe equality and engine collation. Define comparison
  tolerances by the existing method contract, not to make a failing backend pass.
- Explicit NULL order and tie keys in rankings; set versus multiset semantics;
  full/semi/anti join equivalence where backend syntax differs.
- Timestamp precision, UTC instant versus civil time, DST folds/gaps, timezone
  parser behavior and half-open time windows. Reuse the temporal owner.
- Private retained membership/distribution relations, ordered list/struct
  construction, quantile reproduction and contribution-state integrity.
- Driver numerical macros currently use DuckDB BIGNUM to preserve binary64
  summation semantics. Ordinary remote `SUM(double)` is not an equivalent port.
- Event matching, sampling and Lifecycle replay require exact ordering and
  single-evaluation boundaries; successful SQL compilation does not prove them.

Source-private methods remain disabled on a new backend until their existing
retention and native read contract can be met without a forbidden transfer.
Do not export raw private state into local DuckDB merely to increase coverage.
Any required change to that authority belongs in a separate owner amendment.

## 7. Streaming, budgets and remote lifetime

### Bounded transport

Keep server execution budgets separate from local transfer and worker budgets.
DuckDB's memory and spill settings cannot simply be issued on another engine.
Each adapter must specify which server controls it enforces and how the existing
Runtime deadline bounds connection, metadata, execution, page fetch, decoding,
output consumption and cancellation. Missing required enforcement rejects
admission. Server resource metrics that cannot be measured remain unavailable.

`to_pyarrow_batches(chunk_size=...)` is not proof that the driver avoids buffering
the full result or an oversized response page. Qualify the actual driver path;
use an engine-native cursor/stream when needed, with strict Arrow normalization.
Bound response/decode allocations, row and cell sizes, total transferred bytes,
and local worker peak memory. Late detection after an unbounded allocation does
not satisfy the guard. Test empty, multi-batch and oversized single-cell results.

Audit the current `_batch_rows` width probe before porting it: it can inspect
source strings outside the final result. Probes must preserve the required source
scope and have their own execution bounds. A width/count query is neither a free
operation nor a substitute for bounded transport. Keep validation queries
separate by default, preserving the existing protection against large unions.

Use one shared sizing policy with typed adapter-supplied transport limits.
Fixed-width Arrow types provide a conservative baseline; variable-width and
nested values additionally require enforced cell/page/decode limits from the
qualified transport or trustworthy physical bounds. Arrow schema, average row
size and an unverified author declaration cannot bound arbitrary strings.
Use a scoped width probe only when it is explicitly part of the selected
adapter/method preparation and its cost, realization and allocation bounds are
proven. If neither bounded transport nor such a qualified path exists, reject
that type/scope. Do not copy an unrestricted source-table scan into each adapter.

### Cancellation and recovery

Local PID death proves local DuckDB termination; it does not prove a remote
query stopped. Reserve an owned resource before submission. Bind a safe remote
query/session identity and server authority to that reservation as soon as the
protocol permits. Never persist credentials, token-bearing result URLs or raw
connection strings in the journal or Evidence.

The adapter must close the submit/acknowledgement crash window: use a provably
correlated owned request identity and recovery lookup, or another tested server
lifetime fence. Server-assigned IDs learned only after an acknowledgement are
insufficient by themselves. If recovery cannot identify or prove termination,
retain `RecoveryPendingError` for the affected Session and publish nothing.

Cancel the exact owned operation; do not kill sessions by username, broad SQL
matching or reused numeric IDs. Connection close, HTTP timeout, a cancel request
acknowledgement and local worker death are not universally terminal proof.
Use the server/driver's terminal protocol and verify it in crash tests.
For example, Trino exposes paginated result and cancellation operations, whose
full lifecycle must be handled. [Trino client protocol](https://trino.io/docs/current/develop/client-protocol.html)

The selected action is never resubmitted after ambiguous submission. Audit
driver-level retries as well as Marivo retries. Any bounded transport retry must
be demonstrably part of the same owned query; otherwise disable it. Incomplete
transfer, cancellation and schema drift leave no successful Artifact or cache hit.

## 8. Retained inputs, identity and disclosure

The existing `admitted_binding` shortcut assigns a sole source candidate to a
Parquet Artifact. Replace that assumption with an explicit native-reader
capability check. New remote adapters initially have no retained-Parquet import
capability. Do not upload a local result to make a remote source-only method work.

Retained-only operations may continue through the existing DuckDB Parquet domain
or admitted local worker. Mixed remote-source/Artifact operands use an existing
local multi-input method only when complete input and authority checks pass;
otherwise they are rejected. Cross-Session and private-state restrictions remain.

Keep `execution_key(definition_fingerprint)` unchanged. Backend versions and
realization facts explain a newly executed Run; they do not invalidate an already
bound immutable Artifact or become a second physical-plan fingerprint. Store
only the bounded provenance needed to explain the selected implementation and
recover its resources, not an executable query graph. Any new persisted field
or cleanup capability must follow the existing store-format version policy;
do not silently migrate an older store or introduce dual-read compatibility.

Expose support through existing owners: static Help for available backend
boundaries, `contract()` for currently knowable continuations, and structured
errors for the concrete unsupported invocation. Construction-time cards cannot
claim live connector or server verification. Error fields should identify the
operator/method, backend, known type/shape restriction and required capability,
with an actionable existing repair. Never recommend raw SQL as a way to re-enter
governed Dataset analysis.

## 9. Implementation slices

Slices are ordered by dependency. Each ends with a reviewable change and its
evidence. PostgreSQL establishes the transaction adapter pattern; Trino and
ClickHouse feasibility is investigated early to avoid discovering a fundamental
blocker after generic infrastructure has been built.

| Slice | Work and owning files/modules | Exit criteria |
| --- | --- | --- |
| 0: freeze qualification scope | Existing registry, compiler/Runtime inventory and focused backend fixtures; no activation | Inventory every current method/shape; freeze exact versions; prove Trino version-qualified lowering and ClickHouse assertion envelope feasibility; record unresolved blockers |
| 1: extract DuckDB adapter | `materialization/admission.py`, `validation.py`, `resources.py`; new private execution adapter module | Existing DuckDB source, native Parquet, local-suffix, cancellation and publication behavior unchanged; shared code no longer requires DuckDB cursor operations |
| 2: extend exact dispatch | `operators/registry.py`, `compiler/placement.py`, source binding and retained binding admission | Multiple closed backend registrations supported; only DuckDB enabled; unknown backend/type/version cases fail at the correct phase; binding hits remain source-free |
| 3: PostgreSQL group A | New PostgreSQL execution adapter; focused datasource control reuse; shared scalar lowering | Real transactional group-A success, source mutation isolation, bounded streaming, cancellation/recovery and cold Artifact reads |
| 4: MySQL and SQLite group A | Separate concrete adapters and per-engine tests | Same group-A contract under each declared physical-type/table scope; no inherited authoring transaction bugs or driver buffering |
| 5: Trino group A | Trino execution adapter, bounded version-qualified Ibis lowering and journal recovery integration | Real Iceberg-backed source reduction with fixed snapshot IDs, multi-page transfer and remote termination; snapshot expiry fails closed |
| 6: ClickHouse group A | ClickHouse adapter and exact single-statement method lowerer | All assertions and primary output share the qualified read basis; concurrent mutation, empty output and cancellation tests pass; no unproved transactional assumptions |
| 7: expand supported methods | Owning temporal, relationship, retained, correlation and other method modules | Group B/C entries enabled individually; group D enabled only after its additional semantic/private-state proofs; every remaining entry has an explicit unsupported reason |
| 8: consolidated disclosure and packaged acceptance | Native Help/registry tests, affected specs, packaged skills, CLI examples and English/Chinese latest site docs | Installed-package journeys match advertised support; full broad gate green; backend matrix distinguishes live success from rejection-only evidence |

Slice 0 must produce a bounded inventory of statement rendering/parsing,
metadata and scalar queries, transaction exceptions, pre-execute hooks and
compile-to-submit paths, including sampling and retained-part helpers. For each
realization variant, record its actual operations, fence support, method-owned
cross-relation requirement, compatibility verification and transport sizing
strategy. Slice 1 must demonstrate that DuckDB dialect and exception handling
remain only in concrete DuckDB paths; changing type annotations is insufficient.
Slice 2 must test domain authority separately from version compatibility and
realization identity. These are exit checks, not optional later cleanup.

Slice 0 progress is recorded in the
[qualification inventory](2026-09-15-multisource-slice-0-qualification.md).
Slice 0 is complete for its frozen inventory and live feasibility scope, including
visitor-generated Trino snapshot reads/expiry and the ClickHouse assertion envelope
under concurrent writes. Remaining activation gates (including validation ordering,
production transport and recovery) are assigned to their enabling slices in that
inventory. Non-DuckDB execution remains disabled.

Do not combine backend activation with unrelated compiler cleanup. New optional
dependency constraints must be justified by the tested adapter and recorded in
its slice. Each enabling slice updates affected Help, dynamic guidance, drift
tests, examples, skills and English/Chinese documentation in the same change;
Slice 8 consolidates and verifies them rather than postponing that obligation.
Fixture changes follow the repository's `marivo-test-fixtures` skill
when implementation begins. No runtime fixtures or services are started by this
documentation task.

## 10. Acceptance matrix and evidence

Every enabled backend/method entry requires the following applicable checks:

1. **No-I/O and placement:** construction makes no connection; known unsupported
   roots fail before Run admission; compatible chains form maximal source stages;
   unknown live compatibility fails without business-row queries or replanning.
2. **Independent semantics:** expected values come from small explicit fixtures
   or independent arithmetic, with DuckDB as an additional comparator. Cover
   null/empty data, duplicates, skew, fanout, temporal boundaries, ranking ties,
   type extremes and required retained-state folds.
3. **Actual pushdown:** inspect captured executed SQL and transfer counters.
   For the group-A Top-N fixture, one primary source query returns at most N
   public rows. Count assertions, metadata, preparation and private parts
   separately; do not label the whole Run as one query when it is not.
4. **Remote economics:** run a bounded large-source/small-output fixture; prove
   source filtering and aggregation rather than local raw-row collection. Record
   server scan/partition metrics where available and all known extra probes.
   SQL containing a predicate is not proof of physical partition pruning.
5. **Realization integrity:** mutate the source between validation and primary
   reads, and between primary and part reads. Preserve one admitted realization
   or fail with no publication. Test snapshot expiry and single-statement empty
   output; a zero-row primary result must not bypass failed assertions.
6. **Transport and lifetime:** exercise slow execution, blocked/slow fetch,
   malformed or oversized pages, disconnect, user cancellation, process kill
   before/after submit acknowledgement and lost cancellation acknowledgements.
   Observe terminal remote state or the correct recovery block.
7. **Publication and reuse:** interrupt at each writer boundary; verify atomic
   primary/parts metadata, cold-process readback, missing-source cache hits,
   immutable retained continuation, exact ownership and no private-state leakage.
8. **Negative matrix:** every unsupported backend/type/shape/method combination
   has bounded structured rejection. Replace each current blanket non-DuckDB
   rejection only when the corresponding positive entry is proven; retain all
   other negative cases.
9. **Compile-to-submit identity:** intercept the actual adapter submission and
   compare it with the compiled SQL and bound parameters; reject any second
   lowering. Cover default-limit behavior, reserved pre-execute preparations,
   assertions, parts and the single-statement envelope, not only primary rows.
10. **New admission boundaries:** reject sampled/fenced work on variants without
    that capability and reject a two-table ratio over independent snapshots.
    Keep a separately admitted comparison of immutable inputs valid. Test equal
    versions on distinct bindings, server-version mismatch after connection and
    a replacement connection during one realization; none may merge authority
    or replan. Exercise wide/nested results through the shared sizing policy.

Record exact server, connector/table engine, Ibis and driver versions; fixture
identity; enabled method scope; statements by role; transferred rows/bytes;
termination receipts; and commands/outcomes. Distinguish unavailable metrics
from zero. A compile-only test, datasource `test()`, raw SQL success or a synthetic
adapter is not live lazy Dataset acceptance.

Use focused unit tests first, then relevant `make runtime-test TESTS='...'`
backend tests. Use `make typecheck TYPECHECK_TARGETS='...'` and
`make lint-agent LINT_TARGETS='...'` during implementation; run `make check-agent`
for each shared-behavior milestone. Run resource-heavy backend gates serially.
Full release Runtime acceptance and object-store services belong to the later
release workflow, not ordinary documentation or commit preparation.

## 11. Completion and remaining decisions

Initial multi-datasource delivery is complete only when each of the five new
backends has a real successful group-A Dataset journey within its explicitly
advertised scope, with streaming, consistency, termination, publication and
installed-package evidence. A backend blocked at Slice 0 or later remains
unavailable; the overall five-backend objective is then incomplete.

Full DuckDB feature parity is a separate milestone. Group-D restrictions must
remain visible and must not be described as completed multi-engine parity.

The implementation must settle these bounded questions before the affected
activation, rather than add speculative abstractions now:

- Exact server/driver versions available for reproducible qualification.
- Whether the installed Ibis Trino backend can express version-pinned scans or
  needs the proposed small compiler extension.
- Whether the ClickHouse single-statement read-sharing and assertion envelope
  can meet the current source validation contract on the selected table scope.
- The concrete per-driver submit/acknowledgement recovery and bounded decode
  mechanisms, including any required server permissions.
- Which advanced retained-state contracts can be met without changing their
  source-private authority. Unmet contracts keep those methods disabled.

These are qualification blockers for specific slices. They do not authorize
weaker semantics, an implicit data upload or an alternate execution route.
