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
``attribute`` binds two explicit ``AttributeEndpoint`` inputs and new business
classifications; optional contribution requires a separately qualified source route.
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

Current data operations
-----------------------

``select`` derives named values; ``where`` accepts a same-Frame boolean Column;
``top`` is global and deterministic. ``reduce`` uses current represented cells,
with independent valid masks per statistic. It does not recompute original
Metrics. New facts or original overall questions require explicit source inputs.

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
statistics for ``reduce`` compositions; each named item outputs one column.
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

``align`` preserves each side's values; ``compare`` emits left, right, absolute
and relative value groups. Relative change is ``(left-right)/abs(right)``.
Presence is separate from NA; missing endpoints never become zero. Ordinary
axes require complete explicit ``on``. Only zero axes or one sole calendar axis
permit the specified inference. Handles cannot be rebound by matching names or
fingerprints. ``coord`` accepts a single-component complete axis; composite
identities remain complete keys, not one scalar quantity.

``ordinal``, supported builtin ``period_offset`` and ``previous_year`` consume
frozen finite calendar directories. Matching validates complete compatible
nominal partitions and actual boundaries. Paired output retains both endpoint
descriptions and a self-contained map. It does not reconstruct a CalendarAxis,
current model or timezone database. Relationship correspondence, broadcasting,
calendar coarsening, partitioned top and original rollup/drill/share receivers
are outside this Frame surface; current-row reduce is not their equivalent.

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

``session.attribute(left, right, *, by, parents=None, parent_on=(),
parent_how="exact", contribution=False)`` returns an ordinary AnalysisFrame.
``AttributeEndpoint(metrics, during=None, where=None, memberships=())`` copies its
ordered Metric mapping. ``where`` is a native Deferred predicate; ``memberships``
holds explicit Grouping allocation rules independently of selected output keys.
The endpoint retains its own TimeScope/CalendarAxis through ``during``.
Left is current and right is baseline. Default values are ``left.q``, ``right.q``
and ``absolute.q``; presence is separate. Contribution adds ``contribution.q``
and ``contribution_share.q``; direct ratios also expose ``numerator_left.q``,
``numerator_right.q``, ``denominator_left.q`` and ``denominator_right.q``.
There is no relative-change output or synthetic root row. Empty explicit parents
stay empty; omitted parents mean one overall parent. Full ordinary parent axes
require explicit dual binding. Exact/outer parent correspondence does not change
the outer fine-key union or restore missing values as zero. Calendar and
paired-calendar Frame parents retain their explicit binding contracts; interval
and paired-interval parent axes refuse before reads. Reduce the interval axis before
selecting parents, or use an explicit CalendarAxis parent and endpoint scopes.

Default investigation collects no contribution proof and creates no new method
facts. Contribution currently requires the qualified physical DuckDB table
root/fiber statement route; files and remote providers are not qualified by their
datasource access. Unsupported rules or missing component proof fail rather than
downgrade. An explicit default request remains available when its observation is
valid. Native results retain their actual completed types, units and NA; no
universal Decimal precision or source-read count is promised.

Successful contribution facts are inert original explanations. Filtering or
reducing data preserves that history without changing original denominators or
certifying a new decomposition. Saved derivatives own their copied payloads;
the original Artifact and source are not needed. Read the exact getter contract
with ``marivo.help("analysis.analysis_frame.method_facts")`` and the concrete
family with ``marivo.help("analysis.frame_method_facts")``.

Evaluation, publication and recovery
------------------------------------

Compute creates no Run, Artifact or temporary saved result. Expression persist
completes evaluation first, then publishes that existing result ID through the
single Store 14 writer transaction. Run means publication; evaluation can precede
admission. Memory persist never reevaluates. Stored persist validates commit
metadata and returns itself without another Run. A derived Frame never exposes
an ancestor Artifact. Unknown publication outcome is reconciled, not recomputed.

Completed data owns source-free values, coordinates, presence and one audit:
inherited source facts plus the current evaluation. It retains no producer,
semantic graph, connection or hidden aggregate components. Read, evaluation and
commit times remain separate. Session Run records retain publication references
without promising complete data lineage.

Store 14 uses ``marivo.frame_output/v3``, ``frame.v3`` and ``frame-output-v3`` for
all Frames, under ``.marivo/analysis/generations/v14/``. Owned method files share
the existing atomic publication, receipt and unknown-commit reconciliation.
Store 14 refuses older-generation Session/result/audit data unchanged, without
migration or dual reads. Documented absent-current discovery may return None or
empty; create cannot bypass an old-only project. Preserve required
old results by exporting with their original version or retaining a compatible
environment. Create a new named Session in a fresh project for current work;
there is no same-project bypass. RawSqlResult keeps its independent datasource
reader and does not enter Frame Store. Static Help, actual local evidence, provider Runtime and
release acceptance are separate contracts.

Canonical public exports
-------------------------

.. autoclass:: AttributeEndpoint
   :members:

.. autoclass:: FrameMethodValue
   :members:

.. autoclass:: FrameMethodCoordinate
   :members:

.. autoclass:: FrameMethodSupport
   :members:

.. autoclass:: FrameAdditiveMethod
   :members:

.. autoclass:: FrameRatioMethod
   :members:

.. autoclass:: FrameLinearTerm
   :members:

.. autoclass:: FrameLinearMethod
   :members:

.. autoclass:: FrameMethodMetric
   :members:

.. autoclass:: FrameMethodParent
   :members:

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

.. autofunction:: period_offset

.. autofunction:: previous_year

.. autofunction:: grain

.. autoclass:: Grain
   :members:

.. autoclass:: TimeScope
   :members:

.. autoclass:: InstantAnchor
   :members:

.. autoclass:: BeforeEndBoundary
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

.. autoclass:: FailedRun
   :members:

.. autoclass:: IncompleteRun
   :members:

.. autoclass:: SucceededRun
   :members:

.. autoclass:: RunPage
   :members:

.. autoclass:: Session
   :members:

.. automodule:: marivo.analysis.session
   :members:
