---
status: draft
canonical-path: null
created: 2026-05-22
---

# Detect Candidate Set Design

Date: 2026-05-22

Status: approved design draft for implementation planning.

## Purpose

Marivo's `detect` intent should follow the analysis operation target architecture:

```text
detect(metric_frame(time_series | panel)) -> candidate_set(point_anomaly_candidates)
detect(delta_frame(time_series_delta | panel_delta)) -> candidate_set(period_shift_candidates)
```

The intent discovers anomaly candidates from already committed typed artifacts. It
must not read source tables, resolve metric execution context, construct hidden
baseline windows, or rerun query compilation internally.

This task is intentionally a breaking semantic upgrade. It does not preserve old
source-style detect requests, old `artifact_type: "anomaly_candidates"` payloads, or
old AOI `AnomalyCandidatesResult` response bodies.

## Chosen Approach

Use one public input field:

```json
{
  "source_artifact_id": "art_metric_or_delta_123",
  "sensitivity": "balanced",
  "limit": 5
}
```

`strategy` is not a request parameter. The source artifact family and shape determine
the detection strategy:

```text
metric_frame(time_series | panel) -> point_anomaly
delta_frame(time_series_delta | panel_delta) -> period_shift
```

This removes redundant request state and prevents invalid combinations such as
`delta_frame + point_anomaly` or `metric_frame + period_shift`.

Out of scope:

- source-style detect input using `metric`, `time_scope`, `granularity`, `filter`, or
  `dimension`
- compatibility shims for old AOI detect requests or old committed detect artifacts
- Plan DSL, transform guard, or general artifact input expression support
- derived manifest redesign
- implementing period shift by querying previous-adjacent source windows inside
  `detect`

## Input Contract

`Detect` request:

```ts
type Detect = {
  source_artifact_id: string;
  sensitivity?: "conservative" | "balanced" | "aggressive";
  limit?: number;
};
```

Normalization:

- omitted `sensitivity` becomes `"aggressive"`
- omitted `limit` means implementation default
- explicit `null` optional fields are not part of the AOI contract

Validation:

- `source_artifact_id` must resolve to a committed artifact in the current session
- the artifact must be a supported frame family and shape
- `limit`, when present, must be greater than zero
- the frame must contain scan-ready series and point data
- the artifact axes must match its declared shape

Supported combinations:

| Source family | Source shape | Resolved strategy | Output shape |
|---------------|--------------|-------------------|--------------|
| `metric_frame` | `time_series` | `point_anomaly` | `point_anomaly_candidates` |
| `metric_frame` | `panel` | `point_anomaly` | `point_anomaly_candidates` |
| `delta_frame` | `time_series_delta` | `period_shift` | `period_shift_candidates` |
| `delta_frame` | `panel_delta` | `period_shift` | `period_shift_candidates` |

Rejected combinations:

- `metric_frame(scalar | segmented)`
- `delta_frame(scalar_delta | segmented_delta)`
- `attribution_frame`
- `candidate_set`
- association, test, forecast, failure, or unknown artifact shapes

Rejected inputs should fail closed with stable `INVALID_ARGUMENT` or
`ARTIFACT_NOT_FOUND` errors. Runtime must not silently reinterpret unsupported frames
as empty scans.

## Strategy Semantics

### Point Anomaly

`point_anomaly` scans numeric `metric_frame` points inside each series.

For `metric_frame(time_series)`, there is one overall series. For
`metric_frame(panel)`, each series is scanned independently and the series `keys`
identify the candidate segment.

Candidate scoring may reuse the existing z-score style implementation, but the input
comes only from the frame payload. The scanner may drop null or non-numeric values for
scoring while keeping enough quality metadata to explain skipped points.

### Period Shift

`period_shift` scans `delta_frame` points for material current-vs-baseline shifts.

The `delta_frame` already carries compare alignment, current/baseline values, delta
measures, and lineage. `detect` consumes those values directly. It must not construct
or query a previous-adjacent baseline.

For `delta_frame(time_series_delta)`, each matched time bucket can become a candidate.
For `delta_frame(panel_delta)`, each matched time bucket and dimension-key series can
become a candidate.

## Output Contract

Successful `detect` output is a top-level AOI artifact:

```ts
type CandidateSetArtifact = {
  artifact_id: string;
  artifact_family: "candidate_set";
  shape: "point_anomaly_candidates" | "period_shift_candidates";
  subject: CandidateScanSubject;
  axes: Array<TimeAxis | DimensionAxis>;
  measures: CandidateMeasure[];
  capabilities: ["filterable"];
  lineage: CandidateSetLineage;
  payload: CandidateSetPayload;
};
```

The artifact is committed even when no candidates are found:

```json
{
  "artifact_family": "candidate_set",
  "shape": "point_anomaly_candidates",
  "payload": {
    "items": [],
    "scan_summary": {
      "scanned_series_count": 1,
      "total_candidate_count": 0
    },
    "truncation": {
      "returned_candidate_count": 0,
      "total_candidate_count": 0,
      "truncated": false
    },
    "quality": {
      "status": "detectable",
      "issues": []
    }
  }
}
```

### Subject

`subject` identifies the scan target and the source artifact:

```ts
type CandidateScanSubject = {
  kind: "candidate_scan";
  metric_ref: string;
  source_artifact_id: string;
  source_artifact_family: "metric_frame" | "delta_frame";
  source_shape: string;
};
```

`metric_ref` is read from the source artifact. If a source artifact lacks a usable
metric reference, `detect` fails rather than fabricating one.

### Lineage

`lineage` records the resolved operation and source artifact:

```ts
type CandidateSetLineage = {
  operation: "detect";
  source_artifact_ids: [string];
  strategy: "point_anomaly" | "period_shift";
};
```

`strategy` is output metadata, not request state.

### Candidate Item

Each candidate item has a stable item id and a stable point reference into the source
frame:

```ts
type CandidateItem = {
  item_id: string;
  source_point_ref?: FramePointRef;
  source_delta_point_ref?: FramePointRef;
  window: { start: string; end: string };
  keys: Record<string, string> | null;
  value: number | null;
  baseline_value?: number | null;
  delta_abs?: number | null;
  delta_pct?: number | null;
  score: number;
  direction: "increase" | "decrease" | "unknown";
};
```

Rules:

- point-anomaly candidates carry `source_point_ref`
- period-shift candidates carry `source_delta_point_ref`
- `keys` is `null` for an overall time series and a dimension-key map for panel input
- `value` is the observed metric value for point anomalies and the current value for
  period shifts
- `baseline_value`, `delta_abs`, and `delta_pct` are present for period-shift
  candidates when the source delta point provides them
- `score` is non-negative and comparable only within the same candidate set
- `direction` is derived from the candidate value or delta sign

### Frame Point Ref

`FramePointRef` is public AOI contract, not private provenance:

```ts
type FramePointRef = {
  artifact_id: string;
  series_index: number;
  point_index: number;
  series_keys: Record<string, string>;
  point_key: string;
};
```

`point_key` is derived from the frame point's `window.start` when present. If a future
frame shape has explicit row ids, the point key should use that row id. Panel and delta
panel references are stable through `artifact_id + series_keys + point_key`, with
indexes retained for efficient lookup and projection.

## Runtime Design

`marivo/runtime/intents/detect.py` becomes an artifact scanner:

1. Validate request keys.
2. Resolve `source_artifact_id` with `runtime.resolve_artifact_with_step_by_id`.
3. Validate supported family and shape.
4. Iterate source frame series and points using shared frame helper functions.
5. Score candidates using the resolved strategy.
6. Rank candidates deterministically.
7. Apply `limit` after ranking.
8. Commit a `candidate_set` artifact through extraction.
9. Insert the detect step with provenance that references the source artifact and
   resolved strategy.

The runner must remove these responsibilities:

- metric normalization
- metric execution context resolution
- time scope validation for source-style requests
- filter conversion
- scoped query compilation
- source table execution
- implicit previous-adjacent baseline reads

Shared frame helpers should provide stable iteration over:

- `metric_frame.payload.series[].points[]`
- `delta_frame.payload.series[].points[]`
- series keys
- point windows
- point measure values
- metric refs and time axes

These helpers belong beside existing frame helper functions rather than inside the
detect runner.

## AOI Spec And Generated Contracts

Update the public AOI contract files:

- `aoi-spec/schema/aoi.schema.yaml`
- `aoi-spec/schema/aoi.schema.json`
- `aoi-spec/spec.md`
- `docs/specs/analysis/aoi-spec.schema.yaml`
- generated `marivo/contracts/generated/aoi.py`

Contract changes:

- `Detect` request contains only `source_artifact_id`, `sensitivity`, and `limit`
- remove `AnomalyCandidatesResult` as detect's public success output
- add `CandidateSetArtifact`
- add candidate item, candidate scan subject, candidate set lineage, and frame point
  ref definitions
- include `candidate_set` in AOI artifact-family validation
- update operation compatibility documentation so `detect` consumes scan-ready
  `metric_frame` or compatible `delta_frame`

Generated models must come from the schema generator. Do not edit generated classes
manually.

## HTTP And MCP Surfaces

The HTTP endpoint path may remain:

```text
POST /sessions/{session_id}/intents/detect
```

Its request and response contract changes in place. `DetectResponse.result` should be
`aoi.CandidateSetArtifact` or a failure artifact, not `Artifact.result =
AnomalyCandidatesResult`.

The MCP detect tool accepts only:

- `source_artifact_id`
- `sensitivity`
- `limit`

MCP and HTTP must reject removed fields:

- `metric`
- `time_scope`
- `granularity`
- `filter`
- `dimension`
- `strategy`

Runtime lowering must map generated AOI detect requests to the new runner params
without synthesizing source-style fields.

## Evidence And Downstream Dependencies

Evidence extraction remains item-based:

```text
candidate_set artifact
  -> DetectArtifactExtractor
  -> one anomaly_candidate finding per payload.items[]
```

Canonical finding type stays `anomaly_candidate`. The artifact family changes from
`anomaly_candidates` to `candidate_set`.

Extractor registration changes from:

```text
("anomaly_candidates", "v1")
```

to:

```text
("candidate_set", null)
```

or to the current candidate-set schema version if AOI introduces an explicit version.

`AnomalyCandidateFinding.payload` should be populated from candidate items:

- `candidate_ref` points to `candidate_set.payload.items[item_id]`
- `source_point_ref` or `source_delta_point_ref` is copied from the candidate
- `score` comes from `item.score`
- `current_value` comes from `item.value`
- `baseline_value` comes from `item.baseline_value`
- `deviation_absolute` comes from `item.delta_abs`
- `deviation_relative` comes from `item.delta_pct`
- `direction` comes from `item.direction`

Downstream behavior:

- proposition seeding remains `anomaly_candidate -> anomaly`
- assessment context remains `anomaly_candidate -> anomaly_assessment`
- session state success-empty handling switches from `anomaly_candidates` to
  `candidate_set`
- report and projection code detect the new artifact family
- diagnose follow-up selectors reference `candidate_set.items[]` and source frame
  refs instead of old candidate rows

No compatibility:

- do not accept old `artifact_type: "anomaly_candidates"` for extraction
- do not generate old `AnomalyCandidatesResult`
- do not preserve old public fields such as `bucket_start`, `series_keys`, or
  top-level detect request metadata

## Documentation Updates

Update these docs in the implementation change:

- `docs/specs/analysis/intents/atomic/detect.md`
- `docs/specs/analysis/aoi-spec.schema.yaml`
- `aoi-spec/spec.md`
- HTTP or MCP docs that show detect request examples
- downstream evidence docs that list artifact families or success-empty behavior

The detect atomic intent doc should describe:

- artifact-only request
- strategy inferred from source artifact family and shape
- `candidate_set` output
- unsupported source-style fields
- how period shift is represented through `delta_frame` input

## Testing Plan

Targeted verification:

```bash
make test TESTS='tests/contracts/test_generated_models.py'
make test TESTS='tests/contracts/test_aoi_runtime_contract.py'
make test TESTS='tests/runtime/intents/test_detect_runner.py'
make test TESTS='tests/runtime/evidence/test_detect_extractor.py'
make test TESTS='tests/transports/http/test_http_aoi_intents.py'
make test TESTS='tests/transports/mcp/test_mcp_aoi_adapter.py'
make typecheck
```

Expected test updates:

- generated AOI model tests assert new detect request fields and candidate-set artifact
- runtime detect tests build metric-frame and delta-frame artifacts as source inputs
- runtime detect tests prove no SQL compilation or source table execution occurs
- period-shift tests consume delta-frame points
- invalid combination tests cover unsupported frame families and shapes
- extractor tests register under `candidate_set` and emit `anomaly_candidate` findings
- HTTP tests reject removed source-style fields
- MCP adapter tests expose only the new detect request surface
- session/report tests use `candidate_set` for success-empty and summaries

## Acceptance Criteria

- Public AOI detect accepts only artifact input.
- `strategy` is not a public request field.
- `metric_frame(time_series | panel)` input produces point-anomaly candidate sets.
- `delta_frame(time_series_delta | panel_delta)` input produces period-shift candidate
  sets.
- Detect runtime performs no source metric query or hidden baseline query.
- Successful detect commits a `candidate_set` artifact, including success-empty scans.
- Candidate items include stable source frame point refs.
- Evidence extraction emits canonical `anomaly_candidate` findings from
  `candidate_set.payload.items[]`.
- HTTP, MCP, generated contracts, docs, and tests no longer expose or accept the old
  source-style detect contract.
