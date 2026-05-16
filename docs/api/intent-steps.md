# Intent Action Surface

This page documents the current HTTP implementation for submitting typed
analysis intents. The path acts as the intent discriminator; request bodies do
not contain a top-level `step_type`.

## Endpoints

| Intent | Endpoint | Response model |
|--------|----------|----------------|
| `observe` | `POST /sessions/{session_id}/intents/observe` | `ExecutionEnvelope` |
| `compare` | `POST /sessions/{session_id}/intents/compare` | `ExecutionEnvelope` |
| `decompose` | `POST /sessions/{session_id}/intents/decompose` | `ExecutionEnvelope` |
| `correlate` | `POST /sessions/{session_id}/intents/correlate` | `ExecutionEnvelope` |
| `detect` | `POST /sessions/{session_id}/intents/detect` | `ExecutionEnvelope` |
| `forecast` | `POST /sessions/{session_id}/intents/forecast` | `ExecutionEnvelope` |
| `attribute` | `POST /sessions/{session_id}/intents/attribute` | JSON object |
| `diagnose` | `POST /sessions/{session_id}/intents/diagnose` | JSON object |

`/sessions/{session_id}/intents/test` is not mounted by the current HTTP
router. `validate` is not a derived HTTP analysis intent; semantic-model
validation remains available through the semantic-model APIs.

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
  "result": {},
  "provenance": {},
  "product_metadata": null
}
```

`result` contains the AOI artifact payload. `provenance` and
`product_metadata` carry Marivo runtime metadata.

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
  },
  "filter": null
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
  "filter": null,
  "granularity": "day",
  "dimensions": null
}
```

Segmented observation request:

```json
{
  "metric": "order_revenue",
  "time_scope": {
    "field": "order_date",
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-02-01T00:00:00Z"
  },
  "filter": null,
  "dimensions": ["country"]
}
```

### Compare

```http
POST /sessions/{session_id}/intents/compare
```

```json
{
  "left_artifact_id": "art_left",
  "right_artifact_id": "art_right",
  "compare_type": "normal"
}
```

`compare_type` defaults to `normal` when omitted. It is the only public calendar
alignment control: `normal` uses observed bucket intersection, while `yoy`,
`mom`, `wow`, `weekday_aligned_yoy`, `weekday_aligned_mom`, and
`holiday_aligned_yoy` are accepted only for time-series compare artifacts.
`holiday_aligned_yoy` reads configured calendar data and uses holiday keys
before weekday and natural-date fallback.

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
  "method": "spearman"
}
```

`method` may be `pearson` or `spearman`.

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
  "filter": null,
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
  "horizon": 14,
  "profile": "auto"
}
```

## Derived Intents

Derived intent request models are currently transport-local compatibility DTOs.

### Attribute

```http
POST /sessions/{session_id}/intents/attribute
```

```json
{
  "metric": "metric.order_revenue",
  "left": {
    "time_scope": {
      "kind": "range",
      "start": "2026-01-01",
      "end": "2026-02-01"
    },
    "scope": null
  },
  "right": {
    "time_scope": {
      "kind": "range",
      "start": "2025-01-01",
      "end": "2025-02-01"
    },
    "scope": null
  },
  "dimensions": ["country"],
  "decomposition_method": "delta_share",
  "decomposition_limit": 5
}
```

### Diagnose

```http
POST /sessions/{session_id}/intents/diagnose
```

Auto-detect mode:

```json
{
  "mode": "auto_detect",
  "metric": "metric.order_revenue",
  "time_scope": {
    "kind": "range",
    "start": "2026-01-01",
    "end": "2026-02-01"
  },
  "granularity": "day",
  "candidate_dimensions": ["country"],
  "candidate_limit": 5,
  "followup_limit": 3,
  "decomposition_limit": 5
}
```

Explicit-compare mode uses `current` and `baseline` observe-shaped inputs
instead of `time_scope` / `granularity`.

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
