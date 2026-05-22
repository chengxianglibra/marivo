# Detect Candidate Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `detect` to artifact-only input and `candidate_set` output, with `metric_frame` point-anomaly scans and `delta_frame` period-shift scans.

**Architecture:** AOI contracts define the new public request and artifact family, then generated models drive HTTP/MCP validation. Runtime `detect` becomes a pure artifact scanner that reads committed frames through shared frame helpers and commits a `candidate_set`. Evidence extraction reads `candidate_set.payload.items[]` and continues emitting canonical `anomaly_candidate` findings.

**Tech Stack:** Python 3.12, Pydantic v2 generated contracts, JSON Schema, Marivo runtime intent runners, Evidence Engine extractors, FastAPI HTTP models, MCP tool adapters, repository `make` entrypoints.

---

## Source Spec

Implement the approved design in:

- `docs/superpowers/specs/2026-05-22-detect-candidate-set-design.md`

Do not preserve the old source-style `detect` request, old `artifact_type: "anomaly_candidates"`, or old AOI `AnomalyCandidatesResult` response body.

## Scope Check

This is a single implementation plan because all affected pieces are one contract cutover:

- AOI contract and generated models
- runtime `detect`
- evidence extraction and success-empty handling
- HTTP/MCP request and response surfaces
- docs and tests

The plan does not implement Plan DSL, transform guard, derived manifest redesign, or compatibility migrations.

## File Structure

Modify:

- `aoi-spec/schema/aoi.schema.yaml` - human-readable AOI public contract.
- `aoi-spec/schema/aoi.schema.json` - canonical generated-model source.
- `aoi-spec/spec.md` - public AOI narrative and examples.
- `docs/specs/analysis/aoi-spec.schema.yaml` - internal target-state AOI design sketch.
- `docs/specs/analysis/intents/atomic/detect.md` - canonical detect intent design doc.
- `marivo/contracts/generated/aoi.py` - generated Pydantic models, regenerated only.
- `marivo/contracts/aoi_runtime.py` - AOI artifact union and envelope conversion.
- `marivo/contracts/aoi_projection.py` - AOI projection for candidate-set artifacts.
- `marivo/runtime/aoi_lowering.py` - generated `aoi.Detect` to runner params.
- `marivo/runtime/intents/metric_frame.py` - shared frame point iteration and candidate-set artifact helpers.
- `marivo/runtime/intents/detect.py` - pure artifact scanner implementation.
- `marivo/core/evidence/canonical_finding.py` - add source point refs and direction to `AnomalyCandidatePayload`.
- `marivo/runtime/evidence/detect_extractor.py` - extract from `candidate_set.payload.items[]`.
- `marivo/runtime/evidence/finding_extractor_registry.py` - registry expectations remain key-based; update bootstrap effects via extractor class.
- `marivo/core/evidence/family_contract.py` - detect success-empty family name.
- `marivo/runtime/session.py` - status docs and allows-empty references.
- `marivo/runtime/report.py` - candidate-set summary projection.
- `marivo/transports/http/models/intent_response_models.py` - typed response for `candidate_set`.
- `marivo/transports/http/sessions.py` - request model stays `aoi.Detect`; generated model changes.
- `marivo/transports/mcp/tools/intents.py` - MCP detect tool and adapter helper.
- `docs/api/intent-steps.md`, `docs/api/README.md`, `docs/api/runtime-status.md` - public docs.

Test files:

- `tests/contracts/test_generated_models.py`
- `tests/contracts/test_aoi_runtime_contract.py`
- `tests/runtime/test_aoi_lowering.py`
- `tests/runtime/intents/test_metric_frame_helpers.py`
- `tests/runtime/intents/test_detect_runner.py`
- `tests/runtime/evidence/test_detect_extractor.py`
- `tests/runtime/evidence/test_finding_extractor_registry.py`
- `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`
- `tests/runtime/test_session_state.py`
- `tests/runtime/evidence/test_correlate_test_forecast_extractor.py`
- `tests/transports/http/test_http_aoi_intents.py`
- `tests/transports/mcp/test_mcp_aoi_adapter.py`
- `tests/transports/mcp/test_tool_parity.py`

## Task 1: AOI Contract And Generated Models

**Files:**

- Modify: `tests/contracts/test_generated_models.py`
- Modify: `tests/contracts/test_aoi_runtime_contract.py`
- Modify: `aoi-spec/schema/aoi.schema.yaml`
- Modify: `aoi-spec/schema/aoi.schema.json`
- Modify: `docs/specs/analysis/aoi-spec.schema.yaml`
- Modify: `marivo/contracts/generated/aoi.py`
- Modify: `marivo/contracts/aoi_runtime.py`

- [ ] **Step 1: Write failing generated-model tests**

Replace the existing detect contract tests in `tests/contracts/test_generated_models.py` with these tests. Keep unrelated observe/compare/decompose tests unchanged.

```python
def _candidate_set_artifact_payload(shape: str = "point_anomaly_candidates") -> dict[str, Any]:
    return {
        "artifact_id": "artifact_candidates",
        "artifact_family": "candidate_set",
        "shape": shape,
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": "metric.revenue",
            "source_artifact_id": "artifact_source",
            "source_artifact_family": "metric_frame"
            if shape == "point_anomaly_candidates"
            else "delta_frame",
            "source_shape": "time_series"
            if shape == "point_anomaly_candidates"
            else "time_series_delta",
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "score", "value_type": "number", "nullable": False}],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": ["artifact_source"],
            "strategy": "point_anomaly"
            if shape == "point_anomaly_candidates"
            else "period_shift",
        },
        "payload": {
            "items": [
                {
                    "item_id": "2026-01-03T00:00:00Z",
                    "source_point_ref": {
                        "artifact_id": "artifact_source",
                        "series_index": 0,
                        "point_index": 2,
                        "series_keys": {},
                        "point_key": "2026-01-03T00:00:00Z",
                    },
                    "window": {
                        "start": "2026-01-03T00:00:00Z",
                        "end": "2026-01-04T00:00:00Z",
                    },
                    "keys": None,
                    "value": 200.0,
                    "score": 2.4,
                    "direction": "increase",
                }
            ],
            "scan_summary": {
                "scanned_series_count": 1,
                "total_candidate_count": 1,
            },
            "truncation": {
                "returned_candidate_count": 1,
                "total_candidate_count": 1,
                "truncated": False,
            },
            "quality": {"status": "detectable", "issues": []},
        },
    }


@pytest.mark.parametrize("sensitivity", ["conservative", "balanced", "aggressive"])
def test_aoi_detect_accepts_artifact_input_only(sensitivity: str) -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Detect.model_validate(
        {
            "source_artifact_id": "artifact_source",
            "sensitivity": sensitivity,
            "limit": 10,
        }
    )

    assert request.source_artifact_id == "artifact_source"
    assert request.sensitivity == sensitivity
    assert request.limit == 10


def test_aoi_detect_defaults_omitted_optional_fields() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Detect.model_validate({"source_artifact_id": "artifact_source"})

    assert request.sensitivity == "aggressive"
    dumped = request.model_dump(exclude_none=True)
    assert dumped == {"source_artifact_id": "artifact_source", "sensitivity": "aggressive"}


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"source_artifact_id": ""},
        {"sensitivity": "extreme"},
        {"limit": 0},
        {"limit": -1},
        {"metric": "revenue"},
        {"time_scope": _aoi_time_scope()},
        {"granularity": "day"},
        {"filter": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region = 'US'"}]}},
        {"dimension": "region"},
        {"strategy": "point_anomaly"},
    ],
)
def test_aoi_detect_rejects_invalid_or_removed_contract_fields(
    payload_patch: dict[str, Any],
) -> None:
    from marivo.contracts.generated import aoi

    payload = {"source_artifact_id": "artifact_source"}
    payload.update(payload_patch)

    with pytest.raises(ValidationError):
        aoi.Detect.model_validate(payload)


def test_aoi_detect_requires_source_artifact_id() -> None:
    from marivo.contracts.generated import aoi

    with pytest.raises(ValidationError):
        aoi.Detect.model_validate({"sensitivity": "balanced"})


@pytest.mark.parametrize("shape", ["point_anomaly_candidates", "period_shift_candidates"])
def test_aoi_candidate_set_artifact_accepts_public_shape(shape: str) -> None:
    from marivo.contracts.generated import aoi

    artifact = aoi.CandidateSetArtifact.model_validate(
        _candidate_set_artifact_payload(shape)
    )

    assert artifact.artifact_family == "candidate_set"
    assert artifact.shape == shape
    assert artifact.payload.items[0].score == 2.4
```

Add this test in `tests/contracts/test_aoi_runtime_contract.py` near the existing artifact validation tests:

```python
def test_validate_aoi_artifact_accepts_candidate_set_artifact() -> None:
    artifact = validate_aoi_artifact(
        {
            "artifact_id": "artifact_candidates",
            "artifact_family": "candidate_set",
            "shape": "point_anomaly_candidates",
            "subject": {
                "kind": "candidate_scan",
                "metric_ref": "metric.revenue",
                "source_artifact_id": "artifact_source",
                "source_artifact_family": "metric_frame",
                "source_shape": "time_series",
            },
            "axes": [{"kind": "time", "grain": "day"}],
            "measures": [{"id": "score", "value_type": "number", "nullable": False}],
            "capabilities": ["filterable"],
            "lineage": {
                "operation": "detect",
                "source_artifact_ids": ["artifact_source"],
                "strategy": "point_anomaly",
            },
            "payload": {
                "items": [],
                "scan_summary": {
                    "scanned_series_count": 1,
                    "total_candidate_count": 0,
                },
                "truncation": {
                    "returned_candidate_count": 0,
                    "total_candidate_count": 0,
                    "truncated": False,
                },
                "quality": {"status": "detectable", "issues": []},
            },
        }
    )

    assert artifact.artifact_family == "candidate_set"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py::test_aoi_detect_accepts_artifact_input_only tests/contracts/test_generated_models.py::test_aoi_candidate_set_artifact_accepts_public_shape tests/contracts/test_aoi_runtime_contract.py::test_validate_aoi_artifact_accepts_candidate_set_artifact'
```

Expected: FAIL because `aoi.Detect` still requires `metric/time_scope/granularity/strategy` and `aoi.CandidateSetArtifact` does not exist.

- [ ] **Step 3: Update readable AOI YAML contract**

In `aoi-spec/schema/aoi.schema.yaml`, remove the `detect_strategies` enum and replace the `detect` request and detect result sections with:

```yaml
# Supported anomaly detection sensitivity presets.
detect_sensitivities:
  - conservative  # Stricter candidate inclusion.
  - balanced      # Balanced candidate inclusion.
  - aggressive    # Broader candidate inclusion; default.

# Detect anomaly candidates from a committed frame artifact.
detect:
  # Required: artifact_id of the source metric_frame or delta_frame.
  source_artifact_id: string

  # Optional: detection sensitivity preset, one of detect_sensitivities. Omitted means aggressive.
  sensitivity: detect_sensitivities

  # Optional: maximum number of anomaly candidates to return, range [1, +infinity).
  limit: integer
```

In the artifact result union in the same file, replace `anomaly_candidates_result` with `candidate_set_artifact`:

```yaml
      - candidate_set_artifact       # Ranked anomaly candidate set artifact.
```

Replace the old `anomaly_candidates_result` section with:

```yaml
# Candidate set artifact returned by detect requests.
candidate_set_artifact:
  artifact_id: string
  artifact_family: candidate_set
  shape: point_anomaly_candidates | period_shift_candidates
  subject:
    kind: candidate_scan
    metric_ref: string
    source_artifact_id: string
    source_artifact_family: metric_frame | delta_frame
    source_shape: string
  axes:
    - kind: time | dimension
      grain: time_granularities
      name: string
  measures:
    - id: score | value | baseline_value | delta_abs | delta_pct
      value_type: number
      nullable: true
      unit: string
  capabilities:
    - filterable
  lineage:
    operation: detect
    source_artifact_ids: string[]
    strategy: point_anomaly | period_shift
  payload:
    items:
      - item_id: string
        source_point_ref: frame_point_ref
        source_delta_point_ref: frame_point_ref
        window:
          start: iso8601_datetime
          end: iso8601_datetime
        keys: dimension_key_map
        value: number | null
        baseline_value: number | null
        delta_abs: number | null
        delta_pct: number | null
        score: number
        direction: increase | decrease | unknown
    scan_summary:
      scanned_series_count: integer
      total_candidate_count: integer
    truncation:
      returned_candidate_count: integer
      total_candidate_count: integer
      truncated: boolean
    quality:
      status: detectable | needs_attention
      issues:
        - code: string
          severity: warning | error
          message: string

frame_point_ref:
  artifact_id: string
  series_index: integer
  point_index: integer
  series_keys: dimension_key_map
  point_key: string
```

- [ ] **Step 4: Update canonical AOI JSON schema**

In `aoi-spec/schema/aoi.schema.json`, edit `$defs.requests.Detect` so it has only `source_artifact_id`, `sensitivity`, and `limit`.

Use this shape:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["source_artifact_id"],
  "properties": {
    "source_artifact_id": {
      "type": "string",
      "minLength": 1
    },
    "sensitivity": {
      "$ref": "#/$defs/enums/DetectSensitivity",
      "default": "aggressive"
    },
    "limit": {
      "type": "integer",
      "minimum": 1
    }
  }
}
```

Remove any request references to `metric`, `time_scope`, `granularity`, `filter`, `dimension`, and `strategy` from the `Detect` definition. Keep `DetectSensitivity`.

Add artifact definitions under `$defs.artifacts`:

```json
{
  "FramePointRef": {
    "type": "object",
    "additionalProperties": false,
    "required": ["artifact_id", "series_index", "point_index", "series_keys", "point_key"],
    "properties": {
      "artifact_id": {"type": "string", "minLength": 1},
      "series_index": {"type": "integer", "minimum": 0},
      "point_index": {"type": "integer", "minimum": 0},
      "series_keys": {"$ref": "#/$defs/artifacts/DimensionKeyMap"},
      "point_key": {"type": "string", "minLength": 1}
    }
  },
  "CandidateScanSubject": {
    "type": "object",
    "additionalProperties": false,
    "required": [
      "kind",
      "metric_ref",
      "source_artifact_id",
      "source_artifact_family",
      "source_shape"
    ],
    "properties": {
      "kind": {"const": "candidate_scan"},
      "metric_ref": {"type": "string", "minLength": 1},
      "source_artifact_id": {"type": "string", "minLength": 1},
      "source_artifact_family": {"enum": ["metric_frame", "delta_frame"]},
      "source_shape": {"type": "string", "minLength": 1}
    }
  },
  "CandidateSetLineage": {
    "type": "object",
    "additionalProperties": false,
    "required": ["operation", "source_artifact_ids", "strategy"],
    "properties": {
      "operation": {"const": "detect"},
      "source_artifact_ids": {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "minLength": 1}
      },
      "strategy": {"enum": ["point_anomaly", "period_shift"]}
    }
  },
  "CandidateQualityIssue": {
    "type": "object",
    "additionalProperties": false,
    "required": ["code", "severity", "message"],
    "properties": {
      "code": {"type": "string", "minLength": 1},
      "severity": {"enum": ["warning", "error"]},
      "message": {"type": "string", "minLength": 1}
    }
  },
  "CandidateItem": {
    "type": "object",
    "additionalProperties": false,
    "required": ["item_id", "window", "keys", "value", "score", "direction"],
    "properties": {
      "item_id": {"type": "string", "minLength": 1},
      "source_point_ref": {"$ref": "#/$defs/artifacts/FramePointRef"},
      "source_delta_point_ref": {"$ref": "#/$defs/artifacts/FramePointRef"},
      "window": {"$ref": "#/$defs/artifacts/MetricFrameWindow"},
      "keys": {
        "anyOf": [
          {"$ref": "#/$defs/artifacts/DimensionKeyMap"},
          {"type": "null"}
        ]
      },
      "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
      "baseline_value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
      "delta_abs": {"anyOf": [{"type": "number"}, {"type": "null"}]},
      "delta_pct": {"anyOf": [{"type": "number"}, {"type": "null"}]},
      "score": {"type": "number", "minimum": 0},
      "direction": {"enum": ["increase", "decrease", "unknown"]}
    }
  },
  "CandidateSetArtifact": {
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
      "artifact_family": {"const": "candidate_set"},
      "shape": {"enum": ["point_anomaly_candidates", "period_shift_candidates"]},
      "subject": {"$ref": "#/$defs/artifacts/CandidateScanSubject"},
      "axes": {
        "type": "array",
        "items": {"$ref": "#/$defs/artifacts/MetricFrameAxis"}
      },
      "measures": {
        "type": "array",
        "items": {"$ref": "#/$defs/artifacts/MetricFrameMeasure"}
      },
      "capabilities": {
        "type": "array",
        "items": {"enum": ["filterable"]}
      },
      "lineage": {"$ref": "#/$defs/artifacts/CandidateSetLineage"},
      "payload": {
        "type": "object",
        "additionalProperties": false,
        "required": ["items", "scan_summary", "truncation", "quality"],
        "properties": {
          "items": {
            "type": "array",
            "items": {"$ref": "#/$defs/artifacts/CandidateItem"}
          },
          "scan_summary": {
            "type": "object",
            "additionalProperties": false,
            "required": ["scanned_series_count", "total_candidate_count"],
            "properties": {
              "scanned_series_count": {"type": "integer", "minimum": 0},
              "total_candidate_count": {"type": "integer", "minimum": 0}
            }
          },
          "truncation": {
            "type": "object",
            "additionalProperties": false,
            "required": [
              "returned_candidate_count",
              "total_candidate_count",
              "truncated"
            ],
            "properties": {
              "returned_candidate_count": {"type": "integer", "minimum": 0},
              "total_candidate_count": {"type": "integer", "minimum": 0},
              "truncated": {"type": "boolean"}
            }
          },
          "quality": {
            "type": "object",
            "additionalProperties": false,
            "required": ["status", "issues"],
            "properties": {
              "status": {"enum": ["detectable", "needs_attention"]},
              "issues": {
                "type": "array",
                "items": {"$ref": "#/$defs/artifacts/CandidateQualityIssue"}
              }
            }
          }
        }
      }
    }
  }
}
```

Add `CandidateSetArtifact` to the AOI artifact union wherever `MetricFrameArtifact`, `DeltaFrameArtifact`, and `AttributionFrameArtifact` are referenced. Remove `AnomalyCandidatesResult` from the detect success path.

- [ ] **Step 5: Update internal AOI sketch**

In `docs/specs/analysis/aoi-spec.schema.yaml`, update the detect operation compatibility section to:

```yaml
    detect:
      accepts:
        - metric_frame(time_series | panel) -> point_anomaly
        - delta_frame(time_series_delta | panel_delta) -> period_shift
      output_family: candidate_set
```

Update the operation request sketch:

```yaml
  detect:
    operation: detect

    # Required: committed source metric_frame or delta_frame artifact.
    source_artifact_id: string

    # Optional: detection sensitivity preset. Strategy is inferred from the
    # source artifact family and shape.
    sensitivity: detect_sensitivities
    limit: integer

    # Output: candidate_set(point_anomaly_candidates | period_shift_candidates).
    output: operation_response
```

Remove the source-style convenience input fields from this `detect` section.

- [ ] **Step 6: Regenerate Pydantic models**

Run:

```bash
.venv/bin/python scripts/generate_contract_models.py
```

Expected: `marivo/contracts/generated/aoi.py` updates with `Detect.source_artifact_id`, `CandidateSetArtifact`, `CandidateItem`, `FramePointRef`, and no public detect `strategy` field.

- [ ] **Step 7: Update AOI runtime artifact union**

Modify `marivo/contracts/aoi_runtime.py`:

```python
AoiArtifact = (
    aoi.MetricFrameArtifact
    | aoi.DeltaFrameArtifact
    | aoi.AttributionFrameArtifact
    | aoi.CandidateSetArtifact
    | aoi.Artifact1
    | aoi.Artifact2
)
```

In `validate_aoi_artifact`, include `aoi.CandidateSetArtifact` in the `isinstance` tuple and add:

```python
    if value.get("artifact_family") == "candidate_set":
        return aoi.CandidateSetArtifact.model_validate(value)
```

In `artifact_to_envelope_result`, change:

```python
    if data.get("artifact_family") in ("metric_frame", "delta_frame"):
```

to:

```python
    if data.get("artifact_family") in ("metric_frame", "delta_frame", "candidate_set"):
```

In `commit_aoi_artifact_result`, keep candidate sets top-level when this helper is used:

```python
    if canonical_artifact.get("artifact_family") in ("metric_frame", "candidate_set"):
        final_artifact = artifact_to_envelope_result(
            validate_aoi_artifact({**canonical_artifact, "artifact_id": artifact_id})
        )
    else:
        artifact_body_key = "result" if "result" in canonical_artifact else "failure"
```

- [ ] **Step 8: Run contract tests**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py tests/contracts/test_aoi_runtime_contract.py'
```

Expected: PASS for contract tests. If unrelated old detect tests in these files still expect `metric/time_scope/granularity/strategy`, update them to the new artifact-only request before continuing.

- [ ] **Step 9: Commit contract changes**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add aoi-spec/schema/aoi.schema.yaml aoi-spec/schema/aoi.schema.json docs/specs/analysis/aoi-spec.schema.yaml marivo/contracts/generated/aoi.py marivo/contracts/aoi_runtime.py tests/contracts/test_generated_models.py tests/contracts/test_aoi_runtime_contract.py
git diff --cached --name-status
git commit -m "feat: add candidate set AOI contract" -m "Replace detect source-style input with source_artifact_id and add candidate_set artifact models." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 2: Frame Helpers For Source Point References

**Files:**

- Modify: `tests/runtime/intents/test_metric_frame_helpers.py`
- Modify: `marivo/runtime/intents/metric_frame.py`

- [ ] **Step 1: Write failing helper tests**

Add these tests to `tests/runtime/intents/test_metric_frame_helpers.py`:

```python
def test_iter_frame_points_yields_metric_frame_refs() -> None:
    artifact = build_metric_frame_artifact(
        artifact_id="artifact_metric",
        shape="panel",
        metric_ref="metric.revenue",
        time_scope={
            "field": "event_time",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-04T00:00:00Z",
        },
        scope={},
        axes=[
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "region"},
        ],
        series=[
            {
                "keys": {"region": "US"},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-02T00:00:00Z",
                        },
                        "value": 100.0,
                    }
                ],
            }
        ],
        unit="usd",
    )

    points = list(iter_frame_points("artifact_metric", artifact))

    assert len(points) == 1
    assert points[0].series_keys == {"region": "US"}
    assert points[0].value("value") == 100.0
    assert points[0].window == {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
    }
    assert points[0].ref == {
        "artifact_id": "artifact_metric",
        "series_index": 0,
        "point_index": 0,
        "series_keys": {"region": "US"},
        "point_key": "2026-01-01T00:00:00Z",
    }


def test_iter_frame_points_yields_delta_frame_refs() -> None:
    artifact = build_delta_frame_artifact(
        artifact_id="artifact_delta",
        shape="time_series_delta",
        metric_ref="metric.revenue",
        axes=[{"kind": "time", "grain": "day"}],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-02T00:00:00Z",
                        },
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
    )

    points = list(iter_frame_points("artifact_delta", artifact))

    assert len(points) == 1
    assert points[0].value("current_value") == 120.0
    assert points[0].value("delta_pct") == 0.2
    assert points[0].ref["point_key"] == "2026-01-01T00:00:00Z"
```

Update imports in the test file:

```python
from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    build_metric_frame_artifact,
    iter_frame_points,
)
```

- [ ] **Step 2: Run helper tests to verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_metric_frame_helpers.py::test_iter_frame_points_yields_metric_frame_refs tests/runtime/intents/test_metric_frame_helpers.py::test_iter_frame_points_yields_delta_frame_refs'
```

Expected: FAIL because `iter_frame_points` is not defined.

- [ ] **Step 3: Implement frame point helper**

Add this to `marivo/runtime/intents/metric_frame.py` after the artifact readers:

```python
from dataclasses import dataclass
```

Add the dataclass and helper functions:

```python
@dataclass(frozen=True)
class FramePoint:
    artifact_id: str
    series_index: int
    point_index: int
    series_keys: dict[str, str]
    point: dict[str, Any]
    ref: dict[str, Any]

    @property
    def window(self) -> dict[str, Any] | None:
        window = self.point.get("window")
        return dict(window) if isinstance(window, dict) else None

    def value(self, field: str) -> Any:
        return self.point.get(field)


def _string_series_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items()}


def _point_key(point: dict[str, Any], point_index: int) -> str:
    window = point.get("window")
    if isinstance(window, dict):
        start = str(window.get("start") or "").strip()
        if start:
            return start
    for key in ("item_id", "row_id", "bucket_start", "start"):
        raw = point.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return f"point_{point_index}"


def iter_frame_points(artifact_id: str, artifact: dict[str, Any]) -> list[FramePoint]:
    series_list = read_frame_payload_series(artifact)
    frame_points: list[FramePoint] = []
    for series_index, series in enumerate(series_list):
        if not isinstance(series, dict):
            continue
        series_keys = _string_series_keys(series.get("keys"))
        raw_points = series.get("points") or []
        for point_index, point in enumerate(raw_points):
            if not isinstance(point, dict):
                continue
            point_key = _point_key(point, point_index)
            ref = {
                "artifact_id": artifact_id,
                "series_index": series_index,
                "point_index": point_index,
                "series_keys": series_keys,
                "point_key": point_key,
            }
            frame_points.append(
                FramePoint(
                    artifact_id=artifact_id,
                    series_index=series_index,
                    point_index=point_index,
                    series_keys=series_keys,
                    point=dict(point),
                    ref=ref,
                )
            )
    return frame_points
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_metric_frame_helpers.py'
```

Expected: PASS.

- [ ] **Step 5: Commit helper changes**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add marivo/runtime/intents/metric_frame.py tests/runtime/intents/test_metric_frame_helpers.py
git diff --cached --name-status
git commit -m "feat: add frame point iteration helpers" -m "Provide stable source point refs for metric_frame and delta_frame consumers." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 3: Runtime Detect Artifact Scanner

**Files:**

- Modify: `tests/runtime/intents/test_detect_runner.py`
- Modify: `marivo/runtime/intents/detect.py`

- [ ] **Step 1: Write failing point-anomaly runtime tests**

In `tests/runtime/intents/test_detect_runner.py`, replace tests that build source-style params with artifact-input tests. Add helpers:

```python
from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    build_metric_frame_artifact,
)


def _metric_frame_source() -> dict[str, Any]:
    return build_metric_frame_artifact(
        artifact_id="artifact_metric",
        shape="time_series",
        metric_ref="metric.revenue",
        time_scope={
            "field": "event_time",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-09T00:00:00Z",
        },
        scope={},
        axes=[{"kind": "time", "grain": "day"}],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "window": {
                            "start": f"2026-01-{day:02d}T00:00:00Z",
                            "end": f"2026-01-{day + 1:02d}T00:00:00Z",
                        },
                        "value": 200.0 if day == 5 else 100.0,
                    }
                    for day in range(1, 9)
                ],
            }
        ],
        unit="usd",
    )


def _delta_frame_source() -> dict[str, Any]:
    return build_delta_frame_artifact(
        artifact_id="artifact_delta",
        shape="time_series_delta",
        metric_ref="metric.revenue",
        axes=[{"kind": "time", "grain": "day"}],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-05T00:00:00Z",
                            "end": "2026-01-06T00:00:00Z",
                        },
                        "current_value": 130.0,
                        "baseline_value": 100.0,
                        "delta_abs": 30.0,
                        "delta_pct": 0.3,
                        "direction": "increase",
                    }
                ],
            }
        ],
        unit="usd",
    )


def _runtime_with_source(source_artifact: dict[str, Any]) -> MagicMock:
    runtime = MagicMock()
    runtime.resolve_artifact_with_step_by_id.return_value = ("step_source", source_artifact)
    runtime.commit_artifact_with_extraction.return_value = "artifact_candidates"
    return runtime
```

Add tests:

```python
def test_detect_metric_frame_commits_candidate_set_without_sql() -> None:
    runtime = _runtime_with_source(_metric_frame_source())

    envelope = run_detect_intent(
        runtime,
        "sess_1",
        {
            "source_artifact_id": "artifact_metric",
            "sensitivity": "balanced",
            "limit": 5,
        },
    )

    artifact = envelope["result"]
    assert artifact["artifact_family"] == "candidate_set"
    assert artifact["shape"] == "point_anomaly_candidates"
    assert artifact["lineage"]["strategy"] == "point_anomaly"
    assert artifact["payload"]["items"][0]["source_point_ref"]["artifact_id"] == "artifact_metric"
    assert artifact["payload"]["items"][0]["direction"] == "increase"
    runtime.compile_step.assert_not_called()
    runtime.resolve_metric_execution_context.assert_not_called()
    args, kwargs = runtime.commit_artifact_with_extraction.call_args
    assert args[2] == "candidate_set"
    assert kwargs["step_type"] == "detect"


def test_detect_delta_frame_commits_period_shift_candidate_set() -> None:
    runtime = _runtime_with_source(_delta_frame_source())

    envelope = run_detect_intent(
        runtime,
        "sess_1",
        {
            "source_artifact_id": "artifact_delta",
            "sensitivity": "balanced",
        },
    )

    item = envelope["result"]["payload"]["items"][0]
    assert envelope["result"]["shape"] == "period_shift_candidates"
    assert envelope["result"]["lineage"]["strategy"] == "period_shift"
    assert item["source_delta_point_ref"]["artifact_id"] == "artifact_delta"
    assert item["value"] == 130.0
    assert item["baseline_value"] == 100.0
    assert item["delta_abs"] == 30.0
    assert item["delta_pct"] == 0.3


def test_detect_rejects_removed_source_style_fields() -> None:
    runtime = _runtime_with_source(_metric_frame_source())

    with pytest.raises(ValueError, match="unsupported parameter"):
        run_detect_intent(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "artifact_metric",
                "metric": "metric.revenue",
            },
        )


def test_detect_rejects_unsupported_metric_frame_shape() -> None:
    source = _metric_frame_source()
    source["shape"] = "scalar"
    runtime = _runtime_with_source(source)

    with pytest.raises(ValueError, match="metric_frame shape 'scalar' is not supported"):
        run_detect_intent(runtime, "sess_1", {"source_artifact_id": "artifact_metric"})
```

- [ ] **Step 2: Run runtime tests to verify they fail**

Run:

```bash
make test TESTS='tests/runtime/intents/test_detect_runner.py::test_detect_metric_frame_commits_candidate_set_without_sql tests/runtime/intents/test_detect_runner.py::test_detect_delta_frame_commits_period_shift_candidate_set tests/runtime/intents/test_detect_runner.py::test_detect_rejects_removed_source_style_fields tests/runtime/intents/test_detect_runner.py::test_detect_rejects_unsupported_metric_frame_shape'
```

Expected: FAIL because `run_detect_intent` still expects source-style params and compiles SQL.

- [ ] **Step 3: Replace detect runner imports and accepted keys**

In `marivo/runtime/intents/detect.py`, remove imports that are only used for source queries:

```python
from datetime import UTC, datetime
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.intents._helpers import aoi_filter_to_scope
from marivo.runtime.intents.normalization import normalize_metric_ref, validate_granularity
from marivo.runtime.semantic.executor import execute_compiled
from marivo.time_contracts import (
    TimeGrain,
    bucket_window,
    normalize_hour_boundary,
    previous_adjacent_window,
    recommended_minimum_window,
)
from marivo.time_scope import normalize_metric_query_request
```

Add imports:

```python
from datetime import UTC, datetime

from marivo.runtime.intents.metric_frame import (
    FramePoint,
    is_delta_frame_artifact,
    is_metric_frame_artifact,
    iter_frame_points,
    read_delta_frame_shape,
    read_metric_frame_metric_ref,
    read_metric_frame_shape,
)
from uuid import uuid4
```

Set allowed params:

```python
_AOI_PARAM_KEYS: frozenset[str] = frozenset(
    {"source_artifact_id", "sensitivity", "limit"}
)
```

Remove `_VALID_STRATEGIES`, `_AOI_TIME_SCOPE_KEYS`, `_resolve_strategy`, `_table_has_column`, and `_query_scalar_window_values`.

- [ ] **Step 4: Implement artifact scanner helpers**

Replace `_detect_series_candidates` with:

```python
def _direction_from_delta(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value > 0:
        return "increase"
    if value < 0:
        return "decrease"
    return "unknown"


def _window_or_fail(frame_point: FramePoint) -> dict[str, Any]:
    window = frame_point.window
    if not isinstance(window, dict) or not window.get("start") or not window.get("end"):
        raise ValueError("detect: INVALID_ARGUMENT - source frame point missing window")
    return {"start": str(window["start"]), "end": str(window["end"])}


def _candidate_item_id(frame_point: FramePoint) -> str:
    key = str(frame_point.ref["point_key"])
    if frame_point.series_keys:
        return f"{_slice_sort_key(frame_point.series_keys)}|{key}"
    return key


def _score_metric_frame_series(
    points: list[FramePoint],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    numeric_values = [_coerce_float(point.value("value")) for point in points]
    values = [value for value in numeric_values if value is not None]
    if len(values) < _MIN_POINTS_FOR_DETECTION:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    candidates: list[dict[str, Any]] = []
    for point in points:
        value = _coerce_float(point.value("value"))
        if value is None:
            continue
        z_score = (value - mean) / std if std > 0 else 0.0
        score = abs(z_score)
        if score <= threshold:
            continue
        candidates.append(
            {
                "item_id": _candidate_item_id(point),
                "source_point_ref": point.ref,
                "window": _window_or_fail(point),
                "keys": dict(point.series_keys) if point.series_keys else None,
                "value": value,
                "baseline_value": mean,
                "score": score,
                "direction": _direction_from_delta(value - mean),
            }
        )
    return candidates


def _score_delta_frame_points(
    points: list[FramePoint],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for point in points:
        delta_pct = _coerce_float(point.value("delta_pct"))
        delta_abs = _coerce_float(point.value("delta_abs"))
        score = abs(delta_pct) if delta_pct is not None else abs(delta_abs or 0.0)
        if score <= threshold:
            continue
        candidates.append(
            {
                "item_id": _candidate_item_id(point),
                "source_delta_point_ref": point.ref,
                "window": _window_or_fail(point),
                "keys": dict(point.series_keys) if point.series_keys else None,
                "value": _coerce_float(point.value("current_value")),
                "baseline_value": _coerce_float(point.value("baseline_value")),
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
                "score": score,
                "direction": _direction_from_delta(delta_abs),
            }
        )
    return candidates
```

- [ ] **Step 5: Replace `run_detect_intent` body**

Replace `run_detect_intent` with:

```python
def run_detect_intent(
    runtime: MarivoRuntime,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    """Execute `detect` by scanning a committed metric_frame or delta_frame artifact."""
    p = params or {}
    extra_keys = sorted(set(p) - _AOI_PARAM_KEYS)
    if extra_keys:
        raise ValueError(
            "detect: INVALID_ARGUMENT - unsupported parameter(s): "
            f"{extra_keys}; detect accepts only source_artifact_id, sensitivity, and limit"
        )

    source_artifact_id = str(p.get("source_artifact_id") or "").strip()
    if not source_artifact_id:
        raise ValueError("detect: INVALID_ARGUMENT - source_artifact_id is required")

    sensitivity = str(p.get("sensitivity") or "aggressive").lower()
    if sensitivity not in _SENSITIVITY_THRESHOLD:
        raise ValueError(
            f"detect: INVALID_ARGUMENT - sensitivity='{sensitivity}' is not valid. "
            f"Must be one of: {sorted(_SENSITIVITY_THRESHOLD)}"
        )

    limit_raw = p.get("limit")
    limit: int | None = None
    if limit_raw is not None:
        limit = int(limit_raw)
        if limit <= 0:
            raise ValueError("detect: INVALID_ARGUMENT - limit must be > 0")

    resolved = runtime.resolve_artifact_with_step_by_id(session_id, source_artifact_id)
    if resolved is None:
        raise ValueError(
            "detect: ARTIFACT_NOT_FOUND - no committed artifact for "
            f"source_artifact_id '{source_artifact_id}'"
        )
    source_step_id, source_artifact = resolved
    del source_step_id

    if is_metric_frame_artifact(source_artifact):
        source_family = "metric_frame"
        source_shape = read_metric_frame_shape(source_artifact)
        if source_shape not in {"time_series", "panel"}:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - metric_frame shape '{source_shape}' is not supported"
            )
        strategy = "point_anomaly"
        output_shape = "point_anomaly_candidates"
        metric_ref = read_metric_frame_metric_ref(source_artifact)
        threshold = _SENSITIVITY_THRESHOLD[sensitivity]
        points = iter_frame_points(source_artifact_id, source_artifact)
        by_series: dict[int, list[FramePoint]] = {}
        for point in points:
            by_series.setdefault(point.series_index, []).append(point)
        raw_candidates = [
            candidate
            for series_points in by_series.values()
            for candidate in _score_metric_frame_series(series_points, threshold=threshold)
        ]
    elif is_delta_frame_artifact(source_artifact):
        source_family = "delta_frame"
        source_shape = read_delta_frame_shape(source_artifact)
        if source_shape not in {"time_series_delta", "panel_delta"}:
            raise ValueError(
                f"detect: INVALID_ARGUMENT - delta_frame shape '{source_shape}' is not supported"
            )
        strategy = "period_shift"
        output_shape = "period_shift_candidates"
        metric_ref = str(
            (source_artifact.get("subject") or {}).get("metric_ref")
            or source_artifact.get("metric_ref")
            or ""
        )
        if not metric_ref:
            raise ValueError("detect: INVALID_ARGUMENT - source delta_frame missing metric_ref")
        threshold = _PERIOD_SHIFT_THRESHOLD[sensitivity]
        raw_candidates = _score_delta_frame_points(
            iter_frame_points(source_artifact_id, source_artifact),
            threshold=threshold,
        )
    else:
        family = source_artifact.get("artifact_family") or source_artifact.get("artifact_type")
        raise ValueError(
            f"detect: INVALID_ARGUMENT - source artifact family '{family}' is not supported"
        )

    raw_candidates.sort(
        key=lambda candidate: (
            -float(candidate["score"]),
            candidate["window"]["start"],
            str(candidate.get("item_id") or ""),
        )
    )
    total_candidate_count = len(raw_candidates)
    returned_candidates = raw_candidates[:limit] if limit is not None else raw_candidates
    returned_candidate_count = len(returned_candidates)
    truncated = returned_candidate_count < total_candidate_count

    scanned_series = {
        point.series_index for point in iter_frame_points(source_artifact_id, source_artifact)
    }
    quality_status = (
        "detectable"
        if source_family == "delta_frame" or max(len(scanned_series), 0) > 0
        else "needs_attention"
    )
    quality_issues = []
    if source_family == "metric_frame" and not raw_candidates:
        numeric_points = [
            point
            for point in iter_frame_points(source_artifact_id, source_artifact)
            if _coerce_float(point.value("value")) is not None
        ]
        if len(numeric_points) < _MIN_POINTS_FOR_DETECTION:
            quality_status = "needs_attention"
            quality_issues.append(
                {
                    "code": "insufficient_points",
                    "severity": "warning",
                    "message": (
                        f"Only {len(numeric_points)} numeric point(s) found; "
                        f"minimum {_MIN_POINTS_FOR_DETECTION} required for point anomaly scanning."
                    ),
                }
            )

    step_id = new_step_id()
    artifact_id = f"art_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    artifact: dict[str, Any] = {
        "artifact_id": artifact_id,
        "artifact_family": "candidate_set",
        "shape": output_shape,
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": metric_ref,
            "source_artifact_id": source_artifact_id,
            "source_artifact_family": source_family,
            "source_shape": source_shape,
        },
        "axes": source_artifact.get("axes") or [],
        "measures": [
            {"id": "score", "value_type": "number", "nullable": False},
            {"id": "value", "value_type": "number", "nullable": True},
            {"id": "baseline_value", "value_type": "number", "nullable": True},
            {"id": "delta_abs", "value_type": "number", "nullable": True},
            {"id": "delta_pct", "value_type": "number", "nullable": True},
        ],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": [source_artifact_id],
            "strategy": strategy,
        },
        "payload": {
            "items": returned_candidates,
            "scan_summary": {
                "scanned_series_count": len(scanned_series),
                "total_candidate_count": total_candidate_count,
            },
            "truncation": {
                "returned_candidate_count": returned_candidate_count,
                "total_candidate_count": total_candidate_count,
                "truncated": truncated,
            },
            "quality": {"status": quality_status, "issues": quality_issues},
        },
        "execution_metadata": {"executed_at": now},
    }

    artifact_name = f"{metric_ref.removeprefix('metric.')}_candidate_set"
    summary = (
        f"detect {metric_ref} from {source_artifact_id}: "
        f"{total_candidate_count} candidate(s)"
    )
    committed_artifact_id = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        "candidate_set",
        artifact_name,
        artifact,
        step_type="detect",
        artifact_id=artifact_id,
    )
    artifact["artifact_id"] = committed_artifact_id

    provenance = {
        "source_artifact_id": source_artifact_id,
        "source_artifact_family": source_family,
        "source_shape": source_shape,
        "strategy": strategy,
    }
    result: dict[str, Any] = {
        "intent_type": "detect",
        "step_type": "detect",
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": "detect",
        },
        "artifact_id": committed_artifact_id,
        "result": artifact,
        "provenance": provenance,
        "product_metadata": None,
    }
    runtime.insert_step(
        step_id,
        session_id,
        "detect",
        summary,
        result,
        provenance=provenance,
        reasoning=reasoning,
        semantic_metadata=None,
        sql_texts=[],
    )
    return result
```

- [ ] **Step 6: Run runtime tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_detect_runner.py'
```

Expected: PASS after removing or rewriting old tests that assert source-style fields, SQL execution, `artifact_type: "anomaly_candidates"`, or top-level `candidates`.

- [ ] **Step 7: Commit runtime scanner**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add marivo/runtime/intents/detect.py tests/runtime/intents/test_detect_runner.py
git diff --cached --name-status
git commit -m "feat: scan detect candidates from frame artifacts" -m "Replace detect source queries with candidate_set scans over metric_frame and delta_frame inputs." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 4: Evidence Extraction And Success-Empty Family

**Files:**

- Modify: `marivo/core/evidence/canonical_finding.py`
- Modify: `marivo/runtime/evidence/detect_extractor.py`
- Modify: `marivo/core/evidence/family_contract.py`
- Modify: `marivo/runtime/session.py`
- Modify: `marivo/runtime/report.py`
- Modify: `tests/runtime/evidence/test_detect_extractor.py`
- Modify: `tests/runtime/evidence/test_finding_extractor_registry.py`
- Modify: `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`
- Modify: `tests/runtime/test_session_state.py`
- Modify: `tests/core/test_family_contract.py`
- Modify: `tests/runtime/evidence/test_correlate_test_forecast_extractor.py`

- [ ] **Step 1: Write failing extractor tests**

In `tests/runtime/evidence/test_detect_extractor.py`, replace the base payload helper with:

```python
def _candidate_set_payload(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "artifact_id": _ART_ID,
        "artifact_family": "candidate_set",
        "shape": "point_anomaly_candidates",
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": "metric.revenue",
            "source_artifact_id": "artifact_source",
            "source_artifact_family": "metric_frame",
            "source_shape": "time_series",
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "score", "value_type": "number", "nullable": False}],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": ["artifact_source"],
            "strategy": "point_anomaly",
        },
        "payload": {
            "items": items if items is not None else [],
            "scan_summary": {
                "scanned_series_count": 1,
                "total_candidate_count": len(items or []),
            },
            "truncation": {
                "returned_candidate_count": len(items or []),
                "total_candidate_count": len(items or []),
                "truncated": False,
            },
            "quality": {"status": "detectable", "issues": []},
        },
    }
```

Add or update tests:

```python
def test_extract_candidate_set_empty_candidates() -> None:
    result = _EXTRACTOR.extract(_ART_ID, _candidate_set_payload(), _STEP_REF, _SESSION_ID)

    assert result["findings"] == []
    assert result["finding_count"] == 0


def test_extract_candidate_set_item_to_anomaly_candidate() -> None:
    result = _EXTRACTOR.extract(
        _ART_ID,
        _candidate_set_payload(
            [
                {
                    "item_id": "2026-01-05T00:00:00Z",
                    "source_point_ref": {
                        "artifact_id": "artifact_source",
                        "series_index": 0,
                        "point_index": 4,
                        "series_keys": {},
                        "point_key": "2026-01-05T00:00:00Z",
                    },
                    "window": {
                        "start": "2026-01-05T00:00:00Z",
                        "end": "2026-01-06T00:00:00Z",
                    },
                    "keys": None,
                    "value": 200.0,
                    "baseline_value": 111.0,
                    "score": 2.8,
                    "direction": "increase",
                }
            ]
        ),
        _STEP_REF,
        _SESSION_ID,
    )

    finding = result["findings"][0]
    assert finding["finding_type"] == "anomaly_candidate"
    assert finding["subject"]["metric"] == "metric.revenue"
    assert finding["subject"]["analysis_axis"] == "time"
    assert finding["observed_window"] == {
        "field": "time",
        "start": "2026-01-05T00:00:00Z",
        "end": "2026-01-06T00:00:00Z",
    }
    assert finding["payload"]["candidate_ref"]["artifact_id"] == _ART_ID
    assert finding["payload"]["source_point_ref"]["artifact_id"] == "artifact_source"
    assert finding["payload"]["score"] == 2.8
    assert finding["payload"]["current_value"] == 200.0
    assert finding["payload"]["baseline_value"] == 111.0
    assert finding["payload"]["direction"] == "increase"


def test_registered_under_candidate_set_none_version() -> None:
    assert ("candidate_set", None) in default_finding_registry.registered_keys()
    assert isinstance(default_finding_registry.find("candidate_set", None), DetectArtifactExtractor)
    assert default_finding_registry.find("anomaly_candidates", "v1") is None
```

- [ ] **Step 2: Run extractor tests to verify they fail**

Run:

```bash
make test TESTS='tests/runtime/evidence/test_detect_extractor.py::test_extract_candidate_set_empty_candidates tests/runtime/evidence/test_detect_extractor.py::test_extract_candidate_set_item_to_anomaly_candidate tests/runtime/evidence/test_detect_extractor.py::test_registered_under_candidate_set_none_version'
```

Expected: FAIL because extractor still registers `anomaly_candidates` and reads top-level `candidates`.

- [ ] **Step 3: Extend anomaly candidate finding payload**

In `marivo/core/evidence/canonical_finding.py`, add:

```python
class FramePointRef(TypedDict):
    artifact_id: str
    series_index: int
    point_index: int
    series_keys: dict[str, str]
    point_key: str
```

Update `AnomalyCandidatePayload` to include:

```python
    source_point_ref: FramePointRef | None
    source_delta_point_ref: FramePointRef | None
    direction: Literal["increase", "decrease", "unknown"] | None
```

Keep existing `candidate_ref`, `score`, `flag_level`, `current_value`, `baseline_value`, `deviation_absolute`, and `deviation_relative` fields.

- [ ] **Step 4: Rewrite detect extractor for candidate_set**

In `marivo/runtime/evidence/detect_extractor.py`, set:

```python
class DetectArtifactExtractor(FindingExtractor):
    """Extract anomaly_candidate findings from candidate_set artifacts."""

    artifact_type = "candidate_set"
    artifact_schema_version = None
    family = "detect"
    extractor_name = "detect_candidate_set_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"
```

In `extract`, read:

```python
subject = artifact_payload.get("subject") or {}
payload = artifact_payload.get("payload") or {}
items: list[dict[str, Any]] = payload.get("items") or []
metric: str | None = subject.get("metric_ref")
```

Use this item mapping:

```python
item_id = str(item.get("item_id") or "").strip()
window = item.get("window") or {}
window_start = str(window.get("start", "")).strip()
window_end = str(window.get("end", "")).strip()
keys = item.get("keys") if isinstance(item.get("keys"), dict) else None

if item_id:
    canonical_item_key, item_ref = make_item_identity("candidates", key=item_id)
elif window_start and keys:
    canonical_item_key, item_ref = make_item_identity(
        "candidates", key=f"{window_start}|{_segment_stable_key(keys)}"
    )
elif window_start:
    canonical_item_key, item_ref = make_item_identity("candidates", key=window_start)
else:
    canonical_item_key, item_ref = make_item_identity("candidates", index=i)
```

Set finding values:

```python
analysis_axis = "panel" if keys and window_start else ("time" if window_start else "scalar")
subject_slice = dict(keys or {})
observed_window = (
    {"field": "time", "start": window_start, "end": window_end}
    if window_start and window_end
    else None
)
payload=AnomalyCandidatePayload(
    candidate_ref=candidate_ref,
    source_point_ref=item.get("source_point_ref"),
    source_delta_point_ref=item.get("source_delta_point_ref"),
    score=_to_float_or_none(item.get("score")),
    flag_level=None,
    current_value=_to_float_or_none(item.get("value")),
    baseline_value=_to_float_or_none(item.get("baseline_value")),
    deviation_absolute=_to_float_or_none(item.get("delta_abs")),
    deviation_relative=_to_float_or_none(item.get("delta_pct")),
    direction=item.get("direction")
    if item.get("direction") in {"increase", "decrease", "unknown"}
    else None,
)
```

- [ ] **Step 5: Update success-empty family names**

In `marivo/core/evidence/family_contract.py`, change the detect family entry from `anomaly_candidates` to `candidate_set`.

If the file defines a set like:

```python
ALLOWS_EMPTY_ARTIFACT_TYPES = frozenset({"metric_frame", "anomaly_candidates"})
```

change it to:

```python
ALLOWS_EMPTY_ARTIFACT_TYPES = frozenset({"metric_frame", "candidate_set"})
```

In `marivo/runtime/session.py`, update comments and any artifact type checks from `anomaly_candidates` to `candidate_set`.

In `marivo/runtime/report.py`, update `_extract_artifact_summary` to detect candidate-set payloads:

```python
    detect_payload = None
    if payload.get("artifact_family") == "candidate_set":
        detect_payload = payload
    elif content_dict and content_dict.get("artifact_family") == "candidate_set":
        detect_payload = content_dict

    if detect_payload is not None:
        payload_body = detect_payload.get("payload") or {}
        scan = payload_body.get("scan_summary") or {}
        truncation = payload_body.get("truncation") or {}
        if scan.get("total_candidate_count") is not None:
            summary["candidate_count_total"] = scan["total_candidate_count"]
        if truncation.get("returned_candidate_count") is not None:
            summary["candidate_count_returned"] = truncation["returned_candidate_count"]
        items = payload_body.get("items") or []
        if items:
            top = items[0]
            win = top.get("window")
            if isinstance(win, dict) and win.get("start"):
                summary["top_candidate_period"] = win["start"]
            if top.get("score") is not None:
                summary["top_candidate_score"] = top["score"]
            if top.get("delta_pct") is not None:
                summary["top_candidate_deviation_pct"] = top["delta_pct"]
            if top.get("direction"):
                summary["top_candidate_direction"] = top["direction"]
```

- [ ] **Step 6: Update downstream tests**

Apply these exact expectation changes:

```python
# tests/runtime/test_session_state.py
artifact_type="candidate_set"
```

```python
# tests/core/test_family_contract.py
assert "candidate_set" in ALLOWS_EMPTY_ARTIFACT_TYPES
assert "anomaly_candidates" not in ALLOWS_EMPTY_ARTIFACT_TYPES
```

```python
# tests/runtime/evidence/test_finding_extractor_registry.py
self.assertIn(("candidate_set", None), default_finding_registry.registered_keys())
self.assertNotIn(("anomaly_candidates", "v1"), default_finding_registry.registered_keys())
```

```python
# tests/runtime/evidence/test_correlate_test_forecast_extractor.py
expected = {
    "metric_frame",
    "candidate_set",
    "pairwise_time_series_association",
    "hypothesis_test",
    "forecast_series",
    "attribution_frame",
}
```

In `tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py`, update the detect success-empty payload helper to return `artifact_family: "candidate_set"` with `payload.items: []`, and update commit calls to use artifact type `"candidate_set"`.

- [ ] **Step 7: Run evidence tests**

Run:

```bash
make test TESTS='tests/runtime/evidence/test_detect_extractor.py tests/runtime/evidence/test_finding_extractor_registry.py tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py tests/runtime/test_session_state.py tests/core/test_family_contract.py tests/runtime/evidence/test_correlate_test_forecast_extractor.py'
```

Expected: PASS.

- [ ] **Step 8: Commit evidence changes**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add marivo/core/evidence/canonical_finding.py marivo/runtime/evidence/detect_extractor.py marivo/core/evidence/family_contract.py marivo/runtime/session.py marivo/runtime/report.py tests/runtime/evidence/test_detect_extractor.py tests/runtime/evidence/test_finding_extractor_registry.py tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py tests/runtime/test_session_state.py tests/core/test_family_contract.py tests/runtime/evidence/test_correlate_test_forecast_extractor.py
git diff --cached --name-status
git commit -m "feat: extract anomaly findings from candidate sets" -m "Move detect evidence extraction and success-empty handling to candidate_set artifacts." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 5: Lowering, HTTP, And MCP Surfaces

**Files:**

- Modify: `tests/runtime/test_aoi_lowering.py`
- Modify: `tests/transports/http/test_http_aoi_intents.py`
- Modify: `tests/transports/mcp/test_mcp_aoi_adapter.py`
- Modify: `tests/transports/mcp/test_tool_parity.py`
- Modify: `marivo/runtime/aoi_lowering.py`
- Modify: `marivo/contracts/aoi_projection.py`
- Modify: `marivo/transports/http/models/intent_response_models.py`
- Modify: `marivo/transports/mcp/tools/intents.py`

- [ ] **Step 1: Write failing lowering and transport tests**

In `tests/runtime/test_aoi_lowering.py`, replace the detect lowering test with:

```python
def test_lowers_detect_request_to_artifact_input_runner_params() -> None:
    request = aoi.Detect.model_validate(
        {
            "source_artifact_id": "artifact_source",
            "sensitivity": "balanced",
            "limit": 5,
        }
    )

    assert lower_aoi_request("detect", request) == {
        "source_artifact_id": "artifact_source",
        "sensitivity": "balanced",
        "limit": 5,
    }
```

In `tests/transports/http/test_http_aoi_intents.py`, replace old detect request tests with:

```python
def test_detect_accepts_aoi_artifact_input_request() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/intents/detect",
        json={
            "source_artifact_id": "artifact_source",
            "sensitivity": "balanced",
            "limit": 5,
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.detect_payload, aoi.Detect)
    assert runtime.detect_payload.source_artifact_id == "artifact_source"
    assert runtime.detect_payload.sensitivity == "balanced"


@pytest.mark.parametrize(
    "removed_field",
    ["metric", "time_scope", "granularity", "filter", "dimension", "strategy"],
)
def test_detect_rejects_removed_source_style_fields(removed_field: str) -> None:
    payload: dict[str, Any] = {"source_artifact_id": "artifact_source"}
    payload[removed_field] = "bad"

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/detect", json=payload)

    assert response.status_code == 422
```

Update `_FakeRuntime.detect` response fixture to return a `candidate_set` artifact:

```python
return {
    "intent_type": "detect",
    "step_type": "detect",
    "step_ref": {"session_id": session_id, "step_id": "step_detect", "step_type": "detect"},
    "artifact_id": "artifact_candidates",
    "result": {
        "artifact_id": "artifact_candidates",
        "artifact_family": "candidate_set",
        "shape": "point_anomaly_candidates",
        "subject": {
            "kind": "candidate_scan",
            "metric_ref": "metric.revenue",
            "source_artifact_id": "artifact_source",
            "source_artifact_family": "metric_frame",
            "source_shape": "time_series",
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "score", "value_type": "number", "nullable": False}],
        "capabilities": ["filterable"],
        "lineage": {
            "operation": "detect",
            "source_artifact_ids": ["artifact_source"],
            "strategy": "point_anomaly",
        },
        "payload": {
            "items": [],
            "scan_summary": {
                "scanned_series_count": 1,
                "total_candidate_count": 0,
            },
            "truncation": {
                "returned_candidate_count": 0,
                "total_candidate_count": 0,
                "truncated": False,
            },
            "quality": {"status": "detectable", "issues": []},
        },
    },
    "provenance": {},
    "product_metadata": None,
}
```

In `tests/transports/mcp/test_mcp_aoi_adapter.py`, replace detect helper tests with:

```python
def test_to_aoi_detect_request_builds_artifact_input_model() -> None:
    request = to_aoi_detect_request(
        source_artifact_id="artifact_source",
        sensitivity="balanced",
        limit=5,
    )

    assert request.source_artifact_id == "artifact_source"
    assert request.sensitivity == "balanced"
    assert request.limit == 5
```

In `tests/transports/mcp/test_tool_parity.py`, update detect schema assertions:

```python
assert "Detect anomaly candidates from a committed AOI artifact" in detect.description
assert "source_artifact_id" in detect_props
assert "metric" not in detect_props
assert "time_scope" not in detect_props
assert "granularity" not in detect_props
assert "strategy" not in detect_props
assert "filter_expression" not in detect_props
assert "dimension" not in detect_props
assert "Detection sensitivity" in detect_props["sensitivity"]["description"]
assert detect_props["sensitivity"]["default"] == "aggressive"
assert "Maximum anomaly candidates" in detect_props["limit"]["description"]
```

- [ ] **Step 2: Run lowering and transport tests to verify they fail**

Run:

```bash
make test TESTS='tests/runtime/test_aoi_lowering.py::test_lowers_detect_request_to_artifact_input_runner_params tests/transports/http/test_http_aoi_intents.py::test_detect_accepts_aoi_artifact_input_request tests/transports/http/test_http_aoi_intents.py::test_detect_rejects_removed_source_style_fields tests/transports/mcp/test_mcp_aoi_adapter.py::test_to_aoi_detect_request_builds_artifact_input_model tests/transports/mcp/test_tool_parity.py::test_detect_and_decompose_tool_schemas_document_aoi_parameters'
```

Expected: FAIL until lowering, HTTP response models, and MCP tool schemas are updated.

- [ ] **Step 3: Update AOI lowering**

In `marivo/runtime/aoi_lowering.py`, replace the `aoi.Detect` branch with:

```python
    if isinstance(request, aoi.Detect):
        return {
            "source_artifact_id": request.source_artifact_id,
            "sensitivity": request.sensitivity,
            "limit": request.limit,
        }
```

- [ ] **Step 4: Update AOI projection**

In `marivo/contracts/aoi_projection.py`, replace the detect branch in `project_aoi_artifact_result` with:

```python
    if intent_type == "detect":
        if payload.get("artifact_family") == "candidate_set":
            return artifact_to_envelope_result(validate_aoi_artifact(payload))
        raise ValueError("detect AOI projection requires a candidate_set artifact")
```

In `project_aoi_artifact`, add this raw result fast path near the existing observe/compare/decompose raw handling:

```python
    if (
        intent_type == "detect"
        and isinstance(raw, dict)
        and raw.get("artifact_family") == "candidate_set"
    ):
        return artifact_to_envelope_result(validate_aoi_artifact(raw))
```

In `_infer_intent_type`, replace the old anomaly-candidate inference with:

```python
    if payload.get("artifact_family") == "candidate_set":
        return "detect"
```

- [ ] **Step 5: Update HTTP response model**

In `marivo/transports/http/models/intent_response_models.py`, remove `_DetectArtifact` and `_DetectFailureArtifact`. Replace `DetectResponse` with:

```python
class _DetectFailureArtifact(aoi.Artifact2):
    result: None = None


class DetectResponse(_EnvelopeBase):
    result: aoi.CandidateSetArtifact | _DetectFailureArtifact
```

Add `aoi.CandidateSetArtifact` to the `DerivedBundleResult.aoi_artifacts` union if diagnose or other derived bundles can include detect artifacts:

```python
        | aoi.CandidateSetArtifact
```

- [ ] **Step 6: Update MCP detect adapter and tool**

In `marivo/transports/mcp/tools/intents.py`, replace `to_aoi_detect_request` with:

```python
def to_aoi_detect_request(
    source_artifact_id: str,
    sensitivity: Literal["conservative", "balanced", "aggressive"] = "aggressive",
    limit: int | None = None,
) -> aoi.Detect:
    return aoi.Detect.model_validate(
        _omit_none(
            {
                "source_artifact_id": source_artifact_id,
                "sensitivity": sensitivity,
                "limit": limit,
            }
        )
    )
```

Replace `register_detect` tool parameters with:

```python
def register_detect(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Detect anomaly candidates from a committed AOI artifact. "
            "metric_frame(time_series|panel) resolves to point anomalies; "
            "delta_frame(time_series_delta|panel_delta) resolves to period shifts."
        )
    )
    async def detect(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this intent call."),
        ],
        source_artifact_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Committed metric_frame or delta_frame artifact_id to scan.",
            ),
        ],
        sensitivity: Annotated[
            Literal["conservative", "balanced", "aggressive"],
            Field(description="Detection sensitivity preset."),
        ] = "aggressive",
        limit: Annotated[
            int | None,
            Field(ge=1, description="Maximum anomaly candidates to return."),
        ] = None,
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_detect_request(
            source_artifact_id=source_artifact_id,
            sensitivity=sensitivity,
            limit=limit,
        )
        return await call_runtime(
            runtime.detect, session_id=session_id, request=request, reasoning=reasoning
        )
```

- [ ] **Step 7: Run lowering and transport tests**

Run:

```bash
make test TESTS='tests/runtime/test_aoi_lowering.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py tests/transports/mcp/test_tool_parity.py'
```

Expected: PASS after removing old detect tests that assert source-style inputs.

- [ ] **Step 8: Commit transport changes**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add marivo/runtime/aoi_lowering.py marivo/contracts/aoi_projection.py marivo/transports/http/models/intent_response_models.py marivo/transports/mcp/tools/intents.py tests/runtime/test_aoi_lowering.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py tests/transports/mcp/test_tool_parity.py
git diff --cached --name-status
git commit -m "feat: expose artifact-input detect transports" -m "Update lowering, HTTP, and MCP detect surfaces for candidate_set output." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 6: Public Documentation Cutover

**Files:**

- Modify: `aoi-spec/spec.md`
- Modify: `docs/specs/analysis/intents/atomic/detect.md`
- Modify: `docs/api/intent-steps.md`
- Modify: `docs/api/README.md`
- Modify: `docs/api/runtime-status.md`

- [ ] **Step 1: Update AOI public spec**

In `aoi-spec/spec.md`, update the operation table row for `detect` to:

```markdown
| `detect` | artifact | `source_artifact_id`, `sensitivity?`, `limit?` |
```

Replace `anomaly_candidates_result` in the result schema list with:

```text
candidate_set_artifact
```

Replace the old `// anomaly_candidates_result` example with:

```jsonc
// candidate_set_artifact
artifact: { "artifact_id": string,
            "artifact_family": "candidate_set",
            "shape": "point_anomaly_candidates" | "period_shift_candidates",
            "subject": { "kind": "candidate_scan",
                         "metric_ref": string,
                         "source_artifact_id": string,
                         "source_artifact_family": "metric_frame" | "delta_frame",
                         "source_shape": string },
            "axes": [ { "kind": "time", "grain": TimeGranularity } |
                      { "kind": "dimension", "name": string } ],
            "measures": [ { "id": "score",
                            "value_type": "number",
                            "nullable": false } ],
            "capabilities": [ "filterable" ],
            "lineage": { "operation": "detect",
                         "source_artifact_ids": [ string ],
                         "strategy": "point_anomaly" | "period_shift" },
            "payload": { "items": [ { "item_id": string,
                                       "source_point_ref": FramePointRef,
                                       "source_delta_point_ref": FramePointRef,
                                       "window": { "start": "ISO8601",
                                                   "end": "ISO8601" },
                                       "keys": DimensionKeyMap | null,
                                       "value": number | null,
                                       "baseline_value": number | null,
                                       "delta_abs": number | null,
                                       "delta_pct": number | null,
                                       "score": number,
                                       "direction": "increase" | "decrease" | "unknown" } ],
                         "scan_summary": { "scanned_series_count": integer,
                                           "total_candidate_count": integer },
                         "truncation": { "returned_candidate_count": integer,
                                         "total_candidate_count": integer,
                                         "truncated": boolean },
                         "quality": { "status": "detectable" | "needs_attention",
                                      "issues": [ { "code": string,
                                                    "severity": "warning" | "error",
                                                    "message": string } ] } } }
```

Update the scoring semantics row to refer to:

```markdown
| `candidate_set.payload.items[].score` | Non-negative number, `[0, +infinity)`. | Higher means more anomalous within the same candidate set. Scores are not portable severity labels. |
```

- [ ] **Step 2: Rewrite atomic detect doc**

In `docs/specs/analysis/intents/atomic/detect.md`, replace the old source-style request sections with:

````markdown
## Request Shape

```json
{
  "source_artifact_id": "art_metric_or_delta_123",
  "sensitivity": "balanced",
  "limit": 5
}
```

## Typed Schema

```ts
type DetectRequest = {
  source_artifact_id: string;
  sensitivity?: "conservative" | "balanced" | "aggressive";
  limit?: number;
};
```

`strategy` is not a request field. Runtime derives it from the source artifact:

```text
metric_frame(time_series | panel) -> point_anomaly
delta_frame(time_series_delta | panel_delta) -> period_shift
```

Output type: `candidate_set`.
````

Replace the old unsupported input list with:

```markdown
## v1 不支持的输入

- `metric`
- `time_scope`
- `granularity`
- `filter`
- `dimension`
- `strategy`
- `metric_frame(scalar | segmented)`
- `delta_frame(scalar_delta | segmented_delta)`
- `attribution_frame`
- `candidate_set`
- hidden previous-adjacent baseline queries inside `detect`
```

Replace old artifact examples with the `candidate_set` fields from the design spec.

- [ ] **Step 3: Update API docs**

In `docs/api/intent-steps.md`, replace detect request example with:

```json
{
  "source_artifact_id": "art_metric_or_delta_123",
  "sensitivity": "balanced",
  "limit": 5
}
```

Update response prose to say:

```markdown
`detect` returns a top-level `candidate_set` artifact. For `metric_frame(time_series|panel)`
inputs it emits `point_anomaly_candidates`; for `delta_frame(time_series_delta|panel_delta)`
inputs it emits `period_shift_candidates`.
```

In `docs/api/README.md`, change the detect summary row to:

```markdown
| `detect` | Atomic | Scan a committed metric_frame or delta_frame artifact and return a candidate_set |
```

In `docs/api/runtime-status.md`, change queued artifact docs from `anomaly_candidates` to `candidate_set`.

- [ ] **Step 4: Search for stale public detect contract wording**

Run:

```bash
rg -n "anomaly_candidates|AnomalyCandidatesResult|detect\\.granularity|detect\\.strategy|source-style detect|metric.*time_scope.*granularity" aoi-spec docs marivo tests
```

Expected: remaining matches are either migration notes in the approved design spec or non-detect contexts. Update any live docs or tests that still describe old public detect behavior.

- [ ] **Step 5: Commit documentation changes**

Run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add aoi-spec/spec.md docs/specs/analysis/intents/atomic/detect.md docs/api/intent-steps.md docs/api/README.md docs/api/runtime-status.md
git diff --cached --name-status
git commit -m "docs: document artifact-input detect" -m "Update AOI and API docs for candidate_set detect semantics." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

## Task 7: Final Validation And Cleanup

**Files:**

- Modify: files touched by Tasks 1-6 only when validation reports a concrete failure.

- [ ] **Step 1: Run targeted validation suite**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py'
make test TESTS='tests/contracts/test_aoi_runtime_contract.py'
make test TESTS='tests/runtime/intents/test_metric_frame_helpers.py'
make test TESTS='tests/runtime/intents/test_detect_runner.py'
make test TESTS='tests/runtime/evidence/test_detect_extractor.py'
make test TESTS='tests/runtime/evidence/test_finding_extractor_registry.py'
make test TESTS='tests/runtime/evidence/test_evidence_pipeline_family_behaviors.py'
make test TESTS='tests/runtime/test_session_state.py'
make test TESTS='tests/runtime/test_aoi_lowering.py'
make test TESTS='tests/transports/http/test_http_aoi_intents.py'
make test TESTS='tests/transports/mcp/test_mcp_aoi_adapter.py'
make test TESTS='tests/transports/mcp/test_tool_parity.py'
```

Expected: all targeted tests PASS.

- [ ] **Step 2: Run typecheck**

Run:

```bash
make typecheck
```

Expected: PASS. Fix typed-dict or generated-model type errors in the touched files only.

- [ ] **Step 3: Run lint**

Run:

```bash
make lint
```

Expected: PASS. Fix lint errors in touched files only.

- [ ] **Step 4: Run stale-contract search**

Run:

```bash
rg -n "artifact_type.: .anomaly_candidates|AnomalyCandidatesResult|source_artifact_id.*metric.*time_scope|strategy.*point_anomaly.*period_shift|detect accepts only AOI request fields" marivo tests docs aoi-spec
```

Expected:

- No runtime, transport, or public doc still exposes `AnomalyCandidatesResult`.
- No runtime code commits `artifact_type: "anomaly_candidates"`.
- No detect request path accepts `metric`, `time_scope`, `granularity`, `filter`, `dimension`, or `strategy`.
- Historical references are acceptable only inside archived or approved design docs.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short --untracked-files=all
git diff --stat
git diff -- marivo/runtime/intents/detect.py marivo/runtime/evidence/detect_extractor.py marivo/transports/mcp/tools/intents.py
```

Expected: diff contains only detect candidate-set cutover work.

- [ ] **Step 6: Final commit if validation fixes changed files**

If Steps 1-5 required additional fixes after the previous task commits, run the mandatory commit-attribution skill, inspect staged scope, then commit:

```bash
git status --short --untracked-files=all
git add aoi-spec docs marivo tests
git diff --cached --name-status
git commit -m "fix: complete detect candidate set cutover" -m "Resolve final validation issues for artifact-input detect." -m "Co-Authored-By: Codex:gpt-5 [Edit] [Bash]"
```

If no files changed after validation, do not create an empty commit.

## Completion Criteria

- AOI `Detect` request accepts only `source_artifact_id`, `sensitivity`, and `limit`.
- `strategy` is inferred from source artifact family and shape.
- Runtime `detect` performs no metric SQL compilation, source table execution, or hidden baseline query.
- `metric_frame(time_series|panel)` produces `candidate_set(point_anomaly_candidates)`.
- `delta_frame(time_series_delta|panel_delta)` produces `candidate_set(period_shift_candidates)`.
- `candidate_set.payload.items[]` include stable `source_point_ref` or `source_delta_point_ref`.
- Evidence extraction registers under `("candidate_set", None)` and emits canonical `anomaly_candidate` findings.
- HTTP and MCP reject removed source-style detect fields.
- Public AOI/API/docs no longer describe old detect request or `AnomalyCandidatesResult`.
- Targeted tests, `make typecheck`, and `make lint` pass.
