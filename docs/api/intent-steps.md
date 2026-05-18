# Intent Action Surface

This page documents the current HTTP implementation for submitting typed
analysis intents. The path acts as the intent discriminator; request bodies do
not contain a top-level `step_type`.

## Endpoints

| Intent | Endpoint | Response model |
|--------|----------|----------------|
| `observe` | `POST /sessions/{session_id}/intents/observe` | `ObserveResponse` |
| `compare` | `POST /sessions/{session_id}/intents/compare` | `CompareResponse` |
| `decompose` | `POST /sessions/{session_id}/intents/decompose` | `DecomposeResponse` |
| `correlate` | `POST /sessions/{session_id}/intents/correlate` | `CorrelateResponse` |
| `detect` | `POST /sessions/{session_id}/intents/detect` | `DetectResponse` |
| `forecast` | `POST /sessions/{session_id}/intents/forecast` | `ForecastResponse` |
| `test` | `POST /sessions/{session_id}/intents/test` | `TestResponse` |
| `validate` | `POST /sessions/{session_id}/intents/validate` | `ValidateResponse` |
| `attribute` | `POST /sessions/{session_id}/intents/attribute` | `AttributeResponse` |
| `diagnose` | `POST /sessions/{session_id}/intents/diagnose` | `DiagnoseResponse` |

## Common Response Envelope

Atomic AOI-backed intents return:

```json
{
  "intent_type": "observe",
  "step_type": "observe",
  "step_ref": {
    "session_id": "sess_123",
    "step_id": "step_123",
    "step_type": "observe"
  },
  "artifact_id": "art_123",
  "result": {
    "artifact_id": "art_123",
    "result": {
      "value": 42.0
    }
  },
  "provenance": {},
  "product_metadata": null
}
```

For atomic intents, the top-level envelope fields remain stable. The nested
`result` is the AOI artifact wrapper, and the generated AOI artifact result
class is under `result.result`. Marivo query/provenance/product metadata stays
outside the AOI result, in sibling envelope fields such as `provenance` and
`product_metadata`.

For derived intents, the top-level `result` remains a Marivo bundle. AOI-typed
sub-artifacts are carried in `result.aoi_artifacts` as AOI `Artifact1` or
`Artifact2` values.

## Atomic AOI Intents

### Observe

```http
POST /sessions/{session_id}/intents/observe
```

Scalar observation request:

```json
{
  "metric": "order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-02-01T00:00:00Z"
  }
}
```

Time-series observation request:

```json
{
  "metric": "order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-02-01T00:00:00Z"
  },
  "granularity": "day"
}
```

`granularity: "hour"` can run on a native timestamp time field or on datasets that expose
date + hour partition fields such as `log_date` and `log_hour`. Segmenting by an hour column,
for example `dimensions: ["log_hour"]`, is still segmented observe: it returns dimension slices
inside the requested day-level window and does not request hourly time-series buckets.

For Trino sources with varchar timestamp fields like `2026-05-15 09:09:10`, prefer a
`TRINO` field expression such as `date_parse(create_time, '%Y-%m-%d %H:%i:%s')`.
When only the simple ANSI expression `CAST(create_time AS TIMESTAMP)` is present, Marivo rewrites
it to the equivalent Trino parse expression during time-axis resolution.

`granularity` accepts `hour`, `day`, `week`, `month`, `quarter`, or `year`.

Segmented and time-series observe use a fixed internal row cap of 1000. The
request contract does not expose an adjustable `limit` parameter.

Segmented observation request:

```json
{
  "metric": "order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-02-01T00:00:00Z"
  },
  "dimensions": ["country"]
}
```

### Compare

```http
POST /sessions/{session_id}/intents/compare
```

```json
{
  "current_artifact_id": "art_left",
  "baseline_artifact_id": "art_right",
  "compare_type": "normal"
}
```

`compare_type` defaults to `normal` when omitted. It is the only public calendar
alignment control: `normal` compares scalar, segmented, or time-series observe
artifacts. For segmented observe artifacts, including hour partition slices such
as `dimensions: ["log_hour"]`, compare joins rows by segment key and returns a
`segmented_delta` result. For time-series observe artifacts, `normal` pairs
buckets by relative position in the left and right artifact windows.
`holiday_aligned`, `weekday_aligned`, and `holiday_and_weekday_aligned` are
accepted only for time-series compare artifacts. Holiday strategies read
configured calendar data; all alignment strategies fall back to relative-position
pairing when a more specific bucket match is absent.

### Decompose

```http
POST /sessions/{session_id}/intents/decompose
```

```json
{
  "compare_artifact_id": "art_compare",
  "dimension": "country",
  "limit": 10
}
```

### Correlate

```http
POST /sessions/{session_id}/intents/correlate
```

```json
{
  "left_artifact_id": "art_left_timeseries",
  "right_artifact_id": "art_right_timeseries",
  "method": "spearman",
  "min_pairs": 5
}
```

`method` may be `pearson` or `spearman`. `min_pairs` is optional and must be at least 1.

### Detect

```http
POST /sessions/{session_id}/intents/detect
```

```json
{
  "metric": "order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-02-01T00:00:00Z"
  },
  "granularity": "day",
  "dimension": "country",
  "strategy": "point_anomaly",
  "sensitivity": "aggressive",
  "limit": 20
}
```

### Forecast

```http
POST /sessions/{session_id}/intents/forecast
```

```json
{
  "source_artifact_id": "art_timeseries",
  "horizon": 14
}
```

### Test

```http
POST /sessions/{session_id}/intents/test
```

```json
{
  "metric": "order_revenue",
  "current": {
    "time_scope": {
      "field": "order_date",
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-02-01T00:00:00Z"
    }
  },
  "baseline": {
    "time_scope": {
      "field": "order_date",
      "start": "2025-12-01T00:00:00Z",
      "end": "2026-01-01T00:00:00Z"
    }
  },
  "grain": "day",
  "kind": "numeric",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "greater",
    "significance": "balanced"
  }
}
```

`kind` only accepts `numeric`. `hypothesis.family` only accepts
`two_sample_mean`; `hypothesis` has no label field and `test` has no request
`method` parameter. `hypothesis.significance` accepts `conservative`,
`balanced`, or `aggressive`; these resolve internally to alpha thresholds
`0.01`, `0.05`, and `0.10`. `grain` is required and defines the statistical
sample unit used to split each source slice before computing the test; it is
not an output selector. Supported values are `hour`, `day`, `week`, `month`,
`quarter`, and `year`. Time slice boundaries must align to the selected
`grain`; for example, `quarter` windows start and end on Jan/Apr/Jul/Oct 1.

## Derived Intents

`validate`, `attribute`, and `diagnose` use generated AOI request models as
runtime contracts.

### Attribute

```http
POST /sessions/{session_id}/intents/attribute
```

```json
{
  "metric": "metric.order_revenue",
  "current": {
    "time_scope": {
      "field": "order_date",
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-02-01T00:00:00Z"
    },
    "filter": {
      "dialects": [
        {
          "dialect": "ANSI_SQL",
          "expression": "country = 'US'"
        }
      ]
    }
  },
  "baseline": {
    "time_scope": {
      "field": "order_date",
      "start": "2025-01-01T00:00:00Z",
      "end": "2025-02-01T00:00:00Z"
    }
  },
  "dimensions": ["country"],
  "decomposition_method": "delta_share",
  "decomposition_limit": 5
}
```

`current` and `baseline` use AOI `Slice` (`time_scope` plus optional `filter`).
They do not accept additional wrapper fields.

### Diagnose

```http
POST /sessions/{session_id}/intents/diagnose
```

`include_details=false` may be passed as a query parameter to return a compact
projection that omits embedded `aoi_artifacts` and `drivers[].rows`. The full
diagnosis and child artifacts are still committed; callers can lazy-load driver
rows from the returned `decompose_ref.artifact_id` via
`GET /sessions/{session_id}/artifacts/{artifact_id}`. HTTP defaults to
`include_details=true` for backward compatibility.

Diagnose is auto-detect only:

```json
{
  "metric": "metric.order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01",
    "end": "2026-02-01"
  },
  "granularity": "day",
  "filter": {
    "dialects": [
      {
        "dialect": "ANSI_SQL",
        "expression": "country = 'US'"
      }
    ]
  },
  "scan_dimension": "country",
  "dimensions": ["country"],
  "strategy": "point_anomaly",
  "candidate_limit": 3,
  "decomposition_limit": 5
}
```

`scan_dimension` is the optional detection split axis: when present, detect
scans one time series per value of that dimension. `dimensions` are the
attribution dimensions used after candidates are found. `candidate_limit`
bounds how many anomaly candidates are diagnosed end-to-end; `decomposition_limit`
bounds driver rows per diagnosed candidate and attribution dimension.
Each driver is a dimension-level result: `rows` remains the segment-level
attribution detail, while `top_segment`, `total_contribution`, and
`total_contribution_share` provide the common summary fields consumers need
without traversing `rows`.

Each diagnosis candidate separates anomaly magnitude from attribution input:
`anomaly_evidence` reports the detect-side current value, expected value, and
deviation used to rank the anomaly candidate. `attribution_comparison` reports
the actual current/baseline observe windows and scalar delta that feed
`decompose`. For `point_anomaly`, these values can differ because the detect
expected value is the scan-window statistical baseline, while attribution uses
the previous adjacent equal-length baseline window.

Known current/baseline change attribution uses `attribute` with `left` as the
current slice and `right` as the baseline slice. `baseline_policy` is fixed by
the diagnose runtime and is not a request field.

### Validate

```http
POST /sessions/{session_id}/intents/validate
```

HTTP and MCP validate calls cross into runtime as the generated AOI `Validate`
model. Runtime validate requires the complete hypothesis object; MCP may fill
transport defaults before constructing that generated model:

```json
{
  "metric": "metric.order_revenue",
  "current": {
    "time_scope": {
      "field": "order_date",
      "start": "2026-01-01T00:00:00Z",
      "end": "2026-02-01T00:00:00Z"
    }
  },
  "baseline": {
    "time_scope": {
      "field": "order_date",
      "start": "2025-01-01T00:00:00Z",
      "end": "2025-02-01T00:00:00Z"
    }
  },
  "grain": "day",
  "hypothesis": {
    "family": "two_sample_mean",
    "alternative": "greater",
    "significance": "balanced"
  }
}
```

`current` and `baseline` use AOI `Slice` (`time_scope` plus optional `filter`).
`grain` is required and uses AOI `TimeGranularity` values as the statistical
sample unit (`hour`, `day`, `week`, `month`, `quarter`, or `year`).
Time slice boundaries must align to the selected `grain`. Derived `scope` and
`method` are not part of the runtime contract.

## Errors

Common transport statuses:

| Status | Scenario |
|--------|----------|
| `404` | session or referenced artifact not found |
| `409` | semantic runtime is not ready, or the request is incompatible with resolved semantic objects |
| `422` | request body fails schema validation or intent validation |
| `501` | runtime method is not implemented |
| `502` | unexpected execution error |

Read session evidence through [`session-state.md`](session-state.md) and
[`context-surface.md`](context-surface.md); intent endpoints are write/execution
surfaces.
