marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

``SemanticRef`` and ``CatalogObject`` are re-exported here for convenience and
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

Help and agent surface
----------------------

Start with ``python -m marivo help analysis`` to see the capability surface,
artifact families, constraints, and recovery guidance.

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

Frames
------

Public analysis artifacts expose ``ref``, ``kind``, ``show()``,
``contract()``, ``quality_summary``, ``blocking_issues``, ``lineage``,
``state``, and ``to_pandas()``. ``contract().affordances`` describes
mechanical compatibility only; it is not ranked and is not a recommendation.

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

Refs & lineage
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ArtifactRef
   CalendarRef
   Lineage
   LineageStep

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
