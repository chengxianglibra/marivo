marivo.analysis
===============

.. currentmodule:: marivo.analysis

.. automodule:: marivo.analysis
   :no-members:

Lazy inputs and terminal output
-------------------------------

``Population`` is a finite logical coordinate domain. ``Query`` binds named
quantities; ``Column`` locates values on its exact input. Compose all DSL work
before ``Query.execute()``. The returned ``Artifact`` has identity, bounded
``show(n=20)``, complete ``to_pandas()``, paged ``findings()`` and
``evidence_digest()``. It has no contract or DSL continuation.

``session.observe(metrics, during=..., by=..., where=..., members=...)`` consumes
resolved native Deferred Metrics. ``session.query(population)`` creates a
keys-only Query. ``session.values`` captures named literal references on complete
Field/identity coordinates. ``session.read(population, fields)`` is the unique
attribute-read entry: one explicit Session, one Entity identity axis and a named
Field mapping. There is no Population.read or ambient Session lookup. Computed
Measure expressions belong in observe.

Populations support complete-key projection, sets, classification and declared
identity image/preimage. Explicit identities require a resolved Entity and every
primary-key component; a literal key does not prove source presence. Members
selection and attribute versions retain their explicit binding. Query-owned
columns are combined only after compatible input binding, align or broadcast.
``Query.coord(axis)`` constructs without source access; preparation rejects a
composite identity coordinate as a scalar quantity. Complete compound keys remain
valid output coordinates, Population tuple predicates and export keys.

Time scopes and instant anchors are acquired through the explicit Session.
There is no module-level time constructor alias.

Query operations
-----------------

``select`` derives values; ``where`` and ``top`` select represented coordinates.
``reduce`` computes sum, mean and row_count over current cells. ``rollup``
recomputes qualified original Metrics and ``drill`` constructs new endpoint
observations. ``share`` retains its actual input and partition denominator.
``member()`` locates exactly one existing identity axis; extra classifications
must remain single-valued for that identity.

``align(..., how="exact"|"outer", match=...)`` uses left/right roles.
``compare`` uses current/baseline and emits each quantity's endpoints, absolute
change and relative change. Relative is ``(current-baseline)/abs(baseline)``;
zero_denominator controls this new relative only. Presence is independent of NA.
No missing endpoint is silently replaced with zero. Correspondences are axis-free
rules; binding chooses each endpoint's unique compatible calendar or identity.

``ordinal`` pairs complete original calendar positions without compressing filters.
``period_offset`` moves in true calendar order; ``previous_year`` maps builtin
same-name day/month/quarter/year periods and leaves leap days unmatched.
Nonidentity output retains its original endpoint projections. ``broadcast``
requires a complete unique covering target-to-reference projection, including
all reference axes. A one-row grouped reference is not a zero-axis scalar.

Time and numerical boundaries
-----------------------------

``session.instant`` and ``session.time_scope`` freeze exact boundaries and timezone.
Naive Python datetimes and ambiguous/nonexistent clocks reject. ``scope.by(grain)``
creates nominal/actual calendar buckets; ``CalendarAxis.coarsen`` constructs a
candidate consumed only when every fine nominal/actual bucket maps uniquely.
Windowed original rollup must preserve its existing time axis and endpoints.

Window rules attach through Metric.window before observe: all_history,
trailing_duration, trailing_periods, since and to_date. Complete child formulas
are evaluated separately at each endpoint before comparison. Fixed elapsed time
and civil periods retain different meanings across DST.

After source handoff, nullable int64, float64, Decimal, date, aware timestamp and
Duration preserve their qualified physical contracts. Default empty/all-NA sum is
zero; explicit source/internal sum empty="null" yields NA. Mean/extrema/quantiles
yield NA without an effective sample. Strict policies, nonfinite values, overflow, missing mappings
and required coverage fail the invocation. Exact quantiles never silently become
approximate reductions. Exact source median/quantile currently admit only DuckDB
and PostgreSQL; MySQL, Trino and ClickHouse reject those methods before Run admission.
DuckDB native INTERVAL requires a source-native rejection
of year/month/day components and is then admitted in microseconds.

Execution, recovery and qualification
-------------------------------------

Construction and contracts read no business rows or schemas and allocate no Run.
Execute prepares actual schemas and a pure fixed schedule before admission;
source templates are prevalidated and actual instances checked again. Source
Ibis returns pandas; local kernels use pandas, while PyArrow owns terminal data
encoding/reading. Numerical source routes use the selected backend and qualify
the actual expression, physical types and method before Run admission. Unsupported
native methods reject specifically; exact and approximate algorithms never
substitute for each other. Backend access alone does not qualify a numerical route.

Source computation accepts native Ibis/provider numerical precision loss. The
pandas handoff resolves effective numerical types for local consumers and the
published schema; it does not cast values back merely to match source declarations.
Complete keys, null policies, units and time semantics still apply. Saved-output
fidelity concerns the values actually committed.

Every admitted execute is a fresh evaluation. Atomic Store 11 publication commits
output, Findings/Evidence and success together. Metadata opening by exact Artifact
ID needs no current Catalog, source or Parquet scan. New and reopened Artifacts
use the same saved-output reader; receipt verification may read a full consumed
part even for bounded preview. Older graph/continuation formats reject unchanged.
Pandas export keeps complete keys, NA, presence, projections, timezone and order;
its data-only metadata carries no executable semantic authority.

Session graphs contain same-Session Run-to-Artifact production edges only.
Run variants retain an invocation marker and lifecycle, without consumed Artifact
references. Every selected Run is a root and every selected Artifact a head.
Focused ancestors includes the producer; descendants returns the terminal Artifact
alone. Boundary fields disclose omitted requested neighbors. Store 10 and earlier
Stores reject without migration; the terminal output v1 codec is unchanged.

The principal routes have focused local evidence. Full independent acceptance,
remote Runtime checks and release gates are separate. Native Help and structured
errors state exact signatures, admissible next operations and concrete repairs.

Canonical public exports
-------------------------

.. autoclass:: Query
   :members:

.. autoclass:: Column
   :members:

.. autoclass:: Population
   :members:

.. autoclass:: PopulationPredicate
   :members:

.. autoclass:: MembershipAxis
   :members:

.. autoclass:: MemberAxis
   :members:

.. autoclass:: CorrespondenceAxis
   :members:

.. autoclass:: CalendarCorrespondence
   :members:

.. autoclass:: DeclaredCorrespondence
   :members:

.. autofunction:: identities

.. autofunction:: coordinates

.. autofunction:: membership

.. autofunction:: product

.. autofunction:: in_population

.. autofunction:: member

.. autofunction:: member_count

.. autofunction:: row_count

.. autofunction:: broadcast

.. autofunction:: corresponding

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

.. autoclass:: ArtifactSummary
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

.. autoclass:: SessionGraph
   :members:

.. autoclass:: Session
   :members:

.. automodule:: marivo.analysis.session
   :members:
