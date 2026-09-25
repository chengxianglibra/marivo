# Session State and Runtime

Execution follows the [unified operator and backend ownership contract](python-analysis-design.md#unified-operator-and-execution-ownership). Backend-specific preparation does not change operator semantics.


A Session owns one investigation and its immutable Run/Artifact history under the
project's `.marivo/` directory. Use `mv.session.get_or_create(name, ...)`,
`mv.session.current()` and `mv.session.resume(session_id)` through their native
Help contracts. Identity resolution and report-timezone conflicts fail explicitly;
opening a different Session never grants access to another Session's inputs.

## Source boundary

Session owns `observe`, `population`, `events.match`, `lifecycle.replay` and
`source_bindings`. Dataset methods own downstream operations. Constructors load
needed semantic definitions and certified project snapshots without querying
sources. They capture immutable source binding values, period authority and
persisted report timezone. Credentials and live engine timezone are resolved only
for admitted source execution.

An Entity key is stable identity; version coordinates are separate. Membership
selection and observation windows are independent. Definitions retain exact
semantic dependencies, roles and field identities. RuntimeMetric expressions
normalize into the same governed graph and retain each carried output's identity.

## Fixed execution and storage

`execute()` admits a Run, fixes a registered implementation and writes one atomic
result. Shared logical inputs can share realization under exact identity; existing
materialized inputs remain immutable. No failed operation is retried on another
executor or storage target.

Output always uses project-local Parquet. Project manifests have no analysis
result storage setting.

Database result storage is absent. Registered native methods may scan immutable
Parquet using transient DuckDB execution resources. Every primary and private
part uses local storage authority, with independent schemas,
cardinalities, hashes and integrity checks. Cleanup covers interrupted and failed
publication without deleting another Run's resources.

## Accepted S0 input and execution protocol (inactive)

This is the accepted protocol for the
[first-round Analysis DSL slice](python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive).
W4 connects this protocol only through the private
`DatasetRuntime.execute_j1(...)` route for admitted J1 shapes. The public
Analysis DSL and ordinary `Dataset.execute()` routing are not activated by
this slice. The current exact-binding and registered retained-Parquet routes
above remain authoritative for methods not migrated to this protocol.
S2 P1 extends that private route with an ordered pair of exact observed
Artifact inputs for compare. Its descriptor records one member realization
binding and separate endpoint receipts; the fixed key includes both ordered
inputs and the binding. The two endpoint bindings are checked before Run
admission or Artifact row reads. A source-only compare realizes its shared
explicit member node once per invocation and receives a fresh Run identity
on every top-level call.

Classification follows only the current root's transitive data dependencies.
An explicit Materialized leaf is a fixed boundary: its historic source lineage
is not a live dependency. Source output passed to a Python stage within this
invocation remains source-only, not a historic Artifact input.

| Classified root | Accepted execution and placement |
| --- | --- |
| Source-only | Each admitted top-level `execute()` evaluates the current source and gets a fresh Run/evaluation identity. A historical Artifact with the same definition cannot satisfy this call. The source stage uses an admitted Ibis implementation or registered source-preparation plus Python method. |
| Fixed-Artifact-only | Exact receipts, method/protocol versions and input bindings define the same-Session key. A validated exact hit recovers the original Artifact without a Run. On a miss, receipt-checked retained input enters pandas; no DuckDB Parquet scan, source read, upload or re-evaluation is allowed. |
| Mixed fixed Artifact and live source | Reject before Run admission, source connection and Artifact row read. A materialized member list followed by a new read/observe is mixed; a still-Logical list followed by the observation remains source-only. |

The definition fingerprint identifies the same normalized question and explicit
input bindings, so repeating a source-only call does not change it. Explicit
logical node identity controls sharing only within one evaluation: the same
node has one realization, while separately constructed lookalikes do not merge.
No independent source queries are promised one transaction snapshot. A
different producing Run alone does not make two retained endpoints incompatible;
compare still owns domain, shared-member implementation binding and exact-key
admission.

The new protocol orders admission as follows:

1. Perform pure graph/capability checks and classify actual data dependencies.
2. Acquire the existing Session writer guard and reconcile an unfinished Run.
3. Look up and validate an exact fixed-only key; a hit recovers without a Run.
4. On a producer miss, allocate one Run ref and determine the key before
   `SessionStore.admit(..., run_ref=...)`.

A source key includes the new protocol domain, stable definition fingerprint
and this Run ref. A fixed-only key includes the new protocol domain and a
definition fingerprint bound to exact input receipts, binding and method
versions, but no fresh random value. Run, descriptor, receipts, publication
and recovery all bind the same determined key. The Store keeps its
`(Session, execution_key)` uniqueness. Two source evaluations of one definition
have distinct keys and immutable Artifacts,
while one fixed-only key has at most one published result.

A fixed hit performs the full recovery and integrity checks. Exact Artifact
references recover their own producing Run, never the latest result for a
definition. A failed second source evaluation cannot replace the first
successful Artifact. Cancellation or uncertain publication is reconciled
against its original Run/key/receipts; it never grants an automatic new
identity or source replay. Existing Store v6 atomicity and writer ownership
remain the authority.

## Atomic Store v6

A new Store publishes only a complete initialized generation 6 database. The
active Store path accepts only v6 schema; incompatible files there fail read-only
preflight. No migration, dual reader or in-place generation upgrade is provided.
Older generation files and resource obligations remain untouched.

A successful publication commits the Run terminal, Artifact descriptor, storage
receipts, Evidence and Findings together. A failed Run has no successful output;
an interrupted Run is incomplete until its explicit lifecycle operation. Store
writer ownership and caller-owned transactions govern all related records.

## Recovery and bounded reads

Use `session.runs(limit=..., cursor=...)`, `session.get_run(run_id)`,
`session.artifact(reference)` and `session.graph(...)`. Runs have closed
`incomplete`, `succeeded` and `failed` variants. Inspect the exact type before
reading success-only or failure-only fields. Graphs report recorded input/output
relationships; they do not infer or replay lineage.

```python
run = session.get_run(run_id)
if isinstance(run, mv.SucceededRun):
    saved = session.artifact(run.output_artifact_ref)
    saved.show()
```

Recovery binds a concrete Materialized Dataset from retained facts. It does not
open current sources. Retained filters, projection, aggregation and other admitted
continuations use complete retained rows/private state. Their Runtime guards
apply even when the final output is small. Source-offline cold recovery preserves
stored report/read/calendar authority rather than resolving the new host's zone.

`session.revalidate(reference)` exposes separate Artifact, storage-authority and
Evidence integrity assessments. It is not a source-freshness verdict, permission
to reuse stale values, or a business recommendation. Runtime cards and pages stay
bounded and do not expose raw secrets or private implementation inventories.

## Read-only execution cleanup

Normal driver cancellation and close are attempted on errors or interruption.
Missing query IDs, failed cancellation and unknown remote read status do not
certify server termination and do not block later work once Store commit state
and local writer ownership are safe. Failed Runs have no successful local output.
Unknown commit state and conflicting publishers still prevent unsafe continuation.
Recovery never resubmits the action.
Engine, driver and Ibis versions are diagnostics, not admission or identity facts.

## PostgreSQL Group A execution

The PostgreSQL adapter implements the [precise scalar Metric subset](python-analysis-design.md#postgresql-group-a).
It owns a read-only transaction and named cursor for each streamed query; normal
exhaustion, early close and failure release those resources. Metadata operations,
validation reads and primary output submissions are recorded as their actual
operations. Shared Runtime code does not invent DuckDB schema statements for a
remote adapter. Required assertions run even when primary output is empty.

Read-only credentials need no write, CREATE, TEMP or UDF privileges. Validation
and output queries may see different source states. Cancellation and disconnect
preserve the original failure; cleanup uncertainty does not prove server death.
Atomic publication, recoverable Session state, source-free binding hits and the
existing retained Parquet reader remain the same contracts. PostgreSQL cannot
import retained rows or retry on a different executor.

## MySQL and SQLite Group A execution

The concrete adapters own metadata, read-only cursors, cancellation and physical
validation. They share private scalar identity lowering and typed Arrow transport;
no public executor or retained-import capability is added. MySQL uses SSCursor;
SQLite uses native fetchmany with incremental statement stepping, while the engine
may materialize sorts or aggregates internally. Both drivers decode complete cells;
fetchmany limits rows rather than bytes, and Arrow allocates each decoded batch.
Early MySQL cursor close can drain unread responses. Numeric and non-finite
lowering belongs to each concrete adapter; the shared projection pass only handles
identity structure. Internal identity/date/integer conversion guards raise
structured MaterializationError with the owning Run reference.
Neither execution path reuses datasource authoring timeout/transaction wrappers.

Source storage/date assertions and normal semantic assertions precede publication.
MySQL floating SUM triggers native overflow before lossy conversion, and statement
warnings reject potential truncation. SQLite preserves native integer overflow.
Original errors survive cleanup failures. Each engine retains atomic publication,
process-death reconciliation and source-free cold Artifact/binding reads; unknown
remote read termination alone does not block safe local recovery.

The [analysis design](python-analysis-design.md#mysql-and-sqlite-group-a) owns the
precise table, type and method activation scope.

## Trino Group A execution

The Trino adapter owns cursors before execute, including metadata/assertion reads
and the wait for the first HTTP page. It cancels/closes each owned cursor before
closing the HTTP connection; connection close alone is not cancellation proof.
Failed cursor close remains owned until final cleanup. Original execution errors
survive cancellation/close failures and Runtime reports unknown remote read status.
Safe local recovery remains independent of remote termination confirmation.

Execution uses ordinary Trino relation scans, separate semantic checks, native
driver page fetching and typed scalar-to-Arrow identity reconstruction. The
connector name and relation form are observation receipts, not gates. No source
snapshot, shared observation, execution budget or compile-count requirement is
introduced. Driver capability probes and prepared statements are additional
operations, not Dataset primary queries. Read-only metadata access to
`system.metadata.catalogs` and the selected catalog's information schema is
required. Local publication,
writer ownership and cold source-free Artifact/binding reuse retain existing rules.

### ClickHouse scalar Metrics

ClickHouse Group A admits one datasource and one unversioned ordinary relation —
table or view — under any engine: direct-column sum/count/min/max, Population
filters, native-date scopes, same-Entity dimensions, aggregation, projection,
deterministic rank and limit. Distributed and multi-shard relations are accepted,
as are unstable reads: engines whose reads depend on background merge state (for
example ReplacingMergeTree) can return different rows between runs as merges
progress; replay of a committed snapshot is unaffected. Sampling, retained import
and source-private advanced methods remain unavailable. The relational/native-date
extension owns additional method admission.

Physical inputs are Int8/16/32/64, Float32/64, String, Date and explicit Decimal
precision up to 38, optionally Nullable. Unsigned inputs, Int128/256, Enum,
LowCardinality, FixedString, Date32, timestamp/timezone and nested values are not
admitted. Engine form is not restricted by this scalar-type extension.
Timestamp conversion is not qualified by this slice.

Use a SELECT-only account configured with effective `join_use_nulls=1`.
Metadata, remaining required assertions and output run separately; empty output never
bypasses Artifact validation. Source identity and time data are trusted. Exact
integer/Decimal sums widen internally to Decimal256 before checked output
conversion; overflow and non-finite output fail without publication.

Native row streams preserve Decimal and typed Entity identities without pandas
or raw source transfer to another engine. Each response is owned and closed;
driver close can drain unread data, so cancellation has no hard latency promise.
Connection close does not prove remote termination. Unknown remote status does
not prevent safe local recovery; partial output is never published. No shared
snapshot, execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap.

## Relational scalar sufficient-state execution

The [relational/date admission owner](python-analysis-design.md#relational-and-native-date-methods)
extends the existing adapters; it adds no executor, Store generation or retained-import
permission. Mean, weighted mean and ratio publish their sufficient components with
the primary result in the existing atomic transaction. Cold retained rollup reads
those components without reconnecting to the original source. Primary output
and part reads remain separate observations, without a shared
snapshot or automatic retry. Source-to-local Forecast, Kendall and time discovery
transfer the complete admitted aggregate input and execute synchronously.
