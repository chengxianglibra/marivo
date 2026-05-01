# Semantic Layer Dual-Path Update Modes Design

Date: 2026-05-01

## Summary

Marivo semantic layer provides two strictly separated update paths: import for official objects, CRUD for private objects. This design defines the API update mechanisms, per-model versioning, cross-visibility referencing, session-level semantic snapshots, and visibility-gated enforcement.

## Dual-Path Update Model

### Path 1: Import Per-Model Update (Official Layer)

- `POST /semantic-models/import` — the only write path for official objects
- Semantics: **per-model upsert** — import only updates models present in the OSI document; other official models are unaffected
- Use case: enterprise semantic management, semantic JSON developed/reviewed/deployed through Git
- Constraints:
  - All models in imported document must be `visibility=public`
  - Each model is versioned independently with its own `revision` counter
  - If an official model with the same name already exists, it is updated (revision incremented); otherwise a new model is created (revision=1)
  - Models NOT in the import document are left unchanged

### Path 2: CRUD Partial Update (Private Layer)

- All CRUD endpoints (`POST /semantic-models`, `POST /semantic-models/{model}/datasets`, etc.) operate only on `visibility=private` objects
- Use case: Agent autonomously fills official gaps; personal data analysis exploration; validating new metric definitions
- Constraints:
  - Write operations (POST/PUT/DELETE) on official objects return 403
  - Read operations (GET) can read objects of any visibility
  - Creating a private object with the same name as an official object returns 409
  - Private objects must specify `owner_user`

### Invariant

Import and CRUD have disjoint write scopes. Official objects change only through import; private objects change only through CRUD.

## Per-Model Versioning

### Current Model (Global Version)

```
semantic_versions (version_id, created_at)  ← one row per import, global
  └── semantic_models (model_id, semantic_version_id FK, name, ...)
```

- Each import creates a new `semantic_versions` row
- All imported models share that `version_id`
- `list_semantic_models` filters by `semantic_version_id = <latest>` — only models from the most recent import are visible
- Consequence: importing model A replaces ALL official models (B, C, etc. become invisible)

### New Model (Per-Model Revision)

```
semantic_models (model_id, revision, name, visibility, ...)  ← one row per current model
```

- `semantic_models` is the **current state table** — one row per unique model
- Each model has a `revision` counter (default 1), incremented on import update
- **No separate history table** — official model definitions are in Git (the import source). The DB is the current state, not the version history.
- `list_semantic_models` returns all rows from `semantic_models` directly (no version filter)
- Audit and replay use session snapshots which record `(model_name, revision)` at freeze time
- `semantic_versions` table is **dropped** — per-model revision replaces global versioning entirely

### Schema Changes (Destructive)

Drop `semantic_versions` and rewrite `semantic_models` with `revision` column:

```sql
DROP TABLE IF EXISTS semantic_versions;

CREATE TABLE IF NOT EXISTS semantic_models (
    model_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    ai_context  TEXT,
    visibility  TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
    owner_user  TEXT,
    revision    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_semantic_models_visibility_owner
    ON semantic_models(visibility, owner_user);
```

Note: `semantic_version_id` FK column is removed from `semantic_models`. The `revision` column replaces it.

`semantic_readiness_status.evaluated_semantic_version_id` is also removed since it referenced the dropped table.

### Import Behavior Change

**Before (global version):**
1. Create new `semantic_versions` row
2. Insert all models with the new `version_id`
3. Old models become invisible (filtered out by version)

**After (per-model revision):**
1. For each model in the document:
   a. Check if official model with same name exists → if yes, UPDATE (increment revision, replace children)
   b. Check if private model with same name exists → if yes, INSERT the official model alongside it (both exist, official takes priority in resolution)
   c. If no model with that name exists → INSERT with `revision=1`
2. Models not in the document are untouched

No `semantic_versions` row is created. No history archive is needed — old definitions are in Git.

### List/Get Behavior Change

**Before:** `list_semantic_models` filters by `semantic_version_id = <latest>` to find current official models.

**After:** `list_semantic_models` returns all rows from `semantic_models` directly (no version filter). Since `semantic_models` always contains the current state, no filtering needed.

`get_semantic_model(name)` returns the current revision. Resolved semantic objects include the `revision` for audit traceability.

## Cross-Visibility Reference & Resolution

Private objects can reference official objects. Same-name rules are asymmetric.

### Same-Name Rules

- **Within the same visibility**: no same-name allowed
  - Public: no two public models with the same name (import upsert enforces this)
  - Private: no two private models with the same name per `owner_user`
- **Between visibilities**: same-name allowed — a public model and a private model can share the same name
- **Resolution**: when both exist, public takes priority

This means:
- CRUD creating a private model with a name that already exists as a private model for the same owner → 409
- CRUD creating a private model with a name that already exists as a public model → allowed
- Import creating a public model with a name that already exists as a private model → allowed
- When a public and private model share the same name, the public model wins in resolution

### Reference Rules

- Private dataset can reference official dataset as `source` (e.g., `source: "official://commerce/orders"`)
- Private metric can reference official dataset fields in `expression` (e.g., `dataset.orders.field.amount`)
- Private relationship can connect a private dataset to an official dataset

### Resolution Context

Each analysis session carries a resolution context:

```json
{
  "resolution_order": ["official", "private"]
}
```

Resolution logic:
1. Bare ref looked up by resolution_order — official first, then private
2. If a name exists in both official and private, official wins (consistent with the priority rule)
3. If a name exists only in private, it resolves to the private model
4. Cross-layer references (private metric referencing official dimension) handled transparently by the resolver at execution time

## Required Fields (Unified for Official and Private)

### Principle

Private and Official required fields are identical. Only these categories of fields are optional:
1. Descriptive metadata (`description`, `ai_context`, `label`)
2. Derivable from other fields (`observed_dataset`, `observation_grain` derivable from expression)

### Per-Object Required Fields

| Object | Required Fields | Optional Fields |
|--------|----------------|-----------------|
| SemanticModel | name, datasets (min 1), visibility, owner_user (if private) | description, ai_context, relationships, metrics |
| Dataset | name, source, primary_key, fields | description, ai_context, unique_keys |
| Field | name, expression, data_type | description, ai_context, is_time, label |
| Relationship | name, from, to, from_columns, to_columns, cardinality | ai_context |
| Metric | name, expression, additivity | description, ai_context, observed_dataset, primary_time_field, filters |

### Difference from Official

- `visibility` + `owner_user` are required only for Private (Official is forced `public`, no owner needed)
- `description`, `ai_context` are optional in both paths
- `observed_dataset`, `observation_grain`, `primary_time_field`, `filters` are optional (derivable from expression and context)

### Agent Inference Responsibility

- Agent must provide all required fields; missing required fields return 400
- `data_type`, `additivity`, `cardinality` etc. are inferred by Agent from table metadata, sample data, and metric semantics
- No system-level default values are provided by Marivo

## Session-Level Semantic Snapshot

### Core Principle

An entire analysis session uses the same semantic snapshot, ensuring no semantic drift during analysis.

### Snapshot Mechanism

1. **Freeze on session creation**: When an analysis session is created, freeze the current visible semantic objects with their revision
2. **Immutable during session**: All subsequent queries use the frozen revision, unaffected by import/CRUD changes
3. **Persist after session ends**: Used for audit and replay

```json
{
  "session_id": "sess_456",
  "snapshot_frozen_at": "2026-05-01T10:00:00Z",
  "resolved_objects": [
    {
      "ref": "metric.gmv",
      "visibility": "official",
      "model_name": "commerce",
      "revision": 3
    },
    {
      "ref": "metric.gmv_channel_adjusted",
      "visibility": "private",
      "owner_user": "alice",
      "model_name": "alice_explore",
      "revision": 1
    }
  ]
}
```

### Anti-Drift Rules

- After session creation, even if a new import updates official models, the current session continues using the revision captured at freeze time
- New private objects created via CRUD within the session are added to the current snapshot (existing entries unchanged)
- Before query execution: all refs must resolve to objects in the snapshot; if not found, return `unresolved_ref` error
- After session ends or times out, snapshot is persisted for audit

### Per-Model Revision in Snapshot

The snapshot references each model by `(model_name, revision)`. This is sufficient for audit and replay — the combination identifies exactly which version was used. Old model definitions are in Git (the authoritative source for official models), not in the DB.

## Visibility-Gated CRUD Enforcement

### Write Operation Protection

Each CRUD endpoint write operation (POST/PUT/DELETE) adds a visibility check:

| Operation | Check Logic |
|-----------|-------------|
| `POST /semantic-models` | Force `visibility=private`, reject `public` |
| `PUT /semantic-models/{model}` | Query object visibility, return 403 for `public` |
| `DELETE /semantic-models/{model}` | Query object visibility, return 403 for `public` |
| `POST /semantic-models/{model}/datasets` | Parent model must be private, otherwise 403 |
| `PUT/DELETE /semantic-models/{model}/datasets/{name}` | Parent model must be private |
| Same for relationships, metrics | Same pattern |

### Read Operations Unrestricted

- `GET /semantic-models` returns all visible objects (official + current user's private)
- `GET /semantic-models/{model}` can read objects of any visibility
- This ensures private objects can reference and read official object definitions

### Same-Name Validation

- When creating a private model via CRUD, validate that no private model with the same name exists for the same `owner_user` → return 409
- When creating a private model via CRUD, same name as an existing public model is allowed
- When importing an official model, same name as an existing private model is allowed

### Implementation Scope

- Add visibility guard at the start of each write method in `SemanticModelV2Service`
- Add same-name validation in create methods
- Read methods keep existing visibility + owner_user filtering
- No new endpoints or route modifications needed
