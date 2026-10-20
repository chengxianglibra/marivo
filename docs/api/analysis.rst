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
Construction and inspection perform no business read or publication.

Source grouping accepts ordered fields or ``Grouping`` rules, or a complete
``Population`` domain. ``identity(entity)`` declares one complete identity rule;
``identities(entity, keys=...)`` declares a finite requested identity domain.
``population.axes`` reuses grouping definitions without selected keys or allocation
bindings. ``by=population`` retains the complete output domain and coverage checks.
CalendarAxis is a calendar Grouping; membership retains correlated coordinate tuples.

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
Full stored compute/export validates complete required payload and attachments.
Export preserves exact row order, raw-key MultiIndex, nullable types and isolated
typed frozen-interpretation attrs. Exported attrs cannot reenter computation.

Evaluation, publication and recovery
------------------------------------

Compute creates no Run, Artifact or temporary saved result. Expression persist
completes evaluation first, then publishes that existing result ID through the
single Store 12 writer transaction. Run means publication; evaluation can precede
admission. Memory persist never reevaluates. Stored persist validates commit
metadata and returns itself without another Run. A derived Frame never exposes
an ancestor Artifact. Unknown publication outcome is reconciled, not recomputed.

Completed data owns source-free values, coordinates, presence and one audit:
inherited source facts plus the current evaluation. It retains no producer,
semantic graph, connection or hidden aggregate components. Read, evaluation and
commit times remain separate. Session graphs retain Run-to-Artifact publishing
edges and do not promise complete data lineage.

Store 12 refuses older-generation Session/result/audit data unchanged, without
migration or dual reads. Documented absent-current discovery may return None or
empty; create/get_or_create cannot bypass an old-only project. Preserve required
old results by exporting with their original version or retaining a compatible
environment. RawSqlResult keeps its independent datasource reader and does not
enter Frame Store. Static Help, actual local evidence, provider Runtime and
release acceptance are separate contracts.

Canonical public exports
-------------------------

.. autoclass:: AnalysisFrame
   :members:

.. autoclass:: FrameAxis
   :members:

.. autoclass:: Column
   :members:

.. autoclass:: Population
   :members:

.. autoclass:: PopulationPredicate
   :members:

.. autoclass:: Grouping
   :members:

.. autoclass:: CalendarCorrespondence
   :members:

.. autofunction:: identities

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
