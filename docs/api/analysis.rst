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

Help and agent surface
----------------------

Start with ``help_text("agent_surface")`` or ``help("agent_surface")`` to see
the Phase 3 default operator surface, base artifact protocol, bounded read
order, governed derive boundary, and mechanical affordance language.

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

Governed Derivation
-------------------

``derive_metric_frame`` is a ``Session`` method. The helper constructors below
define the governed Ibis query contract: semantic refs identify metric and axis
bindings; query output columns are plain strings.

.. autosummary::
   :toctree: api/
   :nosignatures:

   DeriveContext
   IbisQuerySpec
   MetricColumnBinding
   MetricColumns
   ibis_query
   metric_columns
   time_column
   dimension_column

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
