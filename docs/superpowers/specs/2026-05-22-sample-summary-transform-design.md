# Sample Summary Transform Design

Date: 2026-05-22

## Status

Approved design for implementation planning.

## Goal

Make `sample_summary` a first-class AOI transform operation exposed through AOI spec, HTTP API, and MCP tools. It consumes an existing `metric_frame`, produces a reusable `sample_frame`, and makes `test` consume `sample_frame` artifacts as its canonical input.

This is a breaking target-state change. The implementation does not preserve the old `test` source-type request shape and does not add migration compatibility.

## Design Decisions

1. `sample_summary` is a standard AOI operation, but not an atomic intent.
2. AOI adds a `transforms` namespace alongside `requests` and `derived_requests`.
3. `sample_summary` request does not accept `grain`; sample granularity is inherited from the source `metric_frame`.
4. `test` becomes ref-type and only accepts two sample-frame artifact refs plus `hypothesis`.
5. `sample_frame` is a top-level AOI artifact family.
6. `sample_frame` does not seed propositions by default; it exists as reusable transform output and provenance for downstream tests.

## AOI Contract

AOI adds:

- `$defs.transforms.sample_summary`
- `SampleFrameArtifact`
- root union support for transform requests
- artifact union support for `artifact_family: "sample_frame"`

The transform request is:

```json
{
  "source_artifact_id": "art_metric_frame",
  "sample_kind": "numeric"
}
```

`source_artifact_id` must resolve to `artifact_family: "metric_frame"`. `sample_kind` is required and v1 only supports `"numeric"`. Future sample families such as `"rate"` or paired variants are intentionally left out of this implementation slice.

The `sample_summary` request does not include `metric`, `time_scope`, `filter`, or `grain`. Those are already part of the source `metric_frame` subject and axes. The transform must not redefine the source frame's grain or scope.

The `sample_frame` artifact shape is:

```json
{
  "artifact_id": "art_sample",
  "artifact_family": "sample_frame",
  "shape": "numeric_summary",
  "subject": {
    "kind": "sample_summary",
    "metric_ref": "metric.revenue",
    "source_artifact_id": "art_metric_frame"
  },
  "axes": [
    {
      "kind": "sample",
      "source_axis": "time",
      "grain": "day"
    }
  ],
  "measures": [
    { "id": "n", "value_type": "integer", "nullable": false },
    { "id": "mean", "value_type": "number", "nullable": true },
    { "id": "standard_deviation", "value_type": "number", "nullable": true }
  ],
  "lineage": {
    "operation": "sample_summary",
    "source_artifact_ids": ["art_metric_frame"]
  },
  "payload": {
    "summary": {
      "n": 7,
      "mean": 123.4,
      "standard_deviation": 10.5
    },
    "quality": {
      "status": "test_ready",
      "issues": []
    }
  }
}
```

`sample_summary` v1 only supports source metric frames with a clear time sample axis:

- `time_series`: supported. The source time axis is the sample axis.
- `panel`: fail closed for this slice. It has both time and dimension axes, and this design does not silently decide whether to pool segments.
- `scalar`: fail closed. It has no sample axis.
- `segmented`: fail closed. Segment-as-sample is a separate future design.

`test` request becomes:

```json
{
  "current_sample_artifact_id": "art_current_sample",
  "baseline_sample_artifact_id": "art_baseline_sample",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "two_sided",
    "significance": "balanced"
  }
}
```

The old `metric`, `current`, `baseline`, `grain`, and `kind` fields are removed from `test`.

## Runtime Data Flow

Runtime adds a transform runner:

```text
run_sample_summary_transform(runtime, session_id, params)
```

The runner:

1. Resolves `source_artifact_id`.
2. Validates the source is a `metric_frame`.
3. Validates v1 supports the source shape and sample kind.
4. Reads the sample axis from source frame axes.
5. Computes `n`, `mean`, and `standard_deviation` from source metric-frame point values.
6. Commits a `sample_frame` artifact with `step_type: "sample_summary"` and lineage to the source frame.

The transform should compute directly from the source artifact payload when the metric-frame point values are present. If implementation details still require SQL replay for an initial slice, the replay must use the source metric-frame lineage and inherited source granularity. The transform request must not supply a new grain or scope.

`test` runner changes to a pure sample-frame consumer:

1. Resolve `current_sample_artifact_id` and `baseline_sample_artifact_id`.
2. Validate both artifacts are `sample_frame(shape: "numeric_summary")`.
3. Validate hypothesis family is `two_sample_mean`.
4. Validate metric reference, sample kind, sample axis, and predicate lineage are comparable.
5. Read `n`, `mean`, and `standard_deviation` from each `sample_frame`.
6. Run Welch's t-test and commit the existing `hypothesis_test_result` / `test_result` artifact.

`test` no longer reads semantic metrics, time scopes, filters, SQL, or sample-summary query helpers.

## Validate Expansion

`validate` remains a derived request, but target-state execution expands through standard operations:

```text
observe(current) -> metric_frame
observe(baseline) -> metric_frame
sample_summary(current_metric_frame) -> sample_frame
sample_summary(baseline_metric_frame) -> sample_frame
test(current_sample_frame, baseline_sample_frame) -> test_result
```

If no general Plan DSL executor exists yet, `validate` may keep deterministic orchestration inside its runner for this slice. Its returned bundle should expose the two `sample_summary` steps and artifacts instead of hiding sample preparation inside `test`.

## HTTP API

HTTP exposes transforms separately from intents:

```text
POST /sessions/{session_id}/transforms/sample_summary
```

The request body is the AOI transform request. The response uses the existing `ExecutionEnvelope`, with `step_type: "sample_summary"` and `result` containing the AOI `sample_frame` artifact.

Atomic `test` endpoint remains under:

```text
POST /sessions/{session_id}/intents/test
```

but its request model changes to the new sample-frame ref shape.

OpenAPI and API docs should present analysis operations in two groups:

- atomic intents
- transforms

## MCP Tools

MCP adds a `sample_summary` tool:

```json
{
  "session_id": "sess_...",
  "source_artifact_id": "art_metric_frame",
  "sample_kind": "numeric",
  "reasoning": "optional"
}
```

The MCP adapter converts this DTO to the AOI transform request and calls the runtime transform dispatcher.

The MCP `test` tool changes to:

```json
{
  "session_id": "sess_...",
  "current_sample_artifact_id": "art_current_sample",
  "baseline_sample_artifact_id": "art_baseline_sample",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "two_sided",
    "significance": "balanced"
  },
  "reasoning": "optional"
}
```

It no longer accepts `metric`, `current`, `baseline`, `grain`, or `kind`.

## Downstream Updates

Implementation should update:

- `aoi-spec/spec.md`
- `aoi-spec/schema/aoi.schema.json`
- `aoi-spec/schema/aoi.schema.yaml`
- `aoi-spec/examples/sample_summary/*`
- `aoi-spec/examples/test/*`
- `aoi-spec/README.md`
- `aoi-spec/CHANGELOG.md`
- generated AOI models via `scripts/generate_contract_models.py`
- `marivo/contracts/aoi_runtime.py`
- AOI lowering and runtime dispatch registries
- HTTP request/response models and OpenAPI tests
- MCP schemas, adapters, and parity tests
- runtime tests for `sample_summary` and updated `test`
- docs for test intent, API operation surfaces, and builder-facing workflows
- evidence extractor or artifact-family registry behavior so `sample_frame` is accepted as a non-finding transform artifact

`docs/specs/analysis/foundations/analysis-operation-architecture.md` already matches the broad target, but should be corrected where it implies `sample_summary` can choose `grain`.

## Test Plan

Minimum verification after implementation:

```bash
./.venv/bin/python scripts/generate_contract_models.py --check
make test TESTS='tests/contracts/test_generated_models.py tests/runtime/test_aoi_lowering.py tests/runtime/intents/test_test_runner.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_mcp_aoi_adapter.py'
```

Additional focused tests should cover:

- AOI examples validate.
- `sample_summary` rejects scalar, segmented, and panel metric frames in v1.
- `sample_summary` inherits sample axis from time-series metric frame and never accepts request grain.
- `test` rejects metric frames and source-style request fields.
- `test` does not call semantic metric resolution or sample-summary SQL helpers.
- `validate` bundle exposes sample-summary steps and sample-frame artifacts.

## Open Boundaries

The following are intentionally out of scope:

- rate sample summaries
- paired sample summaries
- segment-as-sample summaries
- panel pooling rules
- compatibility adapters for old `test` source-type requests
- a full general-purpose Plan DSL executor

## Self-Review

- No placeholders remain.
- The design keeps `sample_summary` as a transform, not an atomic intent.
- The request shape does not include `grain`; sample grain is inherited from `metric_frame`.
- The implementation scope is a single coordinated contract/runtime/transport slice.
- Compatibility and migration behavior are explicitly out of scope.
