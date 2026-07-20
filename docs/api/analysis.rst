marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Semantic identities are exact ``marivo.semantic.Ref`` values. Catalog entries
are documented under :doc:`semantic`; analysis inputs accept refs rather than
catalog entries or strings.

Help and agent surface
----------------------

Start with ``python -m marivo help analysis``. Live help owns callable
signatures, accepted input families, constraints, and recovery guidance.

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

Frames
------

Public artifacts expose ``ref``, ``kind``, ``show()``, ``contract()``,
``quality_summary``, ``evidence_status``, ``evidence_digest``, ``lineage``,
``state``, and ``to_pandas()``. ``contract().issues`` contains typed issues;
role-preserving affordances describe mechanical compatibility only.

.. autosummary::
   :toctree: api/
   :nosignatures:

   BaseFrame
   BaseFrameMeta
   MetricFrame
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
   QualityReport
   CandidateSet
   PointAnomalySelection
   PeriodShiftSelection
   DriverAxisSelection
   SliceSelection
   WindowSelection
   CrossSectionalOutlierSelection

Evidence
--------

``Finding`` is the typed audit record. ``ArtifactDigest`` is the bounded
operator-local read model; it never performs cross-artifact judgment.

.. autosummary::
   :toctree: api/
   :nosignatures:

   Finding
   ArtifactDigest
   EvidenceDerivationTrace
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

Bounded pages
-------------

Pages expose immutable ``items``, ``limit``, ``has_more``, and opaque
``next_cursor``. They use ordinary newest-first keyset semantics, not snapshot
isolation.

.. autosummary::
   :toctree: api/
   :nosignatures:

   FrameSummaryPage
   FrameSummaryEntry
   ArtifactDigestPage
   FindingPage

Scopes and windows
------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TimeScope
   AbsoluteWindow

Policies
--------

.. autosummary::
   :toctree: api/
   :nosignatures:

   AlignmentPolicy
   AlignmentKind
   CalendarPolicy
   SamplingPolicy

Refs and lineage
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ArtifactRef
   CalendarRef
   Lineage
   LineageStep

Session and jobs
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   Session
   SessionSummary
   JobSummary

Alignment and window helpers
----------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   dow_aligned
   holiday_aligned
   holiday_and_dow_aligned
   window_bucket

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
