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

An explicit ``name`` declares an Entity directly; omitting it returns a
decorator whose function receives one Source Table and returns one derived
Ibis Table. The output schema owns identity, versioning and downstream fields.
Actual physical input types and capability qualification remain Source facts.
Snapshot and validity coordinates stay outside ``primary_key``.

Loaded definition reading
-------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticDefinition
   SemanticDefinitionReadError

Typed expression authoring
--------------------------

Physical Entity/Field/Relationship constructors capture typed Ref lookup keys.
Capture every physical dependency, then call ``finalized = ms.finalize()`` and
``amount = finalized.resolve(amount_ref)``. Finalization validates and closes
capture before immutable values are created, preserving defaults,
coordinates and supported Ibis forward references without replaying imports. Measure and Metric
functions are pure zero-argument declarations over constructed dependencies;
they do not register or execute a Catalog Metric. Collection/publication and
new execution remain separate R1.4/R2 work.

The definition-update policy below is implemented in the uncommitted R1.3
working tree. Independent review and full R1.3 completion remain pending; a
documentation build does not qualify execution or backend behavior.

Semantic objects need not remain bound to their original definition versions;
a same-path definition change alone does not reject an input. The agent/user
accepts the consequences of semantic changes during analysis. Resolving consumers
use their completed scope; immutable expressions retain their stored computation
and facts without live refresh. Pure Measure additivity still checks coordinate
presence, kind, native time role and permission. Definition fingerprints and
cache keys describe actual consumed definitions; persisted provenance and records
retain their owning contracts.

Use ``marivo.help("semantic.expressions")`` for receiver operations.
``amount.show()`` inspects bounded expression metadata; ``marivo.help(amount.show)``
and ``marivo.help("semantic.Measure.show")`` resolve the shared display contract.
``finalized.show()`` has its own ``semantic.FinalizedDeclarations.show`` contract. Numeric
rows reduce through ``sum/mean/min/max/median/quantile/weighted_mean``; completed
Metrics combine through arithmetic and accept scope-checked ``where`` and
``per(...).reducer()``. Policies are operator-local strings; declaration-wide
empty/null/zero-denominator policies, old factories and default folds are
rejected with a concrete reauthoring action.

.. autosummary::
   :toctree: api/
   :nosignatures:

   finalize
   additive
   additive_all
   non_additive
   snapshot
   validity
   join_on

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


``catalog.source_health(refs, checks=..., scope=...)`` independently checks
current connectivity, physical schema/capability identity, and only explicitly
declared data expectations. Construct expectations with ``source_check``.
Metadata-only health checks omit ``scope`` and never query user data; declared
data checks require an explicit bounded scope and never change readiness.

.. autodata:: source_check
   :annotation:

Refs, binding & loading
-----------------------

``ref`` is the immutable exact-kind lookup factory namespace. Ref values themselves
are immutable identities and are never callable; use ``bind`` to apply a field
ref to a direct entity alias inside a decorated expression body.
Constructed Field inputs identify kind/path in the completed consumer scope;
their original definition versions are not mandatory constraints. Explicit Refs
remain lookup keys, including forward dependencies. Kind, owner/root and temporal
role checks still apply; finalization supplies complete defaults and dependencies.

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

Typed field predicates retain their literal values and contribution root when
attached with ``Metric.where(...)``. The retained legacy materializer can raise
``filter_value_runtime_incompatible`` before query submission; this does not
qualify the new R2 execution route. Any required code/label mapping needs
confirmation from the business owner.

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
