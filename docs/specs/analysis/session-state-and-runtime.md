# Session State and Runtime

## Store 8 local trust contract (2026-10-07)

Store 8 trusts locally committed analysis results and private in-process compiler
objects. Readers decode retained types and required data without content hashes,
anti-tamper snapshot chains, repeated method proofs, or re-extracting Findings.
Production still enforces method input and output contracts. Session ownership,
execution identity, atomic publication, resource reconciliation, and actionable
I/O errors remain mandatory. Definition and execution-key digests identify work;
they are not content-integrity proofs.

Store 7 and earlier are rejected without migration or mutation. Preserve their
bytes and use a fresh project root. The current descriptor is v3 and continuation
is v4. Local receipts retain paths, file sizes, row counts and format version,
without file, manifest or schema hashes. Continuations no longer carry receipt
and method-state proof digests. `session.revalidate` and its result types are
removed. Historical phase sections below describe their original acceptance;
this section supersedes their integrity requirements.


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
That logical Run/result identity does not guarantee one physical scan. Native
expressions, checks and calculations may read independently; a successful source
check proves its own query only, not a later acquisition. No independent source
queries are promised one transaction snapshot. A
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
| `marivo.analysis.continuation/v2` | Frozen graph, entity/dimension facts, semantic and method versions, exact input binding, and receipt/state premises from which the current valid K is derived. It is not a list of unverified advertised actions. |

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

R4.6 installed-package evidence is recorded in the
acceptance ledger (historical record in Git history).
It rechecks this existing boundary with one candidate wheel and source-free
processes; it grants no additional backend or method qualification. Public
Dataset Help must disclose the Store 7 rejection even when a private generic
Dataset harness has historical backend qualification.

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

Local recovery trusts committed results and does not establish source freshness
or business validity. Runtime cards and pages remain bounded.

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

SQLite graph execution installs the temporary owned-query SIGINT relay only
on the main thread with Python's default handler. The relay calls the native
connection interrupt while execute is blocked; cursor and connection cleanup
remain on the execution thread. It restores the prior wakeup descriptor and
preserves custom-handler and worker-thread signal behavior. DuckDB retains its
native SIGINT behavior. The same source owner supplies deadline interruption;
publication checks preserve the original execution start and reject partial or
late output.

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
Captured SQLite exchanges retain nullable integer columns as object-typed inputs
to the temporary Ibis relation. Null does not convert int64 keys or values through
floating-point storage; timestamp strings retain their exact lexical form.
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
or raw source transfer to another engine. Each issued query ID remains owned
through its pending response and stream consumption. A separate same-reader
control connection uses the approved bound query-ID/user cancellation statement;
connect/read timeouts and server control execution are each one second, with
retries disabled. The account requires operator-configured
`SELECT(query, query_id, user) ON system.processes`, which allows general
visibility of those metadata columns. Missing permission yields structured
repair; Marivo does not grant privileges or escalate credentials.

Owner cleanup joins control work, closes the response and both connections,
and clears the private HTTP pool. Driver close can drain unread data, so
cancellation has no hard end-to-end latency promise. Connection close or a
successful control call alone does not prove remote termination. Unknown remote
status does not prevent safe local recovery; partial output is never published.
The Run reserves `clickhouse_owned_control_close@v1` before creating the control
and acknowledges it only after confirmed release. An unconfirmed close retains
an incomplete Run and typed pending recovery error. No shared snapshot,
execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap. The
[ClickHouse cancellation contract](../semantic/datasource-layer.md)
owns exact SQL, parameters and permission authority; precise native Runtime
witnesses retain their own phase and physical-profile scope.

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

## R5.1 state extension and recovery contract

Status: target frozen; R5 method, temporal and numeric qualifications remain
unverified. The R4 public graph/Store 7 contract remains the execution owner.
R5 extends core/rules, analysis.methods, GraphPlan/LoweredPlan and common exchange;
there is no observation-specific executor, alternate Store generation or v6
public recovery path.

A source execute receives a new evaluation identity. One explicitly shared node
is realized once within that execution; equal independently constructed nodes
remain separate. Static invalid/mixed/cross-Session graphs reject before business
I/O or Run allocation. Each selected physical route is immutable for that
invocation; check failure, cancellation or numeric failure never retries another
backend/local algorithm. Data-dependent obligations are completed only from the
actual bound consumer's evidence, after exhaustion and successful close, before
their consume/publication deadline.

For the [R5 state matrix](operators-and-frames.md#r51-method-and-state-contracts),
the existing descriptor/receipt transport carries primary Cells and full keys,
ordered component occurrence states, Subject/member/classification mappings,
coordinate images, coverage, version/temporal boundaries and certified snapshot
identity whenever required. Each part has an exact schema, binding, semantic
role, method/state version and receipt. Empty input still has schema and valid
empty state; absent state, an absent part and an empty part remain distinct.
Part order cannot substitute for role/key matching. Output value, components,
row-set contract and completed checks are verified together before publication.

Compatible additions use existing method/state version slots. A changed state
meaning/layout gets a distinct version and rejects mismatched old state; it is
not decoded permissively or rebuilt from source. Store 7 remains unchanged as a
generation. If a later implementation cannot represent a required shape in the
current protocol, that package must first amend this owner with an explicit
breaking rule; no implicit dual-read, migration or automatic state reconstruction
is authorized by R5.1.

A fixed key includes exact ordered Artifact inputs and receipts, definition and
method/state versions, parameters, required parts and time/coverage facts.
Integrity validation precedes cache-hit/consumer admission. Fixed-only execution
uses controlled Arrow/Parquet-to-pandas, cannot open DuckDB/remote connections,
and cannot load current Semantic models. Retained K is the qualified successor
set of the verified signature and parts, not a promise inferred from result
values. Missing/corrupt/version-mismatched components revoke affected K and reject
attempted execution. Distinct/quantile results have no original-rollup K;
permitted current-row statistics remain separate quantities.

Each method qualification requires source produce, fixed continue, and a separate
cold-recovery process for its promised K. Recovery tests remove/disable the source
and model loading, block backend connections, execute the continuation and compare
value, semantics and K independently. Main/part corruption, reordered parts,
empty batches, incomplete streams, close errors, cancellation and publication
failures must preserve atomic visibility. Existing `graph_primary_written`,
`graph_receipts_verified` and Store commit events provide graph-owned fault
boundaries; old quality-hook waits are not reused without a reachable consumer.


## R5.2 member/read frozen definitions

Source definitions now freeze declared snapshot/validity facts alongside their
exact selection. Field bindings retain ordered complete relationship keys,
resolved version owners and the attribute definition. Selection transports carry
the closed field kind so cold restoration distinguishes selected Measure values
from categorical integer values. Date/timestamp predicate literals have a closed
kind and ISO payload; JSON decoding cannot silently turn them into strings.

The primary and Subject schemas retain complete ordered keys. Source-only member
projection performs no default distinct or preliminary whole-source uniqueness
query. Actual consumed identities, scalar multiplicity and read coverage are
validated before publication. Fixed-only Subject images operate over verified
Arrow parts locally and never import Artifacts into DuckDB. A missing or corrupt
Subject receipt invalidates continuation. Temporal precision and timezone are
retained in the exact realized Arrow schema and checked receipts.

This extends the closed Store 7 continuation definition, without another Store
generation or dual reader. Pre-R5.2 snapshots missing the newly frozen definition
fields fail canonical validation; there is no compatibility fill, migration or
automatic source reconstruction. Start fresh project state or restore state made
with the same definition contract. This is a deliberate breaking refactor of
weakly reusable prior analysis, not a claim of cross-version Artifact recovery.

## R5.3 observation and occurrence-combination frozen definitions

One observation binds one occurrence per canonical `TargetMetricComponent`. Each
occurrence carries its own contribution root, branch filter, route, time range,
unit, amount type and complete target key, and each reduces independently before
any combination. Occurrence combination is a single common-graph rule variant: it
consumes N>=2 ordered quantity inputs on one frozen member binding and produces
the complete-tuple union on the complete target key. It is not a new executor, a
per-column projection product, a root intersection or a row-order alignment, and
it never multiplies a shared contribution table across branches.

The combination rule retains, per occurrence, its original component state,
coverage and `original_state` part, plus the ordered signed terms for `linear`
and the named original components and zero-denominator policy for `ratio`. A
`weighted_mean` occurrence retains the paired value/weight components and its
declared zero or missing-weight-sum policy. This extends the existing
descriptor/receipt transport and method/state version slots; no new Store
generation, dual reader or v6 public recovery path is introduced, and pre-R5.3
state missing these frozen occurrences fails canonical validation rather than
being filled or rebuilt from source.

The R5.3 review repair records a breaking frozen-state change within the same
unpublished v7 generation: `OriginalStatePart.empty_rules` binds one `null` or
`zero` policy per combined occurrence. Ratio state now uniformly carries
`numerator_sum`, `numerator_non_null_count`, `denominator_sum`, and
`denominator_non_null_count`. Count maps to its count magnitude and support;
sum retains its actual sum and non-null support. Policies are metadata, not
fabricated contributing rows. Source, fixed and coordinate rollups merge these
components independently and apply the retained policies before division.
A defined negative denominator is valid; only zero is a zero-denominator case.
Old ratio layouts are not read through a compatibility decoder.

Linear uses the same per-occurrence policies: empty count and empty-zero sum
remain Defined(0), while an empty-null sum propagates Null. The homogeneous
int64 source consumer specializes its exact qualification to the requested
ordered arity, retaining the same backend, domain, shape and route constraints.
Every input is checked against the first complete key set, and the Subject map
is projected once. Existing graph and 256 KiB continuation budgets still apply.

An omitted observation window means the admitted source without an added time
restriction; it is not a claim of all-history completeness, and a method that
requires an endpoint or cumulative anchor still rejects a missing required time
argument instead of degrading. Ownership, share, Session/Store identity, exact
ordered Artifact inputs, receipts, definition and method/state versions, required
parts and time/coverage facts remain part of the fixed key, and integrity
validation still precedes cache-hit and consumer admission. Diagnostic or
unregistered engine versions cannot alter the selected route, the frozen
observation definition or any published state.


The R5.3 follow-up adds `ObserveWeightedMean` and `original_weighted_mean`
publication under the same Store 7 generation. Its four int64 components bind
paired values/weights and row support; validation rejects inconsistent counts,
nonzero unsupported sums, out-of-range state, and Cells that differ from their
finish. `state_rollup.weighted_mean` and `state_rollup.linear` consume retained
original components in source and artifact execution. Cold-process tests recover
both through `session.artifact` with models and sources offline; missing original
state blocks recovery and advertised continuations. This bounded result does not
replace R5.7's full recovery matrix. Method-to-state-kind lookup in source exchange
and exchange validation now uses the existing MethodSemantics owner; protocol
state-kind/role inventories remain explicit closed wire-schema constraints.

## R5.4 breaking frozen-state acceptance

Within the same unpublished Store 7 generation, R5.4 accepts a breaking change
to the frozen graph and retained state contract. `OriginalReduce` binds an
ordered tuple of complete coordinates rather than one optional coordinate.
`RowState.merge` distinguishes fresh current-row statistics from merging retained
statistics. `row.sum` now requires `(sum,count)` state, replacing `(sum)`, so
empty groups and their support remain verifiable. `PartsTransport.classification`
retains an exact Dimension coordinate across selected-category publication;
`external_predicate` records the second exact-correspondence input. These fields,
component names, key correspondence and owning method/state versions are checked
before cache reuse or continuation admission.

Earlier incompatible frozen layouts are rejected. There is no compatibility
reader, implicit support-count synthesis, Semantic reload or source recomputation
to repair them. Missing or corrupt necessary state removes usable continuation
capability and execution fails. This is a source-tree contract acceptance;
installed-package qualification remains R5.7.

Group-domain completion persists complete target keys without Cells or state
parts. Fixed grouped numeric `summarize` retains its group coordinates and
operates on the current materialized rows, one per complete group. It does not
recover the pre-group member rows. For member revenues120/20/7, logical regional
row means are70/7; materialized regional revenues are140/7 and their per-group
current-row means remain140/7, with counts1/1. Original Metric `rollup` still
merges original components; subsequent RowStatistic `rollup` merges only that
statistic's own retained state and identity.

## R5.5 candidate frozen-state change

The in-progress R5.5 candidate changes the unpublished Store 7 frozen graph:
`DomainSignature.time_grid` carries the exact grid identity, ordered original and
clipped UTC bounds, partial flags, precision, report/boundary zones and certified
snapshot identity. `time.product@v1` uses the existing method registry and source
lowering; its required `kind="time_product"` field separates its frozen parameter
variant from existing target-group completion. Observation parameters distinguish row windows; original state records
`none`, `partition`, `repeated` or `overlapping` temporal policy. `OriginalReduce.time_mapping`
records and revalidates whole-cell coarsening. Fixed execution uses those frozen
facts and original components through the registered local reducer.

This changes definition and execution fingerprints even for signatures with no
grid. Incompatible earlier frozen graphs reject on canonical fingerprint/receipt
validation; no Store generation, dual reader, default-state repair or source
recomputation is introduced. Cumulative parameters additionally freeze ordered endpoint windows, reset identity,
report/boundary zones and certification digest. `GridVersionSelection` freezes
attribute versions independently for every cell. Scope identity distinguishes
named occurrences sharing the same bounds.

The `original_fold` state contract retains `samples` and `fold_kind` components,
with the same kind frozen in OriginalStatePart. Sample encoding is version-one
UTC-naive ISO microsecond keys, finite float64 spatial sums and int64 support
counts; source publication canonicalizes ordering, and decoding rejects duplicate
keys or malformed/nonfinite state. The registered local reducer validates aligned
pre-fold samples before any spatial merge and rejects overlapping temporal state.
Coverage, primary Cells, required parts, method state and receipts are checked
through the common exchange. Kind, version, part and receipt damage revoke K.
These additional fields also change the unpublished frozen encoding. Older
candidates reject; no inferred defaults or compatibility reader restore their
continuation. Produce, offline continuation and cold recovery are tested in
separate processes for both source forms and all three state families.
Zero-row fold results retain explicit string status/sample/kind columns and
boolean coverage columns. Fixed grouped reductions preserve that schema even
with no members, and whole-domain reduction produces the declared empty Cell.

## R6.1 composition state and recovery

Status: frozen implementation target, no new Runtime qualification. R6 extends
core/rules, analysis.methods, GraphPlan/LoweredPlan and the existing graph
executor/exchange/publication path. It must not wrap legacy comparison or
attribution publication as another executor. The [method owner](operators-and-frames.md#r61-method-rules-and-qualification-target)
owns arithmetic/RequiredParts/K, and [Analysis](python-analysis-design.md#r61-frozen-relation-composition-target)
owns user-visible variants.

Each operation's ordered graph edges retain semantic roles: current/baseline,
left/right, values/reference, target/opportunities/predicate inputs, ranked
values/partition inputs, or ordered table columns. Shared explicit node identity
means one realization per invocation, not one transaction over independent
sources. Predicate/view and requested Logical attribution expansion dependencies
are included in topology before planning; no hidden query may be introduced
while consuming a row. Source top-level execution remains a new evaluation.
Fixed keys include every ordered Artifact occurrence and all consumed part
receipts, versions, reference and scope identities. Swapping two roles, a
reference, or a nested endpoint changes identity even when displayed values
are equal.

Static kind, Session, mode, known domain/unit/template and retained-part checks
precede business reads and Run allocation. Schema-only preflight may refine
physical facts under R1. Dynamic injectivity, complete key images, bucket maps,
coverage, weight sums, partition and endpoint checks are bound obligations of
the selected plan and must complete before publish. Source preparation and
source-side checks use Ibis; registered local methods consume only controlled
exchange. A plan selects its route before execution and never retries another
route after failure. Fixed-only execution uses verified Arrow/Parquet→pandas;
no DuckDB, current Catalog/Semantic reload or source access is required.

### R6 state envelopes and versions

The existing Store generation remains 7. MethodKey semantic versions stay 1
for existing rules whose meanings are preserved (cell.difference, cell.ratio,
map_correspond and parts_transport); new closed variants are parameters in
those rules, not aliases for a second implementation. New R6 methods start at
semantic/implementation version 1. Expanding an existing implementation's
accepted inputs/parts changes its implementation contract to **4** on the
changed registration, above R5's version 3. Unchanged R5 registrations keep
their versions. Registry keys continue to name exact type/unit/time/domain/
source/route qualifications; a global version bump is not a qualification.

The current difference state/part contract at version 1 stores two endpoints
and cannot express general missing-side, recursive endpoint, design and mapping
facts. Its R6 replacement uses `marivo.analysis.state.difference` contract
version **2**, with corresponding difference part contracts/method-state
version **2**. Old difference state does not acquire the new meaning or continue
through that consumer; reject with a concrete re-execute repair. No v1/v2 dual
reader or migration shim is added. Existing unaffected R5 original/row state
remains version 1 with its currently accepted implementation contract.
New R6 state kinds `relative_change`, `relation_ratio`, `cohort`, `share`,
`penetration`, `standardized`, `ranking`, `table`, `attribution_additive` and
`attribution_component_mix` use `marivo.analysis.state.<kind>` contract version
1. Their new role contracts use `marivo.analysis.part.<kind>.<role>` version 1.
Transport preserves the source state version and includes selected scope;
it cannot relabel an older layout. These are frozen target discriminants,
not declarations that those codecs already exist.

| State family | Ordered retained facts beyond primary full keys/Cells |
| --- | --- |
| Difference / relative change / ordinary ratio | current_endpoint, baseline_endpoint, correspondence; presence tag independent of Cell; exact endpoint definition and realization, recursive child references, design/pairing, units/policies, original bucket coordinates; endpoint sufficient parts only when actually retained |
| cohort | target_subjects, opportunities, subject, coverage, predicate_inputs, decisions; complete original opportunity keys, t/u/f and explicit empty policy, selected keys and exact decision scope |
| share / penetration | fixed_reference, reference_proof and stratum_values; original immutable reference binding/keys/Cells or membership, support/intersection evidence, denominator and independent partition status |
| standardized | fixed_reference, reference_proof, strata and stratum_values; unit identity, all original weights/values including zero-weight non-Defined tags, sum check and numerical policy |
| ranking | values, ranks, ranking_domain, partitions, ordering; full original domain/values or verified immutable part references, tie policy and current selection map |
| attribution | current_endpoint, baseline_endpoint, basis, allocation, reconciliation, selection_scope; original target/basis/rule and endpoint sufficient state, ordered axes, resolution, typed Other/masks, side terms, original complete scope and current selected keys |
| terminal table | columns and column_bindings; authored label order, exact immutable view/input identities, full key correspondence, concrete types/Cells; no continuation K |

Roles may reference verified shared immutable parts under the common protocol;
they cannot point at an unverified path or recover data by replaying lineage.
Every role has a registered schema, exact key/mapping contract, input binding,
version and receipt. Distinct parts need not have identical row counts (a fixed
Singleton denominator is not expanded into an unowned same-row copy); their
explicit mappings must reproduce the consuming primary. The protocol's closed
Part/PartRole/state unions and validation must be extended together. No generic
JSON bag or name-based dynamic lookup replaces these typed roles.

All primary and required parts publish atomically under the existing writer
protocol. Missing, swapped, duplicate, truncated, malformed or wrong-version
parts reject before a fixed cache hit or new publication. Validation separately
checks values, frozen definitions, input realizations, correspondence, coverage
and promised K. Existing Artifact receipts do not authorize requalification
from current Semantic declarations. Failed validation closes exchange resources,
cleans partial publication and does not replay source nodes.

R6.7 recovery tests must run in a fresh process with source/Semantic access and
DuckDB disabled. They compare definitions/parts as well as values, actually call
each promised fixed continuation, and inject corruption into endpoint,
opportunity, reference, rank/view and attribution scope parts. A contract string
or repeat Artifact ID alone is not L6 evidence. The same non-editable wheel must
produce, continue and recover the installation journeys; this contract freeze
does not run or certify those journeys.

### R6.2 comparison encoding and execution

The connected comparison consumers use the existing graph schedule, exchange and
Store 7 publication transaction. Difference retains state/part v2;
`relative_change` and `relation_ratio` retain v1. Their implementations use
contract 4. A retained correspondence has separate Boolean presence, original
ordered coordinate vectors and three float64 error bounds; these are checked
against endpoint Cells and the frozen policy, including zero-row schemas.

All graph continuation roots use the canonical `graph-dag-v1:` deflate/base64
encoding, containing `marivo.analysis.graph_dag/v1`. The continuation envelope is
`marivo.analysis.continuation/v2`; graph execution keys use
`marivo.analysis.execution_key/v2`. Store generation remains 7, and physical
receipts, method versions and state/part versions do not change for this encoding.
Old recursive JSON roots, `comparison-v2:` roots and continuation v1 are rejected
with a source re-execution repair; there is no migration or dual reader.

The closed document has `schema`, `root` and an identity-sorted `nodes` table.
Each node appears once per explicit capture identity. Method records carry
ordered `(role, node)` input references, ordered source references, and ordered
`retained_endpoints` references. Source and fixed records retain their exact typed
definition facts. Different capture identities remain distinct even when their
structural fingerprints match. Repeated identities must have identical complete
definitions; decoding interns each identity into one graph object.

Retained endpoints replace the recursive strings formerly embedded in rule
parameters. They belong to the same definition closure, match the precise input
definition fingerprints and ownership, and participate in structural fingerprints.
They are not execution edges: retained source definitions cannot reopen a source,
add a stage, or promote rollup, share or attribution capability. Template inspection
and structural hashing use per-call memoization; there is no cross-run value cache,
history lookup or recovery from the current Semantic catalog.

The full persisted continuation remains bounded by 256 KiB and expanded definition
JSON by 4 MiB. Additional limits are 4,096 unique nodes, 16,384 input/source/retention
references (plus the root reference), and 128 nodes along any definition path. The encoder also
bounds inspected object occurrences by the reference budget plus the root. Both
producer and reader check these limits. Closed fields, identity conflicts,
reference kinds, ordered roles, missing references, cycles, unreachable entries,
noncanonical JSON/base64/deflate and trailing compressed frames reject. Envelope
and reference validation precede graph-node construction; typed derivation and
endpoint binding validation precede execution and cache hits. Publication rechecks
the snapshot and its existing receipt/state bindings. Old Difference state v1
still rejects independently of the snapshot version.

The affected original mean, weighted mean, ratio and linear implementations use
contract 4 to retain the float magnitudes needed by comparison error propagation.
Original float means retain absolute sums; ratios retain both component absolute
sums; weighted means retain absolute products and absolute weights; linear terms
retain their own absolute sums. This is sufficient-state transport for the
existing methods, not a new original Metric capability. Row-state, original-state
rollup and parts transport contracts carrying explicit endpoint definitions also
use contract 4. Fixed execution requires these actual versions and parts.

Numeric Measure reads use bind-project implementation contract 4: the frozen
quantity carries the declared measure unit and exact attribute-time binding.
Fixed continuation uses those retained facts without consulting Semantic.
Float fold and quantile state lacks a comparison error envelope; those operands
reject during composition rather than receiving an assumed zero error.

Float current-row sum/mean/extrema retain `row_state__error_bound` under the
row implementation contract 4. For sum/mean the stored bound belongs to the
retained sum, including input envelopes and reduction rounding; mean divides
that bound by the retained count and adds its finish rounding. State rollup
merges the bounds with the additional sum rounding, and extrema retain a
conservative maximum input bound. Missing required bounds reject comparison;
nonfinite or negative bounds reject exchange/recovery. Fixed comparison indexes
all retained operand components once by complete typed keys before pairing.
PeriodChange normalizes replaceable anchor coordinates in original-reduction
and row-statistic templates while keeping actual complete bucket maps intact.

### R6.3 transport and cohort state

Predicate trees store exact ordered input positions and authored composition;
execution keys include the full tree and every input occurrence. Retained
ancestor inclusion is checked against executable or frozen receiver definitions.
Decimal predicate literals use an explicit `decimal` kind and exact text in
frozen metadata; numeric-looking string literals stay strings. Decoding preserves
the literal type and Decimal scale before validating a frozen graph.
`domain.cohort@v1` uses the shared graph lowering/local execution and Store 7
publication protocol. Its state kind is `cohort`, state contract version 1,
with `subject` and `cohort_decision` parts. The latter retains every target's
counts and accepted flag, so its complete key image may be larger than the
selected primary image; all other parts keep their own existing image checks.
The primary image must equal the accepted decision keys. Counts, complete grid
authority, quantifier and empty policy survive source-free recovery; wrong schema, counts,
version or missing parts reject before continuation. Unknown is a consumer state,
not a newly qualified public producer.
Primary and Subject part row orders are independent. Cohort restricts the
Subject part by the accepted complete keys, never by primary row positions.

Fixed predicate inclusion retains independently captured, non-executable producer
definitions, checked against their original definition fingerprints. Their source
shape qualification cannot overwrite another Artifact's metadata closure. Actual
Artifact data edges keep their own capture identity and receipt binding. The
inclusion proof uses the exact retained Entity realization, semantic source,
complete key and total projection; execution still verifies key containment.
No evidence definition adds a source stage to fixed admission.

### R6.4 reference state qualification

Store remains generation 7. `share`, `penetration` and `standardized` state and
part contracts are version 1. The new `ReferenceStatePart` declarations carry
an immutable reference identity, complete input domain, input-owned Cell reasons
and, for share support, the exact original additive-state declaration.
`fixed_reference`, `reference_proof` and `stratum_values` are independently
keyed; standardized state additionally requires `strata`. A Singleton denominator
has one row and is never replicated over numerator keys. The proof and complete
weight/value parts preserve their original row counts through `where`.

Each part expression records its exact governed source dependencies. Shared
explicit graph nodes realize once per source execution. Reference arithmetic
consumes controlled Arrow exchange after Ibis preparation; fixed arithmetic uses
verified Artifact parts. Result error envelopes are reproducible from retained
operand bounds, including after selection. Recovery verifies receipt hashes,
contract versions, schema/key images, input Cell policies, support-state/denominator
consistency, strata and primary arithmetic before cache reuse or continuation.
It does not load current Semantic, connect a source or depend on DuckDB.
Publication uses the existing atomic writer and cleans only the failed Run's
resources. Table/Parquet fresh-process recovery and publication faults are covered
by `tests/analysis/numeric/test_analysis_references.py`; installed-wheel closure belongs to R6.7.

### R6.5 ranking and terminal table state v1

Store generation remains 7. The closed `ranking` and `table` method states use
contract version 1 and the existing descriptor, DAG snapshot, execution key,
receipts and atomic publication protocol. Ranking records values, ranks,
ranking_domain, partitions and ordering; the independent scope parts have the
same complete keys as the original domain, and current views have precisely the
current selection. Table records columns and column_bindings with authored
label/type/input order. The row contract stores each column's Cell reason policy.
Its primary contains the complete Cell vectors, including non-Defined reasons.

A terminal table is a distinct persisted result with no scalar primary Cell and
no continuation contract. Numeric node metadata is only a graph typing anchor;
it grants no NumericRelation behavior. Restoring the descriptor reconstructs the
terminal class and ordered exports. Ranking views use the existing
parts_transport method and verified projection, without an extra realization.
A table consuming both views of one explicit rank shares that rank and its source
node once. Independently captured equal definitions retain separate identities.
Source reexecution evaluates anew; fixed cache hits verify every receipt before
reuse. Neither failure reroutes to a different method or datasource.

### R6.6 allocation parts v1

Store 7, the shared DAG snapshot, execution key and atomic writer now carry
attribution_additive/attribution_component_mix state contract v1. The ordered
required roles are current_endpoint, baseline_endpoint, basis, allocation,
reconciliation and selection_scope. Endpoint and original-target parts use the
complete comparison scope; allocation/reconciliation use original output keys;
selection_scope uses exactly the selected primary keys. Selection never truncates
the original allocation or reconciliation parts.
Allocation carries contribution/current/baseline values and their finite R5 error
bounds, keyed together. Each numeric view transports and consumes its own bound.

Frozen declarations retain original sufficient-state and coordinate schemas,
coverage, ordered axes, method/mode/Top-K, complete partition status and view.
Exchange verifies original endpoint Cells and component partitions, recomputes
the common mapping and side terms, independently reproduces the target and each
resolution total, then verifies selected values and keys. Independently ordered
parts are matched by complete keys. Missing/corrupt state, wrong schemas/versions,
contradictory zero basis, zero/unstable denominator and excess reconciliation
residual reject before publication or fixed reuse. No balancing row is introduced.

Ibis owns source observation, grouping, scope correspondence and key checks.
The registered local arithmetic owner consumes controlled exchange, then returns
to the same SourceSession-issued staging and common publication protocol. Fixed
execution consumes checked Arrow/Parquet parts without current Semantic or
DuckDB. AN11's unused hand-built summary SQL is deleted; AN12's legacy source
proof hook remains an explicit pre-execution rejection with no statement call.
The public replacement has native driver submission evidence, independent raw
fact oracles and three-process source-offline continuation/recovery tests.
Legacy family dispatch/helper deletion and installed-wheel closure remain R6.7;
remote backend qualification remains R9.

## R6.7 retired recovery branches

R6 recovery uses the existing graph descriptor/continuation codec in Store 7.
General Metric Delta/Attribution descriptor semantics, evidence codecs and
publication/recovery branches are removed, without dual reads or lineage
reconstruction. Only actual Event funnel comparison/allocation shapes remain
under private R7 owners; source admission remains blocked. Neutral ordered-input
bindings and scalar Finding values remain for their actual Event/R8 consumers.
Original group reductions restore the same MaterializedGroupedNumericRelation
variant and K as execution; Singleton reductions restore MaterializedRolledNumericRelation.

## R7.1 frozen domain execution and retained state

Status: accepted target, 2026-10-01; static freeze only. This owns F05/F12-F14
execution identity, state schemas, placement, resources and Findings. The
[domain API](python-analysis-design.md#r71-frozen-domain-api-target) and
[operator rules](operators-and-frames.md#r71-frozen-domain-method-rules) own
signatures and semantics. The
R7 ledger (historical record in Git history)
records actual blockers and qualification/test responsibility.

### Method identity and parts

Physical Entity/Metric SourceDefinition leaves remain the access boundary.
Closed method parameter captures carry exact Event, StateModel, BusinessOrder,
participant, coverage and relative-window definitions and their ordered
dependencies. EventCapture binds its occurrence Entity leaf; StateModelCapture
binds each distinct trigger Event once; OrderCapture binds exact sequence field
owners and precedence roles. All captures are serializable frozen values in the
same DAG, never a closure or lineage-only Ref. Definition identity includes full
source/definition versions and input captures; separately constructed equal
definitions remain independent realizations.

New domain methods below have semantic/rule/state version v1 and implementation
contract v1. Each named retained part has contract version 1 and carries its
complete typed keys, schema, bound input identity and digest. Extensions of
existing methods preserve their semantic method key and introduce a separately
qualified domain/Duration implementation; old implementations gain no new cells.
New R7 implementation identities are `r7.<method-key>.<route>@v1`, selected by
the exact type/domain/source/time qualification key. This v1 is independent of
existing R5/R6 implementation contracts (including contract 4); it does not
renumber or downgrade them. Source and fixed consumers retain the producing
identity, their own executing identity and both versions.

| Method key / parameters | Output/state kind | Required retained parts and continuation condition |
| --- | --- | --- |
| occurrence.prepare@v1 / Event captures, Subject input, time bounds, order, coverage | occurrence_inputs | exact occurrences, participants, order facts, coverage and capture authority; reusable only for the bound downstream methods |
| journey.match@v1 / pattern, policy, start/follow-up bounds | journey_assignment | complete Journey domain, dense assignment/reach, exact steps, SubjectBinding, input/order/coverage facts |
| journey.duration@v1 / exact from/to steps | journey_duration | full Journey domain, status/endpoints/follow-up, assignment/reach and SubjectBinding |
| journey.dropped_before@v1 / exact noninitial step | journey_truth | full opportunity/reach and coverage; first_per_subject only |
| funnel.reduce@v1 / axes | funnel_components | assignment binding, step/axis domain, seven counts, three rate Cells, entry-time axes and complete-partition evidence |
| funnel.compare@v1 / ordered current/baseline | funnel_comparison | both endpoint components/domains and funnel-period compatibility; read preserves the bound endpoint roles |
| funnel_ratio_mix@v1 / target, axes, mode, Top-K | funnel_allocation | both full component partitions, target, common mapping/masks, all resolutions, allocated sides/error bounds and original reconciliation scope |
| history.replay@v1 / full members + occurrence preparation, model, from_inception, report window | canonical_history | exact full Subject domain/classification, inception/known-prefix/coverage, clipped intervals with original boundaries, all legal transitions and occurrence violations |
| history.in_state@v1 / state, checkpoint | history_truth | full Subject ledger, state/interval and end-left-limit authority; missing interval is not missing Subject |
| history.distribution@v1 / checkpoints, axes | state_distribution | checkpoint/state/actual-axis target domain, checkpoint axes, exact Subject classifications and count components |
| history.transitions@v1 / report window | transition_summary | complete declared pair domain and all legal trace entries, self/zero-duration included |
| history.violations@v1 / report window | violation_rows | exact violation occurrences/time/kind/state, model and SubjectBinding |
| history.intervals@v1 / report window | interval_rows | original/clipped boundary causes, statuses, exact observed ticks, SubjectBinding |
| history.dwell@v1 / completed_window_fragment_duration@v1 | dwell_statistics | complete state domain, classified interval counts, exact completed ticks/order statistics and tick sum/count |
| anchor.bind@v1 / Event role or Journey starts, population, during, order | anchor_domain | full Anchor keys/starts, source assignment or occurrence input, exact Subject map, frozen order/coverage |
| anchor.observe@v1 / Metric/RuntimeMetricExpr, RootRoutes, relative window | anchor_observation | per-Anchor Metric components and Cells, exact windows, contribution-use keys, coverage and root/path definitions |
| anchor.retention@v1 / returning role, relative window, coverage | anchor_retention | original Omega, K+/K-/K?, windows, exact return uses/absence facts and Anchor-to-Subject mapping |
| retention.by_subject@v1 / any_anchor or every_anchor | subject_retention | original instance status fibers, explicit Subject-image Omega, quantified truth and bounds |
| existing parts_transport@v1 / bound selection, completed, owned read, Subject image | receiver-specific transported state | preserve/rekey the required parts and explicit original scope; never recreate members from counts |
| existing row.mean@v1 / Duration current rows | row_statistic | exact checked tick sum/count, unit, complete selected domain and final rounding policy |

These are target registrations, not a second runtime registry. The single
methods owner must accept them before graph derivation/lowering consumes them.
The qualifying implementation supplies exact ordered types/domain kinds,
source/table/time shape, checks and resources. Source/current-version checks have
consume deadlines; exhaustive keys, coverage, partition and part checks complete
before publish. Fixed receipt checks precede row consumption, K and exact hit.
No construction/plan allocates a Run or performs business I/O.

Conditional K is derived from the bound method and its verified required parts:

| Producer/view | Permitted continuation and exact condition |
| --- | --- |
| journey.match | duration and SubjectBinding views require assignment/reach/map; funnel and dropped_before additionally require first_per_subject |
| journey.duration / completed view | owned relations, exact where/Subject image and Duration row.mean require the selected full Journey keys and transported endpoint/unit state |
| funnel.reduce | owned read and period compare require full components; compare additionally needs both compatible complete endpoints; attribute needs complete axis partitions, with explicit Logical same-assignment expansion or already retained fixed axes |
| funnel.compare / funnel_ratio_mix | read and contribution selection/table retain endpoint/allocation/scope parts; selected views lose complete-partition K; no arbitrary rate rollup or Subject reconstruction |
| history.replay | each named History method requires the Subject ledger and its specific trace/interval/checkpoint parts from the table; no bag replay/merge |
| history.in_state | decidable where/members uses the Entity domain and known-state proof, without an instance through binding |
| history.distribution / transitions / dwell | owned read/selection/table retain full state/pair/count/statistic scope; no default Subject map or summary-value rollup |
| history.violations / intervals | owned read/where and members require the total model SubjectBinding; interval Duration row.mean also requires exact observed ticks/unit |
| anchor.bind | observe/retention require the frozen instance keys/windows/map and all explicitly captured downstream Metric/return parts; an unseen live dependency is mixed |
| anchor.observe | existing Metric read/selection/table rules apply per Anchor with original components/use bindings; removing Anchor coordinates grants no original rollup without an independently admitted disjoint/allocation proof |
| anchor.retention / retention.by_subject | status views and known-true members retain original Omega/coverage/map; subject quantification requires complete fibers; bounds have no arithmetic/rollup K |
| parts_transport / row.mean | transport preserves only capabilities justified by surviving rekeyed parts; mean retains exact sum/count/unit for its existing row-statistic continuations, never original Metric state |

Missing required parts removes the corresponding K and causes an attempted
continuation to reject before row reads/exact hit. Materialized continuation
constructs the paired Logical fixed graph; execution and cold restoration must
derive the same K from validated parts, not a serialized capability claim.

### Exchange schemas and source consistency

The typed Arrow schemas have the following fixed roles. Each scalar preserves
its declared physical type; temporal values use their actual captured unit/time
authority, governed by the R7.2 precision amendment, counts are checked int64, and every nullable Cell has its own tag and
reason. Definition/capture metadata is immutable and receipt-bound, not repeated
untyped JSON inside value columns.

| Part role | Complete row key | Required payload beyond the key |
| --- | --- | --- |
| occurrences | Event binding + complete occurrence K | exact Subject K, occurred_at; closed no-sequence/integer-sequence/enum-sequence schema variant |
| coverage | exact Event/source/version + Subject/interval binding | observed/declared/mixed/unknown basis, origin or bounded claim, exclusive extent, known prefix, capture authority |
| assignments | Journey K + exact step key | assigned occurrence/time Cells, reach truth/reason and input binding |
| subjects | exact instance K | exact complete Subject K and role/definition binding; total single-valued map |
| duration | Journey K + exact step-pair binding | closed status, started/completed/follow-up Cells, completed/observed ticks and unit |
| funnel_components | step + complete historical-axis tuple | seven exact counts, first/previous denominator roles, full target scope and coverage |
| subject_history | complete input Subject K | inception/NotStarted/Unknown classification, known prefix, origin and follow-up authority |
| transitions | Subject K + canonical transition ordinal | occurrence key, instant, from/to state, legal/inception disposition, report-window inclusion |
| violations | exact trigger occurrence K | instant, known state, illegal/terminal kind and model binding |
| intervals | Subject K + original canonical interval ordinal | original/clipped start/end and causes, state, completed/right/coverage censor, left clipping and observed ticks |
| checkpoint_axes | Subject K + exact checkpoint | exact historical Dimension tuple and version/path facts |
| metric_candidates | Metric component/root + original contribution K + exact time/version key | exact value/state and null/empty policy, path allocation, historical axes, support mapping and candidate scope |
| anchor_uses | Anchor K + component/return occurrence K | exact per-Anchor window, component state or return truth, coverage and use binding |
| retention_status | original Omega instance K | exactly one true/false/unknown tag and its proof/coverage binding |
| allocation | full resolution/axis/mask/kind key | exact endpoint counts, target/side/contribution values with bounds, common Top-K mapping and original scope |

No schema admits an unkeyed positional join, opaque summary in place of required
state, or a count-only reconstruction of Subjects. Empty streams still carry
their exact schemas/domains. Parts transport records whether scope was filtered,
and which complete target/partition facts still hold. Retained interval ordinal
is tied to the original trace, never renumbered after clipping or selection.

Validation consumes the captured stream that matching/replay/reducers actually
consume. Native multi-query captures needing a shared snapshot require one
verified source-adapter transaction/snapshot authority, including every Event,
historical axis and later Metric dependency. Driver APIs own transaction/control;
no hand-written business SQL or Event packet returns. A file-source capture
requiring common consistency must bind a verified immutable source manifest with
exact files/content identities; a bare path or independent reads provide no
such guarantee. Missing authority blocks that exact route. This freezes the
requirement, not an implemented SourceSession snapshot API. Captured inputs can
prove only their recorded authority, not a global transaction implied by a Run.

### Source placement and pushdown priority

The 2026-10-01 user instruction makes qualified source pushdown the preferred
plan. Select the exact qualified full-Ibis/source-native implementation first
when it preserves the whole requested retained contract. Membership/time filters,
participant joins, historical attributes, projections, key/order checks and
safe grouped count/component aggregation belong at the source whenever expressible.
Do not transfer irrelevant columns, unconstrained Event histories or a whole
population's raw contributions merely because a Python kernel is available.
Canonical assignments/reach should be computed at source when an exact qualified
implementation exists; funnel aggregation over those assignments should remain
at source. Backend native functions are candidates in the same method registry,
not a separate Event executor or backend-name branch.

An ibis_python route is selected before execution only for the precise operation
not represented by an equivalent qualified source implementation. Its declaration
records the missing semantic/lowering capability, irreducible projected input,
bounded member/time candidate envelope, row/byte estimate and execute deadline.
It still pushes all safe preparation/aggregation ahead of exchange. A source
failure never switches route. Source-native and local implementations compare
full domain/assignment/state/K, not just funnel totals. Physical qualification
includes server/Ibis versions, function modes, type/time limits, query/submission
identity and transferred rows/bytes as well as correctness and cancellation.

Local selection followed by observation collects all later source dependencies
from the Logical DAG up front. Before local selection, source prepares governed
Metric candidate support/components within the original member/time envelope,
plus all necessary historical axes. A registered local consumer limits them by
the actual Subject image or each actual Anchor window. Complete components may
be preaggregated only over keys whose retained support proves that restriction
remains exact; a candidate-population scalar is insufficient. Source-computable
selection stays in the source prefix where qualified. No local IDs upload,
source-after-local query, rematch/replay or hidden second selection is permitted.
Explicit Materialized members/Journey + live source remains mixed and refuses.
An unproved candidate time envelope or unenforceable execute deadline blocks the required cell; it does not
authorize all-history collection. Fixed-only consumes verified Arrow/Parquet
parts through artifact_python, without DuckDB, current Semantic or lineage reads.

### Unified execute time budget and versions

r7_execute_v1 is the sole private execution budget: 600 monotonic seconds per
`execute()` invocation, with no new public budget argument. The clock starts
on execute entry and covers admission, dependency preparation, source queries,
exchange, local computation, fixed receipt/cache validation and publication
performed by that call, through successful return. Every stage and route shares
the same remaining time; neither batching nor a source/fixed stage resets it.
The latest user decision removes occurrence/attempt/Subject/step/tie-width,
part-row, captured-byte and sorting-space execution quotas.

Source-native, ibis_python and artifact_python implementations must enforce the
same deadline, including source timeout/cancellation authority and late-result
rejection. An implementation without this authority remains blocked. Estimates
and actual exchanged rows/bytes support pushdown assessment, not extra budgets.
Expiry, cancellation, bad batch or early close fails atomically and closes
source/cursor/reader/staging under existing resource owners. Deadline-aware
publication cannot publish a late or partial Artifact/Evidence/Finding set.
Cleanup preserves its actual acknowledgement and never labels truncated data
complete. Schema, type/overflow, identity and completeness checks remain method
correctness conditions; Finding caps and protocol-envelope limits remain their
own contracts rather than execution quotas.

MySQL source graphs prepare a separate same-datasource control connection before
business submission. The approved provider statement cancels only the still-owned
native data connection ID; its parameter and purpose are closed. Control setup
and requests have one-second native connection/read/write bounds and checkpoint
the shared execution budget. Cancellation also shuts down the owned data socket,
then owner-thread cleanup waits for control work before closing cursors and both
connections. Control errors do not establish remote termination: receipts retain
`remote_unknown`, and a control-close error prevents successful publication.
During a main-thread graph with Python's default SIGINT handler, a temporary
signal wakeup listener requests the same owned cancellation while mysqlclient
blocks in a native read. It preserves the handler and forwards notifications to
the previous wakeup descriptor, restoring that descriptor before releasing the
listener. Concurrent timer and signal requests issue at most one owned KILL.
Cursor close remains on the execution thread. Query ownership lasts until cursor
release, including failed/early-closed unread streams; terminal submission state
alone cannot remove an active query's cancellation target. If interrupted
mysqlclient response draining fails, confirmed disconnection of the owned data
connection replaces that drain and preserves the original KeyboardInterrupt.
For a borrowed backend, only native connection-loss errors 2006/2013 after the
exact owned cancellation preserve the original interruption while retaining
`close_failed`. SourceSession does not disconnect the borrowed backend. Its
outer datasource connection owner must confirm disconnection before
`mark_backend_disconnected()` changes that cursor state to closed; failed outer
release retains the existing typed error and unconfirmed state. Custom signal
handlers and worker-thread callers retain their existing signal behavior.
The Run reserves `mysql_owned_control_close@v1` before control creation and only
discharges it after confirmed owner release. Unconfirmed creation/close retains
the obligation and an incomplete Run; reconciliation raises a typed pending error
instead of assuming that a read-only control was released. It never targets a
connection from a later session. Bounded native tests cover cancelled graph
atomicity and pending control-close recovery; formal backend qualification still
requires a frozen candidate and its complete required scenarios.

Store generation remains 7. The existing graph-dag-v1 document envelope and
continuation/execution-key v2 envelopes carry the new closed method/state/part
variants, with their explicit v1 contracts. No structural envelope rewrite or
dual read is required by this freeze; a later incompatible structural change
must explicitly supersede this owner with a new version before encoding it.
Legacy Event/Lifecycle Dataset descriptors/parts are not graph variants and are
refused, not migrated or rematched. Source reruns allocate a new Run/capture;
fixed exact hits verify all definitions, ordered inputs, schemas, digests,
completed checks and parts before reusing the Artifact, without another Run.

### Evidence and Findings protocol

The common graph Store replaces its unconditional empty-Findings assumption
with an exact producer policy. Target producer contracts are:

| Producer | Extractor / body / policy versions | Eligibility and ordering |
| --- | --- | --- |
| funnel.compare@v1 | graph.funnel_delta_findings@v1; existing closed FunnelDeltaFindingValueV1 body; bounded_algebraic_findings@v1 | calculation_status=ok, complete uncensored components and finite defined delta; abs(loss_rate_delta) descending then full typed row key |
| funnel_ratio_mix@v1 | graph.funnel_contribution_findings@v1; existing closed ContributionFindingValueV1 body; bounded_algebraic_findings@v1 | reconciled complete resolution, valid finite sides/contribution, status ok or zero_total_delta; abs(contribution) descending then resolution/full typed row key/kind |
| every other R7 producer, including reads/selection/row mean | graph.no_findings@v1; no body; zero_findings@v1 | eligible/emitted/truncated=0, empty set digest and explicit no-extractor authority |

Both nonzero policies use cap=1000. eligible is counted before cap, emitted is
min(eligible,1000), truncated=eligible-emitted. No eligible rows is a valid empty
set with the producer's nonzero-policy/extractor version retained. Bodies retain
the existing exact business fields: compare's step/presence, seven count pairs,
two loss rates and delta; contribution's method/masks/kind, two allocated sides,
target/contribution, typed three shares, rank/status and causal_claim=none.
They never contain raw Subject/occurrence identities. Only governed step/axis
coordinates are public; full internal keys may break ties without projection.
Violations are domain rows, not automatic Findings.

Finding identity binds Session/Artifact, producer/state/extractor/policy versions,
ordered input roles and capture/Artifact identities, exact definition/scope,
public coordinates and body digest. The existing neutral input-binding owner
remains shared with actual R8 consumers. Source inputs have captured identities,
not invented source Artifact refs. Evidence includes actual finding_count,
finding_set_digest, extractor/policy versions and all three selection counts.
The canonical set digest uses the deterministically ordered full bound Findings,
not only IDs/counts; each Finding's body and exact input/Artifact binding is checked.

Artifact, Evidence, all Findings and terminal Run state publish in the existing
Store transaction after extraction/validation succeeds. Extractor or transaction
failure publishes none and preserves previous artifacts. graph_publication,
graph_store, Session evidence_digest/findings/finding reads, cold recovery and
exact hit share this validation. Missing/body-corrupt/swapped/foreign/version-wrong
Findings, wrong digest/count/cap and falsely empty sets reject before returning
rows, K or a cached result. No skipping malformed records, second Evidence store,
Dataset codec read or origin replay is admitted.


## R7.2 occurrence preparation and F13 foundation

Accepted implementation boundary, 2026-10-01: private `occurrence.prepare@v1`
and registered preparation/Subject-image/observation stages use the existing
single graph, method registry, snapshot codec, exchange, Runtime and Store 7.
Event/StateModel/BusinessOrder captures bind full immutable definitions,
fingerprints, participant paths, version/time/order/coverage and source node
identities. Repeated model triggers share one explicit Event capture; independent
MethodNode construction retains independent realization. Constructing and
planning these nodes reads no business rows and allocates no Run.

Native DuckDB tables use one native transaction on the same connection for all
checks and reads. Exact local Parquet files are copied, hashed and held read-only
before submission; capture manifests bind path/content/source identities. A
file replacement after capture cannot alter consumption. No remote, glob or
multi-file qualification follows. Driver-owned transaction/control calls do not
author SQL. Every analytical statement is compiled by Ibis and submitted unchanged.
Occurrence rows preserve full typed occurrence and Subject keys, captured time,
conditional sequence columns and exact declaration/observed coverage facts.
Insufficient coverage is recorded as unknown; it is not proved by row counts or
max timestamps. Contradictory/malformed bindings fail with structured R7 errors.

The source prefix prepares all explicit downstream contribution, time, path and
historical coordinate dependencies inside the original member/time envelope.
The registered local consumer restricts keyed sufficient contribution rows to
the actual Subject image. Its restriction primitive also verifies full Anchor
keys and overlapping half-open windows; public Anchor production belongs to
R7.7. Count and int64/float64 sum/mean retain complete components, null support,
original scope and coordinate partitions. Other Metric/expression families have
no R7.2 local qualification. The later relative Metric matrix remains mandatory
in R7.7; this foundation does not pass those cells. Unbounded envelopes, local
result uploads, source-after-local reads, implicit reselection and scalar
preaggregation lacking exact support are refused. Source submissions finish
before the first local consumer, including for empty selections.

A shared private monotonic 600-second budget begins at execute entry and covers
source capture/query/batches, local checks, fixed reads and publication/return
checks. The source adapter interrupts its native DuckDB query on expiry; resource
closure and Store 7 failure prevent partial precommit publication. There is no
row or memory quota. Durable commit acknowledgement continues to use the existing
Store 7 original-Run recovery protocol. Once the transaction has durably committed,
acknowledgement and exact original-Run read-back preserve that success even when
the deadline expires; an outer return check cannot relabel it as an ordinary
timeout failure. All precommit work remains subject to the shared deadline.
Native query cancellation, early stream
close, cross-batch key validation and below/at/above deadline boundaries are
phase-specific R7.2 evidence, not R7.9's full closeout.

Precision loss is accepted by the
[time owner](timezone-and-calendar-design.md#r71-frozen-occurrence-and-relative-window-time).
`r7.capture_authority`, `r7.precision` and `r7.coverage` schema metadata are retained
in artifact receipts; the Evidence descriptor digest binds the schema/parts and
capture definitions. Reads, exact hits and fixed/cold continuation revalidate
these closed facts. Public matching, replay and domain results remain disconnected;
future `.show()` owns precision disclosure when those public results connect.
See the R7.2 evidence index (historical record in Git history).


### R7.4 implementation boundary (2026-10-02)

The public first-per-subject funnel, exact owned reads, compatible period compare,
and funnel_ratio_mix allocation now use the unified graph, registry, Runtime and
Store 7. The Ibis prefix captures entry-time axes before local consumers; fixed
continuations use retained state. Nonempty frozen Findings publish atomically and
share full collection validation across first read, recovery and exact hit.
Private funnel Delta/Attribution registrations, dispatch, extractor consumers and
exclusive codecs are physically removed. Remaining Event/Lifecycle shared code
awaits its owning phase. Detailed validation and physical requirement statuses are
in the R7.4 evidence index; later phases, same-wheel and remote qualification remain separate.

### R7.5 canonical History publication

The bounded R9.3 SQLite native main-table int64/UTC-us route prepares occurrences
from one driver-owned temporary database backup before canonical replay. The same
captured prefix feeds unclassified History views and typed field reads. Source
coverage remains explicit: complete, prefix and unknown origin are distinct.
Fixed and cold consumers verify retained state without reopening the source or
the temporary backup. Other shapes and classified checkpoint axes are not
qualified by this route; complete capability-family acceptance remains separate.

`history.replay@v1` publishes `canonical_history` with one closed `HistoryPart@v1`
row for every complete Subject key. Primary classification/inception/known-through
and the state vector are reproduced from the typed retained trace. Initial exchange,
publication, public Artifact reads and cold recovery verify frozen model/input/order,
coverage, precision, schema/full keys, state/part versions and every projection.
Unknown-origin/follow-up evaluations retain occurrences without inventing initial
state or legal/violation assertions; a proved terminal prefix remains absorbing.
The receipt owner reads Arrow/Parquet only; it does not attach DuckDB, load current
Semantic or invoke replay. Missing, corrupt, swapped or wrong-bound parts fail before
any new Run. The existing Artifact/Evidence/zero-Findings/terminal transaction and
single execution deadline own publication and failure cleanup.

R7.5 introduced this replay registration. R7.6 adds in_state, distribution,
transitions, violations, intervals and dwell as described below. Old Lifecycle
Dataset construction, production and public recovery are closed; the remaining
exclusive private schema/reducer declarations and consumers are removed in R7.6.
The R7 migration ledger retains their historical dispositions and actual shared
Event/R8 owners.

### R7.6 History views and exact Duration publication

The sole method registry registers history.in_state/distribution/transitions/
violations/intervals/dwell and history.read@v1. Closed HistoryViewPart@v1 and its
singleton history_view retained record bind the original canonical History,
request, coverage, captured precision and optional complete checkpoint axes.
Initial exchange, publication, restoration, continuation and exact hit validate
that state and reproduce every scalar/primary projection. Selected transports
retain full original state with a declared incomplete row domain and exact Subject
mapping. Missing, foreign, noncanonical or wrong-version state fails before K.

Checkpoint axes are prepared by Ibis on the original complete member domain,
including Subjects without intervals. Each checkpoint uses its own snapshot or
validity version and complete nullable axis tuple. The source prefix completes
before local History consumption. A fixed History without matching axis proof
rejects new axes; a published distribution retains its checkpoint proof.

Intervals keep canonical ordinal, raw boundary occurrences, clipping and censoring.
Dwell retains the original typed trace, exact completed ticks and their order; it
finishes sum/count and Fraction quantiles with one HALF_EVEN round at microseconds.
Completed interval row.mean retains int64 sum/count and checked merging, including
empty_completed_set. No finished group mean or p90 is pooled.

Logical state/interval/violation selection transports the exact Subject image into
F13 prepared count/sum observation. Every source contribution is prepared first and
restricted locally by complete Subject keys in the same Run. Prepared observation
and Duration row statistics preserve the existing execute-before-continuing
boundary. Fixed selected members plus a new live Metric remain mixed and reject.
The shared 600-second execute deadline and Store 7 atomic publication remain sole
owners. Dedicated old Lifecycle Dataset reducer/compiler/codec consumers are
removed; actual shared Event/R8 consumers remain. This grants local table/Parquet
History behavior only, not R7.7-R7.9, same-wheel, remote or release acceptance.

## R7.7 Anchor execution and recovery

`anchor.bind@v1` and `anchor.observe@v1` use the current graph, registry, planner,
Runtime and Store 7 publication. Event elapsed observation uses public Ibis joins
and per-Anchor aggregates. Calendar and current Journey origins collect every
source candidate component in a proven finite envelope before local restriction;
local Anchors are never uploaded, and source access cannot follow local consumption.

Raw source candidates retain their declared Entity version contract without claiming
that a snapshot/validity member selection has already happened. Preparation resolves
that version at the captured occurrence instant and verifies complete single-valued
Subject correspondence. Internal relative Metric captures are construction templates;
they cannot execute as ordinary Metric nodes. R5 numeric state and finishing remain
the numeric owners for Anchor count/sum/ratio/linear observation.
Relative Metric source shapes retain the captured UTC instant authority when
combined with Event/Anchor preparation. The report timezone remains a rendering
and calendar-window authority; it does not create a second physical source shape.

The Anchor part owns the exact Subject/Event/occurrence identity, source definition,
start, captured order, Journey assignment where applicable, exact deadline and typed
component-use lists. The existing original-state part owns sufficient numeric state;
coverage is retained with the same coordinate binding. Exchange, publication and
recovery verify membership, complete use keys, own-occurrence exclusion, business
order, deadline and recomputed component state. Float comparisons use the existing
R5 magnitude/error owner; int64, Decimal and Duration state remain exact.

Fixed Journey bind requires the original population definition and consumes retained
assignment without rematch. A starts-only Anchor Artifact cannot introduce a new
live Metric input. Already observed NumericRelations continue through verified parts;
missing, corrupt or differently bound required inputs reject before a continuation
Run. The shared 600-second execute clock spans source preparation, local consumption,
checks and publication. A failed or cancelled invocation publishes no successful
Artifact, closes prepared resources and preserves prior verified Artifacts.

The R7.7 qualification record preserves the original P48 native target and every
mandatory ID. Tested local execution, fixed continuations and cold recovery do not
silently replace an unverified native or fixed-method target. Full R7, same-wheel,
retention and remote acceptance keep their later owners.

## R7.8 retained retention scope

`anchor.retention@v1` and `retention.by_subject@v1` use the existing graph,
Runtime and Store 7. The `retention` scope part is one canonical typed ledger,
independent of the selected primary row set. It contains the complete original
Anchor identities, Subject fibers, starts/deadlines, captured sequence values,
Journey assignments where applicable, qualifying return-use identities, exact
Event-bound coverage and the full three-valued partition. The frozen graph binds
the return capture domain, relative window and explicit Subject quantifier.

Primary Boolean Cells and their status state vector are verified against that
ledger. Recovery validates exact identities, own-occurrence exclusion, captured
order, window membership, coverage bindings, complete fibers and truth. Status
selection retains the full scope; it never changes the denominator or bounds.
Declaration and observed coverage intervals are retained under the exact return
capture binding and checked for continuous coverage of each instance window.
Gaps cannot establish false status. Predicates on another bound input remain
general selections and cannot certify the receiver's unknown status.
Subject quantification creates a separately identified full Subject-image Omega.
Empty Omega retains empty status sets and Undefined bounds.

Event elapsed witness selection runs in Ibis; calendar and Journey inputs follow
source preparation before local consumption. Every source read completes before
local retention, quantification or status transport starts. Fixed status views and
quantification consume verified retained parts without loading current semantics,
opening a datasource or rematching Journeys. Artifact recovery preserves the Boolean
status-view and selected-Boolean protocols even when a retention quantity is
retained; predicates such as `value.eq(True)` remain valid after resume.
A starts-only Anchor Artifact lacks
return inputs and rejects retention construction before a Run. Fixed retention
kernel qualification remains unfinished; continuation evidence cannot replace it.

## R8.2 connected deviation execution

Deviation uses the existing closed graph, MethodRegistry, execution-key v2
envelope and Store 7. Source consumers use ibis_python and fixed consumers use
artifact_python. Only deviation.zscore@v1 and deviation.mad@v1 receive the
certified_statistical precision contract; other Decimal consumers retain their
exact-precision admission rules. An explicit shared producer reads/fits once per
Run; separate graph nodes remain separate invocations.

Temporal source preparation records its exact captured unit and report zone.
A deviation consumer with a grid keeps that TimeShape and its original bound
grid. A consumer with no time-coordinate axis uses NoTime, while its upstream
observations retain their own qualified temporal keys and captured time scopes.
Fixed grid inputs restore their captured physical TimeShape. Non-grid fixed
relations use NoTime and preserve observation scopes in their retained parts,
so corresponding member categories remain composable. Source
observations admit s/ms/us/ns facts; comparison literals retain at least the
grid's microsecond precision so coarse source units cannot round scope bounds.

F11 source preparation resolves the original member envelope through actual
Subject parts and captures all later contributions, relationship/version facts
and full typed keys before local scoring or selection. Prepared grid sum
restricts contributions per captured cell, preserves empty cells and coverage,
and keeps original aggregation components. Decimal sum uses exact intermediate
state. Fixed missing follow-up observation contracts give a typed retained-part
repair before connecting a source or allocating a Run.

Publication verifies primary/part receipts and fitted authority, then commits
Artifact, Evidence, empty Findings digest and terminal Run together. Recovery
and fixed exact hits validate current receipts and required parts.
For a mixed fitted table, a versioned table_fits part binds each fitted column
to its exact current input signature, primary values and full original parts.
Its independent Store 7 receipt is required on recovery and fixed exact hits.
Both fit and ranking authorities are validated before the terminal table values
are accepted; malformed arithmetic facts receive a typed retained-part repair.
The common 600-second deadline, cancellation, cleanup and uncertain-commit coordination
remain the Runtime authority. See the
R8.2 evidence index (historical record in Git history)
for actual commands, qualified profiles and remaining fixed/F11 exits.

## R8.1 frozen statistical execution and disclosure

This is the sole inactive R8 execution/publication/recovery target. R8.1 adds no
method registration, producer implementation, new public API or qualification.
The R8 migration ledger (historical record in Git history)
records exact planned/blocked cells and migration responsibilities. Operator
r8.<role>/v1 schemas and timezone grid facts are prerequisites, not satisfied by
this document or by historical Dataset success.

### Exact keys, prepared inputs and sharing

Each method uses the existing MethodRegistry and the exact QualificationKey
fields (method/version, ordered input_types, ordered input_domains, shape,
route). SourceShape is (backend="duckdb", form="table"/"parquet",
table_kind="native"/"parquet", time=NoTime or TimeShape("instant", captured
source s/ms/us/ns, captured zone)). FixedShape contains only its captured time
shape; source backend/form are origin evidence, not extra fixed-key fields.
Complete calendar/grid authority remains a checked bound part in addition to
this physical time shape. Decimal precision/scale and ordered heterogeneous
correlation types participate in the key. Implementation IDs are frozen as
`r8-<method-name-with-dots-replaced-by-hyphens>-<route>-v1`, where method names
are the exact nine operator-owned names and route is ibis/ibis_python/
artifact_python. Only Pearson/Spearman have native ibis targets; the other
seven have ibis_python/artifact_python targets. These IDs are inactive targets,
not Qualified entries or evidence IDs. State/numeric policy versions and bound
RequiredParts/checks also enter execution/capture identity; they are not invented
QualificationKey fields. Store remains
7; graph-dag-v1 and continuation/execution-key v2 remain the envelopes. New
closed r8 part variants and explicit v1 method/state contracts extend them;
there is no generic statistical payload or legacy Dataset codec fallback.

Select qualified full-Ibis Pearson/Spearman first only if the exact route meets
all pairing, numeric, retained-input and output contracts. Otherwise select the
qualified ibis_python preparation/local route before execution. Deviation,
MAD/global ranking, runs, Kendall and forecast target complete projected Ibis
preparation then their registered local methods. Fixed methods use
artifact_python with checked Arrow/Parquet inputs. No compile/read/compute
failure changes route, sampler, batch scope, algorithm or problem. Record every
actual reader/submission/exchange and method invocation separately.

Input/output stage contracts carry complete typed keys, Cells, grid/Subject
mapping and method-required columns; cross-batch schemas/types are invariant.
Logical construction, Help and planning do no business reads, Session Store
writes or Run allocation. Known mixed closures, foreign Session/input identity,
unsupported shape and missing fixed parts reject before those effects. Data
uniqueness/coverage/finite checks execute at registered consume/publish stages.

Source-dependent top-level execute obtains a new realization; repeated edges to
one explicit node share its logical preparation/kernel owner in the same DAG/Run.
This does not require one physical source scan: pure-native Ibis subexpressions
may be queried independently, with each read/check recording its own evidence.
Registered local consumers share their actual captured preparation and kernel.
Distinct node identities do not merge merely because definitions match. Multiple
owned fields reference one fitted/statistical producer and do not recalculate it.
Fixed exact hits bind all ordered Artifact inputs, implementation/numeric/time
versions, parameters, current receipts, full RequiredParts/check results and
output scope, without another source read or Run.

### R9.6 direct native expression execution (2026-10-06)

The user permits a source check and a later native calculation to observe different
source versions. A successful precheck is evidence for that query, not a
same-input certificate for later facts. Required checks still execute at their
registered consume/publish deadlines; a failing check refuses execution. The
Runtime records actual submissions separately and neither retries nor changes
route merely because acquisitions differ.

A qualified pure-native plan keeps dependencies as closed Ibis expressions and
queries checks, required parts and the terminal primary directly. It does not
capture intermediate Arrow tables just to submit them again as literal or temporary
source relations. Independent check queries may still produce temporary Arrow
validation results; only the intermediate stage capture/restaging is removed.
Terminal primary and required-part data remain retained Artifact payloads.
The terminal Arrow exchange still validates actual complete
keys and Cells; primary/RequiredParts schemas, bindings and internal arithmetic
remain validated before Store 7 atomic publication and on recovery. Permitted
source changes do not excuse an internally inconsistent Artifact.

This is an internal execution strategy, not a new public route or callable.
Read-only credentials, unchanged Ibis submissions, one 600-second monotonic budget,
cancellation and resource release remain required. Hybrid/local plans still
prepare all downstream source inputs before local consumption or selection.
Fixed execution and cold recovery remain source-free and do not refit a retained
producer. This amendment does not grant new physical keys or all-backend qualification.

The shared exchange validates complete keys and Cell payloads from bounded Arrow
column slices, retaining a deadline checkpoint for every row and exact cross-batch
duplicate detection. Only a fully exhausted and successfully closed stream may
reuse its own primary key index during that collection. Independent RequiredParts
and method-state tables retain separate schema, key and arithmetic validation;
neither another source read nor a later invocation inherits that index.
Prepared observation expands selected rows once and reuses validated complete-key
and Subject restrictions for output. Its default window, grid index and anchor
position are initialized once at the original bounds-check point. Every selected
row, original-envelope check, grid membership and overlapping contribution use
remains checked; numeric state algorithms and precision policies are unchanged.

F11's mandatory chain is change->deviation->where->observed.members->observe->
summarize in one Logical DAG. Before any local score/Subject selection, collect
and prepare every later observation's contributions, captured member support,
relationship path, historical axes, time envelope and complete composite keys.
The prepared-observation identity binds the original Subject domain, Metric
and source definition fingerprints, path/time/quantity/component contracts and
shared source dependencies. Restriction uses the selected real Subject image
and then the same registered local aggregation. A population scalar without
per-Subject support cannot substitute. No upload, source-after-local, Candidate
membership or fixed/live mixture is allowed. TimeRun boundary reads may inform
an explicit new time_scope/execute, which creates a new realization with the old
Evidence association; reading a boundary itself creates no query.

Baseline prepared observation currently rejects grid/cumulative/fold and some
Decimal states. Mandatory expanded time/Decimal F11 cells remain blocked pending
R8.2 preparation/transport implementation. An allowlist change cannot qualify
this chain. Missing captured grid/pair/training/future parts similarly blocks a
new fixed kernel; existing where/rank/table transport is separately recorded.
R7 gaps and user-skipped qualifications remain unchanged.

### Deadline, cost observations and atomic failure

Reuse r7_execute_v1: one private 600-monotonic-second execute deadline, from
entry through admission, preparation, source/exchange, receipt/exact-hit checks,
all numeric computation/refinement and precommit publication validation. No new
public budget argument or stage/batch clock exists. Below/at/above boundary
requirements retain the existing strict expiry test elapsed>600. Native source
cancellation, local cancellation points and reader cleanup must enforce the same
remaining deadline. Cancellation/timeout/bad or late batch/compute/close errors
abort uncommitted publication and release owned resources. Durable committed
success remains success after a subsequent deadline; unknown commit uses the
existing Store reconciliation owner.

R8.6 connects this execution budget whenever the graph carries fit_inputs,
condition_cells, pair_inputs or training_inputs, including retained views and
fixed exact hits. The inherited budget resets durable-commit state per invocation.
Below/at/above-600, late-result, cancellation, source reader/close and transaction
checks for the nine methods are recorded in the
R8.6 evidence index (historical record in Git history).
This bounded implementation evidence does not close the entire R8 matrix.

Time-run recovery validates the captured classifications and the stored interval
witnesses without calling the segmentation consumer. Every original true cell
must occur exactly once in a maximal interval of one sequence. Checked witnesses
bind adjacent grid cells, input rows, complete typed schema, identity, endpoints,
count, elapsed Duration and both termination reasons. The original zero-Findings
policy must match the condition-capture digest.

The Association candidate ceiling is 4096 pair*lag*series, independently owned
by its method. Shared Finding cap remains 1000. Neither is an input row/byte or
workspace memory quota. Record actual input rows/columns/decoded bytes,
Arrow/pandas/numeric simultaneous buffers, preparation/kernel elapsed time,
output/part bytes and submit/cancel facts. No capacity estimate admits or rejects
otherwise valid complete input. Spearman/MAD use full global vectors/order,
forecast keeps full history, and runs carries unfinished batch state. A batched
source exchange is not evidence of a streaming numeric kernel. Resource failure
never silently truncates, samples or substitutes a different implementation.

### Closed Finding authority and transaction

| Producer | Extractor / policy | Eligibility and full deterministic order |
| --- | --- | --- |
| association.pearson/spearman/kendall@v1 | graph.association_findings@v1 / bounded_descriptive_findings@v1 | every valid candidate, including nonselected lags; abs(coefficient) descending, then complete typed candidate key |
| forecast.naive/drift/seasonal_naive@v1 | graph.forecast_findings@v1 / bounded_prediction_findings@v1 | every valid future point; complete typed series/future key order |
| deviation.zscore/mad@v1 and time.runs@v1 | graph.no_findings@v1 / zero_findings@v1 | eligible/emitted/truncated=0; validated empty set digest |

Nonzero policies have cap=1000, emitted=min(eligible,1000),
truncated=eligible-emitted, counted before cap. An eligible-zero input retains
its actual extractor/policy authority. All ordered quantity/input/scope bindings
and the exact policy's complete eligible set are captured before output selection.
Derived selection transports the producer's original capped scalar bodies,
policy and Evidence authority with its current selected-output binding. Finding
identities/set digest are rebound to the derived Artifact while retaining the
original producer, full eligibility scope and source-Artifact/capture linkage.
Counts, body order and original selected-lag flags remain unchanged. This is
transport, not a new extractor: it must not regenerate a selected-only set or
validate selected main rows as the producer's full domain. All reads check both
the original authority and derived Artifact binding.

Reuse the exact AssociationFindingValueV1 and ForecastPointFindingValueV1 scalar
body fields and lag variants. The Artifact authority additionally proves each
pair/series's complete count equations, scope, selected-lag version and each
forecast series/horizon's innovation/df/variance/assumption contract. Forecast
quality is validated per series, not only through a min/max summary. Numeric
facts carry the r8_numeric_v1 output type/error, not a float-only body assumption.
Deviation/Run Evidence reports statistical/condition facts and unavailable
reasons; it makes no automatic causal, significance or recommendation Finding.

The old AssociationFindingSubjectV1 and MetricFindingSubjectV1 are Metric-only.
R8 freezes AssociationFindingSubjectV2 with ordered a/b quantity bindings, and
ForecastFindingSubjectV2 with its quantity binding. The internal binding is the
closed union ObservedGraphQuantityV1, DerivedGraphQuantityV1,
RowStatisticGraphQuantityV1 and RolledGraphQuantityV1, matching current core
quantity kinds. Each contains exact quantity identity, definition fingerprint,
unit, exact value type and approximation identity. Observed additionally binds
its closed catalog Metric Ref or runtime Metric expression identity and its
graph/contribution fingerprint; Rolled binds its original quantity identity and
contribution identity. Derived binds method/version and ordered input quantity
identities; RowStatistic binds method/version, input quantity/domain and
weighting. None requires a catalog Metric when its real quantity is derived,
runtime-authored or a row statistic. No invented Metric Ref,
raw Subject key, universal dict or additional top-level export is permitted.
The subject discriminator changes to graph-association-v2/graph-forecast-v2;
value bodies remain their existing closed v1 variants. Old Dataset subjects and
read dispatch do not become a second recovery authority for new graph results.

Finding identity binds Session/Artifact, producer/state/extractor/policy versions,
ordered roles and capture/Artifact realization identities, definition and original
scope, public coordinates, complete canonical key and body digest. Artifact,
RequiredParts, Evidence, complete capped Findings and terminal Run publish in
one existing transaction. Public digest/page/single read, source-offline cold
read and fixed exact hit validate schemas/receipts/key sets, body/subject bindings,
full-set digest/order/count/truncation, producer policy and original/current
scope linkage. Corruption rejects; recovery never reruns the statistical method,
loads current Semantic/calendar, opens DuckDB or repairs an old Artifact.

### F14: bounded disclosure and repairs

Use the current native Help coordinator and ANALYSIS_HELP_RENDER_BUDGETS:
root 32 lines/3000 codepoints/8 routes/0 examples; decision_hub
44/4500/10/0; navigation 64/6500/18/0; exact_callable 104/9000/10/1;
public_type 72/7000/12/0; current_briefing 72/7000/6/1. These are the existing
owner's budgets, not new renderer constants. New callable leaves have one
English minimal public example and independently resolve beneath their concrete
dsl type; discovery stays progressive beneath analysis. No live target teaches
an unconnected method in R8.1.

Every Result has a bounded single-line repr with family and identity, pointing
to show(). show() uses the existing 8192-byte/50-row bounded card protocol with
deterministic typed order and explicit truncation/recovery action. Deviation
shows original/valid counts, fit/scale branch and unavailable scores; TimeRun
shows full classification counts, actual elapsed boundary/termination and Subject
availability; Association shows observation unit, pairing/search and selected
scope; Forecast shows model/history/future/df/variance/nominal assumptions.
contract() exposes only mechanically valid actions for the current parts/quantity.
Static API facts belong to Help, current K to contract, concrete repair to typed
errors; none injects an analysis plan or duplicates the capability matrix.

Error codes and fields come from the operators owner. Populate expected/received
from actual typed keys, required/captured parts, input and registered route facts;
repair names the appropriate original grid, corresponding owned view, complete
history or explicit new capture. No source repair of fixed state, implicit impute,
statistics-to-contribution interpretation or recommendation from unavailable
scores. Existing errors must not advertise a placeholder method.

Each R8 implementation package aligns its actual exports/API/help registry,
dynamic K/errors, independent drift/reachability/budget tests, CLI and latest
English/Chinese site examples before public connection. R8.5 closes remaining
retirement. Packaged skills remain untouched without explicit approval of a
concrete proposed diff; M17 records their workflow responsibility separately.

### R8.3 run capture and recovery

Complete-grid runs use the existing registered source-prefix/local execution and
Store 7 publication path. `r8.condition_cells/v1` retains condition inputs,
coverage, original domain and Subject facts; `r8.run_cells/v1` retains the full
classification scope, interval mapping and termination witnesses. Required parts
are receipt-bound. Fixed projections and cold recovery validate retained facts
without loading current Semantic state, calendars or sources. Selection only
changes output keys; the original scope remains available in `contract()`.


Business-covered full-grid sums retain one quantity-bound coverage part containing
both actual read completeness and the explicit business window union. Partial
Cells and original additive components are integrity support, never an alternate
public complete observation. Runtime publication and cold reads validate both
layers; unavailable business Cells cannot be coarsened through original rollup.
The declaration is frozen into the graph/quantity identity. Ordinary acquisitions
and independent remote reads retain their existing consistency contract.


### Native numeric continuation

Ordinary Metric mean/weighted mean/ratio/linear consumers use implementation
contract version 5. Captured primary values and independent component schemas
are authoritative on read. Source-free continuation uses local arithmetic over
saved components and projects to the captured output type; it does not reopen the
source or insert artifacts into DuckDB. Native rounding can differ across source
and continuation. This changes numerical computation, not Store 8's trusted-read
boundary or Unknown/Undefined/null semantics. No legacy algorithm migration is
introduced.
