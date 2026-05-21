# Observe Metric Frame Artifact Design

Date: 2026-05-21

## Goal

Define `metric_frame` as the AOI spec output artifact for the `observe`
operation. This unifies the current scalar, time-series, segmented, and panel
observe output formats under one artifact contract.

This design intentionally does not address backward compatibility, historical
artifact migration, downstream artifact families, Plan DSL, selector resolution,
or outcome envelopes.

## Design Decisions

1. `observe` successful output is a top-level AOI artifact with
   `artifact_family = "metric_frame"`.
2. The artifact uses `shape` to distinguish `scalar`, `time_series`,
   `segmented`, and `panel`.
3. `payload.series` is the only data container for all observe shapes.
4. `subject.scope` is required and uses `{}` when no non-time filter is applied.
5. Quality signals and Marivo execution metadata are not part of the public AOI
   `MetricFrameArtifact` contract.
6. Old `ScalarObservationResult`, `TimeSeriesObservationResult`, and
   `SegmentedObservationResult` result-union contracts are removed from the
   target AOI observe contract.

## Successful Artifact Contract

`observe` no longer returns a thin wrapper around a result union:

```ts
{ artifact_id, result: ScalarObservationResult | TimeSeriesObservationResult | ... }
```

Instead, a successful `observe` returns:

```ts
type MetricFrameArtifact = {
  artifact_id: string;
  artifact_family: "metric_frame";
  shape: MetricFrameShape;
  subject: MetricFrameSubject;
  axes: MetricFrameAxis[];
  measures: MetricFrameMeasure[];
  payload: MetricFramePayload;
};

type MetricFrameShape =
  | "scalar"
  | "time_series"
  | "segmented"
  | "panel";
```

Field semantics:

- `artifact_family` is the primary discriminator for downstream consumers.
- `shape` identifies the structure within the `metric_frame` family.
- `subject` describes what metric was observed and over which scope.
- `axes` describes how the payload is organized.
- `measures` declares stable numeric fields available in the payload.
- `payload.series` contains all observed data.

`observation_type` is not a public AOI contract discriminator. Implementations
may use internal variables while lowering or executing observe, but the public
contract should use `shape`.

## Shape Inference

The observe request stays a single request type. Runtime derives the artifact
shape from `granularity` and `dimensions`:

| granularity | dimensions | shape |
|---|---|---|
| absent | absent | `scalar` |
| set | absent | `time_series` |
| absent | set | `segmented` |
| set | set | `panel` |

The request schema should not use separate `oneOf` branches for scalar,
time-series, segmented, or panel observe requests.

## Axes

```ts
type MetricFrameAxis =
  | { kind: "time"; grain: TimeGranularity }
  | { kind: "dimension"; name: string };
```

Axis rules:

- `scalar`: `axes = []`
- `time_series`: `axes = [{ kind: "time", grain }]`
- `segmented`: `axes = dimensions.map(name => ({ kind: "dimension", name }))`
- `panel`: time axis first, followed by dimension axes

The top-level `axes` field is structural. Downstream consumers should use it
with `artifact_family` and `shape` for compatibility checks instead of opening
private payload details.

## Payload

All observe shapes use the same payload structure:

```ts
type MetricFramePayload = {
  series: MetricFrameSeries[];
};

type MetricFrameSeries = {
  keys: Record<string, string | number | boolean | null>;
  points: MetricFramePoint[];
};

type MetricFramePoint = {
  window?: { start: datetime; end: datetime };
  value: number | null;
};
```

Shape-specific constraints:

- `scalar`: exactly one series, `keys = {}`, exactly one point, and no point
  `window`
- `time_series`: exactly one series, `keys = {}`, and every point has a
  `window`
- `segmented`: one series per segment key tuple, each series has exactly one
  point, and points have no `window`
- `panel`: one series per dimension key tuple, and every point has a `window`

Dimension values live in `series.keys`. Time values live in `point.window`.
This makes `panel` the natural grouped time-series shape, while scalar,
time-series, and segmented observations are simpler projections of the same
container.

## Subject

```ts
type MetricFrameSubject = {
  kind: "metric";
  metric_ref: string;
  time_scope: TimeScope;
  scope: SubjectScope;
};
```

Subject rules:

- `metric_ref` is the resolved semantic metric reference.
- `time_scope` is the overall observe request window.
- `scope` is required.
- If no non-time filter is applied, `scope = {}`.
- `scope` must not be `null`.
- `payload.series[].points[].window` only represents bucket windows and must
  not replace `subject.time_scope`.

## Measures

Observe-only `metric_frame` exposes a single measure:

```ts
type MetricFrameMeasure = {
  id: "value";
  value_type: "number";
  nullable: true;
  unit?: string | null;
};
```

Rules:

- `value` is the only public measure in this observe-only design.
- If unit metadata is available, it belongs on the measure declaration rather
  than each individual point.
- `sample_summary` is out of scope for this design because it belongs to later
  test-oriented input modeling.

## Failure And Empty Data

This design only specifies successful `MetricFrameArtifact` objects.

Failure rules:

- A successful artifact has `payload` and no `failure`.
- Failed observe execution must not produce a fake `metric_frame`.
- Request validation, metric resolution, and query execution failures should be
  represented by the runtime/API failure surface.

Empty data rules:

- Empty or sparse data can still be a successful `metric_frame`.
- A time-series may contain points with `value = null`.
- A segmented or panel frame may have an empty `series` list when no groups are
  observed.
- Empty data is a data state, not an artifact failure.

## Metadata Boundary

The public AOI `MetricFrameArtifact` should contain only portable analysis
semantics. The following do not belong in the public artifact:

- query hash
- SQL
- engine name
- elapsed time
- executed timestamp
- row count
- sample size
- debug plan
- Marivo product metadata
- finding, proposition, or assessment state

Those fields may exist in Marivo execution envelopes, trace views, artifact
store metadata, finding provenance, or state surfaces.

Quality signals are also excluded from the main AOI artifact contract in this
observe-only design. Marivo may expose them through envelope or metadata
surfaces, but `MetricFrameArtifact` should not define public `quality` fields
yet.

## Downstream Notes

This design does not rewrite downstream operation outputs. It only gives them a
stable observe artifact to consume later.

Expected future consumption rules:

- `compare` consumes two `metric_frame` artifacts and checks comparability by
  `shape` and `axes`.
- `detect` consumes scan-ready `metric_frame(time_series | panel)` artifacts.
- `correlate` consumes two metric frames with alignable time axes.
- `forecast` consumes forecastable `metric_frame(time_series)` artifacts.
- `decompose` remains downstream of compare-like output and does not directly
  consume observe output in this design.

These are directional notes, not new request contracts.

## Non-Goals

This design does not define:

- full AOI artifact algebra
- `delta_frame`, `attribution_frame`, or `forecast_frame` contracts
- Plan DSL
- typed reference model
- selector resolver
- outcome envelope
- historical artifact migration
- backward-compatible readers for old observe result unions
- Marivo-specific execution metadata
- evidence engine finding/proposition/assessment schemas

## Conclusion

`metric_frame` should be the AOI spec artifact output for `observe`.

This makes observe a single stable artifact family with multiple shapes, instead
of a union of unrelated result payloads. It also creates a clean downstream
boundary: later operations can reason about `artifact_family`, `shape`, `axes`,
and `measures` without learning several observe-specific result formats.
