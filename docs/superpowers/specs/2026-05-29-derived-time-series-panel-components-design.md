# Derived Time-Series And Panel Components Design

Date: 2026-05-29

Status: approved design for implementation planning

## Problem

Marivo now has a component-aware frame contract for derived ratio and
weighted-average metrics, but the first version deliberately covers only
scalar and segmented frames. Time-series and panel observations still reject
derived component metrics, which leaves a gap in the normal
`observe -> compare -> decompose` workflow.

Users should be able to observe a derived ratio or weighted-average metric over
time, compare two aligned windows, and decompose the resulting movement without
leaving frame world. Panel analysis should keep the existing per-bucket
semantics: explain each time bucket independently across the requested segment
axis.

## Goals

- Support derived ratio and weighted-average metrics for `time_series` and
  `panel` observations.
- Keep parent `MetricFrame` and `DeltaFrame` outputs clean; component columns
  remain available only through `frame.components()`.
- Propagate component state through both `window_bucket` and calendar-backed
  compare alignment.
- Preserve existing public entry points: `session.observe(...)`,
  `session.compare(...)`, and `session.decompose(...)`.
- Make `decompose` follow existing frame semantics:
  - `time_series` decomposes by bucket.
  - `panel` decomposes by the requested dimension within each bucket.
- Keep existing scalar, segmented, and ordinary sum metric behavior unchanged.

## Non-Goals

- Do not add nested derived metric support.
- Do not add cross-backend federation for component metrics.
- Do not make transformed frames preserve component links.
- Do not make `ComponentFrame` an input to ordinary analysis intents.
- Do not introduce a separate global attribution mode for panel or
  time-series component deltas.

## Frame Contract

Extend `ComponentFrameMeta.semantic_kind` from:

```text
"scalar" | "segmented"
```

to:

```text
"scalar" | "time_series" | "segmented" | "panel"
```

Component frames mirror the parent frame's logical axes:

- `scalar`: component columns only.
- `segmented`: dimension columns plus component columns.
- `time_series`: time axis column, normally `bucket_start`, plus component
  columns.
- `panel`: time axis column plus all dimension columns plus component columns.

Metric-parent component frames contain:

- axis columns
- `numerator` and `denominator` for ratio metrics, or `numerator` and `weight`
  for weighted-average metrics
- `metric_value`

Delta-parent component frames contain:

- aligned axis columns matching the parent delta shape
- `current_*`, `baseline_*`, and `delta_*` columns for each component role and
  for `metric_value`
- calendar alignment fields when the parent delta has them, such as
  `align_key`, `align_quality`, `bucket_start_a`, and `bucket_start_b`

Parent `MetricFrame` and `DeltaFrame` data remain unchanged. Their
`component_ref` and `decomposition` metadata link to component state, and
`frame.components()` remains the explicit access point.

## Observe

`observe` supports derived ratio and weighted-average metrics for all four
semantic shapes.

The existing derived scalar and segmented paths remain the base behavior: each
component metric is evaluated first, component columns are merged on the parent
axes, and the derived expression is evaluated into the final metric value.

For derived `time_series` observations:

- Resolve and validate the requested window grain.
- Bucket each component metric with `apply_time_series_bucket(...)`.
- Aggregate each component by `bucket_start`.
- Merge component outputs on `bucket_start`.
- Evaluate the derived expression into the final metric column.
- Persist a clean parent `MetricFrame` with `bucket_start` and the final metric
  value.
- Persist a metric-parent `ComponentFrame` with `bucket_start`, component
  columns, and `metric_value`.

For derived `panel` observations:

- Resolve and validate the requested window grain and dimensions.
- Bucket each component metric with `apply_time_series_bucket(...)`.
- Project the requested dimensions onto each component table.
- Aggregate each component by `bucket_start` plus all dimension columns.
- Merge component outputs on the full axis key.
- Evaluate the derived expression into the final metric column.
- Persist a clean parent `MetricFrame` with `bucket_start`, dimensions, and the
  final metric value.
- Persist a metric-parent `ComponentFrame` with the same axes, component
  columns, and `metric_value`.

Existing constraints continue to apply:

- Component metrics must not be derived metrics.
- Component metrics must be resolvable to the same backend.
- Component metrics and requested dimensions must be joinable through the
  existing dimension resolution rules.
- Ordinary sum metrics and non-derived metrics get no component link.

## Compare

`compare` continues to align parent metric frames first. If either input frame
has decomposition metadata, both inputs must have component frames and the
component metadata must be compatible.

Compatibility requires matching:

- decomposition kind
- component role map
- axes
- semantic kind
- semantic model

If compatibility fails, `compare` raises a structured component error instead
of silently returning a component-unaware delta.

For `window_bucket` alignment:

- `scalar` uses the existing scalar comparison.
- `segmented` merges component rows on dimension columns.
- `time_series` aligns component rows with the same overlap or ordinal bucket
  rules used for the parent delta.
- `panel` aligns component rows per segment group with the same window spine
  behavior used for parent panel compare.

For calendar-backed alignment:

- `time_series` component rows pass through the same calendar policy as the
  parent rows.
- `panel` component rows are aligned per segment group through the same
  calendar policy.
- One-sided and fallback rows preserve the same null and alignment metadata
  semantics as the parent delta.

After component alignment succeeds, `compare` persists a delta-parent
`ComponentFrame` and links it from `DeltaFrameMeta.component_ref`. The parent
`DeltaFrame` remains clean and keeps its existing visible columns.

## Decompose

`session.decompose(delta, axis=...)` remains the only public entry point.

For component-aware deltas:

- `scalar`: component mix attribution stays unsupported unless an actual axis
  column exists. The existing axis/semantic error path should handle this.
- `segmented`: existing one-row-per-segment ratio or weighted mix behavior
  remains unchanged.
- `time_series`: `axis=DimensionRef("bucket_start")` decomposes by bucket.
  Shares and effects are calculated across all buckets in the delta frame,
  matching current time-series sum decompose semantics.
- `panel`: `axis` must be one of the panel dimensions. Shares, value effects,
  mix effects, residuals, contribution percentages, and ranks are computed
  independently within each time bucket.

Component-aware decompose explains only the main `delta` column. A non-default
`measure_column` remains supported only on the existing sum path.

Method names remain:

- `ratio_mix`
- `weighted_mix`

Output columns include the existing component-aware attribution fields:

- axis columns
- `contribution`
- `pct_contribution`
- `value_effect`
- `mix_effect`
- `residual`
- current and baseline component columns
- current and baseline metric values
- current and baseline denominator or weight shares
- `rank`

For panel output, the bucket column is included and rank restarts within each
bucket.

The attribution bucket column is shape- and alignment-aware:

- For `window_bucket` time-series and panel deltas, use `bucket_start`.
- For calendar-aligned time-series and panel deltas, use `bucket_start_a` as
  the current-side explanatory bucket while preserving `bucket_start_b` in the
  component delta for auditability.
- `axis=DimensionRef("bucket_start")` should resolve to the effective
  attribution bucket column for time-series component deltas so callers do not
  need to branch on alignment policy.

## Error Behavior

Component-aware behavior fails closed when metadata claims component support but
the required component frame cannot be loaded or aligned.

Errors should cover:

- missing `component_ref`
- linked frame is not a `ComponentFrame`
- mismatched decomposition kind
- mismatched component role map
- mismatched axes or semantic kind
- unsupported component alignment shape
- no valid contribution rows during decompose

Calendar unmatched rows are not errors by themselves. They produce the same
one-sided semantics that the parent delta produces: the structurally missing
side is treated as zero for delta math and marked with `presence_status`, while
true null metric values remain null. Decompose treats rows with invalid
denominator or weight state as null contribution rows.

## Testing

Add focused tests for:

- Derived `time_series` ratio observe links a clean parent frame and component
  frame.
- Derived `panel` ratio observe links a clean parent frame and component frame.
- Derived `time_series` and `panel` weighted-average metrics use `weight`
  columns.
- `compare` propagates component frames for `window_bucket` time-series deltas.
- `compare` propagates component frames for `window_bucket` panel deltas.
- `compare` propagates component frames for calendar-aligned time-series
  deltas.
- `compare` propagates component frames for calendar-aligned panel deltas.
- Sparse panel segments and one-sided rows preserve parent/component alignment.
- `decompose` time-series ratio and weighted deltas emit bucket
  contributions.
- Calendar-aligned time-series decompose accepts
  `axis=DimensionRef("bucket_start")` and uses `bucket_start_a` as the effective
  attribution bucket.
- `decompose` panel ratio and weighted deltas emit per-bucket segment
  contributions and per-bucket ranks.
- Incompatible component metadata fails closed.
- Existing scalar and segmented component-aware tests continue to pass.
- Existing non-component time-series and panel tests continue to pass.

## Documentation Updates

Update the existing component-aware spec or add a follow-up note explaining
that ratio and weighted-average component support now covers time-series and
panel frames.

Update analysis skill documentation and examples that currently describe
component-aware decompose as segmented-only. Include at least one example of:

- time-series ratio observation, comparison, and bucket decomposition
- panel ratio observation, comparison, and per-bucket segment decomposition
