marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

R2.6 Query execution and terminal reading
--------------------------------------------

``session.observe({"revenue": revenue, "aov": aov}, during=scope,
by=(region,), where=region == "APAC")`` binds ordinary observation context in an
immutable ``Query`` without schema/business reads or Run admission. Each source
reduction occurrence retains its intrinsic conditions and receives common
conditions independently. Scalar paths must be directed, single-valued and
unambiguous. ``query.contract().show()`` describes the frozen context and unresolved
physical types. ``query.execute()`` now evaluates ordinary numeric source/local
routes and atomically publishes terminal output in Store 10. Each invocation
creates a new Run and reevaluates its inputs; preparation failures create no Run.
The returned terminal Artifact reads the same saved output as a reopened handle.

Construct exact time with ``session.instant(value, timezone=None)`` and
``session.time_scope(start=..., end=..., timezone=None)``. Omitted timezone uses
the saved report timezone; returned scope boundaries are ``InstantAnchor`` values.
``scope.by(mv.grain("day"))`` constructs complete calendar buckets. Date/offset-free
strings use the frozen zone, aware inputs preserve their instant, and naive
Python datetimes or ambiguous/nonexistent clocks reject. ``during=None`` adds no
time filter; sampling dependencies still require an explicit period. Scalar
fields support ``via((relationship, ...))`` and versioned attributes support
``at(anchor)``. Population and window inputs remain outside this R2.2 surface.

``Artifact`` has only exact identity, a committed output manifest and a read
service. It offers ``show(n=20)``, ``to_pandas()``, ``findings()`` and
``evidence_digest()``; it has no ``contract()`` or DSL continuation.
``session.artifact(artifact_id)`` accepts an exact string ID, rejects old graph
descriptors unchanged and does not load current semantics or Parquet rows.
Metadata opening and repr read no Parquet. ``show(n=20)`` decodes bounded rows;
receipt hashing can still read an entire consumed part. ``to_pandas()`` returns
a complete isolated DataFrame with RangeIndex, kind/name columns and data-only
``attrs["marivo"]``. ``findings(limit=20, cursor=None)`` reads a bounded page;
``evidence_digest()`` takes no arguments and returns all saved Evidence in
``.items`` with collection digests. Saved facts and coordinates have nested Help
contracts: ``marivo.help(fact)`` and ``marivo.help(coordinate)``. Truncated
Artifact cards route to ``to_pandas()`` and ``evidence_digest()``. Record reads require no result tables. Store 10 saves a wide nullable
primary and explicit output attachments, with data-only schema, receipts and
self-contained output records. Calendar schemas preserve the selected timezone
alongside UTC boundary instants; read-scope summaries preserve Boolean grouping.
It saves no graph, definition fingerprint,
continuation or internal aggregation components. Ordinary observe has no Findings;
actual reads and checks produce Evidence. Earlier Stores remain unchanged and
cannot be resumed, migrated or bypassed by choosing another Session name.

.. autoclass:: InstantAnchor
   :members: show

.. autoclass:: CalendarAxis
   :members: show

.. autoclass:: Query
   :members:

.. autoclass:: Artifact
   :members:

The following sections describe the retired execution surface pending replacement; its graph publisher is unavailable in Store 10.

Construct typed algebra through ``session.members(entity_ref)`` and its owned
read/observe operations. The former Population, Metric Dataset and generic Dataset APIs have been retired.
Logical typed values describe work
without source I/O. ``execute()`` commits a Run and returns an immutable
Materialized value. Its owned fields and methods describe valid continuations.
Use ``show()`` for bounded current state and ``contract()`` for mechanical input
requirements. ``to_pandas()`` is the terminal boundary for custom analysis.

``session.members(entity)`` performs schema-only preflight without reading
business rows or creating a Run. Executing the unfiltered domain reads and saves
the complete member key set, may scan the entire Entity source, and has no default
row truncation. Primary-key uniqueness, unversioned identity and display limits
do not bound member count. Execution shares a 600-second budget across the graph.

Business data display uses ``show(*, n=None, max_output_bytes=8192)`` on
materialized values and tables. The default fits complete rows within 8 KiB;
there is no fixed five-row cap. ``n=20`` requests at most twenty rows, ``n=0``
shows metadata only, and ``max_output_bytes=None`` removes the byte cap while
preserving an explicit ``n``. Counts and recovery hints disclose display
omissions. Budgets include the printed newline. Values preserve precision;
Duration cells show integer ticks with their unit. Cell state counts cover the
whole result. The card shows interpretation facts and conditional evidence
reads; use ``contract()`` for the full operation directory.

Association results, coefficient/selected views and terminal table columns
disclose ``pairing_key`` from the retained original input domain: each Entity,
field and coordinate role in original order. The complete tuple pairs original
observations; association output keys identify candidate pairs. These facts use
frozen definitions without source access or new Runs.
Identical terminal-column facts are printed once with all applicable column
labels; different units and keys remain separate.

Execution retains results as project-local Parquet. There is no analysis result
storage setting or database result storage. Session recovery reads Store 10;
older generation files are not migrated or rewritten.

Execution errors carry an optional ``run_ref`` after the invocation's Run is
stored. Inspect that exact record with ``session.get_run(error.run_ref).show()``
in the same Session; its Help target is ``analysis.session.get_run``. Failures
before Run admission carry no new Run identity. Existing recovery errors retain
their original Run association. Correct the cause before executing again:
each source execution creates a new Run and preserves failed history.
``get_run`` reads incomplete, failed and succeeded Runs; ``abandon_run`` only
reconciles incomplete or failed Runs and preserves their history.

Start discovery with ``marivo.help("analysis")``. Focused Help owns signatures,
examples and constraints; errors preserve concrete diagnostics and own repair.
Entry provides Session bootstrap/recovery and source selection. Named method and
input groups narrow discovery by task. ``dataset.contract().show()`` links current
admitted calls to their canonical Help leaves; return types and prerequisites
provide focused continuations without enumerating the entire API. Exact current semantic
refs or catalog entries select governed inputs, while bound relation fields carry
exact graph ownership. Cross-Session operands are rejected.
For a type, use its canonical target such as
``marivo.help("analysis.LogicalAnalysisDomain")``. Receiver methods can be queried
with ``marivo.help(value.method)``; ``analysis.group_by`` is not an alias.

The admitted J1–J4 Entity-domain path starts at ``session.members(entity_ref)``.
Its logical relations expose ``execute()`` and ``contract()``; materialized
relations expose ``show()``, ``to_pandas()`` and ``contract()``. The public
``AnalysisContract.actions`` tuple contains typed ``AnalysisAction`` values
with the receiver call and exact Help target. The combined result card redacts
Entity member keys. ``python -m marivo help`` is only the installed-interpreter
bootstrap; focused contracts remain in ``marivo.help(...)``.

Entity-domain values
--------------------

.. autoclass:: AnalysisAction
   :members:

.. autoclass:: AnalysisContract
   :members:

.. autoclass:: ReferenceWeights
   :members:

.. autofunction:: reference_weights

.. autoclass:: LogicalAnalysisDomain
   :members:

.. autoclass:: MaterializedAnalysisDomain
   :members:

.. autoclass:: LogicalNumericRelation
   :members:

.. autoclass:: MaterializedNumericRelation
   :members:

.. autoclass:: LogicalRatioRelation
   :members:

.. autoclass:: MaterializedRatioRelation
   :members:

.. autoclass:: LogicalTimeRunResult
   :members:

.. autoclass:: MaterializedTimeRunResult
   :members:

.. autoclass:: LogicalDeviationResult
   :members:

.. autoclass:: MaterializedDeviationResult
   :members:

.. autoclass:: LogicalAssociationResult
   :members:

.. autoclass:: MaterializedAssociationResult
   :members:

.. autofunction:: member

.. autoclass:: MemberAxis
   :members:

.. autoclass:: RowMethod
   :members:

Grouping and current rows
-------------------------

First Metric grouping uses ``members.observe(metric, by=(...))``. Use
``by=(mv.member(),)`` to retain the receiver's complete member identity.
Category ``where()`` retains the CategoryRelation family. Numeric, ratio
and statistic results retain their admitted ``group_by`` and reductions. Member
domains and Category, Boolean and Temporal results have no ``group_by``; scalar
nonnumeric results support overall ``aggregate(mv.count())`` and
``aggregate(mv.count_defined())``. Member domains have no direct count operation.

No observation or grouping accepts ``groups`` or an explicit target domain.
For prescribed report rows, export with ``to_pandas()`` and complete the display
externally. External results cannot re-enter typed analysis. Complete member
domains, empty time buckets and Metric empty-set rules remain part of observations.
Saved completion or standalone group-domain Artifacts require source re-execution;
recovery errors preserve their files. Other current Artifacts remain recoverable.

Member versions and scalar attributes
-------------------------------------

``Session.members(entity, at=...)`` keeps complete ordered keys. Versioned
Entities require an aware instant or ``TimeScope.before_end``. Attribute
``read(field, at=..., via=...)`` binds its version independently and returns
Numeric, Category, Boolean or Temporal relations. Scalar paths require complete
single-valued correspondence and coverage. Fixed member projections retain
Subject parts and cannot introduce a live attribute read.

``members.observe(metric, during=grid)`` computes one observation per complete
time bucket. ``members.read(field, at=grid.before_end)`` and cumulative
``members.observe(metric, at=grid.end)`` bind the endpoint grid directly.
``grid.end`` selects the exact boundary; ``grid.before_end`` selects its symbolic
left limit for versioned attributes. During and at are alternatives. Spatial
``by`` classifications cannot create an independent time axis.
Unversioned attributes retain their stable value in every endpoint cell.

Measure reads admit direct columns and qualified bound row expressions. A
computed Measure evaluates through Ibis on the consumed owner rows, and its
definition and bound-field fingerprints are retained in the graph.

.. autoclass:: BeforeEndBoundary
   :members:

.. autoclass:: LogicalBooleanRelation
   :members:

.. autoclass:: MaterializedBooleanRelation
   :members:

.. autoclass:: LogicalTemporalRelation
   :members:

.. autoclass:: MaterializedTemporalRelation
   :members:

.. autoclass:: LogicalSelectedBooleanRelation
   :members:

.. autoclass:: MaterializedSelectedBooleanRelation
   :members:

.. autoclass:: LogicalSelectedTemporalRelation
   :members:

.. autoclass:: MaterializedSelectedTemporalRelation
   :members:

.. autoclass:: LogicalSelectedNumericRelation
   :members:

.. autoclass:: MaterializedSelectedNumericRelation
   :members:

Public exports
--------------

The following entries follow the pinned public export order. Case-colliding
constructors are documented inline to support case-insensitive filesystems.

.. autoclass:: DatasetByteCount
   :members:

.. autoclass:: MaterializedDatasetState
   :members:

.. autoclass:: ForecastHorizon
   :members:

.. autoclass:: ForecastModel
   :members:

.. autoclass:: ExactAlignment
   :members:

.. autoclass:: OuterAlignment
   :members:

.. autoclass:: OrdinalTimeKey
   :members:

.. autoclass:: Grain
   :members:

.. autoclass:: TimeScope
   :members:

.. autoclass:: ArtifactDigest
   :members:

.. autoclass:: ArtifactRef
   :members:

.. autoclass:: ArtifactSummary
   :members:

.. autoclass:: FailedRun
   :members:

.. autoclass:: Finding
   :members:

.. autoclass:: FindingPage
   :members:

.. autoclass:: IncompleteRun
   :members:

.. autoclass:: RunPage
   :members:

.. autoclass:: SessionGraph
   :members:

.. autoclass:: SucceededRun
   :members:

.. autoclass:: Session
   :members:

.. autofunction:: all_of

.. autofunction:: any_of

.. autofunction:: not_

.. autofunction:: grain

.. autofunction:: time_scope

.. autofunction:: exact

.. autofunction:: outer

.. autofunction:: ordinal

.. autofunction:: periods

.. autofunction:: naive

.. autofunction:: drift

.. autofunction:: seasonal_naive

.. automodule:: marivo.analysis.runtime_metric
   :members:

.. automodule:: marivo.analysis.session
   :members:

R4.5 qualification boundary
----------------------------------------

Public J1–J4 relations share the typed graph Runtime and Store 7. R1 schema-only
preflight may precede Run allocation; business rows are read only after admission.
Legacy graph reads verify their snapshot, primary receipt and required parts
without current Semantic or datasource access; ``session.artifact`` no longer
opens this format. Retired Dataset families have no current API.
Old-generation projects are preserved and are not migrated or read through a fallback.

Ranking and terminal tables
---------------------------

.. currentmodule:: marivo.analysis

``values.rank(order="descending", ties="dense", partition_by=(category,))``
returns a ranking with fixed ``values`` and ``ranks`` numeric views. Filtering
and global ``limit`` preserve original ranks, ordering and fixed references.
For per-partition Top-K, filter ``ranking.ranks.value.is_defined()`` first, then
filter ``selected.ranks.value.lte(k)`` on that selected result.

``mv.table(amount=ranking.values, rank=ranking.ranks)`` requires complete equal
keys, one Session and one source/fixed mode. The terminal Artifact exposes
``artifact_ref``, bounded ``show()`` and an isolated ``to_pandas()`` containing
keys once and authored value columns. Non-Defined values export as missing;
only the Artifact and ``show()`` preserve their tags and reasons. Tables have
no analysis contract or column attributes.

Tables retain the original fit authority for every deviation column, including
tables mixing scores with categories, original values or another fit. A fitted
ranking's ``ranks`` projection retains its original fit scope and its own rank
values; both logical and materialized projections expose the same retained parts
through ``contract()`` and ``show()``. Recovery verifies current values against
captured fitted and ranking parts without fitting again. Missing or corrupt
authority rejects with a typed repair.

.. autoclass:: LogicalRankingResult
   :members:

.. autoclass:: MaterializedRankingResult
   :members:

.. autofunction:: table

.. autoclass:: LogicalTable
   :members:

.. autoclass:: MaterializedTable
   :members:

Dual comparison results
-----------------------

``current.compare(baseline, align=exact())`` infers the sole changed target/time
condition and returns two mandatory views. ``absolute`` preserves the original
unit and attribution authority; ``relative`` is dimensionless and computes
``(current-baseline)/abs(baseline)`` independently. A zero baseline makes only
the relative Cell Undefined. Both Cells publish in one Artifact; select a view
before numeric continuations. Exact alignment checks unknown key equality;
``outer()`` keeps missing sides unless ``missing="empty_value"`` is explicit.
Distinct complete time grids require ``time=ordinal()`` and equal lengths.

.. autoclass:: LogicalComparisonResult
   :members:

.. autoclass:: MaterializedComparisonResult
   :members:

Allocated absolute changes
--------------------------

``change.attribute(axes=(channel,), mode="joint", top_k=5)`` chooses the
allocation method from the original endpoint states. Sum/count/linear changes
use additive differences; mean/weighted_mean/original ratios allocate each
numerator against its side's total denominator before subtracting. Logical
inputs may explicitly expand the frozen observations; fixed inputs require
the axes already retained. Relative/nested changes and direct-only aggregates
cannot acquire allocation authority.

``joint`` emits complete axis tuples. ``hierarchy`` requires at least two axes
and emits each authored prefix. A common Top-K mapping uses both endpoint bases;
typed remainder masks distinguish a real ``"Other"`` label. Every resolution
reconciles independently, with no balancing residual. The ``contribution``,
``current`` and ``baseline`` numeric views always share complete keys.
``where`` keeps the original target, full allocation and reconciliation parts
and revokes current-subdomain completeness even when every row remains selected.

.. autoclass:: LogicalAttributionResult
   :members:

.. autoclass:: MaterializedAttributionResult
   :members:

R6 recovery and retired consumers
---------------------------------

Comparison, selection/cohort, fixed references, ranking, attribution and terminal
tables describe the retired typed graph path; its publisher rejects before Run admission in Store 10.
``session.artifact(id)`` rejects this graph format at the R2.1 terminal boundary.
Missing parts revoke the affected legacy operations. Tables remain
terminal. Original reductions with group keys publish
``MaterializedGroupedNumericRelation``; Singleton reductions publish
``MaterializedRolledNumericRelation``. These are not the new terminal Artifact type.

The old Metric Dataset ``compare`` and public Delta/Attribution Dataset types
are removed. Their Help targets are unresolved, with no compatibility redirect.
R8 Statistical Relations
------------------------

.. autoclass:: LogicalCoefficientRelation
   :members:

.. autoclass:: LogicalForecastResult
   :members:

.. autoclass:: MaterializedForecastResult
   :members:
