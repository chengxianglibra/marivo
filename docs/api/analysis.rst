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

``session.observe`` accepts one named Metric object, a nonempty list or tuple, or a
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
DuckDB HTTP JSON inputs keep concrete GET/POST query and body values in their
``md.json(...)`` semantic source definition. Evaluation checks all pending request
parameters before acquisition, then reuses each response across consumers and
projections in that evaluation. Requests can occur during schema preparation;
no automatic pagination or cross-evaluation response reuse occurs. Completed and
reopened Frames consume fixed data. No source arguments are added to these methods.
Reopen with ``session.persisted_frame(artifact_id)`` in the exact Session; the ID
must be a string. Every Frame exposes its captured ``session_id``. Stored roots
expose ``artifact_id``, ``findings()`` and ``evidence()`` directly.
Pending and memory Frames require ``persist()`` for commit identity and audit reads.
Identity attributes perform no Store reads. There is no public Artifact wrapper.
``show(n=20, max_output_bytes=8192)`` combines metadata, audit summaries and a
whole-row preview with exact omission counts. ``show(n=0)`` reads metadata only;
``max_output_bytes=None`` removes the byte limit while preserving the row limit.
``schema`` returns captured immutable fields, exact types, complete axis keys and
export layout without Store or payload I/O. Its ``fixed_axes``, ``value_contexts``,
``comparisons`` and ``decompositions`` expose only current result facts that were
captured and remain bound to that input. Missing optional context is unspecified;
it does not mean unfiltered, complete or valid data. Axis variants retain their
captured time grain, timezone and paired endpoints. Semantic refs are locators;
read details separately through the Session Catalog. ``read_page(columns=None, limit=1000,
cursor=None, axis_names=None, time_details=False)`` reads complete fixed data in ordered projected
pages, retaining complete coordinate keys. Limits are integers 1..10000, excluding
bool. FramePage exposes input_id, layout, coordinates, limit, total_count,
has_more, next_cursor and read_stats; its ``to_pandas()`` copies only this page
and has empty attrs. Repeat the same selection, aliases and limit when continuing.
``read_periods(target, limit=1000, cursor=None)`` reads saved calendar/interval
periods, paired endpoints or time-comparison correspondences for one current time
axis or comparison. ``FramePeriodPage`` carries the exact ``input_id``, immutable
items and read statistics. Period keys use the target's complete ordered axis key
and join to its coordinate slot values in ``read_page().coordinates``. Periods
are independent of the current row domain; missing paired endpoints and saved
comparison absence have their own distinct meanings. Limits and cursor bindings
match the bounded ``read_page`` contract.
``findings(limit=20, cursor=None)`` and ``evidence(limit=20, cursor=None)`` read
independent saved audit collections with limits 1..100. Both return exact input,
Session and Artifact identity, collection count and continuation state. No public
digest or second complete Evidence reader exists. Full data export uses
``to_pandas()`` and preserves its existing attrs snapshots and full validation.

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
does not establish a new provider Runtime qualification. Exact source quantiles use
DuckDB quantile_cont, PostgreSQL percentile_cont or ClickHouse
quantileExactInclusive; ClickHouse supports integer/floating inputs, while
DuckDB/PostgreSQL also admit Decimal. MySQL/Trino exact quantiles and source
Duration quantiles fail compilation. Explicit approximate quantiles use DuckDB
approx_quantile, Trino approx_percentile or ClickHouse quantileTDigest.
Approximate distinct uses approx_count_distinct, approx_distinct or uniqHLL12
respectively. PostgreSQL/MySQL reject both approximate families. Exact distinct
uses COUNT(DISTINCT), or ClickHouse uniqExact. Unsupported operations/types fail
before business reads; no source-ranked simulation or exact/approximate
substitution is performed.

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
Export returns a flat table with short axis names (final path components), full
value/presence names, nullable types and isolated interpretation attrs. Calendar
and interval axes expose actual half-open ``.start``/``.end`` timestamps in their
captured timezone. ``time_details=True`` adds calendar ``.label``,
``.nominal_start`` and ``.nominal_end``; named labels retain scalar types.
Paired time axes use left/right prefixes. Composite identities remain tuples.
``axis_names={"full.axis.name": "name"}`` accepts full names or unique axis tokens;
FrameAxis keys are rejected. Duplicate output names fail before payload reads.
Full and paged exports share these rules; cursors bind time_details as well.
Compute/persist remains explicit. Exported attrs cannot reenter computation.
Bounded previews use the same default coordinate interpretation.

Merged interval axes with multiple boundary timezones use UTC timestamps.
The common export timezone is captured in the interval descriptor, including for
empty pages. Fixed offsets with nonzero seconds also export in UTC without
changing instants; zero seconds normalize to minute offsets. This applies to
detail columns, pages and previews. Original zones remain in frozen metadata.

The interval descriptor now requires a ``timezone`` field. Frozen boundary data
is unchanged; older interval descriptor formats have no compatibility reader or
migration.

Explicit source investigation and contribution
----------------------------------------------

``frame.attribute(*, by=tuple | Population, permutations=1024, seed=0)`` returns an ordinary lazy
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

Paired mean, weighted mean and admitted component ratios additionally expose
``effect.within.<target>``, ``effect.mix.<target>`` and
``effect.entry_exit.<target>``. Fixed linear formulas preserve each ratio's own
denominator; mixed additive/rate formulas also expose ``effect.additive.<target>``.
Effects sum to each group's contribution and use the same absolute fresh parent
baseline for relative targets. Level and pure additive targets have no synthetic
rate/mix effects. Rules bind before business reads. Qualified paired quantile and
nonlinear targets use whole-Metric group-replacement Shapley;
there is no method override or group-count threshold. ``permutations`` is an integer
at least two and ``seed`` a nonnegative integer; both reject bool. Exact targets do
not sample. Level retains its existing share rules.

Rate/mix requires nonnegative group denominators and effective paired weights,
positive parent denominators, consistent parent/fine components and original
positive-support group values matching N/D. Zero support requires zero numerator
mass. These checks and effect closure use versioned numeric rounding budgets
without a business percentage allowance. Failures never switch rules or allocate
residuals. Facts retain the symmetric rule, component and entry/exit policy versions.

Sampled targets add ``contribution_std_error.<target>``. Qualified state routes
reuse fixed sufficient statistics/frequencies and retain all-coalition domain
proofs. Float SUM and native AVG use captured group SUM and SUM/count
reconstruction, respectively, retaining masks, NULL/empty and carrier limits.
These games disclose ``summary_reconstruction/v1``; reconstructed endpoints and
contributions may differ from native whole-input evaluation, with no bias bound.
Without a qualified state route, native queries run the original reducer over
coalition-selected endpoint rows on compatible original source owners. Native
quantile interpolation, approximate quantiles and ``approx_nunique`` retain their
original algorithms. Frequency states require the original native finisher to be
qualified; they do not bypass unsupported native operations. Approximate distinct
is native-only and never sums per-group estimates. Ordinary backend/type/source and classification qualifications
still apply; Population/membership games retain their existing limits.

No consistent snapshot, temporary table, raw-input materialization or drift audit
is required. Independent queries may see changed inputs. Parent/player keys,
endpoint scopes and captured definitions stay fixed. Each coalition and empty/full
endpoint is evaluated once and cached. Only visited native values are validated;
unvisited combinations and future source values are not certified. Query failures
reject the whole call without retries, fallback or partial publication.

Each player switches every leaf occurrence together, then the complete original
formula executes. Typed subtraction precedes Float64 averaging. Relative values
use one absolute cached baseline. Complete permutations close to cached endpoint
change within conversion/summation roundoff; residuals are never redistributed.
State SE estimates permutation error conditional on captured summaries, excluding
summary bias; native-query SE describes observed marginal
variation and cannot separate source changes or provide a drift confidence bound.

For n>=1, cached unique coalitions are bounded by
``min(2**n, 2 + permutations*(n-1))``. State capture reads stay fixed; native query
counts may grow with coalitions and work can approach O(U*N). Actual statements,
returned rows/decoded bytes and unknown scan/wire statistics are disclosed without
claiming that one query is one physical scan. No row sampling, player truncation,
adaptive count or alternate quantile algorithm is introduced.

Closed facts distinguish ``FrameShapleyGame`` state policies from
``FrameShapleyQueryGame`` independent reads and evaluated-coalition policies.
``FrameShapleyQuery`` records actual query receipts;
``FrameShapleyNativeQuantile`` retains the original reducer/q/algorithm contract.
All remain inert on cold recovery. Read ``analysis.frame_shapley_method`` and its
nested fact targets.
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
single Store 23 writer transaction. Memory persist never reevaluates. Stored
persist validates commit metadata and returns itself without another publication.
A derived or computed memory Frame never exposes an ancestor commit identity. Unknown publication outcome is reconciled, not recomputed.

Completed data owns source-free values, coordinates, presence and one audit:
inherited source facts plus the current evaluation. It retains no producer,
semantic graph, connection or hidden aggregate components. Read, evaluation and
commit times remain separate. Publication records remain internal and do not
promise complete data lineage. Retain Session and Artifact IDs for exact reopening.
If the Artifact ID is lost, recover the Session and call
``session.persisted_frames(limit=20)``. Each immutable summary contains
``artifact_id``, ``committed_at``, optional ``description`` and the existing
``FrameSchema``. Inspect descriptions and structure before opening an exact ID
with ``persisted_frame``. Ambiguous candidates require further inspection or
caller selection; no implicit latest result is selected.

``frame.persist(description=None)`` optionally saves caller-supplied purpose text
unchanged in the atomic commit. Read it through ``saved.description``; pending and
memory Frames require explicit persistence first. Stored persist retains the
original description for omitted/None or equal text and rejects a different
non-None value. New commits do not inherit descriptions from input commits.
Descriptions help identify results and do not certify their meaning.

The directory reads one metadata snapshot with no result files, Catalog or
source access, recovery, publication or Session update. Limits are 1..100; pages
are ordered by commit time and ID descending. Continue with the returned
``next_cursor`` and identical limit. Cursors bind Store, Session, operation and
limit. New commits are discovered from the first page. Cards show bounded output
names and axes; typed schemas remain complete. Corrupt metadata fails explicitly.

Store 23 uses ``marivo.frame_output/v12``, ``frame.v12`` and ``frame-output-v12`` for
all Frames, under ``.marivo/analysis/generations/v23/``. Owned method files share
the existing atomic publication, receipt and unknown-commit reconciliation.
Store 23 refuses older-generation Session/result/audit data unchanged, without
migration or dual reads. Documented absent-current discovery may return None or
empty. Current work may create a new named Session in the same project while
older generations coexist unchanged; discovery warns about older paths. Preserve
required old results by exporting with their original version or retaining a
compatible environment. RawSqlResult keeps its independent datasource
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
   EvidencePage
   FrameSchema
   FrameField
   FrameAxisSchema
   FrameOrdinaryAxisSchema
   FrameCalendarAxisSchema
   FrameIntervalAxisSchema
   FramePairedAxisSchema
   FrameBuiltinGrain
   FrameNamedGrain
   FrameCalendarEndpoint
   FrameIntervalEndpoint
   FrameFixedAxis
   FrameAxisTime
   FrameComparedTime
   FrameFixedTime
   FrameAllHistoryWindow
   FrameSinceWindow
   FrameToDateWindow
   FrameTrailingDurationWindow
   FrameTrailingPeriodsWindow
   FrameObservationContext
   FrameAxisGroup
   FrameFixedGroup
   FrameStatisticContext
   FrameComparison
   FrameComparisonGroup
   FrameComparisonValue
   FrameComparisonPresence
   FrameOrdinaryComparisonBinding
   FrameCalendarComparisonBinding
   FrameIntervalComparisonBinding
   FrameLevelTarget
   FrameChangeTarget
   FrameDecomposition
   FrameDecompositionField
   FramePage
   FrameReadStats
   FramePeriodPage
   FrameCalendarPeriod
   FrameIntervalPeriod
   FramePairedPeriod
   FrameComparisonPeriod
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

Semantic column origins
-----------------------

``frame.lineage`` returns immutable ``values``, ``axes`` and ``presence`` mappings
on every carrier without evaluation or row reads. Its ``input_id`` equals the
same Frame's ``schema.input_id``; derived and reopened Frames rebind the metadata
to their exact current input. Values use complete output
names; axes use local tokens. Each ``ColumnLineage`` separates ``value_origins``
from ``context_origins``. Origins retain kind, path and consumed definition_id.
They preserve naming-only Measures, filtering context and independent recovery,
without restoring semantic execution authority or a historical producer graph.
Named Measures used in predicates contribute context origins; coordinates retain
row selection context too. Attribute weighted-mean denominators take value origins
from weights and context origins from valid-pair selection. Valid-pair counts
have context origins only. Direct-ratio components retain their own named Metric origins.
Use ``marivo.help("analysis.frame_lineage")`` for fields and bounded display.
