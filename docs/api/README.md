# Factum API Reference

Factum is an **agentic analytics system** that provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and structured findings for AI agents and human analysts.

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
| `sync_` | Sync job |
| `ent_` | Semantic entity |
| `met_` | Semantic metric |
| `map_` | Semantic mapping |
| `pol_` | Policy |
| `qr_` | Quality rule |
| `job_` | Async job |
| `apr_` | Approval request |
| `evt_` | Governance event |

### Timestamps

All timestamps are ISO 8601 strings in UTC (e.g., `"2024-01-15T10:30:00+00:00"`).

### JSON Columns

Fields that store structured data are represented as JSON objects in responses. In the database they are stored as TEXT with a `_json` suffix, but the API serializes them as native objects.

### Lifecycle Status Values

| Resource | Status values |
|----------|---------------|
| Session | `active`, `completed`, `abandoned` |
| Plan | `draft` → `validated` → `approved` → `executing` → `completed` / `failed` |
| Semantic entity / metric | `draft` → `published` → `deprecated` |
| Job | `pending` → `running` → `completed` / `failed` / `cancelled` |
| Approval request | `pending` → `approved` / `rejected` |
| Sync job | `pending` → `running` → `completed` / `failed` |

## API Domains

| Domain | Path prefix | Description |
|--------|-------------|-------------|
| [Sessions & Steps](sessions.md) | `/sessions` | Stateful analysis sessions and typed step execution |
| [Planning](planning.md) | `/sessions/{id}/plans` | Multi-step analysis plans with validation and execution |
| [Sources](sources.md) | `/sources` | Data source registration and catalog sync |
| [Engines & Bindings](engines.md) | `/engines`, `/bindings` | Analytics engine registration and source-engine routing |
| [Semantic Layer](semantic.md) | `/semantic` | Entities, metrics, mappings, and catalog search |
| [Governance](governance.md) | `/policies`, `/quality-rules`, `/governance` | Data policies and quality rules |
| [Jobs](jobs.md) | `/jobs` | Async job submission and tracking |
| [Approvals](approvals.md) | `/approvals` | Approval workflow for high-risk recommendations |
| [Health & Observability](observability.md) | `/health`, `/metrics` | Service health and operational metrics |

## Additional Guides

- [Quickstart](quickstart.md) — end-to-end walkthrough with `curl` examples
- [Error Reference](errors.md) — HTTP status codes, error formats, and common error scenarios

## Core Concepts

### Sessions

A **session** is a stateful analysis context with a goal, constraints, budget, and policy. All analysis work — steps, evidence, plans — belongs to a session.

```json
{
  "session_id": "sess_abc123...",
  "goal": "Investigate watch time drop in Q1",
  "constraints": {"region": "US"},
  "budget": {"max_scan_bytes": 500000000000, "max_latency_sec": 120},
  "policy": {"aggregate_only": true, "min_group_size": 100}
}
```

### Steps

A **step** is a typed analysis operation executed within a session. Step types:

| Step type | Category | Description |
|-----------|----------|-------------|
| `compare_metric` | Primitive | Compare a semantic metric between two time windows |
| `profile_table` | Primitive | Profile row count and column-level completeness/cardinality |
| `sample_rows` | Primitive | Return a bounded sample of rows |
| `aggregate_query` | Primitive | Ad-hoc GROUP BY aggregation |
| `synthesize_findings` | Composite | Synthesize observations into claims and recommendations |

Session constraints are automatically injected into `compare_metric`, `sample_rows`, and `aggregate_query` WHERE clauses.

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

### Source-Engine Model

```
Source (external data catalog)
  └─ Source objects (synced schema/table/column snapshots)
  └─ Binding → Engine (DuckDB / Trino)
```

The **QueryRouter** resolves table names at step execution time: table name → source object → binding → engine.
