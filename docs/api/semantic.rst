marivo.semantic
===============

.. currentmodule:: marivo.semantic

.. automodule:: marivo.semantic
   :no-members:

Declaration decorators
----------------------

These public constructors are documented inline because their lowercase names
collide with the corresponding catalog-object class filenames on
case-insensitive filesystems.

.. autofunction:: entity
.. autofunction:: dimension
.. autofunction:: measure
.. autofunction:: metric
.. autofunction:: relationship
.. autofunction:: time_dimension
.. autofunction:: domain

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
   cumulative
   grain_to_date
   trailing

Column helpers
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   dimension_column
   measure_column
   time_dimension_column

Time parsing
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   datetime
   timestamp
   strptime
   hour_prefix

Provenance
----------

.. autosummary::
   :toctree: api/
   :nosignatures:

   from_sql

Readiness & verification
------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   richness
   parity_check

Refs & loading
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   Ref
   load

Discovery
---------

.. autosummary::
   :toctree: api/
   :nosignatures:

   help
   help_text

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

Catalog & objects
-----------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticCatalog
   CatalogCollection
   CatalogEntry
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
   ParityResult
   VerifyResult
   PreviewBatchResult

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
