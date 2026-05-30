# Component-Aware Frame Contract Design

Date: 2026-05-28

Status: approved design for implementation planning

Follow-up: `2026-05-29-derived-time-series-panel-components-design.md`
extends this contract from scalar and segmented derived metrics to time-series
and panel derived metrics.

## Problem

Marivo's current `observe -> compare -> decompose` flow treats every metric as a
single numeric measure once it enters the analysis frame world. That works for
sum-decomposable metrics, but it loses the component state required to explain
ratio and weighted-average metric changes.

For a ratio-style metric such as `failure_rate = failed_count / total_count`,
change attribution needs both:

- value effect: the per-segment rate/value change
- mix effect: the segment denominator or weight share change

Today users must observe component metrics manually, leave frame world with
pandas, compute effects, then promote the result back to an attribution frame.
This design makes component state a first-class typed frame contract while
keeping normal metric frames clean.

## Goals

- Keep the public `session.observe(...) -> session.compare(...) ->
  session.decompose(...)` workflow unchanged for users.
- Preserve `MetricFrame.to_pandas()`, `summary()`, and `preview()` output for
  existing scripts; component columns must not appear in the parent metric frame.
- Add explicit `frame.components()` access for derived ratio and
  weighted-average frames.
- Support component-aware `compare` and `decompose` for scalar and segmented
  derived ratio/weighted-average metrics in the first version.
- Keep existing sum metric behavior unchanged.

## Non-Goals

- Do not add derived time-series or panel metric support in this version.
  Current derived metric time-series/panel limitations remain.
- Do not make `ComponentFrame` a general input to ordinary analysis intents.
- Do not require `MetricFrame.from_dataframe()` or `promote_metric_frame()` to
  construct component-aware frames in the first version.
- Do not make transform operations preserve component frames in the first
  version.

## Frame Contract

Add a new typed frame family: `ComponentFrame`.

`ComponentFrameMeta` fields:

- `kind = "component_frame"`
- `parent_ref: str`
- `parent_kind: "metric_frame" | "delta_frame"`
- `metric_id: str`
- `decomposition_kind: "ratio" | "weighted_average"`
- `components: dict[str, str]`, mapping component role to metric ref
- `axes: dict[str, Any]`
- `semantic_kind: "scalar" | "segmented"`
- `semantic_model: str`

`ComponentFrame` data shape:

- Axis columns match the parent frame exactly.
- Metric-parent component frames contain component value columns:
  `numerator` plus `denominator` for ratio, or `numerator` plus `weight` for
  weighted average.
- Metric-parent component frames also include `metric_value` for sanity checks.
- Delta-parent component frames contain aligned current, baseline, and delta
  component columns, such as `current_numerator`, `baseline_numerator`,
  `delta_numerator`, and equivalent denominator/weight columns.
- Delta-parent component frames also include `current_metric_value`,
  `baseline_metric_value`, and `delta_metric_value`.

Add lightweight links on parent frames:

- `MetricFrameMeta.component_ref: str | None = None`
- `MetricFrameMeta.decomposition: dict[str, Any] | None = None`
- `DeltaFrameMeta.component_ref: str | None = None`
- `DeltaFrameMeta.decomposition: dict[str, Any] | None = None`

`MetricFrame.components()` and `DeltaFrame.components()` load and return the
linked `ComponentFrame`. If no component frame is available, they raise a
structured analysis error explaining that components are only available for
derived ratio or weighted-average frames produced by the component-aware
contract.

`ComponentFrame.next_intents()` is empty in the first version.

## Observe And Compare

`observe` generates component frames only for derived `ratio` and
`weighted_average` metrics with scalar or segmented shape.

- Derived scalar observe already computes component values before evaluating
  the derived metric. Persist those values into a metric-parent
  `ComponentFrame`, and persist the parent `MetricFrame` with `component_ref`.
- Derived segmented observe already merges per-component grouped results before
  evaluating the final metric. Persist the merged component columns into a
  metric-parent `ComponentFrame`, and keep the parent `MetricFrame` data limited
  to axis columns plus the final metric value.
- Sum metrics and non-derived metrics keep `component_ref = None`.

`compare` propagates component state when both inputs have compatible component
frames.

- Current and baseline component frames must have matching
  `decomposition_kind`, `components`, `axes`, `semantic_kind`, and
  `semantic_model`.
- Component comparison uses the same scalar or segmented alignment as the parent
  metric frame comparison.
- The parent `DeltaFrame` remains unchanged in its visible data columns.
- A delta-parent `ComponentFrame` is persisted and linked from
  `DeltaFrameMeta.component_ref`.
- If both parent frames represent derived ratio/weighted-average metrics but
  component frames are missing or incompatible, `compare` fails closed with a
  structured error instead of silently producing a non-component-aware delta.

## Decompose

`session.decompose(delta, axis=...)` remains the single user entry point.

- If `DeltaFrameMeta.component_ref` is absent, decompose keeps the current
  sum-delta behavior.
- If the linked component frame has `decomposition_kind = "ratio"`, decompose
  uses ratio mix attribution.
- If the linked component frame has `decomposition_kind = "weighted_average"`,
  decompose uses weighted mix attribution.
- Decompose always attributes the `delta` column. The `measure_column`
  parameter was removed (it answers "who contributed to the *change*"), so
  there is no per-call measure selector on any path.

Component-aware attribution output remains one row per segment. Common columns:

- axis columns
- `contribution`
- `pct_contribution`
- `value_effect`
- `mix_effect`
- `residual`
- current and baseline component values
- current and baseline denominator/weight shares
- `rank`

Method names:

- ratio: `ratio_mix`
- weighted average: `weighted_mix`

Shared decomposition formula:

- `current_share_i = current_denominator_or_weight_i / sum(current_denominator_or_weight)`
- `baseline_share_i = baseline_denominator_or_weight_i / sum(baseline_denominator_or_weight)`
- `current_value_i = current_numerator_i / current_denominator_or_weight_i`
- `baseline_value_i = baseline_numerator_i / baseline_denominator_or_weight_i`
- `contribution_i = current_share_i * current_value_i - baseline_share_i * baseline_value_i`
- `value_effect_i = current_share_i * (current_value_i - baseline_value_i)`
- `mix_effect_i = (current_share_i - baseline_share_i) * baseline_value_i`
- `residual_i = contribution_i - value_effect_i - mix_effect_i`

The sum of `contribution` values should reconcile to the overall metric delta
within numerical tolerance. Rows with zero or missing denominators retain null
effect values. If no valid contribution rows can be formed, decompose fails with
a structured error.

## Compatibility And Edge Behavior

- Existing persisted frames without `component_ref` remain loadable.
- `to_pandas()`, `summary()`, and `preview()` for parent metric and delta frames
  do not expose component columns.
- `transform` does not preserve component frames in this version. Transformed
  frames have `component_ref = None`.
- `MetricFrame.from_dataframe()` and promotion APIs continue to create ordinary
  non-component-aware frames unless explicitly extended later.
- Old component-unaware ratio frames can still be compared only if they are not
  identified as component-aware derived frames. If a derived ratio/weighted
  metric is produced by new observe code but loses component linkage, compare
  fails closed.

## Testing

Add focused tests for:

- `observe` scalar derived ratio creates a clean parent `MetricFrame` and a
  linked `ComponentFrame`.
- `observe` segmented derived ratio creates component rows aligned with parent
  segment rows.
- `frame.components()` returns a typed immutable `ComponentFrame`; ordinary
  sum metric frames raise the structured unavailable error.
- `load_frame()` round-trips `component_frame`.
- `compare` on compatible segmented ratio frames creates a parent `DeltaFrame`
  plus linked delta-parent `ComponentFrame`.
- `compare` fails closed when component frame metadata is missing or mismatched
  for derived ratio/weighted-average inputs.
- `decompose` on a component-aware ratio delta emits one row per segment with
  `value_effect`, `mix_effect`, `residual`, `contribution`, share columns, and
  stable ranks.
- Contribution sums reconcile to the overall ratio delta.
- Existing sum metric compare/decompose tests continue to pass unchanged.
- Analysis skill examples and user-facing docs include a ratio mix
  decomposition example using `frame.components()`.
