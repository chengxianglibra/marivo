# Session Lifecycle

This page documents the current HTTP implementation for session root
lifecycle. Session root endpoints create/read/list/terminate the analysis
container; typed intent execution and evidence reads are separate surfaces.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Create a session |
| `GET` | `/sessions` | List sessions |
| `GET` | `/sessions/{session_id}` | Get one session |
| `POST` | `/sessions/{session_id}/terminate` | Terminate a session |

`PATCH /sessions/{session_id}` and `POST /sessions/{session_id}/rollover` are
not mounted by the current router.

## Create Session

```http
POST /sessions
```

Request body:

```json
{
  "goal": "Investigate the week-over-week watch time decline for mobile users",
  "budget": {
    "max_scan_bytes": 500000000000,
    "max_latency_sec": 120
  }
}
```

Request rules:

- `goal` is a required string.
- `budget` is optional; omitted values default to `max_scan_bytes=500000000000`
  and `max_latency_sec=120`.
- Session ownership is resolved from `X-Marivo-User` when the header is
  present. The body does not accept `execution_identity`, `policy`, `scope`,
  `time_scope`, or step-level execution controls.

Response body is an `AnalysisSession`:

```json
{
  "session_id": "sess_123",
  "goal": {
    "question": "Investigate the week-over-week watch time decline for mobile users"
  },
  "scope": {
    "constraints": {}
  },
  "owner_user": "alice",
  "lifecycle": {
    "status": "open",
    "terminal_reason": null,
    "ended_at": null,
    "rollover_from_session_id": null
  },
  "state_summary": {
    "state_view_ref": {
      "session_id": "sess_123",
      "view_type": "session_state_view"
    }
  },
  "created_at": "2026-05-15T10:30:00+00:00",
  "updated_at": "2026-05-15T10:30:00+00:00",
  "schema_version": "analysis_session.v1"
}
```

## Get Session

```http
GET /sessions/{session_id}
```

Returns the same `AnalysisSession` shape as create. This endpoint does not
inline `SessionStateView`, proposition context, step history, or artifacts.

## List Sessions

```http
GET /sessions
```

Supported query parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by lifecycle status |
| `session_id` | string | Filter by session id |
| `limit` | integer | Maximum returned sessions |
| `page_token` | string | Cursor for the next page |

Response body:

```json
{
  "items": [],
  "next_page_token": null
}
```

`items[]` contains `AnalysisSession` objects.

## Terminate Session

```http
POST /sessions/{session_id}/terminate
```

Request body:

```json
{
  "terminal_reason": "user_closed"
}
```

Termination requires a caller identity from `X-Marivo-User`. On success, the
response is the updated `AnalysisSession` with `lifecycle.status = "closed"`.

Common statuses:

| Status | Scenario |
|--------|----------|
| `400` | invalid create payload |
| `401` | create requires a user but no identity is set |
| `403` | caller cannot terminate this session |
| `404` | session not found |
| `409` | session cannot be terminated in its current lifecycle state |
| `422` | request body fails schema validation |
