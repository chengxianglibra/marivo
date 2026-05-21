# Observe Panel Mode & Metric Frame Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add panel observe mode (time + dimension axes) and unify all 4 observation shapes under axes+series format, with full downstream intent adaptation.

**Architecture:** Merge AOI schema Observe1/2/3 into single Observe type. Refactor observe runtime to produce unified axes+series output. Adapt compare, decompose, detect, forecast, correlate to consume v2.0 format. All frame types (metric_frame, delta_frame, attribution_frame) share the same axes+series structure.

**Tech Stack:** Python 3.11+, Pydantic v2, DuckDB, FastMCP, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `aoi-spec/schema/aoi.schema.json` | Merge observe oneOf → single type |
| Modify | `aoi-spec/schema/aoi.schema.yaml` | Same merge in YAML |
| Modify | `scripts/generate_contract_models.py` | Regenerate Observe model |
| Regen | `marivo/contracts/generated/aoi.py` | New Observe model, delete Observe1/2/3 |
| Modify | `marivo/contracts/generated/__init__.py` | Export Observe type |
| Modify | `marivo/runtime/intents/observe.py` | Major refactor: unified output, panel mode |
| Modify | `marivo/runtime/intents/compare.py` | Adapt to v2.0 axes+series input/output |
| Modify | `marivo/runtime/intents/decompose.py` | Adapt to v2.0 delta format |
| Modify | `marivo/runtime/intents/detect.py` | Adapt field paths |
| Modify | `marivo/runtime/intents/forecast.py` | Adapt field paths |
| Modify | `marivo/runtime/intents/correlate.py` | Adapt field paths |
| Modify | `marivo/runtime/intents/attribute.py` | Adapt sub-intent consumption |
| Modify | `marivo/runtime/intents/diagnose.py` | Adapt sub-intent consumption |
| Modify | `marivo/transports/mcp/tools/intents.py` | Simplify observe request builder |
| Modify | `tests/runtime/intents/_runner_fixtures.py` | Update fixture format |
| Modify | `tests/runtime/intents/test_observe_runner.py` | Adapt to v2.0 output |
| Modify | `tests/runtime/intents/test_compare_runner.py` | Adapt to v2.0 |
| Modify | `tests/runtime/intents/test_decompose_runner.py` | Adapt to v2.0 |
| Modify | `tests/runtime/intents/test_detect_runner.py` | Adapt to v2.0 |
| Modify | `tests/runtime/intents/test_forecast_runner.py` | Adapt to v2.0 |
| Modify | `tests/runtime/intents/test_correlate_runner.py` | Adapt to v2.0 |
| Modify | `tests/runtime/intents/test_attribute_runner.py` | Adapt to v2.0 |
| Modify | `tests/integration/test_observe_compare_lineage_reuse.py` | Adapt |
| Modify | `docs/specs/analysis/intents/atomic/observe.md` | Update spec |
| Create | `marivo/runtime/intents/metric_frame.py` | Shared axes/series builder helpers |

---

### Task 1: Update AOI Schema — Merge Observe Request Types

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.json:227-340`
- Modify: `aoi-spec/schema/aoi.schema.yaml` (corresponding section)

- [ ] **Step 1: Edit aoi.schema.json — replace observe oneOf with flat type**

Replace the entire `requests.observe` definition (lines ~227-340). The current definition has 3 `oneOf` branches enforcing mutual exclusivity. Replace with a single flat type allowing both `granularity` and `dimensions` as optional fields:

```json
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
```

Remove all `oneOf`, `not`, and `required` constraints that enforce mutual exclusivity between `granularity` and `dimensions`.

- [ ] **Step 2: Edit aoi.schema.yaml — same change in YAML format**

Apply the identical structural change in the YAML version of the schema.

- [ ] **Step 3: Regenerate contract models**

Run: `.venv/bin/python scripts/generate_contract_models.py`
Expected: The script regenerates `marivo/contracts/generated/aoi.py` with a single `Observe` class (no validators enforcing mutual exclusivity). `Observe1`, `Observe2`, `Observe3` are removed from the generated file.

- [ ] **Step 4: Update AoiV02 union type**

In the regenerated `aoi.py`, verify that `AoiV02` RootModel union no longer references `Observe1 | Observe2 | Observe3` but instead references the single `Observe` type. If the union still has old references, manually adjust the schema JSON until regeneration produces the correct union.

- [ ] **Step 5: Update `marivo/contracts/generated/__init__.py`**

Remove any explicit imports/re-exports of `Observe1`, `Observe2`, `Observe3` if they exist. Add `Observe` to exports if not already present via the `aoi` module import.

- [ ] **Step 6: Verify typecheck passes**

Run: `make typecheck`
Expected: PASS (no references to Observe1/2/3 remain)

- [ ] **Step 7: Commit**

```
git add aoi-spec/schema/aoi.schema.json aoi-spec/schema/aoi.schema.yaml marivo/contracts/generated/aoi.py marivo/contracts/generated/__init__.py scripts/generate_contract_models.py
git commit -m "refactor(aoi): merge observe oneOf into single flat request type"
```

---

### Task 2: Create Shared Metric Frame Helpers

**Files:**
- Create: `marivo/runtime/intents/metric_frame.py`
- Test: `tests/runtime/intents/test_metric_frame.py`

- [ ] **Step 1: Write failing tests for metric_frame helpers**

Create `tests/runtime/intents/test_metric_frame.py`:

```python
from __future__ import annotations
import unittest
from marivo.runtime.intents.metric_frame import (
    build_axes,
    determine_observation_type,
    build_scalar_series,
    build_time_series_points,
    build_segmented_series,
    build_panel_series,
)


class TestBuildAxes(unittest.TestCase):
    def test_scalar_no_axes(self):
        self.assertEqual(build_axes(None, None), [])

    def test_time_series_single_time_axis(self):
        axes = build_axes("day", None)
        self.assertEqual(axes, [{"kind": "time", "grain": "day"}])

    def test_segmented_single_dimension_axis(self):
        axes = build_axes(None, ["region"])
        self.assertEqual(axes, [{"kind": "dimension", "name": "region"}])

    def test_panel_two_axes(self):
        axes = build_axes("day", ["region"])
        self.assertEqual(axes, [
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "region"},
        ])

    def test_panel_multiple_dimensions(self):
        axes = build_axes("day", ["region", "platform"])
        self.assertEqual(axes, [
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "region"},
            {"kind": "dimension", "name": "platform"},
        ])


class TestDetermineObservationType(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(determine_observation_type(None, None), "scalar")

    def test_time_series(self):
        self.assertEqual(determine_observation_type("day", None), "time_series")

    def test_segmented(self):
        self.assertEqual(determine_observation_type(None, ["region"]), "segmented")

    def test_panel(self):
        self.assertEqual(determine_observation_type("day", ["region"]), "panel")


class TestBuildScalarSeries(unittest.TestCase):
    def test_with_value(self):
        series = build_scalar_series(value=42.5)
        self.assertEqual(series, [{"keys": {}, "points": [{"value": 42.5}]}])

    def test_with_none_value(self):
        series = build_scalar_series(value=None)
        self.assertEqual(series, [{"keys": {}, "points": [{"value": None}]}])


class TestBuildSegmentedSeries(unittest.TestCase):
    def test_single_dimension(self):
        rows = [
            {"region": "US", "current_value": "120"},
            {"region": "EU", "current_value": "95"},
        ]
        series = build_segmented_series(rows, dimensions=["region"])
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["keys"], {"region": "US"})
        self.assertEqual(series[0]["points"][0]["value"], 120.0)
        self.assertEqual(series[1]["keys"], {"region": "EU"})
        self.assertEqual(series[1]["points"][0]["value"], 95.0)

    def test_sorted_by_value_desc(self):
        rows = [
            {"region": "EU", "current_value": "95"},
            {"region": "US", "current_value": "120"},
        ]
        series = build_segmented_series(rows, dimensions=["region"])
        self.assertEqual(series[0]["keys"]["region"], "US")
        self.assertEqual(series[1]["keys"]["region"], "EU")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/intents/test_metric_frame.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marivo.runtime.intents.metric_frame'`

- [ ] **Step 3: Implement metric_frame.py**

Create `marivo/runtime/intents/metric_frame.py`:

```python
"""Shared helpers for building unified axes+series metric frame output."""
from __future__ import annotations

import contextlib
from typing import Any

from marivo.time_contracts import TimeGrain


def build_axes(
    granularity: TimeGrain | None,
    dimensions: list[str] | None,
) -> list[dict[str, str]]:
    axes: list[dict[str, str]] = []
    if granularity is not None:
        axes.append({"kind": "time", "grain": granularity})
    if dimensions is not None:
        for dim in dimensions:
            axes.append({"kind": "dimension", "name": dim})
    return axes


def determine_observation_type(
    granularity: TimeGrain | None,
    dimensions: list[str] | None,
) -> str:
    if granularity is not None and dimensions is not None:
        return "panel"
    if granularity is not None:
        return "time_series"
    if dimensions is not None:
        return "segmented"
    return "scalar"


def _coerce_numeric_or_none(value: Any) -> float | None:
    with contextlib.suppress(TypeError, ValueError):
        if value is not None:
            return float(value)
    return None


def build_scalar_series(value: float | None) -> list[dict[str, Any]]:
    return [{"keys": {}, "points": [{"value": value}]}]


def build_time_series_points(
    sparse_series: list[dict[str, Any]],
    start: str,
    end: str,
    granularity: TimeGrain,
    dense_series_builder: Any = None,
) -> list[dict[str, Any]]:
    """Build time_series points list. Each point has {window, value}."""
    # dense_series_builder is injected from observe.py's _build_dense_series
    # to avoid circular dependency on time-bucket logic
    if dense_series_builder is not None:
        dense = dense_series_builder(
            sparse_series=sparse_series,
            start=start,
            end=end,
            granularity=granularity,
        )
        return [{"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))} for p in dense]
    # Fallback: use sparse directly
    return [
        {"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))}
        for p in sparse_series
    ]


def build_segmented_series(
    rows: list[dict[str, Any]],
    dimensions: list[str],
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for row in rows:
        keys = {dim: row.get(dim) for dim in dimensions if dim in row}
        raw_value = row.get("current_value")
        value = _coerce_numeric_or_none(raw_value)
        series.append({"keys": keys, "points": [{"value": value}]})
    series.sort(
        key=lambda item: (
            -(item["points"][0]["value"] if item["points"][0]["value"] is not None else float("-inf")),
            *[str(item["keys"].get(dimension, "")) for dimension in dimensions],
        )
    )
    return series


def build_panel_series(
    rows: list[dict[str, Any]],
    dimensions: list[str],
    start: str,
    end: str,
    granularity: TimeGrain,
    dense_series_builder: Any = None,
) -> list[dict[str, Any]]:
    """Group rows by dimension keys, build dense time series per group."""
    from collections import OrderedDict

    groups: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
    for row in rows:
        key_tuple = tuple(str(row.get(dim, "")) for dim in dimensions)
        keys_dict = {dim: row.get(dim) for dim in dimensions if dim in row}
        if key_tuple not in groups:
            groups[key_tuple] = {"keys": keys_dict, "sparse_points": []}
        bucket_start = row.get("bucket_start")
        raw_value = row.get("value")
        value = _coerce_numeric_or_none(raw_value)
        from marivo.time_contracts import bucket_window
        try:
            window = bucket_window(bucket_start, granularity)
        except (ValueError, TypeError):
            window = {"start": str(bucket_start), "end": str(bucket_start)}
        groups[key_tuple]["sparse_points"].append({"window": window, "value": value})

    series: list[dict[str, Any]] = []
    for key_tuple, group in groups.items():
        if dense_series_builder is not None:
            dense_points = dense_series_builder(
                sparse_series=group["sparse_points"],
                start=start,
                end=end,
                granularity=granularity,
            )
            points = [
                {"window": p.get("window"), "value": _coerce_numeric_or_none(p.get("value"))}
                for p in dense_points
            ]
        else:
            points = group["sparse_points"]
        series.append({"keys": group["keys"], "points": points})

    series.sort(
        key=lambda item: (
            -(sum(1 for p in item["points"] if p.get("value") is not None)),
            *[str(item["keys"].get(dim, "")) for dim in dimensions],
        )
    )
    return series


def read_axes_from_artifact(artifact: dict[str, Any]) -> list[dict[str, str]]:
    """Read axes descriptor from a v2.0 artifact."""
    return artifact.get("axes", [])


def has_time_axis(axes: list[dict[str, str]]) -> bool:
    return any(a.get("kind") == "time" for a in axes)


def has_dimension_axis(axes: list[dict[str, str]]) -> bool:
    return any(a.get("kind") == "dimension" for a in axes)


def dimension_names_from_axes(axes: list[dict[str, str]]) -> list[str]:
    return [a.get("name", "") for a in axes if a.get("kind") == "dimension"]


def time_grain_from_axes(axes: list[dict[str, str]]) -> TimeGrain | None:
    for a in axes:
        if a.get("kind") == "time":
            return a.get("grain")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/intents/test_metric_frame.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add marivo/runtime/intents/metric_frame.py tests/runtime/intents/test_metric_frame.py
git commit -m "feat: add shared metric frame axes+series builder helpers"
```

---

### Task 3: Refactor Observe Intent — Unified Output

**Files:**
- Modify: `marivo/runtime/intents/observe.py`
- Modify: `tests/runtime/intents/test_observe_runner.py`
- Modify: `tests/runtime/intents/_runner_fixtures.py`

- [ ] **Step 1: Refactor observe.py — remove granularity+dimensions mutual exclusivity check**

In `observe.py`, delete lines 282-286 that raise ValueError when both granularity and dimensions are set. The new logic allows both (panel mode).

```python
# DELETE this block:
#     if granularity is not None and dimensions is not None:
#         raise ValueError(
#             "observe: granularity and dimensions cannot both be set. "
#             "Use granularity for time_series mode or dimensions for segmented mode, not both."
#         )
```

- [ ] **Step 2: Import metric_frame helpers**

Add at top of `observe.py`:

```python
from marivo.runtime.intents.metric_frame import (
    build_axes,
    build_panel_series,
    build_scalar_series,
    build_segmented_series,
    build_time_series_points,
    determine_observation_type,
)
```

- [ ] **Step 3: Refactor scalar mode to unified format**

Replace the scalar observation dict construction (lines ~580-610). Instead of `{"observation_type": "scalar", "value": value, ...}`, build:

```python
axes = build_axes(None, None)
series = build_scalar_series(value)
observation = {
    "schema_version": "2.0",
    "observation_type": "scalar",
    "metric": metric_name,
    "time_scope": resolved_time_scope,
    "scope": scope_raw or {},
    "predicate_filter_lineage": predicate_filter_lineage_scalar,
    "unit": None,
    "axes": axes,
    "series": series,
    "analytical_metadata": {
        "aggregation_semantics": aggregation_semantics,
        "timezone": None,
        "data_complete": None,
        "quality_status": quality_status,
        "row_count": sample_size,
        "sample_size": sample_size,
        "null_rate": None,
    },
    "execution_metadata": {
        "query_hash": provenance.get("query_hash", ""),
        "engine": engine_type,
        "executed_at": now,
    },
}
artifact_name = f"{metric_name}_observe_scalar"
summary = (
    f"observe {metric_name} [{start_str} → {end_str}]: "
    f"{value if value is not None else 'no data'}"
)
```

- [ ] **Step 4: Refactor time_series mode to unified format**

Replace the time_series observation dict construction (lines ~425-455). Build axes and series using helpers:

```python
axes = build_axes(granularity_typed, None)
series_data = build_time_series_points(
    sparse_series,
    start=start_str,
    end=end_str,
    granularity=granularity_typed,
    dense_series_builder=_build_dense_series,
)
series = [{"keys": {}, "points": series_data}]
observation = {
    "schema_version": "2.0",
    "observation_type": "time_series",
    "metric": metric_name,
    "time_scope": resolved_time_scope,
    "scope": scope_raw or {},
    "predicate_filter_lineage": predicate_filter_lineage_ts,
    "unit": None,
    "axes": axes,
    "series": series,
    "analytical_metadata": {
        "aggregation_semantics": aggregation_semantics,
        "timezone": None,
        "data_complete": data_complete,
        "quality_status": quality_status,
        "row_count": len(rows),
        "sample_size": len(rows),
        "null_rate": None,
    },
    "execution_metadata": {
        "query_hash": provenance.get("query_hash", ""),
        "engine": engine_type,
        "executed_at": now,
    },
}
artifact_name = f"{metric_name}_observe_time_series"
summary = (
    f"observe {metric_name} time_series/{granularity} "
    f"[{start_str} → {end_str}]: {len(series_data)} buckets"
)
```

- [ ] **Step 5: Refactor segmented mode to unified format**

Replace the segmented observation dict construction (lines ~503-530). Build using helpers:

```python
axes = build_axes(None, dimensions)
series = build_segmented_series(rows, dimensions=dimensions)
observation = {
    "schema_version": "2.0",
    "observation_type": "segmented",
    "metric": metric_name,
    "time_scope": resolved_time_scope,
    "scope": scope_raw or {},
    "predicate_filter_lineage": predicate_filter_lineage_seg,
    "unit": None,
    "axes": axes,
    "series": series,
    "analytical_metadata": {
        "aggregation_semantics": aggregation_semantics,
        "timezone": None,
        "data_complete": None,
        "quality_status": quality_status,
        "row_count": len(rows),
        "sample_size": len(rows),
        "null_rate": None,
    },
    "execution_metadata": {
        "query_hash": provenance.get("query_hash", ""),
        "engine": engine_type,
        "executed_at": now,
    },
}
artifact_name = f"{metric_name}_observe_segmented"
summary = (
    f"observe {metric_name} segmented [{start_str} → {end_str}]: {len(series)} segments"
)
```

Note: Remove `scope_value` from segmented output (it was a v1.0 artifact). The aggregate value is available from the whole-scope scalar observation or from `analytical_metadata`.

- [ ] **Step 6: Add panel mode**

Add a new branch after the segmented elif (or restructure into a unified flow). When `granularity is not None and dimensions is not None`, execute panel query and build output:

```python
elif granularity is not None and dimensions is not None:
    # --- Panel mode ---
    time_col = resolved.resolved_time_axis.analysis_time_expr
    if not time_col:
        raise ValueError("panel observe requires resolved_time_axis.analysis_time_expr")
    bucket_expr = f"DATE_TRUNC('{granularity}', {time_col})"
    group_by_cols = [f"{bucket_expr} AS bucket_start"] + dimensions
    compiled_query = runtime.compile_step(
        AnalysisStepIR(
            index=0,
            step_type="aggregate_query",
            params={
                "table": qualified_table,
                "time_scope": mq_params["time_scope"],
                "measures": [{"expr": metric_sql, "as": "value"}],
                "group_by": group_by_cols,
                "order": "bucket_start",
                "scoped_query": scoped_query,
                "limit": _OBSERVE_ROW_LIMIT,
            },
        ),
        engine_type=engine_type,
        semantic_context={"metric_execution_context": execution_context},
    )
    _exec_result = execute_compiled(engine, compiled_query, session_id=session_id)
    rows = list(_exec_result.rows)
    _elapsed_ms = _exec_result.metadata.get("elapsed_ms")
    provenance = make_provenance(
        compiled_query.sql, compiled_query.params, engine_type=engine_type
    )
    predicate_filter_lineage_panel = extract_predicate_filter_lineage(compiled_query)

    axes = build_axes(granularity_typed, dimensions)
    series = build_panel_series(
        rows,
        dimensions=dimensions,
        start=start_str,
        end=end_str,
        granularity=granularity_typed,
        dense_series_builder=_build_dense_series,
    )
    total_points = sum(len(s["points"]) for s in series)
    data_coverage_summary = _build_data_coverage_summary(
        series=series[0]["points"] if series else []
    )
    data_complete = _time_series_data_complete(data_coverage_summary)
    quality_status = _time_series_quality_status(
        row_count=len(rows),
        data_complete=data_complete,
    )
    observation = {
        "schema_version": "2.0",
        "observation_type": "panel",
        "metric": metric_name,
        "time_scope": resolved_time_scope,
        "scope": scope_raw or {},
        "predicate_filter_lineage": predicate_filter_lineage_panel,
        "unit": None,
        "axes": axes,
        "series": series,
        "analytical_metadata": {
            "aggregation_semantics": aggregation_semantics,
            "timezone": None,
            "data_complete": data_complete,
            "quality_status": quality_status,
            "row_count": len(rows),
            "sample_size": total_points,
            "null_rate": None,
        },
        "execution_metadata": {
            "query_hash": provenance.get("query_hash", ""),
            "engine": engine_type,
            "executed_at": now,
        },
    }
    artifact_name = f"{metric_name}_observe_panel"
    summary = (
        f"observe {metric_name} panel/{granularity} by {','.join(dimensions)} "
        f"[{start_str} → {end_str}]: {len(series)} segments, {total_points} total points"
    )
```

- [ ] **Step 7: Restructure the if/elif chain**

The current code has `if granularity` → `elif dimensions` → `else (scalar)`. Refactor to handle all 4 modes in the correct order:

```python
if granularity is not None and dimensions is not None:
    # panel mode
elif granularity is not None:
    # time_series mode
elif dimensions is not None:
    # segmented mode
else:
    # scalar mode
```

- [ ] **Step 8: Remove dead v1.0 helper functions**

After the refactor, the following functions in `observe.py` are no longer needed as direct output construction code (but some may still be used internally by dense_series_builder):
- `_sort_segment_payloads` — replaced by `build_segmented_series` sorting
- Inline dict construction in each mode — replaced by helper calls

Keep `_build_dense_series`, `_series_from_rows`, `_expected_bucket_windows`, `_build_data_coverage_summary`, `_time_series_data_complete`, `_time_series_quality_status` — these are still needed for time_series and panel dense point generation.

- [ ] **Step 9: Update _runner_fixtures.py**

In `_runner_fixtures.py`, update `_scalar_observation()` and `_time_series_observation()` to produce v2.0 format artifacts:

```python
def _scalar_observation(metric: str = "m1", value: float = 42.0) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "observation_type": "scalar",
        "metric": metric,
        "time_scope": {"field": "event_date", "start": "2024-01-01T00:00:00Z", "end": "2024-01-08T00:00:00Z"},
        "scope": {},
        "predicate_filter_lineage": None,
        "unit": None,
        "axes": [],
        "series": [{"keys": {}, "points": [{"value": value}]}],
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "timezone": None,
            "data_complete": None,
            "quality_status": "ready",
            "row_count": 10,
            "sample_size": 10,
            "null_rate": None,
        },
    }


def _time_series_observation(
    metric: str = "m1",
    granularity: str = "day",
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if series is None:
        series = [
            {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 10.0},
            {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 20.0},
        ]
    return {
        "schema_version": "2.0",
        "observation_type": "time_series",
        "metric": metric,
        "time_scope": {"field": "event_date", "start": "2024-01-01T00:00:00Z", "end": "2024-01-08T00:00:00Z"},
        "scope": {},
        "predicate_filter_lineage": None,
        "unit": None,
        "axes": [{"kind": "time", "grain": granularity}],
        "series": [{"keys": {}, "points": series}],
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "timezone": None,
            "data_complete": True,
            "quality_status": "ready",
            "row_count": 7,
            "sample_size": 7,
            "null_rate": None,
        },
    }
```

- [ ] **Step 10: Update test_observe_runner.py assertions**

Update all assertions in `test_observe_runner.py` to match v2.0 format. Key changes:

- `result["value"]` → `result["series"][0]["points"][0]["value"]` (scalar)
- `result["granularity"]` → `result["axes"][0]["grain"]` (time_series)
- `result["series"]` (flat list) → `result["series"][0]["points"]` (time_series)
- `result["dimensions"]` → from `result["axes"]` dimension descriptors
- `result["segments"]` → `result["series"]` (segmented)
- Remove `assertNotIn("granularity", result)` for scalar — now `axes` is always present (but empty for scalar)
- Add assertions for `axes` structure in each mode
- Add new test for panel mode

Example panel test:

```python
def test_panel_observe_produces_axes_and_series(self) -> None:
    runtime, result = self._run_observe(
        {
            "metric": "metric.m1",
            "time_scope": {
                "field": "event_date",
                "start": "2024-01-01",
                "end": "2024-01-08",
            },
            "granularity": "day",
            "dimensions": ["region"],
        },
        rows=[
            {"bucket_start": "2024-01-01", "region": "US", "value": "120"},
            {"bucket_start": "2024-01-02", "region": "US", "value": "135"},
            {"bucket_start": "2024-01-01", "region": "EU", "value": "95"},
            {"bucket_start": "2024-01-02", "region": "EU", "value": "110"},
        ],
        dimensions=["region"],
    )
    self.assertEqual(result["observation_type"], "panel")
    self.assertEqual(result["axes"], [
        {"kind": "time", "grain": "day"},
        {"kind": "dimension", "name": "region"},
    ])
    self.assertEqual(len(result["series"]), 2)
    # Each series has keys and points
    us_series = next(s for s in result["series"] if s["keys"]["region"] == "US")
    eu_series = next(s for s in result["series"] if s["keys"]["region"] == "EU")
    self.assertEqual(len(us_series["points"]), 7)  # dense, 7 days
    self.assertEqual(us_series["points"][0]["value"], 120.0)
    self.assertEqual(eu_series["points"][0]["value"], 95.0)
```

- [ ] **Step 11: Run observe tests**

Run: `.venv/bin/pytest tests/runtime/intents/test_observe_runner.py -v`
Expected: PASS (all existing tests adapted + new panel test passes)

- [ ] **Step 12: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 13: Commit**

```
git add marivo/runtime/intents/observe.py tests/runtime/intents/test_observe_runner.py tests/runtime/intents/_runner_fixtures.py
git commit -m "feat(observe): refactor to unified axes+series format with panel mode"
```

---

### Task 4: Adapt Compare Intent to v2.0 Format

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `tests/runtime/intents/test_compare_runner.py`

- [ ] **Step 1: Import metric_frame helpers in compare.py**

Add:

```python
from marivo.runtime.intents.metric_frame import (
    has_dimension_axis,
    has_time_axis,
    dimension_names_from_axes,
    read_axes_from_artifact,
    time_grain_from_axes,
)
```

- [ ] **Step 2: Replace observation_type-based dispatch with axes-based dispatch**

In `compare.py`, the current dispatch (line ~365) reads `observation_type` from each artifact. Replace with axes-based detection:

```python
# Instead of:
# if left_type == "scalar":
# elif left_type == "time_series":
# elif left_type == "segmented":

# Use:
left_axes = read_axes_from_artifact(left_artifact)
right_axes = read_axes_from_artifact(right_artifact)
left_has_time = has_time_axis(left_axes)
left_has_dim = has_dimension_axis(left_axes)
```

Then dispatch based on axes structure:
- No time, no dim → scalar delta
- Time, no dim → time_series delta
- No time, dim → segmented delta

- [ ] **Step 3: Adapt scalar delta read paths**

For scalar, read `left_artifact["series"][0]["points"][0]["value"]` instead of `left_artifact["value"]`:

```python
left_value = left_artifact["series"][0]["points"][0]["value"]
right_value = right_artifact["series"][0]["points"][0]["value"]
```

- [ ] **Step 4: Adapt time_series delta read paths**

For time_series, read `left_artifact["series"][0]["points"]` instead of `left_artifact["series"]`. Each point has `{window, value}` — the structure is the same, just nested one level deeper:

```python
left_series = left_artifact["series"][0]["points"]
right_series = right_artifact["series"][0]["points"]
```

The existing `_series_row_key`, `_series_map_by_start`, `_normalize_window` helpers still work since `points[i]` has the same `{window, value}` shape as the old `series[i]`.

- [ ] **Step 5: Adapt segmented delta read paths**

For segmented, read `left_artifact["series"]` instead of `left_artifact["segments"]`. Each series entry has `{keys, points}`. Read dimension names from `axes` instead of `dimensions` field:

```python
left_dim_names = dimension_names_from_axes(left_axes)
left_series = left_artifact["series"]
# Build keys→value map from series
left_by_keys = {tuple(sorted(s["keys"].items())): s["points"][0]["value"] for s in left_series}
```

- [ ] **Step 6: Refactor delta output to unified axes+series format**

Replace the current delta output dict construction. For each mode, produce:

**Scalar delta:**
```python
axes = []
series = [{"keys": {}, "points": [{
    "current_value": current_value,
    "baseline_value": baseline_value,
    "delta": absolute_delta,
    "delta_pct": relative_delta,
}]}]
```

**Time_series delta:**
```python
axes = [{"kind": "time", "grain": granularity}]
series = [{"keys": {}, "points": delta_rows}]  # each delta_row has window + current/baseline/delta/delta_pct
```

**Segmented delta:**
```python
axes = [{"kind": "dimension", "name": dim_name}]
series = delta_rows  # each entry has keys + points with current/baseline/delta/delta_pct
```

All delta outputs share: `schema_version: "2.0"`, `axes`, `series`, `analytical_metadata`, `execution_metadata`.

- [ ] **Step 7: Update compare runner tests**

In `test_compare_runner.py`, update fixture artifacts to v2.0 format. Update assertions:
- `result["current_value"]` → `result["series"][0]["points"][0]["current_value"]` (scalar)
- `result["rows"]` → `result["series"][0]["points"]` (time_series)
- `result["dimensions"]` → from `result["axes"]`
- `result["comparison_type"]` stays (still useful as discriminator)
- `result["summary_current_value"]` stays at top level (summary metadata, not series-internal)

- [ ] **Step 8: Run compare tests**

Run: `.venv/bin/pytest tests/runtime/intents/test_compare_runner.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```
git add marivo/runtime/intents/compare.py tests/runtime/intents/test_compare_runner.py
git commit -m "feat(compare): adapt to v2.0 axes+series format for input and output"
```

---

### Task 5: Adapt Decompose Intent to v2.0 Format

**Files:**
- Modify: `marivo/runtime/intents/decompose.py`
- Modify: `tests/runtime/intents/test_decompose_runner.py`

- [ ] **Step 1: Import metric_frame helpers**

```python
from marivo.runtime.intents.metric_frame import (
    read_axes_from_artifact,
    dimension_names_from_axes,
    has_time_axis,
)
```

- [ ] **Step 2: Adapt compare artifact reading**

In `_normalize_decompose_compare_input()`, update field paths to read from v2.0 format:

- `compare_artifact["series"][0]["points"][0]["current_value"]` instead of `compare_artifact["current_value"]` for scalar delta
- `compare_artifact["series"][0]["points"][0]["baseline_value"]` instead of `compare_artifact["baseline_value"]`
- `compare_artifact["axes"]` instead of inferring from `comparison_type`
- For time_series summary values: read from `compare_artifact["series"][0]["points"]` aggregation

- [ ] **Step 3: Refactor attribution output to unified format**

Replace the current `rows`-based attribution output with axes+series format:

```python
axes = [{"kind": "dimension", "name": dimension}]
series = attribution_rows  # each: {keys: {dim: val}, points: [{value, contribution, share}]}
```

The output dict structure becomes:

```python
observation = {
    "schema_version": "2.0",
    "decomposition_type": "delta_decomposition",
    "metric": metric_name,
    "compare_ref": ...,
    "current_ref": ...,
    "baseline_ref": ...,
    "axes": axes,
    "series": series,
    "analytical_metadata": { ... },
    "execution_metadata": { ... },
}
```

Keep `scope_current_value`, `scope_baseline_value`, `scope_absolute_delta`, `scope_relative_delta`, `scope_direction` at top level as summary metadata (not inside series points).

- [ ] **Step 4: Update decompose runner test assertions**

Adapt test fixtures to construct v2.0 compare artifacts. Adapt assertions to read from v2.0 decompose output.

- [ ] **Step 5: Run decompose tests**

Run: `.venv/bin/pytest tests/runtime/intents/test_decompose_runner.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add marivo/runtime/intents/decompose.py tests/runtime/intents/test_decompose_runner.py
git commit -m "feat(decompose): adapt to v2.0 axes+series format"
```

---

### Task 6: Adapt Detect, Forecast, Correlate Intents

**Files:**
- Modify: `marivo/runtime/intents/detect.py`
- Modify: `marivo/runtime/intents/forecast.py`
- Modify: `marivo/runtime/intents/correlate.py`
- Modify: `tests/runtime/intents/test_detect_runner.py`
- Modify: `tests/runtime/intents/test_forecast_runner.py`
- Modify: `tests/runtime/intents/test_correlate_runner.py`

- [ ] **Step 1: Adapt detect.py**

Detect does NOT consume prior observation artifacts — it builds its own queries. However, its internal time-series construction uses `{window, value}` dicts which match the v2.0 point format already. No structural change needed to detect's output, but verify that `series` entries in detect results are compatible.

- [ ] **Step 2: Adapt forecast.py**

Forecast reads a time_series observation artifact. Update field paths:

```python
# Old:
source_series = source_artifact["series"]
# New:
source_series = source_artifact["series"][0]["points"]
```

Each point still has `{window, value}` — same structure, just nested deeper. The forecast output stays as-is (it's a sibling frame family, not a metric_frame variant).

- [ ] **Step 3: Adapt correlate.py**

Correlate reads two time_series observation artifacts. Update field paths:

```python
# Old:
left_series = left_artifact["series"]
right_series = right_artifact["series"]
# New:
left_series = left_artifact["series"][0]["points"]
right_series = right_artifact["series"][0]["points"]
```

Each point still has `{window, value}` — alignment logic unchanged.

- [ ] **Step 4: Update runner test fixtures**

In `test_forecast_runner.py` and `test_correlate_runner.py`, update mock artifact construction to v2.0 format (axes+series structure). In `test_detect_runner.py`, verify detect output still works (detect doesn't read prior artifacts, so minimal change).

- [ ] **Step 5: Run all three test files**

Run: `.venv/bin/pytest tests/runtime/intents/test_detect_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_correlate_runner.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```
git add marivo/runtime/intents/detect.py marivo/runtime/intents/forecast.py marivo/runtime/intents/correlate.py tests/runtime/intents/test_detect_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_correlate_runner.py
git commit -m "feat: adapt detect, forecast, correlate to v2.0 axes+series format"
```

---

### Task 7: Adapt Attribute and Diagnose Derived Intents

**Files:**
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `marivo/runtime/intents/diagnose.py`
- Modify: `tests/runtime/intents/test_attribute_runner.py`
- Modify: `tests/runtime/intents/test_diagnose_runner.py`

- [ ] **Step 1: Adapt attribute.py**

Attribute orchestrates sub-intents (observe, compare, decompose). Since each sub-intent now produces v2.0 format, attribute's reading of sub-intent results must adapt:

- `observe_result["observation_type"]` stays (still `"scalar"` for attribute's internal observe calls)
- `observe_result["series"][0]["points"][0]["value"]` instead of `observe_result["value"]` for reading scalar observe output
- `compare_result["series"][0]["points"][0]["current_value"]` instead of `compare_result["current_value"]`
- `decompose_result["series"]` instead of `decompose_result["rows"]` for contribution data

- [ ] **Step 2: Adapt diagnose.py**

Diagnose orchestrates detect + compare + decompose. Since detect doesn't consume prior artifacts, and compare/decompose now read v2.0 format, diagnose's internal calls need the same adaptations as attribute.

- [ ] **Step 3: Update runner tests**

Update `test_attribute_runner.py` and `test_diagnose_runner.py` to work with v2.0 format sub-intent results.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add marivo/runtime/intents/attribute.py marivo/runtime/intents/diagnose.py tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py
git commit -m "feat: adapt attribute and diagnose derived intents to v2.0 format"
```

---

### Task 8: Update MCP Tool Layer

**Files:**
- Modify: `marivo/transports/mcp/tools/intents.py`

- [ ] **Step 1: Simplify to_aoi_observe_request**

Replace the 3-branch dispatch in `to_aoi_observe_request` (lines 79-99) with a single return:

```python
def to_aoi_observe_request(
    metric: str,
    time_scope: McpTimeScope,
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"] | None = None,
    dimensions: list[str] | None = None,
    filter_expression: McpExpression | dict[str, Any] | None = None,
) -> aoi.Observe:
    payload = _omit_none(
        {
            "metric": metric,
            "time_scope": time_scope.model_dump(),
            "filter": _dump_expression(filter_expression),
            "granularity": granularity,
            "dimensions": dimensions,
        }
    )
    return aoi.Observe.model_validate(payload)
```

- [ ] **Step 2: Update register_observe MCP tool parameter descriptions**

In the `register_observe` function (lines ~311-374), update the `granularity` and `dimensions` parameter descriptions to mention all 4 modes including panel. Remove any restriction that says "granularity and dimensions cannot both be set."

The `dimensions` field description should be:
```
"Segmented observe selector. Provide a non-empty dimension list without granularity for segmented mode; provide with granularity for panel mode. Omit both dimensions and granularity for scalar observe."
```

The `granularity` field description should be:
```
"Time-series observe selector. Provide this without dimensions for time_series mode; provide with dimensions for panel mode. Omit both granularity and dimensions for scalar observe."
```

- [ ] **Step 3: Remove Observe1/Observe2/Observe3 references**

Search for any remaining references to `aoi.Observe1`, `aoi.Observe2`, `aoi.Observe3` in `intents.py` and replace with `aoi.Observe`. Also update the `AoiRequest` type union and `TimeSeriesObserveArtifactId` / `CompareObserveArtifactId` annotated types if they reference old types.

- [ ] **Step 4: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```
git add marivo/transports/mcp/tools/intents.py
git commit -m "feat(mcp): simplify observe tool to support all 4 modes including panel"
```

---

### Task 9: Update Integration Tests and Docs

**Files:**
- Modify: `tests/integration/test_observe_compare_lineage_reuse.py`
- Modify: `docs/specs/analysis/intents/atomic/observe.md`

- [ ] **Step 1: Update integration test helpers**

In `test_observe_compare_lineage_reuse.py`, update `_insert_observe_artifact()` to construct v2.0 format artifacts. The `observation_type` field stays, but `value` → `series[0]["points"][0]["value"]`, `series` → `series[0]["points"]`, `segments` → `series`, etc.

- [ ] **Step 2: Update observe.md spec doc**

Update `docs/specs/analysis/intents/atomic/observe.md` to reflect:
- 4 observation types: scalar, time_series, segmented, panel
- Panel mode allows granularity + dimensions simultaneously
- All artifacts use schema_version "2.0" with axes+series format
- Remove the statement about granularity/dimensions mutual exclusivity

- [ ] **Step 3: Run integration tests**

Run: `.venv/bin/pytest tests/integration/test_observe_compare_lineage_reuse.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```
git add tests/integration/test_observe_compare_lineage_reuse.py docs/specs/analysis/intents/atomic/observe.md
git commit -m "feat: update integration tests and observe spec for v2.0 format"
```

---

### Task 10: Full Test Suite Validation

**Files:**
- All test files

- [ ] **Step 1: Run full test suite**

Run: `make test`
Expected: PASS — all unit tests, runner tests, integration tests pass

- [ ] **Step 2: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 3: Run lint**

Run: `make lint`
Expected: PASS (no new lint issues)

- [ ] **Step 4: Final commit if any fixes needed**

If any fixes were needed during the full validation run, commit them:

```
git add -A  # only affected files
git commit -m "fix: address remaining test/typecheck/lint issues for v2.0 migration"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- Panel mode (granularity + dimensions) → Task 3 Step 6
- Unified axes+series format → Task 3 Steps 3-5
- AOI schema merge → Task 1
- Downstream adaptation (compare) → Task 4
- Downstream adaptation (decompose) → Task 5
- Downstream adaptation (detect/forecast/correlate) → Task 6
- Derived intent adaptation → Task 7
- MCP tool update → Task 8
- Integration tests → Task 9
- Full validation → Task 10

**2. Placeholder scan:** No TBD/TODO/fill-in-later found. All steps contain code or exact commands.

**3. Type consistency:**
- `build_axes()` returns `list[dict[str, str]]` — used consistently in observe, compare, decompose
- `build_scalar_series(value)` returns series with `{keys: {}, points: [{value}]}` — matches read path `artifact["series"][0]["points"][0]["value"]`
- `determine_observation_type()` returns `"scalar"|"time_series"|"segmented"|"panel"` — matches v2.0 observation_type values
- `aoi.Observe` is the single contract type — referenced consistently in MCP tool and runtime
- `TimeGrain` type used consistently for granularity — `build_axes` takes `TimeGrain | None`
