marivo.semantic
===============

.. currentmodule:: marivo.semantic

.. automodule:: marivo.semantic
   :no-members:

Declaration decorators
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   entity
   dimension
   measure
   metric
   relationship
   time_dimension
   domain

Aggregation & measure helpers
-----------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   aggregate
   linear
   ratio
   weighted_average
   semi_additive
   snapshot
   validity
   join_on

Time parsing
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   datetime
   timestamp
   strptime
   hour_prefix

Source builders & provenance
----------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   csv
   parquet
   table
   from_sql

Authoring handoff
-----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   prepare_entity
   prepare_dimension
   prepare_measure
   prepare_metric
   prepare_relationship
   prepare_time_dimension
   prepare_domain
   prepare_cross_entity_metric
   prepare_derived_metric

Readiness & verification
------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   readiness
   richness
   verify_object
   parity_check
   record_decision

Refs & loading
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ref
   make_ref
   load

Discovery
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

Ref types
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   EntityRef
   DimensionRef
   MeasureRef
   MetricRef
   RelationshipRef
   TimeDimensionRef
   DomainRef
   SemanticRef

Brief types
-----------

.. autosummary::
   :toctree: api/
   :nosignatures:

   EntityBrief
   DimensionBrief
   MeasureBrief
   MetricBrief
   RelationshipBrief
   TimeDimensionBrief
   DomainBrief
   DomainBriefSummary
   CrossEntityMetricBrief
   DerivedMetricBrief
   BriefStatus

Details types
-------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   EntityDetails
   DimensionDetails
   MeasureDetails
   MetricDetails
   RelationshipDetails
   TimeDimensionDetails
   DomainDetails
   DatasourceDetails
   DerivedMetricDetails
   SimpleMetricDetails
   SemanticObjectDetails

Catalog & objects
-----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticCatalog
   SemanticObject
   SemanticObjectList
   SemanticKind
   RegisteredMatch
   MeasureIR

Sources & versioning
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   TableSource
   FileSource
   DatasetSource
   SqlProvenance
   SnapshotVersioning
   ValidityVersioning
   EntityVersioning
   VersioningHints

Time-parse specs
----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   DateParse
   DatetimeParse
   TimestampParse
   StrptimeParse
   HourPrefixParse
   FormatCandidate

Readiness & assessment
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ReadinessReport
   ReadinessIssue
   ReadinessInputSummary
   RichnessReport
   AuthoringAssessment
   AssessmentIssue
   AuthoringQuestion
   ParityResult
   VerifyResult
   DecisionRecord

Facts & signals
---------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ComponentFact
   DimensionValueFact
   JoinPathFact
   JoinKey
   PrimaryKeyCandidate
   DemandSignal

AI context
----------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ai_context
   AiContextValue
   AiContextView

Errors
------

.. autosummary::
   :toctree: api/
   :nosignatures:

   LadderOrderError

Submodules
----------

.. list-table::
   :widths: 25 75
   :header-rows: 0

   * - ``marivo.semantic.errors``
     - Typed semantic errors and warnings raised across the semantic layer.
   * - ``marivo.semantic.typing``
     - Shared type aliases for the semantic surface.

Type aliases
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticKindInput
   SemanticRefInput
