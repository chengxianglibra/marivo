# Lazy Analysis Materialization Runtime and Authority Design

Date: 2026-09-01

Revised: 2026-09-04

Status: accepted

## Outcome

Define the runtime contract that turns one admitted lazy Dataset action into one
durable, auditable outcome without introducing a second execution or authority
path.

A reviewer can determine, without consulting operator-specific calculations or
private Ibis compiler internals:

- when an action becomes a persisted Run;
- which work is forbidden before incomplete Run admission;
- how Logical execution differs from Materialized inspection and collection;
- how datasource and semantic authority are captured and enforced;
- which storage receipts can back a materialized Dataset;
- when quality, Evidence, Findings, Artifact identity, and Run success become
  authoritative;
- how write-once execution bindings and concurrent calls behave;
- which state is recoverable after process loss;
- how action-scoped engine resources are cleaned up;
- why bounded Arrow, Run-staged Parquet, DuckDB workspaces, and kernel buffers
  remain private exchange resources rather than Materialized Datasets;
- how a committed Artifact becomes one immutable scan leaf after cold recovery;
- why no partial Artifact, Evidence authority, pandas value, or preview survives
  a failed action.

This document is the Module 4 authority named by
[`2026-09-01-lazy-analysis-design-decomposition-plan.md`](2026-09-01-lazy-analysis-design-decomposition-plan.md).
It consumes the Dataset value contract in
[`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md),
the observation and authority requirements in
[`2026-09-01-lazy-analysis-observation-model-design.md`](2026-09-01-lazy-analysis-observation-model-design.md),
and the execution handoff in
[`2026-09-01-lazy-analysis-planner-and-pushdown-design.md`](2026-09-01-lazy-analysis-planner-and-pushdown-design.md).

The Observation Model and compiler designs are accepted. This design consumes
their frozen filter, bounded-local, invocation, sink-feasibility, and
hidden-durable-stage contracts without redefining them.

## Ownership Boundary

This document owns:

- synchronous execution of `LogicalDataset.execute()` and immutable backing
  reads by `MaterializedDataset.show()` and `to_pandas()`;
- the exact point at which an execution-binding miss admits an incomplete Run;
- execution Run kinds and optional bounded read-audit receipts;
- runtime resolution and validation of semantic, datasource, materialized, and
  storage authority;
- datasource snapshot capture and the source-authority vocabulary;
- deterministic storage-sink selection from compiler-admitted choices;
- local, engine, and object Dataset storage receipts;
- Dataset Artifact metadata, identity, commit markers, and the write-once
  Session execution binding;
- execution claims and concurrent same-key coordination;
- execution of compiler-declared bounded Arrow and Run-staged Parquet exchanges;
- journaling and cleanup of DuckDB workspaces, Python-kernel buffers, and
  private exchange files;
- staging, validation, quality, Evidence, Finding, Artifact, and Run commit
  ordering;
- process-owner leases and cold reconciliation of incomplete Runs;
- cleanup of compiler-declared action-scoped temporary resources and unpublished
  storage;
- immutable scan-leaf decoding and storage-admission validation;
- runtime meaning and enforcement of `semantic_current`,
  `materialized_only`, and `semantic_or_materialized`;
- explicit Artifact revalidation across independent integrity, semantic,
  datasource, and storage axes;
- multi-downstream reuse through an explicit materialized Dataset.

It does not own:

- public Dataset family, shape, schema, row meaning, actions, or state fields;
- Population inference, predicate semantics, filter effects, or aggregate
  coordinate algebra;
- private semantic nodes, bound Ibis expressions, lowerer manifests, boundary
  capabilities, or stage formation;
- operator-specific statistical calculations, quality rules, Evidence
  extractors, or Finding value schemas;
- public multi-sink scheduling, background tasks, cancellation handles, storage
  parameters, or retention parameters;
- SubjectSet identity, Event matching, or Lifecycle replay semantics;
- a migration or compatibility path from eager Frame Artifacts and their
  persistence generation.

Dataset and operator owners decide what an action means. The compiler decides
how that exact meaning can execute. This module decides whether the resulting
execution becomes no public result, one terminal value, or one committed
recoverable Dataset Artifact.

## Upstream Invariants

The runtime accepts these Dataset Core decisions as fixed:

1. Dataset construction does not query a datasource, create a Run, or publish
   reusable authority.
2. Every Dataset belongs to one exact Session.
3. A logical Dataset has no durable public locator, but its exact definition
   can resolve a private same-Session execution binding.
4. Every analytical family has paired Logical and Materialized Dataset classes
   with the same family id, row contract, public schema, shape, and definition
   fingerprint.
5. `execute()` returns the paired immutable Materialized Dataset and does not
   mutate the Logical input.
6. Only Materialized Datasets expose `show()` and `to_pandas()`; both read the
   committed backing and never replay origin SQL. Only Logical Datasets expose
   `execute()`.
7. Every registered downstream operator exists on both states and returns a
   new Logical Dataset; the Materialized receiver becomes a scan leaf in that
   lazy DAG.
8. An `execute()` binding miss creates one synchronous Run. A binding hit and
   materialized reads create no analysis Run.
9. A materialized Dataset is backed only by a committed immutable scan leaf.
10. Materialized reads do not require the current catalog to retain the original
   semantic definitions.
11. `contract()` never performs runtime revalidation.
12. Exact realized row count is mandatory for every materialized Dataset.
13. A public Dataset is never executing, failed, partially materialized, or
    stale; those are runtime or revalidation facts.

The runtime consumes these compiler decisions:

1. pure graph closure and exact-schema validation may happen before Run
   admission;
2. live profile resolution, compilation, datasource statements, transfers, and
   execution happen only after incomplete Run admission;
3. one action receives one immutable `PhysicalStageGraphV1` or one bounded
   compilation failure;
4. the graph names exact output schema, key, validation, transfer, snapshot,
   placement, and temporary-resource requirements;
5. local execution is a registered hard-bounded placement, not fallback;
6. action-scoped temporary relations are private, non-durable, and never
   Artifacts;
7. no private stage may create a hidden durable intermediate Dataset;
8. materialized inputs are non-rewriteable scan leaves;
9. compilation and execution failures retain distinct phases;
10. safe compiler audit is a bounded projection, never a persisted expression;
11. bound Ibis expressions and private lowerer records remain compiler-owned;
    runtime persists only safe compiler/lowerer, Ibis/backend, boundary-profile,
    stage, and applied-lowerer fingerprints or bounded counts.

If the compiler rejects bounded-local stages or permits hidden durable stages,
that module must change first. This runtime does not define both alternatives.

## Decision Summary

### Keep one Run lifecycle for every producing execution

Every producer execution uses the same persisted lifecycle:

```text
absent -> incomplete -> succeeded | failed
```

The lifecycle never moves backward. A failed producer retry is a new Run.
Recovering an existing Artifact through an execution binding is a read and
creates no Run.

The producing action kind is closed:

```text
execute    logical_dataset.execute()
```

An incomplete Run exists before live authority resolution, backend compilation,
datasource data work, transfer, temporary-resource creation, or storage writes.
Only deterministic in-process validation that cannot consult live external
state may precede admission.

`MaterializedDataset.show()` and `to_pandas()` may emit bounded operational
read audit, but that audit is not an analysis Run, creates no graph node, and
does not own or duplicate rows.

### Separate execution, storage, and publication decisions

One producing `execute()` call crosses three private boundaries:

```text
execution complete
    -> immutable storage finalized
    -> Artifact publication committed
```

Execution completion proves only that result and validation streams finished.
Storage finalization proves only that an immutable backing and exact receipt
exist. Artifact publication proves that storage, quality, Evidence, Findings,
metadata, and Session graph identity agree.

None of the first two boundaries creates a public Dataset. Only the third may
produce `MaterializedDatasetState`.

### Use one commit marker to bridge independent durable stores

The Session Store is canonical for Run lifecycle and graph relationships. The
Evidence Store owns Findings, the commit-time Evidence envelope, and one narrow
Artifact commit marker.

The commit marker is the publication decision when filesystem or external
storage, the Evidence Store, and the Session Store cannot share one database
transaction. It is not a second Artifact browse index.

Publication order is:

```text
final immutable storage
    -> exact Artifact metadata
    -> Evidence + Findings + Artifact commit marker
    -> Session Store Artifact row + Run success
```

Before the marker, all output is unpublished and may be cleaned up. After the
marker, the Artifact is commit-decided and must be completed by recovery; it
must never be converted to a failed Run or deleted as staging.

### Make every committed Dataset Artifact Evidence-complete

Every materialized Dataset publishes one commit-time Evidence envelope. An
operator with no domain Findings publishes a valid zero-Finding envelope; it
does not use `unavailable` as a substitute for a failed extractor.

Quality, Evidence, or Finding construction failure fails the execute action
before the commit marker. The first lazy cutover therefore has no committed
Dataset Artifact with partial Evidence authority.

This is stricter than preserving a successful Artifact with a partial or
unavailable Evidence state. A later relaxed policy requires an explicit design
amendment and a new Evidence-envelope variant.

### Bind each exact logical execution once per Session

A logical definition fingerprint alone is not an Artifact identity, but the
runtime can derive one exact Session-local `DatasetExecutionKeyV1` before live
datasource work. The key binds the normalized logical definition, semantic
dependencies, captured parameterized-source binding digest, ordered
logical/materialized input-authority tokens, family and row contracts, and
implementation, quality, and Evidence contract versions.
It deliberately excludes current datasource row state, realized sample rows,
storage placement, and process or script identity.

The Session Store maintains one write-once
`DatasetExecutionKeyV1 -> artifact_ref` binding. A binding miss elects one
producer, admits one execution Run, and publishes one Artifact before filling
the binding. A binding hit validates and recovers that exact Artifact without a
new Run, datasource access, quality extraction, storage copy, or Evidence
publication.

This is Session snapshot semantics, not a global cache-equivalence claim. Once
bound, later datasource changes do not redirect the key. A caller that requires
fresh rows creates a new named Session or changes an explicit definition input
that participates in the key. Non-replayable sources and action-scoped samples
therefore remain reusable through their same-Session binding even though their
Artifacts cannot prove equivalence to an independently executed definition in
another Session.

### Coordinate concurrent exact materializations without duplicate authority

Concurrent `execute()` calls with the same exact execution key do not race to
publish two canonical Artifacts. A private durable materialization claim elects
one producer before Run admission. Other synchronous callers wait under a fixed
internal deadline for the producer to commit the write-once binding, then
validate and recover its Artifact without admitting contender Runs.

The accepted first-cutover coordination policy is:

```text
MaterializationClaimPolicyV1
  schema = "marivo.materialization_claim_policy/v1"
  contender_wait_timeout_seconds = 30
```

When the deadline expires while the producer still owns its process-lifetime
lock, the contender Run fails with one retryable concurrency error. The
producer continues; the runtime does not cancel it or start speculative work.

If the producer fails before commit, one waiter may atomically acquire a new
claim and execute. If the producer loses its process ownership, cold recovery
first reconciles its publication or proves its backend execution terminal before
marking it failed. If terminal state cannot be proved, the existing claim stays
bound and no new producer is admitted.

The claim is coordination state, not Artifact identity, execution reuse, a
public task, or a cross-Session cache.

### Treat revalidation as a read across independent axes

`session.revalidate(artifact_or_ref)` is explicit, read-only, and does not
change Dataset state. It projects independent current observations:

```text
artifact_integrity
storage_authority
evidence_integrity
semantic_authority
datasource_authority
```

Datasource authority may be `current`, `changed`, `unverifiable`, or `unknown`
according to the receipt and datasource profile. A changed source does not make
an immutable Artifact unreadable and does not mutate it into a stale Dataset.

Ordinary materialized `show()` and `to_pandas()` validate Artifact, Evidence,
storage, and authorization integrity. Reconstructed logical `execute()` does
the same validation on a binding hit. None compares the committed semantic or
datasource snapshot to current live sources.

## Runtime Schema Generation

The lazy Dataset cutover introduces one clean persistence generation. It does
not decode eager Frame Artifacts as Datasets and does not migrate old Runs.

The exact top-level schemas are:

```text
marivo.analysis_action_run/v2
marivo.dataset_artifact/v1
marivo.dataset_storage_receipt/v1
marivo.dataset_evidence/v1
marivo.dataset_artifact_commit/v1
marivo.dataset_execution_binding/v1
marivo.action_resource_journal/v1
```

The replacement Session Store uses exact `PRAGMA user_version = 3`. A fresh v3
Store creates the action Run, Artifact, execution-binding, claim, owner-lease,
and resource-journal relations and their stated uniqueness and foreign-key
invariants atomically.
An existing v2 Store is not upgraded in place. A Store or record from an older
or future generation fails closed before Session activation with guidance to
create a new named Session and rerun authoring code.

### Generation-scoped layout

The accepted clean cutover uses one generation-scoped root:

```text
.marivo/analysis/generations/v3/session_store.db
.marivo/analysis/generations/v3/sessions/<session_ref>/evidence_store.db
.marivo/analysis/generations/v3/sessions/<session_ref>/artifacts/<artifact_ref>/...
.marivo/analysis/generations/v3/sessions/<session_ref>/runs/<run_ref>/...
```

`session_store.db` is exact `PRAGMA user_version = 3`.
`evidence_store.db` is a new independent Evidence generation with exact
`PRAGMA user_version = 1`. Run directories contain only private staging and
journaled recovery resources. Artifact directories contain only committed
metadata and an admitted local immutable backing; engine/object receipts keep
their own immutable locators in metadata.

The lazy runtime never opens `.marivo/analysis/session_store.db` as v3
authority. `get_or_create(name)` operates only in the generation-scoped v3
Store and may create a new Session even when an eager v2 Store exists.
`resume(...)`, `current()`, and Artifact reads resolve only v3 identities. If a
caller supplies an old Store, Run, or Artifact identity, activation fails with
one structured legacy-generation error directing the caller to create a new
named Session. It does not inspect old application rows, copy a Session name,
or decode old payloads.

### Exact Store relations

The v3 Session Store owns these relations and no generic key/value or JSON
compatibility table:

| Relation | Primary and unique keys | Required foreign keys and purpose |
| --- | --- | --- |
| `sessions` | primary `session_ref`; unique `name` | Owns question, report timezone name/resolution/warning, created time, and updated time. |
| `runtime_state` | singleton primary key constrained to `1` | Nullable `current_session_ref -> sessions.session_ref ON DELETE SET NULL`; owns only the current-Session pointer and update time. |
| `analysis_action_runs` | primary `run_ref`; unique `(session_ref, run_ref)`; no uniqueness by execution key | `session_ref -> sessions`; nullable composite `(session_ref, output_artifact_ref) -> dataset_artifacts`; owns the exact versioned lifecycle envelope and terminal phase facts. Failed retries may share an execution key; the claim and successful binding, not Run history, enforce one active producer and one committed result. |
| `analysis_action_run_inputs` | primary `(run_ref, input_ordinal)`; unique `(run_ref, artifact_ref)` | Composite `(session_ref, run_ref) -> analysis_action_runs ON DELETE CASCADE`; composite `(session_ref, artifact_ref) -> dataset_artifacts`; owns ordered same-Session Materialized Dataset inputs only. |
| `dataset_artifacts` | primary `artifact_ref`; unique `(session_ref, artifact_ref)`; unique `(session_ref, producing_run_ref)`; unique `(session_ref, execution_key)` | `session_ref -> sessions`; composite `(session_ref, producing_run_ref) -> analysis_action_runs`; owns exact metadata locator/digest, receipt/content/Evidence digests, row/byte authority, and commit time. |
| `dataset_execution_bindings` | primary `(session_ref, execution_key)`; unique `(session_ref, artifact_ref)`; unique `(session_ref, producer_run_ref)` | Composite Artifact and producer-Run foreign keys force all three identities into one Session; immutable binding from one execution key to one committed Artifact. |
| `materialization_claims` | primary `(session_ref, execution_key)`; unique `(session_ref, producer_run_ref)` | `session_ref -> sessions`; composite `(session_ref, producer_run_ref) -> analysis_action_runs`; owns producer nonce and claim timestamps until terminal publication/failure. |
| `run_owner_leases` | primary `run_ref` | `run_ref -> analysis_action_runs ON DELETE CASCADE`; owns one required process-lock nonce plus advisory heartbeat/expiry facts. |
| `action_resource_journal` | primary `(run_ref, resource_ordinal)`; unique `(run_ref, resource_kind, resource_locator_digest)` | `run_ref -> analysis_action_runs ON DELETE CASCADE`; owns secret-safe reserved/created/terminal/cleaned resource state. |

Every foreign key includes `session_ref` in either the key or a composite
agreement check so no cross-Session input, claim, binding, Run output, or
Artifact can be represented. Raw parameterized-source values are forbidden in
every relation; only their opaque execution-identity digest is stored.

The v3 Store creates exact indexes for Session recency, Run recency and
lifecycle within Session, Run lookup by input Artifact, Artifact commit
recency within Session, execution binding by Artifact, claims by producer Run,
live owner leases by expiry, and resource journal entries by cleanup state.
Primary/unique indexes are reused rather than duplicated.

The per-Session Evidence Store owns exactly:

| Relation | Primary and unique keys | Required foreign keys and purpose |
| --- | --- | --- |
| `dataset_evidence` | primary `artifact_ref`; unique `evidence_digest` | Owns one `marivo.dataset_evidence/v1` envelope and its canonical digest. |
| `dataset_artifact_commits` | primary `artifact_ref`; unique `producing_run_ref`; unique `marker_digest` | `artifact_ref -> dataset_evidence.artifact_ref`; owns the immutable `marivo.dataset_artifact_commit/v1` publication decision. |
| `findings` | primary `finding_ref`; unique `(artifact_ref, finding_ordinal)`; unique `(artifact_ref, finding_identity_digest)` | `artifact_ref -> dataset_evidence.artifact_ref ON DELETE CASCADE`; owns one ordered immutable typed Finding payload. |

The Evidence Store indexes Findings by `(artifact_ref, finding_ordinal)` and
commit markers by `committed_at`; it contains no mutable Artifact upsert row,
Run lifecycle, execution binding, receipt, claim, or compatibility payload.

Transaction ownership is exact:

1. Session creation/update and the singleton current pointer are one Session
   Store transaction.
2. Producer election, incomplete Run admission, ordered input rows, initial
   owner lease, and claim creation are one Session Store transaction; a losing
   uniqueness race rolls back all five and admits no Run.
3. Resource-journal state changes are short Session Store transactions owned
   by the active Run and committed before the external state they authorize.
4. Findings, Evidence envelope, and the commit marker are one Evidence Store
   transaction with the marker inserted last.
5. After that marker, Artifact row insertion, immutable execution binding, Run
   success, claim release, and lease terminalization are one Session Store
   transaction. Recovery may replay only this exact transaction after proving
   the marker-before-Store gap.
6. Pre-marker failure terminalizes the Run, releases its claim/lease, and
   records cleanup status in one Session Store transaction after external
   resources are proved terminal or explicitly recovery-pending.

There is no cross-database SQLite transaction. The Evidence marker remains the
publication decision, and the ordered two-transaction protocol above is the
only accepted cross-Store atomicity model.

There is no:

- old-schema decoder;
- Frame-to-Dataset adapter;
- Run backfill;
- Artifact import;
- Evidence migration;
- dual-read graph;
- fallback to `analysis-artifact/v13`, `marivo.analysis_run/v2`, or the
  `marivo.analysis_job/v2` Job-to-Run adapter;
- alias that lets one persisted value enter both eager and lazy execution.

The Public Cutover Plan owns the exact deletion and rollout sequence. This
module owns the replacement schemas and fail-closed boundary.

## Action Run Contract

### Common envelope

Every persisted Run has one immutable common admission envelope:

```text
AnalysisActionRunEnvelopeV2
  schema = "marivo.analysis_action_run/v2"
  run_ref
  session_ref
  action_kind
  admitted_at
  dataset_input: RunDatasetInputV1
  input_artifact_refs[]
  safe_arguments[]
  omitted_argument_names[]
  owner_lease_ref
```

`RunDatasetInputV1` contains only:

```text
definition_fingerprint
family_id
shape_id
row_contract_fingerprint
bounded_operator_ids[]
bounded_semantic_dependency_refs[]
materialized_input_refs[]
authority_requirement_summary
```

It cannot reconstruct a logical Dataset. Raw predicates, SQL, credentials,
identity values, Ibis expressions, backend objects, Python callables, and
unbounded plan payloads are forbidden.

The existing bounded argument projection principles remain:

- secret-like names and values are omitted or redacted;
- URL credentials, bearer tokens, SQL/query strings, backend objects, and raw
  identities never persist;
- depth, collection length, string length, and total payload size are bounded;
- omission is explicit through stable argument names, not silent truncation.

### Discriminated lifecycle records

The persisted payload is one exact variant:

```text
IncompleteActionRunV2
  envelope
  lifecycle = incomplete

SucceededExecuteRunV2
  envelope
  lifecycle = succeeded
  finished_at
  authority_audit
  planning_audit
  output_artifact_ref
  output_mode = produced
  materialization_receipt
  cleanup_summary

FailedActionRunV2
  envelope
  lifecycle = failed
  failed_at
  authority_audit
  planning_audit
  failure: RunFailureV2
  cleanup_summary
```

Terminal-only fields cannot appear on an incomplete Run. Output Artifact fields
cannot appear on failed Runs. An execute success must name exactly one
same-Session committed Artifact. Binding recovery has no succeeded-Run variant
because it is not a new execution attempt.

### Materialized read audit and execution receipts

If enabled, operational read audit for `show()` contains only bounded access
facts:

```text
InspectionExecutionReceiptV1
  presented_row_count
  preview_limit
  has_more
  presentation_order_fingerprint
  realized_schema_fingerprint
  artifact_ref
```

It contains no preview rows, source query, or new authority and is not an
analysis Run.

Operational read audit for `to_pandas()` may contain:

```text
CollectionExecutionReceiptV1
  collected_row_count
  collected_byte_count: ExactBytesV1 | UnavailableBytesV1
  presentation_order_fingerprint
  realized_schema_fingerprint
  artifact_ref
```

It contains no pandas bytes or replay handle and is not an analysis Run. The
method returns only after the complete isolated pandas value exists; on failure
no partial DataFrame is returned.

Materialization persists:

```text
MaterializationExecutionReceiptV1
  execution_key_digest
  storage_receipt_digest
  content_authority_digest
  realized_row_count
  realized_byte_count: ExactBytesV1 | UnavailableBytesV1
  evidence_digest
  finding_count
```

The Artifact owns the full committed facts; the Run receipt is a bounded audit
projection.

### Failure contract

`RunFailureV2` contains:

```text
phase
kind
safe_message
safe_location
expected
received
repair
backend_class
retry_disposition
commit_decision = not_committed
```

The phase vocabulary preserves compiler distinctions and adds runtime phases:

```text
authority_resolution
semantic_validation
source_snapshot
graph_validation
compiler_manifest
execution_boundary
source_binding
ibis_expression_construction
storage_selection
physical_formation
ibis_backend_compile
stage_execution
transfer_guard
output_validation
storage_staging
storage_finalization
quality
evidence
publication
cleanup
presentation
process_lost
```

Backend exception class may be retained only when safe. Error messages, SQL,
query text, credentials, raw identities, and unbounded nested causes are never
persisted.

A Run with a committed Artifact marker is never represented by
`FailedActionRunV2`. If Session Store finalization is temporarily unavailable
after the marker, the Run remains incomplete and the caller receives a typed
commit-pending error until recovery completes it.

## Admission and Execution Lifecycle

### Work allowed before Run admission

Only these deterministic local checks may occur before admission:

1. Dataset object and Session ownership validation;
2. root/state pairing validation;
3. private graph closure;
4. exact contract-version and schema-shape validation;
5. zero-argument `execute()` signature and local bound-shape validation;
6. derivation of `DatasetExecutionKeyV1`;
7. lookup and integrity validation of an existing execution binding;
8. acquisition or observation of the same-key producer claim;
9. construction of the bounded safe Run input projection by the elected
   producer.

These checks must not:

- open a datasource or storage connection;
- resolve live credentials;
- refresh an execution-boundary profile;
- compile against an engine;
- query metadata or data;
- validate current relation existence;
- create a temporary or staging resource.

A binding hit returns the paired Materialized Dataset here and creates no Run.
A claim waiter also admits no Run; it waits for the producer to publish the
binding or fail. Only the claim winner crosses into execution admission. A
failure before that point creates no Run because no execution attempt has begun.

### Work after incomplete Run admission

After the incomplete Run and owner lease are durably recorded, the runtime:

1. resolves current semantic requirements;
2. resolves credentials without persisting secret values;
3. captures datasource execution profiles and source authority;
4. validates all referenced materialized leaves;
5. asks the compiler to canonicalize, bind, lower to Ibis, and compile;
6. executes the stage graph while journaling resource handles;
7. validates transfer guards, output schema, key, row count, and action-time
   requirements;
8. completes action-specific cleanup and publication;
9. writes exactly one terminal Run transition.

Every failure after admission attempts cleanup and terminal failure unless an
Artifact commit marker already exists. A process crash is reconciled by the
owner-lease protocol rather than guessed from an in-memory exception path.

### Materialized read lifecycle

`MaterializedDataset.show()` validates the Artifact, Evidence marker, storage
receipt, authorization, realized schema, and deterministic presentation order,
then reads only a bounded preview from the immutable backing. The limit protects
agent context; it is not an alternate execution plan and cannot reach the
origin datasource.

`MaterializedDataset.to_pandas()` performs the same validation and then reads
the complete immutable backing under the guarded transfer contract. It returns
one isolated DataFrame or fails without a partial return. The DataFrame has no
Artifact ref and cannot re-enter typed analysis; the Materialized Dataset can.

### Execution lifecycle

An execution-binding miss follows:

```text
derive DatasetExecutionKeyV1
  -> miss the write-once Session binding
  -> acquire the producer claim
  -> admit incomplete Run
  -> resolve semantic and source authority
  -> validate materialized inputs
  -> plan and compile
  -> execute into private staged output
  -> validate schema, key, counts, bounds, and action requirements
  -> compute quality, Evidence, and Findings
  -> finalize immutable storage and exact receipt
  -> clean all non-output resources
  -> write exact Artifact metadata
  -> commit Evidence, Findings, and Artifact commit marker atomically
  -> register Artifact, fill the write-once execution binding, and succeed Run
     atomically in Session Store
  -> construct one MaterializedScanLeafHandle
  -> return the paired Materialized Dataset with MaterializedDatasetState
```

The logical input Dataset remains unchanged. No step reads its origin graph
after the materialized leaf is constructed.

## Source and Datasource Authority

### Closed source-authority variants

Every live source stage binds one exact authority variant:

```text
VersionedSourceSnapshotV1
  datasource_ref
  source_ref
  version_token
  consistency_scope
  captured_at

TransactionSourceSnapshotV1
  datasource_ref
  transaction_snapshot_token
  source_refs[]
  consistency_scope
  captured_at

ImmutableSourceObjectV1
  datasource_ref
  object_ref
  object_version_or_hash
  captured_at

NonReplayableExecutionAuthorityV1
  datasource_ref
  source_refs[]
  action_nonce
  observed_window
  reason
```

The first three are replay-comparable. The last is exact for audit but cannot
prove equality with an independent execution, so it forbids cross-Session
equivalence matching. It does not disable recovery through an already committed
same-Session execution binding.

`consistency_scope` distinguishes one-source, one-datasource transaction, and
registered federated snapshot guarantees. Separate source tokens do not become
an atomic cross-source snapshot merely because their capture timestamps are
close.

Raw credentials, source query text, and backend connection handles never enter
source authority.

### Capture rules

Source authority is captured after incomplete Run admission and before the
first data statement that depends on it. A backend may open a read transaction
and expose its token as part of this step. If the backend cannot expose a stable
token, the runtime uses `NonReplayableExecutionAuthorityV1`; it does not invent
one from wall-clock time or SQL text.

A replay-comparable variant is admitted only when every dependent data statement
is bound to the exact captured version or pinned transaction. Reading mutable
current rows after separately observing a version token is not a snapshot. If
the adapter cannot address that version, hold the matching snapshot transaction,
or prove that the source cannot change through statement completion, the runtime
uses `NonReplayableExecutionAuthorityV1`.

The physical plan binds required source-authority classes per stage. Runtime
may reject a backend whose available snapshot semantics are weaker than the
operator or federation contract.

### Parameterized source values are definition-bound

Parameterized non-secret JSON source values are not live source authority and
are not action-time Session state. The Observation owner validates and captures
them into the immutable logical source definition under
`BoundSourceParametersV1`. Before Run admission, this runtime consumes the
definition's exact canonical value digest while the compiler receives the
process-local typed values needed to bind the source adapter.

The runtime never asks `Session.source_bindings(...)`, a `ContextVar`, or the
process environment for a replacement value during `execute()`. Missing
captured values are a corrupt/incomplete logical definition and fail before
Run admission. A caller cannot override them through `execute(...)`.

The exact captured value digest participates in both the logical definition
fingerprint and `DatasetExecutionKeyV1`. Therefore:

- equal reconstructed definitions with equal bindings may recover one existing
  same-Session Artifact before datasource work;
- changing one bound value creates a different execution key;
- a binding hit never needs the raw value to resend a request;
- a binding miss uses only the values captured by that exact definition.

Raw binding values are process-local execution inputs. They do not appear in a
Run envelope, Artifact metadata, Evidence, Finding, graph edge, card, contract,
error, telemetry record, source-authority record, or persisted Dataset
definition. User-visible and audit surfaces may contain only exact
Entity/parameter identities and a bounded redacted projection. The internal
definition and execution-binding records may contain the opaque digest but
never the values. Credentials remain in the datasource credential contract and
are never accepted as source bindings.

### Semantic dependency authority

Logical current-source execution binds one canonical semantic dependency
digest over the exact Entity, Metric, Dimension, Relationship, Event,
Lifecycle, policy, and implementation contracts reachable by the admitted
Dataset graph.

Display labels, Help text, source locations, and non-semantic documentation do
not change this digest. Any field that can change rows, null behavior,
Population membership, coordinates, aggregation, or authority does.

Materialized-only execution reads the committed digest for audit but does not
require the current catalog to reproduce it.

## Authority Modes

### Runtime requirement record

Every operator occurrence carries one selected requirement:

```text
AuthorityRequirementV1
  occurrence_path
  operator_contract_id
  mode = semantic_current | materialized_only | semantic_or_materialized
  selected_branch
  semantic_dependency_requirements[]
  materialized_field_requirements[]
  compatibility_contract_id
```

`selected_branch` is fixed before execution. `semantic_or_materialized` does not
mean try semantic execution and fall back after an error.

### `semantic_current`

The runtime resolves the exact current semantic dependencies and supplies their
digest to compilation and Artifact identity. Missing, changed, or incompatible
current authority fails before the affected data statement.

When a semantic-current operator consumes a materialized leaf beside new
current semantic input, the runtime validates the owner-defined compatibility
contract. It never reopens the leaf's origin graph or silently reconstructs a
missing axis, filter, Population, or Metric definition.

### `materialized_only`

The runtime validates only:

- same-Session ownership;
- committed Artifact and Evidence marker integrity;
- exact row-contract and realized-schema agreement;
- immutable storage content authority;
- current storage readability and authorization;
- retained fields and sufficient statistics named by the operator contract.

It does not consult current semantic state to reinterpret values. Catalog
removal or drift does not block the action.

### `semantic_or_materialized`

The owner supplies two proven-equivalent admission branches. Dataset
construction selects the branch from exact input state and binds it into the
definition fingerprint. Runtime enforces that branch only.

If its authority validation fails, the action fails with the repair declared by
the owner. It does not switch branches inside the same Run.

### Authority audit

Every terminal Run may persist one bounded `AuthorityAuditV1`:

```text
semantic_dependency_digest
source_authority_digest
source_authority_classes[]
materialized_input_refs[]
authority_mode_counts
compatibility_contract_ids[]
storage_authorization_classes[]
```

It contains digests and safe classes, not raw source tokens when those tokens
are secret-like or operationally sensitive.

## Private Exchange Staging

### Exchange authority is not Dataset authority

When a physical graph has a datasource stage followed by a bounded local stage,
the runtime moves rows under the compiler-owned `PhysicalExchangeV1` contract.
That contract's exact Arrow-compatible schema and authority projection are the
only authority for the intermediate rows.

An exchange is never registered as a Dataset, Artifact, execution binding,
storage receipt, Evidence source, or Session graph node. It may contain private
accumulators, ordering keys, or sufficient statistics that do not satisfy any
public Dataset row contract. Only the complete primary output proceeds to
the selected durable sink and publication; private exchanges are never sink
candidates.

### Bounded Arrow streaming

`bounded_arrow_stream` is process-private and one-pass. The runtime validates
every RecordBatch against the exact field order, logical types, nullability,
timezone, decimal, dictionary, and nested-value contract before forwarding it.
It increments row and decoded Arrow-buffer byte counters before consumer
admission and stops the producer as soon as either guard is violated.

A registered Python kernel may assemble accepted batches into one Arrow Table
only after the aggregate local-input guards remain satisfied. Any pandas or
numerical object derived from that Table is kernel-private and is discarded
before stage completion.

### Run-staged Parquet

`run_staged_parquet` exists only for rewindable local input or an admitted
engine-managed Parquet export. The runtime reserves a Run-scoped exchange
directory and ownership nonce before creation, journals its exact locator, and
accepts only the first-cutover Parquet writer/reader contract. Its manifest
binds relative files, sizes, and hashes for cleanup and validation but is not a
`DatasetStorageReceiptV1`.

Both total file bytes and canonical decoded Arrow bytes must satisfy the
exchange byte guard. The runtime validates Parquet schema before the local
consumer opens it. Exchange files are cleaned before Artifact publication and
on every failed, cancelled, or cold-recovered no-marker path. They cannot be
recovered as analysis output or reused by another action.

### Local executor resources

A DuckDB local stage receives only validated Arrow streams or Run-staged
Parquet relations. Any DuckDB database, temporary directory, or spill file is a
Run-scoped journaled workspace and never an engine storage receipt. A Python
kernel receives only the exact Arrow inputs named by its bound implementation
registration. The runtime invokes neither an unregistered callable nor a
generic pandas, Polars, or DuckDB fallback after another stage fails.

## Storage Selection

### Runtime-owned deterministic policy

The compiler declares admissible output domains and transfer properties. The
runtime chooses one storage receipt deterministically from configured and
authorized durable sinks.

The first-cutover order is:

1. project-local storage for a result proven within
   `LocalMaterializationPolicyV1`;
2. an immutable engine-managed relation in an execution domain already holding
   the final rows;
3. an immutable object Dataset written without an unbounded local transfer;
4. failure when none can provide exact recovery and row-count authority.

This order is a storage policy, not an Ibis or datasource query-plan decision.
`execute()` accepts no `storage`, `name`, `retention`, or path parameter.

### Accepted compiler sink-admission seam

Module 3 carries the accepted private two-phase sink seam. It prevents the
runtime from selecting storage that the physical graph cannot write without an
unbounded transfer while keeping storage policy out of the compiler.

The runtime-owned candidate is:

```text
MaterializationSinkCandidateV1
  candidate_id
  storage_kind = local | engine | object
  execution_domain_id
  receipt_protocol_id
  local_row_guard
  local_byte_guard
  authorization_profile_fingerprint
  runtime_policy_rank
```

Module 3 owns the corresponding `MaterializationSinkFeasibilityV1` result,
including disposition, required capabilities, transfer guards, and one bounded
blocker for each candidate. Module 4 consumes that result without redefining
its compiler semantics.

After semantic and relational lowering but before final physical placement or
compilation:

1. the runtime supplies the ordered candidates admitted by current Session
   configuration, authorization, receipt protocols, and storage policy;
2. the compiler evaluates physical feasibility and exact transfer requirements
   without executing data work;
3. the runtime selects the lowest `runtime_policy_rank` candidate whose
   disposition is admitted;
4. the compiler emits and compiles one final physical graph bound to that exact
   candidate;
5. compile or execution failure of the selected graph does not silently switch
   to a lower-ranked sink inside the action.

Module 4 owns candidate construction, policy order, authorization, final
selection, receipt creation, and publication. Module 3 owns feasibility,
transfer proof, stage placement, and the final physical write path. Neither
module owns both halves, and the feasibility list remains private audit input
rather than a public plan.

An exact execution-binding recovery completes before this seam and needs no new sink
selection. Materialized inspection and collection are backing reads and do not
provide durable sink candidates.

### Local materialization policy

The fixed first-cutover policy is:

```text
LocalMaterializationPolicyV1
  schema = "marivo.local_materialization_policy/v1"
  max_rows = 100_000
  max_bytes = 67_108_864
```

It is independent of `LocalExecutionPolicyV1`, even though the first values are
equal. One controls durable output placement; the other controls private local
calculation and transfer.

Local placement is admitted only when the row contract or an owner-registered
action bound proves the row maximum and a schema-aware worst-case byte bound is
within both limits before transfer begins. An exact count without a safe byte
bound is insufficient. The runtime does not issue an extra full count or
download an unknown or high-cardinality Dataset merely to discover that it was
too large.

### Sink choice is bound into audit, not Dataset definition

Storage choice and receipt identity affect Artifact identity but not the
Logical Dataset definition fingerprint. Equal definitions executed in
different Sessions may choose different immutable sinks and therefore produce
different Artifact identities, even if their canonical rows are byte-equivalent.

A later same-Session binding recovery returns the exact existing Artifact rather
than relocating it implicitly. Relocation requires a separately designed
explicit capability; it is not another `execute()` mode.

## Storage Receipt Contract

### First-cutover Parquet contract

Private Parquet exchanges and durable local/object Dataset storage use the same
closed logical-format contract but different authority envelopes:

```text
ParquetDataContractV1
  schema = "marivo.parquet_data_contract/v1"
  contract_version = 1
  arrow_schema_mapping_version
  timestamp_timezone_protocol
  decimal_protocol
  nested_value_protocol
  dictionary_normalization_protocol
  compression_profile
  row_group_profile
  reader_compatibility_fingerprint
  writer_dependency_fingerprint
```

The contract fixes logical round-trip behavior and the compatible pinned
reader/writer profile. A Run-staged exchange references it only through its
physical representation and cleanup manifest. A committed local or object
Dataset receipt additionally binds immutable files, exact row and byte counts,
content authority, and Artifact publication. Equal Parquet syntax therefore
does not make exchange staging a Dataset.

### Common receipt envelope

Every receipt uses:

```text
DatasetStorageReceiptV1
  schema = "marivo.dataset_storage_receipt/v1"
  kind = local | engine | object
  locator
  immutable_authority
  schema_fingerprint
  realized_row_count
  realized_byte_count
  content_authority
  authorization_summary
  scan_admission
```

The concrete variant is closed. Receipt identity is a canonical digest of the
entire secret-safe variant. A receipt is immutable after Artifact commit.

`authorization_summary` names credential-reference and capability classes, not
secret values. `scan_admission` names registered direct-scan, import, or bounded
transfer capabilities; it is not an executable plan.

### Local receipt

```text
LocalDatasetReceiptV1
  kind = local
  project_relative_path
  format = parquet
  parquet_contract_version
  file_manifest[]
  manifest_hash
  bytes_hash
  schema_fingerprint
  realized_row_count
  realized_byte_count: ExactBytesV1
  content_authority
  authorization_summary
  scan_admission
```

The path is under the owning Session's immutable Artifact directory and cannot
contain `..`, an absolute path, symlinks escaping the project, or a mutable
shared filename. Manifest entries have canonical relative paths, sizes, and
hashes. Publication uses same-filesystem atomic rename from a Run-scoped
staging directory.

Local Artifact storage is therefore Parquet in the first cutover. Its directory
belongs to exactly one Artifact and is distinct from every Run-staged exchange
directory. The Parquet contract fixes logical-type mapping, compression,
dictionary normalization, row-group behavior, and reader compatibility; the
manifest and content authority, not the mutable path name, bind the committed
rows.

### Engine receipt

```text
EngineDatasetReceiptV1
  kind = engine
  datasource_ref
  execution_domain_id
  qualified_relation_ref
  immutable_relation_protocol
  relation_version_or_snapshot_token
  schema_fingerprint
  realized_row_count
  realized_byte_count: ExactBytesV1 | UnavailableBytesV1
  content_authority
  authorization_summary
  scan_admission
```

`immutable_relation_protocol` is one registered strategy:

```text
version_addressed_relation
write_once_marivo_relation
snapshot_pinned_relation
```

The adapter must prove that later writes cannot change the rows addressed by
the receipt. A mutable ordinary table, view over mutable sources,
connection-local temporary table, or name protected only by convention is not
an Artifact backing.

The relation uses a generated opaque locator. Raw SQL and credentials are not
persisted. Exact row count comes from the committed write receipt or an exact
count against the immutable version before publication.

### Object receipt

```text
ObjectDatasetReceiptV1
  kind = object
  object_store_ref
  immutable_prefix_or_manifest_ref
  object_version_or_manifest_hash
  format = parquet
  parquet_contract_version
  file_count
  manifest_hash
  schema_fingerprint
  realized_row_count
  realized_byte_count: ExactBytesV1
  content_authority
  authorization_summary
  scan_admission
```

The manifest binds every immutable object version, size, and content hash while
the Artifact metadata keeps only the bounded manifest identity. A mutable
prefix listing without version ids or a committed manifest is invalid.

Object Dataset storage also uses the first-cutover Parquet contract. An engine
may write the files directly, but the immutable object-version manifest and
exact receipt remain mandatory.

The engine may write directly to object storage. Rows do not pass through local
memory unless the compiler and runtime separately admit the bounded transfer.

### Content authority

`content_authority` proves the exact committed rows under the receipt protocol:

```text
CanonicalContentAuthorityV1
  row_contract_fingerprint
  realized_schema_fingerprint
  realized_row_count
  canonical_content_hash_or_version
  hash_or_version_protocol
```

The protocol may be a canonical manifest hash, exact immutable relation
version, or canonical row/file hash. A backend-generated checksum is accepted
only when its registered semantics bind every public column, row multiplicity,
null, type, and ordering fact required by the Dataset contract.

## Dataset Artifact Contract

### Artifact metadata

Every committed Artifact decodes exactly as:

```text
DatasetArtifactV1
  schema = "marivo.dataset_artifact/v1"
  artifact_ref
  session_ref
  family_id
  shape_id
  definition_fingerprint
  execution_key_digest
  row_contract
  row_contract_fingerprint
  realized_schema
  realized_schema_fingerprint
  bounded_lineage
  semantic_dependency_digest
  population_authority
  sampling_execution
  source_authority_set
  operator_implementation_versions[]
  dataset_materialization_contract
  storage_receipt
  content_authority
  realized_row_count
  realized_byte_count
  quality_summary
  typed_issues[]
  evidence_authority
  producing_run_ref
  committed_at
```

Family-owned payloads remain closed discriminated contracts. The runtime
validates their schema ids but does not reinterpret Metric, Candidate, Event,
Lifecycle, or SubjectSet meanings.

The metadata contains no executable logical root, semantic graph, relational
plan, physical plan, SQL, backend connection, or current-catalog resolver.

### Artifact ref and identity

`artifact_ref` is one opaque immutable Session-owned locator. It need not be
the content digest and must not be used as a definition cache key.

Artifact identity binds:

- Session ownership;
- definition fingerprint and input authority tokens;
- exact semantic dependency digest;
- exact Population and realized sampling authority;
- source authority set;
- family, shape, row, schema, and implementation contract versions;
- storage receipt identity;
- canonical content authority;
- quality and Evidence digests.

Two Artifacts with equal rows but different source authority, receipt identity,
quality contract, or implementation version are not the same Artifact.

### Session-local execution key and binding

`DatasetExecutionKeyV1` deliberately excludes current datasource row state,
realized sample membership, the chosen storage locator, commit-time Artifact
ref, Python object identity, variable name, script path, and source location:

```text
DatasetExecutionKeyV1
  schema = "marivo.dataset_execution_key/v1"
  session_ref
  definition_fingerprint
  source_parameter_binding_digest
  ordered_input_authority_tokens[]
  semantic_dependency_digest
  population_definition_digest
  sampling_definition_digest
  family_id
  shape_id
  row_contract_fingerprint
  operator_implementation_versions[]
  quality_contract_versions[]
  evidence_contract_versions[]
```

Logical input tokens bind their normalized upstream definitions; Materialized
input tokens bind exact same-Session Artifact refs and content authority. Thus a
new downstream operator over a recovered checkpoint derives a new key while
still scanning the checkpoint rather than replaying its origin.

The Session Store persists exactly one binding:

```text
DatasetExecutionBindingV1
  schema = "marivo.dataset_execution_binding/v1"
  session_ref
  execution_key_digest
  artifact_ref
  producing_run_ref
  bound_at
```

`(session_ref, execution_key_digest)` is unique and write-once. The binding is
inserted only in the same Store transaction that registers the committed
Artifact and succeeds its producer Run. It cannot be redirected, refreshed, or
deleted independently of the Session. A hit must validate exact Artifact,
marker, storage, Evidence, Session ownership, family, shape, row contract, and
content integrity before constructing a Materialized Dataset.

Source authority and realized sampling remain committed Artifact facts because
they describe what the producer actually observed. They do not participate in
the execution key: querying them would defeat pre-execution lookup, and changing
them later must not silently refresh an established Session snapshot. This does
not authorize cross-Session cache matching by definition.

`source_parameter_binding_digest` is absent only when the complete logical
graph reaches no parameterized source. Otherwise it is the canonical digest of
the ordered per-source `BoundSourceParametersV1.exact_value_digest` values.
Only the digest persists. It does not authorize source replay or disclose a
request payload.

### Materialized public state construction

`MaterializedDatasetState` is constructed only from the conjunction of:

1. exact Artifact metadata;
2. matching Evidence commit marker;
3. matching Session Store Artifact row;
4. validated storage receipt and content authority;
5. same-Session ownership;
6. exact family, row-contract, and schema registrations.

The resulting private root is one `MaterializedScanLeafHandleV1` carrying only
the Artifact ref and validated scan admission. Origin lineage remains audit
metadata and cannot become an executable second root.

## Quality, Evidence, and Findings

### Common materialization-contract envelope

Every producing Dataset definition resolves exactly one immutable contract
before Run admission:

```text
DatasetMaterializationContractV1
  producer_id
  producer_contract_version
  family_id
  qualified_shape_id
  quality_contract_id
  quality_contract_version
  evidence_extractor_id
  evidence_extractor_version
  finding_extractor_id
  finding_extractor_version
  validation_output_contract_ids[]
  retained_private_state_contract_ids[]
  finding_policy_id
```

Module 4 owns this envelope, resolution timing, persistence, and invocation
protocol. The family-owning modules own the exact registrations and semantic
meaning of every quality check, validation output, Evidence projection, Finding
extractor, and retained private state. The envelope is part of Dataset
definition identity, Artifact metadata, and the exact execution key. It is not a
generic extractor: an absent registration, unknown id, family/shape mismatch,
or producer-version mismatch fails before Run admission.

### Staged calculation

Quality checks and Evidence extraction execute before immutable output
publication is commit-decided. They may be pushed into engine stages or consume
registered bounded validation outputs, but their results remain private staging
until the Artifact commit marker.

The runtime requires:

- exact realized row count;
- schema and row-key validation;
- family-owned construction and reconciliation checks;
- one `quality_summary`;
- typed issues in deterministic order;
- one canonical Evidence digest;
- zero or more typed Findings in deterministic identity order.

### Evidence envelope

```text
DatasetEvidenceEnvelopeV1
  schema = "marivo.dataset_evidence/v1"
  artifact_ref
  quality_summary_digest
  typed_issue_digest
  evidence_digest
  finding_count
  finding_identity_digest
  extractor_contract_versions[]
```

An empty Finding set is represented by `finding_count = 0` and the canonical
empty-set digest. It is complete Evidence, not unavailable Evidence.

### Commit marker

The Evidence transaction writes Findings, the Evidence envelope, and exactly
one marker:

```text
DatasetArtifactCommitMarkerV1
  schema = "marivo.dataset_artifact_commit/v1"
  session_ref
  artifact_ref
  producing_run_ref
  artifact_metadata_digest
  storage_receipt_digest
  content_authority_digest
  evidence_digest
  finding_count
  committed_at
```

All fields must match Artifact metadata byte-for-byte under canonical encoding.
The marker is immutable and unique by `(session_ref, artifact_ref)`.

Materialized `show()` and `to_pandas()` never write this envelope or marker.

## Publication Protocol

### Pre-commit staging

Before publication, every mutable resource is Run-scoped:

- bounded Parquet exchanges, DuckDB workspaces, kernel buffers, and local
  primary-output files live under distinct Run staging directories;
- engine relations use an unpublished generated locator and ownership nonce;
- object writes use an unpublished staging manifest or version set;
- quality and Finding records remain in memory or a Run-scoped staging area;
- Artifact metadata is written to a temporary file and fsynced where the
  platform contract requires it.

No public lookup, graph read, Dataset state, or reuse index may expose staging.

### Commit ordering

For a newly produced Artifact:

1. Finish every primary-output and validation stage.
2. Validate exact schema, row key, row count, action bounds, and family checks.
3. Build quality, typed issues, Evidence, and Findings.
4. Finalize the immutable storage object and obtain its exact receipt.
5. Revalidate the finalized receipt and content authority.
6. Clean every compiler-declared non-output temporary resource.
7. Write and atomically publish exact Artifact metadata.
8. In one Evidence Store transaction, write Findings, Evidence envelope, and
   the Artifact commit marker last.
9. In one Session Store transaction, insert the Artifact row, fill the
   write-once execution binding, attach input Artifact edges, release the
   materialization claim, and transition the Run from incomplete to succeeded
   with `output_mode = produced`.
10. Decode the just-committed scan leaf and return the materialized Dataset.

Steps 1–7 are reversible staging. Step 8 decides Artifact publication. Step 9
makes the decision visible through canonical Run and graph reads.

### Execution-binding recovery ordering

For an exact binding hit:

1. derive `DatasetExecutionKeyV1` from the reconstructed Logical Dataset;
2. locate the unique same-Session binding;
3. validate metadata, marker, Store row, storage, authorization, content,
   schema, row count, quality, Evidence, and key agreement;
4. return the paired Materialized Dataset backed by the same scan leaf.

Recovery admits no Run and does not rewrite Artifact metadata, recompute
Findings, refresh Evidence, attach graph edges, relocate storage, or create a
second Artifact.

### Failure before commit decision

Before the marker, any failure:

1. proves every started stage terminal through its declared process-loss
   recovery mode;
2. if that proof is unavailable, keeps the Run incomplete and its claim bound,
   then returns a typed recovery-pending error and no public result;
3. otherwise discards private buffers;
4. cleans compiler-declared temporary resources, Artifact metadata, and
   unpublished storage;
5. releases the materialization claim;
6. transitions the Run to failed with bounded phase and cleanup facts;
7. returns a typed action error and no public result.

Cleanup failure does not publish the primary output. The Run may be terminal
failed with `cleanup_summary.status = pending`; cold maintenance retries the
exact journaled cleanup without re-executing analysis.

### Failure after commit decision

After the marker, storage and Evidence are authoritative. The runtime must not:

- delete the storage;
- delete metadata or Findings;
- mark the Run failed;
- publish a second Artifact;
- rerun the datasource computation.

It retries the Session Store transaction. If that remains unavailable, the Run
stays incomplete, the materialization claim stays bound to it, and the caller
receives a commit-pending error naming the Run ref. Cold reconciliation later
validates the unique marker and completes the same Run.

### Store invariants

The following states fail closed as integrity violations:

- succeeded execute Run without one committed output Artifact and binding;
- failed Run with an output Artifact ref;
- Store Artifact row without matching exact metadata and commit marker;
- marker without exact metadata or immutable storage;
- more than one marker candidate for one producing Run;
- binding whose canonical producer Run is not succeeded;
- receipt row count different from Artifact or Evidence row authority;
- claim committed to an Artifact with a different execution key;
- binding redirected to a different Artifact;
- one Artifact owned by more than one Session.

Recovery may repair only the explicitly recoverable marker-before-Store gap. It
does not guess through contradictory authority.

## Concurrent Materialization

### Materialization claim

Before Run admission, the Session Store admits:

```text
MaterializationClaimV1
  execution_key_digest
  session_ref
  producer_run_ref: absent | admitted_ref
  owner_lease_ref: absent | admitted_ref
  state = producing | commit_decided
  artifact_ref: absent | committed_ref
  acquired_at
  updated_at
```

There is at most one active claim per `(session_ref, execution_key_digest)`. The
record is internal and absent from public Run, Artifact, Graph, Help, and Dataset
contracts.

### Contender behavior

A contender proceeds in stable order:

1. prefer an already bound healthy Artifact;
2. otherwise attempt to acquire the claim;
3. if another live producer owns it, wait for at most 30 seconds under
   `MaterializationClaimPolicyV1` while observing only terminal Store
   transitions;
4. if the producer succeeds, validate and recover its bound Artifact without a
   contender Run;
5. if it fails, atomically acquire a new claim and admit a new producer Run;
6. if its lease expires and the exact owner lock can be acquired, invoke cold
   reconciliation; takeover proceeds only if reconciliation commits or releases
   the old claim;
7. if the wait deadline expires while the producer remains live, return a typed
   retryable concurrency error without a contender Run.

The runtime does not cancel the producer, run a speculative duplicate, or
return a future.

### Different keys

Different execution keys may execute concurrently subject to Session Store
locking, datasource limits, and backend capabilities.
They cannot share private action stages, temporary relations, sampled
Population spines, or execution buffers.

Explicit reuse across actions begins only after one Artifact commits and its
write-once execution binding is visible.

## Process Ownership and Cold Recovery

### Owner lease

Every incomplete Run has one private renewable owner lease:

```text
RunOwnerLeaseV1
  lease_ref
  run_ref
  process_nonce
  host_fingerprint
  owner_lock_ref
  acquired_at
  expires_at
  heartbeat_at
```

The runtime renews the advisory lease independently of long backend statements.
A process-lifetime exclusive owner lock is held for the same Run. A recoverer
never treats elapsed wall time alone as proof that another active process is
dead. Safe takeover requires successful non-blocking acquisition of the exact
owner lock, matching the persisted owner nonce, and atomic replacement under
the Session Store lock. Lease expiry may trigger that check but neither proves
death nor authorizes takeover.

The lease contains no public PID promise. Host and process values are bounded
operational fingerprints, not user identity. The lock target is one exact
Run-scoped file or equivalent Store-backed primitive; it is not a broad Session
or project lock. A platform that cannot provide a reliable process-lifetime
ownership lock does not enable automatic takeover from lease expiry alone.

### Recovery order

On Session activation or explicit runtime recovery, after acquiring recovery
ownership, each exclusively lockable incomplete Run is examined in stable
admission order:

1. read its exact Run and resource journal from one Store snapshot;
2. locate commit markers by exact producing Run ref;
3. if exactly one valid marker, validate metadata and immutable storage,
   reconstruct the missing Artifact row if needed, complete the same Run, and
   release its claim;
4. if no marker, prove every started backend execution is terminal and cannot
   write further using its registered process-loss recovery capability;
5. after that proof, clean journaled temporary, metadata, and unpublished
   storage resources, mark the Run failed with `process_lost`, and release its
   claim;
6. if backend termination or fencing cannot be proved, stop activation with a
   typed recovery-pending error, leave the Run incomplete, and retain its claim;
7. if marker, metadata, storage, or candidate cardinality contradict, stop
   activation with a typed integrity error and do not mutate the conflicting
   records.

Recovery never re-executes a Dataset, reconstructs a logical graph, publishes
Evidence from staging, selects the newest candidate, or converts an incomplete
Run into a new Run.

### Live owner behavior

If an unexpired owner lease exists, a second process may perform read-only
history or graph reads under their existing snapshot contracts, but it cannot
recover, fail, abandon, or take over the live Run. A same-key `execute()` call
with a conflicting claim follows contender behavior.

### Historical terminal Runs

Recovery and cleanup never delete or rewrite succeeded or failed Run history.
A retry after failure creates a new producer Run only if the key remains
unbound. If another producer committed the binding first, the retry recovers
that Artifact without a Run.

## Temporary Resources and Cleanup

### Resource journal

Every external resource or recoverable backend execution is reserved durably
before creation or submission and before it may be consumed:

```text
ActionResourceJournalEntryV1
  schema = "marivo.action_resource_journal/v1"
  run_ref
  resource_ref
  resource_kind
  execution_domain_id
  ownership_nonce
  lifecycle = reserved | created | finalized_output | cleaned | cleanup_pending
  cleanup_capability_id
  safe_locator
  expires_at
  last_cleanup_attempt_at
  cleanup_attempt_count
```

`safe_locator` and `ownership_nonce` are chosen before creation or submission and
are sufficient for the registered adapter to find or target exactly one owned
resource or execution. `reserved` means the external operation may not have
started, may be in flight, or may have succeeded before the lifecycle update;
cleanup therefore treats absence and exact owned presence idempotently. An
adapter that cannot accept or recover an exact pre-reserved locator is not
admitted in the first cutover. The locator cannot contain credentials, SQL,
broad prefixes, unresolved patterns, or a parent directory.

### Resource classes

The journal distinguishes:

```text
planner_temporary_relation
backend_execution
bounded_arrow_exchange
bounded_parquet_exchange
duckdb_workspace
python_kernel_buffer
guarded_local_result
local_storage_staging
engine_storage_staging
object_storage_staging
artifact_metadata
```

Before starting a backend execution, the selected stage must declare either a
registered process/connection-lifetime proof or a recovery capability that can
use the reserved locator to cancel the execution and observe a terminal state.
No-marker recovery does not clean dependent resources or release the claim until
that proof succeeds.

Only the primary output storage and its exact Artifact metadata may transition
to `finalized_output`. Before the Artifact marker both remain eligible for exact
cleanup. After the marker, storage is governed by the receipt and metadata by
the marker digest; neither remains cleanup-eligible even if a journal lifecycle
update was interrupted.

### Cleanup rules

The runtime attempts cleanup on:

- success before terminal Run publication;
- ordinary failure;
- cooperative cancellation;
- transfer or output-bound violation;
- compilation failure after a temporary compile resource was created;
- expired-owner cold recovery.

Cleanup uses exact resource ref plus ownership nonce and is idempotent. Missing
already-cleaned resources count as success only when the adapter can prove the
locator belonged to this Run. Broad prefix deletion and guessed temporary names
are forbidden.

Connection-scoped temporary resources that the backend guarantees to destroy
on disconnect may use a registered `connection_lifetime` cleanup proof. The
runtime still records the resource class and proof; it does not persist an
unusable connection handle.

### Cleanup and action outcome

Before Artifact commit, unresolved cleanup prevents success. The primary output
remains unpublished and the Run fails with a cleanup summary. A background or
later cold maintenance pass may retry cleanup, but cannot change the failed Run
to succeeded.

After the commit marker, all non-output cleanup must already be complete. A
design that requires best-effort temporary cleanup after publication is not
admitted in the first cutover.

## Artifact Recovery and Immutable Scan Leaves

### `session.artifact(ref)` validation

Exact recovery validates:

1. the ref exists in the owning Session Store;
2. Artifact metadata is exact `marivo.dataset_artifact/v1`;
3. the producing Run exists and succeeded with `output_mode = produced`;
4. the Evidence marker and envelope match metadata;
5. Finding count and identity digest match;
6. the storage receipt variant and digest match;
7. storage is readable under current authorization;
8. the immutable version, manifest, or content authority still matches;
9. realized schema and exact row count match the public row contract;
10. the registered family and scan adapter versions are supported.

Failure is typed as absent, incompatible generation, authorization unavailable,
storage missing, storage mutated, Evidence corrupt, row-contract incompatible,
or Session ownership mismatch. No failure causes logical replay.

### Scan-leaf decoding

Successful recovery constructs:

```text
MaterializedScanLeafHandleV1
  artifact_ref
  receipt_kind
  receipt_identity_digest
  content_authority_digest
  row_contract_fingerprint
  realized_schema_fingerprint
  scan_admission
```

The handle is private, immutable, and bound to the recovering Session. It is
not serializable as a public value and cannot be attached to another Session.

The compiler may include admitted projection, predicate, ordering, or
retained-value fold operations in the scan's Ibis expression. It may not inspect Artifact lineage to
recover the origin graph.

### No Materialized `execute()` method

A Materialized Dataset does not expose `execute()`. It is already the reusable
Artifact-backed state. Call `show()` or `to_pandas()` to read it, or call any
registered downstream operator to obtain a new Logical Dataset. Reconstructing
the exact prior Logical Dataset and calling `execute()` resolves the
same-Session binding without a new Run.

## Revalidation

### Result axes

The exact public result family remains owned by the Session read design, while
this module owns the facts it may contain:

```text
ArtifactRevalidationV2
  artifact_ref
  checked_at
  artifact_integrity
  storage_authority
  evidence_integrity
  semantic_authority
  datasource_authority
  issues[]
```

`ArtifactRevalidationV2` is the private persisted/projection schema name. The
public immutable result spelling is the retained `ArtifactRevalidation` frozen
by the accepted lazy v3 Session Runtime Read amendment; it has no versioned
public alias or second result class.

Each axis is closed and independent:

```text
artifact_integrity = valid | invalid | unverifiable
storage_authority = readable | unauthorized | missing | mutated | unknown
evidence_integrity = valid | invalid | unverifiable
semantic_authority = current | changed | missing | unverifiable
datasource_authority = current | changed | unverifiable | unknown
```

`valid` Artifact integrity does not imply current semantics or datasource
freshness. `changed` datasource authority does not imply corrupt storage.
`invalid` means a completed check proved a contradiction. `unverifiable` means
the required metadata or Evidence authority could not be read or checked; its
bounded issue identifies the unavailable check without pretending corruption.

### Read-only behavior

Revalidation may query current semantic state, datasource metadata, immutable
storage metadata, and Evidence records. It does not:

- execute the original Dataset;
- mutate Artifact metadata or Dataset state;
- refresh Evidence or Findings;
- publish a new Run or Artifact;
- replace source authority;
- bless an unverifiable source as current;
- combine several Artifacts into one compatibility verdict.

The result states exactly which checks were not possible and why. Missing
credentials, unsupported snapshot comparison, and a changed source are
different outcomes.

### Runtime use of revalidation logic

Ordinary reads reuse only the Artifact, storage, Evidence, and authorization
validators. Operator execution invokes semantic or datasource comparison only
when its selected authority requirement demands it.

The public revalidation action is not itself a Dataset action and does not
replace the action-specific Run admission contract.

## Multi-Downstream Reuse

Explicit materialization is the only durable sharing boundary:

```python
features = session.observe(
    metrics=[scanned_bytes, peak_memory_bytes, cpu_seconds],
).execute()

association = features.correlate(method="spearman")
outliers = features.metric(cpu_seconds).discover.entity_outliers(...)
```

Each downstream action records the common Artifact ref as an input edge and
plans from the immutable scan leaf. No downstream action traverses or executes
the original observation graph.

Input reuse and execution-binding recovery are separate runtime contracts:

- input reuse is exact and mandatory whenever an operator operand is a
  Materialized Dataset. The runtime validates that Artifact ref and supplies
  its scan admission; it performs no definition-equivalence lookup and never replaces it
  with current semantic execution;
- execution-binding recovery applies only to a Logical Dataset `execute()` call
  whose complete execution key names one healthy committed Artifact binding.

The first rule covers partial and complete materialization of multi-input
operators. The compiler may directly scan, engine-import, or guarded-transfer
the Artifact according to its boundary contracts, but a temporary import is
action-scoped and cannot become a second durable checkpoint. A committed
Artifact that is not named by an input or its exact write-once execution binding
is not an implicit cache candidate.

Holding one Logical Dataset in a Python variable is not the reuse mechanism.
The named Session binding is durable: another script or process may reconstruct
the same exact logical definition and call `execute()` to recover the Artifact.
Python variable names, script paths, and line numbers are irrelevant.
Action-local compiler CSE remains private and disappears when its producer
action ends.

There is no public multi-sink action, execution bundle, shared future, or
automatic common materialization.

### Cross-script reconstruction

Scripts are authoring references, not runtime identity. Re-running a script in
the same named Session reconstructs the Logical Dataset and therefore the same
`DatasetExecutionKeyV1`. Its `execute()` call recovers the bound Artifact before
datasource work. Appending a downstream operator to that script constructs a
new Logical Dataset whose input-authority vector contains the recovered
Artifact ref; executing only that new tail scans the checkpoint.

The runtime never keys reuse by script path, source line, Python variable,
process id, or object identity. A script that does not reconstruct the logical
definition can instead recover the exact checkpoint explicitly with
`session.artifact(ref)`.

## Session Graph Projection

The existing Run-first Session graph model consumes these runtime facts:

- materialized `show()` and `to_pandas()` create no analysis Run or graph edge;
- every produced execute Run has one output Artifact edge;
- execution-binding recovery creates no Run or graph edge;
- every materialized input creates an ordered Artifact-to-Run input edge;
- failed and incomplete Runs remain visible without partial Artifact nodes;
- a downstream chain over a scan leaf records the exact Artifact input even
  when its public definition fingerprint resembles a logical chain;
- claims, owner leases, staging resources, cleanup journals, and commit-pending
  repair mechanics remain private operational state.

Graph reads use only committed Session Store facts plus exact immutable Artifact
metadata. They never query a datasource, run revalidation, perform recovery, or
include uncommitted staging.

## Failure and Repair Matrix

| Condition | Run outcome | Public output | Repair |
| --- | --- | --- | --- |
| deterministic Dataset or graph contract invalid before admission | no Run | none | fix Dataset construction or incompatible runtime |
| semantic dependency missing after admission | failed `semantic_validation` | none | repair current semantic authoring or use an admitted materialized-only path |
| datasource snapshot weaker than required | failed `source_snapshot` | none | use a capable datasource path or explicit materialized input |
| execution boundary unavailable | failed `execution_boundary` | none | use one registered source, federation engine, or reachable explicit boundary |
| Ibis/backend compile rejection | failed `ibis_backend_compile` | none | repair Ibis/lowerer/adapter conformance or choose a supported registered method |
| transfer or local guard exceeded | failed `transfer_guard` | none | narrow scope, use engine execution, or use an admitted explicit boundary |
| output schema, key, or count mismatch | failed `output_validation` | none | repair backend lowering or operator contract |
| no durable sink for result bounds | failed `storage_selection` | none | configure an admitted immutable engine/object sink or reduce the result |
| quality or Evidence extraction fails | failed before marker | none | repair the check/extractor and retry as a new Run |
| temporary cleanup fails before marker | failed `cleanup` | none | retry exact journal cleanup, then rerun as a new Run |
| marker commits but Store finalization fails | remains incomplete, commit pending | none in current call | resume/recover the Session; never rerun blindly |
| process dies before marker | incomplete until owner-lock recovery; failed only after old execution is terminal | none | complete recovery; retry only after the old claim is released |
| process dies after marker | incomplete until recovery, then succeeded | recover exact Artifact | resume/recover the Session |
| exact healthy execution binding already exists | no Run; validated recovery | Materialized Dataset | none |
| candidate Artifact storage is missing or mutated | failed integrity check | none | restore exact immutable storage or recompute as a new Artifact |
| current datasource changed after Artifact commit | Artifact stays readable | revalidation reports changed | use the bound Session snapshot knowingly or execute in a new Session |

Repairs are derived from actual registered capabilities, storage configuration,
Artifact state, and operator authority contracts. They never suggest a hidden
local fallback, logical recovery by fingerprint, or an internal graph boundary
the caller cannot materialize.

## Safe Diagnostics

### Dataset cards and contracts

Before execution, Dataset surfaces may disclose:

- logical or materialized state;
- exact state-specific actions;
- materialized input refs;
- authority requirement classes;
- possible storage kinds;
- fixed local materialization bounds;
- action-time blockers known without live work;
- whether this exact definition already has a same-Session execution binding.

An unexecuted Logical Dataset does not claim a selected sink, current snapshot,
Artifact ref, row count, quality, or Evidence. Binding lookup is performed only
by `execute()`.

### Run audit

Terminal Run reads may disclose:

- action kind and lifecycle;
- safe Dataset definition projection;
- materialized input refs;
- source authority classes and safe digests;
- compiler audit fingerprints and counts;
- Ibis semantic compiler version, lowerer-manifest fingerprint, Ibis/compiler
  fingerprints, boundary-profile fingerprints, and safe applied lowerer ids;
- stage, placement, transfer, and bound summaries;
- produced output Artifact ref for execute Runs;
- failure phase, kind, retry disposition, and cleanup status.

They do not expose raw source tokens when sensitive, query text, SQL, raw
identities, credentials, private plans, temporary locators, or staging paths.

### Artifact cards and contracts

A materialized Dataset may disclose committed:

- Artifact ref, family, shape, definition fingerprint, and producing Run;
- storage kind, realized schema, exact row count, and byte-count availability;
- quality summary, typed issues, Evidence digest, and Finding count;
- bounded lineage and source-authority class;
- mechanically valid reads, revalidation, and downstream continuations.

It does not expose storage credentials, raw object manifests, engine SQL,
identity rows, or origin executable plans.

## Cross-Module Seams

### Dataset Core supplies

- exact Dataset owner, family, row contract, schema, state, and definition
  fingerprint;
- logical root or materialized scan-leaf handle;
- ordered input authority tokens and bounded lineage;
- state-specific action surface, preview bound, collection contract, and paired
  Logical-to-Materialized execute return contract;
- public MaterializedDatasetState fields to construct after commit or binding
  recovery.

This runtime supplies action execution, persisted authority, and the exact scan
leaf. It does not widen the public Dataset type.

### Observation Model supplies

- normalized Population definition and sampling contract;
- exact per-source `BoundSourceParametersV1` values captured at logical source
  construction plus their canonical digests and safe projections;
- semantic dependency set;
- filter occurrence authority requirements;
- realized Population, sampling, construction, and reconciliation validation
  requirements;
- exact quality facts needed for Population and observation Evidence.

This runtime captures realized authority and publishes it only with the
Artifact.

### Ibis Compiler and Execution Boundaries supplies

- immutable executable stage graph;
- Ibis/backend compiler requirements and resolved safe boundary-profile
  fingerprints;
- source snapshot requirements per stage;
- process-loss recovery mode for every executable stage;
- primary output, validation output, schema, key, and ordering contracts;
- action-scoped temporary-resource declarations;
- exact Arrow or Run-staged Parquet exchange contracts, bound guards, and the
  selected DuckDB/Python local implementation registration;
- storage-domain and transfer admissions;
- compiler audit projection and failure phase.

This runtime admits the Run, supplies live authority, executes stages, validates
Arrow/Parquet exchanges, invokes only the bound local implementation, journals
and cleans resources, enforces bounds, chooses storage, commits publication,
and records the outcome.

### Typed Operators supplies

- operator implementation and quality-contract versions;
- selected authority mode for every non-filter occurrence;
- action-time validation requirements;
- family-specific quality, Evidence, and Finding extraction contracts;
- retained sufficient-statistic and materialized-read requirements.

The runtime invokes registered contracts and commits their outputs atomically;
it does not invent statistical meaning.

### Subject, Event, and Lifecycle supplies

- privacy-safe identity authority and storage constraints;
- subject-selection, Event, and Lifecycle authority modes;
- family-specific validation, Evidence, and Finding contracts;
- exact cold-recovery privacy invariants.

The runtime must keep raw identities out of metadata, Run audit, Evidence cards,
errors, and cleanup journals. Storage may contain governed identity rows only
under the family-owned privacy and authorization contract.

### Public Cutover consumes

- replacement Store, Run, Artifact, execution-binding, Evidence, receipt,
  claim, lease, and resource-journal schemas;
- removal of eager Frame persistence, old schema readers, and dual execution;
- implementation order for action admission, execution, storage, publication,
  recovery, reads, Help, docs, skills, and tests;
- exact failure injection and real-agent acceptance journeys.

The cutover must replace the persistence generation atomically. It cannot ship
logical Datasets with eager Artifact recovery or lazy Artifact recovery with
the eager public operator algebra.

## Rejected Alternatives

### Persist logical Datasets for recovery

Rejected. Scripts remain authoring references and reconstruct Logical Datasets;
the runtime persists only the execution-key binding to the resulting Artifact.
Persisted Run projections cannot reconstruct a private graph or become operator
inputs.

### Use definition fingerprint as an Artifact cache key

Rejected as an Artifact identity. The Session-local execution key includes the
definition fingerprint plus semantic, input-authority, family, row, operator,
quality, and Evidence contracts, while the bound Artifact separately records
realized source, sampling, receipt, content, quality, and Evidence authority.

### Publish storage before Evidence and call it an Artifact

Rejected. Immutable bytes without the commit-time governed contract are
unpublished storage, not a Dataset Artifact.

### Succeed materialization with partial Evidence

Rejected for the first cutover. A zero-Finding complete envelope is valid;
extractor or Evidence publication failure is not.

### Make the Session Store row the first commit decision

Rejected. A Store row cannot prove that independently persisted Findings,
Evidence, metadata, and storage committed. The marker is written only after all
of those facts exist and authorizes the one recoverable Store gap.

### Mark a post-marker Run failed

Rejected. Once the commit marker exists, cleanup and retry semantics reverse:
the runtime must preserve storage and complete the same Run.

### Allow duplicate concurrent Artifacts and deduplicate later

Rejected. It spends datasource work twice and creates ambiguous canonical
producers. Claims coordinate before publication.

### Automatically relocate a materialized Dataset

Rejected. A Materialized Dataset has no `execute()` method. Relocation would
need a separately named capability, new Artifact identity, and new Run
semantics.

### Treat datasource freshness as Artifact integrity

Rejected. An immutable snapshot remains valid when its origin changes. Current
freshness is a separate revalidation observation.

### Require current semantic state for ordinary Artifact reads

Rejected. Materialized-only reads use committed row and Evidence authority.
Catalog drift cannot reinterpret or hide committed rows.

### Fall back from semantic-current to materialized-only

Rejected. Authority branch selection is explicit and fingerprinted before
execution. Failure does not change meaning.

### Store temporary-resource names only in process memory

Rejected. Process loss would make durable engine or object staging
unrecoverable and leak resources without an auditable owner.

### Run best-effort cleanup after successful publication

Rejected in the first cutover. All non-output resources must be clean before
the commit marker.

### Expose claims, leases, receipts, or commit-pending handles publicly

Rejected. These are runtime coordination facts, not analysis values or public
continuations.

## Vertical Acceptance Journeys

### Execute and inspect one logical definition

1. Construct an Entity-grained Logical Metric Dataset.
2. Assert no Run, Artifact, Evidence, or storage exists.
3. Call `execute()` and retain the returned Materialized Metric Dataset.
4. Assert one execute Run and one complete Artifact, Evidence envelope, and
   write-once execution binding exist.
5. Call `show()` on the Materialized Dataset and assert it reads a bounded
   deterministic preview without origin datasource work or a new Run.

### Materialized complete collection without re-entry

1. Execute a bounded Logical Dataset.
2. Call `to_pandas()` on the returned Materialized Dataset.
3. Assert no origin SQL and no new analysis Run occurs.
4. Assert the DataFrame is complete and isolated.
5. Assert the DataFrame has no Artifact ref and cannot enter a Dataset operator,
   while the Materialized Dataset remains reusable by downstream operators.
6. Inject a transfer-bound failure and prove no partial DataFrame is returned.

### Local materialization

1. Materialize a statically bounded result under the local policy.
2. Assert staged files are invisible before commit.
3. Assert exact manifest, bytes hash, schema, row count, byte count, quality,
   Evidence, Findings, metadata, marker, Artifact row, and Run success agree.
4. Recover the Artifact in a cold process and read it as the same Dataset family.
5. Prove the recovered handle contains no logical root.

### SQL prefix followed by local execution

1. Execute a definition whose maximal upstream closure is one datasource Ibis
   stage and whose registered tail is a DuckDB relational stage.
2. Prove the Arrow exchange is schema- and guard-validated, the DuckDB workspace
   is Run-scoped, and only the root output enters local Parquet Artifact staging.
3. Execute a second definition whose registered Python kernel requires
   rewindable input and prove the Run-staged Parquet exchange has no storage
   receipt, Artifact ref, Evidence, or graph node.
4. Inject DuckDB, kernel, Arrow, Parquet, and cleanup failures and prove no
   exchange survives as reusable authority and no partial Artifact is visible.
5. Complete both paths and prove every non-output exchange and workspace is
   cleaned before the commit marker.

### High-cardinality engine materialization

1. Materialize a Dataset whose output is not admitted for local storage.
2. Assert no unbounded local transfer occurs.
3. Assert the engine relation uses an admitted immutable protocol.
4. Assert exact row count and version-pinned receipt are committed.
5. Execute downstream projection and filtering against the scan leaf without
   origin replay.

### Object materialization

1. Use an engine that cannot retain an immutable relation but can write an
   admitted immutable object Dataset.
2. Assert manifest and objects commit before the Artifact marker.
3. Assert local memory never receives the unbounded rows.
4. Recover through an admitted scan/import path.

### Repeated exact logical execution

1. Execute one Logical Dataset in a named Session.
2. Reconstruct the same definition in a later script or process and call
   `execute()` in that same Session.
3. Assert the same `DatasetExecutionKeyV1` resolves the same Artifact.
4. Assert no new Run, datasource calculation, quality extraction, Finding
   publication, storage copy, or Artifact occurs.

### Non-replayable source materialization

1. Materialize against a datasource that cannot expose comparable snapshot
   authority.
2. Assert the Artifact is recoverable by ref.
3. Reconstruct the logical definition in the same named Session.
4. Assert the established binding recovers the same Artifact without claiming
   cross-Session source equivalence.

### Action-scoped sample materialization

1. Materialize a logical Dataset whose Population is sampled inside the action.
2. Assert the realized sample is evaluated once and committed in Artifact
   authority.
3. Reconstruct the same logical definition in the same Session and assert its
   binding recovers the same Artifact without resampling.
4. Reconstruct it in a new Session and assert a new execution occurs; an equal
   policy or seed does not prove cross-Session realized-membership equivalence.

### Concurrent same-key materialization

1. Start two `execute()` calls for the same exact execution key.
2. Assert one producer claim exists and only one Run is admitted.
3. Assert only one datasource execution runs.
4. Assert the producer commits one Artifact.
5. Assert the contender returns the bound Artifact without a contender Run.

### Producer failure and claim takeover

1. Fail the elected producer before its commit marker.
2. Assert cleanup completes and the producer Run fails.
3. Assert one waiter atomically acquires a new claim.
4. Assert it produces one Artifact under its own Run without rewriting history.

### Crash before commit marker

1. Crash after immutable staging exists but before the marker.
2. Lose the owner process, acquire the exact owner lock, and replace the matching
   persisted nonce under the Store lock.
3. Assert recovery finds no marker, cleans exact journaled resources, fails the
   same Run with `process_lost`, and releases the claim.
4. Assert no Artifact or Evidence authority becomes visible.

### Crash after commit marker

1. Crash after marker commit but before Session Store finalization.
2. Assert storage, metadata, Evidence, and Findings remain intact.
3. Resume the Session.
4. Assert recovery validates the unique marker, registers the Artifact,
   succeeds the same Run, and returns the recoverable Dataset by ref.
5. Assert no datasource execution or second Artifact occurs.

### Temporary-resource cleanup

1. Force a sampled Population plan to use a single-evaluation temporary
   relation fence.
2. Validate cleanup on success, execution failure, guard failure, cooperative
   cancellation, and expired-owner recovery.
3. Inject cleanup failure and assert no Artifact marker commits.
4. Assert later cleanup targets only the exact journaled locator and nonce.

### Semantic-current over a materialized leaf

1. Materialize a Dataset.
2. Change one current semantic dependency.
3. Execute one materialized-only retained-field operator and assert it reads the
   fixed leaf without catalog reinterpretation.
4. Execute one semantic-current operator whose compatibility contract depends
   on the changed dependency and assert a typed authority failure.
5. Assert neither path replays the leaf's origin graph.

### Datasource freshness revalidation

1. Materialize against a versioned source.
2. Change the live source version.
3. Recover and read the Artifact successfully.
4. Call `session.revalidate(...)` and assert Artifact, storage, and Evidence
   remain valid while datasource authority reports changed.
5. Rerun the logical definition in the same Session and assert its binding still
   recovers the committed snapshot; use a new Session to observe changed rows.

### Strict Evidence atomicity

1. Inject failure in quality, Finding extraction, Evidence envelope write, and
   marker write separately.
2. Assert each action returns no materialized Dataset.
3. Assert no committed Artifact row or partial Evidence is visible.
4. Assert pre-marker storage and Artifact metadata are cleaned from their exact
   journal reservations.

## Implementation Evidence Required

The Public Cutover Plan must require at least:

- exact schema decode and future/old-generation rejection tests;
- bounded compiler-audit round trips proving that no Ibis expression, generated
  SQL, lowerer result payload, or private graph enters Run state;
- Run variant and illegal-transition property tests;
- proof that incomplete Run admission precedes live profile resolution,
  compilation, datasource statements, transfers, and resource creation;
- local, engine, and object receipt round trips and mutation detection;
- exact row-count validation for every receipt family;
- publication failure injection at every ordered boundary;
- a mutation-between-capture-and-read test proving that only a version-bound
  statement remains replay-comparable;
- marker-before-Store cold recovery tests;
- no-marker process-loss cleanup and terminal failure tests;
- ambiguous marker, mismatched metadata, missing storage, and corrupt Evidence
  fail-closed tests;
- exact execution-key and write-once binding tests, including action-scoped
  sampling and non-replayable sources;
- source-binding tests proving construction-time capture, execution after scope
  exit, changed-value key separation, same-value cold reconstruction recovery,
  no action-time ambient lookup, and exhaustive raw-value redaction;
- same-key concurrent producer, waiter, failure, timeout, and takeover tests;
- process owner-lock, lease renewal, and stale-owner takeover tests;
- reservation-before-create crash injection, including Artifact metadata, plus
  idempotent cleanup tests;
- backend-work-survives-owner tests proving recovery retains the claim until the
  old execution is terminal;
- Materialized Dataset `show()` and `to_pandas()` no-origin-replay and no-new-Run
  tests;
- strict zero-Finding Evidence and extractor-failure atomicity tests;
- authority-mode branch enforcement and no-fallback tests;
- revalidation axis independence tests, including unreadable Artifact metadata
  and Evidence authority as `unverifiable` rather than `invalid`;
- cold scan-leaf recovery without logical origin tests;
- Session graph produced/binding-recovery/input-edge tests;
- redaction and bounded-payload adversarial tests;
- local policy boundary tests at exactly and above row and byte limits;
- bounded Arrow exchange tests that reject a bad batch before consumer
  admission and account decoded Arrow-buffer bytes deterministically;
- Run-staged Parquet tests for manifest/file/decoded-byte guards, schema drift,
  reservation-before-create, cleanup, and proof that no receipt, Artifact,
  Evidence, execution binding, or graph node is created;
- DuckDB-workspace and Python-kernel-buffer crash/cleanup tests proving only the
  guarded root output may enter durable storage selection;
- local and object receipt tests proving first-cutover durable file storage uses
  the exact versioned Parquet contract and cannot alias exchange staging;
- engine/object high-cardinality journeys proving no unbounded local transfer;
- deterministic audit and receipt identity tests;
- current English/Chinese Help, docs, skill, and card drift tests owned by the
  cutover module;
- real-agent journeys that execute, recover in a fresh process, inspect
  Run/Artifact authority, revalidate drift, and continue analysis from the
  immutable leaf.

Local process health, a successful backend query, a staged file, an Evidence
row without a marker, or a Run transcript is not acceptance. The terminal proof
is a recoverable same-family Dataset backed by one exact committed Artifact, or
one terminal failed Run with no partial publication.

## Acceptance Criteria

This design is complete when all of the following are reviewable without
consulting implementation guesses:

1. every producing execution kind and lifecycle transition is exact;
2. pre-admission and post-admission work are distinguishable;
3. Run, Artifact, execution-binding, receipt, Evidence, commit-marker, claim,
   lease, and resource-journal schemas are closed and versioned;
4. source authority is exact without inventing replayability;
5. local, engine, and object storage have durable immutable receipt contracts;
6. exact row count is mandatory for every materialized Artifact;
7. commit ordering identifies one irreversible publication decision;
8. pre-marker failure publishes nothing;
9. post-marker failure remains recoverable and never becomes failed;
10. repeated and concurrent exact execution produce one canonical Artifact and
    one producer Run per binding;
11. unversioned sources and action-scoped stochastic samples remain pinned by
    their same-Session binding without claiming cross-Session equivalence;
12. Materialized `show()` and `to_pandas()` read only the committed Artifact and
    create no analysis Run;
13. every committed Artifact has complete quality and Evidence authority;
14. temporary resources are journaled and cleaned on every terminal path;
15. recovery never re-executes analysis or guesses through conflicting state;
16. materialized scan leaves cannot reach through to origin plans;
17. authority modes are selected once and never used as runtime fallback;
18. revalidation keeps integrity, storage, semantic, and datasource facts
    independent;
19. Session graphs expose committed causality without operational coordination
    state;
20. the persistence cutover has no migration, alias, or dual-read path;
21. replay-comparable source authority is bound to the statements that read it;
22. external resources and recoverable executions are reserved before creation
    or submission;
23. cold recovery releases no claim while old backend writes remain possible;
24. Store v3, `storage_selection`, and unverifiable integrity states are exact
    closed contracts;
25. every producing definition resolves one exact
    `DatasetMaterializationContractV1` before Run admission, while family owners
    define its semantic checks and Module 4 alone owns invocation and atomic
    publication;
26. bounded Arrow streams and Run-staged Parquet are private exchange
    representations governed by exact physical schemas, not Dataset authority;
27. no exchange file, DuckDB workspace, or Python-kernel buffer can receive a
    storage receipt, Artifact ref, Evidence envelope, execution binding, or
    Session graph node;
28. every private local-execution resource is reserved, journaled where
    applicable, and cleaned before the Artifact marker or on no-marker recovery;
29. first-cutover local and object Dataset file receipts use one exact versioned
    Parquet contract, while engine receipts remain immutable relations;
30. only the complete primary output is eligible for durable sink selection and
    publication, regardless of how many SQL and local stages produce it;
31. parameterized source bindings are immutable logical-definition inputs,
    participate in `DatasetExecutionKeyV1` by exact digest, and are never
    resolved from ambient Session state at action time;
32. persisted runtime, authority, Evidence, graph, diagnostic, and disclosure
    records contain no raw source-binding values.

## Owner-Confirmed Module Decisions

The owner confirmed the original runtime decisions on 2026-09-01, revised the
Dataset action and reuse boundary on 2026-09-02, revised private local exchange
and Parquet storage on 2026-09-03, and froze parameterized-source execution
identity on 2026-09-04:

1. only an `execute()` binding miss that wins the producer claim admits one Run
   with `absent -> incomplete -> succeeded|failed`;
2. pure graph/schema validation, execution-key derivation, binding lookup, and
   producer-claim election precede Run admission;
3. use `marivo.analysis_action_run/v2` and
   `marivo.dataset_artifact/v1` as a clean persistence generation;
4. the Session Store owns canonical Run lifecycle and graph edges;
5. one Evidence Store commit marker is the cross-store Artifact publication
   decision;
6. a post-marker Run can only recover to succeeded, never failed;
7. every committed Dataset Artifact has complete quality and Evidence authority,
   including a valid zero-Finding envelope;
8. local, engine, and object receipts form the only storage family;
9. local materialization uses a fixed 100,000-row and 64-MiB policy separate
   from bounded-local execution policy;
10. immutable engine storage requires a registered version-addressed,
    write-once, or snapshot-pinned relation protocol;
11. one exact `DatasetExecutionKeyV1` binds normalized logical definition,
    semantic dependencies, ordered input authority, and contract versions;
12. source rows and realized samples do not enter the key; the bound Artifact
    records those realized facts and remains the Session snapshot;
13. repeated same-Session execution-binding recovery creates no new Run and
    reuses one exact healthy Artifact;
14. same-key concurrent calls elect one producer and wait for at most 30
    seconds under `MaterializationClaimPolicyV1`; only the producer admits a
    Run, and waiters recover its binding on success;
15. owner locks, advisory leases, and resource journals make process-loss
    recovery deterministic without time-only takeover;
16. all non-output temporary resources are cleaned before the Artifact marker;
17. cold recovery may repair only the unique valid marker-before-Store gap;
18. `semantic_current`, `materialized_only`, and
    `semantic_or_materialized` are selected requirements, never fallbacks;
19. revalidation includes datasource authority when comparable and reports
    unknown or unverifiable otherwise;
20. datasource drift never invalidates immutable Artifact readability;
21. Materialized Datasets expose no `execute()`; all registered downstream
    operators remain available and return new Logical Datasets;
22. Materialized `show()` and `to_pandas()` may persist bounded operational read
    audit but create no analysis Run or new authority;
23. operational claims, leases, receipts, journals, and commit-pending state
    remain private;
24. no old eager persistence schema is migrated or dual-read;
25. Module 4 ranks durable sink candidates and selects the lowest-ranked
    compiler-admitted candidate; Module 3 owns feasibility and the final physical
    write path;
26. a process-lifetime owner lock plus matching persisted nonce authorizes
    takeover; lease expiry is advisory and cannot authorize takeover alone;
27. ordinary crash recovery no longer requires caller-confirmed
    `abandon_run(...)` once exact owner-lock takeover is safe;
28. replay-comparable source authority requires version-bound or
    transaction-pinned reads;
29. every external resource, backend execution, and Artifact metadata path is
    reserved before creation or submission;
30. no-marker recovery keeps the Run incomplete and retains its claim until old
    backend execution is terminal;
31. the clean replacement Session Store is exact `user_version = 3` with no v2
    in-place upgrade;
32. sink-admission absence is `storage_selection`, not storage staging;
33. Artifact and Evidence integrity distinguish proved invalidity from an
    unverifiable check.
34. Arrow is the canonical local exchange schema and bounded Arrow streaming is
    the default one-pass transport;
35. rewindable private input uses only Run-staged Parquet with exact guards and
    cleanup, never Artifact, Evidence, receipt, binding, or graph authority;
36. DuckDB workspaces and Python-kernel buffers are Run-owned non-output
    resources and must be cleaned before publication;
37. first-cutover local and object Dataset file storage uses the exact versioned
    Parquet contract, distinct from exchange staging;
38. only the complete Dataset root output may become a durable sink candidate
    and enter atomic Artifact publication;
39. parameterized non-secret JSON values are captured by the logical source
    definition, represented in the execution key by an exact opaque digest,
    and never reread from a dynamic Session scope during `execute()`;
40. raw captured source-binding values remain process-local compiler inputs and
    are absent from every persisted or user-visible runtime surface.
41. all lazy runtime state lives under the generation-scoped v3 root; the eager
    fixed-path v2 Store is neither upgraded nor decoded;
42. the Session Store relations, Evidence Store v1 relations, keys, indexes,
    and transaction owners are exactly those frozen under Runtime Schema
    Generation;
43. report-timezone facts live in the v3 `sessions` relation and no Session
    metadata sidecar survives.

Changing one of these decisions requires an explicit amendment to this module
before Typed Operators, Subject/Event/Lifecycle, or Public Cutover relies on a
replacement contract.

## Owner Confirmation

On 2026-09-01 the owner accepted all recommended choices: strict Evidence
atomicity, the Evidence-side commit marker, 30-second wait-and-reuse
coordination, datasource-aware revalidation, the separate 100,000-row/64-MiB
local storage policy, process-lock-authoritative crash recovery, and the
runtime-ranked/compiler-proven durable sink seam. The owner also accepted the
review corrections above as minimal amendments to those choices.

On 2026-09-02 the owner replaced the prior action/reuse semantics with paired
Logical and Materialized Dataset states, Logical-only zero-argument
`execute()`, Materialized-only reads, downstream operators on both states, and
the write-once same-Session execution binding defined above. Those later
decisions supersede any older reuse wording in predecessor plans.

On 2026-09-04 the owner retained `Session.source_bindings(...)` as an
authoring-time scope whose exact non-secret values are captured by each logical
source definition. The owner rejected execution-time ambient lookup and bound
the exact value digest into same-Session execution identity under the redaction
rules above.

On 2026-09-04 the owner also accepted the generation-scoped v3 layout, the
independent Evidence Store `user_version = 1`, the exact relations and
transaction owners above, and removal of the Session metadata sidecar. These
decisions make creation of a new lazy named Session independent of an existing
eager v2 Store without migration, decoding, or dual reads.

No Module 4 owner-choice question remains open. Its consumed Observation Model
contract and family-owned materialization registrations are accepted.

## Final Boundary

The runtime does not make a Logical Dataset durable by serializing its plan. A
script reconstructs the definition; the named Session resolves its exact
execution key to one realized Dataset. The runtime makes that result durable by
committing exact immutable storage, quality, Evidence, Findings, metadata,
Session causality, and the write-once execution binding as one recoverable
authority decision.

Before that decision, every row and resource is private staging and failure
publishes nothing. After that decision, recovery completes the same Run and
preserves the same Artifact. A downstream action consumes only the immutable
scan leaf or explicitly selected current semantic authority; it never reaches
through a committed Dataset to replay its origin.
