# Attribution Composite Materialization Design

## Status

Accepted design for implementation planning.

Date: 2026-07-01

## Context

The current analysis surface exposes `session.attribute(delta, axes=[...])` as
the agent-facing entry point for change attribution. The implementation is
still partly frame-local and over-parameterized:

- single-axis `attribute(...)` delegates to the existing single-axis
  `decompose` implementation;
- multi-axis `attribute(...)` computes hierarchy rows inside `attribute.py` by
  grouping the input delta frame;
- the `mode` parameter exposes `flat`, `nested`, and `recursive` choices even
  though `flat` is just the single-axis case and true recursive attribution has
  no implemented contract.
- Neither `attribute` nor `decompose` can attribute across dimensions that are
  absent from the input `DeltaFrame`.

This creates two problems.

First, contribution math is split across `attribute` and `decompose`. Single-axis
and multi-axis paths can drift in validation, component-aware handling, panel
bucket behavior, lineage, ranking, and error reporting.

Second, the agent-facing flow has too much cognitive load. A typical agent loop
is:

1. Observe current and baseline metric windows.
2. Compare them and detect meaningful movement.
3. Ask for attribution by explicit dimensions such as region or platform.

If those dimensions were not in the original aggregate delta, Marivo currently
forces the agent to manually reconstruct the prior observe calls, add the
dimensions, compare again, and only then attribute. The agent has already made
the business judgment by passing explicit axes. The remaining work is a
deterministic, auditable materialization DAG.

## Decision

Adopt option B:

- `decompose` becomes the complete frame-local attribution primitive.
- `attribute` becomes the agent-facing composite intent that can materialize a
  missing-axis delta before calling `decompose`.

In short:

```text
decompose = frame-local contribution decomposition
attribute = explicit-axis attribution workflow for agents
```

`attribute` must not choose axes for the agent. Axes must be explicitly passed
by the caller or selected from a prior candidate set. Once axes are explicit,
`attribute` may safely perform deterministic materialization when the input
delta lacks those axes and the source frames are recoverable.

This design revises the earlier `decompose` target in
`docs/specs/analysis/python-analysis-design.md`, which described core
`decompose` as single-axis only. The revised scope is ordered hierarchy
decomposition: `axes=[country, platform]` means "country first, then platform
within each country." It is not the same as unordered joint attribution over a
`country x platform` cube. Unordered cross-axis attribution remains a future
composite such as `cross_axis_attribution`.

## Goals

- Keep one default agent-facing entry point for "why did this metric change?"
  after `observe -> compare`: `session.attribute(delta, axes=[...])`.
- Move all contribution math, including ordered-axis hierarchy output, into
  `decompose`.
- Allow `attribute` to automatically materialize missing explicit axes from a
  recoverable delta lineage.
- Remove the premature `mode` parameter. Axis count and axis order are enough to
  define the supported decomposition shape.
- Preserve fail-closed behavior when materialization cannot be proven safe.
- Preserve a single terminal result family: `AttributionFrame`.
- Record the expanded DAG in lineage and job metadata so the result is auditable
  and replayable.

## Non-Goals

- Do not make Marivo choose attribution axes automatically.
- Do not add a generic planner, `run(intent=...)`, or strategy recommender.
- Do not hide unsupported backend, lineage, sampled-fold, or component cases
  behind silent fallbacks.
- Do not introduce a new public result family for this workflow.
- Do not make `compare` accept metric-plus-window shorthand; compare remains
  `MetricFrame + MetricFrame -> DeltaFrame`.

## Public Contract

The default agent-facing API remains:

```python
drivers = session.attribute(delta, axes=[region])
drivers = session.attribute(delta, axes=[region, platform])
```

`attribute` accepts:

- `frame: DeltaFrame`
- `axes: list[DimensionInput]`

The expert/frame-local API becomes:

```python
drivers = decompose(delta_with_axes, axes=[region], session=session)
drivers = decompose(delta_with_axes, axes=[region, platform], session=session)
```

`decompose(axis=...)` is not the new documented contract. The implementation
plan may keep it temporarily for compatibility, but docs, help, examples, and
new tests should use `axes=[...]`.

## Operator Boundaries

### `decompose`

`decompose` is a primitive over an already materialized `DeltaFrame`.

Responsibilities:

- Normalize and validate explicit axes.
- Resolve axes to columns in the input delta or component delta.
- Compute single-axis and multi-axis contribution rows.
- Apply component-aware ratio, weighted-average, and linear attribution paths
  consistently.
- Preserve panel bucket behavior.
- Persist an `AttributionFrame`.

Restrictions:

- No backend access.
- No observe or compare calls.
- No axis discovery.
- No materialization of missing dimensions.

### `attribute`

`attribute` is a composite intent over a `DeltaFrame` and explicit axes.

Responsibilities:

- Normalize and validate explicit axes.
- Decide whether axes are already materialized.
- If all axes are present, call `decompose` directly.
- If axes are missing, recover the source observe/compare context, materialize
  expanded current and baseline frames, compare them, and call `decompose`.
- Persist lineage and job metadata that explain the expanded DAG.
- Fail closed with structured repair guidance when materialization is unsafe.

Restrictions:

- No axis selection or ranking.
- No business explanation text.
- No silent fallback to partial-axis attribution.

## Attribute Execution Flow

For `session.attribute(delta, axes=[...])`:

1. Resolve `session` and ensure it is writable.
2. Validate that `delta` is a `DeltaFrame`.
3. Normalize axes through the semantic catalog.
4. Reject empty axes and duplicate axes.
5. Check whether each axis resolves to a column in the input delta or related
   component frame.
6. If every axis is present, call:

   ```python
   decompose(delta, axes=axes, session=session, _intent="attribute")
   ```

7. If any axis is missing, attempt materialization:
   - load `delta.meta.source_current_ref`;
   - load `delta.meta.source_baseline_ref`;
   - verify both are `MetricFrame` objects in the same session;
   - recover their original observe parameters through a reusable MetricFrame
     replay helper;
   - add missing axes to the original dimensions while preserving existing
     dimensions and order;
   - re-observe current and baseline;
   - recover the replayable alignment policy through a helper that separates
     the original policy fields from enriched alignment metadata;
   - re-compare with that replayable alignment policy;
   - call `decompose(expanded_delta, axes=axes, session=session,
     _intent="attribute")`.
8. Return the resulting `AttributionFrame`.

The result lineage must make both the public intent and expanded steps visible.
The final user-facing artifact remains a single `AttributionFrame`, but job
metadata should include:

- original delta ref;
- missing axes;
- expanded current frame ref;
- expanded baseline frame ref;
- expanded delta frame ref;
- alignment policy reused from the original compare;
- materialization status.

## Recoverability Rules

Missing-axis materialization is allowed only when Marivo can reconstruct the
observe/compare work without guessing.

Required inputs:

- The input delta has loadable `source_current_ref` and `source_baseline_ref`.
- Both source refs load to `MetricFrame` instances in the active session.
- Both source frames were produced by recoverable `observe` jobs.
- Both source frames expose a reusable observe replay descriptor or pass a
  reusable MetricFrame-level replay helper.
- Observe replay includes enough information to repeat the query:
  - metric;
  - time scope;
  - grain, when present;
  - dimensions, when present;
  - time dimension, when present;
  - filters or where clauses, when present;
  - sampling or fold policy, when present and replayable.
- The original compare alignment policy can be recovered without feeding
  enriched alignment metadata into `AlignmentPolicy`.
- Requested axes are valid dimensions or time dimensions for the metric context.

If any required input is absent or ambiguous, `attribute` raises a structured
analysis error and does not run partial materialization.

Replay and recoverability are not `attribute`-local internals. The
implementation should provide reusable helpers, for example:

- `recover_observe_replay(frame)` for MetricFrame observe replay;
- `recover_alignment_policy(delta)` for DeltaFrame compare replay.

`recover_alignment_policy(delta)` must not call `AlignmentPolicy(**delta.meta.alignment)`
directly. Compare currently stores enriched metadata such as axes, coverage,
segment information, calendar details, and baseline bucket columns alongside the
policy dump. A replay helper must either read a separately stored raw
`alignment_policy` value or filter the enriched dictionary down to the fields
accepted by `AlignmentPolicy`.

## Error Contract

Introduce or reuse an `AnalysisError` subclass for materialization failure.
The rendered error should tell the agent:

- what was requested;
- which axes were missing from the input delta;
- why materialization was not possible;
- which refs or params were inspected;
- how to repair the workflow.

Suggested structured details:

```python
{
    "delta_ref": "...",
    "missing_axes": ["sales.orders.region"],
    "recoverability_status": "source_frame_missing | observe_params_missing | axis_not_compatible | runtime_unavailable | unsupported_shape",
    "source_refs": {
        "current": "frame_current",
        "baseline": "frame_baseline",
    },
    "repair_hint": "Re-run observe for current and baseline with dimensions=[region], then compare and attribute the expanded delta.",
}
```

## Ordered-Axes Decompose Output

`decompose` owns the output schema for contribution decomposition.

The decomposition shape is determined by `axes`:

- accepts one or more axes;
- treats one axis as the level-1 case;
- treats multiple axes as ordered hierarchy levels;
- emits flattened hierarchy rows;
- includes at least:
  - `level`;
  - `axis`;
  - `driver`;
  - `path`;
  - `contribution`;
  - `pct_contribution`;
  - `rank`.

`decompose` must not expose a `recursive` mode placeholder. Future recursive
top-k or stopping-condition behavior should be added only when it has a concrete
contract, either as explicit parameters or as a separate operator.

Panel deltas:

- preserve the bucket column;
- compute contribution within each bucket unless an explicit future rollup
  operator requests cross-bucket aggregation.

Component-aware deltas:

- continue to use component-specific ratio, weighted-average, and linear paths;
- share validation and axis resolution with single-axis and multi-axis paths;
- fail closed for non-linear sampled folds that cannot be safely summed.

## Documentation Updates

Update:

- `mv.help("attribute")` to explain automatic explicit-axis materialization;
- `mv.help("decompose")` or expert documentation to explain frame-local
  decomposition;
- latest analysis workflow docs in English and Chinese;
- `marivo-analysis` skill examples so agents call `attribute` for the normal
  workflow and treat `decompose` as expert/internal;
- any design/spec text that still says `attribute` is strictly frame-local.

Docs should avoid implying that Marivo chooses axes. The caller must pass axes
explicitly.

## Test Plan

Focused tests:

- `decompose` accepts `axes=[axis]` and produces level-1 attribution rows.
- `decompose` accepts `axes=[axis_a, axis_b]` and produces flattened hierarchy
  rows.
- `attribute` with axes already present calls the frame-local path and does not
  run new observe jobs.
- `attribute` with missing axes loads current and baseline sources, re-observes
  with expanded dimensions, compares with the original alignment, and returns an
  `AttributionFrame`.
- Expanded materialization preserves metric, time scope, grain, existing
  dimensions, filters, time dimension, and alignment.
- Materialization failure cases raise structured errors with missing axes and
  repair hints.
- Panel deltas preserve bucket behavior.
- Component-aware ratio, weighted-average, and linear attribution do not regress.
- Non-linear sampled folds remain fail-closed unless backed by a supported
  component-aware delta.

Regression gates:

- Run focused attribution/decompose tests first.
- Run broader analysis tests that cover observe, compare, component
  attribution, panel decompose, help, and examples.
- Run `make typecheck` and `make lint` before merge.

## Migration Notes

This is a breaking public-contract cleanup if `decompose(axis=...)` is removed
or hidden from public docs. The recommended migration is:

```python
# Old expert style
decompose(delta, axis=region, session=session)

# New expert style
decompose(delta, axes=[region], session=session)

# Agent-facing style
session.attribute(delta, axes=[region])
```

Agent-facing examples should prefer `session.attribute`.

## Implementation Decisions

The implementation plan should follow these decisions:

- Treat `axes=[...]` as the canonical `decompose` contract. Do not teach
  `axis=...` in help, docs, examples, or new tests. If a temporary shim is kept,
  it must be implementation-only and must not create a second documented path.
- Remove `mode` from `attribute` and `decompose`. Do not keep `flat`, `nested`,
  or `recursive` as public options.
- Add reusable MetricFrame observe replay and DeltaFrame alignment-policy
  recovery helpers before implementing automatic materialization. If existing
  job params are not sufficient for reliable observe replay, add a small typed
  replay descriptor to new `MetricFrame` metadata instead of reconstructing
  calls from ad hoc lineage strings.
