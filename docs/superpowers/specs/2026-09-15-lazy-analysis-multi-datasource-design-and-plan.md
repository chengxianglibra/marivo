# Lazy Analysis Across Datasources: Design and Implementation Plan

Date: 2026-09-15

Status: bounded delivery through Slice 8 complete, including corrected Slice 1d.
PostgreSQL, MySQL, SQLite, Trino Iceberg and ordinary local ClickHouse MergeTree
are enabled within their separately verified scalar, relational and native-date
scopes. See [Slice 6 ClickHouse acceptance](2026-09-16-multisource-slice-6-acceptance.md),
[Slice 7 method acceptance](2026-09-16-multisource-slice-7-acceptance.md) and
[Slice 8 installed-package acceptance](2026-09-16-multisource-slice-8-acceptance.md).
These are historical source and wheel results, not full backend parity or a new
live rerun. The [C0 baseline](2026-09-16-multisource-capability-c0-acceptance.md)
separates current admission, historical evidence and the new completion targets.
This document alone does not enable a backend. Each backend/method combination
requires its own acceptance.
The original Slice 1d blanket read-only restriction is superseded by the
[capability restoration and abstraction correction](2026-09-15-multisource-slice-1d-restoration-acceptance.md).
Remote read-only qualification remains part of each future backend activation.

## 1. Outcome and scope

Enable governed lazy Dataset execution on PostgreSQL, MySQL, SQLite, Trino and
ClickHouse, using the current DuckDB implementation as the behavioral baseline.
Keep the existing public flow: construct a Logical Dataset, call `execute()`,
then inspect the Materialized Dataset. Datasource selection continues to come
from authored semantic sources; no public executor argument is introduced.

The deliverable is a tested set of exact source implementations, not a blanket
claim that every Ibis backend supports every Dataset method. Push the maximal
eligible contiguous calculation into the declared source. Use an existing
in-process local method only where its owner admits that exact input shape.
Reject unsupported source-required work before reading source rows.

### Unified operators and backend-owned execution

Operator contracts are backend-neutral: input/output meaning, exact numeric
semantics, single evaluation, mandatory assertions and atomic publication are
unchanged across implementations. The existing implementation registry selects
an exact method/backend implementation and its concrete execution owner. There
is no operator-level DuckDB exemption, global payload ban or implicit rerouting.
Sharing an abstraction does not require identical SQL or simultaneous backend
coverage.

DuckDB supports its existing internal temporary tables/views, macros, memtable
and UDF registration, JSON and retained-stream readers, and owned cleanup. These
are concrete adapter facilities, not special operator semantics or permission to
modify business tables. Datasource/entity authoring keeps its existing ownership.

Remote adapters (including Trino, ClickHouse, MySQL and PostgreSQL) must work with
read-only accounts. They must not require data writes, object creation, uploads,
macros/UDF registration or cleanup DDL. Required metadata, connection/session
controls and cancellation must be qualified against the actual read-only account.
Marivo changes generated operations; it does not add a general SQL security layer.
An unimplemented method/backend combination fails through the same registration
and admission mechanism before source work. CTE syntax alone does not prove a
volatile input is evaluated once.

This correction restores existing implementations only. It activates no remote
backend and grants no new transfer of private source identities, events or
intermediate state into local DuckDB. Existing Parquet/Store and object-output
writes retain their separate authority.

### Execution and resource responsibility

The agent writes a Python script or executes Python commands against Marivo.
Local analytical methods run synchronously in that calling Python process;
Marivo does not create a local execution worker, subprocess pool or supervisor.
Each call returns its complete result or raises an exception with its original
cause and traceback preserved. This is a per-call contract, not a transaction
over the agent's whole script. External process termination or an OS-level
failure cannot guarantee a Python exception or immediate cleanup.

Resource decisions belong to the agent and its execution environment. Marivo
does not impose execution budgets: no resource-based row/byte/cell/page caps,
memory/RSS/spill quotas, method-complexity ceilings, storage quotas or execution
deadlines across source queries, transfer, local computation and retained reads.
It does not reject supported work because such limits cannot be enforced.
Database, driver, OS and external runner limits still apply independently;
Marivo propagates their failures and does not override them to promise unlimited
capacity. Explicit analytical parameters such as Top-N, semantic validity,
ownership checks and bounded Help/show output remain separate contracts.

This decision replaces the existing worker and execution-budget requirements
for lazy analysis. Slice 1b removes their implementation and disclosure; this
document does not claim that removal has already shipped.

Slice 1c further removes remote termination certification, mandatory single
compilation and engine/driver/Ibis version certification or version-based
runtime admission. Prefer ordinary Ibis execution and driver cleanup. A failed
read-only remote query with unknown termination does not by itself block the
Session. Preserve local publication integrity and report uncertainty honestly.
Version information is optional diagnostic evidence, never an eligibility gate.

First delivery targets useful scalar Metric journeys on all five engines.
Advanced methods follow in separately admitted slices. The minimum successful
journey is a scoped observation, grouped aggregation, filtering, deterministic
ranking and Top-N, followed by immutable publication and cold reads. Backend
restrictions on table engines, connectors and types are part of
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
arbitrary SQL execution through Dataset APIs, business-data writes, remote
datasource DDL/uploads and new cross-engine private-state transfer,
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
execution ownership, the removal of cross-query source consistency guarantees,
in-process local execution without Marivo execution budgets, ordinary Ibis
execution, no version admission, the simplified cleanup rules below, and strictly
remote read-only account compatibility. The corrected Slice 1d restores DuckDB
internal execution facilities while retaining one operator and registration
contract. Concrete adapters own preparation and resource effects.
Do not change the older
Slice 9 acceptance status or treat this proposal as its completion evidence.

## 2. Verified baseline and required changes

The inventory below records the pre-slice baseline. Completed removals and
replacement evidence are recorded in each slice; these historical couplings
are not current requirements.

The current `lazy-dataset` checkout supports six datasource engine profiles but
only DuckDB source execution for lazy analysis. The targeted Runtime economics
suite passed on 2026-09-15: one DuckDB success journey and five explicit
non-DuckDB rejections. Those rejection tests do not prove remote execution.

| Existing owner | Current coupling | Required change |
| --- | --- | --- |
| `marivo/datasource/engines/` | Connection, metadata, timezone and authoring timeout profiles already exist for six engines | Reuse physical connection facts; keep lazy operator eligibility in Analysis |
| `analysis/operators/registry.py` | `source_adapter` is one string; source versions default to DuckDB 1.5.3 / Ibis 12.0.0 | Register implementations per method/backend in the existing registry; delete engine/driver/Ibis version requirements |
| `analysis/compiler/placement.py` | Every source binding includes local DuckDB/Ibis versions | Remove version fields from domain/eligibility decisions; preserve exact datasource and Session ownership |
| `analysis/materialization/admission.py` | DuckDB `Backend` checks, `.con.interrupt()`, `SET`, transactions, schema lookup, temporary tables and batch readers | Route permitted read operations through typed execution adapters; keep concrete preparation and cleanup in the adapter; remote implementations must work with read-only accounts |
| `analysis/materialization/validation.py` | Validation cursor must be `DuckDBPyConnection` | Decode typed validation rows independently of a concrete cursor |
| Statement paths in `materialization/admission.py` and `sampling.py` | `sqlglot`/`sge` rendering with the DuckDB dialect, sampling SQL parsing, `DESCRIBE`, temporary relation SQL, raw scalar checks and `TransactionException` handling | Inventory actual statements and error paths; move dialect behavior into concrete adapters, including diagnostics; remote implementations must not require write/DDL permissions |
| `analysis/materialization/resources.py` | Cleanup and Session recovery require execution terminal proof | Separate local publication safety from remote read-only query cleanup; unknown remote termination alone does not block a Session |
| `analysis/materialization/local_worker.py`, `worker_lifetime.py`, `local.py`, `admission.py` | Supervised local execution, IPC, worker reservations and resource admission | Execute local kernels in the caller; remove worker-only protocols and budget enforcement in Slice 1b |
| Materialization storage/read policies, execution adapters and method registrations | Execution deadlines, row/byte/memory/spill and problem-size caps | Remove resource-only policies, probes, checks and error guidance across source, local, writer and retained-read paths |
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
    -> Runtime: bind and verify the owned execution context
    -> pure Ibis lowering + selected backend compilation
    -> owned source queries and typed Arrow batches
    -> configured Parquet writer / synchronous in-process local methods
    -> validation + atomic publication
```

### 3.1 One method registry

Extend the existing `ImplementationRegistration` with a closed collection of
backend-specific registrations. The key remains the owning operator/method;
each backend has at most one admitted implementation for the exact invocation.
This is direct dispatch, not a list of candidate plans.

Each registration supplies concrete typed facts for:

- adapter identity and implemented method/backend support;
- admitted Dataset shapes, value types and method parameters;
- source builder and, where needed, a declared backend-owned preparation builder;
- required validation, single-evaluation fence and retained-part capabilities;
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
operations are shared; backend-specific operations stay with their concrete owners:

| Operation | Required contract |
| --- | --- |
| Open | Open the declared datasource through its existing connection owner; no version certification or version-based admission |
| Bind execution | Bind statements and resources to the owned action-local execution context; this establishes lifetime and authority, not a shared source snapshot |
| Resolve table/schema | Use datasource-owned qualification and normalize physical types without losing precision |
| Execute expressions | Use the registered concrete implementation, including its hooks and preparation; remote implementations must work with read-only accounts |
| Read assertions | Return the required named integer/scalar checks; reject missing, duplicate or malformed results |
| Stream rows | Yield typed Arrow batches and close the actual underlying cursor/response; batching is a transport strategy, not a hard allocation guarantee |
| Own fences | Preserve method-required single evaluation through the selected adapter; DuckDB may use owned temporary resources, while remote implementations must satisfy their read-only account boundary |
| Cancel and close | Attempt cancellation and close the exact owned driver resources; report failures or unknown remote status without requiring termination certification |

Use one typed action-local execution context for statement ownership and resource
lifetime. Do not introduce transaction, version-pinned or single-statement
realization variants to enforce source consistency. Assertions may use separate
queries; their decoding and required validation order remain explicit. Unsupported
methods fail at admission; missing budget controls, version certification or
remote termination lookup do not.
No new public executor surface is introduced.

Concrete engine code may use its driver's APIs; shared Runtime code must not
depend on `.con`, a DuckDB cursor class or dialect-specific SQL.

**Use the normal execution path.** Recompiling an expression is not resubmitting
a query. Permit Ibis to compile during execution and retain its parameter
binding, required preparation hooks and result conversion. Concrete adapters own
temporary resources, registration and numerical preparation. DuckDB retains these
facilities; remote adapters must implement their methods without requiring write
permissions. Do not require a second execution route merely to enforce compile
counts or immutable statement wrappers.

Preserve complete results, correct parameters/types, required validation order,
owned resource cleanup and method-required single evaluation. Capture actual
submitted statements through the execution path with existing redaction; a
diagnostic compile is not evidence of execution. Check for unintended duplicate
queries rather than rejecting a second pure compilation. Single compilation is
an optional optimization, not an admission or acceptance contract. SQL remains
internal, not a semantic expression body or new public surface. Do not persist
a replayable physical plan or put compilation artifacts into Dataset identity.

Do not reuse `authoring_timeout` as a drop-in Analysis lifetime context or import
its deadline into lazy execution. For
example, the current MySQL authoring helper starts and rolls back a transaction;
reusing it could interfere with Analysis cursor or resource lifetime. Extract shared low-level
controls only where their owners and lifetimes are identical.

### 3.3 Placement and execution order

1. Honor the existing same-Session binding hit before opening a new source.
2. On a miss, check locally known method, backend, type and shape eligibility.
   Known unsupported source roots retain their pre-Run rejection behavior.
3. Select the complete physical graph, including declared preparation boundaries.
   Resolve physical eligibility through the selected implementation before Run
   admission. DuckDB may use its existing internal resources; remote source stages
   must not require uploads or object creation.
4. Admit the Run and reserve execution resources before connecting or submitting.
5. Open the declared datasource and resolve schema or method-required source
   facts. Do not run version certification or version-based admission. Connection,
   compilation and execution failures propagate from the selected path.
6. Bind the owned execution context per source domain. Do not acquire a shared
   snapshot or pin source versions solely to align related reads.
7. Execute the selected expressions through Ibis or the justified backend path,
   in owner-defined order, including assertions, primary reads and part reads.
   Concrete adapters own submission paths and implicit hooks; remote acceptance
   must prove they work with the declared read-only account.
8. Validate and publish through the existing writer and recovery protocol.

Read catalog/schema/settings only where needed for source resolution or a
specific method's semantics. Do not add a generic compatibility preflight or
trial analytical queries to discover a different plan. Optional diagnostic
version collection must not fail execution when unavailable.

### 3.4 Domain identity without version admission

Keep source and execution ownership; remove version-based compatibility authority:

- **Source-domain authority:** the captured Session/store/catalog/semantic and
  datasource binding authority currently checked by `same_domain`. Separate
  bindings never become equal because server endpoints or versions match.
- **Bound execution authority:** statements and resources belong to the exact
  owned action-local context/handle. This prevents accidental cross-execution
  submission; it does not establish a common observation of source data.

`same_domain` remains a pure pre-I/O ownership check. Remove `source_versions`
and `adapter_versions` from implementation eligibility and domain equality;
do not replace exact matching with version ranges, allowlists, certification
records or version probes. Engine/driver/Ibis versions neither admit nor reject
an execution and do not invalidate an immutable Artifact binding hit.

Method/backend registration still identifies implemented analytical behavior;
type, shape and required semantic checks remain. Failures in the selected Ibis
or driver path surface as errors, not as triggers for an alternate plan.
Two distinct datasource bindings remain distinct regardless of versions.
Resources and statements must still belong to their intended connection/action;
version metadata cannot authorize connection substitution. Optional versions
recorded in diagnostics or test evidence have no execution authority. Semantic
business versions, method contract versions and storage format versions are
different concepts and retain their existing owners.

## 4. Source reads without cross-query consistency guarantees

Marivo does not guarantee that source assertions, primary output and required
part reads within one `dataset.execute()` observe the same data version. It also
does not guarantee a common snapshot across contributing relations or Dataset
inputs. Each statement observes the data provided by its backend at execution.
Concurrent source updates can therefore cause checks and published results to
reflect different source states, even when every SQL statement succeeds.
Successful validation records the checks actually performed; it does not certify
that later reads still satisfy those checks. An execution may publish such a
result without detecting or rejecting the intervening update.

Adapters are not required to open a shared read transaction, raise isolation,
pin physical table versions, or fuse checks and outputs into one SQL statement
for this purpose. Backend-native isolation may incidentally provide stronger
behavior, but it is not a Marivo support guarantee or an admission requirement.
No consistency option, mutation-detection retry or alternate execution route is
introduced. Semantic business-time/version selection remains part of the query
meaning and must not be removed with physical snapshot-pinning machinery.

### 4.1 Preserved independent requirements

- Execute all required source and output validations in their owner-defined
  order. Observed failed, missing or malformed assertions still prevent
  publication, including when the primary result is empty.
- Preserve method-owned single evaluation for shared sampling, volatile
  Population selection and other preparations whose computed result must be
  reused. These fences preserve that computation; they do not establish a
  common snapshot for every source read in the execution.
- Preserve atomic publication of primary rows, required parts, Evidence and
  Findings, plus immutable Artifact identity and cold reads. Atomic publication
  does not certify a common source observation.
- Preserve resource ownership reservation, streaming, cancellation, cleanup and
  recovery. Read transactions needed by a driver or cursor may remain for that
  concrete purpose, without datasource mutation or a cross-query consistency claim.
  Reservation records ownership for recovery; it does not allocate a budget.

Existing DuckDB temporary-relation fences preserve shared evaluation and remain
available through its adapter. Every remote implementation must independently
prove the same semantics using operations allowed for its read-only account.
A CTE or repeated query is not sufficient proof of single evaluation. Initial
Trino and ClickHouse group-A registrations remain deterministic and fence-free;
other methods stay unregistered until implemented and accepted.

### 4.2 Current implementation and removal task

Slice 1a removes the DuckDB action-wide transaction and consistency-only
rollback paths. The adapter now initializes the owned execution connection
without opening a shared read transaction. Statement ownership, required checks,
single-evaluation fences and atomic publication remain enforced. The
[Slice 1a evidence record](2026-09-15-multisource-slice-1a-acceptance.md) records
controlled source-update, stable-fixture, cleanup and broad verification. This
remains evidence for that candidate; current restoration has its own acceptance.

## 5. Backend-specific delivery scope

The following are proposed activation scopes, not current support claims.
Backend/method acceptance proves behavior on the test environment; it is not a
version certification program. Record available environment versions for
reproducibility without using them to control runtime eligibility.

| Backend | Initial execution and useful scope | Important restrictions |
| --- | --- | --- |
| DuckDB | Existing methods and native retained scans under their existing authority | Preserve exact semantics through the common registration and adapter boundary, including internal temporary resources and macros |
| PostgreSQL | Ordinary relational scalar Metric chains | Verify cursor lifetime, exact decimal/time decoding and ordering; no repeatable-read requirement or inherited DuckDB macros |
| MySQL | Scalar Metric chains on qualified table engines | Window and join rewrites need exact parity; document buffering and external timeout behavior; no shared-snapshot requirement |
| SQLite | Conservative scalar types and simple Metric chains on a verified database | Do not advertise unsupported Decimal or timezone semantics; interrupt during fetch as well as execute |
| Trino | First qualify scalar chains on one Iceberg connector scope using ordinary table scans | Test connector, types, streaming and driver cleanup; snapshot IDs and remote termination certification are not required |
| ClickHouse | Scalar chains on a precisely tested local MergeTree table scope, with separate checks and output queries | Distributed tables, dictionaries and additional join shapes require their own semantic and transport qualification |

### Trino

Use existing datasource qualification, including catalog/schema/table, and normal
backend compilation. Physical snapshot resolution, snapshot-ID pinning and a
version-qualified compiler extension are not activation requirements. Preserve
explicit semantic temporal selection where a supported method requires it.
Expand connector and multi-table support through ordinary method/type/transport
qualification, without a common-snapshot admission test.

### ClickHouse

Use source assertions and output queries in the required order. A
single-statement assertion envelope and proof of read sharing under concurrent
writes are not activation requirements. Verify empty-output validation, effective
JOIN null behavior, finite-value handling and Decimal decoding. Timezone
conversion must be qualified before admitting timestamp/timezone inputs; the
approved Slice 6 scope defers those inputs and conversion, while retaining the
engine timezone metadata probe for native-date scope resolution. Do not skip
required checks or infer Dataset support from a raw aggregate. Ordinary support does not depend on multi-statement transactions.

### Driver-owned transactions

PostgreSQL, MySQL and SQLite adapters may use transactions where required for
read cursor operation or read-resource management only, with no write/DDL
permission. Qualify their lifetime, cancellation
and cleanup for that purpose. Do not require repeatable-read isolation or reject
a supported method solely because contributing queries may observe updates.

## 6. Method coverage and semantic portability

| Delivery group | Target scope | Admission evidence |
| --- | --- | --- |
| A: basic source analysis | Unversioned supported Entity/Metric shapes; sum/count/min/max; scoped filters, dimensions, aggregation, projection, rank and limit | Identity, null/empty rules, exact output types, deterministic tie ordering and source-side reduction |
| B: relational and temporal analysis | Relationships, ratio/mean/weighted mean, time buckets, version resolution and supported comparison/attribution arithmetic | Fanout, temporal anchors, spatial-before-temporal folds and atomic sufficient-state parts |
| C: admitted local suffixes | Forecast, Kendall, time-based discovery and existing non-Entity local methods after supported source preparation | Exact complete inputs, alignment, synchronous in-process execution and no raw semantic-source collection |
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
  summation semantics. Their installation belongs to the DuckDB adapter.
  Remote implementations need an equivalent read-only implementation;
  ordinary remote `SUM(double)` is not an equivalent port.
- Event matching, sampling and Lifecycle replay require exact ordering and
  single-evaluation boundaries; successful SQL compilation does not prove them.

Source-private methods remain disabled on a new backend until their existing
retention and native read contract can be met without a forbidden transfer.
Do not export raw private state into local DuckDB merely to increase coverage.
Any required change to that authority belongs in a separate owner amendment.

## 7. Streaming, in-process execution and remote lifetime

### Transport without execution-budget admission

Prefer incremental driver reads and Parquet writes to avoid unnecessary full
result copies. Normalize Arrow types exactly and preserve complete results.
Batch sizes are implementation tuning values, not total-result limits or a
promise to bound peak allocation. Document actual driver buffering, including
full-result or large-cell decoding, without rejecting an otherwise supported
type merely because a hard memory bound cannot be proven.

Remove sizing/admission policies and width/count probes whose sole purpose is
enforcing execution budgets, including the budget-only `_batch_rows` width path.
Keep queries required for semantic validation and explicit analytical meaning.
Keep validation queries separate by default; removing budgets does not require
combining them into expensive unions. Do not add preflight cost estimation,
automatic sampling, truncation or fallback to compensate for removing limits.
Available row/byte counters remain observations, not enforcement thresholds.
Unavailable server metrics remain unavailable, not zero.

### Local execution without workers

Invoke admitted pandas and numerical kernels directly in the calling process,
using complete typed inputs and the existing result validation/publication path.
Remove process spawning, IPC payload protocols, worker-only staging, RSS polling,
watchdogs, worker reservations and worker-death receipts. Retain staging needed
for Parquet publication and recovery of actual owned files or remote queries.
Do not replace the worker with a thread executor, sidecar or hidden subprocess.

Ordinary kernel/driver errors propagate with their cause and traceback; preserve
the original failure if cleanup also fails. Handle user interruption and close
owned resources where control returns to Python. Marivo does not promise a hard
deadline or immediate interruption of a blocking native call. The agent or its
runner decides whether to stop the whole calling process. No partial execution
may be published as successful, including after a crash and subsequent recovery.

### Cancellation and recovery

Attempt cancellation and close the actual owned cursor, response and connection
on errors or user interruption where Python retains control. Use the driver's
normal resource cleanup facilities. DuckDB may clean up its exact owned temporary
resources; remote adapters must not require cleanup DDL. Unknown historical
resources are not certified deleted by discharging local publication ownership. Retain a safely available
query ID for diagnostics or exact cancellation. Never kill sessions by username, broad SQL matching or
reused numeric IDs. Never persist credentials, token-bearing result URLs or raw
connection strings in the journal or Evidence.

Do not build a durable remote request-correlation protocol, submit/acknowledgement
crash-window closure or server termination lookup as a backend prerequisite.
A lost acknowledgement, unavailable query ID or failed cancel may leave remote
read-only execution status unknown. Report that uncertainty without claiming
the server stopped; do not mask the original execution exception with cleanup
failure. The agent and execution environment own any further resource decision.

Keep remote query cleanup separate from local publication recovery. Before
allowing a new Session writer, resolve Store commit state and establish that an
old local publisher cannot continue writing, using the existing writer ownership
mechanism. If no successful publication exists and no conflicting writer remains,
record the failed/interrupted Run and allow subsequent work even when remote
read-only termination is unknown. Such a query cannot publish an Artifact on
its own. Clean only exact local resources that are safe to remove; retain useful
cleanup diagnostics without making them a Session-wide admission lock.

Reserve `RecoveryPendingError` for unresolved local commit/publication ownership
or storage integrity that actually prevents safe continuation. Do not remove
this protection by treating every crash as a known failed commit. Atomic
publication and no partial cache hits remain mandatory; immediate cleanup after
process death and proof of remote termination are not promised.

The selected action is never resubmitted after ambiguous submission. Audit
driver-level retries as well as Marivo retries. Any transport retry must
be demonstrably part of the same owned query; otherwise disable it. Incomplete
transfer, cancellation and schema drift leave no successful Artifact or cache hit.

## 8. Retained inputs, identity and disclosure

The existing `admitted_binding` shortcut assigns a sole source candidate to a
Parquet Artifact. Replace that assumption with an explicit native-reader
capability check. New remote adapters initially have no retained-Parquet import
capability. This is not permission to upload: the remote read-only boundary
prohibits result uploads to remote datasources. Do not upload a local result to
make a source-only method work.

Retained-only operations may continue through the existing DuckDB Parquet domain
or admitted in-process local method. Mixed remote-source/Artifact operands use an existing
local multi-input method only when complete input and authority checks pass;
otherwise they are rejected. Cross-Session and private-state restrictions remain.

Keep `execution_key(definition_fingerprint)` unchanged. Backend versions and
execution facts explain a newly executed Run; they do not invalidate an already
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
evidence. PostgreSQL establishes the remote execution adapter pattern; Trino and
ClickHouse feasibility is investigated early to avoid discovering a fundamental
blocker after generic infrastructure has been built.

| Slice | Work and owning files/modules | Exit criteria |
| --- | --- | --- |
| 0: freeze method scope | Existing registry, compiler/Runtime inventory and focused backend fixtures; no activation | Inventory current methods/shapes; record the test environment; test Trino ordinary scans and ClickHouse separate validation/output queries; record unresolved behavioral blockers without version certification |
| 1: extract DuckDB adapter | `materialization/admission.py`, `validation.py`, `resources.py`; new private execution adapter module | Existing DuckDB source, native Parquet, local-suffix, cancellation and publication behavior unchanged; shared code no longer requires DuckDB cursor operations |
| 1a: remove source-consistency enforcement | `materialization/admission.py`, `execution.py`, `duckdb_execution.py`, `resources.py`; consistency-specific tests and owning planner/Runtime/public docs | Remove action-wide `BEGIN`/`ROLLBACK` used solely to align source reads and transaction-realization typing/admission; preserve execution ownership, single-evaluation fences, driver/resource-required transactions and atomic publication; pass focused Runtime and broad gates |
| 1b: remove local workers and execution budgets | `materialization/local_worker.py`, `worker_lifetime.py`, `local.py`, `admission.py`, `resources.py`, storage/read policies, adapters, method registrations and affected contracts/tests | Local kernels execute in the caller without spawning; no Marivo resource-budget admission or enforcement remains in lazy analysis; errors preserve tracebacks; publication, semantic checks and owned-resource recovery pass focused and broad gates |
| 1c: simplify execution and recovery | `materialization/execution.py`, `duckdb_execution.py`, `resources.py`, `admission.py`, `operators/registry.py`, `compiler/placement.py`, Session recovery and owning contracts/tests | Ordinary Ibis execution permitted; no compile-count gate or version certification/admission; remote termination uncertainty alone does not block Session work; local publication integrity remains protected |
| 1d correction: restore capabilities and unify execution ownership | Existing registry, concrete adapters, sampling/preparation and owning contracts/tests | Restore DuckDB capabilities without operator special cases; declare remote read-only requirements for later activation; pass restored success tests and broad gates |
| 2: extend exact dispatch | `operators/registry.py`, `compiler/placement.py`, source binding and retained binding admission | Multiple closed backend registrations supported; only DuckDB enabled; unsupported backend/type/method cases fail at the correct phase; no version gate; binding hits remain source-free |
| 3: PostgreSQL group A | New PostgreSQL execution adapter; focused datasource control reuse; shared scalar lowering | Real group-A success with separate checks and output reads, streaming, cancellation/recovery and cold Artifact reads; no execution-budget prerequisite |
| 4: MySQL and SQLite group A | Separate concrete adapters and per-engine tests | Same group-A contract under each declared physical-type/table scope; no inherited authoring transaction bugs; driver buffering documented |
| 5: Trino group A | Trino execution adapter, ordinary Ibis execution and driver cleanup | Real Iceberg-backed source reduction, required assertions and multi-page transfer; failed cleanup reports uncertainty without blocking safe local recovery; no snapshot or remote-termination certification gate |
| 6: ClickHouse group A | ClickHouse adapter and scalar method lowering with separate assertions | Required assertions, primary output, empty-output validation and cancellation tests pass; no common-read-basis gate |
| 7: expand supported methods | Owning temporal, relationship, retained, correlation and other method modules | Group B/C entries enabled individually; group D enabled only after its additional semantic/private-state proofs; every remaining entry has an explicit unsupported reason |
| 8: consolidated disclosure and packaged acceptance | Native Help/registry tests, affected specs, packaged skills, CLI examples and English/Chinese latest site docs | Installed-package journeys match advertised support; full broad gate green; backend matrix distinguishes live success from rejection-only evidence |

Slice 0 must produce a bounded inventory of statement rendering/parsing,
metadata and scalar queries, transaction exceptions, pre-execute hooks and
compile-to-submit paths, including sampling and retained-part helpers. For each
backend execution path, record its actual operations, single-evaluation fence
support, required semantic checks and actual transport behavior. Slice 1 must demonstrate that DuckDB dialect and exception handling
remain only in concrete DuckDB paths; changing type annotations is insufficient.
Slice 2 must test domain authority and execution ownership independently of
optional version diagnostics. These are exit checks, not optional later cleanup.

Slice 0 progress is recorded in the
[qualification inventory](2026-09-15-multisource-slice-0-qualification.md).
The prior Slice 0 completion record includes Trino snapshot reads/expiry and a
ClickHouse assertion envelope under concurrent writes. Those are historical
experiments, not requirements of this amended plan. Reconcile the inventory in
Slices 1a-1c; production transport, required validation and local publication
recovery remain activation gates. Historical version and remote-termination
proofs do not create new prerequisites. At the Slice 0 boundary, non-DuckDB execution remained disabled.

Slice 1 is complete for the private DuckDB adapter extraction, with 246 targeted
Runtime cases and the broad gate passing. The
[DuckDB adapter evidence record](2026-09-15-multisource-slice-1-acceptance.md)
records the exact scope and independent review. At the Slice 1 boundary,
non-DuckDB execution remained disabled. Slice 1a is also complete, with separate
[acceptance evidence](2026-09-15-multisource-slice-1a-acceptance.md). Slice 1b is complete with
[caller-execution acceptance evidence](2026-09-15-multisource-slice-1b-acceptance.md).
Slice 1c is complete for execution, version-independent placement and guarded
recovery simplification; see the [Slice 1c evidence record](2026-09-15-multisource-slice-1c-acceptance.md).
Store v5 owns the replacement resource semantics without migration or dual read.
Slice 2 is complete for exact backend registration and dispatch; see the
[Slice 2 evidence record](2026-09-15-multisource-slice-2-acceptance.md).
Slice 3 is complete for the declared PostgreSQL Group A scope; see the
[Slice 3 evidence record](2026-09-16-multisource-slice-3-acceptance.md) and its
real read-only Dataset, cursor, recovery and cold-read evidence.
Slice 4 is complete for the declared MySQL and SQLite Group A scopes; see the
[Slice 4 evidence record](2026-09-16-multisource-slice-4-acceptance.md), including
real read-only execution, exact typed identity transport, numerical/date failure
checks, process recovery, independent review and final broad/site validation.
Slice 5 is complete for the declared Trino Iceberg Group A scope; see the
[Slice 5 evidence record](2026-09-16-multisource-slice-5-acceptance.md), including
server-enforced read-only execution, ordinary scans, exact scalar identity/types,
actual multi-page transport, cursor cancellation, safe local recovery, independent
review and final broad/site validation. At the Slice 5 boundary ClickHouse
execution remained unenabled; advanced method groups remain unenabled on Trino.
Slice 6 implements the declared ordinary local MergeTree ClickHouse Group A
scope; see the [Slice 6 evidence record](2026-09-16-multisource-slice-6-acceptance.md).
At the Slice 6 boundary, timestamp/timezone, unsigned physical inputs and advanced
groups were not enabled.
Slice 7 implements individually qualified relational/date B methods and complete
aggregate-to-local C continuations on all five backends; see the
[Slice 7 acceptance and explicit unsupported matrix](2026-09-16-multisource-slice-7-acceptance.md).
Advanced private-state, timestamp/DST and other unqualified entries remain rejected.
Slice 8 completes consolidated disclosure and final-wheel acceptance for all five
new backends, alongside the installed DuckDB baseline; see the
[Slice 8 installed-package acceptance and evidence matrix](2026-09-16-multisource-slice-8-acceptance.md).
The matrix separates installed success from source-only Runtime and rejection-only
evidence. It does not claim full DuckDB parity or activate the remaining methods.
At the Slice 2 boundary only DuckDB was enabled; its multi-entry registration
tests are pure dispatch checks, not remote execution acceptance. PostgreSQL
activation is established by the separate Slice 3 evidence above.
Historical Slice 1 behavior preservation is not a requirement to
retain the mechanisms explicitly removed by these amendments. Slice 1d is a
correction of execution ownership; remote read-only compliance requires actual
per-backend acceptance, which completed Slices 0-1c do not supply.

Slice 1a was implemented as a separately authorized task with the following
scope and acceptance requirements. Inventory each existing transaction and fence
by purpose before removal. Remove the action-wide consistency transaction wrapper,
its consistency-only rollback/exception paths, realization variants and any
snapshot-equality admission/tests introduced solely for that guarantee. Keep
statement-to-execution ownership guards, source authority and owned cleanup.
Existing timeouts are removed in Slice 1b; remote recovery is simplified in
Slice 1c. Retain transactions only where a concrete driver or
read-resource lifecycle needs them; document and test that purpose. Slice 1d
places internal DuckDB resources with its adapter and requires remote adapters
to support read-only accounts. Do not remove
semantic version selection, the requirement for single evaluation,
retained-result integrity checks, or Store publication transactions.

Update the linked planner and Runtime owners, current public execution docs,
affected Help/skills and bilingual site docs in that task. Reconcile the Slice 0
inventory and Slice 1 evidence with the new contract without rewriting historical
results as new acceptance. Verify stable-fixture output parity, required assertion
failures including empty output, sampling reuse, primary/part publication,
cancellation and cleanup. Add a controlled between-query update case showing
that no shared snapshot is promised or required; changed observations alone must
not trigger rejection or retry. Run the relevant focused tests, touched-module
typing/lint and `make check-agent`. Do not activate another backend in Slice 1a.

### Slice 1b removal and refactoring tasks

Completed on 2026-09-15; see the [Slice 1b acceptance record](2026-09-15-multisource-slice-1b-acceptance.md).
The scope and verification requirements below remain the owning contract.
No unrelated backend activation is included.

1. **Inventory enforcement and dependencies.** Trace source execution, native
   retained scans, local suffixes, multi-input methods, storage writers and
   retained collection including `to_pandas()`. Classify each limit as an
   execution resource constraint, semantic requirement, display bound or
   external system setting. Remove only the first category; do not weaken
   authority, type/shape validity, complete-input alignment or exact results.
2. **Move kernels into the caller.** Extract reusable numerical computation from
   `local_worker.py` into its natural local-method owner and invoke it directly.
   Delete worker spawning, IPC serialization, parent supervision, RSS probes,
   watchdogs and worker-only workspace/lifetime capabilities. Remove obsolete
   imports, telemetry fields and receipts; preserve meaningful result provenance.
3. **Remove all lazy execution budgets.** Delete resource-only configuration,
   defaults, policy objects, deadline wrappers, DuckDB memory/spill settings
   injected for those policies, adapter budget capabilities and admission checks.
   Cover input/output/combined rows and bytes, batch/cell/page rejection caps,
   intermediate expansion, pair/coalition/series resource ceilings, stored bytes
   and retained-read limits. Remove budget-only source probes and registry
   requirements. Keep mathematical minima and constraints needed for a method
   to be defined. Do not replace removed limits with larger constants or opt-outs.
4. **Keep resource cleanup independent.** Rework `resources.py` and callers around
   actual files, cursors, connections and remote queries rather than worker
   terminal proof. Preserve atomic Store publication and crash recovery; apply
   the simplified driver cleanup and local recovery contract in Slice 1c.
   External timeouts remain possible
   errors; no internal execution deadline or hidden replacement worker is added.
   If persisted worker/budget fields change, follow the owning store-format
   policy, explicitly reject unsupported old formats and never silently discard
   outstanding resource obligations or add an unowned compatibility path.
5. **Align the disclosure contract.** Update planner/Runtime owners, public
   analysis docs, Help and its independent surface tests, structured errors and
   repairs, packaged skills, CLI/examples and English/Chinese latest site docs.
   Remove obsolete budget errors and worker guidance after checking their other
   owners; preserve errors still used outside lazy execution. Reconcile the
   qualification inventory and evidence records without relabeling historical
   worker/budget acceptance as evidence for the new behavior. Unrelated authoring
   controls and Help/show display bounds are outside this removal task.
6. **Verify the replacement behavior.** Assert caller PID execution and no child
   spawn for local methods. Replace worker/overflow tests with stable-fixture
   numerical parity, original error/cause/traceback propagation, semantic
   rejection, complete input/output and publication/cleanup cases. Use safe
   fixtures or injected external failures to show former caps no longer cause
   rejection; do not deliberately exhaust the host. Cover wide/nested values,
   multiple inputs, retained reads, user interruption and cold recovery after
   killing the calling process. Such recovery tests may launch a test process;
   production Marivo must not launch an execution worker. Test that lack of
   budget-control capabilities does not reject supported adapters. Run focused
   unit/Runtime tests, touched-module typing/lint and `make check-agent`.

Slice 1b is complete only after both execution paths and their advertised
contracts are free of the removed guarantees. Resource measurements may remain
as evidence; no execution cap may survive under a sizing or safety-policy name.

### Slice 1c execution simplification tasks

The following implementation and acceptance requirements are complete for the
current DuckDB route. The linked Slice 1c record owns final verification evidence.
No additional backend is activated.

1. **Remove remote termination certification.** Inventory remote-query journal
   fields, submission correlation, terminal receipts and Session-wide recovery
   gates. Remove mechanisms used solely to prove that a read-only server query
   stopped, including pre-acknowledgement identity requirements. Use normal
   driver cancellation/close and optional safe query IDs. Retain diagnostic
   uncertainty without making missing remote proof a prerequisite for new work.
2. **Narrow blocking recovery to local publication safety.** Resolve the actual
   Store commit and writer ownership before finalizing a lost Run. Preserve
   successful commits, atomic primary/part publication, exact cleanup and
   protection against a surviving publisher. Allow a new action when local
   state is safe even if a remote read may continue. Update failed-Run semantics
   so failure means no successful local publication, not certified remote death.
   Preserve `RecoveryPendingError` for unresolved commit/ownership/integrity.
   Follow the store-format owner for removed fields; do not silently discard
   obligations that still protect local writes or delete guessed resources.
3. **Remove the mandatory compile/submit framework.** Permit normal Ibis
   expression execution, including its own compilation, parameter handling,
   backend-owned hooks and result conversion, subject to the corrected Slice 1d. Delete compile-count
   rejection and wrapper or adapter requirements used only to forbid recompilation. Keep direct driver
   paths where concretely needed; remote activation must remove forbidden operations from
   existing DuckDB implementations.
   Capture actual submissions and test parameters, complete typed output,
   validation ordering and no unintended duplicate queries. Do not replace the
   removed framework with another mandatory execution-plan representation.
4. **Delete version certification and admission.** Remove engine/driver/Ibis
   version requirements from registrations, `source_eligible`, source/native
   binding equality, execution preflight and dynamic guidance. Delete version
   allowlists, comparisons, certification records, mismatch errors/repairs and
   probes serving that purpose; do not substitute ranges or capability probes
   that reproduce version certification. Optional diagnostic version collection
   may fail without affecting execution. Keep normal package dependencies,
   semantic method/type checks, business-time/version selection, method contract
   versions and store-format validation; none is an engine-version admission
   program. Unsupported behavior fails through the owning semantic check or
   selected Ibis/driver operation with its cause preserved.
5. **Synchronize contracts and evidence.** Amend planner/Runtime and public
   execution docs, affected Help/error surfaces and independent tests, packaged
   skills, examples/CLI and both latest site languages. Reconcile Slice 0/1
   records as historical evidence; do not relabel them as acceptance of the
   simplified design. Remove claims of certified environments, exactly-one
   compilation or guaranteed remote termination from advertised support.
6. **Verify the new boundaries.** Cover disconnect before a query ID, failed
   cancellation and unknown remote status with successful later Session work
   once local publication is safe. Separately prove unknown commit state or a
   surviving writer still blocks conflicting work, and committed output is
   never lost. Show that a second pure Ibis compilation is permitted without
   introducing an extra query. Vary or omit diagnostic version strings and
   confirm unchanged method selection, domain equality and execution; distinct
   bindings remain distinct. Exercise real driver errors, correct output and
   error chaining. Run focused unit/Runtime tests, touched-module typing/lint
   and `make check-agent`; do not require server administrative termination
   lookup permissions merely to pass backend acceptance.

Do not combine backend activation with unrelated compiler cleanup. New optional
dependency constraints must be justified by the tested adapter and recorded in
its slice. Each enabling slice updates affected Help, dynamic guidance, drift
tests, examples, skills and English/Chinese documentation in the same change;
Slice 8 consolidates and verifies them rather than postponing that obligation.
Fixture changes follow the repository's `marivo-test-fixtures` skill
when implementation begins. No runtime fixtures or services are started by this
documentation task.

### Slice 1d correction: capability restoration

1. Restore the pre-1d capabilities without reverting earlier Slice 1a–1c or
   Slice 2 work. Remove the blanket payload bans and DuckDB-only read backend.
2. Use the existing implementation registry to resolve adapter binding and
   retained import. Keep physical sampling eligibility/SQL and Driver macro
   preparation in the concrete adapter. Do not add a parallel capability system.
3. Restore sampling, Event/Lifecycle, Candidate, JSON and retained-stream success
   paths, mandatory assertions, single evaluation and owned resource lifetime.
4. Restore affected tests and disclosure. Preserve the original 1d record as
   superseded evidence and record fresh restoration acceptance separately.
5. Run focused/default tests, necessary Runtime tests, typing/lint, broad checks,
   site checks and independent review. Activate no remote backend or new private
   state transfer. Later remote implementations must pass real read-only-account
   acceptance through the same method registration and execution contract.

## 10. Acceptance matrix and evidence

Every enabled backend/method entry requires the following applicable checks:

1. **No-I/O and placement:** construction makes no connection; known unsupported
   roots fail before Run admission; supported chains form maximal source stages;
   no version preflight runs. Selected-path errors propagate without replanning.
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
5. **Read and validation contract:** permit different source observations between
   validation, primary and part reads. Do not require isolation, snapshot pinning
   or failure on concurrent updates. Verify all required assertions still run;
   observed failed assertions prevent publication even with zero primary rows.
   Preserve single-evaluation tests for methods that reuse a computed selection.
6. **Transport and lifetime:** exercise slow execution, blocked/slow fetch,
   malformed pages, valid large cells/pages, disconnect, user cancellation, process kill
   before/after submit acknowledgement and lost cancellation acknowledgements.
   Verify cancellation attempts and honest unknown-status diagnostics; safe local
   recovery allows later Session work without remote terminal proof. Separately
   verify blocking for unresolved publication or conflicting writers. Test harnesses
   may use external timeouts; these are not Marivo execution budgets. Valid
   large values must not be rejected solely for exceeding a former resource cap.
7. **Publication and reuse:** interrupt at each writer boundary; verify atomic
   primary/parts metadata, cold-process readback, missing-source cache hits,
   immutable retained continuation, exact ownership and no private-state leakage.
8. **Negative matrix:** every unsupported backend/type/shape/method combination
   has bounded structured rejection. Replace each current blanket non-DuckDB
   rejection only when the corresponding positive entry is proven; retain all
   other negative cases.
9. **Actual execution:** capture submitted SQL and parameters; verify intended
   meaning, complete output and no unintended duplicate query. Allow repeated
   pure compilation through normal Ibis execution. Cover default-limit behavior,
   pre-execute preparations, assertions and parts, not only primary rows.
10. **New admission boundaries:** reject sampled/fenced work on adapters without
    that capability. Do not reject an otherwise supported multi-relation method
    solely for lacking a common snapshot. Preserve immutable-input comparison
    and alignment rules. Different or unavailable diagnostic versions must not
    affect eligibility or domain equality. Distinct bindings and resources from
    another execution must not merge authority. Exercise complete wide/nested
    results without resource-budget admission.
11. **Caller-owned execution:** local methods execute in the calling Python
    process without worker startup or budget enforcement. Preserve ordinary
    failure tracebacks and never publish partial success; test external failure
    and subsequent recovery without promising a traceback after process death.

12. **Unified execution and remote read-only compatibility:** verify method
    selection, adapter binding, physical preparation and retained-import authority
    use the existing registration mechanism. Prove DuckDB success for its restored
    internal facilities, including failure cleanup and shared evaluation. For each
    later remote activation, capture actual connection, metadata, hook, query and
    cleanup operations under a read-only account; no method may depend on writes,
    object creation, uploads or cleanup DDL. Unimplemented methods reject before
    source work rather than gaining an implicit alternate execution route.

Record fixture identity, connector/table scope, enabled methods, statements by
role, transferred rows/bytes, cleanup outcomes or unknown remote status, and
commands/outcomes. Available server/Ibis/driver versions are diagnostic evidence
only, without certification or runtime admission. Distinguish unavailable metrics
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
advertised scope, with validation, streaming, driver cleanup, publication and
installed-package evidence, plus remote read-only-account acceptance across every
operation path. A backend blocked at Slice 0 or later remains
unavailable; the overall five-backend objective is then incomplete.

Full DuckDB feature parity is a separate milestone. Group-D restrictions must
remain visible and must not be described as completed multi-engine parity.

The implementation must settle these bounded questions before the affected
activation, rather than add speculative abstractions now:

- Available backend test environments and reproducible fixtures; no version
  certification or version-based runtime admission is required.
- Whether the installed Ibis Trino backend and driver support the qualified
  scalar lowering and correct ordinary scan transport.
- Whether ClickHouse separate assertions and output queries meet the required
  validation order and complete typed transfer on the selected table scope.
- The normal per-driver cancellation/close and exact decoding paths, plus
  local publication recovery independent of unknown remote query termination.
- Which advanced retained-state contracts can be met without changing their
  source-private authority. Unmet contracts keep those methods disabled.

These are qualification blockers for specific slices. They do not authorize
weaker semantics, an implicit data upload or an alternate execution route.
