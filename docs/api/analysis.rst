marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Construct typed algebra through ``session.members(entity_ref)`` and its owned
read/observe operations. Event and Lifecycle remain separately owned migration
contracts. Logical Datasets describe work
without source I/O. ``execute()`` commits a Run and returns an immutable
Materialized Dataset. Its owned fields and methods describe valid continuations.
Use ``show()`` for bounded current state and ``contract()`` for mechanical input
requirements. ``to_pandas()`` is the terminal boundary for custom analysis.

Execution retains results as project-local Parquet. There is no analysis result
storage setting or database result storage. Session recovery reads Store 7;
older generation files are not migrated or rewritten.

Start discovery with ``marivo.help("analysis")``. Focused Help owns signatures,
examples and constraints; errors preserve concrete diagnostics and own repair.
Entry provides Session bootstrap/recovery and source selection. Named method and
input groups narrow discovery by task. ``dataset.contract().show()`` links current
admitted calls to their canonical Help leaves; return types and prerequisites
provide focused continuations without enumerating the entire API. Exact current semantic
refs or catalog entries select governed inputs, while Dataset field refs carry
exact Dataset ownership. Cross-Session Dataset operands are rejected.

The admitted J1–J4 Entity-domain path starts at ``session.members(entity_ref)``.
Its logical relations expose ``execute()`` and ``contract()``; materialized
relations expose ``show()``, ``to_pandas()`` and ``contract()``. The public
``AnalysisContract.actions`` tuple contains typed ``AnalysisAction`` values
with the receiver call and exact Help target. The combined result card redacts
Entity member keys. ``python -m marivo help`` is only the installed-interpreter
bootstrap; focused contracts remain in ``marivo.help(...)``.

Entity-domain values
--------------------

.. autoclass:: AnalysisAction
   :members:

.. autoclass:: AnalysisContract
   :members:

.. autoclass:: ReferenceWeights
   :members:

.. autofunction:: reference_weights

.. autoclass:: LogicalAnalysisDomain
   :members:

.. autoclass:: MaterializedAnalysisDomain
   :members:

.. autoclass:: LogicalNumericRelation
   :members:

.. autoclass:: MaterializedNumericRelation
   :members:

.. autoclass:: LogicalRatioRelation
   :members:

.. autoclass:: MaterializedRatioRelation
   :members:

.. autoclass:: LogicalAssociationResult
   :members:

.. autoclass:: MaterializedAssociationResult
   :members:

.. autoclass:: RowMethod
   :members:

Member versions and scalar attributes
-------------------------------------

``Session.members(entity, at=...)`` keeps complete ordered keys. Versioned
Entities require an aware instant or ``TimeScope.before_end``. Attribute
``read(field, at=..., via=...)`` binds its version independently and returns
Numeric, Category, Boolean or Temporal relations. Scalar paths require complete
single-valued correspondence and coverage. Fixed member projections retain
Subject parts and cannot introduce a live attribute read.
Measure reads admit direct columns and qualified bound row expressions. A
computed Measure evaluates through Ibis on the consumed owner rows, and its
definition and bound-field fingerprints are retained in the graph.

.. autoclass:: BeforeEndBoundary
   :members:

.. autoclass:: LogicalBooleanRelation
   :members:

.. autoclass:: MaterializedBooleanRelation
   :members:

.. autoclass:: LogicalTemporalRelation
   :members:

.. autoclass:: MaterializedTemporalRelation
   :members:

.. autoclass:: LogicalSelectedBooleanRelation
   :members:

.. autoclass:: MaterializedSelectedBooleanRelation
   :members:

.. autoclass:: LogicalSelectedTemporalRelation
   :members:

.. autoclass:: MaterializedSelectedTemporalRelation
   :members:

.. autoclass:: LogicalSelectedNumericRelation
   :members:

.. autoclass:: MaterializedSelectedNumericRelation
   :members:

Public exports
--------------

The following entries follow the pinned public export order. Case-colliding
constructors are documented inline to support case-insensitive filesystems.

.. autoclass:: Dataset
   :members:

.. autoclass:: LogicalDataset
   :members:

.. autoclass:: MaterializedDataset
   :members:

.. autoclass:: DatasetShapeId
   :members:

.. autoclass:: DatasetFieldId
   :members:

.. autoclass:: DatasetFieldIdentity
   :members:

.. autoclass:: DatasetPhysicalTypeState
   :members:

.. autoclass:: DatasetField
   :members:

.. autoclass:: DatasetRowBound
   :members:

.. autoclass:: DatasetCardinality
   :members:

.. autoclass:: DatasetOrderTerm
   :members:

.. autoclass:: DatasetOrdering
   :members:

.. autoclass:: DatasetByteCount
   :members:

.. autoclass:: DatasetFamilyRowSemantics
   :members:

.. autoclass:: DatasetRowContract
   :members:

.. autoclass:: DatasetRowSetContract
   :members:

.. autoclass:: DatasetSchema
   :members:

.. autoclass:: LogicalDatasetState
   :members:

.. autoclass:: MaterializedDatasetState
   :members:

.. autoclass:: DatasetContract
   :members:

.. autoclass:: DatasetFields
   :members:

.. autoclass:: DatasetFieldRef
   :members:

.. autoclass:: LogicalPopulationDataset
   :members:

.. autoclass:: MaterializedPopulationDataset
   :members:

.. autoclass:: LogicalMetricDataset
   :members:

.. autoclass:: MaterializedMetricDataset
   :members:

.. autoclass:: LogicalAssociationDataset
   :members:

.. autoclass:: MaterializedAssociationDataset
   :members:

.. autoclass:: LogicalForecastDataset
   :members:

.. autoclass:: MaterializedForecastDataset
   :members:

.. autoclass:: LogicalCandidateDataset
   :members:

.. autoclass:: MaterializedCandidateDataset
   :members:

.. autoclass:: LogicalEventDataset
   :members:

.. autoclass:: MaterializedEventDataset
   :members:

.. autoclass:: LogicalLifecycleDataset
   :members:

.. autoclass:: MaterializedLifecycleDataset
   :members:

.. autoclass:: AnalysisPredicate
   :members:

.. autoclass:: ForecastHorizon
   :members:

.. autoclass:: ForecastModel
   :members:

.. autoclass:: WindowBucketAlignment
   :members:

.. autoclass:: BoundedCompletenessDeclarationV1
   :members:

.. autoclass:: SourceOriginCompletenessDeclarationV1
   :members:

.. autoclass:: DroppedBefore
   :members:

.. autoclass:: EventPattern
   :members:

.. autoclass:: EveryStart
   :members:

.. autoclass:: FirstPerSubject
   :members:

.. autoclass:: FromInception
   :members:

.. autoclass:: FunnelLossRate
   :members:

.. autoclass:: Grain
   :members:

.. autoclass:: InState
   :members:

.. autoclass:: PatternStep
   :members:

.. autoclass:: TimeScope
   :members:

.. autoclass:: ArtifactDigest
   :members:

.. autoclass:: ArtifactRef
   :members:

.. autoclass:: ArtifactRevalidation
   :members:

.. autoclass:: ArtifactSummary
   :members:

.. autoclass:: EvidenceIntegrityError
   :members:

.. autoclass:: FailedRun
   :members:

.. autoclass:: Finding
   :members:

.. autoclass:: FindingPage
   :members:

.. autoclass:: IncompleteRun
   :members:

.. autoclass:: RunPage
   :members:

.. autoclass:: SessionGraph
   :members:

.. autoclass:: SucceededRun
   :members:

.. autoclass:: Session
   :members:

.. autofunction:: eq

.. autofunction:: not_eq

.. autofunction:: lt

.. autofunction:: lte

.. autofunction:: gt

.. autofunction:: gte

.. autofunction:: is_in

.. autofunction:: is_null

.. autofunction:: is_not_null

.. autofunction:: all_of

.. autofunction:: any_of

.. autofunction:: not_

.. autofunction:: grain

.. autofunction:: time_scope

.. autofunction:: window_bucket

.. autofunction:: step

.. autofunction:: sequence

.. autofunction:: first_per_subject

.. autofunction:: every_start

.. autofunction:: dropped_before

.. autofunction:: in_state

.. autofunction:: funnel_loss_rate

.. autofunction:: from_inception

.. autofunction:: periods

.. autofunction:: naive

.. autofunction:: drift

.. autofunction:: seasonal_naive

.. automodule:: marivo.analysis.runtime_metric
   :members:

.. automodule:: marivo.analysis.session
   :members:

R4.5 qualification boundary
----------------------------------------

Public J1–J4 relations share the typed graph Runtime and Store 7. R1 schema-only
preflight may precede Run allocation; business rows are read only after admission.
Exact Artifact recovery verifies its snapshot, primary receipt and required parts
without current Semantic or datasource access. R5–R9 Dataset families retain
their signatures but reject execution before business reads and Run allocation.
Old-generation projects are preserved and are not migrated or read through a fallback.

Ranking and terminal tables
---------------------------

.. currentmodule:: marivo.analysis

``values.rank(order="descending", ties="dense", partition_by=(category,))``
returns a ranking with fixed ``values`` and ``ranks`` numeric views. Filtering
and global ``limit`` preserve original ranks, ordering and fixed references.
For per-partition Top-K, filter ``ranking.ranks.value.is_defined()`` first, then
filter ``selected.ranks.value.lte(k)`` on that selected result.

``mv.table(amount=ranking.values, rank=ranking.ranks)`` requires complete equal
keys, one Session and one source/fixed mode. The terminal Artifact exposes
``artifact_ref``, bounded ``show()`` and an isolated ``to_pandas()`` containing
keys once and authored value columns. Non-Defined values export as missing;
only the Artifact and ``show()`` preserve their tags and reasons. Tables have
no analysis contract or column attributes.

.. autoclass:: LogicalRankingResult
   :members:

.. autoclass:: MaterializedRankingResult
   :members:

.. autofunction:: table

.. autoclass:: LogicalTable
   :members:

.. autoclass:: MaterializedTable
   :members:

Allocated absolute changes
--------------------------

``change.attribute(axes=(channel,), mode="joint", top_k=5)`` chooses the
allocation method from the original endpoint states. Sum/count/linear changes
use additive differences; mean/weighted_mean/original ratios allocate each
numerator against its side's total denominator before subtracting. Logical
inputs may explicitly expand the frozen observations; fixed inputs require
the axes already retained. Relative/nested changes and direct-only aggregates
cannot acquire allocation authority.

``joint`` emits complete axis tuples. ``hierarchy`` requires at least two axes
and emits each authored prefix. A common Top-K mapping uses both endpoint bases;
typed remainder masks distinguish a real ``"Other"`` label. Every resolution
reconciles independently, with no balancing residual. The ``contribution``,
``current`` and ``baseline`` numeric views always share complete keys.
``where`` keeps the original target, full allocation and reconciliation parts
and revokes current-subdomain completeness even when every row remains selected.

.. autoclass:: LogicalAttributionResult
   :members:

.. autoclass:: MaterializedAttributionResult
   :members:

R6 recovery and retired consumers
---------------------------------

Comparison, selection/cohort, fixed references, ranking, attribution and terminal
tables use one typed graph, Runtime and Store 7. ``session.artifact(ref)`` verifies
complete state before fixed continuation. Missing parts revoke the affected
operations; recovery does not consult Semantic or lineage sources. Tables remain
terminal. Original reductions with group keys publish
``MaterializedGroupedNumericRelation``; Singleton reductions publish
``MaterializedRolledNumericRelation``. Cold recovery preserves that contract.

The old Metric Dataset ``compare`` and public Delta/Attribution Dataset types
are removed. Their Help targets are unresolved, with no compatibility redirect.
The private Event funnel Delta/Attribution families and their exclusive codecs
are removed. The public Journey funnel chain below uses registered graph methods
and Store 7; fixed continuations never reopen the source or rerun matching.

R7.4 funnel results and frozen Findings
---------------------------------------

``journeys.funnel(axes=())`` consumes the canonical first-per-subject assignment.
Seven exact int64 counts and three rates retain their original components.
Receiver-owned field handles feed ``read(handle)``; ``funnel_loss_rate(step=...)``
requires an exact noninitial step. Historical axes use the entry-time version,
complete governed paths and true Null categories.

``current.compare(baseline)`` requires the same explicit population, exact
pattern/matching/definitions, equal windows/follow-up lengths and complete
coverage. It pairs complete outer support. ``change.attribute(target=..., axes=...)``
uses exact Fraction ratio-mix allocation, common Top-K and typed Other; logical
axis expansion depends on the same assignment, while fixed missing axes reject.
Filtering retains original reconciliation scope and drops complete partition claims.

Materialized graph results expose ``evidence_digest()``, ``findings(limit=20,
cursor=None)`` and ``finding(finding_id)``. Compare and allocation use frozen
extractors capped at 1000, preserving eligible/emitted/truncated authority.
Every read validates the full collection, bodies, bindings, versions and receipts.
Artifact, Evidence, Findings and successful terminal publish atomically in Store 7.
Other producers use the explicit zero-Finding policy.

.. autoclass:: LogicalFunnelResult
   :members:

.. autoclass:: MaterializedFunnelResult
   :members:

.. autoclass:: LogicalFunnelComparisonResult
   :members:

.. autoclass:: MaterializedFunnelComparisonResult
   :members:
