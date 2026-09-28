marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Construct analysis through ``session.observe``, ``session.population``,
``session.events`` or ``session.lifecycle``. Logical Datasets describe work
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

First-round Entity-domain values
--------------------------------

.. autoclass:: AnalysisAction
   :members:

.. autoclass:: AnalysisContract
   :members:

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

.. autoclass:: LogicalDeltaDataset
   :members:

.. autoclass:: MaterializedDeltaDataset
   :members:

.. autoclass:: LogicalAttributionDataset
   :members:

.. autoclass:: MaterializedAttributionDataset
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
