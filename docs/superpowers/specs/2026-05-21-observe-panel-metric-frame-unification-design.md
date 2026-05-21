# Observe Panel Mode & Metric Frame Unification Design

Date: 2026-05-21

## Goal

Enhance the `observe` atomic intent to support **panel mode** (simultaneous time axis + dimension axis observation) and unify all 4 observation shapes (scalar, time_series, segmented, panel) under a single `axes + series` format. Refactor all downstream intents (compare, decompose, detect, forecast, correlate) to consume the new unified format.

This change is scoped to the atomic intent layer only — it does not address the analysis plan DSL or outcome envelope architecture.

## Design Decisions

1. **Panel definition**: Panel mode produces a grouped time-series panel — multiple time series, each keyed by dimension values. Output shape: `{axes: [...], series: [{keys: {region: "US"}, points: [{window, value}, ...]}, ...]}`.
2. **Unified format**: All 4 observation shapes share the same `axes + series` structure. No per-shape ad-hoc field combinations.
3. **AOI schema simplification**: Merge the 3 mutually-exclusive `oneOf` branches (Observe1/2/3) into a single `Observe` request type. `granularity` and `dimensions` are both optional; the runtime infers the shape from their combination.
4. **No backward compatibility**: No v1.0 compatibility logic. All new artifacts use schema_version `"2.0"`. Historical v1.0 artifacts in completed sessions remain untouched.
5. **Full downstream adaptation**: compare, decompose, detect, forecast, correlate all adapt to the new format in this scope. Panel-specific downstream support (e.g. panel compare) is deferred.
6. **Axes are first-class**: The `axes` field is a top-level structural descriptor, not metadata. Downstream intents read `axes` to determine structure, not `observation_type`.

## Unified axes + series Format

### Observation artifact (metric_frame v2.0)

```python
observation = {
    "schema_version": "2.0",
    "observation_type": "scalar" | "time_series" | "segmented" | "panel",
    "metric": metric_name,
    "time_scope": { "field": ..., "start": ..., "end": ... },
    "scope": ...,
    "predicate_filter_lineage": ...,
    "axes": [...],       # first-class structural descriptor
    "series": [...],     # unified series format
    "analytical_metadata": { ... },
    "execution_metadata": { ... },
}
```

### Axis descriptor

```python
# Time axis (present in time_series and panel)
{ "kind": "time", "grain": "day" }

# Dimension axis (present in segmented and panel)
{ "kind": "dimension", "name": "region" }

# Multiple dimension axes allowed in future; v2.0 supports one dimension axis per observe
```

### Series format (shared across all shapes)

```python
series = [
    {
        "keys": { ... },   # dimension values; {} when no dimension axis
        "points": [
            # With time axis:
            { "window": { "start": "...", "end": "..." }, "value": 120.0 }
            # Without time axis (scalar, segmented):
            { "value": 120.0 }
        ]
    }
]
```

### Shape-specific examples

**scalar:**
```json
{
  "axes": [],
  "series": [{ "keys": {}, "points": [{ "value": 1500.0 }] }]
}
```

**time_series:**
```json
{
  "axes": [{ "kind": "time", "grain": "day" }],
  "series": [{ "keys": {}, "points": [
    { "window": { "start": "2026-01-01", "end": "2026-01-02" }, "value": 120.0 },
    { "window": { "start": "2026-01-02", "end": "2026-01-03" }, "value": 135.0 }
  ]}]
}
```

**segmented:**
```json
{
  "axes": [{ "kind": "dimension", "name": "region" }],
  "series": [
    { "keys": { "region": "US" }, "points": [{ "value": 120.0 }] },
    { "keys": { "region": "EU" }, "points": [{ "value": 95.0 }] }
  ]
}
```

**panel:**
```json
{
  "axes": [
    { "kind": "time", "grain": "day" },
    { "kind": "dimension", "name": "region" }
  ],
  "series": [
    { "keys": { "region": "US" }, "points": [
      { "window": { "start": "2026-01-01", "end": "2026-01-02" }, "value": 120.0 },
      { "window": { "start": "2026-01-02", "end": "2026-01-03" }, "value": 135.0 }
    ]},
    { "keys": { "region": "EU" }, "points": [
      { "window": { "start": "2026-01-01", "end": "2026-01-02" }, "value": 95.0 },
      { "window": { "start": "2026-01-02", "end": "2026-01-03" }, "value": 110.0 }
    ]}
  ]
}
```

### Delta frame (compare output, v2.0)

```json
{
  "axes": [{ "kind": "time", "grain": "day" }],
  "series": [{ "keys": {}, "points": [
    {
      "window": { "start": "2026-01-01", "end": "2026-01-02" },
      "current_value": 120.0,
      "baseline_value": 100.0,
      "delta": 20.0,
      "delta_pct": 0.2
    }
  ]}]
}
```

Segmented delta:
```json
{
  "axes": [{ "kind": "dimension", "name": "region" }],
  "series": [
    { "keys": { "region": "US" }, "points": [
      { "current_value": 120.0, "baseline_value": 100.0, "delta": 20.0, "delta_pct": 0.2 }
    ]},
    { "keys": { "region": "EU" }, "points": [
      { "current_value": 95.0, "baseline_value": 90.0, "delta": 5.0, "delta_pct": 0.056 }
    ]}
  ]
}
```

### Attribution frame (decompose output, v2.0)

```json
{
  "axes": [{ "kind": "dimension", "name": "region" }],
  "series": [
    { "keys": { "region": "US" }, "points": [
      { "value": 120.0, "contribution": 15.0, "share": 0.75 }
    ]},
    { "keys": { "region": "EU" }, "points": [
      { "value": 95.0, "contribution": 5.0, "share": 0.25 }
    ]}
  ]
}
```

## AOI Schema Changes

### Observe request: merge into single type

Replace the current 3 `oneOf` branches (Observe1/Observe2/Observe3) with a single flat type:

```json
{
  "observe": {
    "type": "object",
    "additionalProperties": false,
    "required": ["metric", "time_scope"],
    "properties": {
      "metric": { "type": "string", "minLength": 1 },
      "time_scope": { "$ref": "#/$defs/primitives/TimeScope" },
      "filter": { "$ref": "#/$defs/primitives/Expression" },
      "granularity": { "$ref": "#/$defs/primitives/TimeGranularity" },
      "dimensions": {
        "type": "array",
        "minItems": 1,
        "items": { "type": "string", "minLength": 1 }
      }
    }
  }
}
```

Shape inference table (runtime responsibility):

| granularity | dimensions | observation_type |
|-------------|-----------|-----------------|
| None        | None      | scalar          |
| set         | None      | time_series     |
| None        | set       | segmented       |
| set         | set       | panel           |

### Contract model changes

Delete `Observe1`, `Observe2`, `Observe3`. Add single `Observe` model:

```python
class Observe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str = Field(..., min_length=1)
    time_scope: TimeScope
    filter: Expression = None
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"] = None
    dimensions: list[Dimension] = Field(None, min_length=1)
```

### MCP tool changes

`to_aoi_observe_request` simplifies to return `aoi.Observe` unconditionally. The MCP tool parameter description updates to allow `granularity` + `dimensions` simultaneously (panel mode).

The `observe` MCP tool description should mention all 4 modes:
- Omit both → scalar
- Set `granularity` only → time_series
- Set `dimensions` only → segmented
- Set both → panel (grouped time-series)

## Runtime observe Implementation

### Panel mode SQL

Panel mode extends the time_series GROUP BY with dimension columns:

```sql
SELECT DATE_TRUNC('{granularity}', {time_col}) AS bucket_start,
       {dim1}, {dim2}, ...
       SUM({metric_expr}) AS value
FROM {qualified_table}
WHERE {time_col} >= :start AND {time_col} < :end
  AND {scope predicates}
GROUP BY bucket_start, {dim1}, {dim2}, ...
ORDER BY bucket_start, {dim1}, {dim2}, ...
```

### Panel mode series construction

```python
# Group rows by dimension keys, then build series per group
series_by_keys = group_rows_by_dimension_keys(rows, dimensions)
series = []
for keys_dict, group_rows in series_by_keys.items():
    sparse_points = build_sparse_points(group_rows, granularity)
    dense_points = build_dense_points(sparse_points, start, end, granularity)
    series.append({ "keys": keys_dict, "points": dense_points })
```

### Unified output construction

All 4 modes share the same output assembly path:

```python
axes = build_axes(granularity, dimensions)
series = build_series(rows, axes, granularity, dimensions, start, end)
observation = {
    "schema_version": "2.0",
    "observation_type": determine_type(granularity, dimensions),
    "metric": metric_name,
    "time_scope": resolved_time_scope,
    "scope": scope_raw or {},
    "predicate_filter_lineage": ...,
    "axes": axes,
    "series": series,
    "analytical_metadata": { ... },
    "execution_metadata": { ... },
}
```

Helper functions:

```python
def build_axes(granularity, dimensions):
    axes = []
    if granularity is not None:
        axes.append({ "kind": "time", "grain": granularity })
    if dimensions is not None:
        for dim in dimensions:
            axes.append({ "kind": "dimension", "name": dim })
    return axes

def determine_type(granularity, dimensions):
    if granularity is not None and dimensions is not None:
        return "panel"
    if granularity is not None:
        return "time_series"
    if dimensions is not None:
        return "segmented"
    return "scalar"
```

### Dense series handling for panel

Panel mode needs dense series per segment group. Each group independently fills missing time buckets. The existing `_build_dense_series` logic is reused per group.

## Downstream Intent Adaptation

### compare

- Read `axes` from input artifacts to determine alignment strategy
- Time axis present → align by `window.start/end` (existing logic, new field path: `series[0].points[].window`)
- Dimension axis present → align by `series[].keys` (existing logic, new field path)
- Output delta_frame uses unified `axes + series` format with `current_value/baseline_value/delta/delta_pct` in each point

### decompose

- Read `axes` from compare artifact to find dimension axis name
- Group delta series by keys, compute contribution/share per group
- Output attribution_frame uses unified format with `contribution/share` in each point

### detect

- Read `axes` to confirm time axis presence
- Read `series[0].points` for anomaly scanning (field path change only)
- Future: scan each series in panel independently (requires `scan_dimension` parameter, already supported)

### forecast

- Read `series[0].points` from time_series observe (field path change only)

### correlate

- Read `series[0].points` from both time_series observes (field path change only)

### Core adaptation pattern

Every downstream intent replaces `observation_type`-based branching with `axes`-based structural detection. The pattern:

```python
has_time_axis = any(a["kind"] == "time" for a in artifact["axes"])
has_dim_axis = any(a["kind"] == "dimension" for a in artifact["axes"])
dim_names = [a["name"] for a in artifact["axes"] if a["kind"] == "dimension"]
```

## Version Strategy

- `schema_version` changes from `"1.0"` to `"2.0"` for all metric_frame, delta_frame, and attribution_frame artifacts
- No backward compatibility logic: no adapters, no dual-format reads
- Historical v1.0 artifacts in completed sessions remain untouched
- All test fixtures update to v2.0 format

## Files to Change

### AOI Schema & Contracts
- `aoi-spec/schema/aoi.schema.json` — merge observe oneOf into single type
- `aoi-spec/schema/aoi.schema.yaml` — same change in YAML
- `scripts/generate_contract_models.py` — regenerate Observe model
- `marivo/contracts/generated/aoi.py` — regenerated contract models

### MCP Tools
- `marivo/transports/mcp/tools/intents.py` — simplify `to_aoi_observe_request`, update MCP tool descriptions
- `marivo/transports/mcp/tools/semantic.py` — if needed for semantic model changes

### Runtime Intents
- `marivo/runtime/intents/observe.py` — major refactor: unified output, panel mode
- `marivo/runtime/intents/compare.py` — adapt to v2.0 format, unified delta output
- `marivo/runtime/intents/decompose.py` — adapt to v2.0 format, unified attribution output
- `marivo/runtime/intents/detect.py` — adapt field paths
- `marivo/runtime/intents/forecast.py` — adapt field paths
- `marivo/runtime/intents/correlate.py` — adapt field paths
- `marivo/runtime/intents/attribute.py` — adapt (wraps observe + compare + decompose)
- `marivo/runtime/intents/diagnose.py` — adapt (wraps detect + compare + decompose)
- `marivo/runtime/intents/_helpers.py` — update commit_step_result if needed

### Evidence & Semantic
- `marivo/runtime/evidence/semantic_repository.py` — adapt to v2.0 artifact format
- `marivo/runtime/semantic/analysis_validator.py` — adapt validation logic
- `marivo/runtime/semantic/compile_step.py` — adapt if needed
- `marivo/runtime/semantic_ops.py` — adapt if needed

### Tests
- All observe, compare, decompose, detect, forecast, correlate, attribute, diagnose tests
- Integration tests (test_e2e_osi_aoi.py, test_observe_compare_lineage_reuse.py)
- Test fixtures and shared helpers

### Docs
- `docs/specs/analysis/intents/atomic/observe.md` — update to reflect panel mode and v2.0 format
- `docs/specs/analysis/foundations/analysis-operation-architecture.md` — update metric_frame description if needed

## Non-Goals

- Panel-specific downstream support (panel compare, panel decompose) — deferred to future work
- Analysis plan DSL or outcome envelope — not in scope
- Forecast_frame format changes — forecast output stays as-is (it's a sibling frame family, not a metric_frame variant)
- Evidence result format changes (anomaly_candidate, correlation_result, test_result) — these are not frames

## Success Criteria

1. `observe` with `granularity` + `dimensions` produces valid panel artifact with `axes` + `series` format
2. All 4 observe shapes produce v2.0 unified format
3. All downstream intents (compare, decompose, detect, forecast, correlate) consume v2.0 format correctly
4. `make test` passes with all existing + new tests adapted
5. `make typecheck` passes
6. AOI schema regenerated and contract models updated
7. MCP tool allows `granularity` + `dimensions` simultaneously
