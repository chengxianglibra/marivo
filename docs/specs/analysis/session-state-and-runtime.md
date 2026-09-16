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

No storage configuration is required: output uses project-local Parquet. Explicit
object storage is selected in project configuration:

```toml
[analysis]
storage = "object:archive"

[analysis.object_stores.archive]
endpoint_url = "https://objects.example.test"
bucket = "analysis"
access_key_id_env = "MARIVO_ARCHIVE_ACCESS_ID"
secret_access_key_env = "MARIVO_ARCHIVE_SECRET"
```

`storage = "local"` explicitly chooses the default. Additional named object
bindings can remain configured for reading exact older Artifacts. Credentials
are environment references; serialized project/Artifact state never contains the
resolved secrets. An absent or invalid explicit binding fails without fallback.
Changing the selected write target does not relocate retained results.

Database result storage is absent. Registered native methods may scan immutable
Parquet using transient DuckDB execution resources. Every primary and private
part uses the exact selected storage authority, with independent schemas,
cardinalities, hashes and integrity checks. Cleanup covers interrupted and failed
publication without deleting another Run's resources.

## Atomic Store v5

A new Store publishes only a complete initialized generation 5 database. Existing
v0, v2, v3, v4 or other incompatible generations fail read-only preflight; their original
bytes remain intact. No migration, dual reader or in-place generation upgrade is
provided. Older generation files and resource obligations remain untouched.

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
Unknown commit state, conflicting publishers and unresolved write-capable object
requests still prevent unsafe continuation. Recovery never resubmits the action.
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

Execution uses ordinary Iceberg scans, separate semantic checks, native driver
page fetching and typed scalar-to-Arrow identity reconstruction. No source snapshot,
shared observation, execution budget or compile-count requirement is introduced.
Driver capability probes and prepared statements are additional operations, not
Dataset primary queries. Read-only metadata access to `system.metadata.catalogs`
and the selected catalog's information schema is required. Local publication,
writer ownership and cold source-free Artifact/binding reuse retain existing rules.

### ClickHouse MergeTree scalar Metrics

ClickHouse Group A admits one datasource and one unversioned ordinary local
MergeTree table: direct-column sum/count/min/max, Population filters, native-date
scopes, same-Entity dimensions, aggregation, projection, deterministic rank and
limit. Relationships, versions, sampling, retained import and advanced methods
remain unavailable.

Physical inputs are Int8/16/32/64, Float32/64, String, Date and explicit Decimal
precision up to 38, optionally Nullable. Unsigned inputs, Int128/256, Enum,
LowCardinality, FixedString, Date32, timestamp/timezone and nested values are not
admitted. Distributed, Replicated, specialized MergeTree engines and views are
excluded. Timestamp conversion is not qualified by this slice.

Use a SELECT-only account configured with effective `join_use_nulls=1`.
Metadata, required assertions and output run separately; empty output never
bypasses validation. Declared floating values must be finite or NULL. Exact
integer/Decimal sums widen internally to Decimal256 before checked output
conversion; overflow and non-finite output fail without publication.

Native row streams preserve Decimal and typed Entity identities without pandas
or raw source transfer to another engine. Each response is owned and closed;
driver close can drain unread data, so cancellation has no hard latency promise.
Connection close does not prove remote termination. Unknown remote status does
not prevent safe local recovery; partial output is never published. No shared
snapshot, execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap.
