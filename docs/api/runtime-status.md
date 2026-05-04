# Runtime Status Surface

This document defines the target-state external HTTP contract for Marivo's operator-facing runtime status surface.

It binds the design in [`spec/analysis/evidence-engine/runtime-status-surface.md`](../specs/analysis/evidence-engine/runtime-status-surface.md) to stable HTTP resources. This is a target-state wire specification and does not describe or depend on the current implementation.

## Purpose

Use these endpoints when an operator needs to know:

- whether a session, artifact, or proposition is queued, running, blocked, failed, or externally visible
- which runtime stage most recently succeeded
- whether a missing canonical result reflects "not triggered yet", "still queued", "failed", or "waiting for publish"
- whether backlog, claim conflict, retry exhaustion, policy gates, or migration gates are preventing progress

Do not use these endpoints as:

- canonical evidence read surfaces
- assessment history browsers
- generic queue inspection feeds
- replacements for service-wide health or metrics endpoints

## Runtime Resources

| Surface | Endpoint | Payload |
|---------|----------|---------|
| Session runtime status | `GET /sessions/{session_id}/runtime-status` | `SessionRuntimeStatus` |
| Artifact runtime status | `GET /sessions/{session_id}/artifacts/{artifact_id}/runtime-status` | `ArtifactRuntimeStatus` |
| Proposition runtime status | `GET /sessions/{session_id}/propositions/{proposition_id}/runtime-status` | `PropositionRuntimeStatus` |

These resources expose runtime truth only. They must not be treated as the canonical read baseline for agents.

## Endpoints

### `GET /sessions/{session_id}/runtime-status`

Returns the operator-facing runtime status for the session as a whole.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123/runtime-status" | jq .
```

### `GET /sessions/{session_id}/artifacts/{artifact_id}/runtime-status`

Returns the operator-facing runtime status for a single artifact within the session.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123/artifacts/art_456/runtime-status" | jq .
```

### `GET /sessions/{session_id}/propositions/{proposition_id}/runtime-status`

Returns the operator-facing runtime status for a single proposition within the session.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123/propositions/prop_789/runtime-status" | jq .
```

## Response Shapes

These endpoints return runtime payloads directly. They do not add paging metadata, projection metadata, or transport-only continuation tokens.

### `SessionRuntimeStatus`

```json
{
  "session_id": "sess_123",
  "overall_status": "blocked",
  "last_successful_stage": "assessment_recompute",
  "blocked_reason": "backpressure",
  "backlog_summary": {
    "queued_artifacts": 2,
    "queued_propositions": 11,
    "backpressured_propositions": 4,
    "failed_items": 1
  },
  "updated_at": "2026-04-01T16:00:00+00:00",
  "schema_version": "session_runtime_status.v1"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | string | Requested session identifier |
| `overall_status` | string | `idle`, `running`, `blocked`, or `degraded` |
| `last_successful_stage` | string | `artifact_commit`, `finding_extraction`, `proposition_seeding`, `assessment_recompute`, `proposal_refresh`, or `publish` |
| `blocked_reason` | string | `none`, `backpressure`, `claim_conflict`, `dependency_wait`, `retry_exhausted`, `migration_required`, or `policy_blocked` |
| `backlog_summary` | object | Aggregated runtime queue summary for the session |
| `updated_at` | string | ISO 8601 timestamp; reflects the session row's `updated_at` in v1 |
| `schema_version` | string | Fixed as `session_runtime_status.v1` |

**v1 constraints:** `overall_status` only emits `idle` or `running`. `blocked` and `degraded` are reserved for a future version with real queue/lease tracking. `blocked_reason` is always `none`. `backpressured_propositions` and `failed_items` in `backlog_summary` are always `0`. `queued_artifacts` excludes D4-allows-empty artifact types (`observation`, `anomaly_candidates`).

### `ArtifactRuntimeStatus`

```json
{
  "session_id": "sess_123",
  "artifact_id": "art_456",
  "artifact_stage": "seeding_handoff_pending",
  "extractor_key": {
    "artifact_type": "observe_result",
    "artifact_schema_version": "v1",
    "extractor_version": "observe_extractor@3"
  },
  "correlation_id": "corr_18d5f4f6",
  "attempt_id": "att_9ab1e9d2",
  "last_failure_reason": null,
  "last_failure_at": null,
  "schema_version": "artifact_runtime_status.v1"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | string | Requested session identifier |
| `artifact_id` | string | Requested artifact identifier |
| `artifact_stage` | string | `staged`, `extracting`, `findings_committed`, `seeding_handoff_pending`, or `failed` |
| `extractor_key` | object | Runtime extractor dispatch identity |
| `correlation_id` | string | Stable lineage handle for the runtime work item |
| `attempt_id` | string or `null` | Current or last relevant attempt identifier |
| `last_failure_reason` | string or `null` | Machine-readable failure code when the latest artifact-level work failed |
| `last_failure_at` | string or `null` | ISO 8601 UTC timestamp of the most recent artifact-level failure |
| `schema_version` | string | Fixed as `artifact_runtime_status.v1` |

### `PropositionRuntimeStatus`

```json
{
  "session_id": "sess_123",
  "proposition_id": "prop_789",
  "current_stage": "proposal_refresh",
  "last_successful_stage": "assessment_committed",
  "current_assessment_id": "asm_222",
  "current_attempt": {
    "correlation_id": "corr_e2b73ca1",
    "attempt_id": "att_4d02baf1",
    "claim_owner": "worker-a",
    "claimed_at": "2026-04-01T15:59:00+00:00",
    "lease_expires_at": "2026-04-01T16:04:00+00:00"
  },
  "backlog_state": "queued",
  "last_failure_reason": "none",
  "last_failure_at": null,
  "schema_version": "proposition_runtime_status.v1"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | string | Requested session identifier |
| `proposition_id` | string | Requested proposition identifier |
| `current_stage` | string | `queued`, `assessment_recompute`, `assessment_committed`, `proposal_refresh`, `publish_ready`, `externally_visible`, or `failed` |
| `last_successful_stage` | string | `assessment_recompute`, `assessment_committed`, `proposal_refresh`, or `publish` |
| `current_assessment_id` | string or `null` | Latest committed assessment snapshot identifier when one exists |
| `current_attempt` | object or `null` | Current runtime attempt reference |
| `backlog_state` | string | `none`, `queued`, or `backpressured` |
| `last_failure_reason` | string | `none`, `claim_lost`, `retry_exhausted`, `rule_execution_failed`, `proposal_materialization_failed`, `publish_switch_failed`, `migration_mismatch`, or `dependency_missing` |
| `last_failure_at` | string or `null` | ISO 8601 UTC timestamp of the most recent proposition-level failure |
| `schema_version` | string | Fixed as `proposition_runtime_status.v1` |

**v1 constraints (proposition-level):** `current_stage` is derived entirely from committed canonical DB state — no real queue, claim, lease, or retry records are maintained. Derivation rules: no assessment committed → `"queued"`; assessment committed but no proposals → `"assessment_committed"`; assessment and proposals exist but no publish switch → `"publish_ready"`; publish switch executed → `"externally_visible"`. The `"assessment_recompute"`, `"proposal_refresh"`, and `"failed"` stage values are reserved for a future version with in-flight state tracking. Once `"externally_visible"`, the stage remains stable until `execute_publish_switch` fires again for a later assessment; a newer unpublished assessment does not change the reported stage. `current_attempt` is always `null`. `backlog_state` is always `"none"`. `last_failure_reason` is always `"none"` and `last_failure_at` is always `null`.

### Shared Nested Objects

`RuntimeBacklogSummary`:

```json
{
  "queued_artifacts": 2,
  "queued_propositions": 11,
  "backpressured_propositions": 4,
  "failed_items": 1
}
```

`RuntimeAttemptRef`:

```json
{
  "correlation_id": "corr_e2b73ca1",
  "attempt_id": "att_4d02baf1",
  "claim_owner": "worker-a",
  "claimed_at": "2026-04-01T15:59:00+00:00",
  "lease_expires_at": "2026-04-01T16:04:00+00:00"
}
```

## Wire Semantics

### Runtime Truth Versus Canonical Truth

These payloads answer runtime orchestration questions only.

They may report:

- queued versus claimed versus backpressured work
- last successful runtime stage
- failure family and failure timestamp
- whether publish is pending after a committed assessment

They must not directly claim:

- what the proposition conclusion is
- whether the proposition is supported or opposed
- which findings are in the canonical live closure
- what the current session decision surface contains

Those questions belong to [`session-state.md`](session-state.md) and [`context-surface.md`](context-surface.md).

### `latest_assessment = null` Boundary

Runtime status exists partly to explain canonical ambiguity.

If `GET /sessions/{session_id}/propositions/{proposition_id}/context` returns `latest_assessment = null`, the runtime status endpoint may distinguish among:

- not triggered yet
- queued but not claimed yet
- running assessment recompute
- failed before commit
- blocked by policy, dependency, or migration gates

The canonical context endpoint must not be retrofitted to carry those runtime details.

### Stage Semantics

The wire contract fixes the following meanings:

- `queued`: runtime work item exists but has not obtained an execution claim
- `assessment_committed`: the new assessment snapshot is committed, but proposal refresh or publish may still be incomplete
- `publish_ready`: the proposition-local bundle is fully materialized and is only waiting for the externally visible publish switch
- `externally_visible`: the latest proposition-local bundle is now aligned with the canonical read surfaces

`failed` always means the latest known runtime attempt for the resource ended unsuccessfully. It does not imply the canonical read surface is empty; an older externally visible bundle may still exist.

### Failure And Blockage Visibility

Failure and blockage fields are machine-readable by contract.

- `blocked_reason` and `last_failure_reason` must not degrade to free-text-only status
- free-text explanations may be added in the future as supplementary fields, but not instead of reason codes
- if a resource is delayed by backlog, claim contention, migration, dependency, or policy gates, that state must be explicit rather than inferred from stale timestamps

### Attempt Lineage

Attempt lineage is part of the stable wire contract for artifact and proposition runtime status.

- `correlation_id` tracks the work item across retries
- `attempt_id` identifies the current or most recent execution attempt
- `claim_owner`, `claimed_at`, and `lease_expires_at` expose operator-relevant lease state only through `RuntimeAttemptRef`

## Unsupported Query Controls

These endpoints do not support:

- `include_*`
- `profile`
- `limit`
- `page_token`
- history browsing controls
- bulk filtering or search bodies

Clients must treat these resources as fixed point reads by path identity.

## Errors

These endpoints use the standard error envelope from [`errors.md`](errors.md).

Common cases:

| Status | Scenario |
|--------|----------|
| `400` | malformed path parameter or unsupported query parameter |
| `404` | session not found, artifact not found, proposition not found, or resource does not belong to the requested session |
| `500` | unexpected server-side failure while materializing runtime status |

Error behavior is fixed as follows:

- an artifact or proposition outside the requested session returns `404`
- unsupported projection or paging parameters return `400`
- the service must not silently coerce runtime status into canonical read payloads or vice versa

## Relationship To Canonical Read Surfaces And Observability

Use runtime status together with, not instead of, the canonical read surfaces:

- [`session-state.md`](session-state.md) answers what is externally visible at the session level
- [`context-surface.md`](context-surface.md) answers the proposition-level canonical basis
- [`observability.md`](observability.md) answers service health and process-wide metrics

This document is the missing operator-facing layer between canonical evidence state and service-wide health telemetry.

## Non-goals

This contract does not define:

- canonical evidence object schemas
- session root write APIs
- bulk runtime search or fleet-wide queue inspection
- worker deployment topology or queue middleware selection
- UI copy or incident-report narrative formats
- cache headers or conditional request semantics
