# Decompose Delta Frame Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `decompose` so it consumes all `delta_frame` shapes through the transitional `compare_artifact_id` field and emits `attribution_frame(ranked_contributions)` instead of legacy `delta_decomposition`.

**Architecture:** Keep the current request field name but treat it as a delta-frame source id. Add frame-family helpers, make compare emit `delta_frame` metadata, make decompose validate `artifact_family=delta_frame` plus `decomposable`, normalize scalar/time-series/segmented/panel deltas into one attribution path, and update downstream readers/projections/docs to consume `attribution_frame`.

**Tech Stack:** Python 3.12, Pydantic generated AOI contracts, JSON Schema, pytest through `make test`, Marivo local runtime intent runners, MCP/HTTP transport DTOs.

---

## Source Spec

Implementation follows [2026-05-22-decompose-delta-frame-attribution-design.md](/Users/lichengxiang/source/oss/marivo/docs/superpowers/specs/2026-05-22-decompose-delta-frame-attribution-design.md).

Do not migrate old artifacts. Do not add `source: artifact_input`. Do not keep `DeltaDecompositionResult` as the public decompose output.

## File Structure

Modify these files:

- `marivo/runtime/intents/metric_frame.py`: shared frame helpers for `delta_frame` and `attribution_frame`, plus row readers for downstream derived intents.
- `marivo/runtime/intents/compare.py`: emit `artifact_family=delta_frame`, `shape`, `measures`, `capabilities`, and `payload.series` while preserving temporary internal aliases where current code still reads them.
- `marivo/runtime/intents/decompose.py`: validate source delta frame, normalize all delta shapes, compute or fast-path contributions, and commit `attribution_frame`.
- `marivo/runtime/intents/attribute.py`: read driver rows from attribution-frame helpers and label decompose artifacts as attribution outputs.
- `marivo/runtime/intents/diagnose.py`: read driver rows from attribution-frame helpers and preserve diagnosis bundle behavior with attribution artifacts.
- `marivo/runtime/evidence/decompose_extractor.py`: extract findings from `attribution_frame(ranked_contributions)` payloads.
- `marivo/runtime/evidence/finding_extractor_registry.py`: register the decompose extractor for the new artifact family.
- `marivo/contracts/aoi_projection.py`: project decompose artifacts as attribution-frame artifacts, not `DeltaDecompositionResult`.
- `marivo/transports/http/models/intent_response_models.py`: type `DecomposeResponse` with the new generated attribution frame model.
- `marivo/transports/mcp/tools/intents.py` and `marivo/transports/mcp/tools/schemas.py`: update descriptions and schemas that expose decompose output.
- `aoi-spec/schema/aoi.schema.json`: replace the old decompose result shape with an attribution frame artifact contract.
- `aoi-spec/schema/aoi.schema.yaml`: readable schema version of the same contract.
- `aoi-spec/spec.md` and `aoi-spec/README.md`: update public AOI wording.
- `docs/specs/analysis/aoi-spec.schema.yaml`: align the design-side AOI schema.
- `docs/user/marivo-mcp-tools-reference.md`: update decompose and derived output examples.
- `docs/api/intent-steps.md`: update HTTP intent response examples if they mention `delta_decomposition`.
- `marivo/contracts/generated/aoi.py`: regenerated output only. Do not edit by hand.

Modify these tests:

- `tests/runtime/intents/test_metric_frame.py`
- `tests/runtime/intents/test_compare_runner.py`
- `tests/runtime/intents/test_decompose_runner.py`
- `tests/runtime/intents/test_attribute_runner.py`
- `tests/runtime/intents/test_diagnose_runner.py`
- `tests/runtime/evidence/test_compare_decompose_extractor.py`
- `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`
- `tests/runtime/test_aoi_lowering.py`
- `tests/runtime/test_aoi_intent_execution.py`
- `tests/contracts/test_generated_models.py`
- `tests/transports/http/test_http_aoi_intents.py`
- `tests/transports/mcp/test_tool_parity.py`
- `tests/transports/mcp/test_mcp_aoi_adapter.py`

Create no new top-level modules unless a task below explicitly says to create one.

## Task 1: Add Delta And Attribution Frame Helpers

**Files:**
- Modify: `marivo/runtime/intents/metric_frame.py`
- Test: `tests/runtime/intents/test_metric_frame.py`

- [ ] **Step 1: Write failing helper tests**

Add these tests to `tests/runtime/intents/test_metric_frame.py`:

```python
import pytest

from marivo.runtime.intents.metric_frame import (
    build_attribution_frame_artifact,
    build_delta_frame_artifact,
    is_attribution_frame_artifact,
    is_delta_frame_artifact,
    read_attribution_rows_from_series,
    read_delta_frame_shape,
)


def test_build_delta_frame_artifact_sets_family_shape_payload_and_capabilities() -> None:
    artifact = build_delta_frame_artifact(
        artifact_id="art_cmp",
        shape="scalar_delta",
        metric_ref="metric.revenue",
        subject={
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}},
            "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}},
        },
        axes=[],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.2,
                        "direction": "increase",
                    }
                ],
            }
        ],
        unit="usd",
        lineage={"current_source_ref": {"artifact_id": "art_current"}},
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
    )

    assert is_delta_frame_artifact(artifact)
    assert artifact["artifact_family"] == "delta_frame"
    assert artifact["shape"] == "scalar_delta"
    assert "decomposable" in artifact["capabilities"]
    assert artifact["payload"]["series"][0]["points"][0]["delta_abs"] == 20.0
    assert artifact["payload"]["scope"]["delta_abs"] == 20.0
    assert read_delta_frame_shape(artifact) == "scalar_delta"


def test_build_attribution_frame_artifact_reads_flat_rows_from_payload_series() -> None:
    artifact = build_attribution_frame_artifact(
        artifact_id="art_attr",
        metric_ref="metric.revenue",
        dimension="channel",
        subject={
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}},
            "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}},
        },
        series=[
            {
                "keys": {"channel": "paid"},
                "points": [
                    {
                        "contribution_abs": 12.0,
                        "contribution_pct": 0.6,
                        "current_value": 70.0,
                        "baseline_value": 58.0,
                        "presence": "both",
                        "rank": 1,
                    }
                ],
            }
        ],
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
        quality={
            "reconciliation_status": "within_tolerance",
            "unexplained_delta_abs": 0.0,
            "unexplained_pct": 0.0,
        },
        lineage={"operation": "decompose", "source_artifact_ids": ["art_cmp"]},
    )

    assert is_attribution_frame_artifact(artifact)
    assert artifact["artifact_family"] == "attribution_frame"
    assert artifact["shape"] == "ranked_contributions"
    assert artifact["measures"] == [
        {"id": "contribution_abs", "value_type": "number", "nullable": False},
        {"id": "contribution_pct", "value_type": "number", "nullable": True},
    ]
    assert read_attribution_rows_from_series(artifact) == [
        {
            "key": "paid",
            "channel": "paid",
            "contribution_abs": 12.0,
            "contribution_pct": 0.6,
            "current_value": 70.0,
            "baseline_value": 58.0,
            "presence": "both",
            "rank": 1,
        }
    ]


def test_read_delta_frame_shape_rejects_non_delta_artifact() -> None:
    with pytest.raises(ValueError, match="delta_frame artifact"):
        read_delta_frame_shape({"artifact_family": "metric_frame", "shape": "scalar"})
```

- [ ] **Step 2: Run helper tests and verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_metric_frame.py -k "delta_frame or attribution_frame"'
```

Expected: FAIL because `build_delta_frame_artifact`, `build_attribution_frame_artifact`, and related readers do not exist.

- [ ] **Step 3: Implement frame helpers**

Add this code to `marivo/runtime/intents/metric_frame.py` after `build_metric_frame_artifact`:

```python
DeltaFrameShape = str


def build_delta_frame_artifact(
    *,
    artifact_id: str,
    shape: DeltaFrameShape,
    metric_ref: str,
    subject: dict[str, Any],
    axes: list[dict[str, str]],
    series: list[dict[str, Any]],
    unit: str | None,
    lineage: dict[str, Any],
    scope: dict[str, Any],
    capabilities: list[str] | None = None,
    analytical_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_capabilities = capabilities or ["sliceable", "filterable", "decomposable"]
    if shape == "scalar_delta":
        resolved_capabilities = ["filterable", "decomposable"]
    return {
        "artifact_id": artifact_id,
        "artifact_family": "delta_frame",
        "shape": shape,
        "subject": subject,
        "axes": axes,
        "measures": [
            {
                "id": "delta_abs",
                "value_type": "number",
                "nullable": True,
                "unit": unit,
            },
            {
                "id": "delta_pct",
                "value_type": "number",
                "nullable": True,
            },
        ],
        "capabilities": resolved_capabilities,
        "lineage": lineage,
        "payload": {"series": series, "scope": scope},
        **({"analytical_metadata": analytical_metadata} if analytical_metadata else {}),
        "metric_ref": metric_ref,
    }


def build_attribution_frame_artifact(
    *,
    artifact_id: str,
    metric_ref: str,
    dimension: str,
    subject: dict[str, Any],
    series: list[dict[str, Any]],
    scope: dict[str, Any],
    quality: dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "attribution_frame",
        "shape": "ranked_contributions",
        "subject": subject,
        "axes": [{"kind": "dimension", "name": dimension}],
        "measures": [
            {"id": "contribution_abs", "value_type": "number", "nullable": False},
            {"id": "contribution_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": lineage,
        "payload": {"series": series, "scope": scope, "quality": quality},
        "metric_ref": metric_ref,
    }
```

Add this code near existing artifact readers in `marivo/runtime/intents/metric_frame.py`:

```python
def is_delta_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "delta_frame"


def is_attribution_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "attribution_frame"


def read_delta_frame_shape(artifact: dict[str, Any]) -> str:
    if not is_delta_frame_artifact(artifact):
        raise ValueError("delta_frame artifact expected")
    shape = artifact.get("shape")
    if not isinstance(shape, str) or not shape:
        raise ValueError("delta_frame artifact missing shape")
    return shape


def read_frame_payload_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact.get("payload")
    if isinstance(payload, dict):
        series = payload.get("series")
        if isinstance(series, list):
            return series
    series = artifact.get("series")
    if isinstance(series, list):
        return series
    raise ValueError("frame artifact payload missing series")


def read_frame_payload_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    payload = artifact.get("payload")
    if isinstance(payload, dict):
        scope = payload.get("scope")
        if isinstance(scope, dict):
            return scope
    return {
        "current_value": artifact.get("summary_current_value") or artifact.get("scope_current_value"),
        "baseline_value": artifact.get("summary_baseline_value") or artifact.get("scope_baseline_value"),
        "delta_abs": artifact.get("summary_absolute_delta") or artifact.get("scope_absolute_delta"),
        "delta_pct": artifact.get("summary_relative_delta") or artifact.get("scope_relative_delta"),
        "direction": artifact.get("summary_direction") or artifact.get("scope_direction") or "undefined",
    }


def read_attribution_rows_from_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    if not is_attribution_frame_artifact(artifact):
        raise ValueError("attribution_frame artifact expected")
    axes = read_axes_from_artifact(artifact)
    dim_names = dimension_names_from_axes(axes)
    rows: list[dict[str, Any]] = []
    for entry in read_frame_payload_series(artifact):
        keys = entry.get("keys") or {}
        points = entry.get("points") or []
        for point in points:
            row: dict[str, Any] = {}
            if dim_names:
                for dim_name in dim_names:
                    dim_value = keys.get(dim_name)
                    if dim_value is not None and row.get("key") is None:
                        row["key"] = dim_value
            row.update(keys)
            row.update(point)
            rows.append(row)
    return rows
```

- [ ] **Step 4: Keep the legacy helper as a thin wrapper**

Replace `read_decompose_rows_from_series` in `marivo/runtime/intents/metric_frame.py` with:

```python
def read_decompose_rows_from_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility wrapper for existing derived-intent call sites.

    New code should call ``read_attribution_rows_from_series`` directly.
    """
    if is_attribution_frame_artifact(artifact):
        rows = read_attribution_rows_from_series(artifact)
        for row in rows:
            if "absolute_contribution" not in row and "contribution_abs" in row:
                row["absolute_contribution"] = row["contribution_abs"]
            if "contribution_share" not in row and "contribution_pct" in row:
                row["contribution_share"] = row["contribution_pct"]
        return rows
    series_list = artifact.get("series") or []
    if series_list:
        axes = read_axes_from_artifact(artifact)
        dim_names = dimension_names_from_axes(axes)
        rows: list[dict[str, Any]] = []
        for entry in series_list:
            keys = entry.get("keys") or {}
            points = entry.get("points") or []
            for point in points:
                row: dict[str, Any] = {}
                if dim_names:
                    for dim_name in dim_names:
                        dim_value = keys.get(dim_name)
                        if dim_value is not None and row.get("key") is None:
                            row["key"] = dim_value
                row.update(keys)
                row.update(point)
                rows.append(row)
        return rows
    return artifact.get("rows") or []
```

- [ ] **Step 5: Run helper tests and verify they pass**

Run:

```bash
make test TESTS='tests/runtime/intents/test_metric_frame.py -k "delta_frame or attribution_frame"'
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add marivo/runtime/intents/metric_frame.py tests/runtime/intents/test_metric_frame.py
git commit -m "feat: add delta and attribution frame helpers" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 2: Make Compare Emit Delta Frame Metadata

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Test: `tests/runtime/intents/test_compare_runner.py`

- [ ] **Step 1: Write failing compare metadata tests**

Add these assertions to existing scalar, time-series, and segmented compare tests in `tests/runtime/intents/test_compare_runner.py`.

For the scalar compare test, add:

```python
assert result["artifact_family"] == "delta_frame"
assert result["shape"] == "scalar_delta"
assert "decomposable" in result["capabilities"]
assert result["payload"]["scope"]["delta_abs"] == result["summary_absolute_delta"]
assert result["payload"]["series"][0]["points"][0]["delta_abs"] == result["absolute_delta"]
```

For the time-series compare test, add:

```python
assert result["artifact_family"] == "delta_frame"
assert result["shape"] == "time_series_delta"
assert "decomposable" in result["capabilities"]
assert result["payload"]["scope"]["delta_abs"] == result["summary_absolute_delta"]
assert result["payload"]["series"][0]["points"][0]["delta_abs"] == result["series"][0]["points"][0]["delta"]
```

For the segmented compare test, add:

```python
assert result["artifact_family"] == "delta_frame"
assert result["shape"] == "segmented_delta"
assert "decomposable" in result["capabilities"]
assert result["payload"]["scope"]["delta_abs"] == result["scope_absolute_delta"]
assert result["payload"]["series"][0]["points"][0]["delta_abs"] == result["series"][0]["points"][0]["delta"]
```

- [ ] **Step 2: Run compare tests and verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_compare_runner.py -k "scalar or time_series or segmented"'
```

Expected: FAIL because compare artifacts do not have `artifact_family`, `shape`, `capabilities`, or `payload`.

- [ ] **Step 3: Add compare artifact-family fields**

In `marivo/runtime/intents/compare.py`, import the helper:

```python
from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    dimension_names_from_axes,
    has_dimension_axis,
    has_time_axis,
    is_metric_frame_artifact,
    read_axes_from_artifact,
    read_metric_frame_metric_ref,
    read_metric_frame_scope,
    read_metric_frame_series,
    read_metric_frame_shape,
    read_metric_frame_time_scope,
    read_metric_frame_unit,
    time_grain_from_axes,
)
```

Add this helper near `_compute_direction`:

```python
def _comparison_subject(
    *,
    metric_ref: str,
    current_time_scope: dict[str, Any],
    baseline_time_scope: dict[str, Any],
    current_scope: dict[str, Any],
    baseline_scope: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "comparison",
        "metric_ref": metric_ref,
        "current": {"time_scope": current_time_scope, "scope": current_scope},
        "baseline": {"time_scope": baseline_time_scope, "scope": baseline_scope},
    }
```

- [ ] **Step 4: Wrap scalar compare artifact with delta frame metadata**

In the scalar branch of `run_compare_intent`, after `scalar_series` is built, create `delta_series`:

```python
delta_series = [
    {
        "keys": {},
        "points": [
            {
                "current_value": current_value,
                "baseline_value": baseline_value,
                "delta_abs": abs_delta,
                "delta_pct": rel_delta,
                "direction": direction,
            }
        ],
    }
]
```

Replace the scalar `artifact = { ... }` assignment with:

```python
artifact = {
    **build_delta_frame_artifact(
        artifact_id="",
        shape="scalar_delta",
        metric_ref=metric_ref,
        subject=_comparison_subject(
            metric_ref=metric_ref,
            current_time_scope=_read_time_scope(left_artifact),
            baseline_time_scope=_read_time_scope(right_artifact),
            current_scope=_read_scope(left_artifact),
            baseline_scope=_read_scope(right_artifact),
        ),
        axes=[],
        series=delta_series,
        unit=left_unit,
        lineage=lineage,
        scope={
            "current_value": current_value,
            "baseline_value": baseline_value,
            "delta_abs": abs_delta,
            "delta_pct": rel_delta,
            "direction": direction,
        },
        capabilities=["filterable", "decomposable"],
        analytical_metadata=analytical_metadata,
    ),
    **base,
    "comparison_type": "scalar_delta",
    "axes": [],
    "series": scalar_series,
    "current_value": current_value,
    "baseline_value": baseline_value,
    "absolute_delta": abs_delta,
    "relative_delta": rel_delta,
    "direction": direction,
    "summary_current_value": current_value,
    "summary_baseline_value": baseline_value,
    "summary_absolute_delta": abs_delta,
    "summary_relative_delta": rel_delta,
    "summary_direction": direction,
}
artifact["payload"]["series"] = delta_series
```

Keep the top-level `series` alias for current internal readers. The canonical payload uses `delta_abs` and `delta_pct`.

- [ ] **Step 5: Wrap time-series compare artifact with delta frame metadata**

In the time-series branch, after `time_series_rows` and summaries are computed, add:

```python
delta_series = [
    {
        "keys": {},
        "points": [
            {
                **row,
                "delta_abs": row.get("delta"),
                "delta_pct": row.get("delta_pct"),
            }
            for row in time_series_rows
        ],
    }
]
```

Build the artifact with:

```python
artifact = {
    **build_delta_frame_artifact(
        artifact_id="",
        shape="time_series_delta",
        metric_ref=metric_ref,
        subject=_comparison_subject(
            metric_ref=metric_ref,
            current_time_scope=matched_current_time_scope or left_time_scope,
            baseline_time_scope=matched_baseline_time_scope or right_time_scope,
            current_scope=_read_scope(left_artifact),
            baseline_scope=_read_scope(right_artifact),
        ),
        axes=[{"kind": "time", "grain": granularity}],
        series=delta_series,
        unit=left_unit,
        lineage=lineage,
        scope={
            "current_value": summary_current_value,
            "baseline_value": summary_baseline_value,
            "delta_abs": summary_abs,
            "delta_pct": summary_rel,
            "direction": summary_dir,
        },
        capabilities=["sliceable", "filterable", "decomposable"],
        analytical_metadata=analytical_metadata,
    ),
    **base,
    "comparison_type": "time_series_delta",
    "axes": [{"kind": "time", "grain": granularity}],
    "series": [{"keys": {}, "points": time_series_rows}],
    "coverage": coverage,
    "summary_current_value": summary_current_value,
    "summary_baseline_value": summary_baseline_value,
    "summary_absolute_delta": summary_abs,
    "summary_relative_delta": summary_rel,
    "summary_direction": summary_dir,
}
artifact["payload"]["series"] = delta_series
```

- [ ] **Step 6: Wrap segmented compare artifact with delta frame metadata**

In the segmented branch, after `segmented_series` and summaries are computed, add:

```python
delta_series = [
    {
        "keys": entry.get("keys") or {},
        "points": [
            {
                **point,
                "delta_abs": point.get("delta"),
                "delta_pct": point.get("delta_pct"),
            }
            for point in (entry.get("points") or [])
        ],
    }
    for entry in segmented_series
]
```

Build the artifact with:

```python
artifact = {
    **build_delta_frame_artifact(
        artifact_id="",
        shape="segmented_delta",
        metric_ref=metric_ref,
        subject=_comparison_subject(
            metric_ref=metric_ref,
            current_time_scope=_read_time_scope(left_artifact),
            baseline_time_scope=_read_time_scope(right_artifact),
            current_scope=_read_scope(left_artifact),
            baseline_scope=_read_scope(right_artifact),
        ),
        axes=seg_axes,
        series=delta_series,
        unit=left_unit,
        lineage=lineage,
        scope={
            "current_value": scope_lv,
            "baseline_value": scope_rv,
            "delta_abs": scope_abs,
            "delta_pct": scope_rel,
            "direction": scope_dir,
        },
        capabilities=["sliceable", "filterable", "decomposable"],
        analytical_metadata={**analytical_metadata, "series_complete": True},
    ),
    **base,
    "comparison_type": "segmented_delta",
    "axes": seg_axes,
    "series": segmented_series,
    "scope_current_value": scope_lv,
    "scope_baseline_value": scope_rv,
    "scope_absolute_delta": scope_abs,
    "scope_relative_delta": scope_rel,
    "scope_direction": scope_dir,
}
artifact["payload"]["series"] = delta_series
```

- [ ] **Step 7: Run compare tests and verify they pass**

Run:

```bash
make test TESTS='tests/runtime/intents/test_compare_runner.py'
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add marivo/runtime/intents/compare.py tests/runtime/intents/test_compare_runner.py
git commit -m "feat: emit compare delta frame artifacts" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 3: Update AOI Schema And Generated Contracts

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.json`
- Modify: `aoi-spec/schema/aoi.schema.yaml`
- Modify: `aoi-spec/spec.md`
- Modify: `aoi-spec/README.md`
- Modify: `docs/specs/analysis/aoi-spec.schema.yaml`
- Generated: `marivo/contracts/generated/aoi.py`
- Test: `tests/contracts/test_generated_models.py`

- [ ] **Step 1: Write failing generated-model tests**

Add this test to `tests/contracts/test_generated_models.py`:

```python
from marivo.contracts.generated import aoi


def test_aoi_generated_models_include_attribution_frame_artifact() -> None:
    artifact = aoi.AttributionFrameArtifact.model_validate(
        {
            "artifact_id": "art_attr",
            "artifact_family": "attribution_frame",
            "shape": "ranked_contributions",
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {"time_scope": {"field": "time", "start": "2024-01-08T00:00:00Z", "end": "2024-01-15T00:00:00Z"}, "scope": {}},
                "baseline": {"time_scope": {"field": "time", "start": "2024-01-01T00:00:00Z", "end": "2024-01-08T00:00:00Z"}, "scope": {}},
            },
            "axes": [{"kind": "dimension", "name": "channel"}],
            "measures": [
                {"id": "contribution_abs", "value_type": "number", "nullable": False},
                {"id": "contribution_pct", "value_type": "number", "nullable": True},
            ],
            "capabilities": ["filterable"],
            "lineage": {"operation": "decompose", "source_artifact_ids": ["art_cmp"]},
            "payload": {
                "series": [
                    {
                        "keys": {"channel": "paid"},
                        "points": [{"contribution_abs": 12.0, "contribution_pct": 0.6, "rank": 1}],
                    }
                ],
                "scope": {"delta_abs": 20.0},
                "quality": {"reconciliation_status": "within_tolerance"},
            },
        }
    )

    assert artifact.artifact_family == "attribution_frame"
    assert artifact.shape == "ranked_contributions"
```

- [ ] **Step 2: Run generated-model test and verify it fails**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py -k attribution_frame'
```

Expected: FAIL because `aoi.AttributionFrameArtifact` does not exist.

- [ ] **Step 3: Update JSON schema**

In `aoi-spec/schema/aoi.schema.json`, add definitions under `$defs.artifacts` for:

```json
"AttributionFrameMeasure": {
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "value_type", "nullable"],
  "properties": {
    "id": {"enum": ["contribution_abs", "contribution_pct"]},
    "value_type": {"const": "number"},
    "nullable": {"type": "boolean"}
  }
},
"AttributionPoint": {
  "type": "object",
  "additionalProperties": false,
  "required": ["contribution_abs", "contribution_pct", "rank"],
  "properties": {
    "contribution_abs": {"type": "number"},
    "contribution_pct": {"anyOf": [{"type": "number"}, {"type": "null"}]},
    "current_value": {"$ref": "#/$defs/primitives/NumberOrNull"},
    "baseline_value": {"$ref": "#/$defs/primitives/NumberOrNull"},
    "presence": {"enum": ["both", "current_only", "baseline_only"]},
    "rank": {"type": "integer", "minimum": 1}
  }
},
"AttributionSeriesEntry": {
  "type": "object",
  "additionalProperties": false,
  "required": ["keys", "points"],
  "properties": {
    "keys": {"$ref": "#/$defs/primitives/DimensionKeyMap"},
    "points": {
      "type": "array",
      "items": {"$ref": "#/$defs/artifacts/AttributionPoint"}
    }
  }
},
"AttributionFramePayload": {
  "type": "object",
  "additionalProperties": false,
  "required": ["series", "scope", "quality"],
  "properties": {
    "series": {
      "type": "array",
      "items": {"$ref": "#/$defs/artifacts/AttributionSeriesEntry"}
    },
    "scope": {"type": "object"},
    "quality": {"type": "object"}
  }
},
"AttributionFrameArtifact": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "artifact_id",
    "artifact_family",
    "shape",
    "subject",
    "axes",
    "measures",
    "capabilities",
    "lineage",
    "payload"
  ],
  "properties": {
    "artifact_id": {"type": "string", "minLength": 1},
    "artifact_family": {"const": "attribution_frame"},
    "shape": {"const": "ranked_contributions"},
    "subject": {"type": "object"},
    "axes": {
      "type": "array",
      "items": {"$ref": "#/$defs/artifacts/MetricFrameAxis2"},
      "minItems": 1
    },
    "measures": {
      "type": "array",
      "items": {"$ref": "#/$defs/artifacts/AttributionFrameMeasure"},
      "minItems": 2
    },
    "capabilities": {
      "type": "array",
      "items": {"enum": ["filterable"]}
    },
    "lineage": {"type": "object"},
    "payload": {"$ref": "#/$defs/artifacts/AttributionFramePayload"}
  }
}
```

Then replace `DeltaDecompositionResult` in artifact unions with `AttributionFrameArtifact`. Keep the `Decompose` request unchanged with `compare_artifact_id`.

- [ ] **Step 4: Update readable schemas and docs**

In `aoi-spec/schema/aoi.schema.yaml`, replace `delta_decomposition_result` with:

```yaml
attribution_frame_artifact:
  artifact_id: string
  artifact_family: attribution_frame
  shape: ranked_contributions
  subject:
    kind: comparison
    metric_ref: string
    current: object
    baseline: object
  axes:
    - kind: dimension
      name: string
  measures:
    - id: contribution_abs | contribution_pct
      value_type: number
      nullable: boolean
  capabilities:
    - filterable
  lineage:
    operation: decompose
    source_artifact_ids: string[]
  payload:
    series:
      - keys: dimension_key_map
        points:
          - contribution_abs: number
            contribution_pct: number
            current_value: number
            baseline_value: number
            presence: both | current_only | baseline_only
            rank: integer
    scope: object
    quality: object
```

In `aoi-spec/spec.md`, replace decompose result wording with:

```markdown
`decompose` returns an `attribution_frame` artifact with shape
`ranked_contributions`. Each payload point carries `contribution_abs` and
`contribution_pct`; these are attribution measures, not delta-frame measures.
```

In `aoi-spec/README.md`, update the artifact type list to include `attribution_frame` and remove `delta_decomposition`.

In `docs/specs/analysis/aoi-spec.schema.yaml`, mirror the same `attribution_frame_artifact` structure and confirm the existing registry section still lists `attribution_frame(ranked_contributions)`.

- [ ] **Step 5: Regenerate generated contracts**

Run:

```bash
.venv/bin/python scripts/generate_contract_models.py
```

Expected: command exits 0 and rewrites `marivo/contracts/generated/aoi.py`.

- [ ] **Step 6: Run generated-model tests**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py -k "aoi or attribution_frame"'
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add aoi-spec/schema/aoi.schema.json aoi-spec/schema/aoi.schema.yaml aoi-spec/spec.md aoi-spec/README.md docs/specs/analysis/aoi-spec.schema.yaml marivo/contracts/generated/aoi.py tests/contracts/test_generated_models.py
git commit -m "feat: define attribution frame AOI contract" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 4: Validate Decompose Delta Frame Inputs

**Files:**
- Modify: `marivo/runtime/intents/decompose.py`
- Test: `tests/runtime/intents/test_decompose_runner.py`

- [ ] **Step 1: Write failing input guard tests**

Add these tests to `tests/runtime/intents/test_decompose_runner.py`:

```python
class DecomposeDeltaFrameGuardTests(unittest.TestCase):
    def test_decompose_rejects_non_delta_frame_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "source artifact must be delta_frame"):
            _normalize_decompose_compare_input(
                {
                    "artifact_family": "metric_frame",
                    "shape": "scalar",
                    "capabilities": ["comparable"],
                    "axes": [],
                    "payload": {"series": []},
                }
            )

    def test_decompose_rejects_delta_frame_without_decomposable_capability(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires decomposable capability"):
            _normalize_decompose_compare_input(
                {
                    "artifact_family": "delta_frame",
                    "shape": "scalar_delta",
                    "capabilities": ["filterable"],
                    "axes": [],
                    "payload": {"series": []},
                }
            )

    def test_decompose_accepts_scalar_delta_frame_family(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "scalar_delta",
                "capabilities": ["filterable", "decomposable"],
                "metric": "m1",
                "metric_ref": "metric.m1",
                "axes": [],
                "payload": {
                    "series": [
                        {
                            "keys": {},
                            "points": [
                                {
                                    "current_value": 100.0,
                                    "baseline_value": 80.0,
                                    "delta_abs": 20.0,
                                    "delta_pct": 0.25,
                                    "direction": "increase",
                                }
                            ],
                        }
                    ],
                    "scope": {
                        "current_value": 100.0,
                        "baseline_value": 80.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.25,
                        "direction": "increase",
                    },
                },
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.m1",
                    "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}, "scope": {}},
                    "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}, "scope": {}},
                },
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
            }
        )

        self.assertEqual(normalized["shape"], "scalar_delta")
        self.assertEqual(normalized["scope_absolute_delta"], 20.0)
        self.assertEqual(normalized["current_time_scope"]["start"], "2024-01-08")
```

- [ ] **Step 2: Run guard tests and verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py -k DeltaFrameGuard'
```

Expected: FAIL because `_normalize_decompose_compare_input` still uses `comparison_type` and accepts legacy artifacts.

- [ ] **Step 3: Implement delta-frame guard helpers**

In `marivo/runtime/intents/decompose.py`, update imports from `metric_frame.py`:

```python
from marivo.runtime.intents.metric_frame import (
    build_attribution_frame_artifact,
    dimension_names_from_axes,
    has_dimension_axis,
    has_time_axis,
    is_delta_frame_artifact,
    read_axes_from_artifact,
    read_delta_frame_shape,
    read_frame_payload_scope,
    read_frame_payload_series,
)
```

Add this helper before `_normalize_decompose_compare_input`:

```python
_SUPPORTED_DELTA_FRAME_SHAPES: frozenset[str] = frozenset(
    {"scalar_delta", "time_series_delta", "segmented_delta", "panel_delta"}
)


def _require_delta_frame_source(source_artifact: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    if not is_delta_frame_artifact(source_artifact):
        raise ValueError("decompose: INVALID_ARGUMENT - source artifact must be delta_frame")
    shape = read_delta_frame_shape(source_artifact)
    if shape not in _SUPPORTED_DELTA_FRAME_SHAPES:
        raise ValueError(
            f"decompose: INVALID_ARGUMENT - delta_frame shape '{shape}' is not supported"
        )
    capabilities = source_artifact.get("capabilities") or []
    if "decomposable" not in capabilities:
        raise ValueError(
            "decompose: INVALID_ARGUMENT - delta_frame source requires decomposable capability"
        )
    axes = read_axes_from_artifact(source_artifact)
    _validate_delta_axes(shape, axes)
    read_frame_payload_series(source_artifact)
    return shape, axes


def _validate_delta_axes(shape: str, axes: list[dict[str, str]]) -> None:
    has_time = has_time_axis(axes)
    has_dimension = has_dimension_axis(axes)
    if shape == "scalar_delta" and axes:
        raise ValueError("decompose: INVALID_ARGUMENT - scalar_delta must not declare axes")
    if shape == "time_series_delta" and (not has_time or has_dimension):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - time_series_delta requires one time axis and no dimension axis"
        )
    if shape == "segmented_delta" and (has_time or not has_dimension):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - segmented_delta requires dimension axis and no time axis"
        )
    if shape == "panel_delta" and (not has_time or not has_dimension):
        raise ValueError(
            "decompose: INVALID_ARGUMENT - panel_delta requires time and dimension axes"
        )
```

- [ ] **Step 4: Replace normalizer type discrimination**

At the start of `_normalize_decompose_compare_input`, replace `comparison_type` inference with:

```python
shape, axes = _require_delta_frame_source(compare_artifact)
payload_series = read_frame_payload_series(compare_artifact)
payload_scope = read_frame_payload_scope(compare_artifact)
subject = compare_artifact.get("subject") or {}
metric_ref = (
    compare_artifact.get("metric_ref")
    or subject.get("metric_ref")
    or compare_artifact.get("metric")
    or ""
)
metric_name = str(metric_ref).removeprefix("metric.")
```

Return dictionaries with both names during this task:

```python
"shape": shape,
"comparison_type": shape,
```

This keeps existing call sites stable while moving the primary normalizer to `shape`.

- [ ] **Step 5: Normalize scalar and time-series from payload**

For scalar, read the first payload point:

```python
if shape == "scalar_delta":
    point = (payload_series[0].get("points") or [{}])[0] if payload_series else {}
    scope_current_value = _safe_float(payload_scope.get("current_value") or point.get("current_value"))
    scope_baseline_value = _safe_float(payload_scope.get("baseline_value") or point.get("baseline_value"))
    scope_absolute_delta = _safe_float(payload_scope.get("delta_abs") or point.get("delta_abs") or point.get("delta"))
    scope_relative_delta = _safe_float(payload_scope.get("delta_pct") or point.get("delta_pct"))
    scope_direction = payload_scope.get("direction") or point.get("direction") or "undefined"
    current_time_scope, baseline_time_scope, current_scope, baseline_scope = _comparison_subject_scopes(compare_artifact)
    return {
        "shape": "scalar_delta",
        "comparison_type": "scalar_delta",
        "metric_name": metric_name,
        "unit": _unit_from_measures(compare_artifact),
        "scope_current_value": scope_current_value,
        "scope_baseline_value": scope_baseline_value,
        "scope_absolute_delta": scope_absolute_delta,
        "scope_relative_delta": scope_relative_delta,
        "scope_direction": scope_direction,
        "source_observation_type": "scalar",
        "current_time_scope": current_time_scope,
        "baseline_time_scope": baseline_time_scope,
        "current_scope": current_scope,
        "baseline_scope": baseline_scope,
        "analytical_metadata": {"decomposition_source": "scalar_delta"},
    }
```

Add the helpers used above:

```python
def _comparison_subject_scopes(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    subject = artifact.get("subject") or {}
    current = subject.get("current") or {}
    baseline = subject.get("baseline") or {}
    resolved_input = artifact.get("resolved_input_summary") or {}
    return (
        dict(current.get("time_scope") or resolved_input.get("current_time_scope") or {}),
        dict(baseline.get("time_scope") or resolved_input.get("baseline_time_scope") or {}),
        dict(current.get("scope") or resolved_input.get("current_scope") or {}),
        dict(baseline.get("scope") or resolved_input.get("baseline_scope") or {}),
    )


def _unit_from_measures(artifact: dict[str, Any]) -> str | None:
    measures = artifact.get("measures") or []
    for measure in measures:
        if isinstance(measure, dict) and measure.get("id") == "delta_abs":
            unit = measure.get("unit")
            return str(unit) if unit is not None else None
    return artifact.get("unit")
```

For time-series, reuse current aggregation logic but read `payload_series` and `payload_scope` first, falling back to summary aliases only when needed.

- [ ] **Step 6: Run guard tests and existing decompose tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py -k "DeltaFrameGuard or scalar_delta or time_series"'
```

Expected: PASS for the new guard tests and existing scalar/time-series normalizer tests updated to include `artifact_family=delta_frame` and `capabilities=["decomposable"]`.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add marivo/runtime/intents/decompose.py tests/runtime/intents/test_decompose_runner.py
git commit -m "feat: validate decompose delta frame inputs" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 5: Support Segmented And Panel Delta Inputs

**Files:**
- Modify: `marivo/runtime/intents/decompose.py`
- Test: `tests/runtime/intents/test_decompose_runner.py`

- [ ] **Step 1: Write failing segmented fast-path tests**

Add this test to `tests/runtime/intents/test_decompose_runner.py`:

```python
def test_segmented_delta_same_dimension_normalizes_fast_path_rows() -> None:
    normalized = _normalize_decompose_compare_input(
        {
            "artifact_family": "delta_frame",
            "shape": "segmented_delta",
            "capabilities": ["sliceable", "filterable", "decomposable"],
            "metric_ref": "metric.revenue",
            "axes": [{"kind": "dimension", "name": "channel"}],
            "payload": {
                "series": [
                    {
                        "keys": {"channel": "paid"},
                        "points": [
                            {
                                "current_value": 70.0,
                                "baseline_value": 58.0,
                                "delta_abs": 12.0,
                                "delta_pct": 12.0 / 58.0,
                                "presence": "both",
                            }
                        ],
                    },
                    {
                        "keys": {"channel": "organic"},
                        "points": [
                            {
                                "current_value": 50.0,
                                "baseline_value": 42.0,
                                "delta_abs": 8.0,
                                "delta_pct": 8.0 / 42.0,
                                "presence": "both",
                            }
                        ],
                    },
                ],
                "scope": {
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "delta_abs": 20.0,
                    "delta_pct": 0.2,
                    "direction": "increase",
                },
            },
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}, "scope": {}},
                "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}, "scope": {}},
            },
            "lineage": {
                "current_source_ref": {"step_id": "step_current"},
                "baseline_source_ref": {"step_id": "step_baseline"},
            },
            "analytical_metadata": {"series_complete": True},
        }
    )

    assert normalized["shape"] == "segmented_delta"
    assert normalized["fast_path_dimension"] == "channel"
    assert normalized["fast_path_rows"] == [
        {"key": "paid", "current_value": 70.0, "baseline_value": 58.0, "absolute_contribution": 12.0, "presence": "both"},
        {"key": "organic", "current_value": 50.0, "baseline_value": 42.0, "absolute_contribution": 8.0, "presence": "both"},
    ]
```

- [ ] **Step 2: Write failing panel fast-path tests**

Add:

```python
def test_panel_delta_same_dimension_aggregates_fast_path_rows() -> None:
    normalized = _normalize_decompose_compare_input(
        {
            "artifact_family": "delta_frame",
            "shape": "panel_delta",
            "capabilities": ["sliceable", "filterable", "decomposable"],
            "metric_ref": "metric.revenue",
            "axes": [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "channel"}],
            "payload": {
                "series": [
                    {
                        "keys": {"channel": "paid"},
                        "points": [
                            {"window": {"start": "2024-01-08", "end": "2024-01-09"}, "current_value": 30.0, "baseline_value": 20.0, "delta_abs": 10.0, "presence": "both"},
                            {"window": {"start": "2024-01-09", "end": "2024-01-10"}, "current_value": 40.0, "baseline_value": 38.0, "delta_abs": 2.0, "presence": "both"},
                        ],
                    },
                    {
                        "keys": {"channel": "organic"},
                        "points": [
                            {"window": {"start": "2024-01-08", "end": "2024-01-09"}, "current_value": 25.0, "baseline_value": 20.0, "delta_abs": 5.0, "presence": "both"},
                            {"window": {"start": "2024-01-09", "end": "2024-01-10"}, "current_value": 25.0, "baseline_value": 22.0, "delta_abs": 3.0, "presence": "both"},
                        ],
                    },
                ],
                "scope": {"current_value": 120.0, "baseline_value": 100.0, "delta_abs": 20.0, "delta_pct": 0.2, "direction": "increase"},
            },
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-10"}, "scope": {}},
                "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-03"}, "scope": {}},
            },
            "lineage": {
                "current_source_ref": {"step_id": "step_current"},
                "baseline_source_ref": {"step_id": "step_baseline"},
            },
            "analytical_metadata": {"series_complete": True, "matched_bucket_count": 2},
        }
    )

    assert normalized["shape"] == "panel_delta"
    assert normalized["fast_path_dimension"] == "channel"
    assert normalized["fast_path_rows"] == [
        {"key": "paid", "current_value": 70.0, "baseline_value": 58.0, "absolute_contribution": 12.0, "presence": "both"},
        {"key": "organic", "current_value": 50.0, "baseline_value": 42.0, "absolute_contribution": 8.0, "presence": "both"},
    ]
```

- [ ] **Step 3: Write failing incomplete-source tests**

Add:

```python
def test_segmented_delta_without_completeness_has_no_fast_path() -> None:
    normalized = _normalize_decompose_compare_input(
        {
            "artifact_family": "delta_frame",
            "shape": "segmented_delta",
            "capabilities": ["sliceable", "filterable", "decomposable"],
            "metric_ref": "metric.revenue",
            "axes": [{"kind": "dimension", "name": "channel"}],
            "payload": {"series": [], "scope": {"delta_abs": 20.0}},
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}, "scope": {}},
                "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}, "scope": {}},
            },
            "lineage": {
                "current_source_ref": {"step_id": "step_current"},
                "baseline_source_ref": {"step_id": "step_baseline"},
            },
            "analytical_metadata": {"series_complete": False},
        }
    )

    assert normalized["fast_path_rows"] is None
```

- [ ] **Step 4: Run segmented/panel tests and verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py -k "segmented_delta or panel_delta"'
```

Expected: FAIL because segmented/panel normalizer paths are not implemented.

- [ ] **Step 5: Implement fast-path extraction helpers**

Add to `marivo/runtime/intents/decompose.py`:

```python
def _series_complete(artifact: dict[str, Any]) -> bool:
    analytical = artifact.get("analytical_metadata") or {}
    return bool(analytical.get("series_complete") is True)


def _fast_path_rows_for_delta_frame(
    *,
    artifact: dict[str, Any],
    shape: str,
    requested_dimension: str | None,
) -> tuple[str | None, list[dict[str, Any]] | None]:
    axes = read_axes_from_artifact(artifact)
    dimensions = dimension_names_from_axes(axes)
    if not requested_dimension or dimensions != [requested_dimension]:
        return None, None
    if shape not in {"segmented_delta", "panel_delta"}:
        return None, None
    if not _series_complete(artifact):
        return requested_dimension, None
    if shape == "segmented_delta":
        return requested_dimension, _segmented_fast_path_rows(artifact, requested_dimension)
    return requested_dimension, _panel_fast_path_rows(artifact, requested_dimension)


def _segmented_fast_path_rows(
    artifact: dict[str, Any],
    dimension: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in read_frame_payload_series(artifact):
        keys = entry.get("keys") or {}
        key = keys.get(dimension)
        point = ((entry.get("points") or [{}])[0]) or {}
        rows.append(
            {
                "key": key,
                "current_value": _safe_float(point.get("current_value")),
                "baseline_value": _safe_float(point.get("baseline_value")),
                "absolute_contribution": _safe_float(point.get("delta_abs") or point.get("delta")),
                "presence": point.get("presence") or "both",
            }
        )
    return rows


def _panel_fast_path_rows(
    artifact: dict[str, Any],
    dimension: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in read_frame_payload_series(artifact):
        keys = entry.get("keys") or {}
        key = keys.get(dimension)
        current_values: list[float] = []
        baseline_values: list[float] = []
        delta_values: list[float] = []
        for point in entry.get("points") or []:
            current = _safe_float(point.get("current_value"))
            baseline = _safe_float(point.get("baseline_value"))
            delta = _safe_float(point.get("delta_abs") or point.get("delta"))
            if current is not None:
                current_values.append(current)
            if baseline is not None:
                baseline_values.append(baseline)
            if delta is not None:
                delta_values.append(delta)
        rows.append(
            {
                "key": key,
                "current_value": sum(current_values) if current_values else None,
                "baseline_value": sum(baseline_values) if baseline_values else None,
                "absolute_contribution": sum(delta_values) if delta_values else None,
                "presence": "both",
            }
        )
    return rows
```

- [ ] **Step 6: Wire fast-path metadata into normalizer**

At the end of `_normalize_decompose_compare_input`, for segmented and panel shapes, include:

```python
fast_path_dimension, fast_path_rows = _fast_path_rows_for_delta_frame(
    artifact=compare_artifact,
    shape=shape,
    requested_dimension=None,
)
```

Then update `run_decompose_intent` after `dimension` is validated:

```python
fast_path_dimension, fast_path_rows = _fast_path_rows_for_delta_frame(
    artifact=compare_artifact,
    shape=normalized_compare["shape"],
    requested_dimension=dimension,
)
normalized_compare["fast_path_dimension"] = fast_path_dimension
normalized_compare["fast_path_rows"] = fast_path_rows
```

Keep normalizer tests deterministic by accepting `requested_dimension` as an optional argument:

```python
def _normalize_decompose_compare_input(
    compare_artifact: dict[str, Any],
    *,
    requested_dimension: str | None = None,
) -> dict[str, Any]:
    ...
    fast_path_dimension, fast_path_rows = _fast_path_rows_for_delta_frame(
        artifact=compare_artifact,
        shape=shape,
        requested_dimension=requested_dimension,
    )
```

Update the tests added in Steps 1-3 to call `_normalize_decompose_compare_input(..., requested_dimension="channel")` where they assert fast-path rows.

- [ ] **Step 7: Run segmented/panel tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py -k "segmented_delta or panel_delta"'
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

Run:

```bash
git add marivo/runtime/intents/decompose.py tests/runtime/intents/test_decompose_runner.py
git commit -m "feat: support segmented and panel delta attribution inputs" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 6: Emit Attribution Frame From Decompose

**Files:**
- Modify: `marivo/runtime/intents/decompose.py`
- Test: `tests/runtime/intents/test_decompose_runner.py`

- [ ] **Step 1: Write failing output tests**

Update the main `run_decompose_intent` success test in `tests/runtime/intents/test_decompose_runner.py` to assert:

```python
result = run_decompose_intent(runtime, _SESSION, params)

assert result["artifact_family"] == "attribution_frame"
assert result["shape"] == "ranked_contributions"
assert result["axes"] == [{"kind": "dimension", "name": "channel"}]
assert result["measures"] == [
    {"id": "contribution_abs", "value_type": "number", "nullable": False},
    {"id": "contribution_pct", "value_type": "number", "nullable": True},
]
assert result["payload"]["scope"]["delta_abs"] == result["scope_absolute_delta"]
assert result["payload"]["series"][0]["points"][0]["contribution_abs"] == result["rows"][0]["absolute_contribution"]
assert result["payload"]["series"][0]["points"][0]["contribution_pct"] == result["rows"][0]["contribution_share"]
```

Update the artifact type test to:

```python
def test_decompose_artifact_type_is_attribution_frame(self) -> None:
    runtime = _runtime_for_decompose()
    with patch("marivo.runtime.intents.decompose.commit_step_result") as commit:
        commit.side_effect = lambda _runtime, _session_id, _step_id, _step_type, artifact_type, _artifact_name, artifact, *_args, **_kwargs: {
            **artifact,
            "artifact_id": _FAKE_ARTIFACT_ID,
            "step_ref": {"step_id": _step_id, "step_type": _step_type},
        }
        run_decompose_intent(runtime, _SESSION, {"compare_artifact_id": "art_cmp", "dimension": "channel"})
        args = commit.call_args.args
        self.assertEqual(args[4], "attribution_frame")
```

- [ ] **Step 2: Run output tests and verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py -k "attribution_frame or artifact_type"'
```

Expected: FAIL because decompose still commits `delta_decomposition`.

- [ ] **Step 3: Convert decomposition rows into attribution series**

Add this helper to `marivo/runtime/intents/decompose.py`:

```python
def _attribution_series_from_rows(
    rows: list[dict[str, Any]],
    *,
    dimension: str,
) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for rank_0, row in enumerate(rows):
        key = row.get("key")
        series.append(
            {
                "keys": {dimension: key},
                "points": [
                    {
                        "contribution_abs": row.get("absolute_contribution"),
                        "contribution_pct": row.get("contribution_share"),
                        "current_value": row.get("current_value"),
                        "baseline_value": row.get("baseline_value"),
                        "presence": row.get("presence") or "both",
                        "rank": rank_0 + 1,
                    }
                ],
            }
        )
    return series
```

- [ ] **Step 4: Use fast-path rows when available**

Before `dispatch_decomposition_strategy`, add:

```python
fast_path_rows = normalized_compare.get("fast_path_rows")
```

Replace left/right query execution with a branch:

```python
if fast_path_rows is None:
    left_rows, left_sql, left_query_hash, left_elapsed_ms = _run_segmented_query(...)
    right_rows, right_sql, _, right_elapsed_ms = _run_segmented_query(...)
    left_map = {row.get(dimension): _safe_float(row.get("current_value")) for row in left_rows}
    right_map = {row.get(dimension): _safe_float(row.get("current_value")) for row in right_rows}
else:
    left_rows = []
    right_rows = []
    left_sql = None
    right_sql = None
    left_query_hash = None
    left_elapsed_ms = None
    right_elapsed_ms = None
    left_map = {
        row.get("key"): _safe_float(row.get("current_value"))
        for row in fast_path_rows
    }
    right_map = {
        row.get("key"): _safe_float(row.get("baseline_value"))
        for row in fast_path_rows
    }
```

Do not bypass `dispatch_decomposition_strategy`; it remains the strategy boundary.

- [ ] **Step 5: Build attribution frame artifact**

Replace the existing decompose artifact dict with:

```python
scope_payload = {
    "current_value": scope_current_value,
    "baseline_value": scope_baseline_value,
    "delta_abs": scope_absolute_delta,
    "delta_pct": scope_relative_delta,
    "direction": scope_direction,
}
quality_payload = {
    "reconciliation_status": decomp.quality.reconciliation_status,
    "reconciliation_gap": decomp.quality.reconciliation_gap,
    "confidence_grade": decomp.quality.confidence_grade,
    "unexplained_delta_abs": unexplained_absolute_delta,
    "unexplained_pct": unexplained_share,
    "unexplained_reason": unexplained_reason,
}
attribution_series = _attribution_series_from_rows(returned_rows, dimension=dimension)
subject = {
    "kind": "comparison",
    "metric_ref": f"metric.{metric_name}",
    "current": {"time_scope": current_time_scope, "scope": current_scope},
    "baseline": {"time_scope": baseline_time_scope, "scope": baseline_scope},
}
artifact = {
    **build_attribution_frame_artifact(
        artifact_id="",
        metric_ref=f"metric.{metric_name}",
        dimension=dimension,
        subject=subject,
        series=attribution_series,
        scope=scope_payload,
        quality=quality_payload,
        lineage={
            "operation": "decompose",
            "source_artifact_ids": [compare_artifact_id],
            "compare_artifact": compare_ref_out,
            "current_artifact": current_ref_out,
            "baseline_artifact": baseline_ref_out,
        },
    ),
    "schema_version": "2.0",
    "metric": metric_name,
    "compare_ref": compare_ref_out,
    "current_ref": current_ref_out,
    "baseline_ref": baseline_ref_out,
    "dimension": dimension,
    "rows": returned_rows,
    "method": decomp.method,
    "unit": unit,
    "current_time_scope": current_time_scope,
    "baseline_time_scope": baseline_time_scope,
    "resolved_scopes": {"current": current_scope, "baseline": baseline_scope},
    "scope_current_value": scope_current_value,
    "scope_baseline_value": scope_baseline_value,
    "scope_absolute_delta": scope_absolute_delta,
    "scope_relative_delta": scope_relative_delta,
    "scope_direction": scope_direction,
    "attribution": {"status": attribution_status, "issues": issues},
    "unexplained_absolute_delta": unexplained_absolute_delta,
    "unexplained_share": unexplained_share,
    "unexplained_reason": unexplained_reason,
    "analytical_metadata": {
        "method": decomp.method,
        "decomposition_semantics": metric_decomposition_semantics,
        "reconciliation_status": decomp.quality.reconciliation_status,
        "reconciliation_gap": decomp.quality.reconciliation_gap,
        "confidence_grade": decomp.quality.confidence_grade,
        "confidence_rationale": decomp.quality.confidence_rationale,
        "recommended_use": decomp.quality.recommended_use,
        "flat_tolerance_relative": 0.01,
        "current_row_count": len(left_rows),
        "baseline_row_count": len(right_rows),
        "returned_row_count": len(returned_rows),
        **source_analytical_metadata,
        "time_boundary_constraint": {"scope": "frozen_compare_window", "time_rollup_implied": False},
    },
    "source_lineage": {
        "compare_artifact": compare_ref_out,
        "current_artifact": current_ref_out,
        "baseline_artifact": baseline_ref_out,
    },
    "execution_metadata": execution_metadata,
}
```

Keep top-level `rows` only as an internal transition alias for attribute/diagnose and evidence extraction until later tasks replace readers.

- [ ] **Step 6: Commit as `attribution_frame`**

Change the `commit_step_result` call:

```python
result = commit_step_result(
    runtime,
    session_id,
    step_id,
    "decompose",
    "attribution_frame",
    artifact_name,
    artifact,
    summary,
    provenance=provenance,
    reasoning=reasoning,
    sql_texts=_sql_texts or None,
)
```

- [ ] **Step 7: Run decompose tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_decompose_runner.py'
```

Expected: PASS after updating legacy assertions to `attribution_frame`.

- [ ] **Step 8: Commit Task 6**

Run:

```bash
git add marivo/runtime/intents/decompose.py tests/runtime/intents/test_decompose_runner.py
git commit -m "feat: emit decompose attribution frames" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 7: Update AOI Projection, HTTP, And MCP Surfaces

**Files:**
- Modify: `marivo/contracts/aoi_projection.py`
- Modify: `marivo/transports/http/models/intent_response_models.py`
- Modify: `marivo/transports/mcp/tools/intents.py`
- Modify: `marivo/transports/mcp/tools/schemas.py`
- Test: `tests/runtime/test_aoi_intent_execution.py`
- Test: `tests/runtime/test_aoi_lowering.py`
- Test: `tests/transports/http/test_http_aoi_intents.py`
- Test: `tests/transports/mcp/test_tool_parity.py`
- Test: `tests/transports/mcp/test_mcp_aoi_adapter.py`

- [ ] **Step 1: Write failing projection test**

Add to the relevant projection or HTTP test file:

```python
def test_project_decompose_result_returns_attribution_frame_artifact() -> None:
    projected = project_aoi_artifact_result(
        "decompose",
        {
            "artifact_id": "art_attr",
            "artifact_family": "attribution_frame",
            "shape": "ranked_contributions",
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.revenue",
                "current": {"time_scope": {"field": "time", "start": "2024-01-08", "end": "2024-01-15"}, "scope": {}},
                "baseline": {"time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"}, "scope": {}},
            },
            "axes": [{"kind": "dimension", "name": "channel"}],
            "measures": [
                {"id": "contribution_abs", "value_type": "number", "nullable": False},
                {"id": "contribution_pct", "value_type": "number", "nullable": True},
            ],
            "capabilities": ["filterable"],
            "lineage": {"operation": "decompose", "source_artifact_ids": ["art_cmp"]},
            "payload": {
                "series": [{"keys": {"channel": "paid"}, "points": [{"contribution_abs": 12.0, "contribution_pct": 0.6, "rank": 1}]}],
                "scope": {"delta_abs": 20.0},
                "quality": {"reconciliation_status": "within_tolerance"},
            },
        },
    )

    assert projected["artifact_family"] == "attribution_frame"
    assert projected["shape"] == "ranked_contributions"
```

- [ ] **Step 2: Run projection/transport tests and verify failure**

Run:

```bash
make test TESTS='tests/transports/http/test_http_aoi_intents.py -k decompose'
```

Expected: FAIL because decompose projection still emits `DeltaDecompositionResult`.

- [ ] **Step 3: Update `aoi_projection.py`**

Replace the `if intent_type == "decompose":` branch in `project_aoi_artifact_result` with:

```python
if intent_type == "decompose":
    if payload.get("artifact_family") == "attribution_frame":
        return aoi.AttributionFrameArtifact.model_validate(payload).model_dump(
            mode="json",
            exclude_none=True,
        )
    payload_with_identity = {
        "artifact_id": str(payload.get("artifact_id") or "artifact_decompose"),
        "artifact_family": "attribution_frame",
        "shape": "ranked_contributions",
        "subject": payload.get("subject")
        or {
            "kind": "comparison",
            "metric_ref": f"metric.{payload.get('metric') or 'unknown'}",
            "current": {"time_scope": payload.get("current_time_scope") or {}, "scope": (payload.get("resolved_scopes") or {}).get("current") or {}},
            "baseline": {"time_scope": payload.get("baseline_time_scope") or {}, "scope": (payload.get("resolved_scopes") or {}).get("baseline") or {}},
        },
        "axes": payload.get("axes") or [{"kind": "dimension", "name": payload.get("dimension") or "dimension"}],
        "measures": [
            {"id": "contribution_abs", "value_type": "number", "nullable": False},
            {"id": "contribution_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": payload.get("lineage")
        or {"operation": "decompose", "source_artifact_ids": [(payload.get("compare_ref") or {}).get("artifact_id") or "unknown"]},
        "payload": payload.get("payload")
        or {
            "series": [
                {
                    "keys": {payload.get("dimension") or "dimension": row.get("key")},
                    "points": [
                        {
                            "contribution_abs": row.get("absolute_contribution"),
                            "contribution_pct": row.get("contribution_share"),
                            "current_value": row.get("current_value"),
                            "baseline_value": row.get("baseline_value"),
                            "presence": row.get("presence") or "both",
                            "rank": idx + 1,
                        }
                    ],
                }
                for idx, row in enumerate(payload.get("rows") or [])
            ],
            "scope": {
                "current_value": payload.get("scope_current_value"),
                "baseline_value": payload.get("scope_baseline_value"),
                "delta_abs": payload.get("scope_absolute_delta"),
                "delta_pct": payload.get("scope_relative_delta"),
                "direction": payload.get("scope_direction"),
            },
            "quality": {
                "reconciliation_status": (payload.get("analytical_metadata") or {}).get("reconciliation_status"),
                "unexplained_delta_abs": payload.get("unexplained_absolute_delta"),
                "unexplained_pct": payload.get("unexplained_share"),
            },
        },
    }
    return aoi.AttributionFrameArtifact.model_validate(payload_with_identity).model_dump(
        mode="json",
        exclude_none=True,
    )
```

Update `_infer_intent_type` so `artifact_family == "attribution_frame"` maps to `"decompose"`.

- [ ] **Step 4: Update HTTP response model**

In `marivo/transports/http/models/intent_response_models.py`, replace decompose response types:

```python
class DecomposeResponse(_EnvelopeBase):
    result: aoi.AttributionFrameArtifact | aoi.Artifact2
```

If generated failure artifacts remain `Artifact2`, keep failure typing broad and success typing strict.

- [ ] **Step 5: Update MCP schemas and parity expectations**

In `marivo/transports/mcp/tools/schemas.py`, change decompose output descriptions from `DeltaDecompositionResult` to `AttributionFrameArtifact`.

In `marivo/transports/mcp/tools/intents.py`, update tool descriptions to say:

```text
Returns an attribution_frame artifact with ranked_contributions payload.
```

In `tests/transports/mcp/test_tool_parity.py`, change assertions that mention `DeltaDecompositionResult`, `rows`, or `share` to assert `attribution_frame`, `payload.series`, `contribution_abs`, and `contribution_pct`.

- [ ] **Step 6: Run transport tests**

Run:

```bash
make test TESTS='tests/runtime/test_aoi_lowering.py tests/runtime/test_aoi_intent_execution.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_mcp_aoi_adapter.py'
```

Expected: PASS.

- [ ] **Step 7: Commit Task 7**

Run:

```bash
git add marivo/contracts/aoi_projection.py marivo/transports/http/models/intent_response_models.py marivo/transports/mcp/tools/intents.py marivo/transports/mcp/tools/schemas.py tests/runtime/test_aoi_lowering.py tests/runtime/test_aoi_intent_execution.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_mcp_aoi_adapter.py
git commit -m "feat: project decompose attribution frame outputs" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 8: Update Attribute And Diagnose Downstream Readers

**Files:**
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `marivo/runtime/intents/diagnose.py`
- Test: `tests/runtime/intents/test_attribute_runner.py`
- Test: `tests/runtime/intents/test_diagnose_runner.py`

- [ ] **Step 1: Write failing derived-intent tests**

In `tests/runtime/intents/test_attribute_runner.py`, update `_decompose_result` fixture so it returns:

```python
{
    "artifact_id": f"art_decompose_{dimension}",
    "artifact_family": "attribution_frame",
    "shape": "ranked_contributions",
    "axes": [{"kind": "dimension", "name": dimension}],
    "payload": {
        "series": [
            {
                "keys": {dimension: "A"},
                "points": [
                    {
                        "contribution_abs": 12.0,
                        "contribution_pct": 0.6,
                        "current_value": 70.0,
                        "baseline_value": 58.0,
                        "presence": "both",
                        "rank": 1,
                    }
                ],
            }
        ],
        "scope": {"delta_abs": 20.0},
        "quality": {"reconciliation_status": "within_tolerance"},
    },
    "rows": [],
    "dimension": dimension,
    "scope_absolute_delta": 20.0,
    "unexplained_absolute_delta": 0.0,
    "unexplained_share": 0.0,
    "unexplained_reason": None,
    "attribution": {"status": "attributable", "issues": []},
    "step_ref": {"step_id": f"step_decompose_{dimension}", "step_type": "decompose"},
}
```

Add an assertion to the attribute success test:

```python
assert result["drivers"][0]["rows"][0]["contribution_abs"] == 12.0
assert result["drivers"][0]["rows"][0]["contribution_pct"] == 0.6
```

In `tests/runtime/intents/test_diagnose_runner.py`, update decompose fixtures the same way and assert diagnosis drivers read `contribution_abs`.

- [ ] **Step 2: Run derived tests and verify failure**

Run:

```bash
make test TESTS='tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py -k "attribute or diagnose"'
```

Expected: FAIL where derived intents expect legacy `absolute_contribution` or `contribution_share`.

- [ ] **Step 3: Replace reader imports**

In `marivo/runtime/intents/attribute.py`, change:

```python
from marivo.runtime.intents.metric_frame import (
    read_compare_scalar_point,
    read_decompose_rows_from_series,
    read_metric_frame_scope,
    read_metric_frame_shape,
    read_metric_frame_time_scope,
)
```

to:

```python
from marivo.runtime.intents.metric_frame import (
    read_attribution_rows_from_series,
    read_compare_scalar_point,
    read_metric_frame_scope,
    read_metric_frame_shape,
    read_metric_frame_time_scope,
)
```

Replace:

```python
all_rows: list[dict[str, Any]] = read_decompose_rows_from_series(decompose_result)
```

with:

```python
all_rows: list[dict[str, Any]] = read_attribution_rows_from_series(decompose_result)
for row in all_rows:
    row.setdefault("absolute_contribution", row.get("contribution_abs"))
    row.setdefault("contribution_share", row.get("contribution_pct"))
```

In `marivo/runtime/intents/diagnose.py`, make the same import and row normalization change.

- [ ] **Step 4: Update derived artifact labels**

In `marivo/runtime/intents/attribute.py`, replace driver metadata:

```python
"decomposition_type": "delta_decomposition",
```

with:

```python
"artifact_family": "attribution_frame",
"shape": "ranked_contributions",
```

In `marivo/runtime/intents/diagnose.py`, make the same change for diagnosis driver records.

- [ ] **Step 5: Run derived tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py'
```

Expected: PASS.

- [ ] **Step 6: Commit Task 8**

Run:

```bash
git add marivo/runtime/intents/attribute.py marivo/runtime/intents/diagnose.py tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py
git commit -m "feat: consume attribution frames in derived intents" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 9: Update Evidence Extraction For Attribution Frames

**Files:**
- Modify: `marivo/runtime/evidence/decompose_extractor.py`
- Modify: `marivo/runtime/evidence/finding_extractor_registry.py`
- Test: `tests/runtime/evidence/test_compare_decompose_extractor.py`
- Test: `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`

- [ ] **Step 1: Write failing extractor registration tests**

In `tests/runtime/evidence/test_compare_decompose_extractor.py`, change registration assertions to:

```python
def test_registered_under_attribution_frame_v1(self) -> None:
    extractor = default_finding_registry.find("attribution_frame", "v1")
    self.assertIsInstance(extractor, DecomposeArtifactExtractor)
```

Add an extraction fixture:

```python
def _attribution_frame_payload() -> dict[str, Any]:
    return {
        "artifact_family": "attribution_frame",
        "shape": "ranked_contributions",
        "metric": "revenue",
        "metric_ref": "metric.revenue",
        "compare_ref": {"artifact_id": "art_cmp", "comparison_type": "scalar_delta"},
        "axes": [{"kind": "dimension", "name": "channel"}],
        "payload": {
            "series": [
                {
                    "keys": {"channel": "paid"},
                    "points": [
                        {
                            "contribution_abs": 12.0,
                            "contribution_pct": 0.6,
                            "current_value": 70.0,
                            "baseline_value": 58.0,
                            "presence": "both",
                            "rank": 1,
                        }
                    ],
                }
            ],
            "scope": {"delta_abs": 20.0},
            "quality": {"reconciliation_status": "within_tolerance"},
        },
        "lineage": {
            "compare_artifact": {"artifact_id": "art_cmp", "comparison_type": "scalar_delta"}
        },
    }
```

Assert extracted payload fields:

```python
result = DecomposeArtifactExtractor().extract(
    "art_attr",
    _attribution_frame_payload(),
    {"step_id": "step_attr", "step_type": "decompose"},
    "sess_1",
)

assert result.findings[0].payload.contribution_value == 12.0
assert result.findings[0].payload.contribution_share == 0.6
```

- [ ] **Step 2: Run evidence tests and verify failure**

Run:

```bash
make test TESTS='tests/runtime/evidence/test_compare_decompose_extractor.py -k attribution_frame'
```

Expected: FAIL because extractor is still registered under `delta_decomposition`.

- [ ] **Step 3: Update extractor metadata and row reading**

In `marivo/runtime/evidence/decompose_extractor.py`, change class metadata:

```python
artifact_type = "attribution_frame"
artifact_schema_version = "v1"
family = "decompose"
extractor_name = "attribution_frame_v1"
```

At the start of `extract`, replace row reads with:

```python
axes = artifact_payload.get("axes") or []
dimension = ""
for axis in axes:
    if isinstance(axis, dict) and axis.get("kind") == "dimension":
        dimension = str(axis.get("name") or "")
        break
if not dimension:
    dimension = artifact_payload.get("dimension") or ""
if not dimension:
    raise ValueError("DecomposeArtifactExtractor: attribution_frame is missing dimension axis.")

payload = artifact_payload.get("payload") or {}
series = payload.get("series") or []
rows: list[dict[str, Any]] = []
for entry in series:
    keys = entry.get("keys") or {}
    key = keys.get(dimension)
    for point in entry.get("points") or []:
        rows.append(
            {
                "key": key,
                "absolute_contribution": point.get("contribution_abs"),
                "contribution_share": point.get("contribution_pct"),
                "direction": point.get("direction") or "undefined",
            }
        )
```

Resolve compare artifact from either legacy `compare_ref` or lineage:

```python
compare_ref: dict[str, Any] = (
    artifact_payload.get("compare_ref")
    or (artifact_payload.get("lineage") or {}).get("compare_artifact")
    or {}
)
```

- [ ] **Step 4: Update registry**

In `marivo/runtime/evidence/finding_extractor_registry.py`, replace the decompose registration key with:

```python
registry.register(DecomposeArtifactExtractor())
```

The extractor's own `artifact_type` now provides `attribution_frame`.

- [ ] **Step 5: Update pipeline family behavior tests**

In `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`, replace artifact type expectations:

```python
artifact_type="attribution_frame"
```

and update payloads to use `artifact_family=attribution_frame`, `shape=ranked_contributions`, and `payload.series`.

- [ ] **Step 6: Run evidence tests**

Run:

```bash
make test TESTS='tests/runtime/evidence/test_compare_decompose_extractor.py tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py'
```

Expected: PASS.

- [ ] **Step 7: Commit Task 9**

Run:

```bash
git add marivo/runtime/evidence/decompose_extractor.py marivo/runtime/evidence/finding_extractor_registry.py tests/runtime/evidence/test_compare_decompose_extractor.py tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py
git commit -m "feat: extract evidence from attribution frames" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds and pre-commit hooks pass.

## Task 10: Update Public Docs And Run Full Verification

**Files:**
- Modify: `docs/user/marivo-mcp-tools-reference.md`
- Modify: `docs/api/intent-steps.md`
- Modify: any remaining docs found by the search command below

- [ ] **Step 1: Find stale legacy wording**

Run:

```bash
rg -n "DeltaDecompositionResult|delta_decomposition|delta_decomposition_result|share|absolute_contribution|contribution_share" aoi-spec docs marivo tests
```

Expected: any remaining matches are either internal compatibility aliases in runtime code or tests explicitly checking internal aliases. Public docs and public AOI schema should not mention `DeltaDecompositionResult` or `delta_decomposition_result`.

- [ ] **Step 2: Update MCP user reference**

In `docs/user/marivo-mcp-tools-reference.md`, replace the decompose response block with:

```ts
interface DecomposeResponse {
  result: AttributionFrameArtifact;
}

interface AttributionFrameArtifact {
  artifact_id: string;
  artifact_family: "attribution_frame";
  shape: "ranked_contributions";
  axes: Array<{ kind: "dimension"; name: string }>;
  measures: Array<
    | { id: "contribution_abs"; value_type: "number"; nullable: false }
    | { id: "contribution_pct"; value_type: "number"; nullable: true }
  >;
  payload: {
    series: Array<{
      keys: Record<string, string | number | boolean | null>;
      points: Array<{
        contribution_abs: number;
        contribution_pct: number | null;
        current_value?: number | null;
        baseline_value?: number | null;
        presence?: "both" | "current_only" | "baseline_only";
        rank: number;
      }>;
    }>;
    scope: Record<string, unknown>;
    quality: Record<string, unknown>;
  };
}
```

- [ ] **Step 3: Update API intent docs**

In `docs/api/intent-steps.md`, replace any decompose example result that contains `delta_decomposition` or `items` with:

```json
{
  "artifact_id": "art_attr_123",
  "artifact_family": "attribution_frame",
  "shape": "ranked_contributions",
  "payload": {
    "series": [
      {
        "keys": {"channel": "paid"},
        "points": [
          {
            "contribution_abs": 1200.0,
            "contribution_pct": 0.6,
            "rank": 1
          }
        ]
      }
    ],
    "scope": {"delta_abs": 2000.0},
    "quality": {"reconciliation_status": "within_tolerance"}
  }
}
```

- [ ] **Step 4: Run focused runtime and transport verification**

Run:

```bash
make test TESTS='tests/runtime/intents/test_compare_runner.py tests/runtime/intents/test_decompose_runner.py tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py tests/runtime/evidence/test_compare_decompose_extractor.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_tool_parity.py tests/contracts/test_generated_models.py'
```

Expected: PASS.

- [ ] **Step 5: Run typecheck and lint**

Run:

```bash
make typecheck
make lint
```

Expected: both commands exit 0.

- [ ] **Step 6: Run stale wording search again**

Run:

```bash
rg -n "DeltaDecompositionResult|delta_decomposition_result" aoi-spec docs marivo tests
```

Expected: no matches. If `delta_decomposition` remains in archive docs under `docs/archive/`, leave archive docs unchanged and mention that in the final implementation summary.

- [ ] **Step 7: Commit Task 10**

Run:

```bash
git add docs/user/marivo-mcp-tools-reference.md docs/api/intent-steps.md
git status --short --untracked-files=all
git diff --cached --name-status
git commit -m "docs: document attribution frame decompose output" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: only the public docs edited in this task are staged, commit succeeds, and pre-commit hooks pass. If the stale wording search required additional doc files, stage those exact paths before the status and cached-diff checks. Do not stage the pre-existing user edit to `docs/specs/analysis/foundations/analysis-operation-architecture.md` unless the user separately asks to include it.

## Final Verification

- [ ] **Step 1: Run the full test suite**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 2: Run final type and lint checks**

Run:

```bash
make typecheck
make lint
```

Expected: PASS.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short --untracked-files=all
git log --oneline -n 10
```

Expected: only intentional files are modified or committed. The pre-existing user edit to `docs/specs/analysis/foundations/analysis-operation-architecture.md` remains untouched unless the user separately asked to include it.

## Plan Self-Review Notes

- Spec coverage: tasks cover the transitional request field, delta-frame source guards, scalar/time-series/segmented/panel semantics, attribution-frame output, compare dependency, downstream derived intents, AOI/generated contracts, evidence extraction, transport projection, docs, and final verification.
- Placeholder scan: no unfinished-marker or open-ended validation steps remain.
- Type consistency: the plan consistently uses `artifact_family=delta_frame`, `artifact_family=attribution_frame`, `shape=ranked_contributions`, `contribution_abs`, `contribution_pct`, and the transitional `compare_artifact_id` request field.
