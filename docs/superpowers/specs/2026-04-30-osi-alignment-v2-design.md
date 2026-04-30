# OSI Alignment V2: Marivo Semantic Layer Rewrite

**Date:** 2026-04-30
**Status:** Draft
**Approach:** Clean rewrite aligned to OSI Core Metadata Spec v0.1.1

## 1. Goals

1. Replace Marivo's semantic model with OSI-aligned objects (SemanticModel, Dataset, Field, Relationship, Metric)
2. Delete unused objects (Process, EnumSet, Predicate, Binding, CompatibilityProfile) and collapse Dimension/Time into Field
3. API input/output conforms to OSI spec; MARIVO-specific fields are native in API but serialize to `custom_extensions` on OSI export
4. Simplify to 5 storage tables (from 20+) and 4 object types (from 12+)

## 2. Design Principles

### What vs How Boundary

| Criterion | Decision | Example |
|---|---|---|
| Wrong inference -> silent wrong number | **Keep in harness (MARIVO extension)** | `additivity` on Metric |
| Wrong inference -> error (not wrong result) | **Delegate to expression** | join_type in Relationship |
| No runtime code consumes the field | **Delete** | `stable_descriptors`, `metric_family` |

### Decided Removals

**Object types deleted:** Process Object, EnumSet, Predicate, Binding, Compatibility Profile

**Object types collapsed:** Dimension -> Field property, Time -> Field property (is_time)

**Lifecycle/Readiness/Revision deleted** as API concepts. Objects are created/updated/deleted directly.

### OSI Spec Conformance

- OSI spec is the API contract for external interaction
- Internal storage is organized for efficient queries, not OSI structure
- Write path: OSI JSON + MARIVO extension fields -> normalized storage
- Read path: normalized storage -> API response with native fields
- Export path: normalized storage -> OSI-conformant YAML/JSON (MARIVO extensions in custom_extensions)

## 3. Object Model

### 3.1 SemanticModel

Top-level container. Maps from current Domain.

```
SemanticModel
  name              string   (required) OSI name
  osi_spec_version  string   (required) OSI spec version this model conforms to, default "0.1.1"
  description       string   (optional)
  ai_context        AIContext (optional) OSI format: string or {instructions, synonyms, examples}
  datasets[]        Dataset  (required, min 1)
  relationships[]   Relationship (optional)
  metrics[]         Metric   (optional)
```

No MARIVO extensions on SemanticModel.

### 3.2 Dataset

Maps from current Entity. Direct physical grounding (no separate Binding).

```
Dataset
  name              string   (required) OSI name (was entity_ref minus "entity." prefix)
  source            string   (required) Physical table/view reference: catalog.schema.table
  primary_key       string[] (optional) Column names forming the primary key
  unique_keys       string[][] (optional) Array of unique key definitions
  description       string   (optional)
  ai_context        AIContext (optional)
  fields[]          Field    (required, min 1)

MARIVO extensions:
  primary_time_field  string  (optional) Name of the primary time field in this dataset
```

**Rationale for primary_time_field:** Compiler needs to know which time field to use for time-series query assembly. A metric expression like `SUM(sales)` does not reference a time field; the compiler uses `primary_time_field` to add temporal grouping. Wrong inference would produce incorrect time-series results.

### 3.3 Field

Collapses current EntityField + Dimension + Time into one concept.

```
Field
  name              string   (required) Unique within dataset
  expression        Expression (required) OSI multi-dialect expression
                      dialects[]:
                        dialect    string   (required, default "ANSI_SQL")
                        expression string   (required) SQL scalar expression or column reference
  dimension         object   (optional)
    is_time         boolean  (default false) Indicates time-based dimension
  label             string   (optional) Categorization label
  description       string   (optional)
  ai_context        AIContext (optional)

MARIVO extensions:
  data_type         string   (optional) string/integer/number/boolean/date/datetime
```

**Rationale for data_type:** No type information exists in OSI. Type is safety-critical for SQL compilation and validation. Without it, the compiler cannot validate expressions or choose appropriate SQL functions.

### 3.4 Relationship

Maps from current EntityRelationship. Simplified to FK-based.

```
Relationship
  name              string   (required) Unique within model
  from              string   (required) Dataset name (many side)
  to                string   (required) Dataset name (one side)
  from_columns      string[] (required) FK columns in the "from" dataset
  to_columns        string[] (required) PK/unique columns in the "to" dataset
  ai_context        AIContext (optional)

MARIVO extensions:
  join_type         string   (optional) inner/left/semi/anti
  cardinality       string   (optional) many_to_one/one_to_one
```

**Rationale for join_type + cardinality:** When a metric expression spans multiple datasets, the compiler assembles JOIN clauses. Different join types produce different results (LEFT JOIN preserves rows, INNER JOIN drops them). Wrong inference -> silent wrong numbers. Cardinality affects join direction and result interpretation.

### 3.5 Metric

Flat expression model. Marivo-specific semantics as extension.

```
Metric
  name              string   (required) OSI name (was metric_ref minus "metric." prefix)
  expression        Expression (required) OSI multi-dialect expression
  description       string   (optional)
  ai_context        AIContext (optional)

MARIVO extensions:
  additivity        object   (optional)
    dimension_policy    string   all/subset/none
    additive_dimensions string[] (required when subset)
    time_axis_policy    string   additive/non_additive
```

**Rationale for additivity:** Safety-critical. Wrong additivity inference produces silent wrong numbers (e.g., averaging an average, summing a non-additive measure across dimensions). Every semantic tool (dbt, Looker, Cube) models this.

**Deleted metric concepts:** metric_family (inferable from expression), observed_entity (inferable from expression), observation_grain (inferable), population_subject (no runtime consumer), default_predicates (embedded in expressions), distribution_spec (inferable from expression), sample_kind (no runtime consumer).

## 4. API Structure

### 4.1 Endpoints

```
POST   /semantic-models                              Create model
GET    /semantic-models                              List models
GET    /semantic-models/{model}                      Get model (metadata only)
PUT    /semantic-models/{model}                      Update model metadata
DELETE /semantic-models/{model}                      Delete model

POST   /semantic-models/{model}/datasets             Create dataset
GET    /semantic-models/{model}/datasets             List datasets
GET    /semantic-models/{model}/datasets/{name}      Get dataset
PUT    /semantic-models/{model}/datasets/{name}      Update dataset
DELETE /semantic-models/{model}/datasets/{name}      Delete dataset

POST   /semantic-models/{model}/relationships        Create relationship
GET    /semantic-models/{model}/relationships        List relationships
GET    /semantic-models/{model}/relationships/{name} Get relationship
PUT    /semantic-models/{model}/relationships/{name} Update relationship
DELETE /semantic-models/{model}/relationships/{name} Delete relationship

POST   /semantic-models/{model}/metrics              Create metric
GET    /semantic-models/{model}/metrics              List metrics
GET    /semantic-models/{model}/metrics/{name}       Get metric
PUT    /semantic-models/{model}/metrics/{name}       Update metric
DELETE /semantic-models/{model}/metrics/{name}       Delete metric

GET    /semantic-models/{model}/export               Export as OSI YAML/JSON
```

### 4.2 Write Path (API -> Storage)

1. Validate request against OSI JSON schema + MARIVO extension schema
2. For Dataset: inline fields are validated and stored in `semantic_fields` table
3. MARIVO extension fields stored in dedicated columns (not JSON blobs) for queryability
4. `osi_spec_version` stored on `semantic_models`, defaults to latest supported OSI version

### 4.3 Read Path (Storage -> API)

1. Read from normalized tables
2. Assemble response with native API fields
3. Fields are returned inline within Dataset responses (nested)

### 4.4 Export Path (Storage -> OSI Output)

1. Read from normalized tables
2. Assemble OSI-conformant structure:
   - `version` = model's `osi_spec_version`
   - `semantic_model[]` with nested datasets, relationships, metrics
   - MARIVO extensions serialized into `custom_extensions[].data` as JSON string with `vendor_name: "MARIVO"`
3. Output as YAML or JSON (Accept header or query param)

## 5. Storage Schema

5 tables replacing 20+ current tables.

### `semantic_models`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| model_id | INTEGER | PK AUTO | |
| name | TEXT | UNIQUE NOT NULL | OSI name |
| osi_spec_version | TEXT | NOT NULL DEFAULT '0.1.1' | OSI spec version this model conforms to |
| description | TEXT | | |
| ai_context | JSON | | OSI AIContext format |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

### `semantic_datasets`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| dataset_id | INTEGER | PK AUTO | |
| model_id | INTEGER | FK -> semantic_models NOT NULL | |
| name | TEXT | NOT NULL | Unique within model |
| source | TEXT | NOT NULL | catalog.schema.table or query |
| primary_key | JSON | | Array of column names |
| unique_keys | JSON | | Array of arrays |
| description | TEXT | | |
| ai_context | JSON | | |
| primary_time_field | TEXT | | MARIVO: field name |
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
| data_type | TEXT | | MARIVO: string/integer/number/boolean/date/datetime |
| position | INTEGER | NOT NULL | Field ordering |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(dataset_id, name)

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
| join_type | TEXT | | MARIVO: inner/left/semi/anti |
| cardinality | TEXT | | MARIVO: many_to_one/one_to_one |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(model_id, name)

### `semantic_metrics`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| metric_id | INTEGER | PK AUTO | |
| model_id | INTEGER | FK -> semantic_models NOT NULL | |
| name | TEXT | NOT NULL | Unique within model |
| expression | JSON | NOT NULL | OSI Expression (dialects array) |
| description | TEXT | | |
| ai_context | JSON | | |
| additivity | JSON | | MARIVO: {dimension_policy, additive_dimensions, time_axis_policy} |
| created_at | TIMESTAMP | NOT NULL | |
| updated_at | TIMESTAMP | NOT NULL | |

UNIQUE(model_id, name)

## 6. OSI Export Example

```yaml
version: "0.1.1"
semantic_model:
  - name: retail
    description: Retail analytics semantic model
    ai_context:
      instructions: "Use this model for retail sales analytics"
      synonyms: ["retail", "store sales"]

    datasets:
      - name: store_sales
        source: tpcds.public.store_sales
        primary_key: [ss_item_sk, ss_ticket_number]
        description: Store sales transactions
        fields:
          - name: ss_sold_date_sk
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: ss_sold_date_sk
            description: Foreign key to date dimension
            custom_extensions:
              - vendor_name: MARIVO
                data: '{"data_type": "integer"}'
          - name: ss_sold_time
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: ss_sold_time_sk
            dimension:
              is_time: true
            custom_extensions:
              - vendor_name: MARIVO
                data: '{"data_type": "integer"}'
        custom_extensions:
          - vendor_name: MARIVO
            data: '{"primary_time_field": "ss_sold_time"}'

    relationships:
      - name: store_sales_to_date
        from: store_sales
        to: date_dim
        from_columns: [ss_sold_date_sk]
        to_columns: [d_date_sk]
        custom_extensions:
          - vendor_name: MARIVO
            data: '{"join_type": "left", "cardinality": "many_to_one"}'

    metrics:
      - name: total_sales
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(store_sales.ss_ext_sales_price)
        description: Total sales revenue
        custom_extensions:
          - vendor_name: MARIVO
            data: '{"additivity": {"dimension_policy": "all", "time_axis_policy": "additive"}}'
```

## 7. Deleted Components

### API Endpoints Removed

- `/domains/...` -> replaced by `/semantic-models/...`
- `/entities/...` -> replaced by `/semantic-models/{model}/datasets/...`
- `/dimensions/...` -> collapsed into dataset fields
- `/time-semantics/...` -> collapsed into dataset fields
- `/predicates/...` -> embedded in metric expressions
- `/process-objects/...` -> deleted
- `/enum-sets/...` -> deleted
- `/bindings/...` -> inlined into datasets
- `/compatibility-profiles/...` -> deleted
- All lifecycle endpoints (validate/activate/publish/deprecate) -> deleted
- All revision endpoints -> deleted

### Storage Tables Deleted

semantic_entity_contracts, semantic_entity_key_refs, semantic_entity_stable_descriptors, semantic_metric_contracts, semantic_process_objects, semantic_process_exported_dimension_refs, semantic_dimension_contracts, semantic_time_objects, semantic_enum_sets, semantic_enum_set_versions, semantic_enum_set_values, semantic_predicate_contracts, semantic_domain_catalog, typed_bindings, binding_imports, carrier_bindings, carrier_field_surfaces, carrier_time_surfaces, field_bindings, time_bindings, join_relations, consumption_policies, semantic_entity_relationships, compiler_compatibility_profiles

### Code Modules Replaced/Deleted

**Replaced:**
- `app/api/models/` -> new OSI-aligned Pydantic models
- `app/semantic_service/` -> new simplified services
- `app/semantic_runtime/` -> new runtime with OSI assembler
- `app/storage/schema.py` -> new 5-table schema

**Deleted:**
- `app/semantic_readiness/` -> deleted (lifecycle/readiness removed)
- `app/semantic_revision/` -> deleted (revision removed)
- `app/analysis_core/capability_profiles.py` -> deleted
- `app/analysis_core/predicate_validator.py` -> deleted
- `app/analysis_core/typed_resolution.py` -> replaced by simpler resolution

## 8. MARIVO Extension Summary

| Object | OSI Core Fields | MARIVO Extensions |
|---|---|---|
| SemanticModel | name, osi_spec_version, description, ai_context, datasets, relationships, metrics | none |
| Dataset | name, source, primary_key, unique_keys, description, ai_context, fields | primary_time_field |
| Field | name, expression, dimension.is_time, label, description, ai_context | data_type |
| Relationship | name, from, to, from_columns, to_columns, ai_context | join_type, cardinality |
| Metric | name, expression, description, ai_context | additivity |

Total MARIVO extensions: 5 fields across 4 object types (down from 20+ in the prior spec).

## 9. Feasibility

**Verdict: Feasible**

| Metric | Value |
|---|---|
| Object types | 4 (from 12+) |
| Storage tables | 5 (from 20+) |
| MARIVO extension fields | 5 (from 20+) |
| API endpoints | ~16 (from 40+) |
| OSI core field coverage | ~80% |
| OSI-conformant export | Yes (all structural + metric SQL) |

**Remaining risk:**
- MARIVO vendor namespace not yet registered in OSI (use COMMON + `_vendor: "marivo"` as fallback)
- `additionalProperties: false` in OSI JSON schema means export must serialize all non-OSI fields into custom_extensions
- Downstream consumers (MCP, skill, compiler) need rewrite

**Not a risk:**
- Loss of capability - all safety-critical semantics preserved (additivity, join_type, data_type)
- OSI output quality - standard tools can consume full entity structure, metrics, and relationships
