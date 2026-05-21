# Observe Metric Frame Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AOI `observe` successful output a top-level `metric_frame` artifact with unified `payload.series` data.

**Architecture:** Update the AOI schema first, regenerate generated Pydantic models, then change observe runtime output to build the new artifact contract. Evidence extraction and existing intent consumers get narrow readers for the new shape so the rest of the runtime can continue working without defining new downstream artifact families.

**Tech Stack:** JSON Schema/YAML AOI spec, datamodel-code-generator Pydantic v2 models, Marivo Python runtime, pytest via `make test`, mypy/ruff via `make typecheck` and `make lint`.

---

## File Structure

- Modify `aoi-spec/schema/aoi.schema.yaml`: readable AOI contract sketch for `metric_frame`.
- Modify `aoi-spec/schema/aoi.schema.json`: canonical machine schema for `MetricFrameArtifact`.
- Modify `aoi-spec/examples/observe/*-success.json`: observe success examples using top-level `metric_frame`.
- Modify `marivo/contracts/generated/aoi.py`: regenerated Pydantic models.
- Modify `marivo/contracts/aoi_runtime.py`: accept generated `MetricFrameArtifact` in AOI artifact validation helpers.
- Modify `marivo/runtime/intents/metric_frame.py`: central builders/readers for `MetricFrameArtifact`.
- Modify `marivo/runtime/intents/observe.py`: emit the new top-level contract and commit it as artifact type `metric_frame`.
- Modify `marivo/core/evidence/finding_extraction.py`: extract observation findings from `metric_frame` payload.
- Modify `marivo/runtime/evidence/observe_extractor.py`: register the extractor for `metric_frame`.
- Modify narrow runtime consumers that currently read observe artifacts directly:
  `marivo/runtime/intents/compare.py`, `marivo/runtime/intents/attribute.py`,
  `marivo/runtime/intents/correlate.py`, and `marivo/runtime/intents/forecast.py`.
  These changes should only read `metric_frame`; they must not define new downstream output families.
- Modify focused tests under `tests/contracts/`, `tests/runtime/intents/`, `tests/runtime/evidence/`, and `tests/core/`.

---

### Task 1: AOI Schema Contract

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.yaml`
- Modify: `aoi-spec/schema/aoi.schema.json`
- Modify: `aoi-spec/examples/observe/scalar-success.json`
- Modify: `aoi-spec/examples/observe/time-series-success.json`
- Modify: `aoi-spec/examples/observe/segmented-success.json`
- Create: `aoi-spec/examples/observe/panel-success.json`
- Test: `tests/contracts/test_aoi_runtime_contract.py`

- [ ] **Step 1: Add failing contract tests for MetricFrameArtifact**

Add these tests near the existing `validate_aoi_artifact` tests in `tests/contracts/test_aoi_runtime_contract.py`:

```python
def _metric_frame_payload(shape: str = "scalar") -> dict[str, object]:
    time_scope = _time_scope_payload()
    axes: list[dict[str, str]] = []
    series: list[dict[str, object]] = [{"keys": {}, "points": [{"value": 42.0}]}]
    if shape == "time_series":
        axes = [{"kind": "time", "grain": "day"}]
        series = [
            {
                "keys": {},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-02T00:00:00Z",
                        },
                        "value": 42.0,
                    }
                ],
            }
        ]
    return {
        "artifact_id": "art_observe_1",
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.view_time",
            "time_scope": time_scope,
            "scope": {},
        },
        "axes": axes,
        "measures": [
            {
                "id": "value",
                "value_type": "number",
                "nullable": True,
                "unit": None,
            }
        ],
        "payload": {"series": series},
    }


def test_validate_aoi_artifact_accepts_metric_frame_artifact() -> None:
    artifact = validate_aoi_artifact(_metric_frame_payload())

    assert isinstance(artifact, aoi.MetricFrameArtifact)
    assert artifact.artifact_family == "metric_frame"
    assert artifact.shape == "scalar"
    assert artifact.subject.scope == {}


def test_artifact_to_envelope_result_keeps_metric_frame_top_level() -> None:
    artifact = validate_aoi_artifact(_metric_frame_payload("time_series"))
    result = artifact_to_envelope_result(artifact)

    assert result["artifact_id"] == "art_observe_1"
    assert result["artifact_family"] == "metric_frame"
    assert result["shape"] == "time_series"
    assert "result" not in result
    assert result["payload"]["series"][0]["points"][0]["window"]["start"] == (
        "2026-01-01T00:00:00Z"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"artifact_family": "observation"},
        {"shape": "unknown"},
        {"subject": {"kind": "metric", "metric_ref": "metric.view_time", "time_scope": _time_scope_payload()}},
        {"payload": {"series": [{"keys": {}, "points": [{"window": None, "value": 42.0}]}]}},
        {"measures": [{"id": "count", "value_type": "number", "nullable": True}]},
    ],
)
def test_metric_frame_artifact_rejects_invalid_contract(patch: dict[str, object]) -> None:
    payload = _metric_frame_payload()
    _merge_patch(payload, patch)

    with pytest.raises(ValidationError):
        aoi.MetricFrameArtifact.model_validate(payload)
```

- [ ] **Step 2: Run the focused contract test and verify it fails**

Run:

```bash
make test TESTS=tests/contracts/test_aoi_runtime_contract.py
```

Expected: failure because `aoi.MetricFrameArtifact` does not exist and `validate_aoi_artifact` does not accept the new top-level shape.

- [ ] **Step 3: Update the readable AOI schema sketch**

In `aoi-spec/schema/aoi.schema.yaml`, replace the observe result section with this sketch:

```yaml
# Observe metric frame artifact

# Successful artifact returned by observe.
metric_frame_artifact:
  artifact_id: string
  artifact_family: metric_frame
  shape: scalar | time_series | segmented | panel
  subject:
    kind: metric
    metric_ref: string
    time_scope: time_scope
    scope: object
  axes:
    - kind: time | dimension
      grain: time_granularities
      name: string
  measures:
    - id: value
      value_type: number
      nullable: true
      unit: string
  payload:
    series:
      - keys: object
        points:
          - window:
              start: iso8601_datetime
              end: iso8601_datetime
            value: number
```

Also change the observe comment to:

```yaml
# Observe a metric as a metric_frame artifact. Shape is derived from optional
# granularity and dimensions.
```

- [ ] **Step 4: Update the canonical JSON Schema**

In `aoi-spec/schema/aoi.schema.json`, add `MetricFrameArtifact` and supporting definitions under `$defs.artifacts`. The exact schema body should be:

```json
"MetricFrameArtifact": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "artifact_id",
    "artifact_family",
    "shape",
    "subject",
    "axes",
    "measures",
    "payload"
  ],
  "properties": {
    "artifact_id": { "type": "string", "minLength": 1 },
    "artifact_family": { "const": "metric_frame" },
    "shape": {
      "enum": ["scalar", "time_series", "segmented", "panel"]
    },
    "subject": { "$ref": "#/$defs/artifacts/MetricFrameSubject" },
    "axes": {
      "type": "array",
      "items": { "$ref": "#/$defs/artifacts/MetricFrameAxis" }
    },
    "measures": {
      "type": "array",
      "minItems": 1,
      "maxItems": 1,
      "items": { "$ref": "#/$defs/artifacts/MetricFrameMeasure" }
    },
    "payload": { "$ref": "#/$defs/artifacts/MetricFramePayload" }
  }
},
"MetricFrameSubject": {
  "type": "object",
  "additionalProperties": false,
  "required": ["kind", "metric_ref", "time_scope", "scope"],
  "properties": {
    "kind": { "const": "metric" },
    "metric_ref": { "type": "string", "minLength": 1 },
    "time_scope": { "$ref": "#/$defs/primitives/TimeScope" },
    "scope": {
      "type": "object",
      "additionalProperties": true
    }
  }
},
"MetricFrameAxis": {
  "oneOf": [
    {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "grain"],
      "properties": {
        "kind": { "const": "time" },
        "grain": { "$ref": "#/$defs/primitives/TimeGranularity" }
      }
    },
    {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "name"],
      "properties": {
        "kind": { "const": "dimension" },
        "name": { "type": "string", "minLength": 1 }
      }
    }
  ]
},
"MetricFrameMeasure": {
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "value_type", "nullable"],
  "properties": {
    "id": { "const": "value" },
    "value_type": { "const": "number" },
    "nullable": { "const": true },
    "unit": {
      "anyOf": [{ "type": "string" }, { "type": "null" }]
    }
  }
},
"MetricFramePayload": {
  "type": "object",
  "additionalProperties": false,
  "required": ["series"],
  "properties": {
    "series": {
      "type": "array",
      "items": { "$ref": "#/$defs/artifacts/MetricFrameSeries" }
    }
  }
},
"MetricFrameSeries": {
  "type": "object",
  "additionalProperties": false,
  "required": ["keys", "points"],
  "properties": {
    "keys": {
      "type": "object",
      "additionalProperties": {
        "anyOf": [
          { "type": "string" },
          { "type": "number" },
          { "type": "boolean" },
          { "type": "null" }
        ]
      }
    },
    "points": {
      "type": "array",
      "items": { "$ref": "#/$defs/artifacts/MetricFramePoint" }
    }
  }
},
"MetricFramePoint": {
  "type": "object",
  "additionalProperties": false,
  "required": ["value"],
  "properties": {
    "window": { "$ref": "#/$defs/artifacts/MetricFrameWindow" },
    "value": { "$ref": "#/$defs/primitives/NumberOrNull" }
  }
},
"MetricFrameWindow": {
  "type": "object",
  "additionalProperties": false,
  "required": ["start", "end"],
  "properties": {
    "start": { "$ref": "#/$defs/primitives/ISO8601" },
    "end": { "$ref": "#/$defs/primitives/ISO8601" }
  }
}
```

Then update `$defs.artifacts.Artifact.properties.result.anyOf` by removing the three observe
result refs. Do not add `MetricFrameArtifact` to the nested `Artifact.result` union: successful
observe artifacts are top-level `MetricFrameArtifact` objects, not `{artifact_id, result:
MetricFrameArtifact}` envelopes. Leave the existing non-observe result refs in place.

- [ ] **Step 5: Update observe success examples**

Write the scalar example in `aoi-spec/examples/observe/scalar-success.json` as:

```json
{
  "artifact_id": "art_observe_scalar",
  "artifact_family": "metric_frame",
  "shape": "scalar",
  "subject": {
    "kind": "metric",
    "metric_ref": "metric.view_time",
    "time_scope": {
      "field": "event_time",
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-01-02T00:00:00Z"
    },
    "scope": {}
  },
  "axes": [],
  "measures": [
    { "id": "value", "value_type": "number", "nullable": true, "unit": null }
  ],
  "payload": {
    "series": [{ "keys": {}, "points": [{ "value": 42.0 }] }]
  }
}
```

Apply the same top-level structure to the time-series and segmented examples. Add `panel-success.json` with `shape: "panel"`, axes `[{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "platform"}]`, and two series keyed by `platform`.

- [ ] **Step 6: Regenerate generated contract models**

Run:

```bash
.venv/bin/python scripts/generate_contract_models.py
```

Expected: `marivo/contracts/generated/aoi.py` is rewritten and contains `MetricFrameArtifact`, `MetricFrameSubject`, `MetricFrameAxis`, `MetricFramePayload`, and `MetricFramePoint`.

- [ ] **Step 7: Update AOI runtime helper types**

In `marivo/contracts/aoi_runtime.py`, change:

```python
AoiArtifact: TypeAlias = aoi.Artifact1 | aoi.Artifact2
```

to:

```python
AoiArtifact: TypeAlias = aoi.MetricFrameArtifact | aoi.Artifact1 | aoi.Artifact2
```

Update `validate_aoi_artifact`:

```python
def validate_aoi_artifact(value: Any) -> AoiArtifact:
    if isinstance(value, (aoi.MetricFrameArtifact, aoi.Artifact1, aoi.Artifact2)):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, Mapping) and value.get("artifact_family") == "metric_frame":
        return aoi.MetricFrameArtifact.model_validate(value)
    if not isinstance(value, Mapping):
        return aoi.Artifact2.model_validate(value)
    if "result" in value and "failure" not in value:
        _CanonicalSuccessArtifactShape.model_validate(value)
        return aoi.Artifact1.model_validate(value)
    if "failure" in value and "result" not in value:
        _CanonicalFailureArtifactShape.model_validate(value)
        return aoi.Artifact2.model_validate(value)
    raise ValidationError.from_exception_data(
        "AoiArtifact",
        [
            {
                "type": "value_error",
                "loc": (),
                "input": value,
                "ctx": {"error": ValueError("invalid AOI artifact shape")},
            }
        ],
    )
```

Update `artifact_to_envelope_result`:

```python
def artifact_to_envelope_result(artifact: AoiArtifact) -> dict[str, Any]:
    data = artifact.model_dump(mode="json")
    if data.get("artifact_family") == "metric_frame":
        return data
    if data.get("result") is None:
        data.pop("result", None)
    if data.get("failure") is None:
        data.pop("failure", None)
    return data
```

- [ ] **Step 8: Run contract tests**

Run:

```bash
make test TESTS=tests/contracts/test_aoi_runtime_contract.py
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Run:

```bash
git add aoi-spec/schema/aoi.schema.yaml aoi-spec/schema/aoi.schema.json aoi-spec/examples/observe marivo/contracts/generated/aoi.py marivo/contracts/aoi_runtime.py tests/contracts/test_aoi_runtime_contract.py
git commit -m "feat: add AOI metric frame artifact contract" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

### Task 2: Runtime MetricFrame Builders

**Files:**
- Modify: `marivo/runtime/intents/metric_frame.py`
- Test: `tests/runtime/intents/test_metric_frame.py`

- [ ] **Step 1: Add failing tests for builders and readers**

Append to `tests/runtime/intents/test_metric_frame.py`:

```python
def test_build_metric_frame_artifact_scalar_contract() -> None:
    from marivo.runtime.intents.metric_frame import build_metric_frame_artifact

    artifact = build_metric_frame_artifact(
        artifact_id="art_1",
        shape="scalar",
        metric_ref="metric.view_time",
        time_scope={"field": "event_time", "start": "2026-01-01", "end": "2026-01-02"},
        scope={},
        axes=[],
        series=[{"keys": {}, "points": [{"value": 42.0}]}],
        unit=None,
    )

    assert artifact == {
        "artifact_id": "art_1",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.view_time",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01",
                "end": "2026-01-02",
            },
            "scope": {},
        },
        "axes": [],
        "measures": [
            {"id": "value", "value_type": "number", "nullable": True, "unit": None}
        ],
        "payload": {"series": [{"keys": {}, "points": [{"value": 42.0}]}]},
    }


def test_read_metric_frame_shape_and_series() -> None:
    from marivo.runtime.intents.metric_frame import (
        read_metric_frame_series,
        read_metric_frame_shape,
    )

    artifact = {
        "artifact_family": "metric_frame",
        "shape": "time_series",
        "payload": {"series": [{"keys": {}, "points": [{"value": 1.0}]}]},
    }

    assert read_metric_frame_shape(artifact) == "time_series"
    assert read_metric_frame_series(artifact) == [{"keys": {}, "points": [{"value": 1.0}]}]
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
make test TESTS=tests/runtime/intents/test_metric_frame.py
```

Expected: failure because the new helpers do not exist.

- [ ] **Step 3: Add MetricFrame builder and readers**

In `marivo/runtime/intents/metric_frame.py`, add:

```python
MetricFrameShape = str


def build_metric_frame_artifact(
    *,
    artifact_id: str,
    shape: MetricFrameShape,
    metric_ref: str,
    time_scope: dict[str, Any],
    scope: dict[str, Any],
    axes: list[dict[str, str]],
    series: list[dict[str, Any]],
    unit: str | None,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": metric_ref,
            "time_scope": time_scope,
            "scope": scope,
        },
        "axes": axes,
        "measures": [
            {
                "id": "value",
                "value_type": "number",
                "nullable": True,
                "unit": unit,
            }
        ],
        "payload": {"series": series},
    }


def is_metric_frame_artifact(artifact: dict[str, Any]) -> bool:
    return artifact.get("artifact_family") == "metric_frame"


def read_metric_frame_shape(artifact: dict[str, Any]) -> str:
    shape = artifact.get("shape")
    if not isinstance(shape, str) or not shape:
        raise ValueError("metric_frame artifact missing shape")
    return shape


def read_metric_frame_subject(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = artifact.get("subject")
    if not isinstance(subject, dict):
        raise ValueError("metric_frame artifact missing subject")
    return subject


def read_metric_frame_time_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = read_metric_frame_subject(artifact)
    time_scope = subject.get("time_scope")
    if not isinstance(time_scope, dict):
        raise ValueError("metric_frame artifact subject missing time_scope")
    return time_scope


def read_metric_frame_scope(artifact: dict[str, Any]) -> dict[str, Any]:
    subject = read_metric_frame_subject(artifact)
    scope = subject.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("metric_frame artifact subject missing scope")
    return scope


def read_metric_frame_metric_ref(artifact: dict[str, Any]) -> str:
    subject = read_metric_frame_subject(artifact)
    metric_ref = subject.get("metric_ref")
    if not isinstance(metric_ref, str) or not metric_ref:
        raise ValueError("metric_frame artifact subject missing metric_ref")
    return metric_ref


def read_metric_frame_series(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("metric_frame artifact missing payload")
    series = payload.get("series")
    if not isinstance(series, list):
        raise ValueError("metric_frame artifact payload missing series")
    return series
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
make test TESTS=tests/runtime/intents/test_metric_frame.py
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add marivo/runtime/intents/metric_frame.py tests/runtime/intents/test_metric_frame.py
git commit -m "feat: add metric frame artifact helpers" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

### Task 3: Observe Runtime Output

**Files:**
- Modify: `marivo/runtime/intents/observe.py`
- Modify: `tests/runtime/intents/test_observe_runner.py`

- [ ] **Step 1: Update observe runner tests for new artifact shape**

In `tests/runtime/intents/test_observe_runner.py`, update scalar assertions to:

```python
self.assertEqual(result["artifact_family"], "metric_frame")
self.assertEqual(result["shape"], "scalar")
self.assertEqual(result["subject"]["metric_ref"], "metric.m1")
self.assertEqual(result["subject"]["scope"], {})
self.assertEqual(result["axes"], [])
self.assertEqual(result["payload"]["series"][0]["points"][0]["value"], 42.5)
self.assertNotIn("observation_type", result)
self.assertNotIn("analytical_metadata", result)
self.assertNotIn("execution_metadata", result)
```

Update time-series assertions to read:

```python
self.assertEqual(result["shape"], "time_series")
self.assertEqual(result["axes"], [{"kind": "time", "grain": "day"}])
self.assertEqual(
    result["payload"]["series"][0]["points"],
    [
        {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": 10.0},
        {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": None},
    ],
)
```

Update segmented and panel tests similarly:

```python
self.assertEqual(result["shape"], "segmented")
self.assertEqual(result["payload"]["series"], expected_series)
```

```python
self.assertEqual(result["shape"], "panel")
for s in result["payload"]["series"]:
    self.assertIn("keys", s)
    self.assertIn("points", s)
```

Update commit assertions to:

```python
args, kwargs = runtime.commit_artifact_with_extraction.call_args
self.assertEqual(args[2], "metric_frame")
self.assertEqual(kwargs["step_type"], "observe")
self.assertEqual(args[4]["artifact_family"], "metric_frame")
self.assertEqual(args[4]["shape"], "scalar")
```

- [ ] **Step 2: Run observe tests and verify they fail**

Run:

```bash
make test TESTS=tests/runtime/intents/test_observe_runner.py
```

Expected: failure because runtime still emits old top-level observation fields.

- [ ] **Step 3: Import and use the MetricFrame builder**

In `marivo/runtime/intents/observe.py`, add `build_metric_frame_artifact` to the existing metric frame import list:

```python
from marivo.runtime.intents.metric_frame import (
    build_axes,
    build_metric_frame_artifact,
    build_panel_series,
    build_scalar_series,
    build_segmented_series,
    build_time_series_points,
    determine_observation_type,
)
```

Replace each branch's `observation = { ... }` dictionary with:

```python
observation = build_metric_frame_artifact(
    artifact_id="",
    shape=obs_type,
    metric_ref=metric_ref,
    time_scope=resolved_time_scope,
    scope=scope_raw or {},
    axes=axes,
    series=series,
    unit=None,
)
```

The empty `artifact_id` is replaced immediately before commit in the next step.

- [ ] **Step 4: Generate the artifact id before building the payload**

Near `step_id = new_step_id()`, add:

```python
artifact_id = f"art_{uuid4().hex[:12]}"
```

Add the import:

```python
from uuid import uuid4
```

Then pass `artifact_id=artifact_id` to each `build_metric_frame_artifact(...)` call.

- [ ] **Step 5: Commit as artifact type metric_frame**

Change the `commit_step_result(...)` call from:

```python
result = commit_step_result(
    runtime,
    session_id,
    step_id,
    "observe",
    "observation",
    artifact_name,
    observation,
    summary,
    provenance=provenance,
    reasoning=reasoning,
    semantic_metadata=build_step_semantic_metadata(compiled_query),
    sql_texts=sql_texts,
)
```

to:

```python
result = commit_step_result(
    runtime,
    session_id,
    step_id,
    "observe",
    "metric_frame",
    artifact_name,
    observation,
    summary,
    provenance=provenance,
    reasoning=reasoning,
    semantic_metadata=build_step_semantic_metadata(compiled_query),
    sql_texts=sql_texts,
    artifact_id=artifact_id,
)
```

If `commit_step_result` does not accept `artifact_id`, add this optional parameter in `marivo/runtime/intents/_helpers.py`:

```python
def commit_step_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    artifact_payload: dict[str, Any],
    summary: str,
    *,
    provenance: dict[str, Any] | None = None,
    reasoning: str | None = None,
    semantic_metadata: dict[str, Any] | None = None,
    sql_texts: list[dict[str, Any]] | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
```

Use `artifact_id` when calling `runtime.commit_artifact_with_extraction(...)` if the helper currently generates its own id. Preserve existing behavior when `artifact_id is None`.

- [ ] **Step 6: Run observe tests**

Run:

```bash
make test TESTS=tests/runtime/intents/test_observe_runner.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add marivo/runtime/intents/observe.py marivo/runtime/intents/_helpers.py tests/runtime/intents/test_observe_runner.py
git commit -m "feat: emit observe metric frame artifacts" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

### Task 4: Observation Finding Extraction From MetricFrame

**Files:**
- Modify: `marivo/core/evidence/finding_extraction.py`
- Modify: `marivo/runtime/evidence/observe_extractor.py`
- Modify: `tests/core/test_evidence_finding_extraction.py`
- Modify: `tests/runtime/evidence/test_observe_extractor.py`

- [ ] **Step 1: Add failing extraction tests for metric_frame**

In `tests/core/test_evidence_finding_extraction.py`, add:

```python
def test_extract_observe_findings_accepts_metric_frame_scalar() -> None:
    payload = {
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "time_scope": {"field": "ds", "start": "2026-01-01", "end": "2026-01-02"},
            "scope": {},
        },
        "axes": [],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"value": 10.0}]}]},
    }

    findings = extract_observe_findings("art_1", payload, _step_ref())

    assert len(findings) == 1
    assert findings[0]["payload"]["observation_kind"] == "scalar"
    assert findings[0]["payload"]["value"] == 10.0
    assert findings[0]["subject"]["metric"] == "metric.revenue"
```

Add equivalent tests for time-series and segmented metric frames, checking canonical item keys:

```python
assert findings[0]["provenance"]["canonical_item_key"] == "buckets:2026-01-01/2026-01-02"
assert findings[0]["payload"]["observation_kind"] == "time_bucket"
```

```python
assert findings[0]["provenance"]["canonical_item_key"] == "rows:region=US"
assert findings[0]["payload"]["observation_kind"] == "segment"
```

- [ ] **Step 2: Run extraction tests and verify they fail**

Run:

```bash
make test TESTS=tests/core/test_evidence_finding_extraction.py
```

Expected: failure because extraction has not yet been updated to require `metric_frame`.

- [ ] **Step 3: Normalize metric_frame payload inside extraction**

In `marivo/core/evidence/finding_extraction.py`, change `extract_observe_findings` to a strict
metric-frame dispatcher:

```python
def extract_observe_findings(
    artifact_id: str,
    payload: dict[str, Any],
    step_ref: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract observation findings from an observe metric_frame artifact payload."""
    if payload.get("artifact_family") != "metric_frame":
        raise ValueError("Observe extraction requires artifact_family='metric_frame'")
    return _extract_metric_frame_findings(artifact_id, payload, step_ref)
```

Add `_extract_metric_frame_findings` and shape-specific helpers that build findings directly from
the public `metric_frame` contract. Do not normalize back into legacy observe payloads. The helper
should:

- read `subject.metric_ref`, `subject.time_scope`, `subject.scope`, `axes`, `measures[0].unit`,
  and `payload.series`;
- dispatch on `shape`;
- return one scalar finding for `scalar`;
- return one time-bucket finding per windowed point for `time_series`;
- return one segment finding per series entry for `segmented`;
- return one time-bucket finding per `(series.keys, point.window)` pair for `panel`, with the
  series keys included in `subject.slice` and the stable item key;
- use empty/default quality because public `metric_frame` excludes quality metadata;
- use `extractor_name="observe_metric_frame_v1"` and `artifact_schema_version=None`;
- raise `ValueError` for unknown shapes.

Add helpers:

```python
def _metric_frame_unit(payload: dict[str, Any]) -> str | None:
    measures = payload.get("measures") or []
    if not measures:
        return None
    unit = measures[0].get("unit")
    return unit if isinstance(unit, str) else None


def _metric_frame_time_grain(payload: dict[str, Any]) -> str | None:
    for axis in payload.get("axes") or []:
        if axis.get("kind") == "time":
            grain = axis.get("grain")
            return grain if isinstance(grain, str) else None
    return None


def _stable_keys(keys: dict[str, Any]) -> str:
    return ",".join(f"{key}={keys[key]}" for key in sorted(keys))
```

- [ ] **Step 4: Register observe extractor for metric_frame**

In `marivo/runtime/evidence/observe_extractor.py`, change class constants:

```python
artifact_type = "metric_frame"
artifact_schema_version = None
family = "observe"
extractor_name = "observe_metric_frame_v1"
```

If the registry test still expects `("observation", "v1")`, update it to expect `("metric_frame", None)` and verify the old key is gone.

- [ ] **Step 5: Run extraction tests**

Run:

```bash
make test TESTS="tests/core/test_evidence_finding_extraction.py tests/runtime/evidence/test_observe_extractor.py"
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add marivo/core/evidence/finding_extraction.py marivo/runtime/evidence/observe_extractor.py tests/core/test_evidence_finding_extraction.py tests/runtime/evidence/test_observe_extractor.py
git commit -m "feat: extract findings from observe metric frames" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

### Task 5: Minimal Existing Consumer Reads

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `marivo/runtime/intents/correlate.py`
- Modify: `marivo/runtime/intents/forecast.py`
- Modify: `tests/runtime/intents/_runner_fixtures.py`
- Modify focused tests in `tests/runtime/intents/test_compare_runner.py`, `tests/runtime/intents/test_correlate_runner.py`, `tests/runtime/intents/test_forecast_runner.py`, and `tests/runtime/intents/test_attribute_runner.py`

- [ ] **Step 1: Update shared observe fixtures**

In `tests/runtime/intents/_runner_fixtures.py`, update observe artifact fixtures to return:

```python
{
    "artifact_id": artifact_id,
    "artifact_family": "metric_frame",
    "shape": "time_series",
    "subject": {
        "kind": "metric",
        "metric_ref": metric,
        "time_scope": {"field": "event_date", "start": start, "end": end},
        "scope": {},
    },
    "axes": [{"kind": "time", "grain": "day"}],
    "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
    "payload": {"series": [{"keys": {}, "points": points}]},
}
```

Use the same structure for scalar, segmented, and panel fixtures.

- [ ] **Step 2: Run current consumer tests and verify failures**

Run:

```bash
make test TESTS="tests/runtime/intents/test_compare_runner.py tests/runtime/intents/test_correlate_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_attribute_runner.py"
```

Expected: failures where code reads `observation_type` or top-level `series`.

- [ ] **Step 3: Replace direct observation reads with metric_frame helpers**

In each modified intent file, import the readers:

```python
from marivo.runtime.intents.metric_frame import (
    read_metric_frame_series,
    read_metric_frame_shape,
    read_metric_frame_time_scope,
)
```

Replace:

```python
artifact_obs_type = source_artifact.get("observation_type")
```

with:

```python
artifact_obs_type = read_metric_frame_shape(source_artifact)
```

Replace:

```python
source_artifact.get("series") or []
```

with:

```python
read_metric_frame_series(source_artifact)
```

Replace direct `time_scope` reads:

```python
artifact.get("time_scope")
```

with:

```python
read_metric_frame_time_scope(artifact)
```

Do not change compare, correlate, forecast, or attribute output artifact families in this task.

- [ ] **Step 4: Run consumer tests**

Run:

```bash
make test TESTS="tests/runtime/intents/test_compare_runner.py tests/runtime/intents/test_correlate_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_attribute_runner.py"
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

Run:

```bash
git add marivo/runtime/intents/compare.py marivo/runtime/intents/attribute.py marivo/runtime/intents/correlate.py marivo/runtime/intents/forecast.py tests/runtime/intents/_runner_fixtures.py tests/runtime/intents/test_compare_runner.py tests/runtime/intents/test_correlate_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_attribute_runner.py
git commit -m "feat: read metric frame observe artifacts in intents" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

### Task 6: Documentation And Final Verification

**Files:**
- Modify: `docs/specs/analysis/intents/atomic/observe.md`
- Modify: `docs/specs/analysis/README.md`
- Modify: `docs/superpowers/specs/2026-05-21-observe-metric-frame-artifact-design.md` only if implementation clarifies a field name

- [ ] **Step 1: Update observe schema docs**

In `docs/specs/analysis/intents/atomic/observe.md`, replace the artifact format section with:

```markdown
## Artifact 格式

`observe` 成功输出 `artifact_family = "metric_frame"` 的 AOI artifact。

核心字段：

- `shape`: `scalar`、`time_series`、`segmented` 或 `panel`
- `subject`: metric、整体 `time_scope` 与必填 `scope`
- `axes`: 时间轴和维度轴
- `measures`: observe 中固定为 `value`
- `payload.series`: 所有 shape 的唯一数据容器

`observation_type`、顶层 `series`、顶层 `metric/time_scope/scope`、以及
`analytical_metadata/execution_metadata` 不属于公共 AOI artifact contract。
```

- [ ] **Step 2: Update analysis README naming baseline**

In `docs/specs/analysis/README.md`, update the artifact subtype bullet to say:

```markdown
- `observe` 的公共 artifact subtype 由 `artifact_family = "metric_frame"` 和
  `shape` 决定；不要再使用 `observation_type` 作为 observe 的公共 contract
  discriminator。
```

- [ ] **Step 3: Run focused observe contract/runtime tests**

Run:

```bash
make test TESTS="tests/contracts/test_aoi_runtime_contract.py tests/runtime/intents/test_metric_frame.py tests/runtime/intents/test_observe_runner.py tests/core/test_evidence_finding_extraction.py tests/runtime/evidence/test_observe_extractor.py"
```

Expected: PASS.

- [ ] **Step 4: Run broader affected runtime tests**

Run:

```bash
make test TESTS="tests/runtime/intents/test_compare_runner.py tests/runtime/intents/test_correlate_runner.py tests/runtime/intents/test_forecast_runner.py tests/runtime/intents/test_attribute_runner.py tests/integration/test_observe_compare_lineage_reuse.py"
```

Expected: PASS.

- [ ] **Step 5: Run repository checks**

Run:

```bash
make lint
make typecheck
make test
```

Expected: all PASS.

- [ ] **Step 6: Commit Task 6**

Run:

```bash
git add docs/specs/analysis/intents/atomic/observe.md docs/specs/analysis/README.md docs/superpowers/specs/2026-05-21-observe-metric-frame-artifact-design.md
git commit -m "docs: document observe metric frame artifact contract" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

---

## Self-Review

Spec coverage:

- Top-level `artifact_family = "metric_frame"`: Task 1 and Task 3.
- `shape` for scalar/time_series/segmented/panel: Task 1, Task 2, Task 3.
- Unified `payload.series`: Task 1, Task 2, Task 3.
- Required `subject.scope = {}` when unfiltered: Task 1, Task 2, Task 3.
- No quality/execution metadata in public artifact: Task 3 and Task 6.
- No historical compatibility/migration: all tests and implementation target the new shape directly.
- Downstream output families not rewritten: Task 5 limits changes to readers only.

Placeholder scan:

- This plan contains no unresolved markers or unspecified implementation steps.
- Every code-changing step includes exact files, concrete snippets, commands, and expected outcomes.

Type consistency:

- Public field names are consistent across tasks: `artifact_family`, `shape`, `subject`, `axes`, `measures`, `payload.series`.
- Runtime helper names introduced in Task 2 are the same names used in Tasks 3 and 5.
