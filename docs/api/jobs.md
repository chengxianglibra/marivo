# Jobs

Read-only job inspection endpoints for the `/ui` query workbench.

`Jobs` is an auxiliary troubleshooting surface. It helps operators inspect
background task progress and failure details for a session, but it is not the
primary explanation path for canonical outcomes. The UI must not expose
`POST /jobs` or `POST /jobs/{job_id}/cancel`.

## List Jobs

`GET /jobs`

Optional query parameters:

- `session_id`: return only jobs linked to the given session
- `status`: return only jobs in the given lifecycle state

Example response:

```json
[
  {
    "job_id": "job_abc123def456",
    "session_id": "sess_abc123def456",
    "job_type": "step",
    "status": "completed",
    "payload": {
      "step_type": "profile_table",
      "params": {
        "table_name": "analytics.watch_events"
      }
    },
    "created_at": "2026-04-10T08:00:00+00:00",
    "updated_at": "2026-04-10T08:00:01+00:00",
    "submitted_at": "2026-04-10T08:00:00+00:00",
    "started_at": "2026-04-10T08:00:00+00:00",
    "completed_at": "2026-04-10T08:00:01+00:00"
  }
]
```

Field notes:

- `created_at` is the job submission timestamp.
- `updated_at` is the latest lifecycle transition timestamp, derived from
  `completed_at`, `started_at`, or `submitted_at`.
- `payload` is descriptive metadata for the queued work. UI surfaces should
  present it as a summary or expandable JSON, not as an execution authoring form.

## Get Job Detail

`GET /jobs/{job_id}`

Returns the same fields as the list surface, plus failure detail when present.

Failed example:

```json
{
  "job_id": "job_failed123456",
  "session_id": "sess_abc123def456",
  "job_type": "step",
  "status": "failed",
  "payload": {
    "step_type": "nonexistent_step"
  },
  "error_message": "Unknown step_type: nonexistent_step",
  "created_at": "2026-04-10T08:10:00+00:00",
  "updated_at": "2026-04-10T08:10:00+00:00",
  "submitted_at": "2026-04-10T08:10:00+00:00",
  "started_at": "2026-04-10T08:10:00+00:00",
  "completed_at": "2026-04-10T08:10:00+00:00"
}
```

## UI Mapping

The redesigned `/ui` `Jobs` page should expose only:

- filters: `session_id`, `status`
- list columns: `job_id`, `session_id`, `job_type`, `status`, `created_at`, `updated_at`
- detail panel: payload summary, linked session, error detail

It must not expose submit, cancel, retry, or any other job control.
