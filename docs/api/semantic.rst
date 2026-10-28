marivo.semantic
===============

.. currentmodule:: marivo.semantic

.. automodule:: marivo.semantic
   :no-members:

Declaration decorators
----------------------

Declaration constructors have dedicated pages in a separate directory to avoid
case-insensitive filename collisions with catalog-object classes.

.. autosummary::
   :toctree: api/constructors/
   :nosignatures:
   :caption: Declaration constructors

   entity
   dimension
   measure
   metric
   relationship
   time_dimension
   domain

An explicit ``name`` declares an Entity directly; omitting it returns a
decorator whose function receives one Source Table and returns one derived
Ibis Table. The output schema owns identity, versioning and downstream fields.
Actual physical input types and capability qualification remain Source facts.
Snapshot and validity coordinates stay outside ``primary_key``.

Typed expression authoring
--------------------------

Physical Entity/Field/Relationship constructors and ``ms.ref.*`` capture native
Ibis Deferred symbols with typed identity roles. Compose them directly; the
loader binds the complete dependency scope before publishing closed Catalog
values. Default time, coordinates and supported Ibis forward references use
that complete scope without replaying imports or calling authored functions.
Measure and Metric functions remain pure zero-argument declarations.
``ms.load()`` publishes declarations bound at module level in their owning
model files; aliases are collected once. Distinct declarations cannot share a
full identity, and Session-owned closures are not admitted for publication.

Semantic objects need not remain bound to their original definition versions;
a same-path definition change alone does not reject an input. The agent/user
accepts the consequences of semantic changes during analysis. Resolving consumers
use their completed scope; immutable expressions retain their stored computation
and facts without live refresh. Pure Measure additivity still checks coordinate
presence, kind, native time role and permission. Definition fingerprints and
cache keys describe actual consumed definitions; persisted provenance and records
retain their owning contracts.

Use ``marivo.help("semantic.expressions")`` for receiver operations and
``catalog.get("measure:sales.orders.amount").show()`` for captured
definition facts. Deferred itself has no Marivo contract or show method. Numeric
rows reduce through ``sum/mean/min/max/median/quantile/weighted_mean``; completed
Metrics combine through arithmetic and accept scope-checked ``where`` and
``per(...).reducer()``. Place membership rules directly in ``per`` samples or
``observe.by``; explicit external domains require complete output-key coverage,
while internal domains allow subsets. Division uses ``/``; zero denominators and
zero paired-weight sums return NA before arithmetic. Independent ``nulls``
policies remain operator-local strings; declaration-wide
empty/null/zero-denominator policies, old factories and default folds are
rejected with a concrete reauthoring action.

Time-sample ``mean`` and ``weighted_mean`` use represented samples without
scheduled-clock coverage checks or implicit zero/NULL completion. This policy is
independent of formula shape and execution route; explicit Population domains
and existing NULL/weight policies remain authoritative. Other sample reducers
retain coverage checks. ``sample_interval`` never buckets timestamps.

``ms.rescale(expr, divisor=..., unit=...)`` explicitly divides a numeric Row or
completed Metric by a finite positive constant and declares its target unit.
The source unit must be known after binding; stage, source roots, time restrictions
and sum permissions are preserved. The author owns the factor and business meaning,
including cross-dimension assertions and ``divisor=1``. No conversion is inferred.
Ordinary scalar division retains the source unit, and a Measure unit assertion
remains compatible with a known inferred unit. Fixed duration factors do not
change with the analysis window. See ``marivo.help("semantic.rescale")``.

.. autosummary::
   :toctree: api/
   :nosignatures:

   additive_all
   additive_except
   additive_over
   non_additive
   rescale
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
   temporal_set
   calendar_grain

Time parsing
------------

.. autosummary::
   :toctree: api/
   :nosignatures:

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


``catalog.validate(refs, checks=..., scope=...)`` independently checks
the selected physical dependency closure: current connectivity, Entity output
schemas, field bindings, numeric/duration types, and temporal parser/timezone contracts.
Preview and analysis reuse the same field checks. It also runs explicitly
declared data expectations. Construct expectations with ``source_check``.
Metadata-only validation omits ``scope`` and never queries user data; declared
data checks require an explicit bounded scope and never change readiness.

.. autodata:: source_check
   :annotation:

Symbols, binding & loading
--------------------------

``ref`` is the exact-kind native lookup-symbol namespace. Use ``bind`` to
apply a native physical field to a direct Entity alias inside a restricted Ibis
Dimension/TimeDimension body. Loader binding checks kind, owner and time role.

Use ``dimension_column`` and ``measure_column`` with different semantic names
to declare grouping and quantity roles over the same numeric Entity output
column. For a shared derived value, create that column in the Entity body first.
``@measure`` composes established quantities; it does not convert a Dimension.
Dimension arithmetic and quantity reductions such as ``sum``/``mean`` require
the Measure role; existing Dimension counting reductions remain supported.
Measure/Metric bodies accept tuple and list literals as expression arguments;
captured lists become tuples.

Entity bodies accept typed scalar backend builtin UDFs declared with
``ibis.udf.scalar.builtin``, with at least one argument and concrete argument/return
types. Zero-argument builtin UDFs are not admitted. They capture the function identity and signature
without executing the Python function body. Backend support is checked during
execution; arbitrary Python helpers and Python/Pandas/Arrow UDFs are not admitted
as Entity body symbols.

Metadata identity fields return unbound lookup selectors. Use them for supported
Catalog lookup/check operations; select closed analysis inputs through the
canonical ``catalog.get("kind:path").value`` after checking kind. Ordinary Analysis does not bind a symbol.
Equality builds a recipe rather than testing identity. Symbols are not supported
as boolean identities, hash keys, dictionary keys or container membership keys.
Invalid attributes and methods fail when a declaration or parameter captures the
recipe; ordinary Python attribute access, unused recipes and formatted strings
have no immediate-error guarantee.

.. autodata:: ref
   :annotation:

.. autosummary::
   :toctree: api/
   :nosignatures:

   bind
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
Catalog entries expose complete ``entry.definition`` and bounded ``entry.show()``.
Use type Help for static API navigation; query matching facts belong to result
pages. Inspecting a definition does not require readiness or preview.

Registered error instances retain concrete facts in Help even without a repair.
Aggregate load failures preserve child order and disclose omitted errors with
a full read through ``exc.errors``. Check cards display actual scope, affected
refs, and available repair routes; their display never repeats checks.

Typed field predicates retain their literal values and contribution root when
attached with ``Metric.where(...)``. The retained legacy materializer can raise
``filter_value_runtime_incompatible`` before query submission; this does not
qualify the new R2 execution route. Any required code/label mapping needs
confirmation from the business owner.

Captured definitions
--------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   CatalogScope
   CatalogFilters
   EntryProvenance
   DomainDefinition
   DatasourceDefinition
   EntityDefinition
   FieldDefinition
   MetricDefinition
   MetricTimeBinding
   RelationshipDefinition
   PeriodCalendarDefinition
   TemporalSetDefinition

Catalog & objects
-----------------

``catalog.search(query, kind=..., domain=..., entity=..., limit=10)`` returns
bounded ``CatalogPage`` candidates. Empty queries browse without models;
configured hybrid text search combines lexical and vector recall with RRF and
optional reranking. Search configuration is frozen at load. The optional
``semantic-search`` extra uses explicitly prepared revision-pinned local models;
model or input-length failures are explicit. Derived vector/index/ranking caches
live in ``<project_root>/.marivo/semantic/search/``. No business datasource is read.

``catalog.get("metric:sales.revenue")`` returns one immutable entry by canonical
identity. Check ``entry.kind`` before accessing ``entry.value``. Executable
variants retain native closed expression values; Domain/Datasource have no value.
``entry.related("dependencies")`` uses the complete loaded scope independently
from search filters; unloaded identities are separate ``outside_scope`` metadata.
Complete definitions and ``to_dict()/to_json()`` are independent of display
budgets. Query scores/snippets remain on pages. ``candidate_count`` is a fixed
candidate-pool count, and cursors bind that ranking across short-lived processes.
Changed scope, definitions, configuration or missing cached results rejects a
cursor and requires restarting the search.

``catalog.preview(...)``, ``catalog.preview_many(...)``,
``catalog.validate(...)``, and ``catalog.readiness(refs=[...])`` accept an
exact ref, an entry owned by the current compiled catalog, or a catalog value.
Inputs normalize immediately to refs; readiness results, preview evidence,
persistence, and recovery remain ref-based. Preview ``limit`` must be an integer
from 1 to 100 inclusive and bounds retained preview rows per result, independently
from the ``scope.max_rows`` acquisition guard. Invalid limits raise a structured
``SemanticRuntimeError`` before connection acquisition, with expected/received
input and a repair. Published Metrics support bounded DuckDB ordinary source reductions and arithmetic
previews through the shared nullable Row/Metric kernel. Population/sample
and path/attribute-anchor preview preparation is unavailable and fails before opening a source. Published
Measures still have no physical preview; published expression certification
and source checks remain unavailable. One ``ms.load()`` is the project-level static validation event for a
dependency-coherent authored slice; there is no separate per-object verify step.

For analysis-agent discovery, use ``marivo.help("analysis.inputs.catalog")``.
``catalog.search`` owns discovery, ``catalog.get`` owns canonical lookup,
``CatalogEntry`` owns captured meaning/navigation, and ``ms.ref.*`` owns authored
lookup symbols. Explicit ``ms.ai_context(aliases=[...])`` helps search without
creating alternate identities.

.. autosummary::
   :toctree: api/
   :nosignatures:

   SemanticCatalog
   CatalogPage
   DomainEntry
   DatasourceEntry
   EntityEntry
   DimensionEntry
   TimeDimensionEntry
   MeasureEntry
   MetricEntry
   RelationshipEntry
   CatalogEntry
   SemanticKind
   PeriodCalendarEntry
   CalendarPeriodPage
   TemporalSetEntry
   TemporalOccurrencePage

Readiness & assessment
----------------------

.. autosummary::
   :toctree: api/
   :nosignatures:

   ReadinessReport
   PreviewBatchResult
   ValidationReport

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
``n``. Neither option reruns the preview or expands its source limit. The card's
``preview_limit`` and ``preview_rows_truncated`` describe retained preview rows;
``scope_coverage`` describes acquisition completeness. ``is_truncated=True`` can
accompany successful exhaustive temporal certification when only the retained
preview entries are bounded. Scope coverage, sampling and warnings remain visible
independently of display omissions. ``PreviewBatchResult`` remains a batch summary.

Returned authoring and report records
-------------------------------------

Construct context with ``ms.ai_context(...)``. Use ``ms.typing.AiContextValue`` for explicit
context annotations. Read readiness issue/input records through the report
fields and validation checks through ``ValidationReport.checks``. These returned
records retain their exact types and field Help without root constructor exports;
use ``marivo.help(type(value))`` for each owning type. Factory signatures document
the ten nominal symbol roles; runtime leaves remain native Deferred objects.
