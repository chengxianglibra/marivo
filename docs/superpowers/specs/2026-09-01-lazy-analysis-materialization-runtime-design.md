# Lazy Analysis Materialization Runtime and Authority Design

Execution follows the [unified operator and backend ownership contract](../../specs/analysis/python-analysis-design.md#unified-operator-and-execution-ownership). Backend-specific preparation does not change operator semantics.


Date: 2026-09-01

Revised: 2026-09-08

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
- how source execution and semantic contracts work without reuse verdicts;
- which storage receipts can back a materialized Dataset;
- when quality, Evidence, Findings, Artifact identity, and Run success become
  authoritative;
- how write-once execution bindings and concurrent calls behave;
- which state is recoverable after process loss;
- how action-scoped engine resources are cleaned up;
- why Arrow batches, private DataFrames, Runtime staging, and numerical buffers
  remain private exchange resources rather than Materialized Datasets;
- how a committed Artifact becomes one immutable scan leaf after cold recovery;
- why no partial Artifact, Evidence authority, pandas value, or preview survives
  a failed action.

This document is the Module 4 authority named by
[`2026-09-01-lazy-analysis-design-decomposition-plan.md`](2026-09-01-lazy-analysis-design-decomposition-plan.md).
It consumes the Dataset value contract in
[`2026-09-01-lazy-analysis-dataset-core-design.md`](2026-09-01-lazy-analysis-dataset-core-design.md),
the observation and operator input requirements in
[`2026-09-01-lazy-analysis-observation-model-design.md`](2026-09-01-lazy-analysis-observation-model-design.md),
and the execution handoff in
[`2026-09-01-lazy-analysis-planner-and-pushdown-design.md`](2026-09-01-lazy-analysis-planner-and-pushdown-design.md).

The Observation Model and compiler designs are accepted. This design consumes
their filter, exact invocation, source-pushdown and bounded pandas continuation
contracts. The 2026-09-07 amendment composes the maximal supported Ibis source
prefix and executes the admitted terminal suffix in pandas. The owner-approved
2026-09-11 amendment also permits native Parquet queries in a transient DuckDB
execution domain or a compatible bound source domain. This fully removes the
former prohibition on importing retained local/object data into native analysis.
It introduces no database Artifact storage, hidden durable stages, execution
fallback or target retry. Native scan integrity and exact owned-resource cleanup remain mandatory.

The owner-approved 2026-09-08 delivery allocation in the
[public-cutover plan](2026-09-01-lazy-analysis-public-cutover-plan.md) assigns the
first registered multi-input local consumer and its combined-input validation
acceptance to Slice 5a. Slice 4 proves independent source-domain
identity and existing unary/retained-part execution. The final multi-input
contracts below remain mandatory for Slice 5a; this allocation does not weaken
complete-input validation or permit invocation before all combined guards pass.

### Observation amendment dependencies

The 2026-09-05 Observation Model amendment supplies one Population family for
explicit Entity roots and Event/Lifecycle subject selections. Runtime retains
producer-owned selection time/completeness separately from a consuming Metric's
observation scope; materialization never relabels one as the other.

Basic ratio, mean, and weighted-mean contracts require row-keyed sufficient-state
parts. Primary rows and required parts validate and commit atomically. Filtering,
projection, ranking, and limiting preserve the exact selected-state dependency
closure. The runtime stores family-owned proofs and receipts; it does not invent
folds or reinterpret original Population lineage as a computational denominator.

Cold aggregation and time-removing rollup read only the exact retained rows and
parts required by their registered fold. Missing/corrupt required parts fail
publication or consumption at the owning phase, without origin replay. Selected
or incomplete periods retain their coverage facts after scalarization. Identity
projection admission follows Module 2's proven-unique Entity contract even when
functionally dependent coordinates remain in the primary schema.

The accepted 2026-09-07 semantic/statistical amendment additionally requires
identity-plus-version validation before temporal selection of an identity-bearing
source and identity validation after it. An unkeyed computation-only source
supplies no identity/unique-side proof and never becomes an empty-key singleton.
Population identity is the semantic `primary_key`; version
coordinates remain in source-selection provenance. Runtime executes the owning
checks without inferring current membership or reselecting a version on a
binding hit. Exact temporal anchors and boundary rules participate in logical
definition identity; realized selection facts accompany the committed result.

Per-axis folds require owner-proven contribution partitions, spatial/temporal
order and complete retained state. Attribution keeps its comparison scope,
forecasting uses model-specific uncertainty contracts, and Lifecycle reducers
consume the canonical history parts defined by Module 6. Runtime neither
invents missing statistical assumptions nor repairs lost information by source
replay. These requirements change design targets only until public cutover.

Finding extraction and cold reads use the
[Session Runtime Read design](2026-08-30-session-runtime-read-surface-and-graph-design.md)'s
closed coordinate contract, including registered comparison scopes and the owning
Attribution layout's null/mask interpretation. Publication must preserve enough
coordinates and payload discriminators to distinguish every eligible Finding;
it cannot drop a scope, stringify a null/Other cell, or disclose Entity identity
to satisfy the read schema.

## Ownership Boundary

This document owns:

- synchronous execution of `LogicalDataset.execute()` and immutable backing
  reads by `MaterializedDataset.show()` and `to_pandas()`;
- the exact point at which an execution-binding miss admits an incomplete Run;
- execution Run kinds;
- runtime resolution of semantic contracts, datasource execution configuration,
  and materialized storage integrity;
- factual source lineage and Agent-selected Artifact reuse;
- one configured storage target and its exact writer validation;
- local, engine, and object Dataset storage receipts;
- Dataset Artifact metadata, identity, atomic publication, and the write-once
  Session execution binding;
- Session-scoped writer exclusion and independent cross-Session execution;
- execution of source-to-pandas Arrow boundaries, private DataFrame
  continuations, and Runtime-owned staging;
- journaling of recoverable external resources and action-local cleanup of
  pandas/numerical buffers and private exchanges;
- primary and retained-part storage, validation, quality, Evidence, Finding,
  Artifact, and Run commit
  ordering;
- Session writer locks and cold reconciliation of incomplete Runs;
- cleanup of compiler-declared action-scoped temporary resources and unpublished
  storage;
- immutable scan-leaf decoding and storage-admission validation;
- execution of the concrete input checks owned by operators and compiled nodes;
- explicit Artifact, Evidence, and storage integrity inspection without
  semantic comparison, source freshness, or reuse certification;
- multi-downstream reuse through an explicit materialized Dataset.

It does not own:

- public Dataset family, shape, schema, row meaning, actions, or state fields;
- Population inference, predicate semantics, filter effects, or aggregate
  coordinate algebra;
- private semantic nodes, bound Ibis expressions, implementation registrations, boundary
  capabilities, or stage formation;
- operator-specific statistical calculations, quality rules, Evidence
  extractors, or Finding value schemas;
- public multi-sink scheduling, background tasks, cancellation handles, storage
  parameters, or retention parameters;
- PopulationDataset identity, Event matching, or Lifecycle replay semantics;
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
2. Each Dataset handle has one execution Session; an Artifact retains its original
   producing Session even when explicitly read in another Session.
3. A logical Dataset has no durable public locator, but its exact definition
   can resolve a private same-Session execution binding.
4. Every analytical family has paired Logical and Materialized Dataset classes
   with the same typed shape id, row contract, row-set contract, public schema,
   and definition fingerprint.
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
4. the graph names exact output schema, key, validation, transfer, placement,
   and temporary-resource requirements; execution details produce no source
   snapshot certificate for later reuse;
5. eligible contiguous operators compose into the maximal supported Ibis source
   prefix; an exact registered pandas continuation handles an ineligible terminal
   suffix, and every dependent successor stays local;
6. action-scoped temporary relations are private, non-durable, and never
   Artifacts;
7. no private stage may create a hidden durable intermediate Dataset;
8. materialized inputs are non-rewriteable scan leaves;
9. compilation and execution failures retain distinct phases;
10. compiler diagnostics are optional, bounded, and execution-local;
11. bound expressions and lowerer records remain compiler-owned. No compiler
    fingerprint/count inventory is a persisted Run field or publication gate;
12. source support is decided before data work from tested method and adapter
    registrations, never from failed compilation, observed result sizes, cost
    search, or backend-failure fallback;
13. local and object Parquet leaves use authorized PyArrow readers or their
    registered native scan. Native placement is fixed before execution and may
    use a transient DuckDB domain or an eligible bound datasource domain;
14. a multi-input pandas continuation may gather independently produced inputs
    only when its exact contract admits their identities, alignment and privacy
    projection. There is no generic federation or local substitute for source-only
    semantic work.

Runtime validates complete typed inputs and owns publication. It executes
the compiler's fixed source prefixes and local suffix without inventing a new
boundary or implementation, and never promotes private staging into durable
Dataset authority.

## Decision Summary

### Keep one Run lifecycle for every producing execution

Every producer execution uses the same persisted lifecycle:

```text
absent -> incomplete -> succeeded | failed
```

The lifecycle never moves backward. A failed producer retry is a new Run.
Recovering an existing Artifact through an execution binding is a read and
creates no Run.

Only `logical_dataset.execute()` creates a Run. Because there is one legal
producer action, the Run does not persist a one-value `action_kind` field.

An incomplete Run exists before live authority resolution, backend compilation,
datasource data work, transfer, temporary-resource creation, or storage writes.
Only deterministic in-process validation that cannot consult live external
state may precede admission.

`MaterializedDataset.show()` and `to_pandas()` read the committed Artifact and
create no analysis Run, graph node, receipt, or other durable Runtime state.

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

### Commit all result metadata in one Store transaction

The Session Store owns Session facts, Run history, Artifact descriptors,
Evidence envelopes, Findings, and outstanding external-resource obligations in
one SQLite database. Evidence modules own calculation and typed reading, but
participate in the caller-owned Store transaction; they own no independent
connection commit or database file.

Publication order is:

```text
final immutable storage + validated receipt
    -> one Session Store transaction:
       Artifact + Evidence + Findings + Run success
       + removal of resolved output resource obligations
```

The database commit is the only publication decision. Before it, output storage
is unpublished and remains a cleanup obligation. After it, the Artifact and its
succeeded Run are visible together. There is no separately committed Artifact
metadata file, Evidence commit marker, or marker-to-Store repair gap. Large
immutable data stays outside SQLite under its storage receipt.

A lost commit acknowledgement is resolved by reading the Store, never by
assuming rollback, deleting output, or repeating datasource work.

### Make every committed Dataset Artifact Evidence-complete

Every materialized Dataset publishes one commit-time Evidence envelope. An
operator with no domain Findings publishes a valid zero-Finding envelope; it
does not use `unavailable` as a substitute for a failed extractor.

Quality, Evidence, or Finding construction failure fails the execute action
before the publication transaction commits. The first lazy cutover therefore has no committed
Dataset Artifact with partial Evidence authority.

This is stricter than preserving a successful Artifact with a partial or
unavailable Evidence state. A later relaxed policy requires an explicit design
amendment and a new Evidence-envelope variant.

### Bind each exact logical execution once per Session

A logical definition fingerprint alone is not an Artifact identity, but the
runtime derives `DatasetExecutionKeyV1` from the complete canonical definition
fingerprint and common materialization protocol version before live datasource
work. Dataset Core owns dependency normalization once; Runtime adds no duplicate
input, schema, semantic, sampling, or extractor-version vector.
It deliberately excludes current datasource row state, realized sample rows,
storage placement, and process or script identity.

The Session Store permits at most one Artifact row for
`(session_ref, DatasetExecutionKeyV1)`. A key miss elects one producer and
admits one execution Run; successful publication inserts that Artifact row. A
key hit validates and recovers the Artifact without a new Run, datasource
access, quality extraction, storage copy, or Evidence publication. “Execution
binding” is only shorthand for this unique Artifact key, not another value or
relation.

This is Session snapshot semantics, not a global cache-equivalence claim. Once
bound, later datasource changes do not redirect the key. A caller that requires
fresh rows creates a new named Session or changes an explicit definition input
that participates in the key. Source version support and sampling do not affect this lookup. Marivo makes
no freshness, source-equivalence, or reuse-suitability judgment. Another Session
uses an old result only through an explicit Artifact reference, never through an
automatic definition/time-based search.

### Serialize writers within each Session

One Agent writes each Session serially. The first cutover uses one reliable,
non-blocking exclusive lock per `session_ref`. Separate scripts and processes
may use the same Session sequentially; overlapping top-level writes to that
Session fail with a structured Session-busy error before Run admission.
Different Sessions may execute concurrently while sharing short SQLite write
transactions in the project-level Store.

An execute action holds its Session lock from preflight recovery and binding
lookup through execution, terminal publication or safe failure handling. No
producer election, claim row, owner lease, heartbeat, contender queue, timed
waiting, or automatic contender retry is needed. The Artifact unique execution
key remains the same-Session reuse authority.

Acquiring the lock proves exclusive local writing for that Session, not
termination of an old remote query. Each writer first reconciles incomplete
Runs and outstanding obligations in its own Session. Unresolved work blocks
only that Session. Read-only history and committed Artifact reads do not take
the writer lock.

### Separate factual validation from reuse judgment

Same-Session execution-key hits and explicitly selected cross-Session Artifacts
recover exact committed results after mechanical integrity/access checks. Result
age, changed source rows, and business relevance never authorize or veto a read.

`session.revalidate(artifact_or_ref)` reports Artifact, storage, and Evidence
integrity independently through explicit full inspection. Ordinary operations check only the dependencies they consume; no
path compares the Artifact against the current semantic catalog. There is no datasource
freshness axis, source-equivalence verdict, reusable flag, or overall approval.

Marivo preserves identity, schema, lineage, quality, and producer/commit times.
The Agent decides whether a result fits a new question. A downstream operator
validates its concrete input contract, not a general reuse certificate.

## Runtime Schema Generation

The lazy Dataset cutover introduces one clean persistence generation. It does
not decode eager Frame Artifacts as Datasets and does not migrate old Runs.
The exact versioned value contracts are:

```text
marivo.analysis_action_run/v2
marivo.dataset_artifact/v1
marivo.dataset_artifact_descriptor/v1
marivo.dataset_storage_receipt/v1
marivo.dataset_evidence/v1
```

The replacement Session Store uses exact `PRAGMA user_version = 5`. Its schema,
constraints, indexes, and version are created in one short SQLite schema
transaction before Session resolution; initialization never admits analysis.
Slice 1c does not migrate any v4 schema or its resource obligations. Existing older
or future Store generations fail closed; no compatibility decoder,
in-place upgrade, dual read, or import exists.

### Generation-scoped layout

```text
.marivo/analysis/generations/v5/session_store.db
.marivo/analysis/generations/v5/sessions/<session_ref>/session.lock
.marivo/analysis/generations/v5/sessions/<session_ref>/artifacts/<artifact_ref>/...
.marivo/analysis/generations/v5/sessions/<session_ref>/runs/<run_ref>/...
```

One project-level Store contains all Sessions and all durable analytical
metadata. Artifact directories hold only admitted local immutable data and its
file manifests; engine/object receipts retain their immutable locators in the
Store. Run directories hold private staging and recoverable external resources.
There is no Session sidecar, Artifact metadata sidecar, separate Evidence
database, durable lock-owner record, or process-state file. SQLite-managed WAL
and shared-memory files are database internals, not application authority.

Each Session lock file has a stable identity for the lifetime of that Session.
Its presence is not ownership; the held OS lock is. It is never unlinked or replaced during
normal use. All entrypoints resolve the same canonical Store, Session ref, and
Session lock path.

The lazy runtime never opens `.marivo/analysis/session_store.db` as v5
authority. `get_or_create(name)` operates only in the generation-scoped Store
and may create a Session even when an eager v2 Store exists. `resume(...)`,
`current()`, and Artifact reads resolve only v5 identities. Old identities fail
with a structured generation error; no old rows, names, or payloads are copied.

### Exact Store relations

Every payload below is bounded canonical UTF-8 JSON with one named closed
schema, not an open map. The Store owns exactly these relations:

```text
sessions
  session_ref primary key
  name unique, non-empty
  question nullable
  report_timezone_name non-empty
  report_timezone_resolution = iana | fixed_offset
  created_at
  updated_at

runtime_state
  singleton_key primary key, constrained to 1
  current_session_ref nullable -> sessions.session_ref ON DELETE SET NULL

analysis_action_runs
  run_ref primary key
  session_ref -> sessions.session_ref ON DELETE RESTRICT
  execution_key_digest non-empty
  admitted_at
  dataset_input_payload = RunDatasetInputV1
  unique (session_ref, run_ref)

analysis_action_run_terminals
  run_ref primary key
  session_ref
  outcome = succeeded | failed
  terminal_at
  output_artifact_ref nullable
  failure_payload nullable = RunFailureV2
  (session_ref, run_ref) -> analysis_action_runs ON DELETE RESTRICT
  (session_ref, output_artifact_ref) -> dataset_artifacts ON DELETE RESTRICT
  unique (session_ref, output_artifact_ref)
  succeeded requires output_artifact_ref
  succeeded forbids failure_payload
  failed requires failure_payload
  failed forbids output_artifact_ref

analysis_action_run_inputs
  run_ref
  session_ref
  input_ordinal non-negative
  artifact_ref
  primary key (run_ref, input_ordinal)
  (session_ref, run_ref) -> analysis_action_runs ON DELETE RESTRICT
  artifact_ref -> dataset_artifacts.artifact_ref ON DELETE RESTRICT

dataset_artifacts
  artifact_ref primary key
  session_ref -> sessions.session_ref ON DELETE RESTRICT
  execution_key_digest non-empty
  descriptor_payload = DatasetArtifactDescriptorV1
  committed_at
  unique (session_ref, artifact_ref)
  unique (session_ref, execution_key_digest)

dataset_evidence
  artifact_ref primary key -> dataset_artifacts.artifact_ref ON DELETE RESTRICT
  evidence_digest non-empty
  finding_count non-negative
  finding_set_digest non-empty
  extractor_contract_versions_payload = ordered registered versions[]

findings
  finding_ref primary key
  artifact_ref -> dataset_evidence.artifact_ref ON DELETE RESTRICT
  finding_ordinal non-negative
  finding_identity_digest non-empty
  finding_body_payload = one exact registered Finding body variant
  unique (artifact_ref, finding_ordinal)
  unique (artifact_ref, finding_identity_digest)

action_resource_journal
  run_ref
  resource_kind registered
  execution_domain_id registered
  ownership_nonce non-empty
  cleanup_capability_id registered
  safe_locator non-empty, secret-safe
  primary key (run_ref, resource_kind, execution_domain_id, safe_locator)
  run_ref -> analysis_action_runs.run_ref ON DELETE RESTRICT
```

All tables are SQLite `STRICT` tables with foreign-key enforcement enabled on
every connection. Refs, registered ids, digests, locators, enums, timestamps,
and payloads are `TEXT`; counts and ordinals are `INTEGER`. Timestamps use
canonical UTC RFC 3339 encoding. Fields are `NOT NULL` unless marked nullable;
each stated constant, enum, non-empty value, and non-negative count has a
`CHECK`. Raw parameterized-source values never persist.

Absence of a terminal row is the only persisted `incomplete` Run state.
Admission, inputs, terminal rows, Artifacts, Evidence, and Findings are immutable
after insertion. Failed retries may share an execution key; the Artifact unique
key alone enforces one committed result. The unique non-null terminal output
owns the Run-to-Artifact edge. Input refs may belong to other Sessions in this
Store; their original owner derives from the Artifact row. Output publication
validates that its producer admission has the same Session and execution key;
each committed Artifact must have one succeeded producer and one complete
Evidence envelope.

### Artifact descriptor and derived values

`DatasetArtifactDescriptorV1` is the persisted closed value:

```text
  schema = "marivo.dataset_artifact_descriptor/v1"
  definition_fingerprint
  row_contract
  row_contract_fingerprint
  row_set_contract
  row_set_contract_fingerprint
  realized_schema
  realized_schema_fingerprint
  bounded_lineage
  semantic_dependency_digest
  population_authority
  sampling_execution
  operator_implementation_versions[]
  dataset_materialization_contract
  storage_receipt
  retained_parts[]
  quality_summary
  typed_issues[]
  comparison_basis
  comparison_inputs[]
  delta_evidence
```

Family payloads stay closed and versioned. Contract fingerprints are computed
from their named canonical values and verified on decode, never independently
authored semantic facts. Artifact identity, Session, execution key, and commit
time come from relation columns. `producing_run_ref` comes from the terminal
edge. Public content authority and realized row/byte counts derive from the
primary receipt; private parts retain their own receipts and counts; `DatasetArtifactV1` exposes them as derived projections. The full
Artifact envelope is assembled from these owners and is not stored a second
time. There is no `metadata_locator` or cross-Store metadata digest.

The Evidence envelope derives quality-summary and typed-issue digests from the
Artifact descriptor. Its stored `evidence_digest` binds those digests, Finding
count, ordered Finding-set digest, and extractor contract versions; readers
verify the digest against the assembled envelope. It excludes `artifact_ref`
and itself and is not unique across Artifacts. There is no separately stored
full envelope or duplicate quality payload.

The private Slice 5a descriptor extends that closed pre-cutover shape with a
Metric/Population comparison-basis snapshot and Delta's ordered current/baseline
input authority. The basis binds exact membership selection, sampling intent,
observation reference axis/window and non-time selection digests. It is loaded
with the Artifact and used without Store access during Dataset construction;
neither row-semantics fingerprints nor bounded display lineage owns these facts.
Each comparison operand retains its own definition, selected Artifact refs,
Population authority and realized sampling receipts. Repeated operand refs keep
their distinct Run-input ordinals even when physical reads are shared.

Delta Evidence stores its closed family projection with the descriptor and
binds it into the existing Evidence digest. It includes presence/calculation/
relative-status counts, matched/unpaired counts, promotion and approximation
facts, and eligible/emitted/truncated Finding counts. The extractor streams the
complete staged primary result and retains only its bounded top 1000 eligible
Findings. It reads no unused component parts and never collects raw Entity
coordinates into Evidence. The existing Artifact/Evidence/Finding/terminal
transaction remains the only publication boundary. No legacy descriptor
decoder, migration or public generation switch is introduced.

Slice 5b extends the same private path with Delta's exact side-specific
component parts and Attribution's closed reconciliation Evidence. Each Delta
part is keyed by the current Delta rows and binds its side's fold/partition
contract and structural presence. Row selections select matching parts; they
cannot restore original contributions. Observation windows and sampling
parameters remain input/Artifact authority rather than Delta row semantics.
The interpretation-relevant exact/sampled class is retained in row meaning.

Attribution computes independent endpoints through the admitted fold/finalize
closure over complete selected pre-mapping state. It checks mapped component
totals and the resulting contributions against those endpoints; summing the
new contributions is never how it obtains the overall Delta. Logical axis
expansion additionally reproduces the original selected Delta endpoints.
The retained path needs neither an origin graph nor endpoint snapshots for
every possible future subset of axes.

Attribution's complete reconciliation proof is bounded descriptor metadata,
not another physical part or publication mechanism. Result-only `where`,
`rank`, and `limit` preserve that original proof and lineage without claiming
their selected rows form a complete decomposition, and produce zero new
Findings. Entity-scoped reconciliation and identity-bearing mapping remain
source work; only global bounded validation aggregates and digests reach
publication. No per-Entity hashes or raw identities enter local validation.

The private Slice 4d descriptor codec closes each typed issue over `kind`,
`severity` (`warning` or `blocking`), `expected`, `received`, and `repair`.
Severity is required to derive exact bounded issue counts. This tightens the
pre-cutover private v1 shape: synthetic nonempty issue lists written with the
4c codec and no severity are rejected, with no legacy decoder or migration.
The 4c production descriptor constructor emitted only the default empty issue
tuple; this is a producer fact independent of its zero-Finding registration.
Existing production empty-issue descriptors retain their shape. Public cutover
remains Slice 8; future producers must persist the exact current issue contract.

Finding bodies exclude relation-owned refs, Session identity, and commit time.
Those facts derive from the owning Artifact. Ordinals are the contiguous
zero-based projection of canonical Finding identity order. Summary reads use
the descriptor and envelope without scanning Finding bodies; exact Finding
validation reads the bounded or complete owning records required by its API.

### Mutation, indexes, and transaction ownership

Only `sessions.question`, `sessions.updated_at`, and the current Session pointer
are updated in place. Session recency changes only on explicit activation or
question update. Timezone warning text is derived from its resolution enum.
Resource rows are inserted and deleted as outstanding obligations; they carry
no mirrored resource lifecycle or recovery-progress state.

Indexes cover Session recency, Run admission recency within Session, terminal
outcome/time, Run lookup by input Artifact, and Artifact commit recency within
Session. Finding reads reuse the unique `(artifact_ref, finding_ordinal)` index;
Run-output and execution-key lookups reuse their unique indexes. Resource scans
reuse the primary-key `run_ref` prefix. Session-scoped incomplete lookup uses the
admission/terminal anti-join. No second graph, binding, claim, or owner index is
stored.

All mutations of an existing Session, activation/recovery, and maintenance use
its Session writer guard. Schema initialization and Session-name uniqueness
use short SQLite transactions as described under Session Writer Guard. The
guard's in-memory operation context can be passed to internal helpers; it is not a persisted or public handle. The lock covers whole actions,
while database transactions cover only these short state changes:

1. Session creation/update and current-pointer change form one transaction.
2. After recovery and binding lookup under the guard, Run admission and ordered
   input rows form one transaction. A binding hit inserts nothing.
3. External-resource reservation is committed before creation/submission.
   Deletion follows exact cleanup, terminal proof, or output authority transfer.
4. Publication inserts Artifact, Evidence, Findings, and the succeeded terminal
   row and deletes resolved output obligations in one transaction. It validates
   the complete same-Session bundle and producer execution key before commit.
5. Proven pre-publication failure inserts its failed terminal and deletes
   resolved obligations in one transaction after external execution is terminal.
   Terminal Runs may retain harmless garbage obligations; later guarded
   maintenance retries them without changing history or blocking new work.

No Evidence helper commits independently. No external call or file write occurs
inside these transactions. Uniqueness, foreign keys, closed-payload validation,
and transaction-wide invariant checks apply before public visibility. A read
transaction sees either the entire published bundle or none of it.

Use SQLite WAL and `synchronous=FULL` for durable metadata commits on the
supported local filesystem; every connection enables foreign keys. A lock or
filesystem without the required reliable local locking/durability primitives
fails closed rather than enabling a lease-based fallback. Immutable local bytes,
file manifests, and the final rename must satisfy the platform's file and
parent-directory durability protocol before metadata commit. External receipts
must prove their registered finalized-storage guarantee. This ordering protects
against process interruption and, on supported storage, host restart; arbitrary
media loss or external deletion is detected as loss of storage integrity.

The first cutover does not retain public `session.delete`. Even with one
metadata transaction, safe removal of external immutable data requires a
separately accepted recoverable deletion protocol. Removal of an entire
preserved generation remains an out-of-band user-owned operation, never an
automatic repair or a writer-lock bypass.

The Public Cutover Plan owns removal of old implementations. No old-schema
decoder, Frame-to-Dataset adapter, Run backfill, Artifact import, Evidence
migration, dual-read graph, or eager/lazy alias survives.

## Action Run Contract

### Common envelope

Every persisted Run has one immutable common admission envelope:

```text
AnalysisActionRunEnvelopeV2
  schema = "marivo.analysis_action_run/v2"
  run_ref
  session_ref
  admitted_at
  dataset_input: RunDatasetInputV1
  input_artifact_refs[]
```

`RunDatasetInputV1` contains only:

```text
  definition_fingerprint
  shape_id
  row_contract_fingerprint
  row_set_contract_fingerprint
  bounded_operator_ids[]
  bounded_semantic_dependency_refs[]
```

Ordered Materialized input refs have one owner: the normalized
`analysis_action_run_inputs` relation. Its `session_ref` identifies the consuming
Run; the referenced Artifact retains its producing Session. The global Artifact
key resolves that owner without a duplicated source-Session column. Cross-Store
references and implicit imports are not admitted. `input_artifact_refs[]` is its Run-read
projection and is not copied into `RunDatasetInputV1`. Repeated Artifact refs
are legal operand occurrences and retain distinct ordinals; the Session graph
may deduplicate them only when projecting dependency edges.

`shape_id` is a bounded audit projection derived from the row contract named by
`row_contract_fingerprint`; it is never authored or decoded as a second shape
authority.

It cannot reconstruct a logical Dataset. Raw predicates, SQL, credentials,
identity values, Ibis expressions, backend objects, Python callables, and
unbounded plan payloads are forbidden. `execute()` has no arguments, so the Run
contract has no generic safe-argument payload or omitted-argument bookkeeping.

### Discriminated lifecycle projection

Run reads project one exact variant from the immutable admission row and its
optional terminal row. `incomplete` is derived from terminal-row absence; it is
not a mutable state field:

```text
IncompleteActionRunV2
  envelope
  lifecycle = incomplete

SucceededExecuteRunV2
  envelope
  lifecycle = succeeded
  finished_at
  output_artifact_ref

FailedActionRunV2
  envelope
  lifecycle = failed
  failed_at
  failure: RunFailureV2
```

Terminal-only fields cannot appear on an incomplete Run. Output Artifact fields
cannot appear on failed Runs. An execute success must name exactly one
same-Session committed Artifact. Binding recovery has no succeeded-Run variant
because it is not a new execution attempt.

Failed Runs persist only their structured failure. Successful Runs retain their
output ref and terminal time. Compiler diagnostics are optional execution-local
output, not a mandatory or partial persisted audit object.

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
retry_disposition = retryable | not_retryable
```

`recovery_pending` is an action error while the Run remains incomplete; it is
not a persisted failed-Run disposition. `commit_decision` is also absent: the
failed terminal variant already proves that no output publication committed.

The phase vocabulary preserves compiler distinctions and adds runtime phases:

```text
authority_resolution
semantic_validation
graph_validation
implementation_registration
execution_boundary
source_binding
ibis_expression_construction
storage_selection
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

A Run with a committed Artifact is already succeeded and never represented by
`FailedActionRunV2`. If commit acknowledgement is uncertain, authoritative
Store readback determines its existing outcome before any cleanup or retry.
An unreadable Store returns a recovery-pending action error; it does not create
a second persisted lifecycle.

## Admission and Execution Lifecycle

### Work allowed before Run admission

Pure in-process checks may validate Dataset ownership, graph closure, paired
state, exact contract versions, shape, zero-argument action signature, and the
execution key without taking a writer lock or doing live source work.

`execute()` then acquires the Session writer guard, reconciles this Session's
prior obligations, and looks up the exact execution key under that same guard. A
metadata-valid binding returns its Materialized Dataset without admitting a Run.
Otherwise admission and ordered input rows commit before any new computation.
There is no unlocked miss followed by unguarded producer admission.

Three distinct paths apply before a new Run exists:

- pure definition checks cannot open connections, resolve live credentials,
  compile against a backend, query sources, or create resources;
- recovery may contact an already recorded external execution or owned resource
  to protect publication and clean up; remote read termination proof is not required;
- an existing Artifact read may resolve storage credentials and validate its
  immutable backing, authorization, and content. It may query that backing, but
  never read current origin data or replay the origin plan.

A new computation's live semantic/source resolution, credential resolution,
compilation, datasource statements, transfer, and resource creation require a
durably admitted incomplete Run. Read-side storage validation is not a producing
execution and creates no Run or read-audit record.

### Work after incomplete Run admission

While continuing to hold the Session writer guard, the runtime:

1. resolves current semantic requirements and credentials without storing secrets;
2. resolves datasource execution profiles without source-version certification;
3. validates materialized input leaves;
4. supplies fixed input bindings and one configured storage
   target; the compiler selects source prefixes and the registered pandas suffix
   from declared support, the writer validates the target, and all source
   expressions compile using schema-declared references before data statements;
   Runtime later creates and binds any required source-domain temporary relations;
5. executes steps, reserving durable external obligations before side effects;
6. validates transfer guards, schema, keys, counts, bounds, and family checks;
7. completes quality, Evidence, Findings, and primary/part storage; proves
   publication ownership safe and attempts driver close and harmless cleanup;
8. commits the entire publication bundle or records a proven failed outcome.

Exceptions do not prove database rollback or backend termination. Uncertain
commit acknowledgement follows Store readback; surviving publishers and object
writes require reconciliation. Unknown read-only server work does not block a
new action after local publication safety is established.

Source validation relations compile into separate queries, one check per query.
Even small unions of complex checks can retain unnecessary aggregate state. All queries are planned before
execution; a failed query is never split and retried. Every check produces its own named
zero-violation receipt; missing, duplicate, non-integer or nonzero scalar results
fail at the owning check. Successful results retain their original check order.
Native SQL failures abort the action without retrying individual checks.
Sampling fences preserve query order: pre-sampling checks finish before the fence
is reserved or created, and dependent checks execute only after its realization.
Execution-local `validation_queries` counts physical batch queries, while the
Artifact's validation results retain the individual logical checks. Statement
statistics record each batch's shared SQL once as `validation_batch`.

### Materialized read lifecycle

`MaterializedDataset.show()` validates selected metadata and accessed primary
storage, schema, and deterministic ordering while reading a bounded preview.
When the current schema includes duration columns, the preview names those
columns and explicitly labels their numeric values as microseconds, including
fractional microseconds in Lifecycle dwell statistics. This disclosure does not
collect the complete result or change the stored values.
It does not scan Findings, unused parts, or all data to recompute row counts or
hashes. `to_pandas()` reads and validates complete primary data under collection
guards, returning an isolated DataFrame or no partial value. Parts and Findings
are checked only by consumers that require them or explicit full inspection.

These reads, exact `session.artifact(ref)` recovery, history, Graph, and explicit
revalidation take no writer lock and create no persisted state. Metadata is
read from one Store snapshot. External storage validation happens outside that
SQLite snapshot using the selected immutable receipt; it never mutates metadata
or rereads the origin dataset. The DataFrame cannot re-enter typed analysis;
the Materialized Dataset remains a reusable scan leaf.

### Execution lifecycle

```text
pure validation + derive DatasetExecutionKeyV1
  -> acquire Session writer guard
  -> reconcile old incomplete Runs and resource obligations in this Session
  -> lookup binding; on metadata-valid hit return the existing Materialized Dataset
  -> on miss admit incomplete Run with ordered inputs
  -> resolve authority, compile, execute, and validate
  -> compute quality, Evidence, and Findings
  -> finalize primary/retained storage and validate all required receipts
  -> prove execution terminal and garbage harmless; attempt cleanup
  -> one Store transaction publishes Artifact + Evidence + Findings + Run success
     and removes resolved output obligations
  -> construct the immutable scan leaf and return the Materialized Dataset
  -> release the guard on every exit
```

The lock is never a long SQLite transaction. The Logical Dataset remains
unchanged, and the materialized leaf cannot reach through to its origin plan.

## Source Execution and Reuse Responsibility

### Factual provenance only

The runtime records the definition, explicit input refs, bounded source identities
in lineage, producer Run, admission/finish times, and commit time. Execution times
are not event-time coverage, update watermarks, or freshness guarantees. Source
identities come from the bound definition; there is no generic version capture.

There are no source-authority variants, replay-comparability classes, source-state
digests, freshness checks, or automatic cross-Session equivalence matching. A
source without version metadata needs no special execution-authority record.
Changed source rows cannot invalidate an Artifact or redirect its Session binding.

### Executor-local correctness

After Run admission, adapters execute the expression using their normal statement
and transaction semantics. The compiler still preserves required single
evaluation, sampling, keys, types, and arithmetic. Required transactions or
single-evaluation fences remain execution details rather than persisted source
certificates. Do not query source freshness, capture a version token, or reject
an otherwise supported operation merely to certify future reuse.

The Session lock makes no guarantee about concurrent source-system writes or a
common snapshot across independent statements. Nearby execution timestamps
imply no cross-source consistency. Concrete operator algorithms and input
contracts remain mandatory; no generic source-certification layer is needed.

### Explicit Artifact use across Sessions

Artifact refs are unique in one project Store and retain an immutable producing
Session. `target_session.artifact(ref)` reads any exact committed Artifact there,
including another Session's result. It returns a Materialized Dataset handle
using the target Session as execution context. `state.artifact_session_ref` and
producer metadata retain the original owner; the original handle is unchanged.

This read creates no Run, Artifact, binding, copy, owner change, or reuse approval.
It validates selected metadata and supported decoding without opening payloads.
The consuming operation checks the storage/parts it accesses. Known corruption
in those dependencies fails without origin-source checks or silent recomputation.

The receiver or source constructor determines the consuming Session. Explicit
Materialized operands from other Sessions in the same Store are legal when they
satisfy the operator's structural input contract. Foreign Logical Datasets remain
invalid because they carry another Session's live execution context. To make an
old Artifact the receiver of new work in a chosen Session, use that Session's
`artifact(ref)` read. Never infer the destination from the global current pointer.

Only the consuming Session admits and locks the new Run and owns its output.
Input rows reference the original Artifact, and the execution key binds that
exact identity. No local alias or second registration is created. Reading an
immutable input never activates, locks, or reconciles its producing Session;
a busy/blocked producer Session does not block its already committed results.
Cross-project/Store imports and deletion of externally referenced data remain
outside this cutover.

Agent judgment owns freshness and suitability. Explicit selection waives no
concrete type, identity, unit, row-contract, or alignment requirement of the
requested operator, but needs no general Artifact-reuse certificate.

### Parameterized source values are definition-bound

Parameterized non-secret JSON source values are definition inputs, not
action-time Session state or Artifact-reuse certificates. The Observation owner validates and captures
them into the immutable logical source definition under
`BoundSourceParametersV1`. Before Run admission, this runtime consumes the
definition's exact canonical value digest while the compiler receives the
process-local typed values needed to bind the source adapter.

The runtime never asks `Session.source_bindings(...)`, a `ContextVar`, or the
process environment for a replacement value during `execute()`. Missing
captured values are a corrupt/incomplete logical definition and fail before
Run admission. A caller cannot override them through `execute(...)`.

The exact captured value digest enters the canonical definition fingerprint
once; the execution key inherits it through that fingerprint. Therefore:

- equal reconstructed definitions with equal bindings may recover one existing
  same-Session Artifact before datasource work;
- changing one bound value creates a different execution key;
- a binding hit never needs the raw value to resend a request;
- a binding miss uses only the values captured by that exact definition.

Raw binding values are process-local execution inputs. They do not appear in a
Run envelope, Artifact metadata, Evidence, Finding, graph edge, card, contract,
error, telemetry record, or persisted Dataset
definition. User-visible and audit surfaces may contain only exact
Entity/parameter identities and a bounded redacted projection. The internal
definition and Artifact execution-key row may contain the opaque digest but
never the values. Private execution-local statement statistics retain the original
SQL, including bound literals, without redaction or SQL parsing. These statistics
are not persisted or included in the public result surfaces above.
Credentials remain in the datasource credential contract and
are never accepted as source bindings.

### Semantic dependency authority

Logical current-source execution binds one canonical semantic dependency
digest over the exact Entity, Metric, Dimension, Relationship, Event,
Lifecycle, policy, and implementation contracts reachable by the admitted
Dataset graph.

Display labels, Help text, source locations, and non-semantic documentation do
not change this digest. Any field that can change rows, null behavior,
Population membership, coordinates, aggregation, or authority does.

Execution over retained Artifact rows preserves the committed dependency digest
in provenance but does not require the current catalog to reproduce it.

## Operator Input Validation

Operator contracts and the compiled nodes that implement them own the required
checks. There is no separate generic requirement record, mode enum, selected
branch certificate, or successful-check audit payload.

### Logical sources and explicit semantic enrichment

A Logical input retains its admitted upstream graph and semantic dependencies.
Source nodes resolve those exact definitions for compilation; a missing or
incompatible dependency fails before the affected data statement. The dependency
digest remains part of execution identity and the output Artifact descriptor.

When an operator explicitly admits current semantic enrichment, such as adding
Dimension axes to a retained journey, its contract names the required paths,
identity mappings, cardinality, and fields. Compilation adds those source/join
nodes beside the immutable input. It never reopens that input's origin graph
or silently reconstructs missing historical values. No blanket comparison with
the historical Artifact's semantic digest is a precondition for reuse.

### Materialized inputs

An Artifact scan validates exact identity and original ownership in the same
Store, selected metadata, and the accessed payload's storage identity/access,
schema, and row contracts. It does not scan unrelated parts or Findings. Each consuming
operator checks only the fields, sufficient statistics, types, identities, units,
coverage, and alignment that its own calculation requires.

A calculation over retained rows does not need the original catalog or source.
Catalog drift cannot redirect the scan or cause the original analysis to rerun.
Missing required fields fail with the owning operator's concrete repair; only an
explicitly admitted enrichment can add current semantic data.

### Fixed inputs, no fallback

Construction binds exact logical definitions or Artifact refs to operator inputs.
The compiler lowers those inputs into source, scan, join, and calculation nodes.
The same row algorithm may consume either kind of input without two separately
registered authority branches. Mixed inputs retain their separate authority.
Ibis composition requires one common source domain; an exact registered pandas
continuation may instead collect independently produced inputs under combined
guards when its semantic and privacy contracts permit that boundary. It never
imports them into another datasource or replays a materialized input's origin.

Once a dependency enters pandas, every dependent successor executes locally
through its registered implementation. If no valid local continuation exists,
compilation fails before data work. Source-only identity, Population and semantic
operations retain their owning source requirements rather than gaining a generic
local join path.

A failure never substitutes another input, switches from a scan to origin
execution, or changes the requested calculation. Every execution-relevant choice
remains in the normal definition, input tokens, and operator version; there is
no duplicated requirement summary in Dataset state or Run admission.

### Existing facts and structured failures

Successful Runs retain their output ref and terminal time. Artifact descriptors
own semantic dependency digests; normalized Run input rows own ordered Artifact
refs. Readers use those existing facts for provenance without a new authority
audit object, mode counts, compatibility-id inventory, or historical access class.
Current storage access is checked when needed; past success grants no future
permission. Failed checks use structured errors naming the expected contract,
received input, and concrete repair. They do not create a partial audit object.

## Private Exchange Staging

### Exchange authority is not Dataset authority

A source-to-pandas boundary uses exact Arrow inputs under Module 3's
`PhysicalExchangeV1` contract. The schema, producer identity and admitted privacy
projection are the only authority for those private rows. Same-domain eligible
Ibis relations compose without a transfer edge. The source prefix can contain
several analysis operators; one operator can also own source work followed by a
pandas calculation. Method and adapter registrations determine these boundaries
before data statements, with no compilation probing or failure fallback.

The first local consumer receives complete validated inputs through their
registered Arrow-to-pandas conversion. Local/object Artifact inputs use the
authorized PyArrow Parquet reader, while an engine Artifact can feed an Ibis
source prefix through its immutable scan. Within the local suffix, one step
passes its private DataFrame directly to the next after checking the producer's
output contract. Each consumer still checks its own required fields, parts and
calculation limits and must not mutate a shared upstream DataFrame. There is no
mandatory Arrow round trip, Parquet write, or temporary SQL relation between pandas operators. Numerical methods may use
NumPy/SciPy objects internally and return the registered DataFrame result.

Arrow remains the source/Artifact ingestion and storage-writer boundary; Module 3
also uses `PhysicalExchangeV1` for local output passed to storage. Registered
lossless conversion uses Arrow-backed pandas columns where supported, but the
contract promises neither universal Arrow execution nor zero-copy conversion.
Private DataFrames never become a public input path or weaken immutable Dataset
and cross-Session ownership rules.

An exchange is never a Dataset, Artifact, binding, receipt, Evidence source or
Session graph node. Only the root primary output and registered retained parts
proceed to publication. There is no compiler-selected Parquet transport mode.

### Complete pandas input and validated streaming

Runtime validates actual RecordBatches for ordered fields, types, nullability,
timezone, decimal and nested values. Adapters must bound fetching and individual
variable-width allocations before admitting a batch. Declaration-only schema
checks are insufficient; normalization is permitted only when lossless under
the owning row contract, with explicit overflow failure.

Every required source/Artifact input and retained part is collected completely
and validated before its first local consumer starts. All operands must pass
schema, key, ownership and alignment checks. Later local steps reuse private
DataFrames without recollecting the source or mutating shared inputs. Output
validation precedes publication; failures never change the selected recipe.

### Caller execution and resource responsibility

Local pandas and numerical methods, complete retained collection and explicit
inspection execute synchronously in the calling Python process. Runtime creates
no execution/read worker, IPC protocol, supervisor, RSS monitor or watchdog.
Original kernel/driver exceptions preserve their causes and tracebacks; cleanup
failure cannot replace the original exception. Safe journal summaries remain
separate from the exception delivered to the caller.

Marivo imposes no execution row/byte/cell/page, intermediate expansion, method
complexity, memory/RSS/spill, storage or deadline budgets. Resource decisions
belong to the caller and environment; database, driver, OS and runner limits
still apply. Batch sizes are tuning values, not total-result limits or peak-memory
guarantees. Required semantic checks and explicit analytical parameters remain.
A blocking native call has no promised immediate interruption. Where Python
retains control, close owned resources after errors or user interruption.
External termination is recovered through writer ownership and Store integrity;
no partial result becomes a successful Artifact.

### Runtime staging and cleanup

Arrow buffers and private DataFrames remain in the caller. Local steps may reread
admitted inputs without reconstructing source queries. Keep output staging and
actual datasource-owned resources for atomic publication and recovery; remove
worker-only workspaces and lifetime records.

Before creating any surviving resource, Runtime reserves its exact Run-scoped
locator and ownership nonce. Files use the common Parquet contract where
applicable. Staging is validated before reuse inside the action and cleaned on
all terminal paths; harmless leftovers stay journaled under the existing
cleanup protocol. Pure in-process buffers require no durable journal row.

No staging resource can be recovered as an analysis output or supplied to a
later action. Runtime invokes only the preselected registered source and pandas
implementations and readers; a compilation or execution failure aborts the Run
without any alternative executor, repartitioning or retry path.

## Storage Selection

### One project-configured target

`execute()` remains zero-argument. No configuration means project-local Parquet.
Only a different destination needs an explicit `marivo.toml` setting:

```toml
[analysis]
storage = "object:archive"

[analysis.object_stores.archive]
endpoint_url = "https://objects.example.com"
bucket = "analysis"
access_key_id_env = "ANALYSIS_ACCESS_KEY_ID"
secret_access_key_env = "ANALYSIS_SECRET_ACCESS_KEY"
```

`storage = "local"` is also valid but unnecessary. The named object table may
include `region` and `session_token_env`. Credentials are exact environment
references, resolved only for the selected current operation; raw secrets never
enter project state, receipts, public state or diagnostics. Current object
bindings may remain configured while new outputs use local storage.

The only Artifact receipt kinds are local and object Parquet. Database result
storage is removed. The selected target covers primary rows and every required
retained part as one atomic Artifact. Exact
membership and distribution parts may stream to either target without exposing
raw private rows through the public Dataset boundary.

Configuration and authorization resolve after incomplete Run admission and before
source work. Missing, malformed, unsupported or ambiguous selection fails that
Run at `storage_selection`. No size-based switch, target ranking, automatic
alternative or failed-write retry exists. The writer does not choose or replace
the computation implementation. An existing execution binding returns its exact
Artifact without rewriting or relocating it when the project setting changes.

### One writer-validation handoff

The compiler builds the final output contract and fixed producer domain. Runtime's
writer validates the one configured target against that output and returns an
exact write binding or one bounded failure. `BoundMaterializationOutputV1`
connects the writer to primary/retained producer handles and schemas; it is not
a receipt, candidate-selection token or prepared-plan callback.

The compiler then completes engine compilation and hands Runtime the fixed
recipe. Runtime executes it, creates receipts and performs the existing atomic
publication protocol. Storage validation cannot change input domains, request a
second calculation implementation, or select a second target. Materialized reads
and execution-binding hits never enter this writer handoff.

### Local materialization policy

Local persistence streams rows into Parquet; it is not a bounded-local analytical
calculation. Its fixed first-cutover limits are:

Parquet writers stream complete primary rows and all required parts to the
selected target. Normalize Arrow types losslessly and validate exact schemas,
keys and row counts. There are no stored-byte, decoded-batch, cell or page caps.
Available byte/row measurements describe actual work without admitting it.
Variable-width data and driver buffering may allocate more than a transfer
batch. External write failures publish nothing; no truncation, alternate sink or
fallback is attempted. Unpublished files remain exactly journaled until cleaned.

### Storage choice does not change definition identity

Storage choice and receipt identity affect Artifact identity but not the
Logical Dataset definition fingerprint. Equal definitions executed in
different Sessions may choose different immutable sinks and therefore produce
different Artifact identities, even if their canonical rows are byte-equivalent.

A later same-Session binding recovery returns the exact existing Artifact rather
than relocating it implicitly. Relocation requires a separately designed
explicit capability; it is not another `execute()` mode.

## Storage Receipt Contract

### First-cutover Parquet contract

Runtime Parquet staging and durable local/object Dataset storage use the same
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
```

The contract fixes logical round-trip behavior and supported format decoding.
Writer dependency/tuning details are optional diagnostics, not stored reuse gates. A Run-staged temporary file references it only through its
physical representation and cleanup manifest. A committed local or object
Dataset receipt additionally binds immutable files, exact row and byte counts,
content authority, and Artifact publication. Equal Parquet syntax therefore
does not make exchange staging a Dataset.

### Common receipt envelope

Each primary output or retained part has one closed local, engine, or object
receipt with its locator, format/decoder version, schema fingerprint, exact row
count, byte-count availability, and immutable content identity. The concrete
variant below is the canonical stored value; common fields are not serialized
a second time. Receipt identity is a digest derived from that value.

Storage references identify the configured adapter and credential reference
needed for access, never historical permission grants. The fixed reader's current
access, binding and schema support are checked for the actual consumer; they are
not persisted as a promised future `scan_admission`. No input import or placement
alternative is inferred from a receipt. No credential values or executable plans
enter receipts. Supported format/decoder versions govern cold reads; writer
library fingerprints and compression tuning are diagnostics, not compatibility
or Artifact-reuse gates.

Local/object receipts support authorized PyArrow decoding and registered native
Parquet scans. Preview retains display bounds; complete collection has no resource caps.
Native scans validate exact manifest/version, content hash, schema and row count,
then use the preselected domain with owned-resource cleanup. No receipt grants access to origin sources or changes its retained
semantic authority. Every reader accesses selected backing only.

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
```

The primary or role-specific path is under the owning Session's immutable Artifact
directory and cannot
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
```

The manifest binds every immutable object version, size, and content hash while
the Artifact metadata keeps only the bounded manifest identity. A mutable
prefix listing without version ids or a committed manifest is invalid.

Object Dataset storage also uses the first-cutover Parquet contract. An engine
may write the files directly, but the immutable object-version manifest and
exact receipt remain mandatory.

The engine may write directly to object storage. Rows do not pass through local
memory unless the compiler and runtime separately admit the bounded transfer.

### Content integrity

Each receipt binds the exact stored payload using a canonical manifest, immutable
relation version, or a registered row/file hash. This value is part of that receipt,
not a second `CanonicalContentAuthorityV1` payload duplicating schema, row-set,
and row-count fields. Public content-digest projections derive from the receipt.

The primary receipt must agree with the Artifact's public row and row-set
contracts. A retained-part receipt instead agrees with its exact private contract
and schema; it need not satisfy the public Dataset shape. Validation covers every
column, row multiplicity, null, type, and ordering fact required by that payload.
A checksum whose meaning is insufficient is not accepted as full integrity proof.

Full publication validation computes these facts once. Normal operations validate
only the accessed immutable versions/files; a complete hash/count scan belongs
to explicit integrity inspection, not to opening a handle or showing a preview.

## Dataset Artifact Contract

### Artifact metadata

Every committed Artifact is assembled from one Store snapshot and decodes
exactly as this read value. Only its normalized relation fields and
`DatasetArtifactDescriptorV1` are stored; this full envelope has no sidecar:

```text
DatasetArtifactV1
  schema = "marivo.dataset_artifact/v1"
  artifact_ref
  session_ref
  definition_fingerprint
  execution_key_digest
  row_contract
  row_contract_fingerprint
  row_set_contract
  row_set_contract_fingerprint
  realized_schema
  realized_schema_fingerprint
  bounded_lineage
  semantic_dependency_digest
  population_authority
  sampling_execution
  operator_implementation_versions[]
  dataset_materialization_contract
  storage_receipt
  retained_parts[]
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
Lifecycle, or PopulationDataset meanings.

The metadata contains no executable logical root, semantic graph, relational
plan, physical plan, SQL, backend connection, or current-catalog resolver.

### Artifact ref and identity

`artifact_ref` is one opaque immutable locator unique within the project Store.
Its original producing Session remains its owner. It need not be the content
digest and must not be used as a definition cache key.

Artifact identity binds:

- Session ownership;
- definition fingerprint and input authority tokens;
- exact semantic dependency digest;
- exact Population and realized sampling authority;
- family, shape, row, schema, and implementation contract versions;
- storage receipt identity;
- canonical content authority;
- quality and Evidence digests.

Separately committed Artifacts retain distinct identities even when rows are
equal. Receipt, contracts, and lineage explain the exact result; matching these
facts does not certify suitability for another analysis.

### Session-local execution key and binding

Dataset Core owns one canonical `definition_fingerprint`. It includes exact bound
semantic dependency versions, captured source-parameter digests, ordered Logical
or Materialized input tokens, row/row-set contracts, Population and sampling
intent, the canonical sharing relation of significant logical realizations,
and producer implementation/quality/Evidence/retained-state registrations.
Runtime never independently normalizes or re-lists that dependency closure.

```text
DatasetExecutionKeyV1
  definition_fingerprint
  materialization_protocol_version = 1
```

The digest is domain-separated by `marivo.dataset_execution_key/v1`. The protocol
version changes only when common materialization behavior changes the committed
result contract, not for diagnostics, placement, writer-library updates, or
current storage access. The Session is the lookup scope, not a duplicated hash
input: the Store uses unique `(session_ref, execution_key_digest)`.

Logical tokens bind exact upstream definitions; Materialized tokens bind exact
same-Store Artifact refs. Immutable Artifact identity already resolves contracts
and backing; it needs no second content/row-contract identity vector. A downstream
definition over a selected Artifact therefore differs from one over its original
Logical definition. Equal rows or current source state never substitute inputs.

Equal ordered Logical tokens alone do not establish an equal execution key.
Core's fingerprint also distinguishes one shared sample from separately authored
equal sampling definitions within the action. A committed comparison of separate
samples cannot satisfy a later comparison requiring one shared sample, even
when every standalone operand fingerprint matches. Runtime does not inspect,
renumber or persist realization handles to make this decision; it looks up the
complete Core fingerprint under the ordinary Session key.

There is no binding payload/table, duplicate producer ref, or bound timestamp.
Artifact and succeeded terminal insertion share one transaction. The terminal
owns the producer edge; Artifact `committed_at` is the binding time. The Artifact
row cannot be redirected, refreshed, or deleted independently of the Session.

A hit performs the operation-scoped recovery checks below and constructs a new
handle for the exact Artifact. It does not query source rows, recalculate sampling,
compare the current catalog, choose another sink, or create a Run. A missing or
invalid selected result fails without origin replay.

### Materialized public state construction

A Materialized handle is constructed from one Store snapshot containing exact
Artifact identity, a succeeded producer, the supported descriptor and main row
contract, and the commit-time Evidence summary. It is a handle to committed data,
not a certificate that every file and Finding has just been scanned.

Construction validates the required metadata and receipt structure. Backing and
private-part access are checked by the operation that consumes them. A row-only
operation does not load Findings or unrelated private parts. The private scan
handle names the Artifact and the explicitly selected execution Session; it
cannot replay origin lineage. Known contradictions in the selected metadata fail
explicitly, rather than being silently omitted.

## Quality, Evidence, and Findings

### Common materialization-contract envelope

Every producing Dataset definition resolves exactly one immutable contract
before Run admission:

```text
DatasetMaterializationContractV1
  producer_id
  producer_contract_version
  shape_id
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

### Artifact-owned retained parts

`retained_parts[]` in the descriptor contains only instances required by the
producer's registered retained-state contracts:

```text
ArtifactRetainedPartV1
  role
  contract_id
  contract_version
  storage_receipt
```

The family registration defines unique roles, private schemas, and which
operations require each role. Missing, duplicate, unexpected, or incompatible
required parts fail publication. The empty list is valid when none is required.
Each part is actual data, not just a contract id: for example Metric numerator/
denominator state, `lifecycle_legal_transition_trace@v1`,
`lifecycle_subject_coverage@v1`, or `lifecycle_violation_trace@v1`. Its receipt supplies exact
location, schema, counts, and content identity. Raw identities remain in governed
storage, not descriptor JSON or diagnostics.

Module 6's history registration requires all three Lifecycle roles as one
producer bundle, including empty but valid traces. The legal-transition role
preserves transitions absent from positive-duration public intervals; the
subject-coverage role preserves admitted subjects without any public interval.
Their exact schemas, consumer dependency sets and validation are owned solely
by Module 6. Aggregate counts or source lineage cannot substitute for these
rows. Runtime enforces their joint realization, receipt integrity and atomic
publication without interpreting state-machine meaning.

Primary rows and parts belong to one Artifact and one producer. Parts have no
independent Artifact ref, Run, execution binding, graph node, or publication state.
They use the same selected sink kind/domain and access boundary, with separate
physical relations/files for each role;
the descriptor binds all receipts in the same metadata commit. All locations are
Run-owned reservations before commit and transfer together to Artifact ownership.
A crash before commit leaves all of them unpublished cleanup obligations.

The manifest/directory layout separates primary rows from each private role.
Public scans and `to_pandas()` select primary rows only. A consuming operator
requests its registered parts explicitly, validates their receipts, and fails if
required state is absent or corrupt; it never reconstructs state from origin data.
A row-only read does not validate unrelated parts. Explicit full integrity
inspection covers primary data, every declared part, and all Findings. Any future
deletion must treat the complete Artifact bundle as one ownership unit.

Slice 5c's source-private membership parts use separately frozen source relations
with independent schemas and counts, in the same action realization and selected
engine sink as their primary rows. They are never projected through the generic
Arrow/pandas part reader. Native engine checks validate their immutable receipt,
key schema, pair uniqueness, primary-coordinate support and count endpoints,
returning only schema facts and aggregate violation counts. Full integrity
inspection uses the same source-only boundary; opaque byte hashing of the
immutable engine receipt does not decode membership rows. Missing or corrupt
parts cannot be reconstructed from an origin graph.

### Staged calculation

Quality checks and Evidence extraction execute before immutable output
publication is commit-decided. They may be pushed into engine stages or consume
registered bounded validation outputs, but their results remain private staging
until the publication transaction commits.

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
  finding_set_digest
  extractor_contract_versions[]
```

An empty Finding set is represented by `finding_count = 0` and the canonical
empty-set digest. It is complete Evidence, not unavailable Evidence.

### Publication bundle

Artifact descriptor, relation-owned identity and commit time, complete Evidence,
ordered Findings, and one succeeded Run terminal form the publication bundle.
The Store transaction validates their digests, counts, producer identity,
execution key, consuming Run/output ownership, and exact same-Store input refs
before committing. Input Artifact owners may differ from the Run owner.
There is no additional marker value or publication-state row. Materialized
reads never write any part of this bundle.

## Publication Protocol

### Pre-commit staging

Output files, immutable engine versions, object manifests, validation streams,
and calculated quality/Findings are private until the metadata transaction
commits. Recoverable external resources have exact reservations before creation
or submission. Pure in-process values use action-local cleanup only. Artifact
descriptors remain typed values in memory until their transactional insertion;
there is no metadata file to rename, reserve, or reconcile.

Primary/part final locators are known and journaled before any rename or remote
finalization. Reservation covers both staging and final locations through exact
Run-owned paths; a crash during that transition cannot leave an unrecorded final
file. The publication transaction transfers every committed payload reservation,
never an unrelated temporary-resource row.

### Commit ordering

While holding the Session writer guard:

1. Finish primary-output and validation stages.
2. Validate schema, row keys, counts, action bounds, and family checks.
3. Build quality, typed issues, complete Evidence, and ordered Findings.
4. Finalize primary and retained-part storage, establish durability, and obtain
   all receipts.
5. Validate every finalized receipt, required private part, and content identity.
6. Prove all started executions terminal/fenced and remaining non-output
   resources harmless; attempt cleanup without making deletion a success gate.
7. In one Store transaction insert Artifact, Evidence, Findings, and succeeded
   terminal; validate the complete bundle and delete all output/part obligations.
   Retain unresolved harmless non-output rows without another status field.
8. Commit once, decode the committed scan leaf, and return the Materialized Dataset.

Steps 1-6 do not publish a result. The commit in step 8 makes every metadata
fact visible together; immutable data written earlier is unreachable as an
Artifact until that commit. A reader never observes a committed Artifact with
an incomplete producer or partial Evidence.

### Execution-key recovery ordering

Within the writer guard, after recovery of that Session, `execute()` derives or
reuses its key and validates the selected Artifact metadata as for an exact
handle lookup. It does not inspect all backing, private parts, or Findings. On a metadata-valid hit it returns the same scan leaf
without a Run, metadata write, new Findings, storage copy, or origin execution.
Ordinary `session.artifact(ref)` recovery is read-only and needs no writer guard.

### Proven failure before commit

First establish that the publication transaction did not commit. Then prove
all started backend executions terminal or safely fenced before cleaning their
dependent resources. If terminal proof is unavailable, leave the Run incomplete
and return a typed recovery-pending error naming the Run. Every later writer in
that Session must retry reconciliation before admitting work.

After terminal proof, discard buffers and attempt exact cleanup of unpublished
storage and temporary resources. Insert the failed terminal and delete resolved
obligations in one transaction. Failed Runs may retain unresolved cleanup rows;
guarded maintenance retries them without re-executing analysis. Failure to
record a terminal leaves the admission incomplete for later recovery.

### Uncertain commit acknowledgement or failed delivery

An exception from commit or from returning a result does not establish failure.
While holding the guard, finish/close the transaction connection and inspect a
fresh authoritative Store snapshot:

- a matching complete succeeded bundle means success already committed; preserve
  storage and return or recover the same Artifact;
- a failed terminal remains failed and is never rewritten;
- terminal absence after SQLite has resolved the transaction means publication
  did not commit; perform the proven pre-commit failure path;
- if the Store cannot be read, return a typed recovery-pending error without
  deleting resources, rewriting history, or admitting another computation;
- contradictory partial metadata is an integrity violation, not a repair gap.

If the process exits, the next guarded recovery performs the same check. There
is no durable commit-pending state and no protocol that completes a separately
committed Evidence decision. A result-delivery failure cannot turn a committed
succeeded Run into failed.

### Store invariants

The following states fail closed:

- a succeeded Run without its one complete Artifact/Evidence/Finding bundle;
- a failed Run with an output Artifact;
- an Artifact without exactly one same-Session succeeded producer;
- producer and Artifact execution keys that disagree;
- duplicate same-Session execution-key Artifacts;
- Evidence digests or Finding counts/order that disagree with their exact values;
- receipt/schema/row-contract counts or content authority that disagree;
- committed output still recorded as an outstanding cleanup obligation;
- multiple incomplete Runs within one Session governed by the serial protocol.

The Store validates relational invariants without querying external storage.
Payload-consuming operations validate their selected backing; full inspection
checks all declared payloads and Findings. Metadata-only handle recovery does not. Recovery never
manufactures missing committed metadata, chooses the newest candidate, or
reconstructs Evidence from abandoned staging.

## Session Writer Guard

### Scope and acquisition

One stable `sessions/<session_ref>/session.lock` protects all mutations of that
Session across execution keys, processes, and threads. The guard combines a
reliable OS-released exclusive lock with in-process exclusion keyed by canonical
Store identity and `session_ref`. Another thread or reentrant top-level action
cannot bypass a process-scoped OS lock. Unsupported locking platforms fail
closed; there is no time-based fallback.

Acquire non-blockingly before an existing Session's top-level writer operation.
Contention returns a structured Session-busy error with the safe Session
identity, expected exclusive access, and repair to finish its current writer
and retry serially. It creates no Run, cancels no writer, and waits in no queue.
Internal helpers share the already-held operation context; callbacks cannot
start nested top-level writes. Child processes do not inherit mutation authority
and cannot keep the owner's lock alive after its exit.

Hold the guard through that Session's recovery, binding lookup, execution,
publication, and failure handling. Release it in a guaranteed exit path. Never
delete/replace the lock file, use PID/mtime as ownership proof, or hold a SQLite
write transaction across external work. Read-only operations need no guard.

### Session identity and shared registry

Session names and refs still live in one project-level database. Resolve an
existing name/ref before acquiring its lock, then recheck its exact identity
inside the guarded operation. A new named Session reserves a candidate ref and
acquires that candidate's lock before a short SQLite transaction rechecks name
uniqueness and creates the Session with its initial current-pointer update.
If another creator already committed the name, roll back, release the candidate
lock, and resolve/acquire the winning Session's lock. Never hold two Session
locks during this retry or admit a Run against the discarded candidate. An
unused lock file carries no Session authority and is not a resource obligation.

Creation, explicit question update, activation recency, and current-pointer
change remain transactional. The shared `runtime_state.current_session_ref`
is only a last-successful-activation convenience. Concurrent activation of
different Sessions serializes those short transactions; it cannot redirect an
already acquired Session handle, which always uses its explicit `session_ref`.
Schema initialization is similarly one short SQLite transaction, not a project
execution lock. SQLite busy handling is bounded and separate from non-blocking
Session-lock contention; it cannot elect or retry an analysis producer.

### All Session writers use one boundary

Activation, current-pointer updates on behalf of a Session, explicit recovery,
producer admission, resource reservation/cleanup, publication, and maintenance
use that Session's guard. `current()` and other routes performing reconciliation
resolve one Session then use its guard even if no rows ultimately change.
Metadata-only discovery and exact Artifact reads remain separate read paths.

Each writer first reconciles prior incomplete Runs and resource obligations
only in its own Session. Unresolved execution that can still write blocks new
computation; terminal harmless cleanup does not block normal mutations; recovery/maintenance may still write the facts needed to discharge the
obligation. Maintenance covering several Sessions handles each independently,
never holds multiple Session locks, and does not stop healthy Sessions because
another Session is busy or blocked.

Different Sessions may run backend work concurrently. Their short metadata
transactions serialize through SQLite, and every query, write, and resource
lookup is scoped to its exact Session. Private resources and buffers are never
shared across actions. No project/Store execution lock, claim table, producer
election, lease table, heartbeat, per-Run lock, automatic wait-and-reuse, or
concurrent same-Session execution exists.

## Cold Recovery

### Recovery order

After acquiring the target Session's writer guard and letting SQLite resolve
its own transaction recovery:

1. Read that Session's admissions, terminals, and outstanding resource obligations
   from one Store snapshot. Multiple incomplete Runs in this Session violate
   serial admission; incomplete Runs in other Sessions are independent.
2. For a succeeded Run being inspected, require the complete matching metadata
   bundle and preserve its output. It needs no terminal rewrite or index repair.
3. For an incomplete Run, verify there is no committed output bundle, then use
   its recorded execution recovery capability to prove external executions
   terminal or fenced against further writes. Lock acquisition alone proves no
   remote termination.
4. If terminal proof is unavailable, leave the Run incomplete and return a typed
   recovery-pending error. Reads and other Sessions remain available; this
   Session admits no new work.
5. After proof, clean exact unpublished resources, insert the failed terminal
   with `process_lost` once leftovers are proven harmless, and delete resolved
   obligations transactionally. Remaining
   harmless garbage obligations do not block new work in this Session.
6. Retry cleanup for terminal Runs, succeeded or failed, using exact locators
   and ownership nonces. Leave unresolved harmless rows and continue; delete a
   row only after its obligation is discharged.
7. Contradictory selected metadata stops this Session's recovery with an integrity
   error and no guessed repair. No scan of old generations or other Sessions'
   execution resources occurs.

Recovery never resumes an execution stage, serializes a Logical graph, publishes
staging, replays a query, or converts an old Run into a new one. Retry after
reconciliation admits a new Run only when its key is unbound. Committed results
are recovered unchanged. No lease expiry or caller-confirmed `abandon_run(...)`
is required for normal Session-guarded recovery. If the cutover retains an
explicit `abandon_run(...)` entrypoint, it invokes this same guarded protocol
and requires registered terminal/fencing proof; caller assertion cannot bypass
the guard, publication readback, or external obligations.

### Read availability

A busy Session writer or unresolved remote execution does not by itself block
read-only browsing of committed history, Artifacts, and Findings. Readers select
one SQLite snapshot and never reconcile. Database unavailability or inconsistent
selected metadata fails explicitly; there is no empty-page fallback or second
metadata store. Database-level corruption/unavailability may affect all Sessions;
Session execution contention or unresolved cleanup does not.

## Temporary Resources and Cleanup

### Outstanding external obligations

Persist a resource obligation only when an operation can leave a resource or
backend execution that outlives the owning process. Reserve it durably before
creation/submission and before consumption:

```text
ActionResourceJournalEntryV1
  run_ref
  resource_kind
  execution_domain_id
  ownership_nonce
  cleanup_capability_id
  safe_locator
```

The owning Session is derived through the Run. Every lookup and mutation is
performed under that Session's writer guard. `safe_locator` and the resource
ownership nonce are chosen before creation and let the registered adapter find
exactly one owned resource/execution. They are not process-owner identities.
Presence means only that reconciliation remains necessary, not that creation
succeeded or that the backend is still running. No progress/state column exists.

Write-capable resources require exact owned locators. A read-only query may lack
a query ID; missing remote correlation or termination lookup is not admission.
Locators contain no credentials, SQL, broad prefixes, or unresolved patterns.
An exact Run-owned workspace directory is allowed only when the registered
adapter proves exclusive ownership of its entire subtree; a shared parent is
never a cleanup target. Retries must not reuse old resource locators.

### Durable resource classes

```text
planner_temporary_relation
backend_execution
private_parquet_staging
local_storage_staging
engine_storage_staging
object_storage_staging
```

Read-only execution uses `read_only_execution@v1` with an action nonce, not a
process identity or remote termination certificate. Driver cancellation/close is
attempted where Python retains control. Query IDs are optional safe diagnostics.
Connection-local temporary relations cannot publish Artifacts on their own.
Recovery resolves Store commit state under the Session writer guard before
cleaning exact local output paths. S3 requests retain `s3_request@v1` write-safety
proof; unknown object writes are not treated as harmless remote reads.

Pure in-process Arrow batches, pandas/numerical buffers, and guarded in-memory
results are never journal rows. Their counters and cleanup
remain action-local; process termination discharges their memory lifetime.
If an executor spills to files, those files belong to an exactly reserved
workspace or exchange obligation. There is no Artifact metadata-file resource.
An ordinary configured datasource may own native temporary resources, including
DuckDB resources under its registered datasource protocol. There is no separate
internal DuckDB executor or resource class.

### Cleanup and output ownership transfer

Primary output and all retained parts remain outstanding reservations before
publication, even after finalization. One metadata transaction inserts the
Artifact and succeeded terminal and deletes precisely those output reservations.
Their receipts then own the complete committed bundle. Cleanup cannot delete a
committed payload or infer ownership by scanning shared directories.

Publication and failed terminals require resolved local commit and publisher
ownership. Unknown remote read termination alone does not block Session work.
Only exactly owned non-output locations with no committed dependency may be
cleaned; unknown object writes and unresolved publication ownership still block.

Attempt immediate idempotent cleanup of harmless garbage, but its deletion failure
does not veto valid output, turn success into failure, or block another Run.
Keep the existing journal rows and retry under the Session guard on later writes
or explicit maintenance. Succeeded and failed Runs may both retain such rows.
Their terminal state is unchanged. No extra cleanup status, progress table,
background scheduler, or lease is introduced.

A terminal Run proves all surviving journal entries are harmless cleanup
obligations unreferenced by committed Artifacts, including discarded output
reservations of a failed Run. Incomplete Runs still require guarded publication
reconciliation. Delete each garbage row only after exact cleanup is confirmed;
read-only execution discharge does not claim remote death or file deletion.
Missing resources count as resolved only under the registered ownership protocol.
If maintenance cannot clean a terminal Run's garbage, it leaves the row and
continues without blocking unrelated work. Real quota exhaustion may still fail
a new allocation on its own merits.

## Artifact Recovery and Immutable Scan Leaves

### `session.artifact(ref)` validation

An exact lookup validates only its selected Store metadata: ref/owner, supported
descriptor/receipt formats, producer output edge, and required public contracts.
It reads the commit-time Evidence envelope as summary facts without loading or
re-hashing Findings. No external file, row count, or complete content scan is
required merely to construct the handle.

Operations then validate their actual dependencies:

- `show()` reads primary data under its preview bound, checking accessed storage
  identity/access, schema and ordering; it does not re-count the entire Artifact;
- `to_pandas()` validates and reads all primary rows under collection guards;
- operators validate primary data and only the private roles they consume;
- Finding reads validate the selected Finding bodies and their stored identities;
- explicit `revalidate(ref)` checks the entire Artifact, all parts and Findings,
  including full content/count/digest verification when the receipt requires it.

A committed manifest/version is a lightweight identity check, not a claim that
all payload bytes were just re-hashed. Normal access checks metadata/version identity, format decoding, and checksums
available for the accessed blocks; they do not claim a complete file hash was
recomputed. An operation must fail on known corruption in a dependency it uses,
rather than silently substituting another value. Missing
unrelated private data does not prevent a primary-row preview, but is reported by
full inspection and blocks any operation that needs that part.

Errors distinguish absent metadata, unsupported formats, access failure, missing
or mutated selected backing, corrupt selected Evidence, incompatible row contracts,
and foreign-Store identity. No failure causes logical replay.

### Scan-leaf decoding

The private immutable handle carries the exact Artifact ref, canonical Store
identity, and explicit execution Session. Contract/receipt values are resolved
from that immutable Artifact and current adapter capabilities when needed; the
handle duplicates no persisted receipt or content hash inventory.

A consumer's scan admission is operation-local and may include projection,
predicate, ordering, or retained-state fold operations. It validates that
operation's actual inputs without inspecting lineage for executable rewrites.
Cross-Session recovery creates a new handle, preserving the original Artifact
owner and original handle. No public serialization or registration is introduced.

### No Materialized `execute()` method

A Materialized Dataset does not expose `execute()`. It is already the reusable
Artifact-backed state. Call `show()` or `to_pandas()` to read it, or call any
registered downstream operator to obtain a new Logical Dataset. Reconstructing
the exact prior Logical Dataset and calling `execute()` resolves the
same-Session binding without a new Run.

## Revalidation

Event funnel attribution includes its mapped additive component part in full
storage inspection, validating the owning component schema and non-null support
fields for both joint and hierarchy outputs. Part receipts fingerprint the
persisted Parquet Arrow schema after the verified write round trip, including
physical nested-field names. Primary previews remain independent
of that part, while missing or changed part bytes affect the storage axis.

`session.revalidate(ref)` is explicit full integrity inspection. Its read-only
result has exactly three independent axes:

```text
ArtifactRevalidationV2
  artifact_ref
  checked_at
  artifact_integrity = valid | invalid | unverifiable
  storage_authority = readable | unauthorized | missing | mutated | unknown
  evidence_integrity = valid | invalid | unverifiable
  issues[]
```

Artifact checks include the descriptor/producer relation, primary and retained-part
contracts. Storage checks cover all declared receipts and required full content,
row-count and ordering verification. Evidence checks validate the complete Finding
set against the committed digest. Issues identify the exact affected part or
Finding using safe identities. Unavailable checks are `unverifiable`/`unknown`,
not proof of corruption. This potentially expensive operation is never implicit
in handle recovery, a preview, or execution-key lookup.

There is no semantic-catalog comparison, source freshness check, equivalence
verdict, expiry policy, reusable flag, or overall approval. Producer versions and
times remain factual provenance. New operators validate only current semantic
inputs they explicitly request. Inspection never contacts origin sources, changes
metadata, repairs Evidence, reruns analysis, or creates a Run.

When different declared payloads have different storage outcomes, the summary
uses this deterministic priority: `mutated`, `missing`, `unauthorized`, then
`unknown`. Every observed problem remains in `issues`; `readable` requires all
declared storage checks to succeed. This summary does not combine the Artifact
or Evidence axes into an overall approval.

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
  whose complete execution key names one metadata-valid committed Artifact binding.

The first rule covers partial and complete materialization of multi-input
operators. Their immutable engine readers may compose in one source domain;
otherwise only an exact registered pandas continuation may collect their
required rows/parts under combined limits and admitted semantic/privacy rules.
Local/object inputs use their PyArrow reader, with no datasource import.
Source-only identity operations still require a compatible source-domain leaf;
calling `execute()` is not automatically a repair for that requirement. A
committed Artifact not named by an input or its exact write-once execution
binding is not an implicit cache candidate.

Holding one Logical Dataset in a Python variable is not the reuse mechanism.
The named Session binding is durable: another script or process may reconstruct
the same exact logical definition and call `execute()` to recover the Artifact.
Python variable names, script paths, and line numbers are irrelevant.
Within an action, the compiler preserves the owner-bound realization sharing
relation already included in Core's definition fingerprint. Shared producer
handles may also share expression construction, but required single evaluation
needs its exact fence. Distinct sampling realizations are not merged because
their definitions match. General compiler CSE and separately fingerprinted
execution graphs are absent.

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
- a foreign input retains its original owner and appears as an external Artifact
  boundary in the consuming Session graph; its producer Run is not expanded;
- only locally produced Artifacts are candidates for that Session's head set,
  and only succeeded consumers in that Session remove them from it;
- root Runs have no Artifact inputs, including external inputs; reading an
  Artifact alone creates no membership or graph edge;
- failed and incomplete Runs remain visible without partial Artifact nodes;
- a downstream chain over a scan leaf records the exact Artifact input even
  when its public definition fingerprint resembles a logical chain;
- Session writer locks, staging resources, cleanup obligations, and recovery
  mechanics remain private operational concerns.

Graph reads project committed Run, Artifact descriptor, and Evidence-envelope
facts from one Session Store snapshot without scanning Finding bodies. Artifact
summaries derive original owner and producer admission/finish times from their
canonical rows. Reads never query a datasource, run revalidation, perform recovery,
or include uncommitted staging. External scope boundaries alone do not indicate
truncation; budget omissions still do.

## Failure and Repair Matrix

| Condition | Run outcome | Public output | Repair |
| --- | --- | --- | --- |
| deterministic Dataset/graph contract invalid | no Run | none | repair construction or incompatible runtime |
| Session writer lock busy | no Run | none | finish the writer in this Session, then retry serially |
| prior execution may still write | prior Run incomplete | no new computation | prove exact termination or fencing in this Session |
| semantic dependency missing after admission | failed `semantic_validation` | none | repair the required semantic definition or explicitly select retained inputs |
| execution boundary unavailable | failed `execution_boundary` | none | configure a registered reachable boundary |
| Ibis/backend compile rejection | failed `ibis_backend_compile` | none | repair lowerer/adapter conformance or use a supported method |
| transfer or local guard exceeded | failed `transfer_guard` | none | narrow scope or use an admitted execution boundary |
| output schema/key/count mismatch | failed `output_validation` | none | repair lowering or operator contract |
| no durable sink for result bounds | failed `storage_selection` | none | configure immutable storage or reduce the result |
| quality or Evidence construction fails | failed before publication | none | repair check/extractor and retry after guarded publication recovery |
| harmless temporary deletion fails after terminal proof | computation outcome unchanged | publish/recover valid output | retain journal entry and retry cleanup later; new work remains allowed |
| commit acknowledgement uncertain | read existing terminal; no inferred transition | same Artifact only if commit is proved | read authoritative Store before deleting output or retrying |
| process dies before metadata commit | incomplete until guarded recovery proves no successful publication, then failed | none | reconcile this Session, then retry explicitly |
| process dies after metadata commit | already succeeded | recover exact Artifact | read the committed bundle; no index repair or re-execution |
| exact metadata-valid binding exists | no Run | same Materialized Dataset | none |
| selected backing missing/mutated when accessed | existing producer unchanged | dependent read/operator fails | restore exact backing or explicitly author new work; never replay implicitly |
| source rows changed after commit | no automatic check/state change | exact selected Artifact | Agent chooses the existing result or a new computation |

Failed terminal insertion requires proven absence of publication and terminal
external execution. If either cannot be established, return recovery-pending
without guessing an outcome. Repairs use actual capabilities and exact owned
resources, never hidden fallback, broad cleanup, or origin-plan replay.

## Safe Diagnostics

### Dataset cards and contracts

Before execution, Dataset surfaces may disclose:

- logical or materialized state;
- exact state-specific actions;
- materialized input refs;
- concrete operator input requirements;
- possible storage kinds;
- fixed local materialization bounds;
- action-time blockers known without live work;

An unexecuted Logical Dataset does not claim a selected sink, current snapshot,
Artifact ref, row count, quality, or Evidence. Binding lookup is performed only
by `execute()`.

### Run facts and optional diagnostics

Run reads expose lifecycle, bounded definition/source/input identity, admission
and terminal times, output ref, or structured failure. Input references preserve
operand order and repeated roles, including when both comparison operands select
the same Artifact. Graph nodes remain unique while consumption edges retain those
ordered occurrences. Storage kind and counts
come from the output Artifact. There is no mandatory compiler-audit payload,
physical-plan fingerprint inventory, CSE count, or sink-feasibility certificate.

Opt-in compiler diagnostics may report bounded stage/placement/transfer facts
and implementation versions during an invocation. They create no Store state,
public result type, or success prerequisite. Formatting or emitting diagnostics
cannot invalidate a committed result. Safe output excludes SQL, credentials,
raw identities, complete plans, temporary locators, and private buffers.

The private `DatasetRuntime.statistics.statements` list is a separate in-process
debugging surface: it retains statement kind and original SQL verbatim, including
literals and source parameters. Recording performs no parse, transform, or render
pass. It does not add SQL to Store records, cards, errors, or telemetry.

### Artifact cards and contracts

A materialized Dataset may disclose committed:

- Artifact ref, family, shape, definition fingerprint, and producing Run;
- storage kind, realized schema, exact row count, and byte-count availability;
- quality summary, typed issues, Evidence digest, and Finding count;
- bounded source/input lineage, original owning Session, and producer timing;
- mechanically valid reads, revalidation, and downstream continuations.

It does not expose storage credentials, raw object manifests, engine SQL,
identity rows, or origin executable plans.

## Cross-Module Seams

### Dataset Core supplies

- exact Dataset owner, shape, row contract, row-set contract, schema, state, and
  definition fingerprint;
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
- filter field resolution and input checks;
- realized Population, sampling, construction, and reconciliation validation
  requirements;
- exact quality facts needed for Population and observation Evidence.

This runtime captures realized authority and publishes it only with the
Artifact.

### Source Pushdown and Pandas Execution supplies

- a small immutable in-process execution recipe with maximal supported Ibis
  source prefixes and a registered pandas terminal suffix;
- exact Ibis builders, `PandasStepV1` implementations and output/retained contracts;
- Marivo-owned domain identity checks independent of Ibis connection equality;
- required single-evaluation fences and process-loss behavior;
- exact source/Artifact-to-pandas Arrow boundaries, direct private DataFrame
  continuations and safe optional execution diagnostics;
- configured-target writer inputs, with no sink candidates or selection callback.

Runtime admits the Run and supplies bindings and one storage
target. It validates the writer, executes the fixed steps, checks actual batches,
invokes complete-input pandas consumers, validates local intermediate results,
journals resources and commits the Artifact.
No compiler graph, implementation manifest or fingerprint is recovery authority.

### Typed Operators supplies

- operator implementation and quality-contract versions;
- concrete input checks for every non-filter occurrence;
- action-time validation requirements;
- family-specific quality, Evidence, and Finding extraction contracts;
- retained sufficient-statistic and materialized-read requirements.

The runtime invokes registered contracts and commits their outputs atomically;
it does not invent statistical meaning.

### Subject, Event, and Lifecycle supplies

- privacy-safe identity authority and storage constraints;
- subject-selection, Event, and Lifecycle input contracts;
- family-specific validation, Evidence, and Finding contracts;
- exact cold-recovery privacy invariants.

The runtime must keep raw identities out of metadata, Run audit, Evidence cards,
errors, and cleanup journals. Storage may contain governed identity rows only
under the family-owned privacy and authorization contract.

### Public Cutover consumes

- the unified Store, Run, Artifact descriptor, Evidence, receipt, and external
  obligation schemas, Session writer guard, and Artifact execution-key constraint;
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
the runtime stores execution identity on the admitted Run and resulting Artifact,
without persisting an executable definition.
Persisted Run projections cannot reconstruct a private graph or become operator
inputs.

### Use definition fingerprint as an Artifact cache key

Rejected as an Artifact identity. The canonical definition fingerprint names
exact authored meaning and input identity; the Session-scoped execution key adds
only the common publication protocol version. The Artifact ref separately names
one immutable realized result. None of these identities replaces the other.

### Publish storage before Evidence and call it an Artifact

Rejected. Immutable bytes without the commit-time governed contract are
unpublished storage, not a Dataset Artifact.

### Succeed materialization with partial Evidence

Rejected for the first cutover. A zero-Finding complete envelope is valid;
extractor or Evidence publication failure is not.

### Split Artifact metadata and Evidence into independent stores

Rejected. Artifact descriptors, complete Evidence/Findings, and Run success have
one publication lifetime and fit in one metadata transaction. Module ownership
does not require separate SQLite databases or metadata sidecars. External data
is finalized before the transaction and remains a journaled obligation until
commit. A separate marker and cross-Store completion protocol add no required
capability.

### Treat a commit exception as a failed Run

Rejected. An acknowledgement can be lost after commit. Read the authoritative
Store before cleanup, failure insertion, or retry. A complete committed bundle
already includes success and cannot be rewritten as failed.

### Elect concurrent producers within one Session

Rejected. Same-Session writes are serial by product contract, enforced by one
Session writer guard across the complete action. Claim/lease records, waiting
queues, heartbeats, and speculative duplicate work are unnecessary. Different
Sessions may execute independently with short shared-database transactions.

### Serialize every Session with one Store execution lock

Rejected for this cutover. The lock scope is `session_ref`: a busy or blocked
Session must not prevent another Session from executing. Only short metadata
writes and shared-registry updates serialize through SQLite.

### Automatically relocate a materialized Dataset

Rejected. A Materialized Dataset has no `execute()` method. Relocation would
need a separately named capability, new Artifact identity, and new Run
semantics.

### Certify source freshness or Artifact reusability

Rejected. Marivo reports factual lineage, timing, and integrity. The Agent
chooses suitability. Source snapshot certificates, freshness axes, maximum-age
admission, and automatic cross-Session equivalence matching have no runtime role.

### Require current semantic state for ordinary Artifact reads

Rejected. Artifact reads use committed row and Evidence contracts.
Catalog drift cannot reinterpret or hide committed rows.

### Substitute historical rows or origin execution after failure

Rejected. Exact input definitions and Artifact refs are bound before execution.
A failed semantic dependency or unreadable Artifact cannot change those inputs.

### Store temporary-resource names only in process memory

Rejected. Process loss would make durable engine or object staging
unrecoverable and leak resources without an auditable owner.

### Make harmless garbage deletion a publication gate

Rejected. Once executions are terminal/fenced and leftovers are exactly owned,
unreferenced garbage, deletion does not affect result correctness. Persist the
existing cleanup obligation and allow valid publication/new work. Unknown write-capable
requests still block unsafe publication; unknown remote reads alone do not.

### Expose writer guards, receipts, or recovery coordination publicly

Rejected. These are runtime coordination facts, not analysis values or public
continuations.

## Vertical Acceptance Journeys

Journey fixtures must cover default local Parquet and explicitly configured
object Parquet, including exact private membership/distribution state. Retained
identity selections may join eligible current sources through the fixed native
Parquet reader. Cold Artifact-only native execution must work with origin sources
offline and without a persistent database output. Retain independent native and
pandas algorithm conformance, same-Session ownership, complete-input validation,
source-domain conflicts and failure/no-retry assertions. Native eligibility must
be established before execution; failed compilation never chooses another path.

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
   Evidence, Findings, descriptor, Artifact row, and Run success agree.
4. Recover the Artifact in a cold process and read it as the same Dataset family.
5. Prove the recovered handle contains no logical root.

### Source pushdown and pandas continuation

1. Execute an eligible observe/filter/rollup chain and prove its contiguous
   supported operators compose in the Ibis source prefix without an intermediate
   DataFrame or Artifact.
2. Execute forecast over a governed logical daily series; prove the source query
   feeds exact guarded Arrow and pandas starts only after all required primary
   and retained-part inputs validate completely. No failed compilation probing,
   cost search or Parquet exchange-mode selection occurs.
3. Execute a registered local method followed by local filter/rank operations;
   verify direct validated private DataFrame handoff, no mandatory Arrow round
   trip, no local-to-SQL upload and publication of only the root Artifact.
4. Execute a registered multi-input pandas calculation over separate admitted
   source results. Assert each source prefix is pushed down, all inputs pass a
   combined guard before local invocation, and no generic federation occurs.
5. Execute a registered rollup over a local Parquet Artifact using PyArrow and
   pandas only; prove no origin query or internal DuckDB connection occurs.
6. Inject source compilation/execution, Arrow validation, pandas/output overflow,
   numerical failure, timeout and cleanup failure; prove no executor changes and
   no private buffer or staging becomes reusable authority.
7. Consume complete local primary/part inputs beyond former resource caps without
   origin replay or fallback. Separately registered source-only operations still
   reject incompatible domains before collecting private identities locally.

### Retained parts and scoped reads

1. Materialize Metric component state and a Lifecycle history with a violation
   trace. Assert concrete role/contract/receipt entries, complete payload data,
   one Artifact/Run per producer, and no separate part graph nodes.
2. Restart and execute a registered component fold or `violations()` using only
   the required part; original sources may be unavailable.
3. Instrument reads: handle lookup touches only Store metadata; preview touches
   no Findings or unused parts; selected Finding reads do not scan the full set.
4. Remove an unused private part. Primary preview still works, its consumer fails
   without replay, and explicit full inspection identifies the missing role.
5. Crash during part finalization and publication. No partial Artifact is visible;
   staging and final locations remain journaled, or all receipts transfer together.

### Variable-width streaming persistence

1. Write a small string-bearing result whose schema has no maximum text width.
   It succeeds without a full count query or static worst-case byte proof.
2. Stream and collect more than 100,000 rows; verify exact counts and complete values.
3. Verify wide/nested values and complete primary-plus-part writes without resource
   ceilings, using safe fixtures. Inject external write failures and verify no
   partial Artifact, truncation or alternate sink.
4. Exercise caller PID, original exceptions, user interruption and cold recovery
   after external process termination. Never deliberately exhaust the host.

### High-cardinality engine materialization

1. Configure an engine target for a Dataset whose output is not admitted for
   local storage; its final relation must already belong to that engine.
2. Assert no unbounded local transfer occurs.
3. Assert the engine relation uses an admitted immutable protocol.
4. Assert exact row count and version-pinned receipt are committed.
5. Execute downstream projection and filtering against the scan leaf without
   origin replay.

### Object materialization

1. Configure an object target and use its exact admitted immutable export
   protocol; no local/engine target is attempted first.
2. Assert manifest and objects finalize durably before metadata publication.
3. Assert local memory never receives the unbounded rows.
4. Recover through the fixed authorized object Parquet reader, with no engine import.

### Repeated exact logical execution

1. Execute one Logical Dataset in a named Session.
2. Reconstruct the same definition in a later script or process and call
   `execute()` in that same Session.
3. Assert the same `DatasetExecutionKeyV1` resolves the same Artifact.
4. Assert no new Run, datasource calculation, quality extraction, Finding
   publication, storage copy, or Artifact occurs.

### Source-independent same-Session recovery

1. Materialize a source without version metadata.
2. Change its rows and make its original query endpoint unavailable.
3. Recover its same-Session binding with no Run, origin connection, or freshness
   check, provided the Artifact backing remains readable.
4. Prove no source-authority record or reuse verdict is stored or disclosed.

### Explicit cross-Session Artifact input

1. Produce an Artifact in A and record its exact ref and producer times.
2. Read it through `session_b.artifact(ref)`; verify execution context B with
   unchanged Artifact owner A, ref, producer, storage, and Findings.
3. Derive/execute from the handle: only B creates a Run/output, whose input edge
   points to A's original Artifact. No copy or local binding is created by the read.
4. Verify no source query, automatic match, or approval; a busy/blocked A does
   not block this read or B's new execution.
5. Admit explicitly passed foreign Materialized operands, reject foreign Logical
   and cross-Store objects locally, and enforce normal operator input contracts.
6. B's graph shows A's input as a boundary with the actual owner; A's graph does
   not acquire B's Run or producer edge.

### Action-scoped sample materialization

1. Materialize a logical Dataset whose Population is sampled inside the action.
2. Assert the realized sample is evaluated once and committed in Artifact
   authority.
3. Reconstruct the same logical definition in the same Session and assert its
   binding recovers the same Artifact without resampling.
4. Reconstruct it in a new Session and assert a new execution occurs; an equal
   policy or seed does not prove cross-Session realized-membership equivalence.

### Same-Session write exclusion

1. Start an `execute()` and hold its Session guard through backend execution.
2. Start a second top-level write in the same Session, testing both the same and
   a different execution key, another process, another thread, and reentrancy.
3. Assert immediate Session-busy failure, no new Run, no wait queue, and no second
   datasource execution. Metadata-only history and Artifact reads still work.
4. After the first action finishes, explicitly retry the same key and prove it
   returns the bound Artifact with no new Run.

### Cross-Session independence

1. Start long backend work in Session A and execute another key in Session B.
2. Prove backend work overlaps while each publication is a short complete SQLite
   transaction. No project execution lock or cross-Session resource reuse occurs.
3. Leave A with an unresolved object write; B admits work while A waits for
   publication safety. Then leave only harmless garbage in A and prove
   A also admits new work without losing its cleanup obligation.
4. Race creation/activation by name and verify one canonical Session per name,
   transactional current-pointer updates, and stable ownership of existing handles.

### Failed execution and explicit retry

1. Fail an execute action before publication and prove external execution terminal.
2. Assert cleanup and the failed terminal, with no published Artifact/Evidence.
3. After releasing the Session guard, retry explicitly and obtain a new Run.
4. Assert no failed Run is rewritten and the key binds at most one Artifact.

### Crash before metadata commit

1. Crash after finalized immutable storage exists but before publication commits.
2. Acquire that Session's lock in a fresh process; no persisted lease is needed.
3. Prove surviving external work terminal before cleanup; unavailable proof keeps
   the Run incomplete and blocks only its own Session.
4. Clean the exact obligations and fail the same Run with `process_lost`.
5. Assert no partial Artifact, Evidence, or Finding becomes visible.

### Crash after commit and lost acknowledgement

1. Crash immediately after metadata commit but before returning the Dataset.
2. Assert Artifact, descriptor, Evidence, Findings, and Run success already exist
   together, and no output cleanup obligation remains.
3. Read the same Artifact in a fresh process without terminal rewrite, index
   repair, datasource execution, or a second Artifact.
4. Inject a commit acknowledgement error and prove readback detects success.
5. Make readback unavailable and prove no output deletion or guessed failed
   terminal occurs; a later reader resolves the existing database outcome.

### Temporary-resource cleanup

1. Force a sampled Population plan to use a single-evaluation temporary
   relation fence.
2. Validate cleanup on success, execution failure, guard failure, cooperative
   cancellation, and Session-guarded process-loss recovery.
3. Inject harmless deletion failure after terminal proof; assert valid output
   commits, its Run succeeds, journal rows remain, and another Run can execute.
4. Assert later cleanup targets only the exact journaled locator and nonce.

### Retained rows and explicit semantic enrichment

1. Materialize a Dataset, then remove an original semantic definition.
2. Execute a retained-field operator and assert it reads the fixed leaf without
   catalog reinterpretation or a current-dependency comparison.
3. Request one admitted current Dimension enrichment and verify only its exact
   source/path is joined to the retained input; the changed historical catalog
   alone does not veto the operation.
4. Make that requested path missing, multi-valued, or type-incompatible and
   assert a structured input-check failure, with no implicit alternate path.
5. Assert neither operation replays the leaf's origin graph and no requirement
   record, mode count, or successful-check audit is stored or exposed.

### Agent-owned reuse judgment

1. Change origin rows after materialization while preserving Artifact backing.
2. Read the exact result in the same Session and explicitly in another; neither
   checks source state or treats age as an admission rule.
3. Full inspection exposes exactly three integrity axes and no semantic/source
   comparison, source token request, or reuse verdict.
4. Show original Session, producer admission/finish times, commit time, schema,
   and lineage as facts; no timestamp label implies data coverage or freshness.
5. Missing/mutated backing and invalid operator input shapes still fail explicitly
   without silent recomputation or a general reuse score.

### Strict Evidence atomicity

1. Inject failure in quality, Finding extraction, Artifact insertion, Evidence
   insertion, Finding insertion, terminal insertion, and metadata commit separately.
2. Assert each action returns no materialized Dataset.
3. Assert no committed Artifact row or partial Evidence is visible.
4. Assert unpublished storage is cleaned from exact journal reservations;
   descriptor/Evidence/Finding/terminal rows roll back together with no sidecar.

## Implementation Evidence Required

The Public Cutover Plan must require at least:

- exact schema decode and future/old-generation rejection tests;
- optional diagnostics tests proving no compiler-audit type/payload, Ibis
  expression, generated SQL, or private graph enters Run state or gates success;
- Run variant and illegal-transition property tests;
- proof that incomplete Run admission precedes live profile resolution,
  compilation, datasource statements, transfers, and resource creation;
- local, engine, and object receipt round trips and mutation detection;
- exact row-count validation for every receipt family;
- publication failure injection at every ordered boundary;
- changed source rows and absent version metadata trigger no reuse check,
  snapshot capture, or automatic recomputation;
- atomic bundle visibility and lost-acknowledgement readback tests;
- uncommitted process-loss cleanup and terminal failure tests;
- contradictory committed metadata, missing storage, and corrupt Evidence
  fail-closed tests;
- exact execution-key and write-once binding tests, including action-scoped
  sampling and sources without version metadata;
- source-binding tests proving construction-time capture, execution after scope
  exit, changed-value key separation, same-value cold reconstruction recovery,
  no action-time ambient lookup, and exhaustive raw-value redaction;
- same-Session different/same-key, cross-process/thread, and reentrant writer
  rejection tests, plus lock release on failure and child-process isolation;
- cross-Session concurrent execution and blocked-Session isolation tests;
- raced Session creation/activation and stable current-pointer/handle tests;
- reservation-before-create crash injection and idempotent cleanup tests;
- pure memory buffers create no journal rows; spilled resources do;
- backend-work-survives-owner tests proving new work is blocked in that Session
  until old execution is terminal/fenced; harmless garbage alone never blocks;
- Materialized Dataset `show()` and `to_pandas()` no-origin-replay and no-new-Run
  tests;
- strict zero-Finding Evidence and extractor-failure atomicity tests;
- exact input/node validation and no-fallback tests;
- revalidation axis independence tests, including unreadable Artifact metadata
  and Evidence authority as `unverifiable` rather than `invalid`;
- cold scan-leaf recovery without logical origin tests;
- Session graph produced/binding-recovery/input-edge tests;
- redaction and bounded-payload adversarial tests;
- complete streaming-persistence tests, variable-width values without
  static total-size proof, exact streamed counts, and no whole-result buffering;
- source/Artifact-to-pandas Arrow tests that reject a bad batch before consumer
  admission, validate all required parts and combined inputs, and account decoded
  Arrow-buffer and conversion bytes deterministically;
- direct private DataFrame continuation tests covering output schema, key/null/
  alignment semantics, intermediate expansion and absence of per-operator Arrow
  round trips or internal DuckDB connections;
- Runtime Parquet staging tests for file/resource guards, schema drift,
  reservation-before-create, cleanup, and proof that no receipt, Artifact,
  Evidence, execution-key Artifact row, or graph node is created;
- caller-process, numerical-buffer and datasource-resource crash/cleanup tests
  proving only the validated root output may enter the configured durable target;
- source-prefix pushdown tests and admitted multi-input pandas tests proving
  support-based boundaries before data work, no failed-compilation probing, no
  local-to-source upload, and no source-only semantic work moved locally;
- local and object receipt tests proving first-cutover durable file storage uses
  the exact versioned Parquet contract and cannot alias exchange staging;
- explicitly configured engine/object high-cardinality journeys proving no
  unbounded in-memory transfer and no automatic target switching;
- deterministic receipt identity and optional diagnostic redaction tests;
- current English/Chinese Help, docs, skill, and card drift tests owned by the
  cutover module;
- real-agent journeys that execute, recover in a fresh process, inspect
  Run/Artifact facts, inspect full integrity, and continue analysis from the
  immutable leaf.

Local process health, a successful backend query, a staged file, an Evidence
row without its complete publication bundle, or a Run transcript is not acceptance. The terminal proof
is a recoverable same-family Dataset backed by one exact committed Artifact, or
one terminal failed Run with no partial publication.

## Acceptance Criteria

This design is complete when all of the following are reviewable without
consulting implementation guesses:

1. every producing execution kind and lifecycle transition is exact;
2. pre-admission and post-admission work are distinguishable;
3. Run, Artifact descriptor, receipt, Evidence, and external-obligation
   schemas are closed and versioned, and the Artifact
   execution-key uniqueness contract is exact;
4. source lineage and execution timing carry no freshness, equivalence, or
   reuse-suitability guarantee;
5. local, engine, and object storage have durable immutable receipt contracts;
6. exact row count is mandatory for every materialized Artifact;
7. commit ordering identifies one irreversible publication decision;
8. pre-commit failure publishes nothing;
9. committed success survives process loss or lost acknowledgement unchanged;
10. repeated exact execution reuses one Artifact per Session execution key;
    overlapping writes are rejected within a Session and permitted across Sessions;
11. unversioned sources and action-scoped stochastic samples remain pinned by
    their same-Session binding without claiming cross-Session equivalence;
12. Materialized `show()` and `to_pandas()` read only the committed Artifact and
    create no analysis Run;
13. every committed Artifact has complete quality and Evidence authority;
14. recoverable external resources are journaled before creation; each row is
    deleted only after exact obligation discharge or atomic output transfer;
    succeeded/failed Runs may retain harmless maintenance obligations;
15. recovery never re-executes analysis or guesses through conflicting state;
16. materialized scan leaves cannot reach through to origin plans;
17. exact inputs and calculation nodes are fixed without runtime substitution;
18. full inspection keeps Artifact, storage, and Evidence integrity independent
    with no semantic/source comparison or reuse verdict;
19. Session graphs expose committed causality without operational coordination
    state;
20. the persistence cutover has no migration, alias, or dual-read path;
21. explicit same-Store cross-Session inputs preserve original Artifact identity
    while only the consuming Session owns and locks its new computation;
22. external resources and recoverable executions are reserved before creation
    or submission;
23. cold recovery admits no new work in its Session while old backend writes
    remain possible; harmless garbage does not block work in any Session;
24. Store v5, `storage_selection`, and unverifiable integrity states are exact
    closed contracts;
25. every producing definition resolves one exact
    `DatasetMaterializationContractV1` before Run admission, while family owners
    define its semantic checks and Module 4 alone owns invocation and atomic
    publication;
26. source/Artifact Arrow boundaries, private pandas continuations and Runtime
    staging are execution representations governed by exact contracts, not
    Dataset authority;
27. no exchange file, private DataFrame or numerical buffer can receive a
    storage receipt, Artifact ref, Evidence envelope, execution binding, or
    Session graph node;
28. every private local-execution resource is reserved, journaled where
    applicable; harmless deletion failure leaves exact obligations without
    blocking valid output or later work;
29. first-cutover local and object Dataset file receipts use one exact versioned
    Parquet contract, while engine receipts remain immutable relations;
30. the complete root Artifact includes primary output and all required retained
    parts; private intermediate exchanges are never independently published;
31. parameterized source bindings are immutable logical-definition inputs,
    participate in `DatasetExecutionKeyV1` by exact digest, and are never
    resolved from ambient Session state at action time;
32. persisted runtime, authority, Evidence, graph, diagnostic, and disclosure
    records contain no raw source-binding values;
33. eligible contiguous operators compose in Ibis before a support-selected
    pandas terminal suffix; dependent successors never return to SQL, and no
    internal DuckDB executor or failure fallback exists;
34. every local consumer validates complete required primary/part operands before
    invocation, preserving ownership, alignment and exact values;
35. local/object Parquet continuation and full collection execute in the caller,
    without resource budgets or workers; atomic publication and recovery remain.

## Owner-Confirmed Module Decisions

The following is the current decision set after the owner's 2026-09-07
source-pushdown and pandas amendment, retaining the earlier single-writer,
Session-level locking and atomic publication decisions:

1. One Agent writes each Session serially. A non-blocking Session writer guard
   covers each whole action; different Sessions can execute concurrently.
2. The project-level v5 SQLite Store owns all Session, Run, Artifact descriptor,
   Evidence, Finding, and external-obligation metadata. No metadata sidecar or
   independent Evidence database participates in publication.
3. Immutable external storage is finalized before one metadata transaction
   publishes Artifact, Evidence, Findings, succeeded terminal, and output
   obligation removal. The transaction commit is the publication decision.
4. There are no claims, leases, heartbeats, per-Run locks, contender queues,
   timed wait-and-reuse, independent markers, or cross-Store repair gaps.
5. Only a binding miss under the Session guard admits a Run. Immutable admission
   and optional immutable terminal project `incomplete -> succeeded|failed`;
   retry after failure creates a new Run.
6. The Artifact unique `(session_ref, execution_key_digest)` key is the only
   binding. It cannot redirect, refresh, or be deleted independently.
7. The key adds only common materialization protocol version to Core's canonical
   definition fingerprint; Store lookup supplies Session scope. Core normalizes
   source/semantic/input/producer dependencies and significant realization
   sharing once. Live source rows, realized samples, raw realization handles,
   placement, and script identity do not enter reuse lookup. A separately sampled
   comparison cannot satisfy a shared-sample comparison; reconstruction of the
   same sharing relation in a fresh process recovers its own exact binding.
8. Only process-local pure checks precede guarded recovery and binding lookup.
   Read-side validation may access committed storage without a Run; a new
   computation requires admission before live authority resolution or execution.
9. All committed Artifacts have complete quality and Evidence, including a valid
   zero-Finding envelope. Family-owned contracts define checks and extraction;
   Module 4 owns invocation and atomic publication.
10. Local, engine, and object receipts are the only durable storage family.
    Local storage streams under separate batch-memory and total-disk guards; engine
    storage requires a registered immutable relation protocol.
11. Module 4 supplies one configured storage target and validates its writer
    against the fixed output domain. There are no candidate ranks or two-phase
    selection callbacks. Primary data and required parts publish as one Artifact.
12. Ibis executes the maximal supported source prefix. An exact registered pandas
    terminal suffix consumes complete guarded Arrow inputs and required parts;
    its steps pass validated private DataFrames directly. Selection precedes data
    work, and failures never change implementations. DuckDB is an ordinary
    datasource only. Runtime owns staging, cancellation and cleanup. Local/object
    storage retains its exact Parquet contract independently of temporary files.
13. Temporary exchanges and workspaces never acquire Dataset, receipt, Evidence,
    binding, or Graph authority. Pure memory buffers have no journal rows;
    resources that survive process loss have exact durable reservations.
14. Resource locators and ownership nonces are reserved before external side
    effects. All executions must be terminal/fenced before publication; harmless
    garbage can remain journaled after success without blocking new work.
15. Every writer reconciles only its own Session's prior incomplete execution
    and cleanup obligations. Session-lock acquisition proves no remote query
    publication safety; unresolved writers or object writes block that Session alone.
16. Proven uncommitted interruption becomes failed after guarded ownership recovery;
    harmless uncleaned resources remain journaled.
    Committed success needs no index repair or terminal rewrite. Uncertain
    acknowledgement is resolved through authoritative Store readback.
17. Recovery never resumes stages or reconstructs the Logical graph. Scripts
    rebuild definitions; Artifact reads recover exact immutable scan leaves.
18. Materialized `show()` and `to_pandas()` are read-only, create no Run, and
    never replay origin SQL. All registered downstream operators create new
    Logical Datasets over the immutable leaf.
19. Operator contracts and compiled nodes own input checks without generic
    requirement records or authority audits. No input fallback is allowed.
    Full inspection keeps Artifact, Evidence, and storage checks independent;
    no semantic/source comparison or reusability axis remains.
20. Source lineage and execution times are factual only. Source changes never
    redirect bindings; Agent judgment owns reuse suitability. Source-authority
    variants and reusable snapshot certificates are removed.
21. Raw source-binding values stay process-local; no Run, metadata, Evidence,
    Finding, graph, error, or disclosure persists them. Only exact opaque binding
    digests enter execution identity.
22. Session timezone facts live in `sessions`; warning text is derived. The shared
    current pointer is transactional navigation state, never execution authority.
23. Graph reads project one Store snapshot without recovery or Finding-body scans.
    External inputs are boundary Artifacts with their actual owners; only local
    Runs enter the Session graph. Coordination remains private.
24. The clean Store generation is `user_version = 5`; older state is neither
    decoded nor migrated. Public deletion waits for a recoverable metadata and
    external-storage deletion contract.
25. Local/object Artifact readers use authorized PyArrow or a registered native
    Parquet scan. Computation validates complete typed inputs and semantic
    authority without Marivo resource admission.
26. An admitted multi-input pandas method may collect separate source outputs
    under combined semantic and privacy checks. There is no generic
    federation or unregistered substitute for identity and semantic work.
    Native Parquet scans do not recover missing origin semantics.

Changes require an explicit amendment before downstream modules rely on them.

## Owner Confirmation

The 2026-09-01 through 2026-09-04 decisions established the Dataset action,
storage, Evidence-completeness, exchange, and generation contracts retained
above. On 2026-09-05 the owner first removed duplicated Run
and binding state, then approved a single-writer design and unified metadata
publication. The owner subsequently selected Session-level locking instead of
Store-level locking.

This revision retains that scope: one lock per Session across its whole
write action, independent execution/recovery across Sessions, and short shared
SQLite transactions. It supersedes the earlier independent Evidence database,
Artifact metadata file, cross-Store marker, claims, leases, and contender
protocols. Those superseded choices are not implementation alternatives.

The owner then assigned freshness and reuse suitability to the Agent. This
revision removes generic source-authority certificates and datasource freshness
revalidation, and permits explicit same-Store cross-Session Artifact reads and
inputs with original ownership and lineage. Foreign Logical Datasets remain
Session-bound; no copying, reparenting, or automatic matching is introduced.

On 2026-09-07 the owner selected a simpler lazy calculation model: push eligible
contiguous operators to Ibis sources, then execute the admitted terminal suffix
in pandas. This revision removes DuckDB's internal analysis role and its large
local Artifact continuation promise. Source/Artifact ingestion uses Arrow,
local steps pass private DataFrames, and Parquet remains a storage protocol.
Source-only semantic and identity requirements, fixed pre-data support decisions,
no failure fallback, complete local resource guards and publication authority
remain explicit constraints.

This is an accepted design amendment, not evidence that the lazy runtime or
its acceptance journeys are implemented. No runtime code changes or release
operations are authorized by this document revision alone.

## Final Boundary

The runtime does not make a Logical Dataset durable by serializing its plan. A
script reconstructs the definition; the named Session resolves its exact
execution key to one realized Dataset. The runtime makes that result durable by
committing exact immutable storage, quality, Evidence, Findings, metadata,
Session causality, and one unique execution-key Artifact registration as one
recoverable authority decision.

Before that decision, new output is private staging and failure publishes
nothing. After that decision, the same Run is already succeeded and recovery
preserves the same Artifact. A downstream action consumes only the immutable
scan leaf or explicitly selected current semantic authority; it never reaches
through a committed Dataset to replay its origin.

## Current Review Amendments

The owner approved operation-scoped reads, Artifact-owned retained parts, streaming
local persistence, and non-blocking harmless cleanup on 2026-09-05. That amendment
removes mandatory planning audits and semantic revalidation, gives Dataset Core one
canonical execution-definition fingerprint, and removes pre-execution binding
status from cards. The 2026-09-07 amendment adds maximal supported Ibis source
prefixes and a bounded pandas terminal suffix, direct local DataFrame handoff,
and PyArrow Artifact reads, while removing the internal DuckDB executor. All
acceptance criteria above use this single current contract; historical executor
choices are not implementation alternatives.

### Slice 5d distribution preparation

Metric and Delta distribution parts are independently sized, engine-private
value-frequency relations published with the existing immutable engine
receipts. Source-native checks validate exact schema, positive finite support,
unique value keys, coordinate support and endpoint reproduction. Generic
Arrow/pandas readers reject their roles and receipts before reading payloads.
Consumption and full integrity inspect required parts; ordinary primary reads
do not consume unused distributions.

The existing physical stage graph gains one closed distribution preparation
input. Its stream is not a Dataset or an Artifact: it carries one value per
scope/resolution/coalition, a consistent typed non-identity player inventory,
player count and independent endpoints. The complete input is validated before
exact local Shapley combination. Missing coalitions, duplicate players,
inconsistent inventories/endpoints or incomplete resolutions fail atomically.
Run resources, cancellation, source realization and receipt rechecks retain
the existing owners; no separate publication or recovery mechanism is added.

## 2026-09-15 amendment: Slice 1c execution and recovery

Shared execution accepts Ibis expressions as well as explicit concrete driver
statements. Ibis owns normal compilation, parameters and preparation hooks;
DuckDB retains its useful direct SQL/fence and typed transport implementation.
There is no compile-count gate, persisted physical plan or engine-version
certification. Actual submissions, including validation and preparations, are
captured by role rather than inferred from a diagnostic compile.

Store generation 5 replaces generation 4 resource semantics without migration
or dual reading. Read-only execution obligations carry exact action ownership
but do not certify server death. Failed cancellation, close or missing query ID
cannot override the original execution exception. A failed Run means no local
successful publication. S3 write proofs, surviving publisher exclusion, exact
cleanup, commit readback and atomic primary/part publication remain mandatory.

The Slice 1/1a/1b records describe their historical implementations, not acceptance
of these simplified contracts. Slice 1c has its own evidence record and does not
activate any remote backend.

## 2026-09-15 amendment: Slice 2 selected backend operations

The physical graph carries the selected backend registration and one exact
source/preparation operation. Runtime checks this selection against the declared
source binding before opening its connection; no execution failure changes the
selection. Only the existing DuckDB operations are enabled. Retained Parquet
imports require the existing native DuckDB reader, not merely a sole source
candidate. Source-domain equality and action-local execution ownership retain
their separate checks, independent of version diagnostics.

An immutable binding hit returns before placement and source resolution. On a
miss, known unsupported methods/backends/shapes fail before Run admission; live
physical schema checks still precede source-row computation. The registry
extension changes neither the execution key nor Store generation 5 publication.
