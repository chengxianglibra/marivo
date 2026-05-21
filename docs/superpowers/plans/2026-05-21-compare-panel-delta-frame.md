# Compare Panel + Delta Frame Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade compare intent to accept panel metric_frame inputs and output delta_frame artifacts per the v0.3 AOI design, updating all downstream consumers in the same change.

**Architecture:** Breaking change replaces `compare_artifact` with `delta_frame` as `artifact_family`. The `comparison_type` field becomes `shape`. Point-level `delta` becomes `delta_abs`. Scalar top-level aliases are removed. Panel delta computes per-series time-aligned deltas. `comparison_side` is declared as a metadata axis but not placed in series keys.

**Tech Stack:** Python 3.11+, Pydantic, DuckDB, pytest, MagicMock

---

## File Structure

### New files
- None (all changes are modifications to existing files)

### Modified files — Contracts (foundation layer)
- `aoi-spec/schema/aoi.schema.json` — add DeltaFrameArtifact, ComparisonSubject, comparison_side axis, DeltaFramePoint, DeltaFramePayload, DeltaFrameSeries
- `marivo/contracts/generated/aoi.py` — regenerated from schema
- `marivo/contracts/aoi_runtime.py` — add DeltaFrameArtifact to AoiArtifact alias, add delta_frame route to validate_aoi_artifact

### Modified files — Runtime helpers
- `marivo/runtime/intents/metric_frame.py` — add is_delta_frame_artifact, read_delta_frame_shape, read_delta_frame_series, build_delta_frame_artifact, rename read_compare_scalar_point to read_delta_scalar_point

### Modified files — Core intent
- `marivo/runtime/intents/compare.py` — rewrite output to delta_frame, add panel_delta computation branch, remove panel hard-reject, rename comparison_type to shape, rename delta to delta_abs, add comparison_side axis, add comparison subject, remove scalar top-level aliases

### Modified files — Downstream consumers
- `marivo/runtime/intents/decompose.py` — read delta_frame instead of compare_artifact, dispatch by shape, read delta_abs instead of delta, accept panel_delta
- `marivo/runtime/intents/attribute.py` — read shape from compare output, write shape in compare_ref
- `marivo/runtime/intents/diagnose.py` — read shape from compare output, write shape in attribution_comparison
- `marivo/runtime/evidence/compare_extractor.py` — dispatch by shape, read delta_abs, add panel_delta extraction
- `marivo/runtime/evidence/decompose_extractor.py` — read shape from compare_ref
- `marivo/runtime/evidence/proposition_seeding.py` — update change_kind mapping, add panel_change
- `marivo/contracts/aoi_projection.py` — add delta_frame fast path, panel_delta projection

### Modified files — Tests
- `tests/runtime/intents/_runner_fixtures.py` — add _scalar_delta_v2, _time_series_delta_v2, _segmented_delta_v2, _panel_delta_v2 helpers using build_delta_frame_artifact
- `tests/runtime/intents/test_compare_runner.py` — update all assertions from compare_artifact to delta_frame
- `tests/runtime/intents/test_decompose_runner.py` — update to consume delta_frame artifacts
- `tests/runtime/intents/test_attribute_runner.py` — update compare_result helper to produce delta_frame
- `tests/runtime/intents/test_diagnose_runner.py` — update compare mock results to delta_frame
- `tests/runtime/evidence/test_compare_decompose_extractor.py` — update all delta payload helpers to delta_frame format
- `tests/contracts/test_aoi_runtime_contract.py` — add DeltaFrameArtifact validation tests
- `tests/contracts/test_aoi_projection.py` (if exists) or tests within test_aoi_runtime_contract.py — add delta_frame projection tests

---

### Task 1: AOI Schema — Add DeltaFrameArtifact Definitions

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.json`

This task adds the JSON Schema definitions for delta_frame artifacts. These definitions are the contract foundation that all runtime changes depend on.

- [ ] **Step 1: Add DeltaFrameSubject definition**

Add `ComparisonSubject` inside the `$defs` section of `aoi.schema.json` (near the existing `MetricFrameSubject`):

```json
"ComparisonSubject": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "kind": { "const": "comparison" },
    "metric_ref": { "type": "string", "minLength": 1 },
    "current": { "$ref": "#/$defs/SubjectScopeRef" },
    "baseline": { "$ref": "#/$defs/SubjectScopeRef" }
  },
  "required": ["kind", "metric_ref", "current", "baseline"]
},
"SubjectScopeRef": {
  "type": "object",
  "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
  "properties": {
    "time_scope": { "$ref": "#/$defs/TimeScope" },
    "scope": { "type": "object" }
  }
}
```

- [ ] **Step 2: Add comparison_side axis definition**

Add `MetricFrameAxis3` (comparison_side axis) inside `$defs`:

```json
"MetricFrameAxis3": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "kind": { "const": "comparison_side" }
  },
  "required": ["kind"]
}
```

- [ ] **Step 3: Add DeltaFramePoint definition**

Add `DeltaFramePoint` inside `$defs`:

```json
"DeltaFramePoint": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "window": { "$ref": "#/$defs/MetricFrameWindow" },
    "current_value": { "type": ["number", "null"] },
    "baseline_value": { "type": ["number", "null"] },
    "delta_abs": { "type": ["number", "null"] },
    "delta_pct": { "type": ["number", "null"] },
    "direction": { "type": "string", "enum": ["increase", "decrease", "flat", "undefined"] },
    "presence": { "type": "string", "enum": ["both", "current_only", "baseline_only"] }
  },
  "required": ["current_value", "baseline_value", "delta_abs", "delta_pct", "direction"]
}
```

- [ ] **Step 4: Add DeltaFrameSeries definition**

```json
"DeltaFrameSeries": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "keys": {
      "type": "object",
      "additionalProperties": { "type": ["string", "number", "boolean", "null"] }
    },
    "points": {
      "type": "array",
      "items": { "$ref": "#/$defs/DeltaFramePoint" }
    }
  },
  "required": ["keys", "points"]
}
```

- [ ] **Step 5: Add DeltaFramePayload definition**

```json
"DeltaFramePayload": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "series": {
      "type": "array",
      "items": { "$ref": "#/$defs/DeltaFrameSeries" }
    }
  },
  "required": ["series"]
}
```

- [ ] **Step 6: Add DeltaFrameMeasure definition**

```json
"DeltaFrameMeasure": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string", "enum": ["delta_abs", "delta_pct"] },
    "value_type": { "const": "number" },
    "nullable": { "const": true },
    "unit": { "type": ["string", "null"] }
  },
  "required": ["id", "value_type", "nullable"]
}
```

- [ ] **Step 7: Add DeltaFrameArtifact definition**

Add the top-level `DeltaFrameArtifact` definition alongside the existing `MetricFrameArtifact`:

```json
"DeltaFrameArtifact": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "artifact_id": { "type": "string", "minLength": 1 },
    "artifact_family": { "const": "delta_frame" },
    "shape": { "type": "string", "enum": ["scalar_delta", "time_series_delta", "segmented_delta", "panel_delta"] },
    "subject": { "$ref": "#/$defs/ComparisonSubject" },
    "axes": {
      "type": "array",
      "items": { "$ref": "#/$defs/MetricFrameAxis1" }
    },
    "measures": {
      "type": "array",
      "minItems": 1,
      "maxItems": 2,
      "items": { "$ref": "#/$defs/DeltaFrameMeasure" }
    },
    "payload": { "$ref": "#/$defs/DeltaFramePayload" }
  },
  "required": ["artifact_id", "artifact_family", "shape", "subject", "axes", "measures", "payload"]
}
```

- [ ] **Step 8: Update axes items to include comparison_side**

In the `DeltaFrameArtifact.axes` definition, update the items to allow all three axis types (time, dimension, comparison_side):

```json
"axes": {
  "type": "array",
  "items": {
    "anyOf": [
      { "$ref": "#/$defs/MetricFrameAxis1" },
      { "$ref": "#/$defs/MetricFrameAxis2" },
      { "$ref": "#/$defs/MetricFrameAxis3" }
    ]
  }
}
```

- [ ] **Step 9: Verify schema is valid JSON**

Run: `python -c "import json; json.load(open('aoi-spec/schema/aoi.schema.json')); print('OK')"`

Expected: OK

- [ ] **Step 10: Commit**

```bash
git add aoi-spec/schema/aoi.schema.json
git commit -m "feat: add DeltaFrameArtifact, ComparisonSubject, comparison_side axis to AOI schema"
```

---

### Task 2: Regenerate AOI Contract Models

**Files:**
- Modify: `marivo/contracts/generated/aoi.py` (regenerated)
- Modify: `scripts/generate_contract_models.py` (if needed for new definitions)

- [ ] **Step 1: Regenerate contract models**

Run: `.venv/bin/python scripts/generate_contract_models.py`

Expected: Script runs successfully, `marivo/contracts/generated/aoi.py` is updated with `DeltaFrameArtifact`, `ComparisonSubject`, `MetricFrameAxis3`, `DeltaFramePoint`, `DeltaFrameSeries`, `DeltaFramePayload`, `DeltaFrameMeasure`, and `SubjectScopeRef` Pydantic models.

- [ ] **Step 2: Verify generated models exist**

Run: `.venv/bin/python -c "from marivo.contracts.generated.aoi import DeltaFrameArtifact, ComparisonSubject; print('OK')"`

Expected: OK

- [ ] **Step 3: Run contract tests**

Run: `make test TESTS='tests/contracts/'`

Expected: All existing contract tests pass. The generated models are new additions; they don't break existing tests.

- [ ] **Step 4: Commit**

```bash
git add marivo/contracts/generated/aoi.py
git commit -m "feat: regenerate AOI contract models with DeltaFrameArtifact"
```

---

### Task 3: Update AOI Runtime — Validate and Envelope Delta Frame

**Files:**
- Modify: `marivo/contracts/aoi_runtime.py`
- Test: `tests/contracts/test_aoi_runtime_contract.py`

- [ ] **Step 1: Write failing test for DeltaFrameArtifact validation**

Add to `tests/contracts/test_aoi_runtime_contract.py`:

```python
def test_validate_aoi_artifact_accepts_delta_frame_artifact():
    from marivo.contracts.aoi_runtime import validate_aoi_artifact

    delta_frame = {
        "artifact_id": "art_delta_test",
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.test",
            "current": {"time_scope": {"field": "log_date", "start": "2026-05-15T00:00:00+00:00", "end": "2026-05-16T00:00:00+00:00"}, "scope": {}},
            "baseline": {"time_scope": {"field": "log_date", "start": "2026-05-08T00:00:00+00:00", "end": "2026-05-09T00:00:00+00:00"}, "scope": {}},
        },
        "axes": [{"kind": "comparison_side"}],
        "measures": [{"id": "delta_abs", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"current_value": 10.0, "baseline_value": 5.0, "delta_abs": 5.0, "delta_pct": 1.0, "direction": "increase"}]}]},
    }
    result = validate_aoi_artifact(delta_frame)
    assert result["artifact_family"] == "delta_frame"
    assert result["shape"] == "scalar_delta"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/contracts/test_aoi_runtime_contract.py::test_validate_aoi_artifact_accepts_delta_frame_artifact'`

Expected: FAIL — `validate_aoi_artifact` does not route `artifact_family == "delta_frame"` yet.

- [ ] **Step 3: Update AoiArtifact type alias**

In `marivo/contracts/aoi_runtime.py`, update the `AoiArtifact` type alias to include `DeltaFrameArtifact`:

```python
from marivo.contracts.generated.aoi import DeltaFrameArtifact

AoiArtifact: TypeAlias = aoi.MetricFrameArtifact | aoi.DeltaFrameArtifact | aoi.Artifact1 | aoi.Artifact2
```

- [ ] **Step 4: Update validate_aoi_artifact to route delta_frame**

In `marivo/contracts/aoi_runtime.py`, in the `validate_aoi_artifact()` function, add a new branch after the `metric_frame` branch:

```python
if artifact_family == "delta_frame":
    return aoi.DeltaFrameArtifact.model_validate(raw).model_dump(mode="json", exclude_none=True)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `make test TESTS='tests/contracts/test_aoi_runtime_contract.py::test_validate_aoi_artifact_accepts_delta_frame_artifact'`

Expected: PASS

- [ ] **Step 6: Write test for delta_frame envelope result**

Add:

```python
def test_artifact_to_envelope_result_keeps_delta_frame_top_level():
    from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact

    delta_frame = {
        "artifact_id": "art_delta_test",
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.test",
            "current": {"time_scope": {"field": "log_date", "start": "2026-05-15T00:00:00+00:00", "end": "2026-05-16T00:00:00+00:00"}, "scope": {}},
            "baseline": {"time_scope": {"field": "log_date", "start": "2026-05-08T00:00:00+00:00", "end": "2026-05-09T00:00:00+00:00"}, "scope": {}},
        },
        "axes": [{"kind": "comparison_side"}],
        "measures": [{"id": "delta_abs", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"current_value": 10.0, "baseline_value": 5.0, "delta_abs": 5.0, "delta_pct": 1.0, "direction": "increase"}]}]},
    }
    validated = validate_aoi_artifact(delta_frame)
    result = artifact_to_envelope_result(validated)
    assert result["artifact_family"] == "delta_frame"
    assert result["payload"]["series"][0]["points"][0]["delta_abs"] == 5.0
```

- [ ] **Step 7: Run full contract tests**

Run: `make test TESTS='tests/contracts/'`

Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add marivo/contracts/aoi_runtime.py tests/contracts/test_aoi_runtime_contract.py
git commit -m "feat: add DeltaFrameArtifact to AOI runtime validation and envelope"
```

---

### Task 4: Add Delta Frame Helpers to metric_frame.py

**Files:**
- Modify: `marivo/runtime/intents/metric_frame.py`
- Test: `tests/runtime/intents/test_metric_frame_helpers.py` (create if not existing, or add to existing)

- [ ] **Step 1: Write failing tests for delta_frame helpers**

Create or add to a test file for metric_frame helpers. These tests verify pure-function behavior:

```python
import pytest
from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    is_delta_frame_artifact,
    read_delta_frame_shape,
    read_delta_frame_series,
    read_delta_scalar_point,
)


def test_is_delta_frame_artifact_returns_true():
    artifact = {"artifact_family": "delta_frame"}
    assert is_delta_frame_artifact(artifact) is True


def test_is_delta_frame_artifact_returns_false_for_metric_frame():
    artifact = {"artifact_family": "metric_frame"}
    assert is_delta_frame_artifact(artifact) is False


def test_read_delta_frame_shape_reads_shape_field():
    artifact = {"shape": "panel_delta"}
    assert read_delta_frame_shape(artifact) == "panel_delta"


def test_read_delta_frame_shape_raises_on_missing():
    with pytest.raises(ValueError, match="delta_frame artifact missing shape"):
        read_delta_frame_shape({})


def test_read_delta_frame_series_reads_payload_series():
    series = [{"keys": {}, "points": [{"delta_abs": 5.0}]}]
    artifact = {"payload": {"series": series}}
    assert read_delta_frame_series(artifact) == series


def test_build_delta_frame_artifact_scalar():
    result = build_delta_frame_artifact(
        artifact_id="art_test",
        shape="scalar_delta",
        metric_ref="metric.test",
        current_scope={"time_scope": {"field": "log_date", "start": "2026-05-15", "end": "2026-05-16"}, "scope": {}},
        baseline_scope={"time_scope": {"field": "log_date", "start": "2026-05-08", "end": "2026-05-09"}, "scope": {}},
        axes=[{"kind": "comparison_side"}],
        series=[{"keys": {}, "points": [{"current_value": 10.0, "baseline_value": 5.0, "delta_abs": 5.0, "delta_pct": 1.0, "direction": "increase"}]}],
        unit=None,
    )
    assert result["artifact_family"] == "delta_frame"
    assert result["shape"] == "scalar_delta"
    assert result["subject"]["kind"] == "comparison"
    assert result["subject"]["metric_ref"] == "metric.test"
    assert result["measures"][0]["id"] == "delta_abs"


def test_build_delta_frame_artifact_panel():
    result = build_delta_frame_artifact(
        artifact_id="art_test",
        shape="panel_delta",
        metric_ref="metric.test",
        current_scope={"time_scope": {"field": "log_date", "start": "2026-05-15", "end": "2026-05-16"}, "scope": {}},
        baseline_scope={"time_scope": {"field": "log_date", "start": "2026-05-08", "end": "2026-05-09"}, "scope": {}},
        axes=[{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "country"}, {"kind": "comparison_side"}],
        series=[{"keys": {"country": "US"}, "points": [{"window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"}, "current_value": 150, "baseline_value": 100, "delta_abs": 50, "delta_pct": 0.5, "direction": "increase", "presence": "both"}]}],
        unit=None,
    )
    assert result["axes"] == [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "country"}, {"kind": "comparison_side"}]
    assert result["payload"]["series"][0]["keys"]["country"] == "US"


def test_read_delta_scalar_point_reads_from_series():
    artifact = {
        "payload": {
            "series": [{"keys": {}, "points": [{"current_value": 10.0, "baseline_value": 5.0, "delta_abs": 5.0, "delta_pct": 1.0, "direction": "increase"}]}]
        },
    }
    point = read_delta_scalar_point(artifact)
    assert point["delta_abs"] == 5.0
    assert point["current_value"] == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test TESTS='tests/runtime/intents/test_metric_frame_helpers.py'`

Expected: FAIL — functions not defined yet.

- [ ] **Step 3: Implement delta_frame helpers**

In `marivo/runtime/intents/metric_frame.py`, add the following functions after the existing `is_metric_frame_artifact`:

```python
def is_delta_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "delta_frame"


def read_delta_frame_shape(artifact: dict[str, Any]) -> str:
    shape = artifact.get("shape")
    if not isinstance(shape, str) or not shape:
        raise ValueError("delta_frame artifact missing shape")
    return shape


def read_delta_frame_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("delta_frame artifact missing payload")
    series = payload.get("series")
    if not isinstance(series, list):
        raise ValueError("delta_frame artifact payload missing series")
    return series


def build_delta_frame_artifact(
    *,
    artifact_id: str,
    shape: str,
    metric_ref: str,
    current_scope: dict[str, Any],
    baseline_scope: dict[str, Any],
    axes: list[dict[str, str]],
    series: list[dict[str, Any]],
    unit: str | None,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "delta_frame",
        "shape": shape,
        "subject": {
            "kind": "comparison",
            "metric_ref": metric_ref,
            "current": current_scope,
            "baseline": baseline_scope,
        },
        "axes": axes,
        "measures": [
            {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": unit},
            {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
        ],
        "payload": {"series": series},
    }


def read_delta_scalar_point(artifact: dict[str, Any]) -> dict[str, Any]:
    series_list = artifact.get("series") or []
    if not series_list:
        series_list = read_delta_frame_series(artifact)
    if series_list:
        points = series_list[0].get("points") or []
        if points:
            return dict(points[0])
    raise ValueError("delta_frame artifact has no scalar delta point")
```

Also rename the existing `read_compare_scalar_point` to `read_delta_scalar_point`. The old function reads `delta` field; the new one reads `delta_abs`. Keep the old `read_compare_scalar_point` for now but mark it as reading from `delta_abs` instead of `delta`, since the compare output will now use `delta_abs`.

Update `read_compare_scalar_point` at line 270-288 to read `delta_abs` instead of `delta`:

```python
def read_compare_scalar_point(artifact: dict[str, Any]) -> dict[str, Any]:
    """Read the scalar delta point from a v2.0 delta_frame artifact.
    Returns the first point from the first series entry.
    """
    series_list = artifact.get("series") or []
    if series_list:
        points = series_list[0].get("points") or []
        if points:
            return dict(points[0])
    return {
        "current_value": artifact.get("current_value"),
        "baseline_value": artifact.get("baseline_value"),
        "delta_abs": artifact.get("absolute_delta"),
        "delta_pct": artifact.get("relative_delta"),
        "direction": artifact.get("direction") or "undefined",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test TESTS='tests/runtime/intents/test_metric_frame_helpers.py'`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marivo/runtime/intents/metric_frame.py tests/runtime/intents/test_metric_frame_helpers.py
git commit -m "feat: add delta_frame helpers to metric_frame.py"
```

---

### Task 5: Rewrite Compare Intent — Scalar & Time-Series Delta Output

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `tests/runtime/intents/_runner_fixtures.py`
- Modify: `tests/runtime/intents/test_compare_runner.py`

This task rewrites the scalar_delta and time_series_delta output branches in compare.py to produce delta_frame artifacts instead of compare_artifact. It does NOT add panel_delta yet (that's Task 6). It removes the panel hard-reject but raises a temporary placeholder error so existing tests don't break unexpectedly.

- [ ] **Step 1: Update runner fixtures to produce delta_frame compare artifacts**

In `tests/runtime/intents/_runner_fixtures.py`, add delta_frame helpers that mirror the current pattern but use `build_delta_frame_artifact`. These will be used by the updated compare tests.

However, since compare tests create input observe artifacts (not compare output artifacts), the fixtures for *inputs* remain the same. The test assertions on *outputs* need to change from checking `comparison_type` to `shape` and from `delta` to `delta_abs`.

No fixture changes needed for inputs. The output assertions change directly in test_compare_runner.py.

- [ ] **Step 2: Rewrite scalar_delta output branch in compare.py**

In `marivo/runtime/intents/compare.py`, update the scalar delta branch (lines 715-758) to produce delta_frame output:

Replace the `base` dict construction at line 697-709. Change `artifact_type` to use `artifact_family: "delta_frame"` instead of `artifact_type: "compare_artifact"`. Add `shape` field. Change `subject` to comparison kind. Add `comparison_side` axis. Rename `delta` to `delta_abs` in the point. Remove top-level scalar aliases.

Key changes to the `base` dict:

```python
base: dict[str, Any] = {
    "artifact_family": "delta_frame",
    "shape": "",  # filled per branch
    "schema_version": "2.0",
    "metric": metric_name,
    "subject": {
        "kind": "comparison",
        "metric_ref": left_metric,
        "current": {
            "time_scope": _read_time_scope(left_artifact),
            "scope": _read_scope(left_artifact),
        },
        "baseline": {
            "time_scope": _read_time_scope(right_artifact),
            "scope": _read_scope(right_artifact),
        },
    },
    "current_ref": current_ref_out,
    "baseline_ref": baseline_ref_out,
    "lineage": lineage,
    "resolved_input_summary": resolved_input_summary,
    "unit": left_unit,
    "comparability": comparability,
    "analytical_metadata": analytical_metadata,
    "execution_metadata": execution_metadata,
}
```

Then for scalar_delta branch:

```python
if left_effective_type == "scalar":
    current_value = _read_scalar_value(left_artifact)
    baseline_value = _read_scalar_value(right_artifact)
    abs_delta = _compute_absolute_delta(current_value, baseline_value)
    rel_delta = _compute_relative_delta(abs_delta, baseline_value)
    direction = _compute_direction(abs_delta, rel_delta, flat_tolerance_relative)
    artifact = {
        **base,
        "shape": "scalar_delta",
        "axes": [{"kind": "comparison_side"}],
        "measures": [
            {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": left_unit},
            {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
        ],
        "payload": {
            "series": [
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
            ],
        },
        "summary_current_value": current_value,
        "summary_baseline_value": baseline_value,
        "summary_absolute_delta": abs_delta,
        "summary_relative_delta": rel_delta,
        "summary_direction": direction,
    }
    artifact_name = f"{metric_name}_compare_scalar"
    summary = f"compare {metric_name} scalar: {direction} (delta_abs {abs_delta if abs_delta is not None else 'n/a'})"
```

- [ ] **Step 3: Rewrite time_series_delta output branch**

Replace the time_series_delta branch (lines 760-961). Change the `comparison_type` field to `shape: "time_series_delta"`. Add `comparison_side` to axes. Rename `delta` to `delta_abs` in each row. Move `series` into `payload.series`. Remove top-level summary aliases that duplicate series data.

The series construction for time_series_delta becomes:

```python
artifact = {
    **base,
    "shape": "time_series_delta",
    "axes": [{"kind": "time", "grain": granularity}, {"kind": "comparison_side"}],
    "measures": [
        {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": left_unit},
        {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
    ],
    "payload": {"series": [{"keys": {}, "points": time_series_rows}]},
    "coverage": coverage,
    "summary_current_value": summary_current_value,
    "summary_baseline_value": summary_baseline_value,
    "summary_absolute_delta": summary_abs,
    "summary_relative_delta": summary_rel,
    "summary_direction": summary_dir,
}
```

Each `time_series_rows` point must use `delta_abs` instead of `delta`:

```python
time_series_rows.append(
    {
        "window": window,
        "current_value": current_value,
        "baseline_value": baseline_value,
        "delta_abs": row_abs,
        "delta_pct": row_rel,
        "direction": row_dir,
        "presence": presence,
    }
)
```

- [ ] **Step 4: Rewrite segmented_delta output branch**

Replace the segmented_delta branch (lines 963-1063). Same pattern: `shape` instead of `comparison_type`, `delta_abs` instead of `delta`, `comparison_side` axis, `payload.series`:

```python
artifact = {
    **base,
    "shape": "segmented_delta",
    "axes": [{"kind": "dimension", "name": d} for d in dims] + [{"kind": "comparison_side"}],
    "measures": [
        {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": left_unit},
        {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
    ],
    "payload": {"series": segmented_series},
    "scope_current_value": scope_lv,
    "scope_baseline_value": scope_rv,
    "scope_absolute_delta": scope_abs,
    "scope_relative_delta": scope_rel,
    "scope_direction": scope_dir,
}
```

Each segmented series point must use `delta_abs`:

```python
segmented_series.append(
    {
        "keys": keys_dict,
        "points": [
            {
                "current_value": lv,
                "baseline_value": rv,
                "delta_abs": row_abs,
                "delta_pct": row_rel,
                "direction": row_dir,
                "presence": presence,
            }
        ],
    }
)
```

- [ ] **Step 5: Update commit_step_result call**

At line 1071-1082, update the `commit_step_result` call to use `"delta_frame"` as `artifact_type` instead of `"compare_artifact"`:

```python
result = commit_step_result(
    runtime,
    session_id,
    step_id,
    "compare",
    "delta_frame",
    artifact_name,
    artifact,
    summary,
    provenance=provenance,
    reasoning=reasoning,
)
```

- [ ] **Step 6: Remove panel hard-reject**

Remove lines 535-538 that raise `UNSUPPORTED_OPERATION` for panel. Instead, the panel branch will be handled in Task 6. For now, add a temporary placeholder that's not a hard-reject but still raises:

```python
if left_effective_type == "panel" or right_effective_type == "panel":
    raise ValueError(
        "compare: UNSUPPORTED_OPERATION - panel delta_frame computation will be implemented in a follow-up change"
    )
```

Wait — since panel delta is coming in Task 6 right after this, we can skip this temporary placeholder and just leave the panel validation in `_require_metric_frame_artifact` (which already allows panel shape). The error at lines 535-538 is the only place that rejects panel. We'll remove it here and add the panel_delta branch in Task 6.

Actually, remove the panel hard-reject entirely. The `_require_metric_frame_artifact` already validates panel axes (line 146-148: it returns shape="panel" with axes without calling `_validate_axes_for_shape`). We need to also validate panel axes properly:

In `_validate_axes_for_shape`, add a panel branch:

```python
if shape == "panel":
    grain = time_grain_from_axes(axes)
    if not has_time or not has_dim or grain is None:
        raise ValueError(
            "compare: INVALID_ARGUMENT - panel metric_frame requires one time axis with grain "
            "and at least one dimension axis"
        )
    return
```

And remove the special case at line 146-148 in `_require_metric_frame_artifact` that bypasses `_validate_axes_for_shape` for panel:

```python
def _require_metric_frame_artifact(
    artifact: dict[str, Any], *, label: str
) -> tuple[str, list[dict[str, str]]]:
    if not is_metric_frame_artifact(artifact):
        raise ValueError(...)
    shape = read_metric_frame_shape(artifact)
    if shape not in _SUPPORTED_METRIC_FRAME_SHAPES:
        raise ValueError(...)
    read_metric_frame_metric_ref(artifact)
    read_metric_frame_scope(artifact)
    read_metric_frame_time_scope(artifact)
    read_metric_frame_series(artifact)
    axes = read_axes_from_artifact(artifact)
    _validate_axes_for_shape(shape, axes, label=label)  # always validate, including panel
    return shape, axes
```

Remove the lines 535-538 panel hard-reject entirely.

- [ ] **Step 7: Update compare test assertions**

In `tests/runtime/intents/test_compare_runner.py`, update all assertions from the old format to the new delta_frame format. Key changes:

- `result["comparison_type"] == "scalar_delta"` → `result["shape"] == "scalar_delta"`
- `result["comparison_type"] == "time_series_delta"` → `result["shape"] == "time_series_delta"`
- `result["comparison_type"] == "segmented_delta"` → `result["shape"] == "segmented_delta"`
- `result["artifact_type"] == "compare_artifact"` → `result["artifact_family"] == "delta_frame"` (if this assertion exists)
- `result["series"][0]["points"][0]["delta"]` → `result["series"][0]["points"][0]["delta_abs"]` or `result["payload"]["series"][0]["points"][0]["delta_abs"`
- `result["absolute_delta"]` → `result["summary_absolute_delta"]`
- `result["current_value"]` → `result["payload"]["series"][0]["points"][0]["current_value"]` or `result["summary_current_value"]`
- Remove assertions on `result["direction"]` as a top-level scalar alias; check `result["summary_direction"]` or `result["payload"]["series"][0]["points"][0]["direction"]`

Update the `commit_step_result` mock assertion to check `artifact_type="delta_frame"` instead of `"compare_artifact"`.

Update `test_compare_panel_metric_frame_is_explicitly_unsupported` — this test should now be removed or renamed since panel is no longer explicitly unsupported. Replace with a test that validates panel input axes properly.

- [ ] **Step 8: Run compare tests**

Run: `make test TESTS='tests/runtime/intents/test_compare_runner.py'`

Expected: All tests pass with updated assertions.

- [ ] **Step 9: Commit**

```bash
git add marivo/runtime/intents/compare.py tests/runtime/intents/test_compare_runner.py
git commit -m "feat: rewrite compare output to delta_frame (scalar, time_series, segmented)"
```

---

### Task 6: Add Panel Delta Computation to Compare Intent

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `tests/runtime/intents/test_compare_runner.py`

This task adds the panel_delta computation branch and a test for it.

- [ ] **Step 1: Write failing test for panel delta**

Add to `tests/runtime/intents/test_compare_runner.py`:

```python
def test_compare_panel_commits_panel_delta():
    from tests.runtime.intents._runner_fixtures import (
        _make_runtime,
        _panel_observation_v2,
        _FAKE_ARTIFACT_ID,
        _SESSION,
    )

    current = _panel_observation_v2("metric.test", "day", ["country"], [
        {"keys": {"country": "US"}, "points": [
            {"window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"}, "value": 150},
            {"window": {"start": "2026-05-16T00:00:00Z", "end": "2026-05-17T00:00:00Z"}, "value": 160},
        ]},
        {"keys": {"country": "UK"}, "points": [
            {"window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"}, "value": 80},
        ]},
    ])
    baseline = _panel_observation_v2("metric.test", "day", ["country"], [
        {"keys": {"country": "US"}, "points": [
            {"window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"}, "value": 100},
            {"window": {"start": "2026-05-16T00:00:00Z", "end": "2026-05-17T00:00:00Z"}, "value": 110},
        ]},
        {"keys": {"country": "UK"}, "points": [
            {"window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"}, "value": 70},
        ]},
    ])
    runtime = _make_runtime(
        current_artifact=current, baseline_artifact=baseline
    )
    result = run_compare_intent(runtime, _SESSION, {
        "current_artifact_id": _FAKE_ARTIFACT_ID,
        "baseline_artifact_id": _FAKE_ARTIFACT_ID,
    })
    assert result["shape"] == "panel_delta"
    assert result["artifact_family"] == "delta_frame"
    assert result["axes"] == [
        {"kind": "time", "grain": "day"},
        {"kind": "dimension", "name": "country"},
        {"kind": "comparison_side"},
    ]
    # Check that series has entries for US and UK
    series = result["payload"]["series"]
    assert len(series) >= 2
    # Find US series
    us_series = [s for s in series if s["keys"].get("country") == "US"][0]
    assert us_series["points"][0]["current_value"] == 150
    assert us_series["points"][0]["baseline_value"] == 100
    assert us_series["points"][0]["delta_abs"] == 50
    assert us_series["points"][0]["delta_pct"] == 0.5
    assert us_series["points"][0]["direction"] == "increase"
    assert us_series["points"][0]["presence"] == "both"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/runtime/intents/test_compare_runner.py::test_compare_panel_commits_panel_delta'`

Expected: FAIL — panel branch not implemented yet.

- [ ] **Step 3: Implement panel_delta computation branch**

In `marivo/runtime/intents/compare.py`, add a panel_delta branch in the `run_compare_intent` function after the segmented_delta branch. The branch:

1. Extracts series entries from both artifacts keyed by dimension tuples
2. For each dimension key present in either artifact, aligns time buckets using the same pairing logic as time_series_delta
3. Computes per-bucket deltas for each series independently
4. Builds panel_delta series with keys + points
5. Computes scope-level summary across all series/buckets

```python
elif left_effective_type == "panel":
    dims = dimension_names_from_axes(left_axes)
    granularity = time_grain_from_axes(left_axes)
    left_series_entries = _read_metric_frame_series_entries(left_artifact)
    right_series_entries = _read_metric_frame_series_entries(right_artifact)

    def _panel_key(entry: dict[str, Any]) -> tuple[str, ...]:
        return tuple(str(entry.get("keys", {}).get(d)) for d in dims)

    left_panel_map = {_panel_key(s): s for s in left_series_entries}
    right_panel_map = {_panel_key(s): s for s in right_series_entries}
    all_panel_keys = set(left_panel_map) | set(right_panel_map)

    # Resolve time pairing basis using the same logic as time_series_delta
    # The bucket pairing is shared across all series (same time scope)
    pairing_basis = _resolve_time_series_pairing_basis(
        runtime=runtime,
        compare_type=compare_type,
        left_artifact=left_artifact,
        right_artifact=right_artifact,
    )
    # Extract the paired bucket keys (these are shared across all series)
    paired_bucket_keys = pairing_basis["series_keys"]
    left_ts_map = pairing_basis["left_series_map"]
    right_ts_map = pairing_basis["right_series_map"]

    matched_current_total: float | None = None
    matched_baseline_total: float | None = None
    matched_current_values: list[float] = []
    matched_baseline_values: list[float] = []

    for panel_key in sorted(all_panel_keys):
        l_entry = left_panel_map.get(panel_key)
        r_entry = right_panel_map.get(panel_key)

        if l_entry and r_entry:
            # Both sides present: align time buckets within this series pair
            l_points = l_entry.get("points") or []
            r_points = r_entry.get("points") or []
            keys_dict = l_entry.get("keys") or {}

            # Build per-series point maps by window start
            l_by_start = {}
            for p in l_points:
                w = p.get("window") or {}
                start = str(w.get("start") or "")
                if start:
                    l_by_start[start] = p
            r_by_start = {}
            for p in r_points:
                w = p.get("window") or {}
                start = str(w.get("start") or "")
                if start:
                    r_by_start[start] = p

            series_delta_points: list[dict[str, Any]] = []
            for bucket_key in paired_bucket_keys:
                # The paired_bucket_keys are "{start}|{end}" strings from the
                # global time-series pairing. Extract the start to find matching
                # points in each panel series.
                bucket_start = bucket_key.split("|")[0]
                l_point = l_by_start.get(bucket_start)
                r_point = r_by_start.get(bucket_start)

                anchor = left_ts_map.get(bucket_key) or right_ts_map.get(bucket_key) or {}
                window = dict(anchor.get("window") or {})
                current_value = _coerce_numeric_or_none(l_point.get("value")) if l_point else None
                baseline_value = _coerce_numeric_or_none(r_point.get("value")) if r_point else None

                if l_point and r_point and current_value is not None and baseline_value is not None:
                    presence = "both"
                    row_abs = _compute_absolute_delta(current_value, baseline_value)
                    row_rel = _compute_relative_delta(row_abs, baseline_value)
                    row_dir = _compute_direction(row_abs, row_rel, flat_tolerance_relative)
                    matched_current_values.append(current_value)
                    matched_baseline_values.append(baseline_value)
                elif current_value is not None:
                    presence = "current_only"
                    row_abs = current_value
                    row_rel = None
                    row_dir = "undefined"
                elif baseline_value is not None:
                    presence = "baseline_only"
                    row_abs = -baseline_value if baseline_value is not None else None
                    row_rel = None
                    row_dir = "undefined"
                else:
                    presence = "current_only" if l_point else "baseline_only"
                    row_abs = None
                    row_rel = None
                    row_dir = "undefined"

                series_delta_points.append({
                    "window": window,
                    "current_value": current_value,
                    "baseline_value": baseline_value,
                    "delta_abs": row_abs,
                    "delta_pct": row_rel,
                    "direction": row_dir,
                    "presence": presence,
                })

            panel_series.append({"keys": keys_dict, "points": series_delta_points})
        elif l_entry:
            # Current-only series
            keys_dict = l_entry.get("keys") or {}
            series_delta_points = [
                {
                    "window": point.get("window"),
                    "current_value": _coerce_numeric_or_none(point.get("value")),
                    "baseline_value": None,
                    "delta_abs": _coerce_numeric_or_none(point.get("value")),
                    "delta_pct": None,
                    "direction": "undefined",
                    "presence": "current_only",
                }
                for point in (l_entry.get("points") or [])
            ]
            panel_series.append({"keys": keys_dict, "points": series_delta_points})
        else:
            # Baseline-only series
            keys_dict = (r_entry or {}).get("keys") or {}
            series_delta_points = [
                {
                    "window": point.get("window"),
                    "current_value": None,
                    "baseline_value": _coerce_numeric_or_none(point.get("value")),
                    "delta_abs": -_coerce_numeric_or_none(point.get("value")) if point.get("value") is not None else None,
                    "delta_pct": None,
                    "direction": "undefined",
                    "presence": "baseline_only",
                }
                for point in ((r_entry or {}).get("points") or [])
            ]
            panel_series.append({"keys": keys_dict, "points": series_delta_points})

    # Sort by descending non-null point count, then by dimension keys
    panel_series.sort(
        key=lambda item: (
            -(sum(1 for p in item["points"] if p.get("delta_abs") is not None)),
            *[str(item["keys"].get(dim, "")) for dim in dims],
        )
    )

    # Compute scope-level summary
    matched_current_values = []
    matched_baseline_values = []
    for entry in panel_series:
        for point in entry.get("points") or []:
            if point.get("presence") == "both" and point.get("current_value") is not None and point.get("baseline_value") is not None:
                matched_current_values.append(point["current_value"])
                matched_baseline_values.append(point["baseline_value"])

    summary_current_value = sum(matched_current_values) if matched_current_values else None
    summary_baseline_value = sum(matched_baseline_values) if matched_baseline_values else None
    summary_abs = _compute_absolute_delta(summary_current_value, summary_baseline_value)
    summary_rel = _compute_relative_delta(summary_abs, summary_baseline_value)
    summary_dir = _compute_direction(summary_abs, summary_rel, flat_tolerance_relative)

    panel_axes = [{"kind": "time", "grain": granularity}] + [{"kind": "dimension", "name": d} for d in dims] + [{"kind": "comparison_side"}]
    artifact = {
        **base,
        "shape": "panel_delta",
        "axes": panel_axes,
        "measures": [
            {"id": "delta_abs", "value_type": "number", "nullable": True, "unit": left_unit},
            {"id": "delta_pct", "value_type": "number", "nullable": True, "unit": None},
        ],
        "payload": {"series": panel_series},
        "summary_current_value": summary_current_value,
        "summary_baseline_value": summary_baseline_value,
        "summary_absolute_delta": summary_abs,
        "summary_relative_delta": summary_rel,
        "summary_direction": summary_dir,
    }
    artifact_name = f"{metric_name}_compare_panel"
    summary = f"compare {metric_name} panel: {len(panel_series)} series deltas"
```

Note: The per-series time-aligned pairing is the critical logic. For each series that exists in both sides, we need to align the time buckets within that series. The simplest approach is to use relative position pairing within each series (same as time_series_delta normal mode). For calendar-aligned modes, the pairing resolution from `_resolve_time_series_pairing_basis` is shared across all series since they cover the same time scope.

The key implementation detail is: for each series pair (l_entry, r_entry), extract their points and pair them using the same relative position or calendar-aligned pairing logic. This can reuse `_relative_position_pairing_basis` applied to the per-series points rather than the overall time_series points.

- [ ] **Step 4: Run test to verify it passes**

Run: `make test TESTS='tests/runtime/intents/test_compare_runner.py::test_compare_panel_commits_panel_delta'`

Expected: PASS

- [ ] **Step 5: Run full compare test suite**

Run: `make test TESTS='tests/runtime/intents/test_compare_runner.py'`

Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/compare.py tests/runtime/intents/test_compare_runner.py
git commit -m "feat: add panel_delta computation to compare intent"
```

---

### Task 7: Update Decompose Intent — Read Delta Frame

**Files:**
- Modify: `marivo/runtime/intents/decompose.py`
- Modify: `tests/runtime/intents/test_decompose_runner.py`

- [ ] **Step 1: Update decompose test compare input dicts**

In `tests/runtime/intents/test_decompose_runner.py`, update all hand-crafted compare_artifact dicts to delta_frame format. Key changes per test:

- Replace `"comparison_type": "scalar_delta"` with `"shape": "scalar_delta"`
- Replace `"comparison_type": "time_series_delta"` with `"shape": "time_series_delta"`
- Add `"artifact_family": "delta_frame"` to the dict
- Replace `"delta"` field in points with `"delta_abs"`
- Add `"comparison_side"` to axes
- Add `"subject"` with comparison kind
- Move `"series"` into `"payload": {"series": ...}`

For example, `_normalize_decompose_compare_input` tests currently pass dicts like:

```python
{"comparison_type": "scalar_delta", "schema_version": "2.0", ...}
```

These become:

```python
{"artifact_family": "delta_frame", "shape": "scalar_delta", "schema_version": "2.0", ...}
```

- [ ] **Step 2: Update _normalize_decompose_compare_input in decompose.py**

In `marivo/runtime/intents/decompose.py`, update `_normalize_decompose_compare_input()` (around line 356):

1. Replace `comparison_type = compare_artifact.get("comparison_type", "")` with `shape = read_delta_frame_shape(compare_artifact)` (or fall back to `compare_artifact.get("shape", "")`)
2. Replace all `comparison_type` references with `shape`
3. Replace `"delta"` field reads with `"delta_abs"`
4. The axes-based inference fallback should check `shape` instead of `comparison_type`
5. Accept `"panel_delta"` as valid input

The dispatch logic becomes:

```python
shape = compare_artifact.get("shape", "")
if not shape:
    # Infer from axes (robustness fallback)
    axes = read_axes_from_artifact(compare_artifact)
    has_time = has_time_axis(axes)
    has_dim = has_dimension_axis(axes)
    if has_time and has_dim:
        shape = "panel_delta"
    elif has_time:
        shape = "time_series_delta"
    elif has_dim:
        shape = "segmented_delta"
    else:
        shape = "scalar_delta"

if shape == "scalar_delta":
    # ... read from series/points using delta_abs
elif shape == "time_series_delta":
    # ... read from series/points using delta_abs
elif shape == "panel_delta":
    # ... read from series/points using delta_abs, compute per-series decomposition
elif shape == "segmented_delta":
    raise ValueError("decompose: INVALID_ARGUMENT - ...")
```

- [ ] **Step 3: Update compare_ref dict in decompose output**

In the decompose output, update `compare_ref_out` (around line 262-268) to use `shape` instead of `comparison_type`:

```python
compare_ref_out = {
    "step_id": str(compare_step_id),
    "artifact_id": compare_artifact_id,
    "comparison_type": compare_artifact.get("shape", "scalar_delta"),  # transition: keep comparison_type as alias for now
    "shape": compare_artifact.get("shape", "scalar_delta"),
}
```

- [ ] **Step 4: Run decompose tests**

Run: `make test TESTS='tests/runtime/intents/test_decompose_runner.py'`

Expected: All pass

- [ ] **Step 5: Run decompose strategies tests**

Run: `make test TESTS='tests/runtime/intents/test_decompose_strategies.py'`

Expected: All pass (these test pure strategy functions, not affected by delta_frame changes)

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/decompose.py tests/runtime/intents/test_decompose_runner.py
git commit -m "feat: update decompose intent to read delta_frame artifacts"
```

---

### Task 8: Update Attribute & Diagnose Intents

**Files:**
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `tests/runtime/intents/test_attribute_runner.py`
- Modify: `marivo/runtime/intents/diagnose.py`
- Modify: `tests/runtime/intents/test_diagnose_runner.py`

- [ ] **Step 1: Update attribute.py**

In `marivo/runtime/intents/attribute.py`:

1. Update `compare_ref` dict (around line 225-230) to write `"shape": "scalar_delta"` alongside or instead of `"comparison_type": "scalar_delta"`
2. Update the `_compare_result` helper to produce delta_frame format: add `"artifact_family": "delta_frame"`, `"shape": "scalar_delta"`, `"axes": [{"kind": "comparison_side"}]`, `"subject": {"kind": "comparison", ...}`, `"payload": {"series": ...}`, use `"delta_abs"` instead of `"delta"`/`"absolute_delta"` in points
3. Update `read_compare_scalar_point` usage to read `"delta_abs"` instead of `"delta"`

- [ ] **Step 2: Update attribute tests**

In `tests/runtime/intents/test_attribute_runner.py`, update the `_compare_result` helper to produce delta_frame format. Update assertions that read `comparison_type` to read `shape`.

- [ ] **Step 3: Run attribute tests**

Run: `make test TESTS='tests/runtime/intents/test_attribute_runner.py'`

Expected: All pass

- [ ] **Step 4: Update diagnose.py**

In `marivo/runtime/intents/diagnose.py`, around line 635:

1. Update the `attribution_comparison` dict to write `"shape": "scalar_delta"` instead of `"comparison_type": "scalar_delta"`
2. Update any compare mock results in the diagnose test helpers to produce delta_frame format

- [ ] **Step 5: Update diagnose tests**

In `tests/runtime/intents/test_diagnose_runner.py`, update mock compare results to produce delta_frame format. Specifically, any dicts with `"comparison_type": "scalar_delta"` should use `"shape": "scalar_delta"` and `"artifact_family": "delta_frame"`, and points should use `"delta_abs"` instead of `"delta"`.

- [ ] **Step 6: Run diagnose tests**

Run: `make test TESTS='tests/runtime/intents/test_diagnose_runner.py'`

Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add marivo/runtime/intents/attribute.py tests/runtime/intents/test_attribute_runner.py marivo/runtime/intents/diagnose.py tests/runtime/intents/test_diagnose_runner.py
git commit -m "feat: update attribute and diagnose intents to consume delta_frame"
```

---

### Task 9: Update Evidence Extractors

**Files:**
- Modify: `marivo/runtime/evidence/compare_extractor.py`
- Modify: `tests/runtime/evidence/test_compare_decompose_extractor.py`
- Modify: `marivo/runtime/evidence/decompose_extractor.py`
- Modify: `marivo/runtime/evidence/proposition_seeding.py`
- Modify: `tests/core/test_evidence_proposition_seeding.py` (if affected)

- [ ] **Step 1: Update compare_extractor.py**

In `marivo/runtime/evidence/compare_extractor.py`:

1. Replace `comparison_type` dispatch with `shape` dispatch
2. Replace `"delta"` reads with `"delta_abs"`
3. Add `"panel_delta"` extraction branch that creates 1 finding per series per time bucket (similar to segmented_delta but with window/presence fields)
4. Keep the extraction function signature unchanged — it still takes a dict payload

- [ ] **Step 2: Update extractor test payloads**

In `tests/runtime/evidence/test_compare_decompose_extractor.py`, update all `_scalar_delta_payload()`, `_segmented_delta_payload()`, `_time_series_delta_payload()` helpers to produce delta_frame format:

- Add `"artifact_family": "delta_frame"` and `"shape": ...` to each
- Replace `"comparison_type"` with `"shape"`
- Replace `"delta"` in points with `"delta_abs"`
- Add `"comparison_side"` to axes
- Add `"subject"` with comparison kind
- Move `"series"` into `"payload": {"series": ...}`

- [ ] **Step 3: Add panel_delta extractor tests**

Add a new `TestComparePanelDelta` class in the extractor test file:

```python
class TestComparePanelDelta(unittest.TestCase):
    def _panel_delta_payload(self):
        return {
            "artifact_family": "delta_frame",
            "shape": "panel_delta",
            "axes": [
                {"kind": "time", "grain": "day"},
                {"kind": "dimension", "name": "country"},
                {"kind": "comparison_side"},
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
            "summary_current_value": 230,
            "summary_baseline_value": 170,
            "summary_absolute_delta": 60,
        }

    def test_two_series_produce_findings(self):
        # Each series gets a scope-level finding
        findings = extract_compare_findings(self._panel_delta_payload(), "art_test")
        assert len(findings) >= 2  # At minimum, 1 per series

    def test_delta_abs_propagated(self):
        findings = extract_compare_findings(self._panel_delta_payload(), "art_test")
        us_finding = [f for f in findings if f.payload.get("keys", {}).get("country") == "US"][0]
        assert us_finding.payload["delta_abs"] == 50
```

- [ ] **Step 4: Update decompose_extractor.py**

In `marivo/runtime/evidence/decompose_extractor.py`, update the `compare_ref.comparison_type` reads to use `compare_ref.shape`. Around lines 144-166:

```python
comparison_shape = compare_ref.get("shape") or compare_ref.get("comparison_type", "")
if comparison_shape == "time_series_delta":
    delta_collection = "summary"
elif comparison_shape in ("", "scalar_delta"):
    delta_collection = "result"
else:
    raise ValueError(f"Unsupported compare shape: {comparison_shape}")
```

- [ ] **Step 5: Update proposition_seeding.py**

In `marivo/runtime/evidence/proposition_seeding.py`, update the `delta_kind` → `change_kind` mapping. The `delta_kind` is derived from `shape` instead of `comparison_type`. Add `"panel_delta"` → `"panel_change"`:

```python
change_kind_map = {
    "scalar_delta": "scalar_change",
    "segmented_delta": "segment_change",
    "time_series_delta": None,  # no proposition for time-series bucket deltas
    "panel_delta": "panel_change",
}
```

- [ ] **Step 6: Run extractor tests**

Run: `make test TESTS='tests/runtime/evidence/test_compare_decompose_extractor.py'`

Expected: All pass

- [ ] **Step 7: Run proposition seeding tests**

Run: `make test TESTS='tests/core/test_evidence_proposition_seeding.py'`

Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add marivo/runtime/evidence/compare_extractor.py marivo/runtime/evidence/decompose_extractor.py marivo/runtime/evidence/proposition_seeding.py tests/runtime/evidence/test_compare_decompose_extractor.py
git commit -m "feat: update evidence extractors to read delta_frame format"
```

---

### Task 10: Update AOI Projection — Delta Frame Fast Path & Panel Delta Projection

**Files:**
- Modify: `marivo/contracts/aoi_projection.py`
- Test: `tests/contracts/test_aoi_runtime_contract.py` (add projection tests)

- [ ] **Step 1: Add delta_frame fast path in project_aoi_artifact**

In `marivo/contracts/aoi_projection.py`, in `project_aoi_artifact()` (lines 287-323), add a fast path for delta_frame after the observe fast path:

```python
if (
    intent_type == "compare"
    and isinstance(raw, dict)
    and raw.get("artifact_family") == "delta_frame"
):
    return artifact_to_envelope_result(validate_aoi_artifact(raw))
```

- [ ] **Step 2: Update project_aoi_artifact_result compare branch**

In `project_aoi_artifact_result()` (lines 157-206), replace `comparison_type` dispatch with `shape` dispatch:

```python
if intent_type == "compare":
    shape = payload.get("shape")
    if shape is None and {"current_value", "baseline_value", "delta_abs"} & set(payload):
        shape = "scalar_delta"
    matched_time_scope = _as_aoi_time_scope(
        (payload.get("analytical_metadata") or {}).get("matched_time_scope")
    )
    series_list = payload.get("payload", {}).get("series") or payload.get("series") or []

    if shape == "time_series_delta":
        ts_points = series_list[0].get("points") or [] if series_list else []
        compare_result = aoi.TimeSeriesDeltaResult(...)
    elif shape == "segmented_delta":
        compare_result = aoi.SegmentedDeltaResult(...)
    elif shape == "panel_delta":
        # Panel delta: project each series entry
        compare_result = _project_panel_delta(series_list, matched_time_scope)
    else:
        # scalar_delta
        compare_result = aoi.ScalarDeltaResult(
            current_value=payload.get("summary_current_value") or (series_list[0]["points"][0].get("current_value") if series_list else None),
            baseline_value=payload.get("summary_baseline_value") or (series_list[0]["points"][0].get("baseline_value") if series_list else None),
            delta=payload.get("summary_absolute_delta") or (series_list[0]["points"][0].get("delta_abs") if series_list else None),
            matched_time_scope=matched_time_scope,
        )
    return compare_result.model_dump(mode="json")
```

Note: The AOI v0.2 result classes (`ScalarDeltaResult`, `TimeSeriesDeltaResult`, `SegmentedDeltaResult`) don't have a panel variant yet. The `_project_panel_delta` function needs to decide what to return. Since the v0.2 spec doesn't define a `PanelDeltaResult`, we need to either:
- Create a new result class in the schema (requires updating aoi.schema.json and regenerating)
- Or project panel_delta through the fast path only (skip the `project_aoi_artifact_result` legacy projection for panel)

For now, add the fast path and let panel_delta artifacts be projected through `validate_aoi_artifact` + `artifact_to_envelope_result`. The `project_aoi_artifact_result` legacy path only needs to handle scalar/time_series/segmented delta for backward compatibility with any remaining non-envelope compare payloads.

- [ ] **Step 3: Update _infer_intent_type**

In `_infer_intent_type()` (lines 326-350), update the compare detection to also recognize `artifact_family == "delta_frame"`:

```python
if artifact_type == "delta_frame" or comparison_type is not None or payload.get("shape") in ("scalar_delta", "time_series_delta", "segmented_delta", "panel_delta"):
    return "compare"
```

- [ ] **Step 4: Write and run projection tests**

Add to `tests/contracts/test_aoi_runtime_contract.py`:

```python
def test_project_aoi_artifact_produces_delta_frame_envelope():
    from marivo.contracts.aoi_projection import project_aoi_artifact

    delta_frame = {
        "artifact_id": "art_delta_test",
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "subject": {...},
        "axes": [{"kind": "comparison_side"}],
        "measures": [{"id": "delta_abs", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"current_value": 10.0, "baseline_value": 5.0, "delta_abs": 5.0, "delta_pct": 1.0, "direction": "increase"}]}]},
    }
    result = project_aoi_artifact("compare", "art_delta_test", {"result": delta_frame})
    assert result["artifact_family"] == "delta_frame"
```

Run: `make test TESTS='tests/contracts/'`

Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add marivo/contracts/aoi_projection.py tests/contracts/test_aoi_runtime_contract.py
git commit -m "feat: add delta_frame fast path and panel_delta projection to AOI projection"
```

---

### Task 11: Full Test Suite Validation

**Files:** None — purely validation

- [ ] **Step 1: Run full runtime intent tests**

Run: `make test TESTS='tests/runtime/intents/'`

Expected: All pass

- [ ] **Step 2: Run full evidence tests**

Run: `make test TESTS='tests/runtime/evidence/'`

Expected: All pass

- [ ] **Step 3: Run full contract tests**

Run: `make test TESTS='tests/contracts/'`

Expected: All pass

- [ ] **Step 4: Run full core tests**

Run: `make test TESTS='tests/core/'`

Expected: All pass

- [ ] **Step 5: Run typecheck**

Run: `make typecheck`

Expected: No new type errors

- [ ] **Step 6: Run lint**

Run: `make lint`

Expected: No new lint errors

- [ ] **Step 7: Commit (if any fixes needed)**

If any fixes were needed during validation, commit them. Otherwise skip this step.

---

### Task 12: Update Report Module (If It Reads comparison_type)

**Files:**
- Modify: `marivo/runtime/report.py` (if it references `comparison_type`)

- [ ] **Step 1: Check if report.py reads comparison_type**

Search `report.py` for `"comparison_type"` references. If found, update to also read `"shape"`:

```python
# In report metadata summary construction:
comparison_type = content_dict.get("comparison_type") or content_dict.get("shape") or ""
```

- [ ] **Step 2: Run report-related tests if modified**

Run: `make test TESTS='tests/runtime/test_report.py'` (if this test file exists)

- [ ] **Step 3: Commit**

```bash
git add marivo/runtime/report.py
git commit -m "feat: update report module to read delta_frame shape field"
```