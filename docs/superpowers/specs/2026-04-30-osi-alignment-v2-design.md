---
status: completed
canonical-path: spec/semantic/ (pending update for dataset-native OSI alignment)
created: 2026-04-30
---

# OSI Alignment V2: Marivo Semantic Layer Rewrite

**Date:** 2026-04-30
**Status:** Draft (incorporating review feedback)
**Approach:** Clean rewrite aligned to OSI Core Metadata Spec v0.1.1

## 1. Goals

1. Replace Marivo's semantic model with OSI-aligned objects (SemanticModel, Dataset, Field, Relationship, Metric)
2. Delete unused objects (Process, EnumSet, Predicate, Binding, CompatibilityProfile) and collapse Dimension/Time into Field
3. Three-layer boundary: OSI external contract → MARIVO extension schema → internal storage/implementation
4. Preserve harness safety (write-time validation, readiness, semantic layer versioning) in MARIVO extension layer

## 2. Three-Layer Boundary

The design has three distinct layers with clear contracts between them:

### Layer 1: OSI External Contract

The wire format for API input/output. Must pass OSI JSON schema validation (`additionalProperties: false`). All MARIVO-specific data lives in `custom_extensions` with `vendor_name: "MARIVO"`. Any tool that understands OSI can consume this format without knowing Marivo.

Top-level structure follows OSI exactly:
```json
{
  "version": "0.1.1",
  "semantic_model": [{ ... }]
}
```

`version` is a **document-level** property, not per-SemanticModel. It indicates which OSI spec version this document conforms to.

### Layer 2: MARIVO Extension Schema

Defines the structure of `custom_extensions[].data` when `vendor_name: "MARIVO"`. These are the fields that Marivo needs beyond OSI core for safe analysis. The extension schema is versioned alongside the OSI spec version.

Machine-readable schema: [`osi-marivo-schema.json`](../../api/osi-marivo-schema.json).

Extension fields are only added when they serve a harness purpose: preventing silent wrong numbers, enabling validation, or providing explicit references that cannot be safely inferred from SQL expressions.

### Layer 3: Internal Storage/Implementation

Storage is organized for efficient queries, not OSI structure. Tables may differ from the OSI object model. The service layer handles the mapping between OSI wire format and storage.

## 3. Design Principles

### What vs How Boundary

| Criterion | Decision | Example |
|---|---|---|
| Wrong inference -> silent wrong number | **Keep as MARIVO extension** | `additivity`, `observed_dataset` |
| Wrong inference -> error (not wrong result) | **Delegate to planner** | join behavior (INNER vs LEFT) |
| No runtime code consumes the field | **Delete** | `stable_descriptors`, `metric_family` |
| Cannot be safely inferred from SQL expression | **Keep as MARIVO extension** | `data_type`, `observation_grain` |

### Decided Removals

**Object types deleted:** Process Object, EnumSet, Predicate, Binding, Compatibility Profile

**Object types collapsed:** Dimension -> Field property, Time -> Field property (is_time)

**Lifecycle ceremony deleted:** draft→validate→activate→publish→deprecate flow removed. Objects are created/updated/deleted directly.

**Harness preserved:** Write-time validation, readiness assessment, and semantic layer versioning remain as MARIVO extension concerns (see Section 7).

### OSI Spec Conformance

- API input/output IS an OSI document. The request/response body must pass OSI JSON schema validation.
- MARIVO-specific data lives ONLY in `custom_extensions` with `vendor_name: "MARIVO"`.
- The `/export` endpoint is redundant — the standard GET endpoints already return OSI-conformant format.
- Internal storage is organized for efficient queries; the service layer maps between OSI and storage.

## 4. Object Model

### 4.1 SemanticModel

Top-level container. Maps from current Domain.

```
SemanticModel (OSI core)
  name              string   (required)
  description       string   (optional)
  ai_context        AIContext (optional) string or {instructions, synonyms, examples}
  datasets[]        Dataset  (required, min 1)
  relationships[]   Relationship (optional)
  metrics[]         Metric   (optional)

MARIVO extensions (in custom_extensions):
  visibility        string   public/private
  owner_user        string   required when visibility=private
```

The `version` field is document-level (top-level alongside `semantic_model`), not inside SemanticModel. See Section 6 for document structure.

**Public vs private models:** `public` models are business-domain semantic-layer objects curated by business experts and visible/usable by all users after publication. `private` models are temporary analysis-time semantic-layer objects created by analysts or agents, visible/usable only to the owning user. This is a visibility contract, not a lifecycle state machine: publishing a model means updating `visibility` to `public`, not reintroducing draft/validate/activate/publish stages.

`owner_user` uses the same username-style user identity boundary as analysis sessions. It is required for `private` models and is used for visibility filtering. This does not introduce full authentication, delegation, or role management into the semantic model contract.

### 4.2 Dataset

Maps from current Entity. Direct physical grounding (no separate Binding).

```
Dataset (OSI core)
  name              string   (required)
  source            string   (required) Physical table/view reference: database.schema.table or SQL query
  primary_key       string[] (optional)
  unique_keys       string[][] (optional)
  description       string   (optional)
  ai_context        AIContext (optional)
  fields[]          Field    (optional in OSI, required by MARIVO validation)

MARIVO extensions (in custom_extensions):
  datasource_id     string   (optional) Marivo datasource reference for routing, readiness, and schema resolution
```

**Source vs datasource_id:** `Dataset.source` follows OSI spec as a physical reference (`database.schema.table` or query). The Marivo `datasource_id` extension links the dataset to Marivo's datasource abstraction, which provides routing, readiness checks, and multi-source support. When `datasource_id` is set, the service resolves `source` against the registered datasource's schema. When absent, `source` is used as-is.

### 4.3 Field

Collapses current EntityField + Dimension + Time into one concept.

```
Field (OSI core)
  name              string   (required) Unique within dataset
  expression        Expression (required) OSI multi-dialect expression
                      dialects[]:
                        dialect    string   (required, default "ANSI_SQL")
                        expression string   (required) SQL scalar expression or column reference
  dimension         object   (optional)
    is_time         boolean  (default false)
  label             string   (optional)
  description       string   (optional)
  ai_context        AIContext (optional)

MARIVO extensions (in custom_extensions):
  data_type         string   (optional) string/integer/number/boolean/date/datetime
```

**Rationale for data_type:** No type information exists in OSI. Type is safety-critical for SQL compilation, validation, and correct function selection. Cannot be reliably inferred from SQL expressions across dialects.

### 4.4 Relationship

Maps from current EntityRelationship. Semantic key/cardinality declarations only.

```
Relationship (OSI core)
  name              string   (required) Unique within model
  from              string   (required) Dataset name (many side)
  to                string   (required) Dataset name (one side)
  from_columns      string[] (required) FK columns in the "from" dataset
  to_columns        string[] (required) PK/unique columns in the "to" dataset
  ai_context        AIContext (optional)

MARIVO extensions (in custom_extensions):
  cardinality       string   (optional) many_to_one/one_to_one
```

**No join_type in Relationship.** Semi/anti joins are not stable entity relationships — they are query-scoped filter/planner policies that change metric populations. INNER vs LEFT behavior is also planner policy: the relationship declares that two datasets are connected via specific columns with a known cardinality; how the planner uses that connection (row-preserving vs row-filtering) depends on the query context.

**Rationale for cardinality:** Semantic property of the relationship. Affects how the planner interprets join direction and result granularity. Wrong cardinality assumption can produce wrong grain results.

### 4.5 Metric

Flat expression model with explicit harness extensions for safety-critical metadata.

```
Metric (OSI core)
  name              string   (required)
  expression        Expression (required) OSI multi-dialect expression
  description       string   (optional)
  ai_context        AIContext (optional)

MARIVO extensions (in custom_extensions):
  observed_dataset  string   (optional) Primary dataset this metric is computed from
  observation_grain string[] (optional) Field names defining the metric's observation grain
  primary_time_field string  (optional) Time field for this metric's time axis (overrides dataset default)
  additivity        object   (optional)
    dimension_policy    string   all/subset/none
    additive_dimensions string[] (required when subset)
    time_axis_policy    string   additive/non_additive
  filters           object[] (optional) Named filter expressions applied by default
    name            string   Filter identifier
    expression      Expression (OSI multi-dialect)
```

**Rationale for each extension:**

| Field | Why not inferable from expression |
|---|---|
| `observed_dataset` | A metric expression like `SUM(a.col) / COUNT(DISTINCT b.col)` references multiple datasets. The observed dataset (the one whose grain defines the metric's population) is not parseable from SQL. Wrong inference -> wrong grain -> wrong numbers. |
| `observation_grain` | The grain at which a metric is meaningful (e.g., per-user, per-order) cannot be determined from an aggregate expression alone. Wrong grain -> duplicate counting or missing rows. |
| `primary_time_field` | Different metrics on the same dataset may use different time axes (e.g., order_date vs ship_date). Not inferable from expression if the metric doesn't explicitly reference a time column. Wrong time axis -> wrong time-series results. |
| `additivity` | Safety-critical. Wrong additivity inference produces silent wrong numbers. Every semantic tool models this explicitly. |
| `filters` | Default filters (e.g., "exclude test data", "active users only") are semantic constraints on the metric's population, not embedded in the aggregate expression. They must be applied consistently whenever the metric is computed. |

## 5. API Structure

### 5.1 Wire Format

All API input/output is OSI-conformant. Request bodies and response bodies must pass OSI JSON schema validation. MARIVO extensions appear only in `custom_extensions` arrays.

Example create request:
```json
{
  "version": "0.1.1",
  "semantic_model": [{
    "name": "retail",
    "description": "Retail analytics",
    "datasets": [{
      "name": "store_sales",
      "source": "datasource:tpcds",
      "fields": [{
        "name": "ss_sold_date_sk",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}]},
        "custom_extensions": [{
          "vendor_name": "MARIVO",
          "data": "{\"data_type\": \"integer\"}"
        }]
      }],
      "custom_extensions": [{
        "vendor_name": "MARIVO",
        "data": "{}"
      }]
    }]
  }]
}
```

The service parses MARIVO extensions from `custom_extensions` and stores them in dedicated columns for queryability. On read, it re-serializes into `custom_extensions`.

### 5.2 Endpoints

```
POST   /semantic-models                              Create model (OSI document)
GET    /semantic-models                              List models (summary)
GET    /semantic-models/{model}                      Get model (OSI document)
PUT    /semantic-models/{model}                      Update model (OSI document)
DELETE /semantic-models/{model}                      Delete model

POST   /semantic-models/{model}/datasets             Create dataset
GET    /semantic-models/{model}/datasets             List datasets
GET    /semantic-models/{model}/datasets/{name}      Get dataset (OSI format)
PUT    /semantic-models/{model}/datasets/{name}      Update dataset
DELETE /semantic-models/{model}/datasets/{name}      Delete dataset

POST   /semantic-models/{model}/relationships        Create relationship
GET    /semantic-models/{model}/relationships        List relationships
GET    /semantic-models/{model}/relationships/{name} Get relationship
PUT    /semantic-models/{model}/relationships/{name} Update relationship
DELETE /semantic-models/{model}/relationships/{name} Delete relationship

POST   /semantic-models/{model}/metrics              Create metric
GET    /semantic-models/{model}/metrics              List metrics
GET    /semantic-models/{model}/metrics/{name}       Get metric (OSI format)
PUT    /semantic-models/{model}/metrics/{name}       Update metric
DELETE /semantic-models/{model}/metrics/{name}       Delete metric

POST   /semantic-models/import                       Import public semantic models from OSI document
GET    /semantic-models/{model}/readiness            Get readiness status + blockers
```

Note: No `/export` endpoint needed — standard GET endpoints return OSI format. No standalone `validate` endpoint exists; deterministic validation runs inside create/update. The `readiness` endpoint is the agent-facing harness operation for deciding whether a model can be used for analysis (see Section 7).

List and read endpoints apply model visibility before returning results:
- All users can see and use `public` models.
- A user can see and use only their own `private` models.
- A direct read of another user's `private` model returns `404` so the caller cannot distinguish hidden private models from missing models.
- Public model reads expose only models associated with the latest internal semantic version.
- Private models do not bind to the internal semantic version and remain visible only to their owner.

### 5.3 Write Path (API -> Storage)

1. Validate request against OSI JSON schema (strict — must pass `additionalProperties: false`)
2. Parse MARIVO extensions from `custom_extensions[].data` where `vendor_name == "MARIVO"`
3. Validate MARIVO extension fields against MARIVO extension schema
4. Run deterministic semantic validation:
   - All required OSI/MARIVO fields are present
   - All dataset references in relationships and metrics resolve to existing datasets
   - All field references in metric expressions resolve to existing fields
   - `observed_dataset` references a valid dataset
   - `observation_grain` fields exist in the observed dataset
   - `primary_time_field` references an `is_time: true` field
   - `additivity.additive_dimensions` reference valid dimension fields
   - Relationship column pairs have matching types where `data_type` is available
   - Referenced datasources exist
   - `visibility` is `public` or `private`
   - `owner_user` is present when `visibility=private`
5. Reject invalid create/update requests with structured `4xx` errors. Invalid semantic objects are not persisted.
6. If a public model is created directly, associate it with the latest `semantic_versions.version_id`. If no semantic version exists, create the initial internal semantic version first.
7. Private models are stored with no `semantic_version_id`.
8. Store OSI core fields and MARIVO extension fields in normalized tables
9. MARIVO extension fields stored in dedicated columns for queryability where possible; structured objects (additivity, filters) stored as JSON

### 5.4 Import Path (OSI JSON -> Public Semantic Layer)

`POST /semantic-models/import`

Imports an OSI document as the latest public Marivo semantic layer. The input may be either:
- A standard OSI v0.1.1 document with no MARIVO extensions
- A Marivo-extended OSI document that passes [`osi-marivo-schema.json`](../../api/osi-marivo-schema.json)

Import semantics:
1. Validate the input as OSI JSON. If MARIVO extensions exist, validate their decoded `data` payloads against the Marivo extension schema.
2. Treat every imported `semantic_model` as `public`. If a model has no MARIVO SemanticModel extension, the service normalizes it as `{"visibility": "public"}` on storage/readback. If a Marivo-extended import marks a model as `private`, reject the import because private models are not part of the versioned public semantic layer.
3. Create a new row in `semantic_versions`; its auto-increment `version_id` becomes the latest public semantic-layer version.
4. Store all imported semantic models with `visibility="public"`, `owner_user=NULL`, and `semantic_version_id=<new version_id>`.
5. Existing public models from older `semantic_version_id` values remain in storage for internal audit/staleness resolution but are no longer visible through normal read/list APIs.
6. Private models are not modified, replaced, or rebound by import.

The import endpoint is replace-by-version, not row-by-row merge. The submitted document is the complete latest public semantic layer.

### 5.5 Read Path (Storage -> API)

1. Read from normalized tables
2. Assemble OSI-conformant response:
   - OSI core fields from dedicated columns
   - MARIVO extension fields serialized into `custom_extensions[].data` as JSON string
3. Response must pass OSI JSON schema validation

## 6. Document Structure and Version Handling

OSI documents have a specific top-level structure:

```json
{
  "version": "0.1.1",
  "semantic_model": [
    {
      "name": "retail",
      "datasets": [...],
      "relationships": [...],
      "metrics": [...]
    }
  ]
}
```

- `version` is the OSI specification version — a fixed protocol identifier, NOT a per-model property. Currently `"0.1.1"`.
- `semantic_model` is an array (OSI supports multiple models per document).

### Version Storage

`version` is NOT stored in the database. It is an application-level constant — the version of the OSI spec that this Marivo release conforms to. When the OSI spec is upgraded (e.g., v0.2.0), Marivo updates this constant in code and ensures all models conform to the new version.

The `osi_spec_version` column on `semantic_models` is removed. The top-level `version` is always the application's current supported OSI version.

### Version on Read

All API responses that return OSI documents use the application's current OSI version constant:

- **Single model read** (`GET /semantic-models/{model}`): Returns `{version: "0.1.1", semantic_model: [<model>]}`
- **List models** (`GET /semantic-models`): Returns a summary list (not an OSI document)

If Marivo upgrades to a newer OSI spec version in the future, existing models must be migrated to conform to the new version before the constant is updated.

## 7. Harness: Write-Time Validation, Readiness, and Semantic Layer Versioning

Lifecycle ceremony (draft→validate→activate→publish→deprecate) is removed, but the harness functions remain.

### 7.1 Write-Time Validation

No standalone `validate` endpoint exists. Create/update requests perform deterministic validation inline and fail fast when the model is not structurally valid.

Checks the model for completeness and correctness before persistence:
- All required OSI fields present
- All dataset references in relationships and metrics resolve to existing datasets
- All field references in metric expressions resolve to existing fields
- `observed_dataset` references a valid dataset
- `observation_grain` fields exist in the observed dataset
- `primary_time_field` references an `is_time: true` field
- `additivity.additive_dimensions` reference valid dimension fields
- Relationship column pairs have matching types (via `data_type` extension)
- Referenced datasources exist
- `visibility` is `public` or `private`
- `owner_user` is present when `visibility=private`

Invalid requests return structured `4xx` errors: `{error: {code, message, path, details}}`.

### 7.2 Readiness

`GET /semantic-models/{model}/readiness`

Assesses whether the already-valid model is currently ready for analysis. This endpoint is designed for agents to decide whether they can safely use the model, and if not, what concrete remediation is needed.

Readiness checks environment-dependent and freshness-dependent conditions:
- The model was accepted by write-time validation
- All referenced datasources are reachable and have current schema
- No stale dependencies (referenced datasources, datasets, or physical fields have not changed since the public semantic version was written, or since the private model was written)
- Compiler/runtime prerequisites are available for the referenced datasource types

Returns:

```json
{
  "status": "ready",
  "semantic_version_id": 3,
  "blockers": []
}
```

When unavailable:

```json
{
  "status": "not_ready",
  "semantic_version_id": 3,
  "blockers": [{
    "code": "datasource_unreachable",
    "message": "Datasource tpcds is not reachable.",
    "subject_ref": "dataset.store_sales",
    "dependency_ref": "datasource.tpcds",
    "next_action_hint": "Check datasource connectivity or choose another ready semantic model."
  }]
}
```

Blockers must be explicit enough for an agent to choose between retrying later, repairing metadata, selecting another model, or asking the user for a datasource/configuration fix.

### 7.3 Semantic Layer Versioning

Public semantic models are grouped by an internal Marivo semantic-layer version. This version is not the OSI `version` field and is never exposed in OSI documents.

Stored in `semantic_versions.version_id` (integer primary key, auto-increment). Public `semantic_models` rows reference it through `semantic_models.semantic_version_id`.

Rules:
- `POST /semantic-models/import` creates a new semantic version and stores the imported public semantic models against that version.
- Directly created public semantic models attach to the latest semantic version.
- Normal public read/list APIs return only models whose `semantic_version_id` is the latest version.
- Private semantic models have `semantic_version_id=NULL` and are filtered only by `owner_user`.

This internal versioning mechanism allows:
- Analysis artifacts to pin to a specific public semantic-layer snapshot
- Staleness detection when the public semantic layer changes after an analysis was run
- Audit trail of public semantic-layer imports and replacements

### 7.4 Dependency Graph

The service maintains a dependency graph from metric -> dataset -> field -> datasource. This enables:
- Impact analysis when a datasource schema changes
- Staleness detection for readiness
- Efficient resolution for the compiler

Stored as a computed property, not a separate table. Derived from:
- Metric `observed_dataset` + `expression` field references
- Dataset `source` -> datasource
- Field `expression` -> physical column references

## 8. Storage Schema

Tables are organized for queryability and harness support, not minimalism.

### `semantic_versions`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| version_id | INTEGER | PK AUTO | Internal Marivo semantic-layer version |
| created_at | TIMESTAMP | NOT NULL | |

This table is internal only. It is not part of OSI output, API response bodies, or MARIVO `custom_extensions`.

### `semantic_models`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| model_id | INTEGER | PK AUTO | |
| semantic_version_id | INTEGER | FK -> semantic_versions | Required for public models; NULL for private models |
| name | TEXT | NOT NULL | OSI name |
| description | TEXT | | |
| ai_context | JSON | | OSI AIContext (object form) |
| visibility | TEXT | NOT NULL | MARIVO extension: public/private |
| owner_user | TEXT | | Required when visibility=private; used for private model filtering |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

Recommended indexes:
- `(semantic_version_id, visibility)` for latest-public filtering
- `(visibility, owner_user)` for private list/search filtering

Recommended uniqueness:
- Public models: unique `(semantic_version_id, name)`
- Private models: unique `(owner_user, name)` where `visibility='private'`

### `semantic_datasets`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| dataset_id | INTEGER | PK AUTO | |
| model_id | INTEGER | FK -> semantic_models NOT NULL | |
| name | TEXT | NOT NULL | Unique within model |
| source | TEXT | NOT NULL | Physical table/view reference (OSI: database.schema.table or query) |
| primary_key | JSON | | Array of column names |
| unique_keys | JSON | | Array of arrays |
| description | TEXT | | |
| ai_context | JSON | | |
| datasource_id | TEXT | | MARIVO extension: Marivo datasource reference |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(model_id, name)

### `semantic_fields`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| field_id | INTEGER | PK AUTO | |
| dataset_id | INTEGER | FK -> semantic_datasets NOT NULL | |
| name | TEXT | NOT NULL | Unique within dataset |
| expression | JSON | NOT NULL | OSI Expression (dialects array) |
| is_time | BOOLEAN | NOT NULL DEFAULT 0 | |
| label | TEXT | | |
| description | TEXT | | |
| ai_context | JSON | | |
| data_type | TEXT | | MARIVO extension: string/integer/number/boolean/date/datetime |
| position | INTEGER | NOT NULL | Field ordering |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(dataset_id, name)

Note: `data_type` is stored in a dedicated column (not in a JSON blob) because it's frequently queried for type validation and expression compilation.

### `semantic_relationships`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| relationship_id | INTEGER | PK AUTO | |
| model_id | INTEGER | FK -> semantic_models NOT NULL | |
| name | TEXT | NOT NULL | Unique within model |
| from_dataset | TEXT | NOT NULL | Dataset name |
| to_dataset | TEXT | NOT NULL | Dataset name |
| from_columns | JSON | NOT NULL | Array of column names |
| to_columns | JSON | NOT NULL | Array of column names |
| ai_context | JSON | | |
| cardinality | TEXT | | MARIVO extension: many_to_one/one_to_one |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(model_id, name)

Note: No `join_type` column. Join behavior is planner policy.

### `semantic_metrics`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| metric_id | INTEGER | PK AUTO | |
| model_id | INTEGER | FK -> semantic_models NOT NULL | |
| name | TEXT | NOT NULL | Unique within model |
| expression | JSON | NOT NULL | OSI Expression (dialects array) |
| description | TEXT | | |
| ai_context | JSON | | |
| observed_dataset | TEXT | | MARIVO extension: dataset name |
| observation_grain | JSON | | MARIVO extension: array of field names |
| primary_time_field | TEXT | | MARIVO extension: field name |
| additivity | JSON | | MARIVO extension: {dimension_policy, additive_dimensions, time_axis_policy} |
| filters | JSON | | MARIVO extension: array of {name, expression} |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(model_id, name)

Note: `observed_dataset`, `observation_grain`, and `primary_time_field` are in dedicated columns for queryability and foreign key validation. `additivity` and `filters` are structured JSON objects.

### `semantic_readiness_status` (harness)

| Column | Type | Constraints | Notes |
|---|---|---|---|
| model_id | INTEGER | PK, FK -> semantic_models | |
| status | TEXT | NOT NULL | ready/not_ready |
| blockers | JSON | | Array of {code, message, subject_ref, dependency_ref, next_action_hint} |
| evaluated_semantic_version_id | INTEGER | | Public semantic version evaluated by this readiness snapshot; NULL for private models |
| updated_at | TIMESTAMP | NOT NULL | |

## 9. OSI Document Example

A complete OSI-conformant document (what the API returns):

```json
{
  "version": "0.1.1",
  "semantic_model": [{
    "name": "retail",
    "description": "Retail analytics semantic model",
    "ai_context": {
      "instructions": "Use this model for retail sales analytics",
      "synonyms": ["retail", "store sales"]
    },
    "custom_extensions": [{
      "vendor_name": "MARIVO",
      "data": "{\"visibility\": \"public\"}"
    }],
    "datasets": [{
      "name": "store_sales",
      "source": "tpcds.public.store_sales",
      "primary_key": ["ss_item_sk", "ss_ticket_number"],
      "description": "Store sales transactions",
      "fields": [{
        "name": "ss_sold_date_sk",
        "expression": {
          "dialects": [{
            "dialect": "ANSI_SQL",
            "expression": "ss_sold_date_sk"
          }]
        },
        "description": "Foreign key to date dimension",
        "custom_extensions": [{
          "vendor_name": "MARIVO",
          "data": "{\"data_type\": \"integer\"}"
        }]
      }, {
        "name": "ss_sold_time",
        "expression": {
          "dialects": [{
            "dialect": "ANSI_SQL",
            "expression": "ss_sold_time_sk"
          }]
        },
        "dimension": {
          "is_time": true
        },
        "custom_extensions": [{
          "vendor_name": "MARIVO",
          "data": "{\"data_type\": \"integer\"}"
        }]
      }],
      "custom_extensions": [{
        "vendor_name": "MARIVO",
        "data": "{\"datasource_id\": \"tpcds\"}"
      }]
    }],
    "relationships": [{
      "name": "store_sales_to_date",
      "from": "store_sales",
      "to": "date_dim",
      "from_columns": ["ss_sold_date_sk"],
      "to_columns": ["d_date_sk"],
      "custom_extensions": [{
        "vendor_name": "MARIVO",
        "data": "{\"cardinality\": \"many_to_one\"}"
      }]
    }],
    "metrics": [{
      "name": "total_sales",
      "expression": {
        "dialects": [{
          "dialect": "ANSI_SQL",
          "expression": "SUM(store_sales.ss_ext_sales_price)"
        }]
      },
      "description": "Total sales revenue",
      "custom_extensions": [{
        "vendor_name": "MARIVO",
        "data": "{\"observed_dataset\": \"store_sales\", \"additivity\": {\"dimension_policy\": \"all\", \"time_axis_policy\": \"additive\"}}"
      }]
    }, {
      "name": "avg_order_value",
      "expression": {
        "dialects": [{
          "dialect": "ANSI_SQL",
          "expression": "SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT store_sales.ss_ticket_number)"
        }]
      },
      "description": "Average order value",
      "custom_extensions": [{
        "vendor_name": "MARIVO",
        "data": "{\"observed_dataset\": \"store_sales\", \"observation_grain\": [\"ss_ticket_number\"], \"primary_time_field\": \"ss_sold_time\", \"additivity\": {\"dimension_policy\": \"none\", \"time_axis_policy\": \"non_additive\"}}"
      }]
    }]
  }]
}
```

This document passes OSI JSON schema validation. All MARIVO-specific data is in `custom_extensions`.

## 10. Deleted Components

### Object Types Deleted

Process Object, EnumSet, Predicate, Binding, Compatibility Profile

### Object Types Collapsed

Dimension -> Field property (dimension.is_time), Time -> Field property (dimension.is_time: true)

### Lifecycle Ceremony Deleted

draft→validate→activate→publish→deprecate flow removed. Revision mechanism removed. Objects are created/updated/deleted directly. Deterministic validation runs inside create/update; readiness remains as the agent-facing harness operation (Section 7).

### API Endpoints Removed

- `/domains/...` -> replaced by `/semantic-models/...`
- `/entities/...` -> replaced by `/semantic-models/{model}/datasets/...`
- `/dimensions/...` -> collapsed into dataset fields
- `/time-semantics/...` -> collapsed into dataset fields
- `/predicates/...` -> replaced by metric `filters` extension
- `/process-objects/...` -> deleted
- `/enum-sets/...` -> deleted
- `/bindings/...` -> inlined into datasets (source + expression)
- `/compatibility-profiles/...` -> deleted
- Lifecycle endpoints (activate/publish/deprecate) -> deleted
- Revision endpoints -> deleted

### Storage Tables Deleted

semantic_entity_contracts, semantic_entity_key_refs, semantic_entity_stable_descriptors, semantic_metric_contracts, semantic_process_objects, semantic_process_exported_dimension_refs, semantic_dimension_contracts, semantic_time_objects, semantic_enum_sets, semantic_enum_set_versions, semantic_enum_set_values, semantic_predicate_contracts, semantic_domain_catalog, typed_bindings, binding_imports, carrier_bindings, carrier_field_surfaces, carrier_time_surfaces, field_bindings, time_bindings, join_relations, consumption_policies, semantic_entity_relationships, compiler_compatibility_profiles

### Code Modules Replaced/Deleted

**Replaced:**
- `app/api/models/` -> new OSI-aligned Pydantic models (OSI schema + MARIVO extension parsing)
- `app/semantic_service/` -> new services with OSI<->storage mapping
- `app/semantic_runtime/` -> new runtime with OSI assembly
- `app/storage/schema.py` -> new schema

**Deleted:**
- `app/semantic_revision/` -> deleted (revision removed)

**Refactored (not deleted):**
- `app/semantic_readiness/` -> simplified to agent-facing readiness with explicit blockers and next-action hints
- `app/analysis_core/capability_profiles.py` -> deleted
- `app/analysis_core/predicate_validator.py` -> deleted (filters are in metric extensions)
- `app/analysis_core/typed_resolution.py` -> simplified for new object model
- `app/analysis_core/compiler.py` -> adapted for flat expressions + MARIVO extensions

## 11. MARIVO Extension Summary

| Object | OSI Core Fields | MARIVO Extensions |
|---|---|---|
| SemanticModel | name, description, ai_context, datasets, relationships, metrics | visibility, owner_user |
| Dataset | name, source, primary_key, unique_keys, description, ai_context, fields | datasource_id |
| Field | name, expression, dimension.is_time, label, description, ai_context | data_type |
| Relationship | name, from, to, from_columns, to_columns, ai_context | cardinality |
| Metric | name, expression, description, ai_context | observed_dataset, observation_grain, primary_time_field, additivity, filters |

Total MARIVO extension fields: 10 across 5 object types.

Every MARIVO extension field exists because it cannot be safely inferred from SQL expressions and wrong inference produces either silent wrong numbers or validation gaps.

## 12. Feasibility

**Verdict: Feasible**

| Metric | Value |
|---|---|
| Object types | 4 (from 12+) |
| Storage tables | 7 (6 core + 1 harness) |
| MARIVO extension fields | 10 (from 20+) |
| API endpoints | 22 (from 40+) |
| OSI schema validation | Passes (all non-OSI data in custom_extensions) |
| OSI core field coverage | ~75% |
| Harness preserved | Write-time validation, readiness, semantic layer versioning, dependency graph |

**Remaining risk:**
- MARIVO vendor namespace not yet registered in OSI (use COMMON with `_vendor: "marivo"` as fallback until registered)
- `custom_extensions[].data` is a JSON string, not a native object — parsing/serialization overhead on every request
- Downstream consumers (MCP, skill, compiler) need rewrite
- Source mapping to datasource_id needs implementation (currently `source` is just a string)

**Not a risk:**
- OSI conformance — wire format passes schema validation by construction
- Loss of capability — all safety-critical semantics preserved with explicit extensions
- Harness gaps — write-time validation, readiness, and semantic layer versioning explicitly designed
