# Session State Surface

This document defines the target-state external HTTP contract for Marivo's canonical session state surface.

It binds the canonical schema from [`spec/analysis/evidence-engine/schemas/state-surface-schema.md`](../specs/analysis/evidence-engine/schemas/state-surface-schema.md) to stable HTTP resources. This is a target-state wire specification and does not describe or depend on the current implementation.

## Purpose

Use these endpoints when a client needs:

- the session-level decision surface centered on live propositions

Do not use these endpoints as generic projection containers. They expose the canonical session-level read surface, not compact summaries or arbitrary `include_*` projections.

## Canonical Resources

| Surface | Endpoint | Canonical payload |
|--------|----------|-------------------|
| Session state surface | `GET /sessions/{session_id}/state` | `SessionStateView` plus transport paging metadata |
| Session state surface with structured filtering | `POST /sessions/{session_id}/state/query` | `SessionStateView` plus transport paging metadata |

`SessionStateView` remains the session-level default read baseline. For the proposition-level minimal closure, use [`context-surface.md`](context-surface.md).

This surface exposes externally visible canonical state only. It is not a runtime orchestration or backlog view.

## Session State Endpoints

### `GET /sessions/{session_id}/state`

Returns the default session state view. This is the primary read path for agent clients.

Supported query parameters:

| Parameter | Type | Notes |
|-----------|------|-------|
| `metric` | string | Matches `SessionStateQuery.metric` |
| `entity` | string | Matches `SessionStateQuery.entity` |
| `proposition_type` | repeated string | Repeated key form of `SessionStateQuery.proposition_types` |
| `origin_kind` | repeated string | Repeated key form of `SessionStateQuery.origin_kinds` |
| `assessment_presence` | string | `assessed` or `unassessed` |
| `assessment_status` | repeated string | Repeated key form of `SessionStateQuery.assessment_statuses` |
| `has_blocking_gaps` | boolean | Matches `SessionStateQuery.has_blocking_gaps` |
| `limit` | integer | Applies only to `active_propositions` |
| `page_token` | string | Opaque cursor for continuing the same normalized query |

`slice` is intentionally not supported on the `GET` endpoint. Any request that needs `slice` must use `POST /sessions/{session_id}/state/query`.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123/state?metric=watch_time&assessment_presence=assessed&has_blocking_gaps=true&limit=25" | jq .
```

### `POST /sessions/{session_id}/state/query`

Returns the session state view for a structured `SessionStateQuery`.

Use this endpoint when the caller needs:

- `slice`
- multi-axis filtering that is awkward in query strings
- a large or generated filter payload

Request body:

```json
{
  "metric": "watch_time",
  "entity": "video",
  "slice": {
    "country": "US",
    "device_type": "mobile"
  },
  "proposition_types": ["metric_status"],
  "origin_kinds": ["system_seeded"],
  "assessment_presence": "assessed",
  "assessment_statuses": ["insufficient"],
  "has_blocking_gaps": true,
  "limit": 25
}
```

`POST /state/query` accepts exactly the canonical `SessionStateQuery` fields in the request body. It does not introduce a parallel search DSL.

`page_token` is a transport concern and is **not** part of the request body. When cursor pagination is implemented it will be passed as a URL query parameter on both `GET /state` and `POST /state/query`.

### Session State Query Rules

The HTTP layer fixes the following behavior:

- `metric`, `entity`, `assessment_presence`, `has_blocking_gaps`, and `limit` have the same meaning on `GET /state` and `POST /state/query`
- `page_token` is a URL query parameter on both endpoints; it is not part of the `POST /state/query` request body
- repeated query parameters on `GET /state` normalize to the array fields used by `POST /state/query`
- `slice` is a proposition subject slice subset exact match
- `assessment_presence = "unassessed"` matches only entries where `latest_assessment = null`
- `assessment_presence = "assessed"` matches only entries where `latest_assessment != null`
- `assessment_statuses` match only `latest_assessment.status`
- `assessment_presence = "unassessed"` combined with any `assessment_statuses` is valid but returns an empty result set
- `has_blocking_gaps = true` matches only assessed entries with non-empty `blocking_gaps`
- `has_blocking_gaps = false` matches only assessed entries with `blocking_gaps = []`; it does not include unassessed propositions

## Session State Response Shape

The wire response is the canonical `SessionStateView` plus one transport field:

```json
{
  "session_id": "sess_123",
  "focus_subjects": [],
  "active_propositions": [],
  "backing_findings": [],
  "blocking_gaps": [],
  "artifact_refs": [],
  "truncation": {
    "is_truncated": false,
    "returned_count": 0,
    "total_count": 0,
    "sort_key": "default_active_proposition_order_v1",
    "applies_to": "active_propositions"
  },
  "schema_version": "session_state_view.v1",
  "next_page_token": null
}
```

Transport field semantics:

- `next_page_token` is an opaque HTTP continuation cursor
- `next_page_token` is `null` when there is no next page
- `next_page_token` is not part of canonical view identity and must not be persisted as a canonical ref

Canonical payload invariants still follow the analysis contracts:

- `truncation` describes truncation of `active_propositions`
- `focus_subjects`, `backing_findings`, `blocking_gaps`, and `artifact_refs` must shrink to the returned proposition closure when truncation is active
- `backing_findings`, `blocking_gaps`, and `artifact_refs` must never contain members belonging only to propositions excluded by paging
- the response must reflect only externally visible proposition-local bundles; it must not expose a partially refreshed combination such as a new `latest_assessment` with an old proposal closure
- runtime attempt, claim, retry, backlog, or migration-blocked status does not belong to this payload
- this surface must not expose semantic-ref-specific fields such as `metric_ref`, `process_ref`, `dimension_ref`, `time_ref`, `binding_ref`, or `semantic_ref`
- embedded canonical subject payloads may still carry typed semantic identifiers such as `metric.*` and `dimension.*` inside ordinary business fields like `subject_json.metric` or `subject_json.slice`
- `artifact_refs` remain canonical provenance handles only; they must not be expanded into semantic identity payloads
- semantic meaning may be recovered internally through step lineage metadata, but that recovery must not change this payload's canonical-only shape

## Session State Pagination

Session state paging uses cursor pagination.

Rules:

- paging applies only to `active_propositions`
- `limit` bounds the number of returned `active_propositions`
- `page_token` continues the exact same normalized query and sort order
- tokens are opaque to clients
- tokens are session-scoped and query-scoped; a token generated for one session or filter set is invalid for another
- `truncation.is_truncated = true` means the returned `active_propositions` page is partial, even if `truncation.total_count` is `null`
- `truncation.total_count = null` means total count was not computed; it does not mean the result is unbounded

The default `active_propositions` sort key is fixed as `default_active_proposition_order_v1`, which maps to the canonical ordering defined in [`spec/analysis/evidence-engine/schemas/state-surface-schema.md`](../specs/analysis/evidence-engine/schemas/state-surface-schema.md).

## Errors

These endpoints use the standard error envelope from [`errors.md`](errors.md).

Common cases:

| Status | Scenario |
|--------|----------|
| `400` | malformed `page_token`, `slice` passed to `GET /state`, invalid enum value, invalid boolean value |
| `404` | session not found |
| `422` | request body validation failure on `POST /state/query` |
| `500` | unexpected server-side failure while materializing the view |

Error behavior is fixed as follows:

- `assessment_presence = "unassessed"` combined with `assessment_statuses` returns `200` with an empty result, not a validation error
- an invalid or expired `page_token` returns `400`
- the service must not silently downgrade `slice` on `GET /state`; callers must move to `POST /state/query`

## Relationship To Context Surface

This document defines the session-level canonical read surface:

- `GET /sessions/{session_id}/state` for the session-level decision surface

If a caller needs to know whether recompute, proposal refresh, publish, or migration is still in progress, that must come from a separate operator-facing runtime status surface rather than from `SessionStateView`.

For the proposition-level minimal closure, use [`context-surface.md`](context-surface.md):

- `GET /sessions/{session_id}/propositions/{proposition_id}/context`

## Non-goals

This contract does not define:

- session root write APIs
- step execution APIs
- assessment history browsing
- compact or audit projection profiles
- runtime attempt or queue status
- cache headers or conditional request semantics
