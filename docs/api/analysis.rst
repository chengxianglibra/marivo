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

Source grouping accepts ordered fields or ``Grouping`` rules, or a complete
``Population`` domain. ``identity(entity)`` declares one complete identity rule;
``coordinates(by=(identity(entity),), keys=((("C1",),),))`` declares a finite
requested identity domain. Every identity value is a complete primary-key tuple.
Coordinates also accepts field coordinates and correlated field/identity tuples.
``population.axes`` reuses grouping definitions without selected keys.
``by=population`` selects complete output keys, including keys without facts.
CalendarAxis is a calendar Grouping. Membership rules preserve correlated tuples
through the separate ``memberships`` parameter on observe or Metric.per;
Population stores no allocation rules.

``population.count()`` counts complete distinct tuples as a lazy integer Metric,
including Null categories and references without source facts. Empty domains
count 0; the zero-axis unit domain counts 1. Ordinary grouping coordinates must
already exist in the Population. Classify a new attribute while retaining identity
before counting members by it; projecting to the attribute alone counts categories.

Use the returned ``compute()`` Frame for complete fixed memory data and the
returned ``persist()`` Frame for committed data. Derived operations remain lazy.
Reopen with ``session.persisted_frame(artifact_id)`` in the exact Session; the ID
must be a string. ``frame.artifact`` is available only for a persisted root and
provides audit-only ``show()``, ``findings()`` and ``evidence_digest()``.
Frame owns ``show(n=20)`` and ``to_pandas()``. Artifact is not a computation input.

Public classes describe returned values; they do not authorize direct construction
or subclass extension. Use ``mv.*`` for explicit descriptions/composition,
``session.*`` when introducing Catalog, Store, execution ownership or the frozen
default timezone, and receiver methods/properties for existing context.
``mv.session.create/resume/recent/inspect`` establish or discover lifecycle context.

Current data operations
-----------------------

``select`` derives named values; ``where`` accepts a same-Frame boolean Column;
``top`` is global and deterministic. ``reduce`` uses current represented cells,
with independent valid masks per statistic. It does not recompute original
Metrics. New facts or original overall questions require explicit source inputs.

``frame.over(statistic, by=())`` reduces each current partition and broadcasts its
result as a Column on the exact same input, preserving complete keys and row order.
It accepts the same sum/mean/corr/pair_count and reusable row_count requests as
reduce, with the same sample, numeric and unit rules. ``by`` contains distinct
complete current axes; () uses all rows. Empty input stays empty. Nested over
counts repeated broadcast values, and where/top consume the full input partition
before selecting rows.

``column.share(along=axes, zero_denominator="null")`` divides current values by
their partition sum, summing along the named axes and partitioning by the remaining
axes. ``along`` is a nonempty tuple of distinct complete same-input axes; zero-axis
inputs require explicit division. Signed shares can exceed [0,1]; zero totals give
NA unless this division requests ``"error"``. Unknown units stay unknown. Filtering
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
``compare`` accepts a nonempty ordered mapping of named same-Session Frames,
including a single baseline. It retains current axes and values, then adds each
alias's ``baseline``, ``absolute`` and ``relative`` groups. For example,
``current.compare({"yoy": baseline}, match={"yoy": mv.offset(years=-1)})``
produces ``revenue`` and ``yoy.baseline.revenue``, ``yoy.absolute.revenue``,
``yoy.relative.revenue``. Relative is ``(current-baseline)/abs(baseline)``.
``current.present`` and ``yoy.present`` are row-existence flags independent of
value NA; inherited flags live below ``current.presence.*`` / ``yoy.presence.*``.
Missing rows/NA never become zero. Duration baseline/absolute retain their tick
unit and relative is dimensionless; strict child policies remain required.

Each alias's ``on`` binds every complete axis, except zero axes or one sole
interval/calendar axis permit unique structural inference. ``match`` is also
alias-keyed. A compatible immediate frozen shift relationship can supply it;
wrong anchors and conflicting explicit rules reject. ``exact`` checks complete
actual joint keys; ``outer`` preserves current order then mapped baseline-only
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
does not establish a new provider Runtime qualification. Exact source quantiles
remain specifically qualified; exact and approximate algorithms never substitute.

``show(n=0)`` and Artifact audit read metadata only. Positive show decodes the
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
Export preserves exact row order, raw-key MultiIndex, nullable types and isolated
typed frozen-interpretation attrs. Exported attrs cannot reenter computation.

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
Relative contributions normalize absolute contributions by the absolute parent
baseline. Defined zero totals retain signed contributions and NA shares.

``by`` accepts nonempty semantic classification tuples or one complete Population
with its correlated keys, membership and allocation rules. Frozen interval,
calendar and paired parent bindings retain their complete correspondence and
original endpoint scopes. Selected parents remain fixed when source values
change; attribution does not rerun historical anomaly selection.

Contribution requires the qualified native DuckDB physical-table route. Files
and remote providers do not qualify through datasource accessibility. Original
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
single Store 16 writer transaction. Memory persist never reevaluates. Stored
persist validates commit metadata and returns itself without another publication.
A derived Frame never exposes an ancestor Artifact. Unknown publication outcome is reconciled, not recomputed.

Completed data owns source-free values, coordinates, presence and one audit:
inherited source facts plus the current evaluation. It retains no producer,
semantic graph, connection or hidden aggregate components. Read, evaluation and
commit times remain separate. Publication records remain internal and do not
promise complete data lineage. Retain Session and Artifact IDs for exact reopening.
Lost return references require explicit resubmission, accepting duplicate saved
outputs and possibly changed source data. There is no public result directory.

Store 16 uses ``marivo.frame_output/v5``, ``frame.v5`` and ``frame-output-v5`` for
all Frames, under ``.marivo/analysis/generations/v16/``. Owned method files share
the existing atomic publication, receipt and unknown-commit reconciliation.
Store 16 refuses older-generation Session/result/audit data unchanged, without
migration or dual reads. Documented absent-current discovery may return None or
empty; create cannot bypass an old-only project. Preserve required
old results by exporting with their original version or retaining a compatible
environment. Create a new named Session in a fresh project for current work;
there is no same-project bypass. RawSqlResult keeps its independent datasource
reader and does not enter Frame Store. Static Help, actual local evidence, provider Runtime and
release acceptance are separate contracts.

Canonical public exports
-------------------------

.. autoclass:: FrameMethodFacts
   :members:

.. automethod:: FrameMethodFacts.show

.. autoclass:: AnalysisFrame
   :members:

.. autoclass:: FrameAxis
   :members:

.. autoclass:: Column
   :members:

.. autoclass:: Population
   :members:

.. autoclass:: Grouping
   :members:

.. autoclass:: CalendarCorrespondence
   :members:

.. autofunction:: coordinates

.. autofunction:: membership

.. autofunction:: product

.. autofunction:: in_population

.. autofunction:: identity

.. autofunction:: row_count

.. autofunction:: ordinal

.. autofunction:: offset

.. autofunction:: grain

.. autoclass:: Grain
   :members:

.. autoclass:: TimeScope
   :members:
   :exclude-members: model_dump

.. autoclass:: InstantAnchor()
   :members:

.. autoclass:: CalendarAxis
   :members:

.. autofunction:: all_history

.. autofunction:: since

.. autofunction:: to_date

.. autofunction:: trailing_duration

.. autofunction:: trailing_periods

.. autoclass:: Artifact
   :members:

.. autoclass:: ArtifactDigest
   :members:

.. autoclass:: ArtifactRef
   :members:

.. autoclass:: Finding
   :members:

.. autoclass:: FindingPage
   :members:

.. autoclass:: Session
   :members:

.. automodule:: marivo.analysis.session
   :members:

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
