marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Immutable analysis data and explicit source inputs
---------------------------------------------------

``Population`` is a finite complete coordinate domain. ``AnalysisFrame`` is one
immutable data type with expression, complete memory and exact stored carriers.
``Column`` and ``FrameAxis`` bind to the exact input. ``observe`` binds Metrics,
``read`` binds nonempty fields to one Entity identity domain, ``values`` declares
literal rows, and ``frame`` binds general Population keys, including zero axes.
``frame.attribute(by=...)`` explains its captured direct Metric or comparison
outputs over new business classifications through a qualified source route.
Construction and inspection perform no business read or publication.

``session.observe`` accepts one closed declared Metric, a nonempty tuple, or a
nonempty explicit name-to-expression Mapping. Shorthand takes the local name only
from the consumed root ``DeclaredMetricRef`` in the frozen scope. A directly
referenced declared formula retains its declaration name; arithmetic, filter,
window, per or rescale added around that reference requires explicit naming.
Direct source reducers, Entity/Population counts and unnamed expressions also
require Mapping. Python variable names, reprs and dependency leaves supply no names.
Duplicate local names, including repeated inputs, and invalid names reject during
capture. Use distinct legal single-segment Mapping keys to repair them.
Names do not establish Metric identity or business comparability.

Source grouping accepts ordered fields or ``Grouping`` rules, or a complete
``Population`` domain. ``identity(entity)`` declares one complete identity rule;
``coordinates(by=(identity(entity),), keys=((("C1",),),))`` declares a finite
requested identity domain. Every identity value is a complete primary-key tuple.
Coordinates also accepts field coordinates and correlated field/identity tuples.
``population.axes`` reuses grouping definitions without selected keys.
``by=population`` selects complete output keys, including keys without facts.
CalendarAxis is a calendar Grouping. Membership rules preserve correlated tuples
directly in observe.by or Metric.per, including interleaved field positions;
Population stores no allocation rules. External output domains must cover necessary
complete keys before internal sampling; internal sample domains may select subsets.

``population.count()`` counts complete distinct tuples as a lazy integer Metric,
including Null categories and references without source facts. Empty domains
count 0; the zero-axis unit domain counts 1. Ordinary grouping coordinates must
already exist in the Population. Classify a new attribute while retaining identity
before counting members by it; projecting to the attribute alone counts categories.

Use the returned ``compute()`` Frame for complete fixed memory data and the
returned ``persist()`` Frame for committed data. Derived operations remain lazy.
Reopen with ``session.persisted_frame(artifact_id)`` in the exact Session; the ID
must be a string. Every Frame exposes its captured ``session_id``. Stored roots
expose ``artifact_id``, ``findings()`` and ``evidence_digest()`` directly.
Pending and memory Frames require ``persist()`` for commit identity and audit reads.
Identity attributes perform no Store reads. There is no public Artifact wrapper.
``show(n=20, max_output_bytes=8192)`` combines metadata, audit summaries and a
whole-row preview with exact omission counts. ``show(n=0)`` reads metadata only;
``max_output_bytes=None`` removes the byte limit while preserving the row limit.
Full data export uses ``to_pandas()``.

Public classes describe returned values; they do not authorize direct construction
or subclass extension. Use ``mv.*`` for explicit descriptions/composition,
``session.*`` when introducing Catalog, Store, execution ownership or the frozen
default timezone, and receiver methods/properties for existing context.
``mv.session.get_or_create/resume/recent/inspect`` establish or discover lifecycle context.
``get_or_create(name, *, question=None, timezone=None, domains=None)`` creates or
recovers an exact name. None uses defaults on creation and retains saved values
on recovery. Non-None configuration must match the saved description, normalized
timezone name and sorted, deduplicated domain scope; conflicts raise
``SessionStateError`` before recovery. Read ``error.repair.candidates`` for the
existing Session ID. Acquisition sets no current Session and never rewrites
saved configuration. Recovery without pending work preserves ``updated_at``.

Current data operations
-----------------------

``select`` derives named values; ``where`` accepts a same-Frame boolean Column.

``fix(axis, key)`` selects one complete ordinary member and removes its axis
without aggregation. Use a scalar for one key component or a complete tuple in
``axis.key_names`` order. ``None`` selects nullable NULL; absent members yield empty
Frames. Values, types, units, NA, presence and row order are retained. Calendar,
interval and paired axes cannot be fixed. Reacquire handles from the result;
remaining ordinary axes still require explicit ``on`` for compare.

``top`` is global and deterministic. ``reduce`` uses current represented cells,
with independent valid masks per statistic. It does not recompute original
Metrics. New facts or original overall questions require explicit source inputs.

``reduce`` accepts one statistic, a nonempty tuple, or a nonempty explicit
name-to-statistic Mapping. Direct single-segment value sum/mean use
``<name>_sum`` / ``<name>_mean``; ``mv.row_count()`` uses ``row_count``.
For example, ``frame.reduce((frame['revenue'].sum(), mv.row_count()))`` has
``output_names == ('revenue_sum', 'row_count')``. Binding and the receiver schema
determine these names, not expression dependencies. Arithmetic, pair statistics
and multi-segment paths such as ``left.revenue`` require explicit single-segment
Mapping names; naming does not make an otherwise invalid statistic legal.
Repeated or invalid names reject without overwriting or numbering.
Tuple and Mapping order fix output order before evaluation. Names are available
through ``output_names`` at capture and remain unchanged for empty results,
compute, persistence and independent recovery. Lists and arbitrary iterables are
not shorthand inputs. Explicit Mapping remains available for business names.

``frame.over(statistic, by=())`` reduces each current partition and broadcasts its
result as a Column on the exact same input, preserving complete keys and row order.
It accepts the same sum/mean/corr/pair_count and reusable row_count requests as
reduce, with the same sample, numeric and unit rules. ``by`` contains distinct
complete current axes; () uses all rows. Empty input stays empty. Nested over
counts repeated broadcast values, and where/top consume the full input partition
before selecting rows.

``column.share(along=axes)`` divides current values by
their partition sum, summing along the named axes and partitioning by the remaining
axes. ``along`` is a nonempty tuple of distinct complete same-input axes; zero-axis
inputs require explicit division. Signed shares can exceed [0,1]; zero totals give
NA after masking the zero denominator before arithmetic. Unknown units stay unknown. Filtering
before share changes the denominator; filtering its output preserves it. Both
operations use current data on pending, memory and stored carriers without new
source questions or method facts.

``frame.correlate(x, y, method="pearson" | "spearman", by=())`` returns a lazy
ordinary Frame with exactly ``coefficient`` (nullable float64, unit ``1``) and
``n_pairs`` (nonnullable int64, no unit), evaluated from the same input once.
Both numeric Columns and complete grouping axes belong to that exact input;
different units are allowed, Duration is rejected. Each group uses common
non-NA pairs after checking both expressions; zero and repeated values remain.
Undefined coefficients (fewer than two pairs or a constant endpoint) retain
their count, including zero-pair groups. Spearman uses average ranks within
paired group values. Presence is removed and is never an implicit filter.
``Column.corr(other, method=...)`` and ``Column.pair_count(other)`` are unfinished
statistics for ``reduce`` compositions or ``over`` broadcasts; each produces one value.
``mv.row_count()`` is an unbound request, reusable in different Frames without
mutation. Column sum/mean/corr/pair_count bind one exact Frame and Session;
equal-looking data cannot substitute. Only their read-only ``unit`` is public;
pass requests to ``frame.reduce`` or ``frame.over``. See ``analysis.value_statistic``
in Live Help.
Self pair counting counts non-NA values. Output names never infer matching
variable pairs. Group coefficients/counts do not reconstruct pooled correlation.

.. code-block:: python

   result = frame.correlate(frame['spend'], frame['revenue'], method='pearson').compute()
   combined = frame.reduce({
       'spearman': frame['spend'].corr(frame['revenue'], method='spearman'),
       'n_pairs': frame['spend'].pair_count(frame['revenue']),
   }).compute()

``session.population_from(frame, on=...)`` projects jointly selected ordinary
complete axes onto explicitly supplied Field/Entity bindings. ``on`` is required
and may be empty. Calendar/paired axes and arbitrary value columns cannot bridge.

``align`` preserves left/right values and general paired time unions.
``compare`` accepts one same-Session Frame with fixed alias ``reference``, or a
nonempty ordered Mapping of named ComparisonBaseline declarations from ``mv.baseline(...)``. Multiple baselines require explicit
aliases. ``current.compare(baseline)`` is equivalent to
``current.compare({'reference': mv.baseline(baseline)})``. It retains current axes and values, then adds each
alias's ``baseline``, ``absolute`` and ``relative`` groups. For example,
``current.compare({"yoy": mv.baseline(baseline, match=mv.offset(years=-1))})``
produces ``revenue`` and ``yoy.baseline.revenue``, ``yoy.absolute.revenue``,
``yoy.relative.revenue``. Relative is ``(current-baseline)/abs(baseline)``.
``current.present`` and ``yoy.present`` are row-existence flags independent of
value NA; inherited flags live below ``current.presence.*`` / ``yoy.presence.*``.
Zero baselines return relative NA. Missing rows/NA never become zero. Duration baseline/absolute retain their tick
unit and relative is dimensionless; strict child policies remain required.

For a bare baseline Frame, ``on`` is a bare axis-pair tuple and ``match`` is a
bare CalendarCorrespondence, for example
``current.compare(baseline, on=((current.axes[0], baseline.axes[0]),), match=mv.ordinal())``.
For named input, put each Frame and its rules in ``mv.baseline(frame, on=..., match=...)``;
no top-level rules or bare Frame Mapping are accepted. The two
parameter shapes cannot be mixed. Omitting the alias does not infer a date shift
or business relationship. A generated ``reference`` path collision requires an
explicit alias or prior select; no suffix is generated automatically.

Each alias's ``on`` binds every complete axis, except zero axes or one sole
interval/calendar axis permit unique structural inference. ``match`` is also
alias-keyed for Mapping input. A compatible immediate frozen shift relationship can supply it;
wrong anchors and conflicting explicit rules reject. Fixed ``outer`` correspondence
preserves current order then mapped baseline-only
keys in alias order. Actual baseline time rows outside the declared current time
domain reject; use ``align`` for a general two-ended time union.

``offset(*, years=n)`` and ``offset(*, periods=n)`` construct an axis-free rule;
supply exactly one non-boolean integer keyword. ``periods`` uses nominal grain
distance and ``years`` uses civil years (ISO week-year for weeks), never fixed
365 days or 52 weeks. ``CalendarAxis.shift`` accepts unit-step builtin
week/month/year full buckets or bucket-start prefixes. It freezes exact selected
targets and one direct relation; sparse/empty targets do not fill an envelope.
Missing ISO weeks/civil cutoffs have no target and never shorten the current
domain. Nonzero target DST gaps/folds reject; zero offsets retain captured exact
instants. Observe each endpoint's original Metric explicitly before comparison.

Fixed-data matching consumes saved finite UTC/civil boundaries. Complete builtin
day/quarter offset matching remains supported; partial day/quarter reject.
``ordinal`` requires compatible complete nominal partitions or a single interval
per side with the same boundary timezone. It never compresses positions after
filtering and cannot construct a shifted source domain. Completed comparison
facts/attachments explain saved values and endpoint presence, with no Metric,
source or current timezone reconstruction. Relationship correspondence, arbitrary
broadcasting between independent Frames, calendar coarsening and original
rollup/drill/share receivers remain outside this Frame surface. Same-input
``over`` broadcasts current statistics and ``Column.share`` normalizes current
values; neither reconstructs original Metric contributions.



Time, values and reading
------------------------

Session time constructors freeze exact timezone-aware boundaries. Source time,
report time and calendar time remain distinct. Metric.window captures explicit
source windows before observe. Full original source formulas, per samples,
units, null/empty policies and backend method qualification retain their owners.
Source or internal sum defaults to NA for empty/all-NA input; explicit
``empty="zero"`` selects zero. Mean/extrema/quantiles have no effective value
without a sample. Boolean is not numeric. Strict child errors cannot be hidden
by projection, Top-0 or a parent policy.

Numerical source routes qualify the actual backend, expression, physical type
and method. Native Ibis/provider precision loss is accepted at source handoff;
keys, units, time and null correctness remain strict. Native sum/mean/weighted
and ratio routes include PostgreSQL, MySQL, Trino and ClickHouse; this code scope
does not establish a new provider Runtime qualification. Exact numeric source
quantiles support those four backends and DuckDB. MySQL, Trino and ClickHouse
extract ranked bounds on the source before typed linear interpolation; DuckDB
and PostgreSQL retain their qualified exact routes. Explicit approximate
quantiles use DuckDB approx_quantile, Trino approx_percentile or ClickHouse
TDigest. PostgreSQL and MySQL reject approximate source methods. Exact and
approximate algorithms never substitute; actual type and source restrictions
still apply.

``show(n=0)`` reads metadata and bounded audit summaries only, including stored
commit time, inputs/operations and source-fact/check/Finding counts; it does not
read full audit payloads. Positive show decodes the
saved prefix plus needed frozen coordinate records, without full export/head.
Receipt verification or Parquet decompression can read more than that prefix.
``to_pandas()`` validates complete current data and its required attachments.
``method_facts`` explicitly reads complete immutable original explanation facts;
on stored Frames it validates only the owned method payloads, without primary
rows, Catalog or source access. Pending access requires compute/persist first.
Stored ``compute()`` and evaluated derivatives consume both current data and
method facts. Damaged method payloads can therefore leave metadata and export
usable while full consumers fail. Cards show only count, bounded origins and a
getter pointer. Calling a fact's ``show(n=0)`` follows the explicit payload read.
Export returns one readable flat table with full value/presence names and axis
names, preserving row order, nullable types and isolated frozen-interpretation
attrs. Calendar labels include actual bounds and timezone; intervals expose
start/end/timezone; paired time coordinates expose both endpoints. Composite
identities remain tuples. ``axis_names={axis: "name"}`` names axes from the exact
Frame and resolves column collisions; duplicate names are rejected before reads.
Exported attrs cannot reenter computation. Bounded show previews use the same
coordinate interpretation without a full export.

Explicit source investigation and contribution
----------------------------------------------

``frame.attribute(*, by=tuple | Population)`` returns an ordinary lazy
AnalysisFrame. Its receiver supplies the complete selected parent keys, observed
values and direct source targets. Direct Metric/current/baseline outputs explain
levels; a comparison absolute or relative output explains that target's change.
Direct select/rename preserves targets. General Column arithmetic does not create
source Metric authority; the supported align subtraction retains its captured
reciprocal endpoint pairing.

Each target has ``contribution.<target>``, ``total.<target>``,
``observed_total.<target>`` and ``contribution_share.<target>``. ``total`` is the
freshly read original parent result; ``observed_total`` remains the receiver's
old observation. Detail values and independent presence retain each endpoint;
applicable numerator, denominator and valid-count diagnostics remain inspectable.
Their own units are available on pending Columns before computation. Fixed
constant division and Metric ``ms.rescale`` preserve the typed divisor and
division order; rescale declares the result unit. Acquired division method facts
expose ``divisor``, ``operation`` and the child ``method`` through
``analysis.frame_division_method`` and ``analysis.frame_level_division_method`` Help.
Relative contributions normalize absolute contributions by the absolute parent
baseline. Defined zero totals retain signed contributions and NA shares.

``by`` accepts nonempty semantic classification tuples or one complete Population
with its correlated keys, membership and allocation rules. Frozen interval,
calendar and paired parent bindings retain their complete correspondence and
original endpoint scopes. Selected parents remain fixed when source values
change; attribution does not rerun historical anomaly selection.

Contribution uses qualified native DuckDB, PostgreSQL, MySQL, Trino and ClickHouse
physical-table routes, subject to each backend's method, type and source restrictions. Original
parent and fine formulas, coverage, disjointness, unknown components and closure
checks remain mandatory. Unsupported recipes fail explicitly. Investigate such
Metrics through ``population_from`` and a new ``observe`` over complete parent
keys and fine classifications. Native results retain their actual completed
types, units and NA; no universal Decimal precision is promised.

Source declarations live on the exact Frame instance and survive compute and
same-process persist return values. Independent reopen and literal ``values``
inputs have no declarations, including after compute. Declarations are not saved,
recovered from a global cache or reconstructed by loading source definitions.

Successful contribution facts are inert original explanations. Filtering or
reducing data preserves that history without changing original denominators or
certifying a new decomposition. Saved derivatives own their copied payloads;
the original Artifact and source are not needed. Read the exact getter contract
with ``marivo.help("analysis.analysis_frame.method_facts")`` and the concrete
family with ``marivo.help("analysis.frame_method_facts")``.

Evaluation, publication and recovery
------------------------------------

Compute creates no Artifact or temporary saved result. Expression persist
completes evaluation first, then publishes that existing result ID through the
single Store 17 writer transaction. Memory persist never reevaluates. Stored
persist validates commit metadata and returns itself without another publication.
A derived or computed memory Frame never exposes an ancestor commit identity. Unknown publication outcome is reconciled, not recomputed.

Completed data owns source-free values, coordinates, presence and one audit:
inherited source facts plus the current evaluation. It retains no producer,
semantic graph, connection or hidden aggregate components. Read, evaluation and
commit times remain separate. Publication records remain internal and do not
promise complete data lineage. Retain Session and Artifact IDs for exact reopening.
Lost return references require explicit resubmission, accepting duplicate saved
outputs and possibly changed source data. There is no public result directory.

Store 17 uses ``marivo.frame_output/v6``, ``frame.v6`` and ``frame-output-v6`` for
all Frames, under ``.marivo/analysis/generations/v17/``. Owned method files share
the existing atomic publication, receipt and unknown-commit reconciliation.
Store 17 refuses older-generation Session/result/audit data unchanged, without
migration or dual reads. Documented absent-current discovery may return None or
empty; create cannot bypass an old-only project. Preserve required
old results by exporting with their original version or retaining a compatible
environment. Create a new named Session in a fresh project for current work;
there is no same-project bypass. RawSqlResult keeps its independent datasource
reader and does not enter Frame Store. Static Help, actual local evidence, provider Runtime and
release acceptance are separate contracts.

Canonical public exports
-------------------------

.. autosummary::
   :toctree: api/
   :nosignatures:
   :caption: Classes

   FrameMethodFacts
   FrameLineage
   ColumnLineage
   SemanticObjectIdentity
   AnalysisFrame
   FrameAxis
   Column
   Population
   Grouping
   CalendarCorrespondence
   ComparisonBaseline
   Grain
   TimeScope
   InstantAnchor
   CalendarAxis
   ArtifactDigest
   ArtifactRef
   Finding
   FindingPage
   Session

.. autosummary::
   :toctree: api/functions/
   :nosignatures:
   :caption: Functions

   coordinates
   membership
   product
   in_population
   identity
   baseline
   row_count
   ordinal
   offset
   grain
   all_history
   since
   to_date
   trailing_duration
   trailing_periods

Session lifecycle
-----------------

.. automodule:: marivo.analysis.session
   :no-members:

.. currentmodule:: marivo.analysis.session

.. autosummary::
   :toctree: api/
   :nosignatures:
   :caption: Session lifecycle

   get_or_create
   resume
   recent
   inspect

Nested method-fact records
--------------------------

Read complete original explanation records through ``frame.method_facts`` and
its nested fields. ``FrameMethodFacts`` remains the aggregate public type.
Nested value, coordinate, support, method, term, metric, target, output and parent record types
retain native type/field Help through ``marivo.help(type(value))`` and
``marivo.help("analysis.frame_method_facts")``. They are not ``mv.*`` constructor
exports. Method variants retain their ``kind`` discriminants: ``additive``,
``ratio_component`` and ``linear`` for paired endpoints, and
``level_additive``, ``level_ratio_component`` and ``level_linear`` for one endpoint. Reading these facts grants no source execution
authority or new decomposition certification.

``facts.targets`` preserves the complete ordered target/output directory even
when no parents were selected. Each parent retains complete typed keys and
metrics with ``kind`` (level/absolute/relative), original rule/support trees,
``observed_total``, ``total``, explicit output paths and
``share_state`` (defined/zero_total). ``facts.show(n=...)`` and nested record
``show`` bound presentation only; complete fields remain available.

Schema, grid and saved Runtime boundaries
-----------------------------------------

``facts.output_schema`` and ``coordinate.schema`` expose saved complete layouts.
``facts.parent_schema`` preserves the original receiver's typed layout and frozen
comparison correspondence independently of the report's output columns; it
contains no original value table or executable declaration.

``method_value.column`` owns its physical dtype, unit and nullability.
``calendar_axis.grid`` contains frozen buckets/calendar interpretation.
``calendar_axis.scope`` reads the original scope after ``scope.by(grain)``;
a shifted axis may have None, which is never filled from a grid envelope.
``finding.value`` is saved content selected by finding_type/kind, including lag
facts where applicable. Publication failures expose their phase and concrete
repair directly through structured errors.
These read-only paths do not open constructors for nested implementation types.

Sampling coverage failures
--------------------------

A demanded fixed-clock coverage failure remains a ``MaterializationError`` with
an immutable ``sampling_context``. It identifies the output Metric, sample layer
and reducer, semantic time field, actual interval/timezone, and bounded complete
group coordinates. Each group reports expected/covered counts and up to three
missing instants; at most six groups are shown, with explicit truncation flags.
The rendered error includes the same facts and a concrete repair. Represented
NULL samples are distinct from missing clocks. Native diagnostics use the same
business statement, without a follow-up query.

Semantic column origins
-----------------------

``frame.lineage`` returns immutable ``values``, ``axes`` and ``presence`` mappings
on every carrier without evaluation or row reads. Values use complete output
names; axes use local tokens. Each ``ColumnLineage`` separates ``value_origins``
from ``context_origins``. Origins retain kind, path and consumed definition_id.
They preserve naming-only Measures, filtering context and independent recovery,
without restoring semantic execution authority or a historical producer graph.
Named Measures used in predicates contribute context origins; coordinates retain
row selection context too. Attribute weighted-mean denominators take value origins
from weights and context origins from valid-pair selection. Valid-pair counts
have context origins only. Direct-ratio components retain their own named Metric origins.
Use ``marivo.help("analysis.frame_lineage")`` for fields and bounded display.
