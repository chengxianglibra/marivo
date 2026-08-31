marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

At qualifying catalog-bound runtime parameters, analysis accepts an exact
current ``marivo.semantic.CatalogEntry`` or its exact
``marivo.semantic.Ref``. The boundary validates ownership, kind, and current
membership, then normalizes immediately to the ref. Bare semantic strings,
stale or cross-catalog entries, arbitrary entry subclasses, and duck-typed
``.ref`` objects are rejected. Runtime metric constructors and nested Event
handles retain their existing exact input contracts.

Typed regression is not part of the current analysis operator surface. Work
that requires it remains explicit terminal custom analysis through
``frame.to_pandas()`` or ``md.raw_sql(...)``; neither terminal result can
re-enter typed Marivo analysis.

Help and agent surface
----------------------

At analysis entry, use the project interpreter to run
``python -m marivo help`` once and verify the environment fingerprint.
After entry, use the public object already in hand: ``show()`` reports current
state, while ``contract()`` describes mechanically valid next actions. Open
focused ``marivo.help("analysis.<target>")`` only when that object contract is
insufficient or before first use of an unfamiliar capability. After a failure,
follow the structured repair. Focused help is not required before every API
call.

Live help owns callable signatures, accepted input families, constraints, and
recovery guidance. Focused operator help also reports its closed Artifact
authority policy. ``semantic_current`` consumers reject confirmed scoped drift
with ``errors.ArtifactStaleError`` and unknown authority with
``errors.ArtifactAuthorityUnknownError`` before execution. ``materialized_only``
consumers retain committed-value and integrity checks without consulting the
current catalog. ``contract()`` remains mechanical and does not perform
currentness validation.

Frames
------

Public artifacts expose ``ref``, ``kind``, ``shape``, ``row_count``,
``columns``, ``show()``, ``contract()``,
``quality_summary``, ``evidence_status``, ``evidence_digest``, ``lineage``,
``state``, and ``to_pandas()``. ``row_count == shape[0]``.
``contract().issues`` contains typed issues; role-preserving affordances
describe mechanical compatibility only. A multi-metric contract exposes one
exact full-id ``frame.metric(...)`` projection repair per carried metric when a
consumer requires arity 1; it never selects one.

Every public value returned by ``.contract()`` has bounded ``repr``,
``render()``, and ``show()`` while retaining its structured fields. This is
structural conformance, not a shared public contract base class.

.. autosummary::
   :toctree: api/
   :nosignatures:

   BaseFrame
   BaseFrameMeta
   MetricFrame
   EventFrame
   LifecycleFrame
   SubjectSet
   ComponentFrame
   DeltaFrame
   CoverageFrame
   AttributionFrame
   ForecastFrame
   ArtifactAffordance
   ArtifactInputRequirement
   ArtifactColumn
   ArtifactContract
   ArtifactPrecondition
   ArtifactSchema
   ArtifactState

Analysis results and selections
-------------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   AssociationResult
   HypothesisTestResult
   CandidateSet
   PointAnomalySelection
   PeriodShiftSelection
   DriverAxisSelection
   SliceSelection
   WindowSelection
   CrossSectionalOutlierSelection
   OntologyMetricCandidate
   CandidateOrigin

Evidence
--------

``Finding`` is the typed audit record. ``ArtifactDigest`` is the bounded
operator-local read model; it never performs cross-artifact judgment. A
``Finding`` renders as one bounded evidence statement with
``finding.render()`` (English by default) or ``finding.render(language="zh")``.
``FindingPage.render()`` uses the same statements and retains each canonical
``finding_id`` for exact follow-up reads.

.. autosummary::
   :toctree: api/
   :nosignatures:

   Finding
   ArtifactDigest
   ArtifactRevalidation
   EvidenceRuleIssue
   AnalysisScope
   ObservationFact
   ChangeFact
   ContributionFact
   AssociationFact
   TestDecision
   ForecastOutput
   AnomalyCandidate
   QualityCheckResult
   DataQualityIssue
   ComparabilityIssue
   EvidenceAvailabilityIssue
   CandidateResolutionIssue

Bounded pages
-------------

Pages expose immutable ``items``, ``limit``, ``has_more``, and opaque
``next_cursor``. They use ordinary newest-first keyset semantics, not snapshot
isolation.

.. autosummary::
   :toctree: api/
   :nosignatures:

   FindingPage
   RunPage

Scopes and windows
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TimeScope
   AbsoluteWindow
   Grain

.. autofunction:: grain

Event Journey, Lifecycle replay, and typed cohorts
---------------------------------------------------

``session.events.match(...)`` consumes typed participant roles and a closed
``EventPattern``. The first step uses the half-open ``TimeScope`` cohort
window; ``completion_through`` is an inclusive follow-up bound.
``session.events.funnel(...)`` and ``session.events.time_to_event(...)`` reduce
the persisted journey assignment without rematching Events.
``session.select_subjects(...)`` materializes the closed
``dropped_before(...)`` selection as a persisted ``SubjectSet``. A ready
SubjectSet may scope ``observe(..., cohort=...)`` and
``events.match(..., cohort=...)``.

``session.lifecycle.replay(...)`` consumes one exact current StateModel
entry/ref, an explicit timezone-aware half-open window, and the explicit
``from_inception()`` seed. It returns ``LifecycleFrame[history]``. Lifecycle
reducers consume that persisted history without querying Event sources or
replaying the StateModel again. ``in_state(...)`` is the closed Lifecycle
selection used by ``session.select_subjects(...)``; a resulting ready
``SubjectSet`` may scope later metric, Event, or Lifecycle materialization.
Use focused ``marivo.help("analysis.lifecycle.replay")`` and the returned
artifact ``contract()`` for the current mechanical contract and continuations.
Before selecting a window, ``session.events.occurrence_bounds(...)`` returns
the observed earliest/latest occurrences of one exact Event or StateModel as
``EventOccurrenceBounds``; it does not establish completeness.

.. autosummary::
   :toctree: api/
   :nosignatures:

   PatternStep
   EventPattern
   FirstPerSubject
   EveryStart
   CompletenessDeclaration
   EventOccurrenceBounds
   DroppedBefore
   FromInception
   InState
   FunnelLossRate
   funnel_loss_rate
   EventWatermarkRequest
   EventWatermarkReceipt
   step
   sequence
   first_per_subject
   every_start
   declared_complete_through
   dropped_before
   from_inception
   in_state

Policies
--------

.. autosummary::
   :toctree: api/
   :nosignatures:

   AlignmentPolicy
   AlignmentKind
   SamplingPolicy

Refs and lineage
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ArtifactRef
   Lineage
   LineageStep

Session runtime
---------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   Session
   SessionSummary
   SessionGraph
   ArtifactSummary
   IncompleteRun
   SucceededRun
   FailedRun

Alignment and window helpers
----------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   window_bucket
   day_of_week
   period_progress
   period_correspondence
   occurrence_progress
   working_day_progress

Slices
------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SlicePredicate
   SlicePredicateOp

Submodules
----------

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - ``marivo.analysis.errors``
     - Typed analysis errors and local repair contracts.
   * - ``marivo.analysis.evidence``
     - Typed findings, bounded digests, issues, pages, and derivation traces.
   * - ``marivo.analysis.frames``
     - Frame classes and metadata companions.
   * - ``marivo.analysis.session``
     - Session lifecycle helpers (``get_or_create``, ``current``, ``list``, ``delete``).

Type aliases
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SliceScalar
   SliceValue
   TimeScopeInput
