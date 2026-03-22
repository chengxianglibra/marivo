# Jobs

The Jobs API provides asynchronous execution for long-running steps and plans. Instead of waiting for a step or plan to complete in a single HTTP request, you can submit it as a job and poll for its status.

Jobs are automatically used when no event loop is available (synchronous fallback). When an event loop is present, steps executed via the Sessions API (`POST /sessions/{id}/steps/{type}`) run synchronously in the request.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jobs` | Submit a job |
| `GET` | `/jobs` | List jobs |
| `GET` | `/jobs/{job_id}` | Get a job |
| `POST` | `/jobs/{job_id}/cancel` | Cancel a pending job |

---

## Job Lifecycle

```
pending → running → completed
                 ↘ failed
                 ↘ cancelled  (only from pending)
```

---

## Submit Job

```
POST /jobs
```

Submits a step or plan for asynchronous execution. The job runs in the background; the response returns immediately with the job ID.

### Request Body

**Submit a step as a job:**

```json
{
  "session_id": "sess_...",
  "job_type": "step",
  "payload": {
    "step_type": "aggregate_query",
    "params": {
      "table_name": "events.user_video_watch",
      "group_by": ["device_type", "region"],
      "measures": [
        {"expr": "AVG(watch_duration_sec)", "alias": "avg_watch_sec"}
      ],
      "filter": "event_date >= '2024-01-01'"
    }
  }
}
```

**Submit a plan as a job:**

```json
{
  "session_id": "sess_...",
  "job_type": "plan",
  "payload": {
    "plan_id": "plan_...",
    "continue_on_failure": false
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | Session to run the job within |
| `job_type` | string | yes | `"step"` or `"plan"` |
| `payload` | object | no | Job-specific parameters (default: `{}`) |

**Payload for `job_type: "step"`:**

| Field | Type | Description |
|-------|------|-------------|
| `step_type` | string | Step type to execute (e.g., `"compare_metric"`) |
| `params` | object | Step parameters |

**Payload for `job_type: "plan"`:**

| Field | Type | Description |
|-------|------|-------------|
| `plan_id` | string | ID of an `approved` plan to execute |
| `continue_on_failure` | boolean | Whether to continue past step failures |

### Response

```json
{
  "job_id": "job_a1b2c3d4e5f6",
  "session_id": "sess_...",
  "job_type": "step",
  "payload": {...},
  "status": "pending",
  "result": null,
  "error_message": null,
  "submitted_at": "2024-01-15T10:00:00+00:00",
  "started_at": null,
  "completed_at": null
}
```

---

## List Jobs

```
GET /jobs
```

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Filter by session |
| `status` | string | Filter by status: `pending`, `running`, `completed`, `failed`, `cancelled` |

### Response

Array of job objects.

---

## Get Job

```
GET /jobs/{job_id}
```

Poll this endpoint to check job completion.

### Response

**While running:**

```json
{
  "job_id": "job_...",
  "session_id": "sess_...",
  "job_type": "step",
  "status": "running",
  "result": null,
  "error_message": null,
  "submitted_at": "2024-01-15T10:00:00+00:00",
  "started_at": "2024-01-15T10:00:01+00:00",
  "completed_at": null
}
```

**On completion:**

```json
{
  "job_id": "job_...",
  "session_id": "sess_...",
  "job_type": "step",
  "status": "completed",
  "result": {
    "step_id": "step_...",
    "step_type": "aggregate_query",
    "status": "completed",
    "summary": "...",
    "result": {...}
  },
  "error_message": null,
  "submitted_at": "2024-01-15T10:00:00+00:00",
  "started_at": "2024-01-15T10:00:01+00:00",
  "completed_at": "2024-01-15T10:00:09+00:00"
}
```

**On failure:**

```json
{
  "job_id": "job_...",
  "status": "failed",
  "result": null,
  "error_message": "Engine query error: table 'events.user_video_watch' not found",
  "completed_at": "2024-01-15T10:00:03+00:00"
}
```

---

## Cancel Job

```
POST /jobs/{job_id}/cancel
```

Cancels a job in `pending` status. Jobs that are already `running` cannot be cancelled (they will complete or fail naturally).

### Response

```json
{
  "job_id": "job_...",
  "status": "cancelled",
  "completed_at": "2024-01-15T10:00:02+00:00"
}
```

If the job is not in `pending` status:

```json
{
  "detail": "Cannot cancel job in 'running' status. Only pending jobs can be cancelled."
}
```
(HTTP 409 Conflict)
