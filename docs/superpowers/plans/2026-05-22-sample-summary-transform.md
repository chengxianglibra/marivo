# Sample Summary Transform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AOI-standard `sample_summary` as a first-class transform operation that produces `sample_frame`, and make `test` consume sample-frame artifacts as its canonical input.

**Architecture:** Update AOI first, regenerate generated models, then wire transform lowering/dispatch into runtime, HTTP, and MCP. Keep `sample_summary` outside atomic intents by adding transform-specific registries and routes, while `test` remains an atomic intent with ref-type sample-frame inputs.

**Tech Stack:** Python 3.12, Pydantic generated AOI models, FastAPI HTTP routes, FastMCP tools, Marivo runtime artifact store, Makefile test entrypoints.

---

## File Structure

- Modify `aoi-spec/schema/aoi.schema.json`: canonical AOI JSON Schema. Add `$defs.transforms.sample_summary`, `SampleFrameArtifact`, sample-frame primitives, root union entry, and update `requests.test`.
- Modify `aoi-spec/schema/aoi.schema.yaml`: human-readable schema companion. Mirror the JSON schema shape.
- Modify `aoi-spec/spec.md`: standard text for transforms, sample-frame artifact, and new test request contract.
- Modify `aoi-spec/README.md`: operation list and artifact list.
- Modify `aoi-spec/CHANGELOG.md`: breaking change entry.
- Create `aoi-spec/examples/sample_summary/request.json`: transform request example.
- Create `aoi-spec/examples/sample_summary/success.json`: sample-frame artifact example.
- Create `aoi-spec/examples/sample_summary/failed.json`: transform failure artifact example.
- Modify `aoi-spec/examples/test/*.json`: rewrite test requests to sample-frame refs and keep result examples valid.
- Modify `marivo/contracts/generated/aoi.py`: regenerated output from `scripts/generate_contract_models.py`.
- Modify `marivo/contracts/aoi_runtime.py`: add AOI transform type alias, registry, validation support for `sample_frame`.
- Modify `marivo/runtime/aoi_lowering.py`: add `lower_aoi_transform_request`, update the `lower_aoi_request` branch for `test`.
- Modify `marivo/runtime/intent_execution.py`: add transform dispatch path and `sample_summary` wrapper.
- Modify `marivo/runtime/runtime.py`: add `sample_summary` runtime method.
- Create `marivo/runtime/intents/sample_summary.py`: sample-summary transform runner and pure helpers for metric-frame validation and summary computation.
- Modify `marivo/runtime/intents/test.py`: remove source-slice summary computation from runner; consume two sample-frame artifacts.
- Modify `marivo/runtime/intents/validate.py`: expand validate through observe, sample_summary, sample_summary, test.
- Modify `marivo/transports/http/models/intent_response_models.py`: add `SampleSummaryResponse` and include `SampleFrameArtifact` in derived bundles.
- Modify `marivo/transports/http/sessions.py`: add `/sessions/{session_id}/transforms/sample_summary` route.
- Modify `marivo/transports/http/errors.py`: add schema guidance for transform endpoint.
- Modify `marivo/transports/mcp/tools/schemas.py`: add MCP sample-summary DTO if needed and update test hypothesis descriptions.
- Modify `marivo/transports/mcp/tools/intents.py`: add `to_aoi_sample_summary_request`, update `to_aoi_test_request`, register `sample_summary` tool, update `test_intent`.
- Modify `marivo/transports/mcp/tools/__init__.py`: register the new MCP tool.
- Modify `docs/api/intent-steps.md`: document transform endpoint and sample-frame test input.
- Modify `docs/specs/analysis/foundations/analysis-operation-architecture.md`: remove wording that implies `sample_summary` accepts or chooses `grain`.
- Modify `docs/specs/analysis/intents/atomic/test.md`: document sample-frame canonical input.
- Modify `docs/marivo-for-builders.zh.md`: update workflow examples that currently describe direct source-style test.
- Modify tests listed in the tasks below.

## Task 1: Lock The AOI Contract With Failing Tests

**Files:**
- Modify: `tests/contracts/test_generated_models.py`
- Modify: `tests/runtime/test_aoi_lowering.py`

- [ ] **Step 1: Add generated-model tests for sample_summary, sample_frame, and test refs**

Append these tests near the existing AOI `test` model tests in `tests/contracts/test_generated_models.py`:

```python
def _aoi_sample_summary_request_payload() -> dict[str, Any]:
    return {
        "source_artifact_id": "art_metric_frame_current",
        "sample_kind": "numeric",
    }


def _aoi_sample_frame_payload() -> dict[str, Any]:
    return {
        "artifact_id": "art_sample_current",
        "artifact_family": "sample_frame",
        "shape": "numeric_summary",
        "subject": {
            "kind": "sample_summary",
            "metric_ref": "metric.revenue",
            "source_artifact_id": "art_metric_frame_current",
        },
        "axes": [{"kind": "sample", "source_axis": "time", "grain": "day"}],
        "measures": [
            {"id": "n", "value_type": "integer", "nullable": False},
            {"id": "mean", "value_type": "number", "nullable": True},
            {"id": "standard_deviation", "value_type": "number", "nullable": True},
        ],
        "lineage": {
            "operation": "sample_summary",
            "source_artifact_ids": ["art_metric_frame_current"],
        },
        "payload": {
            "summary": {"n": 7, "mean": 120.0, "standard_deviation": 10.0},
            "quality": {"status": "test_ready", "issues": []},
        },
    }


def test_aoi_sample_summary_transform_accepts_public_shape() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.SampleSummary.model_validate(_aoi_sample_summary_request_payload())

    assert request.source_artifact_id == "art_metric_frame_current"
    assert request.sample_kind == "numeric"


def test_aoi_sample_summary_transform_rejects_grain() -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_sample_summary_request_payload()
    payload["grain"] = "day"

    with pytest.raises(ValidationError):
        aoi.SampleSummary.model_validate(payload)


def test_aoi_sample_frame_artifact_accepts_public_shape() -> None:
    from marivo.contracts.generated import aoi

    artifact = aoi.SampleFrameArtifact.model_validate(_aoi_sample_frame_payload())

    assert artifact.artifact_family == "sample_frame"
    assert artifact.shape == "numeric_summary"
    assert artifact.axes[0].grain == "day"
    assert artifact.payload.summary.n == 7


def test_aoi_sample_frame_rejects_non_numeric_shape() -> None:
    from marivo.contracts.generated import aoi

    payload = _aoi_sample_frame_payload()
    payload["shape"] = "rate_summary"

    with pytest.raises(ValidationError):
        aoi.SampleFrameArtifact.model_validate(payload)


def test_aoi_test_accepts_sample_frame_refs_only() -> None:
    from marivo.contracts.generated import aoi

    request = aoi.Test.model_validate(
        {
            "current_sample_artifact_id": "art_sample_current",
            "baseline_sample_artifact_id": "art_sample_baseline",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "two_sided",
                "significance": "balanced",
            },
        }
    )

    assert request.current_sample_artifact_id == "art_sample_current"
    assert request.baseline_sample_artifact_id == "art_sample_baseline"
    assert request.hypothesis.family == "two_sample_mean"


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"metric": "metric.revenue"},
        {
            "current": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-08T00:00:00Z",
                }
            }
        },
        {
            "baseline": {
                "time_scope": {
                    "field": "event_time",
                    "start": "2026-01-08T00:00:00Z",
                    "end": "2026-01-15T00:00:00Z",
                }
            }
        },
        {"grain": "day"},
        {"kind": "numeric"},
    ],
)
def test_aoi_test_rejects_removed_source_style_fields(payload_patch: dict[str, Any]) -> None:
    from marivo.contracts.generated import aoi

    payload = {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }
    payload.update(payload_patch)

    with pytest.raises(ValidationError):
        aoi.Test.model_validate(payload)
```

- [ ] **Step 2: Add lowering tests for transform request and new test request**

Update the import in `tests/runtime/test_aoi_lowering.py`:

```python
from marivo.runtime.aoi_lowering import (
    lower_aoi_derived_request,
    lower_aoi_request,
    lower_aoi_transform_request,
)
```

Replace the existing `test_lowers_test_request_to_runner_params` and `test_lowers_test_request_with_filters_to_runner_params` tests with:

```python
def test_lowers_sample_summary_transform_to_runner_params() -> None:
    request = aoi.SampleSummary(
        source_artifact_id="art_metric_frame_current",
        sample_kind="numeric",
    )

    assert lower_aoi_transform_request("sample_summary", request) == {
        "source_artifact_id": "art_metric_frame_current",
        "sample_kind": "numeric",
    }


def test_lowers_test_request_to_sample_frame_ref_runner_params() -> None:
    request = aoi.Test(
        current_sample_artifact_id="art_sample_current",
        baseline_sample_artifact_id="art_sample_baseline",
        hypothesis=aoi.Hypothesis(
            family="two_sample_mean",
            alternative="greater",
            significance="balanced",
        ),
    )

    assert lower_aoi_request("test", request) == {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "greater",
            "significance": "balanced",
        },
    }
```

- [ ] **Step 3: Run the failing contract tests**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py::test_aoi_sample_summary_transform_accepts_public_shape tests/contracts/test_generated_models.py::test_aoi_sample_frame_artifact_accepts_public_shape tests/contracts/test_generated_models.py::test_aoi_test_accepts_sample_frame_refs_only tests/runtime/test_aoi_lowering.py::test_lowers_sample_summary_transform_to_runner_params tests/runtime/test_aoi_lowering.py::test_lowers_test_request_to_sample_frame_ref_runner_params'
```

Expected: FAIL because generated `aoi.SampleSummary`, generated `aoi.SampleFrameArtifact`, and `lower_aoi_transform_request` do not exist yet, and `aoi.Test` still has the old source-style shape.

- [ ] **Step 4: Commit the failing tests**

Run:

```bash
git add tests/contracts/test_generated_models.py tests/runtime/test_aoi_lowering.py
git commit -m "test: lock sample summary AOI contract" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds with only test files staged. If hooks rewrite formatting, run `git status --short`, restage only these two files, and retry the same commit command.

## Task 2: Update AOI Schema, Examples, And Generated Models

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.json`
- Modify: `aoi-spec/schema/aoi.schema.yaml`
- Modify: `aoi-spec/spec.md`
- Modify: `aoi-spec/README.md`
- Modify: `aoi-spec/CHANGELOG.md`
- Create: `aoi-spec/examples/sample_summary/request.json`
- Create: `aoi-spec/examples/sample_summary/success.json`
- Create: `aoi-spec/examples/sample_summary/failed.json`
- Modify: `aoi-spec/examples/test/numeric-request.json`
- Modify: `aoi-spec/examples/test/numeric-two-sided-request.json`
- Modify: `aoi-spec/examples/test/success.json`
- Modify: `aoi-spec/examples/test/failed.json`
- Modify: `marivo/contracts/generated/aoi.py`

- [ ] **Step 1: Add AOI schema definitions**

Edit `aoi-spec/schema/aoi.schema.json` so the root `oneOf` includes:

```json
{
  "$ref": "#/$defs/transforms/sample_summary"
}
```

Add this sibling to `$defs.requests` and `$defs.derived_requests`:

```json
"transforms": {
  "sample_summary": {
    "title": "SampleSummary",
    "type": "object",
    "additionalProperties": false,
    "required": ["source_artifact_id", "sample_kind"],
    "properties": {
      "source_artifact_id": {
        "type": "string",
        "minLength": 1
      },
      "sample_kind": {
        "type": "string",
        "enum": ["numeric"]
      }
    }
  }
}
```

Replace `$defs.requests.test` with:

```json
"test": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "current_sample_artifact_id",
    "baseline_sample_artifact_id",
    "hypothesis"
  ],
  "properties": {
    "current_sample_artifact_id": {
      "type": "string",
      "minLength": 1
    },
    "baseline_sample_artifact_id": {
      "type": "string",
      "minLength": 1
    },
    "hypothesis": {
      "$ref": "#/$defs/primitives/Hypothesis"
    }
  }
}
```

Add sample-frame artifact definitions under `$defs.artifacts`:

```json
"SampleAxis": {
  "type": "object",
  "additionalProperties": false,
  "required": ["kind", "source_axis", "grain"],
  "properties": {
    "kind": { "const": "sample" },
    "source_axis": { "const": "time" },
    "grain": { "$ref": "#/$defs/primitives/TimeGranularity" }
  }
},
"SampleMeasure": {
  "type": "object",
  "additionalProperties": false,
  "required": ["id", "value_type", "nullable"],
  "properties": {
    "id": {
      "type": "string",
      "enum": ["n", "mean", "standard_deviation"]
    },
    "value_type": {
      "type": "string",
      "enum": ["integer", "number"]
    },
    "nullable": { "type": "boolean" }
  }
},
"SampleFrameSubject": {
  "type": "object",
  "additionalProperties": false,
  "required": ["kind", "metric_ref", "source_artifact_id"],
  "properties": {
    "kind": { "const": "sample_summary" },
    "metric_ref": { "type": "string", "minLength": 1 },
    "source_artifact_id": { "type": "string", "minLength": 1 }
  }
},
"SampleSummaryPayload": {
  "type": "object",
  "additionalProperties": false,
  "required": ["summary", "quality"],
  "properties": {
    "summary": {
      "type": "object",
      "additionalProperties": false,
      "required": ["n", "mean", "standard_deviation"],
      "properties": {
        "n": { "type": "integer", "minimum": 0 },
        "mean": { "anyOf": [{ "type": "number" }, { "type": "null" }] },
        "standard_deviation": {
          "anyOf": [{ "type": "number" }, { "type": "null" }]
        }
      }
    },
    "quality": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "issues"],
      "properties": {
        "status": {
          "type": "string",
          "enum": ["test_ready", "insufficient_data", "unsupported_source"]
        },
        "issues": {
          "type": "array",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["code", "message"],
            "properties": {
              "code": { "type": "string", "minLength": 1 },
              "message": { "type": "string", "minLength": 1 }
            }
          }
        }
      }
    }
  }
},
"SampleFrameLineage": {
  "type": "object",
  "additionalProperties": false,
  "required": ["operation", "source_artifact_ids"],
  "properties": {
    "operation": { "const": "sample_summary" },
    "source_artifact_ids": {
      "type": "array",
      "minItems": 1,
      "items": { "type": "string", "minLength": 1 }
    }
  }
},
"SampleFrameArtifact": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "artifact_id",
    "artifact_family",
    "shape",
    "subject",
    "axes",
    "measures",
    "lineage",
    "payload"
  ],
  "properties": {
    "artifact_id": { "type": "string", "minLength": 1 },
    "artifact_family": { "const": "sample_frame" },
    "shape": { "const": "numeric_summary" },
    "subject": { "$ref": "#/$defs/artifacts/SampleFrameSubject" },
    "axes": {
      "type": "array",
      "minItems": 1,
      "maxItems": 1,
      "items": { "$ref": "#/$defs/artifacts/SampleAxis" }
    },
    "measures": {
      "type": "array",
      "minItems": 3,
      "maxItems": 3,
      "items": { "$ref": "#/$defs/artifacts/SampleMeasure" }
    },
    "lineage": { "$ref": "#/$defs/artifacts/SampleFrameLineage" },
    "payload": { "$ref": "#/$defs/artifacts/SampleSummaryPayload" }
  }
}
```

Add `SampleFrameArtifact` to every AOI artifact union that currently includes top-level frame artifacts. The success `Artifact.result` union should include it only if that union is used for top-level artifact validation. If the union keeps top-level frame artifacts as direct shapes, include `SampleFrameArtifact` in the same place as `MetricFrameArtifact`, `DeltaFrameArtifact`, `AttributionFrameArtifact`, and `CandidateSetArtifact`.

- [ ] **Step 2: Mirror the schema changes in YAML**

Edit `aoi-spec/schema/aoi.schema.yaml` with the same transform request, test request, and sample-frame artifact definitions. Keep it single-document YAML and preserve existing ordering style.

- [ ] **Step 3: Update AOI spec prose**

In `aoi-spec/spec.md`, update the operation inventory from:

```markdown
- **7 atomic intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
```

to:

```markdown
- **7 atomic intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
- **1 standard transform**: `sample_summary`
```

Update the per-intent table row for `test` to:

```markdown
| `test` | ref | `current_sample_artifact_id: string`, `baseline_sample_artifact_id: string`, `hypothesis: Hypothesis` |
```

Add a transform table after the per-intent table:

```markdown
#### 4.1.2 Transform input typing

| Transform | Input mode | Required inputs |
| --------- | ---------- | --------------- |
| `sample_summary` | ref | `source_artifact_id: string`, `sample_kind: "numeric"` |

`sample_summary` consumes an existing `metric_frame` and produces a `sample_frame`. It does not accept `grain`, `metric`, `time_scope`, or `filter`; those are inherited from the source `metric_frame`.
```

Renumber later headings if needed, and add `sample_frame_artifact` to the result schema catalog.

- [ ] **Step 4: Add sample_summary examples**

Create `aoi-spec/examples/sample_summary/request.json`:

```json
{
  "source_artifact_id": "art_metric_frame_current",
  "sample_kind": "numeric"
}
```

Create `aoi-spec/examples/sample_summary/success.json`:

```json
{
  "artifact_id": "art_sample_current",
  "artifact_family": "sample_frame",
  "shape": "numeric_summary",
  "subject": {
    "kind": "sample_summary",
    "metric_ref": "metric.revenue",
    "source_artifact_id": "art_metric_frame_current"
  },
  "axes": [
    {
      "kind": "sample",
      "source_axis": "time",
      "grain": "day"
    }
  ],
  "measures": [
    {
      "id": "n",
      "value_type": "integer",
      "nullable": false
    },
    {
      "id": "mean",
      "value_type": "number",
      "nullable": true
    },
    {
      "id": "standard_deviation",
      "value_type": "number",
      "nullable": true
    }
  ],
  "lineage": {
    "operation": "sample_summary",
    "source_artifact_ids": ["art_metric_frame_current"]
  },
  "payload": {
    "summary": {
      "n": 7,
      "mean": 120.0,
      "standard_deviation": 10.0
    },
    "quality": {
      "status": "test_ready",
      "issues": []
    }
  }
}
```

Create `aoi-spec/examples/sample_summary/failed.json`:

```json
{
  "artifact_id": "art_sample_failed",
  "failure": {
    "code": "UNSUPPORTED_SOURCE_SHAPE",
    "message": "sample_summary numeric v1 requires a time_series metric_frame source"
  }
}
```

- [ ] **Step 5: Rewrite test request examples**

Update `aoi-spec/examples/test/numeric-request.json` and `aoi-spec/examples/test/numeric-two-sided-request.json` to:

```json
{
  "current_sample_artifact_id": "art_sample_current",
  "baseline_sample_artifact_id": "art_sample_baseline",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "two_sided",
    "significance": "balanced"
  }
}
```

If one file is intended to show one-sided alternatives, use `"greater"` in that file and keep the same ref shape.

- [ ] **Step 6: Update README and changelog**

In `aoi-spec/README.md`, update the operation and artifact bullets to include:

```markdown
- Standard transforms: `sample_summary`
- Artifact types: `metric_frame`, `sample_frame`, `delta_frame`, `attribution_frame`, `candidate_set`, `association_result`, `hypothesis_test_result`, `forecast_series`
```

In `aoi-spec/CHANGELOG.md`, add under the current unreleased or v0.2 section:

```markdown
- Added `sample_summary` as the first AOI standard transform operation.
- Added `sample_frame` artifacts for reusable test-ready sample summaries.
- Changed `test` to consume `current_sample_artifact_id` and `baseline_sample_artifact_id`; removed source-style `metric`, `current`, `baseline`, `grain`, and `kind` fields.
```

- [ ] **Step 7: Regenerate generated models**

Run:

```bash
./.venv/bin/python scripts/generate_contract_models.py
```

Expected: `marivo/contracts/generated/aoi.py` changes and contains generated classes named `SampleSummary` and `SampleFrameArtifact`.

- [ ] **Step 8: Run AOI checks**

Run:

```bash
./.venv/bin/python scripts/generate_contract_models.py --check
make test TESTS='tests/contracts/test_generated_models.py::test_aoi_example_validates tests/contracts/test_generated_models.py::test_aoi_sample_summary_transform_accepts_public_shape tests/contracts/test_generated_models.py::test_aoi_sample_summary_transform_rejects_grain tests/contracts/test_generated_models.py::test_aoi_sample_frame_artifact_accepts_public_shape tests/contracts/test_generated_models.py::test_aoi_test_accepts_sample_frame_refs_only tests/contracts/test_generated_models.py::test_aoi_test_rejects_removed_source_style_fields'
```

Expected: PASS.

- [ ] **Step 9: Commit AOI schema and generated models**

Run:

```bash
git add aoi-spec marivo/contracts/generated/aoi.py tests/contracts/test_generated_models.py
git commit -m "feat: add sample summary AOI transform contract" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds. If hooks rewrite generated formatting, restage the same files and retry.

## Task 3: Add Transform Runtime Contracts, Lowering, And Dispatch

**Files:**
- Modify: `marivo/contracts/aoi_runtime.py`
- Modify: `marivo/runtime/aoi_lowering.py`
- Modify: `marivo/runtime/intent_execution.py`
- Modify: `marivo/runtime/runtime.py`
- Modify: `tests/runtime/test_aoi_intent_execution.py`
- Modify: `tests/runtime/test_runtime_intent_dispatch.py`
- Modify: `tests/runtime/test_aoi_lowering.py`

- [ ] **Step 1: Add failing dispatch tests**

Append to `tests/runtime/test_aoi_intent_execution.py`:

```python
def _sample_summary_request() -> aoi.SampleSummary:
    return aoi.SampleSummary(
        source_artifact_id="art_metric_frame_current",
        sample_kind="numeric",
    )


def test_sample_summary_transform_dispatches_to_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    request = _sample_summary_request()

    def runner(runtime_arg: Any, session_id: str, params: dict[str, Any], *, reasoning: str | None = None) -> dict[str, Any]:
        assert runtime_arg is runtime
        assert session_id == "s1"
        assert params == {
            "source_artifact_id": "art_metric_frame_current",
            "sample_kind": "numeric",
        }
        assert reasoning == "why"
        return {"status": "ok"}

    monkeypatch.setattr(intent_execution, "_assert_session_is_open", lambda *_: None)
    monkeypatch.setitem(intent_execution.TRANSFORM_RUNNERS, "sample_summary", runner)

    result = intent_execution.sample_summary(runtime, "s1", request, reasoning="why")

    assert result == {"status": "ok"}
```

Append to `tests/runtime/test_runtime_intent_dispatch.py`:

```python
def test_sample_summary_runtime_method_dispatches() -> None:
    rt = _make_runtime()
    request = aoi.SampleSummary(
        source_artifact_id="art_metric_frame_current",
        sample_kind="numeric",
    )

    with patch(
        "marivo.runtime.intent_execution.sample_summary",
        return_value={"status": "ok"},
    ) as mock_fn:
        result = rt.sample_summary("s1", request, reasoning="why")

    mock_fn.assert_called_once_with(rt, SessionId("s1"), request, reasoning="why")
    assert result == {"status": "ok"}
```

- [ ] **Step 2: Run failing dispatch tests**

Run:

```bash
make test TESTS='tests/runtime/test_aoi_intent_execution.py::test_sample_summary_transform_dispatches_to_runner tests/runtime/test_runtime_intent_dispatch.py::test_sample_summary_runtime_method_dispatches tests/runtime/test_aoi_lowering.py::test_lowers_sample_summary_transform_to_runner_params tests/runtime/test_aoi_lowering.py::test_lowers_test_request_to_sample_frame_ref_runner_params'
```

Expected: FAIL because transform aliases, registries, dispatch functions, and runtime method do not exist.

- [ ] **Step 3: Add AOI transform registry**

In `marivo/contracts/aoi_runtime.py`, add the transform type alias after `AoiDerivedRequest`:

```python
AoiTransformRequest: TypeAlias = aoi.SampleSummary  # noqa: UP040
```

Update `RuntimeIntentEnvelope.request` to include transforms:

```python
request: AoiAtomicRequest | AoiDerivedRequest | AoiTransformRequest
```

Add transform registry and assertion:

```python
AOI_TRANSFORM_OPERATION_REGISTRY: dict[str, AoiOperationDefinition] = {
    "sample_summary": AoiOperationDefinition("sample_summary", (aoi.SampleSummary,)),
}


def assert_transform_request_matches_operation(
    operation_type: str,
    request: AoiTransformRequest,
) -> None:
    definition = AOI_TRANSFORM_OPERATION_REGISTRY.get(operation_type)
    if definition is None:
        raise ValueError(f"AOI_TRANSFORM_OPERATION_UNKNOWN: {operation_type}")
    if not isinstance(request, definition.request_types):
        raise ValueError(
            f"AOI_TRANSFORM_OPERATION_MISMATCH: operation_type={operation_type} "
            f"request_type={type(request).__name__}"
        )
```

Update `AoiArtifact` and `validate_aoi_artifact` to include `aoi.SampleFrameArtifact` in the same branches as other top-level artifacts:

```python
AoiArtifact = (
    aoi.MetricFrameArtifact
    | aoi.SampleFrameArtifact
    | aoi.DeltaFrameArtifact
    | aoi.AttributionFrameArtifact
    | aoi.CandidateSetArtifact
    | aoi.Artifact1
    | aoi.Artifact2
)
```

Add to the `isinstance` tuple and mapping validation:

```python
if value.get("artifact_family") == "sample_frame":
    return aoi.SampleFrameArtifact.model_validate(value)
```

Update `artifact_to_envelope_result` so it treats `sample_frame` like other top-level frame artifacts:

```python
if data.get("artifact_family") in ("metric_frame", "sample_frame", "delta_frame", "candidate_set"):
    return data
```

- [ ] **Step 4: Add transform lowering**

In `marivo/runtime/aoi_lowering.py`, update imports:

```python
from marivo.contracts.aoi_runtime import (
    AoiAtomicRequest,
    AoiDerivedRequest,
    AoiTransformRequest,
    assert_derived_request_matches_intent,
    assert_request_matches_intent,
    assert_transform_request_matches_operation,
)
```

Add:

```python
def lower_aoi_transform_request(
    operation_type: str,
    request: AoiTransformRequest,
) -> dict[str, Any]:
    assert_transform_request_matches_operation(operation_type, request)

    if isinstance(request, aoi.SampleSummary):
        return {
            "source_artifact_id": request.source_artifact_id,
            "sample_kind": request.sample_kind,
        }
    raise TypeError(f"Unsupported AOI transform request type: {type(request).__name__}")
```

Replace the existing `aoi.Test` lowering branch with:

```python
if isinstance(request, aoi.Test):
    return {
        "current_sample_artifact_id": request.current_sample_artifact_id,
        "baseline_sample_artifact_id": request.baseline_sample_artifact_id,
        "hypothesis": request.hypothesis.model_dump(exclude_none=True),
    }
```

- [ ] **Step 5: Add transform dispatch**

In `marivo/runtime/intent_execution.py`, import the transform type and assertion:

```python
from marivo.contracts.aoi_runtime import (
    AoiAtomicRequest,
    AoiDerivedRequest,
    AoiTransformRequest,
    assert_request_matches_intent,
    assert_transform_request_matches_operation,
)
from marivo.runtime.aoi_lowering import (
    lower_aoi_derived_request,
    lower_aoi_request,
    lower_aoi_transform_request,
)
from marivo.runtime.intents.sample_summary import run_sample_summary_transform
```

Add wrapper:

```python
def sample_summary(
    runtime: MarivoRuntime,
    session_id: SessionId,
    request: AoiTransformRequest,
    *,
    reasoning: str | None = None,
) -> dict[str, Any]:
    return _run_aoi_transform(runtime, "sample_summary", session_id, request, reasoning=reasoning)
```

Add registry:

```python
TRANSFORM_RUNNERS: dict[str, _IntentRunner] = {
    "sample_summary": run_sample_summary_transform,
}
```

Add to `INTENT_DISPATCHERS` only if the existing generic dispatcher needs every action name. If added, keep the operation name `"sample_summary"` and do not call it an intent in docs or comments.

Add runner:

```python
def _run_aoi_transform(
    runtime: MarivoRuntime,
    operation_type: str,
    session_id: SessionId,
    request: AoiTransformRequest,
    *,
    reasoning: str | None = None,
) -> dict[str, Any]:
    _assert_session_is_open(runtime, session_id)
    assert_transform_request_matches_operation(operation_type, request)
    params = lower_aoi_transform_request(operation_type, request)
    return TRANSFORM_RUNNERS[operation_type](runtime, str(session_id), params, reasoning=reasoning)
```

- [ ] **Step 6: Add runtime method**

In `marivo/runtime/runtime.py`, update imports to include `AoiTransformRequest`, then add:

```python
def sample_summary(
    self, session_id: str, request: AoiTransformRequest, *, reasoning: str | None = None
) -> dict[str, Any]:
    return intent_execution.sample_summary(self, SessionId(session_id), request, reasoning=reasoning)
```

- [ ] **Step 7: Run dispatch tests**

Run:

```bash
make test TESTS='tests/runtime/test_aoi_intent_execution.py::test_sample_summary_transform_dispatches_to_runner tests/runtime/test_runtime_intent_dispatch.py::test_sample_summary_runtime_method_dispatches tests/runtime/test_aoi_lowering.py::test_lowers_sample_summary_transform_to_runner_params tests/runtime/test_aoi_lowering.py::test_lowers_test_request_to_sample_frame_ref_runner_params'
```

Expected: PASS after `marivo/runtime/intents/sample_summary.py` exists. If the import fails because the runner file is not created yet, create the file with the minimal stub in the next step before rerunning:

```python
from __future__ import annotations

from typing import Any


def run_sample_summary_transform(
    runtime: Any,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError("sample_summary transform runner is implemented in Task 4")
```

- [ ] **Step 8: Commit transform dispatch contracts**

Run:

```bash
git add marivo/contracts/aoi_runtime.py marivo/runtime/aoi_lowering.py marivo/runtime/intent_execution.py marivo/runtime/runtime.py marivo/runtime/intents/sample_summary.py tests/runtime/test_aoi_intent_execution.py tests/runtime/test_runtime_intent_dispatch.py tests/runtime/test_aoi_lowering.py
git commit -m "feat: wire AOI transform dispatch" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 4: Implement sample_summary Transform Runner

**Files:**
- Modify: `marivo/runtime/intents/sample_summary.py`
- Create: `tests/runtime/intents/test_sample_summary_runner.py`

- [ ] **Step 1: Write failing runner tests**

Create `tests/runtime/intents/test_sample_summary_runner.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents.sample_summary import (
    compute_numeric_summary_from_metric_frame,
    extract_time_sample_axis,
    run_sample_summary_transform,
)


def _metric_frame_artifact(
    *,
    shape: str = "time_series",
    values: list[float | None] | None = None,
) -> dict[str, Any]:
    point_values = values if values is not None else [100.0, 110.0, None, 130.0]
    return {
        "artifact_id": "art_metric_frame_current",
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-05T00:00:00Z",
            },
            "scope": {},
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "window": {
                                "start": f"2026-01-0{index + 1}T00:00:00Z",
                                "end": f"2026-01-0{index + 2}T00:00:00Z",
                            },
                            "value": value,
                        }
                        for index, value in enumerate(point_values)
                    ],
                }
            ]
        },
    }


def _runtime(source_artifact: dict[str, Any]) -> MagicMock:
    runtime = MagicMock()
    runtime.resolve_artifact_by_id.return_value = source_artifact
    runtime.commit_artifact_with_extraction.return_value = "art_sample_current"
    return runtime


def test_extract_time_sample_axis_from_time_series_metric_frame() -> None:
    axis = extract_time_sample_axis(_metric_frame_artifact())

    assert axis == {"kind": "sample", "source_axis": "time", "grain": "day"}


@pytest.mark.parametrize("shape", ["scalar", "segmented", "panel"])
def test_extract_time_sample_axis_rejects_unsupported_shapes(shape: str) -> None:
    artifact = _metric_frame_artifact(shape=shape)
    if shape == "scalar":
        artifact["axes"] = []
    if shape == "segmented":
        artifact["axes"] = [{"kind": "dimension", "name": "region"}]
    if shape == "panel":
        artifact["axes"] = [{"kind": "time", "grain": "day"}, {"kind": "dimension", "name": "region"}]

    with pytest.raises(ValueError, match="requires a time_series metric_frame"):
        extract_time_sample_axis(artifact)


def test_compute_numeric_summary_ignores_null_points() -> None:
    summary = compute_numeric_summary_from_metric_frame(_metric_frame_artifact())

    assert summary == {
        "n": 3,
        "mean": pytest.approx(113.33333333333333),
        "standard_deviation": pytest.approx(15.275252316519467),
    }


def test_compute_numeric_summary_returns_null_stats_for_no_numeric_points() -> None:
    summary = compute_numeric_summary_from_metric_frame(_metric_frame_artifact(values=[None, None]))

    assert summary == {"n": 0, "mean": None, "standard_deviation": None}


def test_run_sample_summary_commits_sample_frame_from_metric_frame_payload() -> None:
    runtime = _runtime(_metric_frame_artifact())

    with patch("marivo.runtime.intents.sample_summary.new_step_id", return_value="step_sample"):
        result = run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_metric_frame_current",
                "sample_kind": "numeric",
            },
            reasoning="need test input",
        )

    runtime.resolve_artifact_by_id.assert_called_once_with("sess_1", "art_metric_frame_current")
    artifact = runtime.commit_artifact_with_extraction.call_args.args[4]
    assert artifact["artifact_family"] == "sample_frame"
    assert artifact["shape"] == "numeric_summary"
    assert artifact["subject"]["metric_ref"] == "metric.revenue"
    assert artifact["axes"] == [{"kind": "sample", "source_axis": "time", "grain": "day"}]
    assert artifact["payload"]["summary"]["n"] == 3
    assert artifact["lineage"]["source_artifact_ids"] == ["art_metric_frame_current"]
    assert result["step_type"] == "sample_summary"
    assert result["artifact_id"] == "art_sample_current"


def test_run_sample_summary_rejects_request_grain() -> None:
    runtime = _runtime(_metric_frame_artifact())

    with pytest.raises(ValueError, match="unsupported field"):
        run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_metric_frame_current",
                "sample_kind": "numeric",
                "grain": "day",
            },
        )


def test_run_sample_summary_rejects_non_metric_frame_source() -> None:
    runtime = _runtime({"artifact_id": "art_delta", "artifact_family": "delta_frame"})

    with pytest.raises(ValueError, match="must point to a metric_frame"):
        run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_delta",
                "sample_kind": "numeric",
            },
        )
```

- [ ] **Step 2: Run failing runner tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_sample_summary_runner.py'
```

Expected: FAIL because helpers and runner implementation are not present.

- [ ] **Step 3: Implement pure helpers and runner**

Replace `marivo/runtime/intents/sample_summary.py` with:

```python
from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from marivo.core.intent.primitives import new_step_id
from marivo.runtime.intents._helpers import commit_aoi_artifact_result

_REQUEST_FIELDS: frozenset[str] = frozenset({"source_artifact_id", "sample_kind"})
_VALID_SAMPLE_KINDS: frozenset[str] = frozenset({"numeric"})


def run_sample_summary_transform(
    runtime: Any,
    session_id: str,
    params: dict[str, Any] | None,
    reasoning: str | None = None,
) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("sample_summary: INVALID_ARGUMENT - params must be an object")
    unexpected_fields = set(params) - _REQUEST_FIELDS
    if unexpected_fields:
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - unsupported field(s): "
            f"{sorted(unexpected_fields)}"
        )
    missing_fields = _REQUEST_FIELDS - set(params)
    if missing_fields:
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - missing required field(s): "
            f"{sorted(missing_fields)}"
        )

    source_artifact_id = params["source_artifact_id"]
    sample_kind = params["sample_kind"]
    if not isinstance(source_artifact_id, str) or not source_artifact_id.strip():
        raise ValueError("sample_summary: INVALID_ARGUMENT - source_artifact_id is required")
    if sample_kind not in _VALID_SAMPLE_KINDS:
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - sample_kind must be one of "
            f"{sorted(_VALID_SAMPLE_KINDS)}"
        )

    source_artifact = runtime.resolve_artifact_by_id(session_id, source_artifact_id)
    if not isinstance(source_artifact, dict):
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - source_artifact_id was not found"
        )
    if source_artifact.get("artifact_family") != "metric_frame":
        raise ValueError(
            "sample_summary: INVALID_ARGUMENT - source_artifact_id must point to a "
            "metric_frame artifact"
        )

    sample_axis = extract_time_sample_axis(source_artifact)
    summary = compute_numeric_summary_from_metric_frame(source_artifact)
    metric_ref = _metric_ref(source_artifact)
    now = datetime.now(UTC).isoformat()
    step_id = new_step_id()

    artifact = {
        "artifact_id": "pending",
        "artifact_family": "sample_frame",
        "shape": "numeric_summary",
        "subject": {
            "kind": "sample_summary",
            "metric_ref": metric_ref,
            "source_artifact_id": source_artifact_id,
        },
        "axes": [sample_axis],
        "measures": [
            {"id": "n", "value_type": "integer", "nullable": False},
            {"id": "mean", "value_type": "number", "nullable": True},
            {"id": "standard_deviation", "value_type": "number", "nullable": True},
        ],
        "lineage": {
            "operation": "sample_summary",
            "source_artifact_ids": [source_artifact_id],
        },
        "payload": {
            "summary": summary,
            "quality": {"status": _quality_status(summary), "issues": _quality_issues(summary)},
        },
    }
    summary_text = (
        f"sample_summary {metric_ref}: n={summary['n']} "
        f"grain={sample_axis['grain']}"
    )
    envelope = commit_aoi_artifact_result(
        runtime,
        session_id,
        step_id,
        "sample_summary",
        "sample_frame",
        f"{metric_ref}_sample_summary",
        artifact,
        summary_text,
        provenance={
            "engine": "artifact",
            "timestamp": now,
            "source_artifact_id": source_artifact_id,
            "sample_kind": sample_kind,
        },
        reasoning=reasoning,
    )
    return envelope.model_dump()


def extract_time_sample_axis(metric_frame: dict[str, Any]) -> dict[str, Any]:
    if metric_frame.get("shape") != "time_series":
        raise ValueError(
            "sample_summary: UNSUPPORTED_SOURCE_SHAPE - numeric sample_summary v1 "
            "requires a time_series metric_frame"
        )
    axes = metric_frame.get("axes")
    if not isinstance(axes, list) or len(axes) != 1:
        raise ValueError(
            "sample_summary: UNSUPPORTED_SOURCE_SHAPE - numeric sample_summary v1 "
            "requires exactly one time axis"
        )
    axis = axes[0]
    if not isinstance(axis, dict) or axis.get("kind") != "time":
        raise ValueError(
            "sample_summary: UNSUPPORTED_SOURCE_SHAPE - numeric sample_summary v1 "
            "requires a time axis"
        )
    grain = axis.get("grain")
    if not isinstance(grain, str) or not grain:
        raise ValueError(
            "sample_summary: UNSUPPORTED_SOURCE_SHAPE - source time axis requires grain"
        )
    return {"kind": "sample", "source_axis": "time", "grain": grain}


def compute_numeric_summary_from_metric_frame(metric_frame: dict[str, Any]) -> dict[str, Any]:
    values = [
        float(value)
        for value in _iter_metric_frame_values(metric_frame)
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "standard_deviation": None}
    mean = sum(values) / n
    if n < 2:
        return {"n": n, "mean": mean, "standard_deviation": None}
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return {"n": n, "mean": mean, "standard_deviation": math.sqrt(variance)}


def _iter_metric_frame_values(metric_frame: dict[str, Any]) -> list[Any]:
    payload = metric_frame.get("payload")
    if not isinstance(payload, dict):
        return []
    values: list[Any] = []
    for series in payload.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("points") or []:
            if isinstance(point, dict):
                values.append(point.get("value"))
    return values


def _metric_ref(metric_frame: dict[str, Any]) -> str:
    subject = metric_frame.get("subject")
    if isinstance(subject, dict):
        metric_ref = subject.get("metric_ref")
        if isinstance(metric_ref, str) and metric_ref:
            return metric_ref
    return "metric.unknown"


def _quality_status(summary: dict[str, Any]) -> str:
    return "test_ready" if summary["n"] >= 2 and summary["standard_deviation"] is not None else "insufficient_data"


def _quality_issues(summary: dict[str, Any]) -> list[dict[str, str]]:
    if summary["n"] >= 2 and summary["standard_deviation"] is not None:
        return []
    return [
        {
            "code": "INSUFFICIENT_SAMPLE_SIZE",
            "message": "numeric sample summary requires at least two numeric points for test readiness",
        }
    ]
```

- [ ] **Step 4: Run runner tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_sample_summary_runner.py'
```

Expected: PASS. If `commit_aoi_artifact_result` validation rejects `"artifact_id": "pending"`, change `commit_aoi_artifact_result` in `marivo/runtime/intents/_helpers.py` to treat `sample_frame` like other top-level artifact families when replacing `artifact_id`.

- [ ] **Step 5: Commit sample_summary runner**

Run:

```bash
git add marivo/runtime/intents/sample_summary.py tests/runtime/intents/test_sample_summary_runner.py marivo/runtime/intents/_helpers.py
git commit -m "feat: implement sample summary transform runner" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 5: Rewrite test Runner To Consume sample_frame

**Files:**
- Modify: `marivo/runtime/intents/test.py`
- Modify: `tests/runtime/intents/test_test_runner.py`

- [ ] **Step 1: Replace test-runner fixtures with sample-frame artifacts**

In `tests/runtime/intents/test_test_runner.py`, add:

```python
def _sample_frame(
    *,
    artifact_id: str,
    metric_ref: str = "metric.test_metric",
    grain: str = "day",
    n: int | None = 30,
    mean: float | None = 100.0,
    standard_deviation: float | None = 15.0,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_family": "sample_frame",
        "shape": "numeric_summary",
        "subject": {
            "kind": "sample_summary",
            "metric_ref": metric_ref,
            "source_artifact_id": f"{artifact_id}_source",
        },
        "axes": [{"kind": "sample", "source_axis": "time", "grain": grain}],
        "measures": [
            {"id": "n", "value_type": "integer", "nullable": False},
            {"id": "mean", "value_type": "number", "nullable": True},
            {"id": "standard_deviation", "value_type": "number", "nullable": True},
        ],
        "lineage": {
            "operation": "sample_summary",
            "source_artifact_ids": [f"{artifact_id}_source"],
        },
        "payload": {
            "summary": {
                "n": n,
                "mean": mean,
                "standard_deviation": standard_deviation,
            },
            "quality": {"status": "test_ready", "issues": []},
        },
    }


def _valid_params() -> dict[str, Any]:
    return {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }
```

Replace `_run_with_mock_data` with:

```python
def _run_with_mock_data(
    params: dict[str, Any] | None = None,
    *,
    current_sample: dict[str, Any] | None = None,
    baseline_sample: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    runtime = _runtime()
    params = deepcopy(params if params is not None else _valid_params())
    current = current_sample or _sample_frame(artifact_id="art_sample_current")
    baseline = baseline_sample or _sample_frame(
        artifact_id="art_sample_baseline",
        n=25,
        mean=90.0,
        standard_deviation=12.0,
    )
    runtime.resolve_artifact_by_id.side_effect = [current, baseline]

    with patch(
        "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent"
    ) as mock_lineage:
        mock_lineage.return_value = {
            "issues": [],
            "fatal_message": None,
            "reuse_summary": None,
        }
        with patch("marivo.runtime.intents.test.commit_step_result") as mock_commit:
            mock_commit.return_value = {
                "intent_type": "test",
                "step_type": "test",
                "step_ref": {"session_id": "s1", "step_id": "step-1", "step_type": "test"},
                "artifact_id": "art-1",
            }
            run_test_intent(runtime, "session-1", params)
            artifact = mock_commit.call_args[0][6]
            return artifact, runtime.resolve_artifact_by_id
```

- [ ] **Step 2: Add tests that forbid source-style behavior**

Add:

```python
def test_reads_sample_frames_by_artifact_id() -> None:
    artifact, resolver = _run_with_mock_data()

    assert resolver.call_args_list[0].args == ("session-1", "art_sample_current")
    assert resolver.call_args_list[1].args == ("session-1", "art_sample_baseline")
    assert artifact["source_lineage"]["current_sample_artifact_id"] == "art_sample_current"
    assert artifact["source_lineage"]["baseline_sample_artifact_id"] == "art_sample_baseline"
    assert artifact["source_lineage"]["sample_axis"] == {"source_axis": "time", "grain": "day"}


def test_does_not_resolve_metric_or_compute_sample_summary() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current"),
        _sample_frame(artifact_id="art_sample_baseline", mean=90.0),
    ]

    with (
        patch("marivo.runtime.intents.test.compute_numeric_sample_summary") as mock_compute,
        patch("marivo.runtime.intents.test.commit_step_result") as mock_commit,
        patch(
            "marivo.runtime.intents.test.resolve_predicate_lineage_reuse_for_intent",
            return_value={"issues": [], "fatal_message": None, "reuse_summary": None},
        ),
    ):
        mock_commit.return_value = {"artifact_id": "art_test"}
        run_test_intent(runtime, "session-1", _valid_params())

    assert not mock_compute.called
    assert not runtime.core.normalize_intent_metric_ref.called
    assert not runtime.core.metric_name_from_ref.called


@pytest.mark.parametrize(
    ("payload_patch", "message"),
    [
        ({"metric": "metric.test_metric"}, "unsupported"),
        ({"grain": "day"}, "unsupported"),
        ({"kind": "numeric"}, "unsupported"),
        ({"current": {"time_scope": {"field": "event_time", "start": "2026-01-01", "end": "2026-01-02"}}}, "unsupported"),
        ({"baseline": {"time_scope": {"field": "event_time", "start": "2026-01-01", "end": "2026-01-02"}}}, "unsupported"),
    ],
)
def test_rejects_removed_source_request_fields(payload_patch: dict[str, Any], message: str) -> None:
    params = _valid_params()
    params.update(payload_patch)

    with pytest.raises(ValueError, match=message):
        run_test_intent(_runtime(), "session-1", params)


def test_rejects_non_sample_frame_artifacts() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        {"artifact_id": "art_metric", "artifact_family": "metric_frame"},
        _sample_frame(artifact_id="art_sample_baseline"),
    ]

    with pytest.raises(ValueError, match="sample_frame"):
        run_test_intent(runtime, "session-1", _valid_params())


def test_rejects_mismatched_sample_axes() -> None:
    runtime = _runtime()
    runtime.resolve_artifact_by_id.side_effect = [
        _sample_frame(artifact_id="art_sample_current", grain="day"),
        _sample_frame(artifact_id="art_sample_baseline", grain="week"),
    ]

    with pytest.raises(ValueError, match="sample axis"):
        run_test_intent(runtime, "session-1", _valid_params())
```

- [ ] **Step 3: Run failing test-runner tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_test_runner.py::test_reads_sample_frames_by_artifact_id tests/runtime/intents/test_test_runner.py::test_does_not_resolve_metric_or_compute_sample_summary tests/runtime/intents/test_test_runner.py::test_rejects_removed_source_request_fields tests/runtime/intents/test_test_runner.py::test_rejects_non_sample_frame_artifacts tests/runtime/intents/test_test_runner.py::test_rejects_mismatched_sample_axes'
```

Expected: FAIL because the runner still expects source-style params and computes sample summaries internally.

- [ ] **Step 4: Replace request validation and sample extraction in `run_test_intent`**

In `marivo/runtime/intents/test.py`, set:

```python
_REQUEST_FIELDS: frozenset[str] = frozenset(
    {"current_sample_artifact_id", "baseline_sample_artifact_id", "hypothesis"}
)
```

Remove validation for `metric`, `current`, `baseline`, `grain`, and `kind`. Add helpers:

```python
def _resolve_sample_frame(
    runtime: MarivoRuntime,
    session_id: str,
    artifact_id: Any,
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ValueError(f"test: INVALID_ARGUMENT - {label}_sample_artifact_id is required")
    artifact = runtime.resolve_artifact_by_id(session_id, artifact_id)
    if not isinstance(artifact, dict):
        raise ValueError(f"test: INVALID_ARGUMENT - {label}_sample_artifact_id was not found")
    if artifact.get("artifact_family") != "sample_frame":
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label}_sample_artifact_id must point to a sample_frame"
        )
    if artifact.get("shape") != "numeric_summary":
        raise ValueError(
            f"test: INVALID_ARGUMENT - {label}_sample_artifact_id must be numeric_summary"
        )
    return artifact


def _sample_stats(sample_frame: dict[str, Any]) -> tuple[int | None, float | None, float | None]:
    payload = sample_frame.get("payload")
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return None, None, None
    return (
        _coerce_int(summary.get("n")),
        _coerce_float(summary.get("mean")),
        _coerce_float(summary.get("standard_deviation")),
    )


def _sample_axis(sample_frame: dict[str, Any]) -> dict[str, Any]:
    axes = sample_frame.get("axes")
    if not isinstance(axes, list) or len(axes) != 1 or not isinstance(axes[0], dict):
        raise ValueError("test: INVALID_ARGUMENT - sample_frame requires exactly one sample axis")
    axis = axes[0]
    if axis.get("kind") != "sample":
        raise ValueError("test: INVALID_ARGUMENT - sample_frame axis must have kind='sample'")
    return {
        "source_axis": axis.get("source_axis"),
        "grain": axis.get("grain"),
    }


def _metric_ref(sample_frame: dict[str, Any]) -> str | None:
    subject = sample_frame.get("subject")
    if isinstance(subject, dict):
        value = subject.get("metric_ref")
        if isinstance(value, str) and value:
            return value
    return None


def _source_artifact_id(sample_frame: dict[str, Any]) -> str | None:
    subject = sample_frame.get("subject")
    if isinstance(subject, dict):
        value = subject.get("source_artifact_id")
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
```

Inside `run_test_intent`, after request field validation and hypothesis validation:

```python
current_sample_artifact_id = p["current_sample_artifact_id"]
baseline_sample_artifact_id = p["baseline_sample_artifact_id"]
current_sample = _resolve_sample_frame(
    runtime,
    session_id,
    current_sample_artifact_id,
    label="current",
)
baseline_sample = _resolve_sample_frame(
    runtime,
    session_id,
    baseline_sample_artifact_id,
    label="baseline",
)
current_axis = _sample_axis(current_sample)
baseline_axis = _sample_axis(baseline_sample)
if current_axis != baseline_axis:
    raise ValueError("test: NOT_COMPARABLE - sample axis must match")
current_metric_ref = _metric_ref(current_sample)
baseline_metric_ref = _metric_ref(baseline_sample)
if current_metric_ref != baseline_metric_ref:
    raise ValueError("test: NOT_COMPARABLE - sample metric_ref must match")
n1, mean1, std1 = _sample_stats(current_sample)
n2, mean2, std2 = _sample_stats(baseline_sample)
metric_name = current_metric_ref or "metric.unknown"
```

Build `source_lineage` as:

```python
source_lineage: dict[str, Any] = {
    "current_sample_artifact_id": current_sample_artifact_id,
    "baseline_sample_artifact_id": baseline_sample_artifact_id,
    "current_source_artifact_id": _source_artifact_id(current_sample),
    "baseline_source_artifact_id": _source_artifact_id(baseline_sample),
    "sample_axis": current_axis,
}
```

Build `query_hash` from sample artifact ids and hypothesis:

```python
_hash_input = (
    f"{current_sample_artifact_id}:{baseline_sample_artifact_id}:"
    f"welch_t:{family}:{alternative}:{alpha}"
)
query_hash = hashlib.sha256(_hash_input.encode()).hexdigest()[:16]
```

Remove source-slice SQL collection from `sql_texts`; pass `sql_texts=None` into `commit_step_result`.

- [ ] **Step 5: Run test runner suite**

Run:

```bash
make test TESTS='tests/runtime/intents/test_test_runner.py'
```

Expected: PASS after deleting or rewriting old tests that assert source filters, grain, or SQL helper behavior.

- [ ] **Step 6: Commit test runner rewrite**

Run:

```bash
git add marivo/runtime/intents/test.py tests/runtime/intents/test_test_runner.py
git commit -m "feat: make test consume sample frames" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 6: Expand validate Through sample_summary And test

**Files:**
- Modify: `marivo/runtime/intents/validate.py`
- Modify: `tests/runtime/intents/test_validate_runner.py`

- [ ] **Step 1: Add failing validate orchestration test**

In `tests/runtime/intents/test_validate_runner.py`, add a test that patches the underlying operation runners used by validate:

```python
def test_validate_expands_through_sample_summary_before_test(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    calls: list[tuple[str, dict[str, Any]]] = []

    def observe_runner(runtime_arg: Any, session_id: str, params: dict[str, Any], *, reasoning: str | None = None) -> dict[str, Any]:
        calls.append(("observe", params))
        suffix = "current" if len(calls) == 1 else "baseline"
        return {
            "artifact_id": f"art_metric_{suffix}",
            "result": {
                "artifact_id": f"art_metric_{suffix}",
                "artifact_family": "metric_frame",
                "shape": "time_series",
                "subject": {
                    "kind": "metric",
                    "metric_ref": "metric.revenue",
                    "time_scope": params["time_scope"],
                    "scope": {},
                },
                "axes": [{"kind": "time", "grain": "day"}],
                "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
                "payload": {"series": [{"keys": {}, "points": []}]},
            },
        }

    def sample_runner(runtime_arg: Any, session_id: str, params: dict[str, Any], *, reasoning: str | None = None) -> dict[str, Any]:
        calls.append(("sample_summary", params))
        suffix = "current" if params["source_artifact_id"] == "art_metric_current" else "baseline"
        return {"artifact_id": f"art_sample_{suffix}"}

    def test_runner(runtime_arg: Any, session_id: str, params: dict[str, Any], *, reasoning: str | None = None) -> dict[str, Any]:
        calls.append(("test", params))
        return {"artifact_id": "art_test", "result_type": "hypothesis_test"}

    monkeypatch.setattr("marivo.runtime.intents.validate.run_observe_intent", observe_runner)
    monkeypatch.setattr("marivo.runtime.intents.validate.run_sample_summary_transform", sample_runner)
    monkeypatch.setattr("marivo.runtime.intents.validate.run_test_intent", test_runner)

    result = run_validate_intent(runtime, "sess_1", _valid_params())

    assert [name for name, _ in calls] == [
        "observe",
        "observe",
        "sample_summary",
        "sample_summary",
        "test",
    ]
    assert calls[2][1] == {"source_artifact_id": "art_metric_current", "sample_kind": "numeric"}
    assert calls[3][1] == {"source_artifact_id": "art_metric_baseline", "sample_kind": "numeric"}
    assert calls[4][1]["current_sample_artifact_id"] == "art_sample_current"
    assert calls[4][1]["baseline_sample_artifact_id"] == "art_sample_baseline"
    assert result["result"]["aoi_artifacts"]
```

- [ ] **Step 2: Run failing validate test**

Run:

```bash
make test TESTS='tests/runtime/intents/test_validate_runner.py::test_validate_expands_through_sample_summary_before_test'
```

Expected: FAIL because validate still invokes source-style test directly.

- [ ] **Step 3: Rewrite validate orchestration**

In `marivo/runtime/intents/validate.py`, import:

```python
from marivo.runtime.intents.sample_summary import run_sample_summary_transform
```

Where validate currently calls `run_test_intent` with source-style params, replace that section with:

```python
current_observe_result = run_observe_intent(
    runtime,
    session_id,
    {
        "metric": metric,
        "time_scope": current["time_scope"],
        "filter": current.get("filter"),
        "granularity": grain,
    },
    reasoning=reasoning,
)
baseline_observe_result = run_observe_intent(
    runtime,
    session_id,
    {
        "metric": metric,
        "time_scope": baseline["time_scope"],
        "filter": baseline.get("filter"),
        "granularity": grain,
    },
    reasoning=reasoning,
)
current_sample_result = run_sample_summary_transform(
    runtime,
    session_id,
    {
        "source_artifact_id": current_observe_result["artifact_id"],
        "sample_kind": "numeric",
    },
    reasoning=reasoning,
)
baseline_sample_result = run_sample_summary_transform(
    runtime,
    session_id,
    {
        "source_artifact_id": baseline_observe_result["artifact_id"],
        "sample_kind": "numeric",
    },
    reasoning=reasoning,
)
test_result = run_test_intent(
    runtime,
    session_id,
    {
        "current_sample_artifact_id": current_sample_result["artifact_id"],
        "baseline_sample_artifact_id": baseline_sample_result["artifact_id"],
        "hypothesis": hypothesis,
    },
    reasoning=reasoning,
)
```

Ensure the returned bundle `aoi_artifacts` includes the observe artifacts, both sample-frame artifacts, and the test artifact when present. If a runner returns an `ExecutionEnvelope` dict with `result`, append `result`; if it returns a flat artifact payload, append the flat artifact payload.

- [ ] **Step 4: Run validate tests**

Run:

```bash
make test TESTS='tests/runtime/intents/test_validate_runner.py'
```

Expected: PASS after updating old assertions that expected hidden sample computation.

- [ ] **Step 5: Commit validate expansion**

Run:

```bash
git add marivo/runtime/intents/validate.py tests/runtime/intents/test_validate_runner.py
git commit -m "feat: expand validate through sample summary" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 7: Add HTTP Transform Endpoint And Update test Endpoint

**Files:**
- Modify: `marivo/transports/http/models/intent_response_models.py`
- Modify: `marivo/transports/http/sessions.py`
- Modify: `marivo/transports/http/errors.py`
- Modify: `tests/transports/http/test_http_aoi_intents.py`
- Modify: `tests/transports/http/test_openapi_fragments.py`
- Modify: `tests/transports/http/test_openapi_schema_quality.py`

- [ ] **Step 1: Add HTTP tests for sample_summary and new test shape**

In `_FakeRuntime` inside `tests/transports/http/test_http_aoi_intents.py`, add:

```python
self.sample_summary_payload: Any | None = None
```

and method:

```python
def sample_summary(self, session_id: str, payload: Any) -> dict[str, Any]:
    self.sample_summary_payload = payload
    return {
        "intent_type": "sample_summary",
        "step_type": "sample_summary",
        "step_ref": {
            "session_id": session_id,
            "step_id": "step_sample_1",
            "step_type": "sample_summary",
        },
        "artifact_id": "art_sample_1",
        "result": {
            "artifact_id": "art_sample_1",
            "artifact_family": "sample_frame",
            "shape": "numeric_summary",
            "subject": {
                "kind": "sample_summary",
                "metric_ref": "metric.revenue",
                "source_artifact_id": "art_metric_frame_current",
            },
            "axes": [{"kind": "sample", "source_axis": "time", "grain": "day"}],
            "measures": [
                {"id": "n", "value_type": "integer", "nullable": False},
                {"id": "mean", "value_type": "number", "nullable": True},
                {"id": "standard_deviation", "value_type": "number", "nullable": True},
            ],
            "lineage": {
                "operation": "sample_summary",
                "source_artifact_ids": ["art_metric_frame_current"],
            },
            "payload": {
                "summary": {"n": 7, "mean": 120.0, "standard_deviation": 10.0},
                "quality": {"status": "test_ready", "issues": []},
            },
        },
    }
```

Replace `_valid_test_request()` with:

```python
def _valid_test_request() -> dict[str, Any]:
    return {
        "current_sample_artifact_id": "art_sample_current",
        "baseline_sample_artifact_id": "art_sample_baseline",
        "hypothesis": {
            "family": "two_sample_mean",
            "alternative": "two_sided",
            "significance": "balanced",
        },
    }
```

Add tests:

```python
def test_sample_summary_transform_accepts_aoi_request_and_returns_execution_envelope() -> None:
    runtime = _FakeRuntime()
    response = _client(runtime).post(
        "/sessions/sess_1/transforms/sample_summary",
        json={
            "source_artifact_id": "art_metric_frame_current",
            "sample_kind": "numeric",
        },
    )

    assert response.status_code == 200, response.text
    assert isinstance(runtime.sample_summary_payload, aoi.SampleSummary)
    body = response.json()
    assert body["step_type"] == "sample_summary"
    assert body["artifact_id"] == "art_sample_1"
    assert body["result"]["artifact_family"] == "sample_frame"
    assert body["result"]["payload"]["summary"]["n"] == 7


def test_sample_summary_transform_rejects_grain() -> None:
    response = _client(_FakeRuntime()).post(
        "/sessions/sess_1/transforms/sample_summary",
        json={
            "source_artifact_id": "art_metric_frame_current",
            "sample_kind": "numeric",
            "grain": "day",
        },
    )

    assert response.status_code == 422
```

Update `test_test_accepts_aoi_request_and_returns_execution_envelope` assertions:

```python
assert isinstance(runtime.test_payload, aoi.Test)
assert runtime.test_payload.current_sample_artifact_id == "art_sample_current"
assert runtime.test_payload.baseline_sample_artifact_id == "art_sample_baseline"
assert runtime.test_payload.hypothesis.family == "two_sample_mean"
```

Delete `test_test_accepts_time_granularity_grain`; replace it with:

```python
def test_test_rejects_removed_grain() -> None:
    payload = _valid_test_request()
    payload["grain"] = "day"

    response = _client(_FakeRuntime()).post("/sessions/sess_1/intents/test", json=payload)

    assert response.status_code == 422
```

- [ ] **Step 2: Run failing HTTP tests**

Run:

```bash
make test TESTS='tests/transports/http/test_http_aoi_intents.py::test_sample_summary_transform_accepts_aoi_request_and_returns_execution_envelope tests/transports/http/test_http_aoi_intents.py::test_sample_summary_transform_rejects_grain tests/transports/http/test_http_aoi_intents.py::test_test_accepts_aoi_request_and_returns_execution_envelope tests/transports/http/test_http_aoi_intents.py::test_test_rejects_removed_grain'
```

Expected: FAIL because route and response model do not exist and HTTP still assumes old `aoi.Test` fields.

- [ ] **Step 3: Add response model**

In `marivo/transports/http/models/intent_response_models.py`, add:

```python
class _SampleSummaryFailureArtifact(aoi.Artifact2):
    result: None = None


class SampleSummaryResponse(_EnvelopeBase):
    result: aoi.SampleFrameArtifact | _SampleSummaryFailureArtifact
```

Add `aoi.SampleFrameArtifact` to `DerivedBundleResult.aoi_artifacts`.

- [ ] **Step 4: Add HTTP route**

In `marivo/transports/http/sessions.py`, import `SampleSummaryResponse`, then add after the atomic intent routes or in a new transform section:

```python
@router.post(
    "/sessions/{session_id}/transforms/sample_summary",
    response_model=SampleSummaryResponse,
    response_model_exclude_none=True,
)
def transform_sample_summary(
    session_id: str,
    payload: aoi.SampleSummary,
    request: Request,
) -> SampleSummaryResponse:
    result = get_services(request).runtime.sample_summary(session_id, payload)
    return SampleSummaryResponse.model_validate(_atomic_intent_response("sample_summary", result))
```

The helper name `_atomic_intent_response` can be reused if it only normalizes `ExecutionEnvelope` shape. Do not rename public docs to call transforms intents.

- [ ] **Step 5: Update HTTP error examples and OpenAPI tests**

In `marivo/transports/http/errors.py`, add:

```python
("POST", "/sessions/{session_id}/transforms/sample_summary"): "SampleSummary",
```

and a short schema guidance example:

```python
"/sessions/{session_id}/transforms/sample_summary": {
    "source_artifact_id": "art_metric_frame_current",
    "sample_kind": "numeric",
}
```

In `tests/transports/http/test_openapi_fragments.py`, add an assertion that:

```python
sample_summary_request = schema["paths"]["/sessions/{session_id}/transforms/sample_summary"]["post"]["requestBody"]["content"]["application/json"]["schema"]
assert sample_summary_request["$ref"].endswith("/SampleSummary")
```

In `tests/transports/http/test_openapi_schema_quality.py`, update the allowed operational path predicate:

```python
or path.startswith("/sessions/{session_id}/transforms/")
```

- [ ] **Step 6: Run HTTP tests**

Run:

```bash
make test TESTS='tests/transports/http/test_http_aoi_intents.py tests/transports/http/test_openapi_fragments.py tests/transports/http/test_openapi_schema_quality.py'
```

Expected: PASS.

- [ ] **Step 7: Commit HTTP surface**

Run:

```bash
git add marivo/transports/http tests/transports/http
git commit -m "feat: expose sample summary HTTP transform" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 8: Add MCP sample_summary Tool And Update MCP test Tool

**Files:**
- Modify: `marivo/transports/mcp/tools/schemas.py`
- Modify: `marivo/transports/mcp/tools/intents.py`
- Modify: `marivo/transports/mcp/tools/__init__.py`
- Modify: `tests/transports/mcp/test_tool_parity.py`
- Modify: `tests/transports/mcp/test_mcp_aoi_adapter.py`

- [ ] **Step 1: Add MCP adapter tests**

Update imports in `tests/transports/mcp/test_mcp_aoi_adapter.py`:

```python
from marivo.transports.mcp.tools.intents import (
    to_aoi_attribute_request,
    to_aoi_compare_request,
    to_aoi_decompose_request,
    to_aoi_detect_request,
    to_aoi_diagnose_request,
    to_aoi_forecast_request,
    to_aoi_observe_request,
    to_aoi_sample_summary_request,
    to_aoi_test_request,
    to_aoi_validate_request,
)
```

Replace the old `to_aoi_test_request` test with:

```python
def test_to_aoi_sample_summary_request_builds_transform_model() -> None:
    request = to_aoi_sample_summary_request(
        source_artifact_id="art_metric_frame_current",
        sample_kind="numeric",
    )

    assert isinstance(request, aoi.SampleSummary)
    assert request.source_artifact_id == "art_metric_frame_current"
    assert request.sample_kind == "numeric"


def test_to_aoi_test_request_builds_sample_ref_model() -> None:
    request = to_aoi_test_request(
        current_sample_artifact_id="art_sample_current",
        baseline_sample_artifact_id="art_sample_baseline",
        hypothesis=McpTestHypothesis(alternative="greater", significance="balanced"),
    )

    assert isinstance(request, aoi.Test)
    assert request.current_sample_artifact_id == "art_sample_current"
    assert request.baseline_sample_artifact_id == "art_sample_baseline"
    assert request.hypothesis.family == "two_sample_mean"
    assert request.hypothesis.alternative == "greater"
```

- [ ] **Step 2: Add MCP tool parity test**

In `tests/transports/mcp/test_tool_parity.py`, add `sample_summary` to `FakeRuntime`:

```python
def sample_summary(self, **kw):
    return {}
```

Add a parity assertion near other tool-name checks:

```python
def test_mcp_registers_sample_summary_tool() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert "sample_summary" in tool_names
```

- [ ] **Step 3: Run failing MCP tests**

Run:

```bash
make test TESTS='tests/transports/mcp/test_mcp_aoi_adapter.py::test_to_aoi_sample_summary_request_builds_transform_model tests/transports/mcp/test_mcp_aoi_adapter.py::test_to_aoi_test_request_builds_sample_ref_model tests/transports/mcp/test_tool_parity.py::test_mcp_registers_sample_summary_tool'
```

Expected: FAIL because adapter function and tool registration do not exist.

- [ ] **Step 4: Update MCP adapters**

In `marivo/transports/mcp/tools/intents.py`, replace `to_aoi_test_request` with:

```python
def to_aoi_sample_summary_request(
    source_artifact_id: str,
    sample_kind: Literal["numeric"] = "numeric",
) -> aoi.SampleSummary:
    return aoi.SampleSummary.model_validate(
        {
            "source_artifact_id": source_artifact_id,
            "sample_kind": sample_kind,
        }
    )


def to_aoi_test_request(
    current_sample_artifact_id: str,
    baseline_sample_artifact_id: str,
    hypothesis: McpTestHypothesis | dict[str, Any],
) -> aoi.Test:
    hypothesis_model = (
        hypothesis
        if isinstance(hypothesis, McpTestHypothesis)
        else McpTestHypothesis.model_validate(hypothesis)
    )
    return aoi.Test.model_validate(
        {
            "current_sample_artifact_id": current_sample_artifact_id,
            "baseline_sample_artifact_id": baseline_sample_artifact_id,
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": hypothesis_model.alternative,
                "significance": hypothesis_model.significance,
            },
        }
    )
```

Add tool registration:

```python
def register_sample_summary(server: Any, runtime: Any) -> None:
    @server.tool(  # type: ignore
        description=(
            "Summarize an existing AOI metric_frame into a numeric sample_frame for "
            "hypothesis testing. The transform inherits grain from the source metric_frame; "
            "do not pass grain, metric, time_scope, or filter."
        )
    )
    async def sample_summary(
        session_id: Annotated[
            str,
            Field(description="Marivo analysis session ID that owns this transform call."),
        ],
        source_artifact_id: Annotated[
            str,
            Field(min_length=1, description="Artifact ID of a time_series metric_frame."),
        ],
        sample_kind: Annotated[
            Literal["numeric"],
            Field(description="Sample summary family. v1 supports only numeric."),
        ] = "numeric",
        reasoning: _ReasoningField = None,
    ) -> dict[str, Any]:
        request = to_aoi_sample_summary_request(
            source_artifact_id=source_artifact_id,
            sample_kind=sample_kind,
        )
        return await call_runtime(
            runtime.sample_summary,
            session_id=session_id,
            request=request,
            reasoning=reasoning,
        )
```

Replace `register_test_intent` parameters and call:

```python
async def test_intent(
    session_id: Annotated[str, Field(description="Marivo analysis session ID that owns this intent call.")],
    current_sample_artifact_id: Annotated[str, Field(min_length=1, description="Current sample_frame artifact ID.")],
    baseline_sample_artifact_id: Annotated[str, Field(min_length=1, description="Baseline sample_frame artifact ID.")],
    hypothesis: Annotated[McpTestHypothesis, Field(description="Structured two_sample_mean hypothesis choices.")],
    reasoning: _ReasoningField = None,
) -> dict[str, Any]:
    request = to_aoi_test_request(
        current_sample_artifact_id=current_sample_artifact_id,
        baseline_sample_artifact_id=baseline_sample_artifact_id,
        hypothesis=hypothesis,
    )
    return await call_runtime(
        runtime.test,
        session_id=session_id,
        request=request,
        reasoning=reasoning,
    )
```

- [ ] **Step 5: Register MCP tool**

In `marivo/transports/mcp/tools/__init__.py`, import and register:

```python
from marivo.transports.mcp.tools.intents import (
    register_attribute,
    register_compare,
    register_correlate,
    register_decompose,
    register_detect,
    register_diagnose,
    register_forecast,
    register_observe,
    register_sample_summary,
    register_test_intent,
    register_validate,
)
```

Call after `register_observe(server, runtime)`:

```python
register_sample_summary(server, runtime)
```

- [ ] **Step 6: Run MCP tests**

Run:

```bash
make test TESTS='tests/transports/mcp/test_mcp_aoi_adapter.py tests/transports/mcp/test_tool_parity.py'
```

Expected: PASS.

- [ ] **Step 7: Commit MCP surface**

Run:

```bash
git add marivo/transports/mcp tests/transports/mcp
git commit -m "feat: expose sample summary MCP tool" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit succeeds.

## Task 9: Update Docs And Evidence Artifact Acceptance

**Files:**
- Modify: `docs/api/intent-steps.md`
- Modify: `docs/specs/analysis/foundations/analysis-operation-architecture.md`
- Modify: `docs/specs/analysis/intents/atomic/test.md`
- Modify: `docs/marivo-for-builders.zh.md`
- Modify: `marivo/runtime/evidence` files only if tests reveal a hard artifact-family rejection.
- Modify: relevant evidence tests only if a hard artifact-family rejection exists.

- [ ] **Step 1: Update API docs**

In `docs/api/intent-steps.md`, add a transform section after the intent table:

```markdown
## Transforms

| Operation | Endpoint | Response |
| --- | --- | --- |
| `sample_summary` | `POST /sessions/{session_id}/transforms/sample_summary` | `SampleSummaryResponse` |

`sample_summary` consumes an existing `metric_frame` artifact and produces a `sample_frame`. It does not accept `grain`; sample granularity is inherited from the source metric frame.
```

Replace the test request example with:

```json
{
  "current_sample_artifact_id": "art_sample_current",
  "baseline_sample_artifact_id": "art_sample_baseline",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "two_sided",
    "significance": "balanced"
  }
}
```

- [ ] **Step 2: Correct architecture docs**

In `docs/specs/analysis/foundations/analysis-operation-architecture.md`, replace any wording like:

```markdown
`sample_summary` 负责样本统计、rate 分子分母、null 处理、pairing 与方法输入准备
```

with:

```markdown
`sample_summary` 负责从已有 `metric_frame` 的样本轴和点值生成检验输入摘要。它不重新选择 `grain`、`filter` 或 `time_scope`；这些语义必须来自上游 `metric_frame`。
```

Ensure no paragraph says `sample_summary` request accepts or chooses `grain`.

- [ ] **Step 3: Update atomic test docs**

In `docs/specs/analysis/intents/atomic/test.md`, replace source-style input descriptions with:

```markdown
`test` 的 canonical 输入是两个 `sample_frame` artifact refs：

- `current_sample_artifact_id`
- `baseline_sample_artifact_id`
- `hypothesis`

`test` 不读取 semantic metric、不接收 `grain`，也不在内部生成 sample summary。样本准备由上游 `sample_summary` transform 完成。
```

- [ ] **Step 4: Update builder docs**

In `docs/marivo-for-builders.zh.md`, update any workflow that jumps directly from `observe` to `test` so it shows:

```markdown
observe -> sample_summary -> test
```

and states:

```markdown
`sample_frame` 是检验输入工件，默认不生成 proposition；`test_result` 才进入 hypothesis 证据链路。
```

- [ ] **Step 5: Run docs grep check**

Run:

```bash
rg -n "test\\.grain|grain.*sample_summary|sample_summary.*grain|metric/current/baseline/grain|source-type.*test" docs aoi-spec marivo tests
```

Expected: any remaining hits either describe removed behavior as a changelog breaking change or explicitly say `sample_summary` does not accept `grain`.

- [ ] **Step 6: Run evidence acceptance tests if artifact commit fails**

If sample-frame commit or extraction fails during runtime tests, add an explicit no-op family behavior test in the nearest evidence registry test. The assertion should be:

```python
def test_sample_frame_artifact_does_not_require_finding_extractor() -> None:
    registry = make_finding_extractor_registry()

    assert registry.get("sample_frame") is None
```

Use the actual registry factory from the existing evidence test file. Do not add a proposition seed for `sample_frame`.

- [ ] **Step 7: Commit docs and evidence acceptance**

Run:

```bash
git add docs aoi-spec marivo/runtime/evidence tests/runtime/evidence
git commit -m "docs: document sample summary transform flow" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash]"
```

Expected: commit includes docs and only evidence files needed to accept non-finding sample-frame artifacts.

## Task 10: Full Verification And Cleanup

**Files:**
- Verify all files changed in earlier tasks.

- [ ] **Step 1: Run generated contract freshness gate**

Run:

```bash
./.venv/bin/python scripts/generate_contract_models.py --check
```

Expected: PASS with no generated-model diff.

- [ ] **Step 2: Run targeted test bundle**

Run:

```bash
make test TESTS='tests/contracts/test_generated_models.py tests/runtime/test_aoi_lowering.py tests/runtime/test_aoi_intent_execution.py tests/runtime/test_runtime_intent_dispatch.py tests/runtime/intents/test_sample_summary_runner.py tests/runtime/intents/test_test_runner.py tests/runtime/intents/test_validate_runner.py tests/transports/http/test_http_aoi_intents.py tests/transports/http/test_openapi_fragments.py tests/transports/http/test_openapi_schema_quality.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_mcp_aoi_adapter.py'
```

Expected: PASS.

- [ ] **Step 3: Run lint and typecheck**

Run:

```bash
make lint
make typecheck
```

Expected: PASS.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
git diff --name-status HEAD
```

Expected: only files related to AOI sample_summary, sample_frame, test ref inputs, runtime dispatch, HTTP, MCP, tests, and docs are changed.

- [ ] **Step 5: Final commit if verification changed files**

If verification or hooks changed files, run:

```bash
git add aoi-spec marivo tests docs
git commit -m "chore: finalize sample summary transform wiring" -m "Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]"
```

Expected: commit succeeds. If there are no remaining changes, skip this step.

## Self-Review

Spec coverage:

- AOI `transforms.sample_summary`: Task 1 and Task 2.
- `sample_summary` request without `grain`: Task 1, Task 2, Task 4, Task 8, Task 9.
- `sample_frame` artifact: Task 1, Task 2, Task 4, Task 7.
- `test` sample-frame refs: Task 1, Task 2, Task 5, Task 7, Task 8.
- Runtime transform dispatch: Task 3.
- HTTP transform endpoint: Task 7.
- MCP transform tool: Task 8.
- `validate` expansion: Task 6.
- Docs and downstream evidence boundary: Task 9.
- Verification: Task 10.

Placeholder scan:

- Placeholder and command-policy scan passed.

Type consistency:

- AOI generated transform model name is consistently `aoi.SampleSummary`.
- AOI generated artifact model name is consistently `aoi.SampleFrameArtifact`.
- Runtime transform method is consistently `sample_summary`.
- Runner function is consistently `run_sample_summary_transform`.
- Test request fields are consistently `current_sample_artifact_id`, `baseline_sample_artifact_id`, and `hypothesis`.
