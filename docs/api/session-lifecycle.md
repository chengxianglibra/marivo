# Session Lifecycle

This document defines the target-state external HTTP contract for Marivo's session root lifecycle.

It binds the canonical session schema from [`docs/analysis/evidence-engine/schemas/session.md`](../analysis/evidence-engine/schemas/session.md) to stable HTTP resources. This is a target-state wire specification and does not describe or depend on the current implementation.

## Purpose

Use these endpoints when a client needs to:

- create a new analysis container
- read session root metadata and lifecycle state
- discover sessions by lifecycle status
- update mutable session root fields while the session remains open
- explicitly terminate a session
- roll over to a new session when immutable governance boundaries change

Do not use these endpoints as a substitute for:

- typed analysis step execution
- session state or proposition context retrieval
- agent-private planning state
- workflow/job orchestration state

The canonical session root remains a lightweight analysis container. Global evidence decisions belong to the state/context read surfaces, not to the session root.

## Canonical Resources

| Resource | Endpoint | Canonical payload |
|----------|----------|-------------------|
| Create session | `POST /sessions` | `AnalysisSession` |
| Get session | `GET /sessions/{session_id}` | `AnalysisSession` |
| List sessions | `GET /sessions` | `AnalysisSession` items plus transport paging metadata |
| Update mutable session fields | `PATCH /sessions/{session_id}` | `AnalysisSession` |
| Explicitly terminate a session | `POST /sessions/{session_id}/terminate` | `AnalysisSession` |
| Roll over to a new session | `POST /sessions/{session_id}/rollover` | rollover result envelope containing old and new `AnalysisSession` payloads |

`AnalysisSession` is the canonical session root payload. Transport wrappers used for listing or rollover are not part of canonical session identity.

## Canonical Session Payload

All session lifecycle endpoints use the canonical wire shape below unless the endpoint explicitly defines a transport envelope:

```json
{
  "session_id": "sess_123",
  "goal": {
    "question": "Why did watch time decline last week?"
  },
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  },
  "governance": {
    "policy_refs": [
      {
        "policy_id": "pol_abc",
        "policy_version": "3"
      }
    ],
    "budget": {
      "max_steps": 12,
      "max_scan_bytes": 100000000000,
      "max_latency_sec": 60
    },
    "warnings": [
      "Sampling disallowed by current policy set"
    ]
  },
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
  "created_at": "2024-01-15T10:30:00+00:00",
  "updated_at": "2024-01-15T10:30:00+00:00",
  "schema_version": "analysis_session.v1"
}
```

Wire invariants fixed by this contract:

- `goal.question` is descriptive task context, not canonical identity
- `execution_identity` freezes optional session-level execution user context; omit it or use `{}` when no runtime username should be recorded
- `execution_identity.session_user` is the username Marivo may inject into a username-aware execution engine such as Trino
- `execution_identity.actor_ref` is Marivo audit context for the calling agent or actor; it is not an authentication credential and does not participate in Trino authorization
- `governance.policy_refs` defines the immutable governance boundary for the session
- `governance.budget` and `governance.warnings` are mutable while the session is open
- `lifecycle.status` is one of `open`, `closed`, or `aborted`
- `state_summary.state_view_ref` always exists and points to the canonical session state surface
- `state_summary` is an entry handle only; clients must not treat it as a readiness or blocker summary

## Create Session

### `POST /sessions`

Creates a new open session root.

Request body:

```json
{
  "goal": "Investigate the week-over-week watch time decline for mobile users",
  "execution_identity": {
    "session_user": "alice",
    "actor_ref": "agent.alice"
  },
  "budget": {
    "max_scan_bytes": 500000000000,
    "max_latency_sec": 120
  },
  "policy": {
    "aggregate_only": true,
    "min_group_size": 100
  }
}
```

Request rules:

- `goal` is required
- `execution_identity` may be omitted; omitted or empty values persist and read back as `{}`
- `execution_identity.session_user` and `execution_identity.actor_ref` are optional session-level metadata fields
- `execution_identity.session_user` is frozen at session creation and is the only HTTP input for the per-analysis execution username
- typed intent payloads must not include `session_user`, and they cannot override the session's frozen execution user
- when provided, `execution_identity.session_user` and `execution_identity.actor_ref` are trimmed before persistence; blank-after-trim values are rejected
- session root request bodies must not define `scope`, `time_scope`, `focus`, `constraints`, `raw_filter`, or other step-level execution controls

Response:

- returns `201 Created` with the canonical `AnalysisSession` payload
- echoes the normalized `execution_identity`; if omitted or empty, the response contains `execution_identity: {}`
- the created session always starts as:
  - `lifecycle.status = "open"`
  - `lifecycle.terminal_reason = null`
  - `lifecycle.ended_at = null`
  - `lifecycle.rollover_from_session_id = null`

Example:

```bash
curl -s -X POST "http://localhost:8000/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Investigate the week-over-week watch time decline for mobile users",
    "execution_identity": {
      "session_user": "alice",
      "actor_ref": "agent.alice"
    },
    "budget": {
      "max_scan_bytes": 500000000000,
      "max_latency_sec": 120
    },
    "policy": {
      "aggregate_only": true,
      "min_group_size": 100
    }
  }' | jq .
```

## Get Session

### `GET /sessions/{session_id}`

Returns the canonical session root payload for a single session.

This endpoint is the root read baseline for:

- descriptive task context
- optional execution identity reflection via `execution_identity`
- normalized `execution_identity.session_user` and `execution_identity.actor_ref` values as frozen at session creation
- compatibility-only session scope reflection via `scope.constraints`; execution does not read session-level filters
- governance boundary
- lifecycle state
- entry to the canonical state surface via `state_summary.state_view_ref`

This endpoint does not inline `SessionStateView` or proposition context closure.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123" | jq .
```

## List Sessions

### `GET /sessions`

Returns paged session root discovery results.

Supported query parameters:

| Parameter | Type | Notes |
|-----------|------|-------|
| `status` | string | Filter by `open`, `closed`, or `aborted` |
| `session_id` | string | Prefix match against the session identifier |
| `limit` | integer | Maximum number of returned sessions |
| `page_token` | string | Cursor for continuing the same normalized query; current implementation uses a non-negative offset token |

Response shape:

```json
{
  "items": [
    {
      "session_id": "sess_123",
      "goal": {
        "question": "Why did watch time decline last week?"
      },
      "scope": {
        "constraints": {
          "region": "us"
        }
      },
      "governance": {
        "policy_refs": null,
        "budget": null,
        "warnings": null
      },
      "execution_identity": {},
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
      "created_at": "2024-01-15T10:30:00+00:00",
      "updated_at": "2024-01-15T10:30:00+00:00",
      "schema_version": "analysis_session.v1"
    }
  ],
  "next_page_token": null
}
```

List rules:

- `items` contains canonical `AnalysisSession` payloads
- each item includes the same `execution_identity` shape as `GET /sessions/{session_id}`; omitted values appear as `{}`
- `next_page_token` is transport-only and not part of canonical session identity
- `status` filters use root lifecycle semantics only; clients must not use read-surface concepts such as `active`, `latest`, or `ready`
- `session_id` filters apply prefix matching against canonical session identifiers
- `page_token` is session-query scoped and invalid across different normalized filters
- `limit` defaults to `25` and is capped at `100`

## Update Mutable Session Fields

### `PATCH /sessions/{session_id}`

Updates mutable root fields on an open session.

Request body:

```json
{
  "goal": {
    "question": "Investigate the mobile watch time decline after the January ranking launch"
  },
  "governance": {
    "budget": {
      "max_steps": 20,
      "max_scan_bytes": 750000000000,
      "max_latency_sec": 180
    },
    "warnings": [
      "Sampling remains approval-gated",
      "Cross-region joins may increase latency"
    ]
  }
}
```

Patch rules:

- `PATCH` is allowed only when `lifecycle.status = "open"`
- mutable fields:
  - `goal.question`
  - `governance.budget`
  - `governance.warnings`
- immutable fields:
  - `session_id`
  - `governance.policy_refs`
  - any `lifecycle.*` field
  - `state_summary`
  - `created_at`
  - `schema_version`
- omitted fields remain unchanged
- patching `governance.policy_refs` is never an in-place update; callers must use rollover instead
- the request must not include step-level execution constraints

Response:

- returns `200 OK` with the updated `AnalysisSession`

## Terminate Session

### `POST /sessions/{session_id}/terminate`

Explicitly commits a terminal lifecycle transition for an open session.

Request body:

```json
{
  "terminal_reason": "answered"
}
```

Allowed `terminal_reason` values:

| Terminal reason | Resulting status | Eligibility |
|-----------------|------------------|-------------|
| `answered` | `closed` | Always allowed for explicit termination |
| `abandoned` | `aborted` | Always allowed for explicit termination |
| `governance_terminated` | `aborted` | Allowed only if the session already has a governance termination signal |
| `budget_exhausted` | `aborted` | Allowed only if the session already has a budget exhaustion signal |
| `timed_out` | `aborted` | Allowed only if the session already has a timeout signal |

Termination rules:

- `terminate` is allowed only when `lifecycle.status = "open"`
- `rolled_over` is not a valid value on this endpoint; rollover has its own resource
- the endpoint records the committed terminal outcome only; it does not create or explain signal resources
- once committed:
  - `lifecycle.status` changes to `closed` or `aborted`
  - `lifecycle.terminal_reason` becomes fixed
  - `lifecycle.ended_at` becomes non-null
- terminal sessions are read-only after termination
- terminating a terminal session is an invalid state transition

Example:

```bash
curl -s -X POST "http://localhost:8000/sessions/sess_123/terminate" \
  -H "Content-Type: application/json" \
  -d '{"terminal_reason":"answered"}' | jq .
```

## Rollover Session

### `POST /sessions/{session_id}/rollover`

Creates a new session when immutable governance boundary values need to change, and closes the current session as rolled over.

Request body:

```json
{
  "goal": {
    "question": "Continue the watch time investigation under the revised policy set"
  },
  "governance": {
    "policy_refs": [
      {
        "policy_id": "pol_aggregate_only",
        "policy_version": "8"
      }
    ],
    "budget": {
      "max_steps": 10,
      "max_scan_bytes": 250000000000,
      "max_latency_sec": 90
    },
    "warnings": [
      "Policy version updated after governance review"
    ]
  }
}
```

Rollover response shape:

```json
{
  "previous_session": {
    "session_id": "sess_123",
    "goal": {
      "question": "Why did watch time decline last week?"
    },
    "governance": {
      "policy_refs": [
        {
          "policy_id": "pol_aggregate_only",
          "policy_version": "7"
        }
      ],
      "budget": {
        "max_steps": 15,
        "max_scan_bytes": 500000000000,
        "max_latency_sec": 120
      },
      "warnings": null
    },
    "lifecycle": {
      "status": "closed",
      "terminal_reason": "rolled_over",
      "ended_at": "2024-01-15T11:00:00+00:00",
      "rollover_from_session_id": null
    },
    "state_summary": {
      "state_view_ref": {
        "session_id": "sess_123",
        "view_type": "session_state_view"
      }
    },
    "created_at": "2024-01-15T10:30:00+00:00",
    "updated_at": "2024-01-15T11:00:00+00:00",
    "schema_version": "analysis_session.v1"
  },
  "current_session": {
    "session_id": "sess_456",
    "goal": {
      "question": "Continue the watch time investigation under the revised policy set"
    },
    "governance": {
      "policy_refs": [
        {
          "policy_id": "pol_aggregate_only",
          "policy_version": "8"
        }
      ],
      "budget": {
        "max_steps": 10,
        "max_scan_bytes": 250000000000,
        "max_latency_sec": 90
      },
      "warnings": [
        "Policy version updated after governance review"
      ]
    },
    "lifecycle": {
      "status": "open",
      "terminal_reason": null,
      "ended_at": null,
      "rollover_from_session_id": "sess_123"
    },
    "state_summary": {
      "state_view_ref": {
        "session_id": "sess_456",
        "view_type": "session_state_view"
      }
    },
    "created_at": "2024-01-15T11:00:00+00:00",
    "updated_at": "2024-01-15T11:00:00+00:00",
    "schema_version": "analysis_session.v1"
  }
}
```

Rollover rules:

- rollover is allowed only when `lifecycle.status = "open"`
- rollover is required when `governance.policy_refs` value changes
- the request must provide the full new session root values to use for the successor session
- the successor session may also change `goal.question`, `governance.budget`, and `governance.warnings`
- rollover closes the old session as:
  - `lifecycle.status = "closed"`
  - `lifecycle.terminal_reason = "rolled_over"`
  - `lifecycle.ended_at != null`
- the new session opens as:
  - `lifecycle.status = "open"`
  - `lifecycle.terminal_reason = null`
  - `lifecycle.ended_at = null`
  - `lifecycle.rollover_from_session_id = {old_session_id}`
- rollover is a container switch only; it does not merge or inherit canonical evidence objects from the previous session
- calling rollover without an actual `policy_refs` value change is invalid

## Session Lifecycle State Machine

The HTTP contract fixes the following lifecycle transitions:

- `open -> closed`
- `open -> aborted`

The following transitions are invalid:

- `closed -> open`
- `aborted -> open`
- `closed -> aborted`
- `aborted -> closed`
- any terminal-to-terminal rewrite

Terminal reason mapping is fixed as follows:

| Terminal reason | Target status | Allowed endpoint |
|-----------------|---------------|------------------|
| `answered` | `closed` | `POST /sessions/{id}/terminate` |
| `abandoned` | `aborted` | `POST /sessions/{id}/terminate` |
| `rolled_over` | `closed` | `POST /sessions/{id}/rollover` only |
| `governance_terminated` | `aborted` | `POST /sessions/{id}/terminate` |
| `budget_exhausted` | `aborted` | `POST /sessions/{id}/terminate` |
| `timed_out` | `aborted` | `POST /sessions/{id}/terminate` |

The session root does not expose intermediate states such as `closing`, `terminating`, or `pending_terminal`.

## Read/Write Guarantees

While `lifecycle.status = "open"`:

- the session root may be patched within mutability rules
- typed analysis steps may append canonical evidence objects under the same `session_id`
- lifecycle-related signals may exist outside the root lifecycle payload

After `lifecycle.status` becomes `closed` or `aborted`:

- session root writes are no longer allowed
- canonical evidence writes are no longer allowed
- `GET /sessions/{session_id}` remains valid
- the canonical read surfaces remain valid through:
  - `GET /sessions/{session_id}/state`
  - `GET /sessions/{session_id}/propositions/{proposition_id}/context`

## Errors

These endpoints use the standard error envelope from [`errors.md`](errors.md).

Common cases:

| Status | Scenario |
|--------|----------|
| `400` | invalid enum value, invalid query parameter value, unknown patch field, malformed `page_token` |
| `404` | session not found |
| `409` | invalid lifecycle transition, write attempted on terminal session, in-place update of immutable field, missing termination eligibility signal, rollover requested without `policy_refs` change |
| `422` | request body validation failure |
| `500` | unexpected server-side failure |

Error behavior is fixed as follows:

- attempting to patch `governance.policy_refs` returns `409`, not silent ignore
- attempting to set any `lifecycle.*` field through `PATCH` returns `409`
- `POST /terminate` with `terminal_reason = "rolled_over"` returns `400`
- `POST /terminate` with a signal-backed reason but without the required eligibility condition returns `409`
- `POST /rollover` on a terminal session returns `409`
- `GET /sessions` with `status=open` and any `terminal_reason` filter returns `200` with an empty result set, not a validation error
- an invalid or expired `page_token` on `GET /sessions` returns `400`

## Relationship To Session State And Context Surface

This document defines the session root lifecycle only.

It does not replace the canonical read surfaces defined in [`session-state.md`](session-state.md) and [`context-surface.md`](context-surface.md):

- `GET /sessions/{session_id}/state` remains the session-level decision surface
- `GET /sessions/{session_id}/propositions/{proposition_id}/context` remains the proposition-level minimal closure

`state_summary.state_view_ref` is the only canonical read entry embedded in the session root.

## Non-goals

This contract does not define:

- typed step execution endpoints
- signal resource schemas or signal read APIs
- evidence graph read surfaces
- compact summary projections
- async job semantics for termination or rollover
