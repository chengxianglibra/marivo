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
.. autofunction:: event
.. autofunction:: relationship
.. autofunction:: time_dimension
.. autofunction:: domain

``ms.entity(...)`` returns a non-callable Entity ref and must be assigned to a
name. It is not a function decorator. Entity identity types are observed from
the source at execution, and snapshot or validity coordinates stay outside
``primary_key``.

Event helpers
-------------

``event`` is the only Event declaration entrypoint. Filtered Event bodies
return a restricted boolean expression; unfiltered bodies explicitly return
``all_rows()``.

.. autofunction:: participant
.. autofunction:: participant_role
.. autofunction:: all_rows

Business order and lifecycle
----------------------------

``ms.business_order`` declares one same-Subject order authority. Loading
validates exact Event roles, source-owned sequence fields, and acyclic
precedence. Source values and Event history remain for R7 verification.

.. autofunction:: event_sequence
.. autofunction:: precedes
.. autofunction:: business_order
.. autofunction:: state_model

Aggregation & measure helpers
-----------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   aggregate
   count
   where
   linear
   ratio
   weighted_mean
   additive
   additive_all
   non_additive
   snapshot
   validity
   join_on
   cumulative
   grain_to_date
   trailing

Value policies are declared with ``ms.nulls.reject()`` or ``ms.nulls.ignore()``,
``ms.empty.zero()`` or ``ms.empty.null()``, and
``ms.zero_denominator.undefined()`` or ``ms.zero_denominator.error()``.

Column helpers
--------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   dimension_column
   measure_column
   time_dimension_column
   period_calendar
   period_correspondence
   temporal_set
   work_schedule
   calendar_grain

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


Readiness & runtime checks
--------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   richness

``catalog.source_health(refs, checks=..., scope=...)`` independently checks
current connectivity, physical schema/capability identity, and only explicitly
declared data expectations. Construct expectations with ``source_check``.
Metadata-only health checks omit ``scope`` and never query user data; declared
data checks require an explicit bounded scope and never change readiness.

.. autodata:: source_check
   :annotation:

Refs, binding & loading
-----------------------

``ref`` is the immutable exact-kind factory namespace. Ref values themselves
are immutable identities and are never callable; use ``bind`` to apply a field
ref to a direct entity alias inside a decorated expression body.

.. autodata:: ref
   :annotation:

.. autofunction:: bind

.. autosummary::
   :toctree: api/
   :nosignatures:

   Ref
   load

Focused help
------------

``python -m marivo help`` only validates the active interpreter, package
version, and environment fingerprint. Use the sole public coordinator,
``marivo.help("semantic.<target>")``, for bounded constructor, catalog, and
validation contracts rendered from the semantic registry. The ``ms`` namespace
executes semantic operations and intentionally has no ``ms.help()`` alias.

The packaged semantic skill owns task exits, reuse and business authority,
evidence selection, and delivery. Help owns static usage and proof boundaries.
``entry.show()`` displays key current facts, while ``entry.details()`` expands
the definition. ``marivo.help(entry)`` adds identity, usage navigation, and
kind-level analysis handoff without checking readiness or execution admission.
Explaining an existing definition does not require readiness or preview.

Registered error instances retain concrete facts in Help even without a repair.
Aggregate load failures preserve child order and disclose omitted errors with
a full read through ``exc.errors``. Check cards display actual scope, affected
refs, and available repair routes; their display never repeats checks.

A legal ``ms.where(...)`` declaration that cannot be compared with the resolved
runtime dtype raises ``filter_value_runtime_incompatible`` before query
submission. Its authored literal is preserved until the user or business owner
confirms any required code/label mapping.

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
   EventDetails
   BusinessOrderDetails
   StateModelDetails
   TimeDimensionDetails
   DomainDetails
   DatasourceDetails
   DerivedMetricDetails
   SimpleMetricDetails
   PeriodCalendarDetails
   CalendarLevelDetails
   TemporalSetDetails
   WorkScheduleDetails

Catalog & objects
-----------------

Typed collections resolve a local name, a full semantic path, a displayed
same-kind typed key such as ``metric:sales.revenue``, or an exact same-kind
``Ref`` within the collection's current scope. Scoped collections do not widen
to out-of-scope objects. ``catalog.require(ref)`` remains the strict, global,
ref-only lookup for configured, persisted, or logged identity.

``catalog.require(...)``, ``catalog.preview(...)``,
``catalog.preview_many(...)``, ``catalog.source_health(...)``, and
``catalog.readiness(refs=[...])`` accept either an exact entry owned by the
current compiled catalog or its exact ref. Entries normalize immediately to
refs; readiness results, preview evidence, persistence, and recovery remain
ref-based. One ``ms.load()`` is the project-level static validation event for a
dependency-coherent authored slice; there is no separate per-object verify step.

For analysis-agent discovery, use ``marivo.help("analysis.catalog")`` and the
focused ``analysis.catalog.<family>`` pages for the bounded collection,
exact lookup, entry-detail, and handoff contracts. ``CatalogCollection`` owns
lookup grammar, ``CatalogEntry`` owns detail and continuation inspection, and
``Ref`` owns exact identity. The packaged ``marivo-analysis`` skill provides
routing and boundaries only; it does not duplicate these API recipes.

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticCatalog
   CatalogCollection
   CatalogEntry
   SemanticKind
   EventSequence
   EventPrecedence
   BusinessOrderEntry
   PeriodCalendarEntry
   CalendarPeriodPage
   TemporalSetEntry
   TemporalOccurrencePage
   WorkScheduleEntry

Readiness & assessment
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ReadinessReport
   ReadinessIssue
   ReadinessInputSummary
   RichnessReport
   PreviewBatchResult
   SourceHealthReport
   SourceHealthCheckResult

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

Preview data display
--------------------

``PreviewResult.show(n=None, max_output_bytes=8192)`` fits complete returned
rows within a UTF-8 budget including the printed newline. ``render()`` accepts
the same controls and returns text without a newline. ``n=0`` shows metadata
and columns; ``max_output_bytes=None`` removes the byte limit, still respecting
``n``. Neither option reruns the preview or expands its source limit. Query
truncation, scope coverage, sampling and warnings remain visible independently
of display omissions. ``PreviewBatchResult`` remains a batch summary.
