# Analysis Operators and Frame Contract

Status: design. This document specifies the concrete operator algebra of
`marivo.analysis`: the frame/result families, the typed shapes and policies that
gate them, the agent-facing operators that produce them, and the shared contract
every result obeys. It is the "how" companion to
[`python-analysis-design.md`](python-analysis-design.md) (the "why"). For session
lifecycle and persistence see
[`session-state-and-runtime.md`](session-state-and-runtime.md); for the
evidence/judgment surface see
[`evidence-access-surface.md`](evidence-access-surface.md).

The analysis alias throughout is `mv` (`import marivo.analysis as mv`). Operators
are methods on a single `Session` object; there are no free operator functions.

## Frame and result families

Every operator has exactly one canonical output family. Callers, the executor,
evidence extraction, and help all reference the same registry rather than
inventing family names. The public families and their producers are:

| Family | Produced by | Meaning |
| --- | --- | --- |
| `MetricFrame` | `session.observe(...)` | Observed metric facts |
| `DeltaFrame` | `session.compare(...)` | Difference between two `MetricFrame`s |
| `AttributionFrame` | `session.attribute(...)` | Contribution attribution of a delta |
| `CandidateSet` | `session.discover.<objective>(...)` | Candidates worth following up |
| `AssociationResult` | `session.correlate(...)` | Statistical association between frames |
| `HypothesisTestResult` | `session.hypothesis_test(...)` | Result of an explicit statistical test |
| `ForecastFrame` | `session.forecast(...)` | Model projection of future buckets |
| `QualityReport` | `session.assess_quality(...)` | Explicit quality/coverage/precondition assessment |

Two projection frames are produced by frame accessors rather than operators, and
are therefore not part of the default `mv.__all__` surface:

| Projection frame | Produced by | Meaning |
| --- | --- | --- |
| `ComponentFrame` | `MetricFrame.components()`, `DeltaFrame.components()` | Per-component breakdown of a derived metric |
| `CoverageFrame` | `MetricFrame.coverage()` | Row/segment coverage of an observed frame |

The following are deliberately **not** operator output families: a
`sample_frame` (an internal materialized sampling node for `hypothesis_test`, it
enters lineage but is not authored by agents); artifact summaries/profiles
(projections, not canonical artifacts); and ad-hoc pandas DataFrames (terminal
via `frame.to_pandas()`, cannot re-enter typed analysis).

## Typed shapes and typed policies

A fixed output family does not mean a single internal shape. Shapes are closed
enumerations so that shape mismatches fail at submit/plan time, not after a
backend scan.

### Frame and result shapes

| Shape | Family | Legal content |
| --- | --- | --- |
| `MetricFrame[scalar]` | `MetricFrame` | Single-point metric |
| `MetricFrame[time_series]` | `MetricFrame` | One metric over time |
| `MetricFrame[segmented]` | `MetricFrame` | Segmented metric, single/no time |
| `MetricFrame[panel]` | `MetricFrame` | Segment × time panel |
| `DeltaFrame[scalar_delta \| time_series_delta \| segmented_delta \| panel_delta]` | `DeltaFrame` | Delta of the corresponding metric shape |
| `CandidateSet[point_anomaly \| period_shift \| driver_axis \| slice \| window \| cross_sectional_outlier \| semantic_hypothesis]` | `CandidateSet` | Objective-specific scored candidates or unscored ontology hypotheses |
| `AssociationResult[signed_lag]` | `AssociationResult` | Zero-lag or signed lag-sweep association |
| `QualityReport[metric \| delta \| candidate \| forecast \| attribution]` | `QualityReport` | Quality report scoped to the assessed family |

Adding a shape requires updating the family registry, producer, consumer
compatibility (the [DAG](#shape-aware-dag), projection, and evidence/follow-up
rules together — a shape is not a display concern.

### Typed policies

Cross-input alignment and sampling are typed objects, never bare dicts or free
strings:

| Policy | Used by | Key fields |
| --- | --- | --- |
| `AlignmentPolicy` | `compare`, `correlate`, `hypothesis_test` | closed helper-specific fields |
| `SamplingPolicy` | `hypothesis_test` sampling/pairing/null handling | `unit`, `method`, `pairing`, `null_handling`, `min_n` |

`AlignmentPolicy.kind` is a closed enum with constructor helpers exported at top
level: `mv.window_bucket()`, `mv.day_of_week()`, `mv.period_progress()`, and
`mv.period_correspondence()`. `mv.window_bucket()` defaults to `mode="ordinal_bucket"`
(pairs buckets by ordinal position within each window); use
`mode="calendar_bucket"` to pair by normalized bucket key, and
`strict_lengths=True` only when equal-length same-period windows are required.
Calendar authority comes from semantic period bindings and exact snapshots; no
analysis holiday calendar or session-global calendar is consulted.

### Semantic refs

Every qualifying catalog-bound runtime input accepts an exact current
`CatalogEntry[K]` or its exact `Ref[K]`, never a guessed string. The boundary
validates catalog ownership, exact kind, and current membership, then normalizes
immediately to the ref. Period calendars use semantic refs and artifacts use
`ArtifactRef`.

## Agent-facing core operator surface

This is the single analysis API an agent learns on the main path. Each entry is a
`Session` method with a fixed output family:

| Operator | Output | Notes |
| --- | --- | --- |
| `session.observe(...)` | `MetricFrame` | Materialize one metric or a same-scope metric list over a window. |
| `session.compare(current, baseline, ...)` | `DeltaFrame` | Frame-to-frame delta only; no metric+windows shorthand. |
| `session.attribute(delta, axes=[...], ...)` | `AttributionFrame` | Deterministic attribution over explicit axes. |
| `session.discover.<objective>(...)` | `CandidateSet` | Objective-specific candidate discovery. |
| `session.correlate(a, b, ...)` | `AssociationResult` | Statistical association; not causality. |
| `session.hypothesis_test(...)` | `HypothesisTestResult` | Test over prepared frames/delta/sample. |
| `session.forecast(history, ...)` | `ForecastFrame` | Project an observed history frame forward. |
| `session.assess_quality(artifact)` | `QualityReport` | Explicit quality/coverage/comparability assessment. |

An operator earns a place here only if it reduces agent steps without hiding
judgment, fixes an output family, names a computation task (not a primitive
alias), fails more instructively than the primitive, or is itself an
irreducible primitive. This is why there is no `measure` (use `observe`), no
`compare_frames`/`correlate_frames`/`forecast_frame` alias, no `explain` (use
`attribute`), no `scan(objective=...)` (use `discover.<objective>`), no `test`
(use `hypothesis_test`), and no bare `assess` (use `assess_quality`).

### Capability registry and runtime family gate

Every public operator, constructor, read, recovery, and boundary crossing is
registered in a closed capability registry. Each entry carries a stable
`capability_id`, `public_entrypoint`, `help_target`, `accepted_inputs` (a
mapping from parameter name to the closed set of accepted input families), and
an `ArtifactOutputContract` carrying output family plus any statically known
semantic shapes and matching kinds. The same artifact-admission predicates
filter static producer/consumer edges and concrete runtime affordances. Unknown
or input-dependent output shapes stay conditional and defer to the concrete
artifact's `.contract()`; fixed funnel and Lifecycle reducer shapes never
advertise consumers that require journey or replay-history input. The registry
is the single source of truth for the help surface, `contract()` affordances,
and runtime family gate.

Constructor descriptors also declare the exact input family they produce.
Focused help obtains prerequisites from the same producer graph: artifact
inputs point to compatible producers, semantic inputs point to the owning typed
catalog collection, and composite Event patterns point through
`participant_role` → `step` → `sequence`. Every accepted input family must have
one registered acquisition path before the registry can initialize.

The runtime family gate validates submitted inputs against the registry's
`accepted_inputs` before any backend work begins. When an input family does not
match, the gate fails closed with a structured `AnalysisError` carrying typed
`expected`/`received`/`location`/`repair` fields. The terminal `to_pandas()`
exit has the capability id `boundary.to_pandas`.

### Internal / expert surface

These exist for debugging, implementation decomposition, and a few expert cases.
They are reachable via focused `marivo.help("analysis.<target>")` calls but are never taught
alongside the core surface:

| API | Output | Notes |
| --- | --- | --- |
| `decompose` | `AttributionFrame` | Frame-local attribution over explicit axes; multi-axis calls require `mode="joint"` or `mode="hierarchy"`. Does not materialize missing dimensions. |
| `session.transform.<op>` | Same family/shape as input | Family-preserving reshape/filter/rank/window/normalize. |
| `CandidateSet.select(item_id=...)` | `CandidateSelection` | Stable-id read returning one closed shape-specific selector; creates no job or artifact. |
| Sampling helpers | Sample artifact | Prepare a sample/summary for `hypothesis_test`. |

## Operator detail

### `observe`

`observe` is the source-to-artifact primitive and the default entry point. It
resolves a semantic metric and returns a `MetricFrame` whose shape follows the
requested axes:

```python
revenue = session.catalog.metrics.get("analytics.dau")
platform = session.catalog.dimensions.get("analytics.events.platform")

series = session.observe(
    metrics=revenue,
    time_scope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
    dimensions=[platform],
)
```

**Multi-metric.** The `metrics` argument accepts a single metric or a non-empty
list of same-scope metrics. A single metric may also be passed positionally.
For temporal observations, every root must resolve
to the same exact time-dimension ref; roots with different implicit axes fail
before backend work rather than merging semantically different buckets. Simple
metrics on one datasource are merged into one query (compatible
cross-datasource metrics are grouped per datasource and outer-joined on the
shared time axis). The result carries multiple measure columns;
`frame.meta.measures` records each metric's `metric_id`, `column`, and `unit`,
and `frame.meta.metric_id` is `None`. On an arity-N frame, `frame.metric(id)`
returns an arity-1 `MetricFrame` without re-querying; an unknown id raises
`MetricArityError`. List elements must be simple, unfolded metrics — derived or
folded metrics, duplicates, and empty lists fail closed.

Single-, derived-, cumulative-, and multi-metric observations include their
persisted additivity, aggregation, and status-time semantics in artifact
identity. Re-running `observe` after upgrading the frame schema therefore does
not reuse a legacy artifact that lacks those fields.

**Derived metrics.** Derived-metric components share the base-metric planner:
each component is planned independently, may span multiple datasets, and each
component plan is single-datasource. Dispatch enforces fail-closed comparability
checks — every parent dimension must resolve to the same semantic field in every
component (`component-axis-*`), every parent `slice_by` must apply to every
component (`component-filter-*`), and versioned datasets shared across components
must agree on version mode/anchor/predicate/mapping (`component-version-mismatch`).
Component rows align on the union of their axis keys; a component absent on a key
stays null and composes to a null value. Division compositions (`ratio`,
`weighted_mean`) evaluate under the fixed `zero_division="null"` policy: a
present zero denominator/weight yields a null value, never `+/-inf`, and the
affected-row count is persisted as `frame.meta.zero_denominator_rows` and
surfaced as `quality_summary.zero_denominator_rows`. The policy participates in
observe artifact identity, so frames cached under the older bare-division
semantics are never reused. Transforms drop the count together with the parent's
component links: a transformed frame carries `zero_denominator_rows = None`
because the parent's row-level facts no longer describe the transformed rows.

**Grain.** `observe` and `compare` accept `grain` as a token string. Calendar
grains (`day`, `week`, `month`, `quarter`, `year`) require `count == 1`. Sub-day
grains (`5minute`, `15minute`, `30minute`, `1hour`, `4hour`) must (1) be no finer
than the metric time field's declared `granularity` (else `GrainUnsupportedError`)
and (2) divide a day evenly (`7minute` is rejected).

**Temporal suitability preflight.** After semantic inputs, window, and grain
normalize—but before connection acquisition, query capture, backend
execute/fetch, or state writes—`observe` validates the complete metric forest
against compiled candidate time dimensions. It distinguishes no candidate time
axis, an ordinary dimension supplied as the time axis, ambiguous candidates,
different implicit axes across metric roots, and incompatible encoding/grain.
No-axis and wrong-kind cases use
`semantic_authoring` repair when the requested temporal analysis cannot remain
typed; ambiguity exposes exact candidates without choosing one; incompatible
grain is retryable only when the legal replacement is mechanically unique.
Executor checks remain defensive backstops for runtime dtype facts and preserve
the same structured repair shape.

**Window bounds.** Every `time_scope` is the literal half-open interval
`[start, end)`: `start` is included and `end` is excluded. An agent must not
advance a caller-supplied end date to make it inclusive. For example,
`{"start": "2026-07-01", "end": "2026-08-01"}` includes all of July and no
rows from August 1; `end="2026-07-31"` excludes July 31.

**Semi-additive temporal folds.** A status time dimension with a declared
`sample_interval` uses the sampled two-phase fold and may compute expected-slot
coverage. Without `sample_interval`, `first` and `last` are supported only when
the metric root is snapshot-versioned on that exact status axis and its
business identity remains non-empty after removing the partition key. Marivo
selects the first/last raw row per business identity inside each current
observation bucket, then applies the metric's original spatial aggregation.
It never sums snapshot dates and does not carry rows across windows or buckets.
Other unsampled folds fail with `unsampled-time-fold-unsupported`; an unresolved
snapshot identity fails with `snapshot-fold-identity-missing`. Snapshot
selection records `fold_strategy="snapshot_selection"` and `identity_keys`,
keeps `sample_interval=None`, and does not fabricate expected-slot coverage.

**Cumulative frames.** Cumulative `MetricFrame`s store running totals whose
semantics depend on the accumulation anchor (`all_history`, `grain_to_date`,
`trailing`). `transform.window(...)` clips display rows for every anchor;
`decompose` and `forecast` reject cumulative frames. `attribute` accepts only a
current `cumulative-delta/v1` produced by `compare` and selects a persisted
business-axis or accumulation-time route; it does not accept legacy cumulative
delta metadata. `compare` is anchor-dispatched: `all_history` is allowed when both
frames carry the same valid anchor and paired business coordinates with exact
`evaluation_end` cutoffs; its result is current minus baseline observed level,
not an asserted interval flow, and source revision is unverified. `trailing` is
allowed when both frames have the same fixed-duration span (`7 day` and `1 week`
are equivalent);
`grain_to_date` is allowed for a single reset-boundary-anchored period that spans
at most one reset period and equal elapsed length. Comparable-period deltas use
paired coordinates only. Ordinal window alignment is supported for both anchors;
day-of-week, period-progress, and named period-correspondence alignment are
admitted only through their closed helper contracts.
`transform.rollup`
re-aggregates with `rollup_fold="last"`, selecting the complete last row so its
`evaluation_end` is retained. The anchor-specific caveat is surfaced by
`contract()`, `show()`, and `marivo.help(ref)`.

For a derived metric, compare applies the same anchor rules only when every
outer component is cumulative and every component has exactly the same anchor.
Its `derived_contains_cumulative` marker is the required closed structure
`{kind, anchor, compare_blocker, components}`. `anchor` is the common anchor or
`None`; `compare_blocker` is `non_cumulative_component`,
`mixed_component_anchors`, `unresolved_component_anchor`, or `None`. Mixed,
incomplete, and malformed wrappers fail closed. A complete homogeneous
`all_history` wrapper is comparable by derived observed level, with component
sidecars restricted to the parent's paired key set. Both sides must carry the
same marker kind and effective anchor. This is a breaking artifact contract:
cumulative observes include contract version 4 and the reserved
`evaluation_end` coordinate in artifact identity; persisted legacy rows are
neither expanded nor migrated.

**Versioned joins.** `ms.snapshot()` / `ms.validity()` declare dataset
versioning. The planner auto-selects `as_of_root_time` when the root dataset has
a day-level time field, else falls back to `latest` anchored on `time_scope.end`
or plan time. Snapshot `as_of_root_time` runs two narrow discovery queries to
build an anchor→partition mapping injected as an `ibis.memtable`; validity
`as_of_root_time` evaluates interval predicates inline and records a single
`validity_overlap_unverified` lineage warning per join.

### `compare`

`compare` is the single-purpose frame-to-frame delta operator: it accepts two
already-observed `MetricFrame`s of the same shape and returns a `DeltaFrame`.
Window/grain/dimension choices are made explicitly in the two `observe` calls, so
`compare` never guesses windows:

```python
current = session.observe(metrics=m, time_scope={"start": "2026-06-18", "end": "2026-06-25"}, grain="day")
baseline = session.observe(metrics=m, time_scope={"start": "2026-06-11", "end": "2026-06-18"}, grain="day")
delta = session.compare(current, baseline, alignment=mv.window_bucket())
```

For an all-history cumulative comparison, every persisted delta row carries
`current_evaluation_end` and `baseline_evaluation_end`. One-sided business
coordinates are dropped and counted in `meta.alignment["cumulative_pairs"]`;
matched rows with a null level remain paired with a null delta. The
`cumulative_change` marker and `show()` identify the result as an observed level
difference and state that source revision is unverified.

For trailing and grain-to-date comparisons, inspect
`delta.meta.cumulative_alignment`. It preserves each authored anchor, their
canonical comparison anchor, and exact matched/null/current-only/baseline-only/
fallback row counts. One-sided rows are always dropped before the delta is
materialized, including panel coordinates.

`compare` propagates additivity, aggregation, and status-time semantics only
when both source frames carry the same three values and additivity is known.
Missing or mismatched source semantics produce an unknown gate on the
`DeltaFrame`, so later attribution fails closed instead of trusting one side.

### `attribute`

`attribute` performs deterministic attribution of a `DeltaFrame` over explicit
axes and returns an `AttributionFrame`. It is not a planner: with no axes or
search policy it fails closed. `DeltaFrame.contract().attribute_admission` is
the sole typed mechanical admission state; `show()` and `attribute()` project
that same state. A rollup-safe multi-axis call explicitly chooses
`mode="joint"` for one additive row per full axis combination, or
`mode="hierarchy"` for flattened prefix rows. Hierarchy parent rows repeat
their descendants' totals, so only the deepest level is additive. Candidate
axes, coverage warnings, and budget stops go to metadata/blocking
issues/lineage, never a next-step recommendation or narrative.

For a cumulative delta, inspect
`DeltaFrame.contract().cumulative_attribution`. Business dimensions replay the
same cumulative observations with the missing dimensions and explain endpoint
level changes; direct sum/count paths retain new and churned coordinates as
zero-valued absent sides. Requesting exactly the cumulative `over_ref` invokes
the additive base-flow bridge. Its
`cumulative-flow-attribution-rows/v1` rows carry the comparison coordinate,
bridge bucket, exact half-open interval, source side, anchor-specific effect
kind, observed side values, contribution, ranks, and shares. All-history uses
flow between cutoffs; grain-to-date contrasts the two elapsed reset scopes;
trailing splits entering, leaving, and retained/revision flow. Reconciliation
is independent for every parent comparison row. Mixed time/business axes,
count-distinct bases, bridge mismatch, and derived component time bridges fail
closed with the exact route blocker.

There is no default for a multi-axis call; a single-axis call omits `mode`, and
supplying one there has no effect. A single-axis result preserves the resolved
dimension column (for example, `cluster`) and sets `driver_field` to that name,
so its pandas rows join directly to the source `DeltaFrame`. Namespaced
`attribution_level` / `attribution_axis` / `attribution_driver` /
`attribution_path` columns are reserved for multi-axis hierarchy output.
Additive single-axis results report `method="sum"`.
The preserved dimension name must not collide with attribution result, value,
or panel bucket columns. Such a collision fails closed with a
`SemanticKindMismatchError` and a semantic-authoring repair instead of
producing duplicate columns. Evidence protocol fields are mapped explicitly
from metadata and do not reserve user dimension names.
`AttributionFrame.attribution_mode` exposes
the persisted row layout (`"joint"`, `"hierarchy"`, or `None` for single-axis
and legacy artifacts). It is intentionally distinct from
`AttributionFrame.attribution_shape` / `meta.method`, which identify the
attribution math. Both layouts may therefore report `method="weighted_mix"`.
`marivo.help("analysis.AttributionMode")` owns this focused contract.

Graph-owned attribution bases also admit three non-additive aggregate roots:

| Aggregate root | Installed method | Admission and execution boundary |
| --- | --- | --- |
| `count_distinct(key)` | Distinct membership allocation | Scalar keys only. `(key, partition)` deduplication, membership degree, and `1 / degree` allocation execute in the datasource; raw keys never cross the artifact boundary. |
| DuckDB `median` / `percentile(q)` | Exact value-frequency replacement game | Linear weighted order statistics; exact Shapley through 8 partitions, 128 deterministic permutations for 9–64, and a 250,000 frequency-row cap. |
| Trino `median` / `percentile(q)` | Mergeable qdigest replacement game | `qdigest_agg -> merge -> value_at_quantile` stays server-side and preserves the admitted bigint/double/real source type. Endpoint reproduction is mandatory; source error remains unknown. |
| ClickHouse reservoir quantile | — | Blocked because the sampled states are not an admitted mergeable distribution contract. |

These methods independently replay an unsegmented `observe -> compare` endpoint;
segmented point estimates are explanatory only and are never summed into the
target delta. Non-additive multi-axis calls allow only `mode="joint"` or
`mode="multiresolution"`. Multiresolution is **independent multiresolution
attribution**: each exact ordered semantic-ref prefix is recomputed, ranked,
and reconciled as a separate game. Complete rows must never be summed across
resolutions. Select one query-free immutable view with
`frame.at_resolution(axes=[...])`; the selected rows may be summed once per
comparison bucket. Empty intermediate quantile coalitions, endpoint mismatch,
more than 64 partitions, or oversized distribution evidence fail closed.

Every contribution row uses explicit denominators: `share_of_total_delta` is
the signed contribution divided by the independently computed overall delta;
`share_of_positive_pool` and `share_of_negative_pool` are non-negative shares
within the two same-sign contribution pools. Core analysis deliberately does
not call either pool improvement or degradation because metric desirability is
not part of the persisted semantic contract. Agents may apply that business
interpretation only when the metric objective is known.

Component mix attribution treats a structurally absent `0 / 0` side as a zero
term, so new and churned segments retain exact one-sided contributions instead
of becoming null. The `AttributionFrame` persists a reconciliation summary and
renders it in `show()`: total delta, contribution sum, one-sided contribution
sum, unattributed contribution sum, residual, and partition count. Every
deepest partition must reconcile within numeric tolerance or attribution fails
closed; hierarchy parents remain repeated presentation rows.

Attribution permission comes from semantics persisted on the `DeltaFrame`; it
does not re-query a catalog that may have changed since observation. This gate
runs on the original delta before `attribute` replays observations to
materialize a missing axis. `DeltaFrame.show()` surfaces the current delta's
supported, conditional, or blocked attribution state, and `DeltaFrame.contract()`
mirrors the same persisted boundary with typed preconditions. Unknown and ordinary
non-additive deltas fail, semi-additive deltas surface the status-time-axis
condition, and persisted ratio/weighted-mean component paths remain available.

| Persisted metric semantics | Axis attribution |
| --- | --- |
| `additive` | Supported by the sum/hierarchy paths. |
| `semi_additive` | Supported on non-time axes; rejected when `axes` contains its `status_time_dimension`. |
| Component-aware `ratio` / `weighted_mean` | Supported by ratio/weighted mix attribution. |
| Tier-1 `mean` over a measure | Lowered during observe to `sum(measure)` / `count_non_null(measure)` components and supported by weighted mix attribution. |
| Graph-owned `count_distinct` | Supported by distinct membership when the key type is reproducible. |
| Graph-owned `median` / `percentile(q)` | Supported by exact value-frequency or Trino qdigest when the persisted basis admits the installed method. |
| Other `non_additive` without supported component math | Rejected, including opaque/tier-2 means, min, max, unsupported quantile sources, tier-2 non-additive metrics, and non-additive linear compositions. |
| Missing additivity metadata | Rejected; re-run `observe` and `compare` to create a current self-contained delta. |

The mean lowering is runtime-only and never substitutes entity row count for
`count_non_null(measure)`. For a rejected metric, inspect
`DeltaFrame.contract().attribute_admission`: re-observe legacy artifacts or
author the aggregate-specific component/distribution evidence named by its
repair. Existing non-linear sampled-fold validation still runs first.

```python
drivers = session.attribute(
    delta,
    axes=[
        session.catalog.require(ms.ref.dimension("analytics.events.country")).ref,
        session.catalog.require(ms.ref.dimension("analytics.events.platform")).ref,
    ],
    mode="joint",
)
```

The internal `decompose` primitive is the frame-local building block
`attribute` composes; it is not on the agent-facing surface.

### `discover.<objective>`

`session.discover` is a namespace of objective-specific helpers returning
`CandidateSet`. The helper name expresses what the agent is looking for; strategy
is a closed default, not a natural-language string. The current objectives:

| Objective | Input | Candidate shape |
| --- | --- | --- |
| `discover.point_anomalies(metric_frame, ...)` | `MetricFrame[time_series \| panel]` | `point_anomaly` |
| `discover.period_shifts(delta, ...)` | `DeltaFrame[time_series_delta \| panel_delta]` | `period_shift` |
| `discover.driver_axes(delta, search_space=[...])` | `DeltaFrame` | `driver_axis` |
| `discover.interesting_slices(metric_or_delta, ...)` | `MetricFrame` / `DeltaFrame` | `slice` |
| `discover.interesting_windows(metric_or_delta, ...)` | `MetricFrame[time_series \| panel]` / delta | `window` |
| `discover.cross_sectional_outliers(metric_frame, ...)` | `MetricFrame[segmented \| panel]` | `cross_sectional_outlier` |
| `discover.semantic_hypotheses(metric_or_delta, limit=50)` | Arity-one catalog MetricFrame or same-Metric DeltaFrame | `semantic_hypothesis` |

`discover` emits candidates only — never attribution, test verdicts, or new fact
frames. The optional `semantic_hypotheses` objective follows at most one authored
`influences` or `related_to` edge, resolves its opposite endpoint through the
semantic catalog, and returns deterministic, unscored Metric hypotheses. It is
available only when the Session has a ready ontology binding; it does not assert
causality, score candidates, or execute them. Whether scored candidate generation
was reliable is decided by
`assess_quality(candidate_set)`; whether a candidate is a real driver/anomaly is
decided by downstream `hypothesis_test` or agent judgment. Thresholds are
absolute z-score cutoffs with per-objective defaults (see each method's
docstring). Scored objectives accept `analysis_purpose` to label the step;
`semantic_hypotheses` intentionally accepts only `source` and the resource bound
`limit`.

### `correlate`

`correlate(a, b, method=..., alignment=..., lag_range=...)` returns an
`AssociationResult` expressing statistical association only — no causality, no
written explanation. `lag=k` pairs `a[t]` with `b[t+k]`: positive lag means `a`
leads `b`, negative lag means `b` leads `a`, and the default is lag 0. Each lag
requires at least two overlapping, non-constant values after shifting. Non-zero
lags require `time_series` or `panel` inputs. Panel shifts are evaluated within
each dimension series, never across series boundaries, and null pairs are
dropped only after shifting so missing buckets do not collapse the time axis.

### `hypothesis_test`

`hypothesis_test` accepts a prepared `DeltaFrame`, `MetricFrame`, or sample
artifact plus an explicit hypothesis and `SamplingPolicy`, and returns a
`HypothesisTestResult`. It has no metric+windows shorthand — to compare windows,
`observe` and `compare` first.

### `forecast`

`forecast(history, horizon=...)` accepts an observed history `MetricFrame`
(`time_series` or `panel`) and returns a `ForecastFrame`. It never implicitly
materializes history; the agent declares the history window/grain/dimensions via
`observe`. Forecast-vs-actual evaluation is not a public Session step.

### `assess_quality`

`assess_quality(artifact)` returns a `QualityReport` scoped to the artifact
family. It evaluates mechanical quality — data quality, coverage, comparability,
attributability — never business good/bad. It is distinct from the cheap
`artifact.quality_summary` metadata projection: the summary reads lightweight
facts already on the artifact; `assess_quality` runs explicit checks and produces
a terminal report. A source artifact records at most a
`latest_quality_report_ref`, never a copied full report.

For a metric `DeltaFrame`, assessment validates the delta row contract. When the
delta carries `CumulativeAlignmentV1`, the report reads that typed field directly
and surfaces matched-null, unpaired, and fallback counts as explicit caveats.

`QualityReport.overall_status`, `.blocking_issue_count`, and `.warning_count`
are read-only projections of the authoritative report metadata for programmatic
gating. They do not recompute quality. `QualityReport.state` retains the common
`ArtifactState` contract and therefore describes materialization and content
identity, not the quality verdict.

## Result contract and read protocol

Analysis operators never write to stdout; every result is silent and returns a
typed object. Typed artifacts share one protocol so an agent can read them
cheaply and recover them across script turns. The layered read order is:

```text
repr(result)  ->  result.show() / result.render()  ->  result.contract()  ->  result.to_pandas()
```

- `repr(result)` — one-line cold-start hint carrying kind + identity and pointing
  at `.show()`; default dataclass reprs are never used.
- `result.show()` — print a bounded result card and return `None`;
  `result.render()` returns the same bounded text without writing stdout.
- `result.contract()` — the mechanical `ArtifactContract` (below).
- `result.to_pandas()` — an isolated defensive DataFrame copy (tabular frames
  only). It is the only method that returns a mutable copy.

Terminal `RawSqlResult` supports the same bounded row/column reads but omits
`contract()` because it has no typed continuation.

A `MetricFrame` card includes the persisted observation scope, axes and slices,
aggregation/additivity/reaggregation facts, and any temporal fold strategy.
Sampled folds show their expected-slot coverage summary. Unsampled snapshot
selection shows its identity keys and explicitly marks expected-sample coverage
as not applicable; it never invents a coverage sidecar. Derived metrics render a
bounded component-fold list. Recovery uses the same persisted metadata and
therefore renders the same execution facts.

Every artifact card explicitly lists `output_columns`, using the exact names
returned by `.columns` and `to_pandas()`. `ArtifactContract.output_columns` is
derived from `artifact_schema.columns[*].name`, so it cannot drift.
Where an artifact retains direct semantic inputs, the card and
`contract().semantic_inputs` preserve their roles, semantic kinds and paths,
optional output-column binding, exact
`session.catalog.<collection>.get("<path>")` acquisition call, and focused
catalog help target. The ordered list is bounded and reports any omitted count.
Metric artifacts expose catalog metric roots, axes, slices,
status-time dimensions, and governed runtime-expression leaves; Event artifacts
expose pattern Events and reducer axes; Lifecycle artifacts expose their
StateModel, replay Events, and reducer axes. These are mechanical reacquisition
facts, not claims that an entry remains current in a different catalog.

Frames are immutable: `frame[col]` reads, but `frame[col] = ...` and frame
arithmetic (`+`, `-`, `*`, `/`) raise `FrameMutationError` directing the agent to
`.to_pandas()`. Frames expose `.ref`, `.kind`, `.lineage`, `.state`,
`.quality_summary`, `.evidence_status`, `.evidence_digest`, `.columns`, and
`.shape`. Every frame also exposes read-only `.row_count`, with
`frame.row_count == frame.shape[0]` at creation and recovery boundaries. The
`BaseFrame.describe()` and `BaseFrame.plot()` methods are intentionally removed;
accessing them raises `AttributeError`. Use `frame.show()` for bounded inspection
and `frame.to_pandas()` for terminal custom analysis.

Every public read of a single-metric `MetricFrame` uses the metric name for its
value column. This includes `show()` / `render()`, `.columns`, iteration,
`contract().artifact_schema`, `frame[metric_name]`, and `to_pandas()`, and remains
true after transforms. The typed runtime and persisted frame keep the canonical
internal `value` column; transform predicates and operator implementations use
that internal schema. If a metric short name collides with an axis column, the
public value column uses the qualified metric id with `.` replaced by `__`.
If that qualified name also collides, Marivo appends a deterministic `#N`
suffix until the public column name is unique.

### The mechanical contract

`contract()` returns an `ArtifactContract` describing mechanical compatibility
only — it never ranks, recommends, or narrates:

- `kind`, `ref`, `is_canonical`, an `artifact_schema` (typed columns +
  `semantic_shape`), and `output_columns` equal to the schema's ordered names.
- `semantic_inputs` — bounded, role-preserving semantic identity and exact
  catalog reacquisition paths retained by the artifact.
- `issues` — a closed tuple of typed data-quality, comparability, or evidence
  availability issues.
- `affordances: ArtifactAffordance[]` — each a gate that mechanically exists:
  `capability_id` (the stable registry id such as `compare` or
  `discover.driver_axes`), `public_entrypoint` (the public API path),
  `help_target` (the canonical `marivo.help("analysis.<target>")` target), role-preserving
  `inputs` (`parameter`, accepted artifact families, and whether the current
  artifact can bind that parameter), `preconditions`
  (`(check, pass|fail, reason)`), and `expected_output_family`.
- `boundary_ports: ArtifactBoundaryPort[]` — typed terminal-exit ports derived
  from the capability registry. Each port carries `capability_id`
  (e.g. `boundary.to_pandas`), `public_entrypoint`, `help_target`,
  `preserves`, and `does_not_preserve`. The terminal exits are
  `frame.to_pandas()` and `md.raw_sql(...)`; results from either cannot
  re-enter typed analysis.

Every public value returned by `.contract()`—currently `AuthoringContract`,
`ArtifactContract`, and `DigestReadContract`—structurally provides a bounded
one-line `repr`, deterministic bounded `render()`, and `show()` that prints the
same card. Typed fields and `model_dump()` remain available; this conformance
does not introduce a cross-layer contract base class.

A failed artifact precondition remains visible only with a usable repair. The
common case carries one `repair`; a closed choice set uses ordered
`repair_options`. A retry repair always has an executable snippet; otherwise the
repair is `inspect`, `semantic_authoring`, or `environment`. An affordance whose
failed precondition has no visible repair is suppressed.

When a downstream capability requires arity 1, an arity-N `MetricFrame`
contract exposes one ordered projection repair for every full metric identity:

```python
frame.metric("sales.revenue")
frame.metric("sales.order_count")
```

The contract names `frame` as the receiver and never chooses a projection.
Single-metric frames and capabilities that do not require arity 1 receive no
projection repair.

Affordances are not recommendations.
Marivo says which parameters can accept which artifact families; the agent
decides which valid call matters for the question and whether to stop.

`ArtifactState` (via `result.state`) carries only baseline runtime facts:
`materialization` (`materialized` | `recomputed` | `partial`) and
`content_hash`. Cache/freshness/superseded relationships are deliberately not
baseline artifact fields.

## Candidate consumption

Scored `CandidateSet` items share common fields — `item_id`, `score` (ranking
within the set only, not cross-artifact), `reason_codes`, `source_refs`, optional
`selector`/`window`/`baseline_window`/`keys`/`axis`/`direction` — plus a small
set of shape-specific required fields per objective. A candidate is a lead, not a
proven fact. `CandidateSet[semantic_hypothesis]` is a separate unscored shape:
each row contains only `item_id`, `semantic_edge_ref`, `edge_relation`,
`candidate_semantic_ref`, and resolved `metric_ref` as meaningful fields. Its
metadata retains inherited scope, edge judgment context, resolution counts, and
bounded exclusions.

Candidates are consumed via `candidate_set.select(item_id=...)`; numeric rank is
not accepted. The method returns one closed immutable variant:
`PointAnomalySelection`, `PeriodShiftSelection`,
`DriverAxisSelection`, `SliceSelection`, `WindowSelection`, or
`CrossSectionalOutlierSelection`. Semantic-hypothesis selection instead returns
`OntologyMetricCandidate`, which can re-enter only through
`session.observe(candidate, analysis_purpose=...)` with the exact inherited
scope. Selection is a bounded read, not an artifact-producing step: it creates no
job, lineage step, finding, or digest.

Only the ontology variant is an `observe` input because it resolves a different,
not-yet-materialized Metric together with the exact inherited scope required to
observe it. Scored selections identify coordinates already computed in their
source artifact: an anomaly row, comparison period, driver axis, slice, window,
or peer outlier. Passing those values to `observe` would be ambiguous between
re-querying semantic data and projecting the existing artifact, so they remain
typed terminal selectors until a shape-specific downstream operator explicitly
accepts them.

Evaluation results (`HypothesisTestResult`, `AssociationResult`,
`QualityReport`) are not directly re-fed into `compare`/`attribute`/`discover`.
They retain source refs, `AnalysisScope`, typed issues, and a bounded digest.

## Shape-aware DAG

A legal path must match both family and shape; the executor gates this at plan
time. Projection/read methods are not analysis steps. Summary of the adjacency
(internal `decompose` shown as the single-axis attribution primitive):

| Source | Legal downstream |
| --- | --- |
| `MetricFrame[time_series]` | `transform.<op>`, `compare` (same shape), `correlate` (same shape), `discover.point_anomalies`, `discover.interesting_windows`, conditional `discover.semantic_hypotheses`, `hypothesis_test`, `forecast`, `assess_quality` |
| `MetricFrame[segmented]` | `transform.<op>`, `compare`, `correlate`, `discover.interesting_slices`, `discover.cross_sectional_outliers`, conditional `discover.semantic_hypotheses`, `hypothesis_test`, `assess_quality` |
| `MetricFrame[panel]` | union of the time_series and segmented rows above |
| `DeltaFrame[time_series_delta \| panel_delta]` | `transform.<op>`, `attribute`, `discover.period_shifts`, `discover.driver_axes`, `discover.interesting_windows`, `discover.interesting_slices`, conditional `discover.semantic_hypotheses`, `assess_quality` |
| `DeltaFrame[scalar_delta]` | `transform.<op>`, `attribute`, `discover.driver_axes`, conditional `discover.semantic_hypotheses`, `assess_quality` |
| `DeltaFrame[segmented_delta]` | `transform.<op>`, `attribute`, `discover.driver_axes`, `discover.interesting_slices`, conditional `discover.semantic_hypotheses`, `assess_quality` |
| `AttributionFrame` | `transform`, `select`, `assess_quality` |
| Scored `CandidateSet[*]` | `assess_quality`, `CandidateSet.select` |
| `CandidateSet[semantic_hypothesis]` | `CandidateSet.select` → `OntologyMetricCandidate` → exact-scope `session.observe` |
| `AssociationResult` / `HypothesisTestResult` / `ForecastFrame` / `QualityReport` | bounded reads and supported quality inspection |

Illegal paths fail closed: `candidate_set -> attribute` (select an axis/window/
slice first); `summary -> compare` (a projection is not a canonical input);
`forecast_frame -> compare` (no forecast-vs-actual step).

## Composite operators

A composite is admitted only if it clears two gates together: (1) it cannot be
replaced by one core operator plus a typed policy, and (2) its expansion carries a
cross-step constraint an agent would plausibly miss when writing glue code
(alignment pairing, provenance retention, definition compatibility, evidence
binding, scan-bundle consistency). "Frequent" or "saves typing" alone is
insufficient — that case is served by step-wise session code. A composite must
fix one output family and reach `canonical` level only when its input schema is
stable, its expansion DAG is fixed, it needs no mid-run agent decision, its output
is bounded, and its evidence/lineage/failure semantics are definable; otherwise it
stays `exploratory`. No composite is on the current default agent-facing surface;
`attribute` is a core operator, not a composite.

## Terminal boundaries

There are two one-way terminal exits from typed analysis. Results from either
cannot re-enter the typed artifact chain.

- **`md.raw_sql(...)`** — the sole public raw SQL execution path. Returns a
  `RawSqlResult` with timeout enforcement, exact row bounding, ordered
  `columns`, isolated `to_pandas()`, and basic reads `shape` and `row_count`.
  Here `row_count == shape[0] == returned_row_count` means returned bounded
  rows, not full-source cardinality. Its card keeps `requested_limit` and exact
  `is_truncated` adjacent and states `terminal_only: true` and
  `typed_reentry: false`. It has no `.contract()` or typed affordances. Use it
  for custom analysis that cannot be expressed through
  `session.observe(...)`, including an analysis branch blocked by a semantic
  gap. Temporary inferred semantics must be disclosed and remain terminal-only.
- **`frame.to_pandas()`** — any tabular frame exposes `.to_pandas()`, returning
  an isolated defensive copy for ad-hoc pandas exploration, plotting, or
  modeling.

Terminal raw SQL does not resolve missing business semantics. It may provide a
provisional result without prior approval, but carries no canonical metric
identity, analysis lineage, or evidence continuity and cannot re-enter typed
analysis. The closeout retains the gap and requests approval for the smallest
durable change through `marivo.semantic`. `session.observe(...)` remains the sole
canonical `MetricFrame` producer.

## Cross-cutting metadata

- **`quality_summary` vs `assess_quality`.** `frame.quality_summary` is a cheap,
  bounded, persisted metadata projection; `assess_quality()` is the only quality
  assessment action and produces a terminal `QualityReport`. They are layered, not
  duplicated.
- **Metric-definition compatibility.** Cross-frame operators compute a
  compatibility verdict and write it to result metadata: `exact` (same id +
  version) runs; catalog-declared backward-compatible changes run with a warning;
  aggregation/unit/subject/filter/denominator/event-definition changes are
  `incompatible` and fail closed with a `definition_drift` blocking issue; missing
  version info is `unknown` and blocks unless an exploratory policy allows it.
  Rename/description/owner changes are never incompatible.

## Non-goals

The operator layer does not: dress arbitrary Ibis/SQL as a core operator; pass
generic pandas/sklearn wrappers off as canonical artifact producers; provide
typed regression or a generic statistical planner; do causal inference or
what-if simulation; auto-generate business conclusions; emit free text as its
primary output (`explain`/narrative `diagnose`); map one BI chart template to one
core operator; or allow `RawSqlResult`/pandas values to re-enter typed analysis.
