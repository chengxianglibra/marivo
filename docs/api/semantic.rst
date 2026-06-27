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
   count
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

Sources & provenance
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SqlProvenance

Readiness & assessment
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ReadinessReport
   ReadinessIssue
   ReadinessInputSummary
   RichnessReport
   AuthoringQuestion
   ParityResult
   VerifyResult
   DecisionRecord

Keys & kinds
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   JoinKey

AI context
----------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ai_context
   AiContextValue

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
