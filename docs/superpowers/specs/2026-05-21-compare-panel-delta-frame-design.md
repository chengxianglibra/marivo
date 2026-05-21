# Compare Intent: Panel Input + Delta Frame Unification Design

Date: 2026-05-21
Status: Draft — approved by user, pending implementation plan

## Context

The compare intent currently:
- Explicitly rejects panel `metric_frame` inputs with `UNSUPPORTED_OPERATION`
- Outputs `compare_artifact` with `comparison_type` discriminator (not `delta_frame` as `artifact_family`)
- Uses field names (`delta`, `absolute_delta`, `relative_delta`) that differ from the v0.3 AOI design
- Carries scalar top-level aliases (`current_value`, `baseline_value`, `absolute_delta`, etc.) alongside the v2.0 series structure

Per the analysis operation architecture design doc (`docs/specs/analysis/foundations/analysis-operation-architecture.md`) and the v0.3 AOI design (`docs/specs/analysis/aoi-v0.3-design.md`), compare should:
- Accept all four `metric_frame` shapes including `panel`
- Output `delta_frame` as a proper artifact family with `shape`, `axes`, `measures`, `subject`, `payload`
- Include `comparison_side` as a structural axis
- Use `delta_abs` / `delta_pct` as measure names

This is a **breaking change** with no backward compatibility or migration path.

## Design Decisions

### 1. Panel delta computation: per-series, time-aligned

Each panel series (dimension key combination) is independently time-aligned and compared. Panel delta preserves the `time x dimension` structure, producing per-series per-bucket deltas. Each series entry has dimension keys, and each point has time window + delta fields.

Calendar-aligned compare types (`holiday_aligned`, `weekday_aligned`) are supported for panel. The bucket pairing resolution is shared across all series (same time scope), but delta computation happens per series.

### 2. Payload structure: self-contained points with comparison_side as metadata axis

Each series entry is keyed by dimension values only. Each point carries all fields in a single self-contained record:

```python
{
    "window": {...},             # time shapes only
    "current_value": float | None,
    "baseline_value": float | None,
    "delta_abs": float | None,   # current - baseline
    "delta_pct": float | None,   # delta_abs / baseline_value
    "direction": "increase" | "decrease" | "flat" | "undefined",
    "presence": "both" | "current_only" | "baseline_only",  # time shapes only
}
```

`comparison_side` is declared as an axis expressing the semantic structure (this artifact is a comparison of two sides) but does NOT appear in series `keys`. This keeps the series count matching the input (N entries, not 3N), makes each point self-contained, and minimizes downstream disruption.

### 3. Change scope: compare + all direct downstream consumers

All downstream consumers that read `compare_artifact` are updated in the same change:
- decompose, attribute, diagnose intent handlers
- compare_extractor, decompose_extractor evidence extractors
- proposition_seeding
- aoi_projection, aoi_runtime, aoi schema/contracts

Other intents (observe, detect, correlate, test, forecast) are unchanged.

## Delta Frame Envelope Structure

### Full artifact dict

```python
{
    "artifact_id": "<id>",
    "artifact_family": "delta_frame",
    "shape": "<shape>",
    "subject": {
        "kind": "comparison",
        "metric_ref": "<ref>",
        "current": {<SubjectScopeRef>},
        "baseline": {<SubjectScopeRef>},
    },
    "axes": [...],
    "measures": [
        {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": "<unit>"},
        {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
    ],
    "payload": {"series": [...]},
    # --- Marivo internal fields (not in v0.3 public contract) ---
    "schema_version": "2.0",
    "metric": "<name>",
    "current_ref": {...},
    "baseline_ref": {...},
    "lineage": {...},
    "comparability": {...},
    "analytical_metadata": {...},
    "execution_metadata": {...},
}
```

### Axes per shape

| shape | axes |
|---|---|
| `scalar_delta` | `[{"kind": "comparison_side"}]` |
| `time_series_delta` | `[{"kind": "time", "grain": "<grain>"}, {"kind": "comparison_side"}]` |
| `segmented_delta` | `[{"kind": "dimension", "name": "<dim>"}, ... {"kind": "comparison_side"}]` |
| `panel_delta` | `[{"kind": "time", "grain": "<grain>"}, {"kind": "dimension", "name": "<dim>"}, ... {"kind": "comparison_side"}]` |

### Subject: comparison kind

The `subject` field changes from `{kind: "metric", ...}` to `{kind: "comparison", metric_ref, current, baseline}`:

```python
{
    "kind": "comparison",
    "metric_ref": "metric.total_query_count",
    "current": {"time_scope": {...}, "scope": {...}},
    "baseline": {"time_scope": {...}, "scope": {...}},
}
```

`current` and `baseline` are `SubjectScopeRef` dicts derived from the input artifacts' time_scope and scope fields.

### Point fields (all shapes)

```python
{
    "window": {"start": "...", "end": "..."},  # present for time shapes; absent for scalar/segmented
    "current_value": float | None,
    "baseline_value": float | None,
    "delta_abs": float | None,
    "delta_pct": float | None,
    "direction": "increase" | "decrease" | "flat" | "undefined",
    "presence": "both" | "current_only" | "baseline_only",  # time shapes only
}
```

## Panel Delta Computation

### Algorithm

1. **Pre-flight validation**:
   - Both inputs must be `metric_frame(shape=panel)` with `comparable` capability
   - Same `metric_ref`, same dimension names (order-insensitive sorted comparison), same time grain, same unit
   - Remove the current `UNSUPPORTED_OPERATION` hard-reject for panel

2. **Per-series time-aligned pairing**:
   - Extract series entries from both artifacts
   - Build `series_map` keyed by dimension key tuples from each artifact
   - For each dimension key present in either artifact:
     - Get left (current) and right (baseline) series entries
     - Align time buckets using the same pairing logic as `time_series_delta` (supporting `normal`, `holiday_aligned`, `weekday_aligned` compare types)
     - The bucket pairing resolution is shared across all series (same time scope)
     - Compute per-bucket deltas using `_compute_absolute_delta`, `_compute_relative_delta`, `_compute_direction`

3. **Build panel_delta series**:
   - Each series entry: `keys={dim1: val1, ...}`, `points=[{window, current_value, baseline_value, delta_abs, delta_pct, direction, presence}]`
   - Series present in only one side: `presence="current_only"` or `"baseline_only"` with partial delta fields
   - Sort by descending non-null point count, then by dimension keys (same pattern as observe panel)

4. **Scope-level summary**:
   - Aggregate all matched `current_value` / `baseline_value` across all series and buckets
   - Produce `summary_current_value`, `summary_baseline_value`, `summary_absolute_delta`, `summary_relative_delta`, `summary_direction` at artifact top level

5. **Calendar alignment**:
   - For `normal` compare_type: relative position pairing (same as time_series_delta)
   - For calendar-aligned compare_types: apply calendar pairing to each series independently (shared time scope, same bucket pairing resolution)

### Example output

2-country panel with day granularity:

```python
{
    "artifact_family": "delta_frame",
    "shape": "panel_delta",
    "axes": [
        {"kind": "time", "grain": "day"},
        {"kind": "dimension", "name": "country"},
        {"kind": "comparison_side"},
    ],
    "measures": [
        {"id": "delta_abs", "value_type": "number", "nullable": True},
        {"id": "delta_pct", "value_type": "number", "nullable": True},
    ],
    "payload": {
        "series": [
            {
                "keys": {"country": "US"},
                "points": [
                    {"window": {"start": "2026-05-15", "end": "2026-05-16"},
                     "current_value": 150, "baseline_value": 100,
                     "delta_abs": 50, "delta_pct": 0.5,
                     "direction": "increase", "presence": "both"},
                    {"window": {"start": "2026-05-16", "end": "2026-05-17"},
                     "current_value": 160, "baseline_value": 110,
                     "delta_abs": 50, "delta_pct": 0.45,
                     "direction": "increase", "presence": "both"},
                ],
            },
            {
                "keys": {"country": "UK"},
                "points": [
                    {"window": {"start": "2026-05-15", "end": "2026-05-16"},
                     "current_value": 80, "baseline_value": 70,
                     "delta_abs": 10, "delta_pct": 0.14,
                     "direction": "increase", "presence": "both"},
                ],
            },
        ],
    },
    "summary_current_value": 230,    # 150 + 80 (first bucket matched)
    "summary_baseline_value": 170,
    "summary_absolute_delta": 60,
    "summary_relative_delta": 0.35,
    "summary_direction": "increase",
}
```

## Field Name Migration

| Old field (`compare_artifact`) | New field (`delta_frame`) |
|---|---|
| `artifact_type: "compare_artifact"` | `artifact_family: "delta_frame"` |
| `comparison_type: "scalar_delta"` | `shape: "scalar_delta"` |
| `comparison_type: "time_series_delta"` | `shape: "time_series_delta"` |
| `comparison_type: "segmented_delta"` | `shape: "segmented_delta"` |
| — (new) | `shape: "panel_delta"` |
| `delta` (in point) | `delta_abs` (in point) |
| `absolute_delta` (scalar top-level) | removed — read from `series[0].points[0].delta_abs` |
| `relative_delta` (scalar top-level) | removed — read from `series[0].points[0].delta_pct` |
| `current_value` (scalar top-level) | removed — read from `series[0].points[0].current_value` |
| `baseline_value` (scalar top-level) | removed — read from `series[0].points[0].baseline_value` |
| `direction` (scalar top-level) | removed — read from `series[0].points[0].direction` |

Summary-level fields (`summary_current_value`, `summary_baseline_value`, `summary_absolute_delta`, `summary_relative_delta`, `summary_direction`) are kept at top level for all shapes. These names remain unchanged — `summary_absolute_delta` is already distinct from the point-level `delta_abs`.

## Downstream Consumer Adaptation

### `metric_frame.py` helpers

New/updated helpers:

- `is_delta_frame_artifact(artifact)` — checks `artifact_family == "delta_frame"`
- `read_delta_frame_shape(artifact)` — reads `shape` field (replaces `comparison_type` dispatch)
- `read_delta_frame_series(artifact)` — same as `read_metric_frame_series` (both use `payload.series`)
- `build_delta_frame_artifact(...)` — new builder producing full `delta_frame` structure
- Rename `read_compare_scalar_point` → `read_delta_scalar_point` — same logic, expects `delta_abs` instead of `delta`

### `decompose.py`

Current `_normalize_decompose_compare_input()` dispatches by `comparison_type`. Update to:
- Dispatch by `shape` (via `read_delta_frame_shape()`)
- Read `delta_abs` from points instead of `delta`
- Accept `panel_delta` as input: decompose treats each panel series as a separate segment and computes contribution shares across dimension key combinations, rolling up per-bucket deltas to scope-level totals per series. The decomposition dimension is the same as the panel's dimension axes — each dimension key combination becomes a contributor to the overall delta.
- `segmented_delta` rejection logic stays (future work to support)

### `attribute.py`

Currently hardcodes `comparison_type: "scalar_delta"`. Update to:
- Read `shape` field from compare output
- Write `shape: "scalar_delta"` in its `compare_ref`

### `diagnose.py`

Currently hardcodes `comparison_type: "scalar_delta"`. Update to:
- Read `shape` field from compare output
- Write `shape: "scalar_delta"` in its `attribution_comparison`

### Evidence extractors

- **`compare_extractor.py`**: dispatch by `shape` instead of `comparison_type`; read `delta_abs` from points instead of `delta`; add `panel_delta` extraction path (1 finding per series per bucket)
- **`decompose_extractor.py`**: read `shape` from embedded `compare_ref` instead of `comparison_type`
- **`proposition_seeding.py`**: update `change_kind` mapping from `delta_kind` (derived from `shape`); add `"panel_change"` for `panel_delta`

### AOI projection (`aoi_projection.py`)

Current `project_aoi_artifact_result("compare", payload)` dispatches by `comparison_type` and produces three distinct AOI result classes. Update to:
- Dispatch by `shape`
- Fast path: if raw dict has `artifact_family == "delta_frame"`, validate and envelope directly (same pattern as observe fast path)
- Legacy path: retained for any remaining `compare_artifact` format data
- Add `panel_delta` projection branch

### AOI contracts (`aoi.schema.json`, generated models)

- Add `DeltaFrameArtifact` model definition to `aoi.schema.json`
- Add `ComparisonSubject` model (`kind: "comparison"`, `metric_ref`, `current`, `baseline`)
- Add `comparison_side` axis type (`kind: "comparison_side"`, no additional fields)
- Add `DeltaFramePoint` model with `window`, `current_value`, `baseline_value`, `delta_abs`, `delta_pct`, `direction`, `presence`
- Add `DeltaFramePayload` model with `series`
- Regenerate contract models via `scripts/generate_contract_models.py`
- Update `AoiArtifact` type alias in `aoi_runtime.py` to include `DeltaFrameArtifact`
- Update `validate_aoi_artifact()` to route `artifact_family == "delta_frame"` to `DeltaFrameArtifact.model_validate()`

## Files Changed

### Core (compare output rewrite)

- `marivo/runtime/intents/compare.py` — rewrite output to `delta_frame`, add panel delta computation, remove panel hard-reject
- `marivo/runtime/intents/metric_frame.py` — add delta_frame helpers, rename scalar point reader

### Contracts (schema + generated models)

- `aoi-spec/schema/aoi.schema.json` — add `DeltaFrameArtifact`, `ComparisonSubject`, `comparison_side` axis, `DeltaFramePoint`
- `marivo/contracts/generated/aoi.py` — regenerated
- `marivo/contracts/aoi_runtime.py` — update `AoiArtifact` alias, `validate_aoi_artifact()`
- `marivo/contracts/aoi_projection.py` — add `delta_frame` fast path, panel_delta projection

### Downstream consumers

- `marivo/runtime/intents/decompose.py` — read `delta_frame`, dispatch by `shape`, accept `panel_delta`
- `marivo/runtime/intents/attribute.py` — read `shape` instead of `comparison_type`
- `marivo/runtime/intents/diagnose.py` — read `shape` instead of `comparison_type`
- `marivo/runtime/evidence/compare_extractor.py` — dispatch by `shape`, read `delta_abs`, add `panel_delta`
- `marivo/runtime/evidence/decompose_extractor.py` — read `shape` from `compare_ref`
- `marivo/runtime/evidence/proposition_seeding.py` — update `change_kind` mapping, add `panel_change`

### Tests

- Update all compare intent tests to use `delta_frame` assertions
- Update decompose/attribute/diagnose tests to consume `delta_frame`
- Add panel delta integration test
- Update evidence extractor tests
- Update projection tests