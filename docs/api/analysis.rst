marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Construct typed algebra through ``session.members(entity_ref)`` and its owned
read/observe operations. Journey, History, Anchor and retention use the unified
graph. The former Population, Metric Dataset and generic Dataset APIs have been retired.
Logical typed values describe work
without source I/O. ``execute()`` commits a Run and returns an immutable
Materialized value. Its owned fields and methods describe valid continuations.
Use ``show()`` for bounded current state and ``contract()`` for mechanical input
requirements. ``to_pandas()`` is the terminal boundary for custom analysis.

Business data display uses ``show(*, n=None, max_output_bytes=8192)`` on
materialized values and tables. The default fits complete rows within 8 KiB;
there is no fixed five-row cap. ``n=20`` requests at most twenty rows, ``n=0``
shows metadata only, and ``max_output_bytes=None`` removes the byte cap while
preserving an explicit ``n``. Counts and recovery hints disclose display
omissions. Budgets include the printed newline. Values preserve precision;
Duration cells show integer ticks with their unit. Cell state counts cover the
whole result. The card shows interpretation facts and conditional evidence
reads; use ``contract()`` for the full operation directory.

Execution retains results as project-local Parquet. There is no analysis result
storage setting or database result storage. Session recovery reads Store 7;
older generation files are not migrated or rewritten.

Start discovery with ``marivo.help("analysis")``. Focused Help owns signatures,
examples and constraints; errors preserve concrete diagnostics and own repair.
Entry provides Session bootstrap/recovery and source selection. Named method and
input groups narrow discovery by task. ``dataset.contract().show()`` links current
admitted calls to their canonical Help leaves; return types and prerequisites
provide focused continuations without enumerating the entire API. Exact current semantic
refs or catalog entries select governed inputs, while bound relation fields carry
exact graph ownership. Cross-Session operands are rejected.

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

.. autoclass:: LogicalTimeRunResult
   :members:

.. autoclass:: MaterializedTimeRunResult
   :members:

.. autoclass:: LogicalDeviationResult
   :members:

.. autoclass:: MaterializedDeviationResult
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

.. autoclass:: DatasetByteCount
   :members:

.. autoclass:: MaterializedDatasetState
   :members:

.. autoclass:: LogicalHistoryResult
   :members:

.. autoclass:: MaterializedHistoryResult
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

.. autoclass:: ArtifactSummary
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
without current Semantic or datasource access. Retired Dataset families have no current API.
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

Tables retain the original fit authority for every deviation column, including
tables mixing scores with categories, original values or another fit. A fitted
ranking's ``ranks`` projection retains its original fit scope and its own rank
values; both logical and materialized projections expose the same retained parts
through ``contract()`` and ``show()``. Recovery verifies current values against
captured fitted and ranking parts without fitting again. Missing or corrupt
authority rejects with a typed repair.

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

Anchor domains and relative observation
---------------------------------------

``Session.anchors(event_role, population=members, during=scope, business_order=order)``
selects each Event start with its full Subject and occurrence identity. Journey
starts inherit their captured assignment and order; fixed Journey input requires
a compatible fixed population. ``during`` filters starts only.

``anchors.observe(metric, within=window, via=route)`` returns a NumericRelation
on the Anchor instance domain. Windows are half-open and exclude the exact Anchor
occurrence. Overlapping windows retain separate component-use bindings and cannot
be rolled up by discarding Anchor coordinates. Event elapsed observation uses Ibis;
calendar and local Journey observation prepare all bounded source candidates before
local window restriction. Fixed results continue through retained parts; a fixed
Anchor cannot introduce a new live Metric dependency.

.. autoclass:: Duration
   :members:

.. autofunction:: duration

.. autoclass:: ElapsedWindow
   :members:

.. autofunction:: elapsed

.. autoclass:: CalendarWindow
   :members:

.. autofunction:: calendar_days

.. autoclass:: LogicalAnchorDomain
   :members:

.. autoclass:: MaterializedAnchorDomain
   :members:

.. automethod:: LogicalAnchorDomain.observe

.. automethod:: LogicalAnchorDomain.subjects

.. automethod:: MaterializedAnchorDomain.observe

.. automethod:: MaterializedAnchorDomain.subjects

Retention and Subject quantification
------------------------------------

.. autofunction:: any_anchor

.. autofunction:: every_anchor

.. autoclass:: AnyAnchor
   :members:

.. autoclass:: EveryAnchor
   :members:

.. autoclass:: LogicalRetentionResult
   :members:

.. autoclass:: MaterializedRetentionResult
   :members:

.. autoclass:: LogicalSubjectRetentionResult
   :members:

.. autoclass:: MaterializedSubjectRetentionResult
   :members:

.. automethod:: LogicalAnchorDomain.retention

.. automethod:: MaterializedAnchorDomain.retention

.. automethod:: LogicalRetentionResult.by_subject

.. automethod:: MaterializedRetentionResult.by_subject

.. automethod:: LogicalRetentionResult.known_true

.. automethod:: LogicalRetentionResult.known_false

.. automethod:: LogicalRetentionResult.unknown

R8 Statistical Relations
------------------------

.. autoclass:: LogicalCoefficientRelation
   :members:

.. autoclass:: LogicalForecastResult
   :members:

.. autoclass:: MaterializedForecastResult
   :members:
