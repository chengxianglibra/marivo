# Multi-Metric Observe Design

## Status

Accepted design for implementation planning.

Date: 2026-07-07

## Context

`mv.observe(metric=...)` is a single-metric API over a single-metric
execution path. A report-style task over four metrics with identical scope
(same datasource, entities, time window, grain, dimensions, and slice)
becomes four `observe` calls and four full scans of the same fact table.

Three existing commitments shape the solution:

- `docs/specs/analysis/python-analysis-design.md` ("Batch optimization")
  states that query merging is a runtime execution optimization, not a
  second authoring model. Agents must not learn a lazy plan API.
- `MetricFrame` semantics are already value-dependent: `semantic_kind` is
  one of `scalar | time_series | segmented | panel`, and intents declare
  preconditions dynamically through `contract()` and teaching errors.
- The planner already decomposes one observation into multiple
  `BaseObservePlan` values (`DerivedObservePlan.component_plans`), so
  planning several metrics over one scope is structurally the same problem
  the codebase already solves for derived metrics.

Industry semantic-layer query primitives (MetricFlow, Cube, LookML) accept
N measures natively; single-metric observation is the N=1 special case
hardcoded into the current API.

## Decision

Fold metric arity into `observe` and `MetricFrame` instead of adding a
parallel batch entry point.

1. `observe(metric: MetricInput | Sequence[MetricInput], ...)` always
   returns one `MetricFrame`. Arity-1 behavior is unchanged.
2. `MetricFrame` carries one value column per metric over shared axes.
   Frame reads (`show()`, `summary()`, `to_pandas()`, `contract()`) work at
   any arity.
3. Arity-N accepts simple, unfolded metrics only. Derived metrics and
   metrics with `time_fold` raise a teaching error at the observe
   boundary naming the offending metric and suggesting a separate
   arity-1 observe. Their scalar sidecar metadata (`component_ref`,
   `composition`, `coverage_ref`, `coverage_summary`, quantile and
   sample fields) is attached after execution and has no per-measure
   representation yet; admitting them would silently lose
   component-aware compare and coverage semantics after projection.
4. Projection is first-class: `frame.metric("model.name")` returns an
   arity-1 `MetricFrame` as a cheap in-memory lineage step, no query.
5. Every intent boundary that accepts a `MetricFrame` (`compare`,
   `discover`, `correlate`, `transform`, `assess_quality`,
   `hypothesis_test`, `forecast`) requires arity-1 and raises a teaching
   error naming the actual metrics and the exact `frame.metric(...)` call
   to make first. Intents that consume downstream artifacts
   (`decompose`, `attribute` over `DeltaFrame`) are covered transitively
   by the `compare` gate.
6. No broadcasting. Arity is an observation/reading affordance only;
   analytical intents never fan out over metrics implicitly.
7. Query fusion stays in the execution layer: metrics whose base plans
   share scope are computed in one GROUP BY; the rest execute separately
   and join on the shared axes. Fusion is invisible to the authoring
   surface, consistent with the spec's batch-optimization stance.

## Goals

- One entry point for observation at any metric arity; no
  `observe_many`, no batch context manager, no lazy plan API.
- One backend scan per fusible metric group; a four-metric weekly report
  over one fact table costs one GROUP BY.
- Downstream intent contracts stay single-metric and unchanged in
  semantics; the arity gate is explicit and teachable.
- Persisted arity-1 frames written before this change keep loading.

## Non-Goals

- Arity-preserving `compare` (per-metric deltas from two arity-N frames).
  Deferred until a real need is argued; the gate keeps the door open.
- Cache interoperability between an arity-N frame and previously cached
  arity-1 frames of its member metrics. Artifact identity follows params;
  a different metric list is a different artifact.
- Per-metric scope overrides (different windows or slices per metric in
  one call). One call means one scope.
- Multi-metric support in `derive_metric_frame` / `metric_columns`.
- Derived or folded metrics at arity-N. The extension path is
  per-measure sidecar metadata (component/coverage refs, composition,
  quantile and sample fields moved into `MeasureMeta`) plus projection
  rewiring; it is deferred until designed, and the boundary error keeps
  the contract explicit until then. Fusing a derived metric's simple
  components with sibling metrics is part of that future work.

## API Surface

### observe

```python
catalog = session.catalog
frame = mv.observe(
    [
        catalog.get("analytics.dau"),
        catalog.get("analytics.new_users"),
        catalog.get("analytics.orders"),
        catalog.get("analytics.gmv"),
    ],
    time_scope={"start": "2026-06-29", "end": "2026-07-05"},
    grain="day",
)
frame.show()                      # time axis + four value columns
dau = frame.metric("analytics.dau")   # arity-1 MetricFrame
```

- `metric` accepts a single `MetricInput` or a non-empty sequence of
  `MetricInput`. The element contract is unchanged: catalog objects or
  `SemanticRef`s, bare strings stay rejected (projection takes string
  ids because the frame's own `metrics` list makes them unambiguous).
  Duplicates (after normalization) raise `SemanticInputError` listing the
  duplicate ids. An empty sequence raises the same error class.
- Sequence elements must be simple, unfolded metrics; a derived or
  folded metric raises a teaching error naming it and suggesting a
  separate arity-1 `observe` (see Decision 3).
- All other parameters are unchanged and apply to every metric:
  `time_scope`, `grain`, `dimensions`, `slice_by`, `time_dimension`,
  `expect_shape`, `analysis_purpose`, `session`.
- Dimension and slice inputs must resolve within every metric's planner
  scope. A dimension that resolves for some metrics but not others raises
  the existing dimension-resolution error, extended to name the failing
  metric.
- `expect_shape` is orthogonal to arity and keeps its current meaning;
  shape is shared because axes inputs are shared.
- Semi-additive `status_time_dimension` injection only applies at
  arity-1, where it is unambiguous. At arity-N there is no implicit time
  axis injection: the shared window resolves once from the explicit
  inputs, and a metric that requires a different status axis raises the
  existing axis-resolution teaching error, extended to suggest passing
  `time_dimension=...` (shared by all metrics) or observing that metric
  separately. This keeps the frame-level `window` metadata single-valued.

### MetricFrame

- `frame.metrics` (new read-only property): ordered tuple of metric ids,
  matching input order. `frame.arity == len(frame.metrics)`.
- `frame.metric(metric_id)` (new): returns an arity-1 `MetricFrame`
  containing the shared axis columns plus that metric's value column
  renamed to `"value"`. Unknown ids raise a teaching error listing
  `frame.metrics`. On an arity-1 frame, projecting the only metric
  returns `self`.
- `__repr__` at arity-1 is unchanged; at arity-N it reports
  `metrics=4` and points to `.show()`.
- `show()` at arity-N renders one bounded per-metric summary line (metric
  id, unit, fusion group) above the shared table preview.
- `contract()` at arity-N lists the per-metric projection affordances
  built from `frame.metrics` and marks single-metric intents as gated on
  projection.

### Value columns

- Arity-1: the value column stays `"value"` (`VALUE_COLUMN`); nothing
  downstream changes.
- Arity-N: one column per metric named by metric short name; on collision
  across semantic models the full id with `.` replaced by `__` is used.
  The authoritative mapping is `meta.measures[i].column`, not the column
  string itself.

## Frame Metadata

`MetricFrameMeta` changes:

- New `measures: list[MeasureMeta] | None = None`, one entry per metric in
  input order: `{metric_id, name, column, unit, additivity,
  reaggregatable}`. `None` means a legacy arity-1 frame; accessors derive
  the single entry from the existing scalar fields. No per-measure `fold`
  field: folded metrics are rejected at arity-N (Decision 3).
- `metric_id: str | None` — populated only at arity-1 (unchanged value);
  `None` at arity-N. Same rule for the scalar `unit`, `measure`,
  `additivity`, and `reaggregatable` fields: they describe the frame only
  when it has exactly one metric; at arity-N `reaggregatable` is the
  conjunction and the rest live per-measure.
- `axes`, `window`, `where`, `semantic_kind`, `semantic_model` stay
  frame-level (shared scope). Metrics from different semantic models set
  `semantic_model` to the root model of the first metric and record every
  model in `measures`; cross-model observation is allowed when every
  dimension input resolves for every metric.
- The remaining scalar sidecar fields (`component_ref`, `composition`,
  `coverage_ref`, `coverage_summary`, `quantile_mode`,
  `quantile_method`, `sample_set_digest`) stay arity-1-only by
  construction: the metric classes that populate them are rejected at
  arity-N (Decision 3), so no per-measure representation is needed yet.
- Persisted legacy frames load without migration because every new field
  defaults to the legacy reading.

## Evidence And Knowledge

An arity-N observation is one artifact but N evidence subjects.
`Subject` stays single-metric; the multi-metric contract lives in the
extractor:

- The observe commit still goes through `commit_result` once. Semantic
  anchors at arity-N are `{"metrics": ordered metric ids, "models":
  ordered distinct models}`; arity-1 keeps `{metric_id, model}`
  unchanged so existing artifact ids are stable.
- The `metric_frame` extractor becomes measure-aware: for each
  `meta.measures` entry it derives a per-measure `Subject`
  (`metric=` that entry's id; `slice`, `grain`, and `analysis_axis`
  shared from the call scope) and emits that measure's `metric_value`
  findings and observation digest from that measure's column. At
  arity-1 the output is byte-identical to today's single-subject
  extraction. No finding is ever attributed to the frame as a whole,
  so nothing in session knowledge aggregates across metrics silently.
- One job record covers the call; the params `fusion` block names which
  metric ids each captured query served, so query provenance stays
  per-metric readable.

## Projection

`frame.metric(id)` is a session step with a full commit contract, not an
in-memory view:

- Commit through the standard `commit_result` +
  `register_frame_artifact` tail with `step_type="select_metric"`,
  `inputs=[parent artifact id]`, `params={"metric": id}`, semantic
  anchors `{metric_id, model}`, and `Subject(metric=id)` carrying the
  parent's slice, grain, and analysis axis. The deterministic artifact
  id follows from these values, so a repeated projection first checks
  the frame store (as observe's cache probe does) and returns the
  persisted frame; `load_frame` recovers projections across sessions
  like any other registered artifact.
- The projected frame's meta is fully scalar-populated (arity-1) and its
  lineage appends the `select_metric` step, so every downstream intent
  sees a frame equivalent to a direct single-metric `observe` of the
  same simple metric.
- Projection emits no `metric_value` or digest findings: the observe
  commit already recorded that measure's evidence, and re-extraction
  would duplicate session knowledge. The projection commit uses a
  finding-free extractor path and writes a job record with zero
  captured queries.

## Arity Gate

A shared boundary helper (alongside the existing shape assertions in the
frames layer) enforces arity-1 at every analytical intent entry:

```
MetricArityError: compare expects a single-metric frame, got 4 metrics
  ['analytics.dau', 'analytics.new_users', 'analytics.orders', 'analytics.gmv'].
  Fix: pass frame.metric("analytics.dau") (or another id above).
```

- New `MetricArityError` subclasses `AnalysisError` with structured fields
  `{intent, expected_arity, got_arity, metrics}`; suggestions are built
  from the frame's real metric list.
- `contract()` on arity-N frames declares the same precondition so agents
  can discover the gate before tripping it.

## Execution

Planning per metric, fusion by evidence:

1. Normalize the metric list and reject derived or folded metrics with
   the boundary teaching error (Decision 3); every admitted metric plans
   exactly as today into a `BaseObservePlan`.
2. Group plans by fusion key: `(datasource_name, root_entity,
   resolved window + grain + time_dimension, dimensions,
   normalized where)`.
3. Each fused group executes one GROUP BY with one named aggregate per
   metric. Each remaining plan executes as today.
4. Result blocks join on the shared axis columns (`bucket_start` and
   dimension columns) with a full outer join, deterministic axis
   ordering, missing combinations as NaN. A scalar-shape call (no axes)
   concatenates single-row results columnwise.
5. Frame `params` record the ordered metric list plus, per metric, the
   same per-metric params recorded today, and a `fusion` block naming the
   groups (metric ids per executed query). Warnings surface why a metric
   did not fuse only when the caller could plausibly have expected fusion
   (same datasource and root entity but mismatched axes); pure
   different-table metrics are not warned.

Artifact identity: the prospective artifact id covers the ordered metric
list and shared params, so repeated identical multi-metric observations
hit the frame cache exactly like arity-1 observations do today.

## Testing

- Fusion: query capture asserts one backend query for a fusible group and
  the expected count for mixed groups; values equal per-metric `observe`
  results.
- Alignment: outer-join semantics with partially disjoint axis values;
  scalar concatenation.
- Boundary: derived and folded metrics in a sequence raise the teaching
  error naming the offending metric; arity-1 derived/folded observe is
  unaffected.
- Projection: `frame.metric(...)` meta scalars, `select_metric` lineage
  step, downstream `compare` accepting the projected frame, repeated
  projection returning the cached artifact, and `load_frame` recovering
  a projection in a fresh session.
- Evidence: arity-N commit emits per-measure `metric_value` findings and
  digests with per-metric subjects; arity-1 extraction output is
  unchanged; projection commits emit no value findings.
- Gate: every analytical intent raises `MetricArityError` on an arity-N
  frame; the message carries the real metric list.
- Meta: arity-N round-trip through frame persistence; legacy arity-1
  frame files still load.
- Surface: `__all__` snapshot updated for `MetricArityError`; observe
  signature docstring and `mv.help`/`describe` cover the sequence form.

## Documentation Updates

- `docs/specs/analysis/python-analysis-design.md`: observe section gains
  the arity contract; the batch-optimization section references fusion as
  its realization.
- `marivo/skills/marivo-analysis/`: report-style workflow uses one
  multi-metric observe plus projections.
- `site/src/content/docs/*/latest/`: observe examples in both English and
  Chinese editions.
