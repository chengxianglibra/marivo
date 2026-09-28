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

S4 P1 admits the finite public J1–J4 domain/relation chain on the same Session,
Run, Store v6 and J1 execution protocol. Source adapters are opened from the
loaded project's datasource declarations only inside an admitted evaluation;
the public API has no source-factory argument. Public J1–J4 publication records
a versioned continuation snapshot with its exact definition, method, domain,
input and receipt binding. `session.artifact(reference)` validates that snapshot
and recovers its concrete materialized shape and permitted local K without
loading the current Semantic catalog or reconnecting to the source. An older
private J1 Artifact without this snapshot remains outside the public recovery
contract and is not migrated. Non-DSL Artifact recovery is unchanged.
S4 P2 disclosure reads this validated snapshot and the Artifact descriptor for
kind, quantity, declared retained parts and admissible calls. It does not
reconnect to a source; missing or mismatched backing still rejects through the
exact Artifact repair path. A contract card is not full storage revalidation.

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
S2 P2 admits a source-only compare nested below strict numeric selection,
member projection, new observation and current-row statistic in one top-level
Run. The selected member definition remains a live dependency of that Run;
the compare member node has one realization within it. Fixed compare and
selected Difference continuations bind exact ordered endpoint or predecessor
receipts and execute in pandas. A fixed selected member followed by a live
read/observe remains mixed and rejects before Run admission or data I/O.

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

## Atomic Store v6 (current, before R4 cutover)

A new Store publishes only a complete initialized generation 6 database. The
active Store path accepts only v6 schema; incompatible files there fail read-only
preflight. No migration, dual reader or in-place generation upgrade is provided.
Older generation files and resource obligations remain untouched.

A successful publication commits the Run terminal, Artifact descriptor, storage
receipts, Evidence and Findings together. A failed Run has no successful output;
an interrupted Run is incomplete until its explicit lifecycle operation. Store
writer ownership and caller-owned transactions govern all related records.

## R4.1 frozen Runtime and Store target (inactive)

This section fixes the R4 cutover contract; the S4/J1 route and v6 protocol
above describe the current pre-cutover product, not a second R4 target.
R4.2–R4.5 must switch one existing Session/Runtime/Store owner
as a unit. The first new Store has SQLite `user_version=7`. It retains the v6
relations, foreign keys, `(session_ref, execution_key_digest)` uniqueness,
ordered Run inputs, resource journal, writer guard and atomic publication
transaction. Version 7 does not grant any old execution route permission to
write its descriptor. The generation check precedes Session or Artifact reads:
v6 and earlier Stores fail without mutation. There is no schema migration,
dual reader, automatic rebuild, or deletion of the old `.marivo` directory.
The repair is to preserve that project state and use a separate fresh project
root for the new generation, not a new Session name inside the old Store.

| Owner and new schema tag | Closed authority in v7 |
| --- | --- |
| Store `user_version=7` | Session, Run, Artifact and resource ownership; one successful Artifact per `(Session, execution key)`; committed Evidence and Findings. |
| `marivo.analysis.run_input/v1` | Root definition fingerprint, input class (`source` or `fixed`), selected plan and method-version digest, and ordered source-binding or Artifact-reference occurrences. The Store row owns Run ref and execution key; the input envelope cannot replace them. |
| `marivo.analysis.artifact_descriptor/v1` | Output domain, quantity, row and row-set contracts, realized schema, frozen semantic dependencies, selected method/implementation versions, completed check evidence, primary/part receipts, method-state payload and frozen continuation snapshot with its digest. |
| `marivo.analysis.receipt/v1` | A closed primary or part variant binding input, complete ordered keys, physical schema fingerprint, cardinality, exact local file manifest/hash/bytes and Parquet contract v1. A part additionally binds its role, contract ID/version and method-state version. Primary and parts have independent receipts. |
| `marivo.analysis.exchange/v1` | Schema-carrying `BatchStream` binding, four Cell tags/reasons, ordered keys, required part schemas, method and input binding, and check obligations. This is a transient exchange contract, not a second Store. |
| `marivo.analysis.method_state/v1` | The versioned, kind-dispatched state envelope and exact required part roles defined in [Dataset Methods and States](operators-and-frames.md#r41-frozen-method-state-and-evidence-target-inactive). Method-specific contract versions remain separate from this envelope version. |
| `marivo.analysis.continuation/v1` | Frozen graph, entity/dimension facts, semantic and method versions, exact input binding, and receipt/state premises from which the current valid K is derived. It is not a list of unverified advertised actions. |

The exact top-level field sets are fixed as follows. Each `kind` selects its
own required fields; no field is silently optional. `local` is the existing
closed `LocalReceipt` payload, including path, manifest entries and hash,
bytes hash/count, physical schema fingerprint, row count and Parquet version 1.

| Envelope | Required fields |
| --- | --- |
| Run input, common | `schema`, `kind`, `definition_fingerprint`, `plan_digest` |
| Run input, `source` | Common fields plus `ordered_source_bindings` |
| Run input, `fixed` | Common fields plus `ordered_artifact_inputs` |
| Artifact descriptor | `schema`, `definition_fingerprint`, `producing_run_ref`, `execution_key_digest`, `signature`, `row_contract`, `row_set_contract`, `realized_schema`, `semantic_dependency_digest`, `method_bindings`, `completed_checks`, `primary_receipt`, `parts`, `method_state`, `continuation_snapshot`, `continuation_snapshot_digest` |
| Receipt, `primary` | `schema`, `kind`, `input_binding`, `key_fields`, `local` |
| Receipt, `part` | Primary fields plus `role`, `contract_id`, `contract_version`, `method_state_version` |
| Exchange | `schema`, `signature`, `method_binding`, `input_binding`, `primary_schema`, `cell_contract`, `parts`, `check_requirements` |
| Continuation snapshot | `schema`, `root`, `entity_facts`, `dimension_facts`, `semantic_versions`, `method_versions`, `input_binding`, `primary_receipt_digest`, `part_receipt_digests`, `method_state_digest` |

`key_fields` is the complete ordered physical key with field names and types;
`parts` is the ordered, closed tuple of role, state contract and independent
receipt. The exchange `cell_contract` names the value/tag/reason fields and
their exact four-tag policy. The descriptor's `completed_checks` is a tuple
of the closed check-evidence variants below, not a list of claimed check IDs.
R4.3 implements only the transient exchange and method execution side of this
target. Its selected local input must exhaust receipt-checked primary and part
streams before pandas receives them; a partially consumed or failed stream
cannot supply completed evidence. The private consumer keeps check origin,
scope, ordered inputs and deadline while source and local methods run. It
allocates no Run and writes no descriptor or Store row. The v7 authority and
atomic publication remain R4.4 work.

The R4.3 transient `ExchangeContract` carries the exact signature, method,
input binding, primary schema, complete ordered keys, part schemas, Cell reason
policy, method-state kind/schema and pending check requirements. A produced
`ExchangeResult` retains independent primary, keyed parts, keyed method-state
vector and completed check records. The collector requires every required
numerical state part to agree with its primary Cell after full-key
association: row counts are nonnegative, row sums and means agree with retained
components, and Spearman counts reconcile with its status and coefficient.
Fixed current-row consumers compare the declared leaf value type with the
exchange Arrow value type before opening any receipt. The collector requires
every pending requirement to have a completed record and rejects foreign completions;
completion never follows an early close, failed close, iterator exception or
cancelled read. Source-derived Arrow staging belongs to its original R1
`SourceSession` and is released on both success and failure.

The descriptor embeds the bounded canonical snapshot text and its SHA-256
digest in the existing payload column; v7 adds no second snapshot table. The
snapshot's root and frozen facts determine possible continuations; only
validated method state, parts and completed checks grant actual K. Both the
descriptor and snapshot bind receipt digests without a cyclic descriptor
reference. Metadata uses canonical JSON with exact keys and stable ordering;
execution-key hashing alone uses the existing typed tuple encoding.

Old `marivo.dataset_artifact_descriptor/v1` and `/v2`, all
`marivo.j1_artifact_exchange/v1`–`/v3`, and
`marivo.analysis.public_continuation/v1` are rejected before retained rows or
current Semantic definitions are consulted. The existing `LocalReceipt`
Parquet physical contract remains at version 1 inside the new receipt; this
does not make an old Artifact readable. Missing, corrupt or version-mismatched
primary, part, snapshot or method state prevents a cache hit and cold recovery.

Admission uses only the reachable graph. A fixed leaf stops source-lineage
traversal. Mixed inputs, cross-Session inputs, incompatible ordered bindings and
known unavailable implementations fail before source open, Artifact row read
or Run allocation. After pure admission, the Session writer guard reconciles
the original unfinished Run. A fixed-only invocation checks its exact key and
all required receipts/snapshot before returning the original Artifact without
a new Run. A source-only invocation never looks up a historical definition hit:
it allocates a new Run/evaluation ref under the guard, binds that ref to the key,
and evaluates current sources. A fixed miss allocates one Run. All internal
stages share that Run and cannot publish independently. The same explicit graph
node is realized once per invocation; a new top-level invocation never shares
source realization. Chosen implementations do not change after a failure.

The following observable states are mandatory at each failure boundary. A
local file can exist before Store commit only as a journaled resource of its
own Run; its existence is never a successful Artifact.

| Boundary | Observable state and coordination |
| --- | --- |
| Before guarded admission, including busy writer | No new Run, source read or Artifact row read. Preserve the other writer's state. |
| Run admitted; check, execution, cancellation or output staging fails | One incomplete or failed Run and only its journaled resources; no successful Artifact. Cleanup is limited to that Run. |
| Primary/part write, receipt verification or pre-commit transaction fails | No successful Artifact or terminal. Roll back Store changes; reconcile the original Run and its owned resources without deleting another Run's files. |
| Transaction commits but acknowledgement is lost | Read back the original Run, output reference, key, descriptor and all receipts. Return success only after exact validation; never allocate another identity or replay source/algorithm work. |
| Commit cannot be established by read-back | Preserve the unresolved Run, journal and diagnostic evidence. Report unknown commit state and require explicit inspection/reconciliation; do not report success or retry execution. |
| Process exits with an unfinished Run | The existing guarded reconciliation resolves only its recorded resources and terminal state. A committed success remains immutable; no staging is promoted into success. |

R4's check record is kind-dispatched. All variants carry `origin_node`,
`check_id`, `scope`, `ordered_input_occurrences`, `deadline` and `status`.
`static` and `pending` carry no execution result. `completed` alone adds
`producing_run_ref` and `result_digest`, after the check has been exhausted
and passed in that Run. A pending or failed check cannot authorize
publication. The descriptor stores completed evidence and binds it to the
producing Run. Historical declarations and a previous Run's evidence cannot
discharge it.

### R4.4 private v7 publication

The v7 target is now available only through `SessionStore._graph_store` and
`DatasetRuntime._execute_graph`. Each Store instance selects exactly one
generation. The public constructor and J1–J4 chain remain v6 until R4.5;
neither chain can write into the other generation. Opening v7 in a project
containing an old generation fails before initialization or business reads.
Use a fresh project root, preserving all old state. There is one Store schema,
transaction owner, writer guard and resource journal; no schema migration or
format-probing fallback is installed.

After pure graph and persistence admission, the private Runtime acquires the
Session guard and reconciles only incomplete or still-obligated original Runs.
Source metadata is frozen before admission; the R1 factory opens after the Run
is allocated, and its exact physical source declaration must match the admitted
binding digest. The separately frozen semantic dependency digest is not the
source definition fingerprint; the opened R1 binding verifies the selected
physical source, not that semantic value. A new source invocation always obtains
a new evaluation key.
Fixed invocation keys retain every ordered input occurrence, even when a shared
leaf is read only once. Full primary/part verification precedes a cache hit.
A fixed miss allocates one Run; a verified hit returns the original Artifact.

The new canonical codecs do not call the legacy descriptor, exchange or public
snapshot codecs. The existing local Parquet v1 physical receipt remains the
inner `local` value. The fixed Run envelope records each occurrence's Session,
Artifact and producer references, primary/part receipt digests, binding, method
state version and snapshot digest; its ordered references must also match the
Store input rows. Reads recompute the execution key from this frozen admission,
without following source lineage or consulting current Semantic definitions.
The snapshot stores a closed frozen graph and verifies explicit shared-node
identity, entity/coordinate facts and semantic/method versions. Its UTF-8 budget
remains 256 KiB. Core dataclass changes therefore require deliberate protocol
review; decoding rejects missing, extra or noncanonical fields.

Primary and each required part have independent manifests, schemas, cardinality
and hashes under the same Run-owned output reservation. All are verified before
the Store transaction commits Artifact, Evidence, terminal and ownership transfer.
The transaction rechecks every receipt after the final pre-commit fault boundary
and refuses success while any resource obligation remains for the producing Run.
There are no registered Findings in this slice; the Evidence row binds the
complete descriptor and an exact empty Finding set. Numerical and check failures
cannot produce a successful terminal. An execution failure records
`execution_failed`; a new-process reconciliation of an abandoned Run records
`process_lost`. Cleanup is restricted to exact journaled paths.

If commit acknowledgement is lost, the private Runtime reads back the original
Run, Artifact, key, snapshot and all receipts. It never retries the algorithm or
source factory. An unavailable or contradictory read-back raises
`RecoveryPendingError` at `graph_commit_unknown` and preserves the recorded state.
An unfinished Run is never promoted from staged files. Independent-process tests
cover pre-commit exit, post-commit exit, lock release and a competing writer.

This is private publication and protocol recovery, not the public R4.5
`session.artifact` or dynamic-K cutover. Old public codecs remain solely for
v6 consumers and are deleted with those consumers in R4.5.

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
