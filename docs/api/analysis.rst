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

Execution retains results as local Parquet by default. An explicit project
configuration may select object storage. Database result storage and automatic
storage or executor fallback are unavailable. Session recovery reads Store v3;
older Store generations are rejected without rewriting their files.

Start discovery with ``marivo.help("analysis")``. Focused Help owns signatures,
examples and constraints; errors own concrete repair. Exact current semantic
refs or catalog entries select governed inputs, while Dataset field refs carry
exact Dataset ownership. Cross-Session Dataset operands are rejected.

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

.. autoclass:: EntitySamplingPolicy
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

.. autofunction:: engine_sample

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
