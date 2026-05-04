# Marivo API Reference

Marivo is an **agentic analytics system** that provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and structured findings for AI agents and human analysts.

## Base URL

```
http://localhost:8000
```

## Authentication

Currently unauthenticated. Auth/RBAC is on the roadmap for production deployments.

## Content Type

All request and response bodies use `application/json`.

## Conventions

### ID Format

All resource IDs follow the pattern `{prefix}_{12-char hex}`:

| Prefix | Resource |
|--------|----------|
| `sess_` | Session |
| `step_` | Step |
| `art_` | Artifact |
| `obs_` | Observation |
| `claim_` | Claim |
| `rec_` | Recommendation |
| `edge_` | Evidence edge |
| `plan_` | Plan |
| `src_` | Source |
| `eng_` | Engine |
| `bind_` | Source-engine binding |
| `obj_` | Source object (synced catalog item) |
| `sel_` | Sync selection |
| `ent_` | Semantic entity |
| `met_` | Semantic metric |
| `map_` | Semantic mapping |
| `pol_` | Policy |
| `qr_` | Quality rule |
| `evt_` | Governance event |

### Timestamps

All timestamps are ISO 8601 strings in UTC (e.g., `"2024-01-15T10:30:00+00:00"`).

### JSON Columns

Fields that store structured data are represented as JSON objects in responses. In the database they are stored as TEXT with a `_json` suffix, but the API serializes them as native objects.

### Lifecycle Status Values

| Resource | Status values |
|----------|---------------|
| Session | `open`, `closed`, `aborted` |
| Semantic objects | Storage `status`: `draft` → `published` → `deprecated`; public `lifecycle_status`: `draft` → `active` → `deprecated`; public `readiness_status`: `not_ready` / `ready` / `stale` |

For the semantic layer, callers must treat `status` as a storage compatibility field only.
Runtime/catalog availability is gated by `lifecycle_status` and `readiness_status`, so
`status=published` does not imply the object is ready for default resolution or execution.

## API Domains

| Domain | Path prefix | Description |
|--------|-------------|-------------|
| [Session Lifecycle](session-lifecycle.md) | `/sessions` | Session root lifecycle: create, read, list, patch, terminate, and rollover |
| [Intent Step Submission](intent-steps.md) | `/sessions/{id}/intents/*` | Target-state per-intent step submission for atomic and derived analysis intents |
| [Session State Surface](session-state.md) | `/sessions/{id}/state` | Canonical session-level decision surface |
| [Context Surface](context-surface.md) | `/sessions/{id}/propositions/{pid}/context` | Canonical proposition-level minimal closure |
| [Runtime Status Surface](runtime-status.md) | `/sessions/{id}/**/runtime-status` | Operator-facing runtime stage, attempt, failure, and backlog status |
| [Progressive OpenAPI Access](openapi.md) | `/openapi/*`, `/openapi.json` | Progressive machine-readable contract retrieval derived from the canonical OpenAPI schema |
| [Datasources](sources.md) | `/datasources` | Datasource registration, live browse, and preview |
| [Engines](engines.md) | `/engines` | Analytics engine registration and execution capability surfaces |
| [Mappings](mappings.md) | `/mappings` | Operator-managed authority-to-execution projection for source-engine routing |
| [Semantic Layer](semantic.md) | `/semantic-models` | OSI semantic models with dataset-native physical grounding |
| [Governance](governance.md) | `/policies`, `/quality-rules`, `/governance` | Data policies and quality rules |
| [Health & Observability](observability.md) | `/health`, `/metrics` | Service health and operational metrics |

## Additional Guides

- [Session Lifecycle](session-lifecycle.md) — canonical session root lifecycle HTTP contract
- [Intent Step Submission](intent-steps.md) — target-state per-intent write contract for atomic and derived analysis intents
- [Session State Surface](session-state.md) — canonical session-level decision surface HTTP contract
- [Context Surface](context-surface.md) — canonical proposition-level minimal closure HTTP contract
- [Runtime Status Surface](runtime-status.md) — operator-facing runtime stage and failure HTTP contract
- [Progressive OpenAPI Access](openapi.md) — path- and schema-focused contract retrieval over the canonical OpenAPI document
- [Quickstart](quickstart.md) — end-to-end walkthrough with `curl` examples
- [Error Reference](errors.md) — HTTP status codes, error formats, and common error scenarios

Target-state step submission wire contracts live under `docs/api/`. Non-HTTP analysis-intent design drafts live under `specs/analysis/`.

## Core Concepts

### Sessions

A **session** is the root analysis container. It carries descriptive task context, governance boundaries, lifecycle state, and the entry to the canonical session state surface. All analysis work — steps, evidence, plans — belongs to a session.

```json
{
  "session_id": "sess_abc123",
  "goal": {
    "question": "Investigate watch time drop in Q1"
  },
  "governance": {
    "policy_refs": [
      {
        "policy_id": "pol_aggregate_only",
        "policy_version": "7"
      }
    ],
    "budget": {
      "max_scan_bytes": 500000000000,
      "max_latency_sec": 120
    },
    "warnings": null
  },
  "lifecycle": {
    "status": "open",
    "terminal_reason": null,
    "ended_at": null,
    "rollover_from_session_id": null
  }
}
```

### Steps

A **step** is a typed analysis operation executed within a session. The target-state submission surface is defined in [Intent Step Submission](intent-steps.md). Target-state step families are:

| Step type | Category | Description |
|-----------|----------|-------------|
| `observe` | Atomic | Read a semantic metric as a scalar, time series, segmented observation, or inferential-ready sample summary |
| `compare` | Atomic | Compute a typed delta between two compatible observations |
| `decompose` | Atomic | Allocate a scalar delta across a semantic dimension using a typed attribution method |
| `correlate` | Atomic | Estimate association between two aligned time-series observations |
| `detect` | Atomic | Scan a bounded time range and return ranked anomaly candidates |
| `test` | Atomic | Execute a typed statistical hypothesis test on inferential-ready observations |
| `forecast` | Atomic | Project a bounded time-series observation into future buckets |
| `attribute` | Derived | Expand `observe -> compare -> decompose` into a deterministic attribution bundle |
| `diagnose` | Derived | Expand `detect -> compare -> decompose` into a deterministic diagnosis bundle |
| `validate` | Derived | Expand `observe -> observe -> test` into a deterministic validation bundle |

Step-level analysis constraints belong in typed step requests such as `scope`, `time_scope`, and typed refs; the session root does not carry canonical execution scope.

### Evidence Graph

Each session accumulates a structured evidence graph:

```
Artifacts → Observations → Claims → Recommendations
                 ↕ (evidence edges)
```

- **Artifact** — raw step output (comparison table, aggregated rows)
- **Observation** — typed factual finding extracted from an artifact (e.g., "metric down 14.2% for slice X")
- **Claim** — synthesized conclusion supported or contradicted by observations
- **Evidence edge** — typed relationship: `supports`, `contradicts`, `justifies`
- **Recommendation** — action proposal backed by claims, with priority, risk, and validation metric

### Datasource-Grounding Model

```
Datasource (external data catalog)
  └─ Live browse / preview
  └─ OSI Dataset + Field grounding
```

Marivo persists physical grounding in the semantic model: `dataset.datasource_id`, `dataset.source`,
and `field.expression`. Live browse helps authors choose those values; it is not a persisted catalog
snapshot.
