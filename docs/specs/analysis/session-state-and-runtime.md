# Session State and Runtime

Execution follows the [unified operator and backend ownership contract](python-analysis-design.md#unified-operator-and-execution-ownership). Backend-specific preparation does not change operator semantics.


A Session owns one investigation and its immutable Run/Artifact history under the
project's `.marivo/` directory. Use `mv.session.get_or_create(name, ...)`,
`mv.session.current()` and `mv.session.resume(session_id)` through their native
Help contracts. Identity resolution and report-timezone conflicts fail explicitly;
opening a different Session never grants access to another Session's inputs.

## Source boundary

The qualified public entry is `session.members`. It constructs a typed graph,
using schema-only R1 preflight when the Entity identity or selected value type is
unknown. This may connect to the source but submits no business rows and allocates
no Run. Graphs capture semantic dependencies, routes, coordinate order and report
timezone; execution verifies the same schema before business work. Other Session
Dataset constructors retain their signatures but R5–R9 execution is not qualified
for Store 7.

## Fixed execution and storage

`execute()` admits a Run, fixes a registered implementation and writes one atomic
result. Shared logical inputs can share realization under exact identity; existing
materialized inputs remain immutable. No failed operation is retried on another
executor or storage target.

Output always uses project-local Parquet. Project manifests have no analysis
result storage setting.

Database result storage is absent. Fixed J1–J4 continuations read verified
Parquet through Arrow and pandas, without DuckDB. Every primary and private
part uses local storage authority, with independent schemas,
cardinalities, hashes and integrity checks. Cleanup covers interrupted and failed
publication without deleting another Run's resources.

## Public J1–J4 input and execution protocol

R4.5 replaces the former S1–S4 scenario path with the typed graph Runtime and
Store 7. Public construction, execution, history and exact Artifact recovery
share that owner. The old J1 codec and scenario executor are removed. Exact
recovery validates the frozen definition, completed checks and every required
receipt before exposing the original public type or its continuations.

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
identity or source replay. Store 7 atomicity and writer ownership remain the authority.

## R4.1 frozen Runtime and Store target

This section owns the R4 protocol, activated publicly by R4.5. The new Store has
SQLite `user_version=7`. It reuses the existing
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
For a checked v7 Artifact, fixed graph construction consumes the signature
with its completed obligations removed only after validating the descriptor
and completed evidence. It cannot substitute a caller-authored signature for
the committed one. Independent fixed endpoints may differ in Artifact ref when
their frozen Session, member binding and complete coordinates agree; their
ordered receipts remain separate execution-key inputs.

Admission uses only the reachable graph. A fixed leaf stops source-lineage
traversal. Mixed inputs, cross-Session inputs, incompatible ordered bindings and
known unavailable backend/source forms fail before any source open, Artifact row
read or Run allocation. Source-only execution may then perform R1 schema-only
preflight to resolve physical Entity keys and fields absent from Semantic
metadata. It cannot submit or iterate business rows, and the execution binding
must match the selected schema before its first business read. Unqualified
physical types reject before Run allocation. After admission, the Session writer guard reconciles
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

### R4.4 v7 publication and R4.5 public selection

Public Session entrypoints select v7 through `SessionStore._graph_store`; all
J1–J4 execution uses `DatasetRuntime._execute_graph`. Each Store instance selects exactly one
generation. Public constructors select Store 7; the isolated private v6
R5 test harness cannot write into that generation. Opening v7 in a project
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

Original sum, sum-zero, count and ratio have distinct v1 state contracts.
The observed component's declared empty policy selects its state contract;
Metric-level nullable metadata cannot substitute for that declaration.
An optional `coordinate_state` part stores a sorted, unique partition of one or two ordered string coordinates
 per complete primary key, with the exact original component types.
Every read validates that partition against the original state: int64 sums
are exact; finite float64 sums use the qualified 1e-12 relative/absolute
partition tolerance. Empty contributions retain an empty coordinate list.
Parquet writing preserves nested Arrow field names so schema receipts remain
byte-exact after a cold read. A coordinate rollup consumes these saved components;
it does not reopen sources or average already-finished ratio values.

R4.5 uses these same verified descriptors for public `session.artifact` and
dynamic continuation contracts. The replaced scenario codecs have been removed.

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

### R4.5 public read and continuation boundary

Public create/resume/current/recent/inspect, Run pages and Session graphs use the
same Store 7 authority. Run results preserve ordered source or fixed input
variants from the closed v7 envelope. Artifact summaries derive from the checked
descriptor and its unique succeeded producer; no v6 descriptor fallback exists.

Schema-only R1 preflight before admission is authorized for unknown Entity key and
value types. It cannot read business rows or allocate a Run. Execution rechecks
that schema. Public R5–R9 Dataset methods remain structured refusals before I/O
and Run allocation. Fixed recovery and continuation do not consult current
Semantic definitions, datasource connections or DuckDB.

Coordinate state records one or two ordered string coordinates, with exact complete
key equality across primary and parts. A coordinate-refined ratio has one primary
row for each member and complete coordinate tuple in the union of its independent
component roots. Its nested state has exactly that row's component partition.
Integer components are exact; floating partitions use the specified finite tolerance.
A coordinate rollup selects one retained dimension and merges original components.
