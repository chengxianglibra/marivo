marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

``SemanticRef`` and ``SemanticObject`` are re-exported here for convenience and
documented under :doc:`semantic`.

References
----------

.. autosummary::
   :toctree: api/
   :nosignatures:

Alignment & window helpers
--------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   dow_aligned
   holiday_aligned
   holiday_and_dow_aligned
   window_bucket

Discovery
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

Frames
------

Public analysis artifacts expose ``ref``, ``kind``, ``summary()``, ``schema()``,
``contract()``, ``quality_summary``, ``blocking_issues``, ``lineage``, ``state``,
and ``show()``. ``contract().affordances`` describes mechanical compatibility
only; it is not ranked and is not a recommendation.

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
   FramePreview
   FrameSummary
   FrameSummaryEntry
   ArtifactAffordance
   ArtifactColumn
   ArtifactContract
   ArtifactParamTemplate
   ArtifactPrecondition
   ArtifactSchema
   ArtifactState

Analysis results
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   AssociationResult
   HypothesisTestResult
   ExplorationResult
   QualityReport
   CandidateSet

Scopes & windows
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TimeScope
   ConfidenceScope
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
   PromotionPolicy
   PromotionSemanticAnchors

Refs & lineage
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ArtifactRef
   CalendarRef
   Lineage
   LineageStep
   ReportRegistration

Session & jobs
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   Session
   SessionSummary
   JobSummary
   BlockingIssue
   CandidateObjective
   DiscoverSensitivity

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
     - Typed analysis errors raised across frames and sessions.
   * - ``marivo.analysis.evidence``
     - Evidence facts, findings, and open-item types for investigations.
   * - ``marivo.analysis.frames``
     - Frame classes and their metadata companions.
   * - ``marivo.analysis.publish``
     - Report artifact, manifest, and publishing configuration types.
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
