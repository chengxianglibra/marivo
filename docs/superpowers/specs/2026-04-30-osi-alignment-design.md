# OSI Alignment Design: Marivo Semantic Layer

**Date:** 2026-04-30
**Status:** Draft
**Approach:** B — Maximum core mapping + vendor extensions + new OSI objects

## 1. Goals

1. Replace Marivo's internal semantic model with OSI-aligned entities
2. Preserve all actively-used capabilities (dead fields removed)
3. OSI-conformant parts can be cleanly exported for standard tool consumption
4. Propose new OSI objects for features with broad cross-tool demand

## 2. Design Principles

### What vs How Boundary

The semantic layer should encode **intent and safety-critical constraints**, not computation recipes. Specific criteria:

| Criterion | Decision | Example |
|---|---|---|
| Wrong inference → silent wrong number | **Keep in harness** | `additivity_constraints` |
| Wrong inference → error (not wrong result) | **Delegate to LLM** | `value_semantics` |
| No code consumes the field today | **Delete** | `nullability_policy`, `ownership_semantics` |

### Dead Fields to Remove

Fields with zero business logic consumers, removed from the model entirely:

**Entity:** `nullable_key_policy`, `cardinality_to_parent`, `ownership_semantics`
**Entity (simplify):** `id_stability` → delete (presence-check only, no value branching); `uniqueness_scope` → delete (same)
**Metric:** `aggregation_scope`, `default_test_method`, `value_semantics`
**Metric (delete families):** `survival_spec` → delete entire `survival_metric` family; `score_kind` → delete entire `score_metric` family; `sketch_policy_ref` (within DistributionSpec)
**Binding:** `nullability_policy`, `repeated_value_policy`, `compatibility_rule_refs`, `freshness_policy_ref`, `semantic_role_ref`
**Binding (simplify):** `temporal_constraint_refs` → delete (presence-check only); `consumption_policy` → retain in Marivo model for publish-time validation, but do not map to OSI (no runtime enforcement exists)

### Remaining Metric Families

After removing dead families, 5 active families remain:

| Family | Active Logic | OSI Expression Pattern |
|---|---|---|
| `count_metric` | Core | `COUNT(...)` |
| `sum_metric` | Core | `SUM(...)` |
| `rate_metric` | Core | `numerator / denominator` |
| `average_metric` | Core | `numerator / denominator` |
| `distribution_metric` | SQL compilation (kind, percentile) | `APPROX_PERCENTILE(...)` |

## 3. Entity Mapping

### 3.1 Entity → OSI Dataset

**Core mapping (OSI-conformant output):**

| Marivo Field | OSI Field | Notes |
|---|---|---|
| `entity_ref` | `Dataset.name` | Strip `entity.` prefix for OSI |
| `description` | `Dataset.description` | Direct |
| `display_name` | `Dataset.ai_context.synonyms` | Via ai_context object |
| `key_refs` | `Dataset.primary_key` | Composite keys as array |
| `CarrierLocatorSpec` | `Dataset.source` | `catalog.schema.table` |
| `FieldSurfaceSpec` | `Dataset.fields[]` | Each surface → a Field |
| `TimeSurfaceSpec` | `Dataset.fields[]` + `dimension.is_time: true` | Time fields get dimension flag |
| `parent_entity_ref` | `Relationship` | from=child (many), to=parent (one) |

**MARIVO extension:**

| Marivo Field | Extension Key | Rationale |
|---|---|---|
| `stable_descriptors` | `stable_descriptors` | Entity-dimension association, non-obvious |
| `primary_time_ref` | `primary_time_ref` | Indicates which is_time field is primary, not derivable |
| `contract_version` | `contract_version` | Lifecycle metadata |

**OSI output:** Fully conformant. Standard tools see entity structure, keys, fields, and relationships.

### 3.2 Dimension → OSI Field + Dimension

**Core mapping:**

| Marivo Field | OSI Field | Notes |
|---|---|---|
| `dimension_ref` | `Field.name` | Strip `dimension.` prefix |
| `description` | `Field.description` | Direct |
| `structure_kind == "time_derived"` | `Field.dimension.is_time: true` | Boolean mapping |
| `structure_kind != "time_derived"` | `Field.dimension.is_time: false` or omit | |
| `semantic_role == "label"` | `Field.label` | Direct |
| Physical column reference | `Field.expression.dialects[]` | SQL column reference |

**MARIVO extension:**

| Marivo Field | Extension Key | Rationale |
|---|---|---|
| `structure_kind` | `structure_kind` | flat/hierarchical/ordinal/time_derived — not derivable from is_time alone |
| `value_type` | `data_type` | string/integer/number/boolean/date/datetime — safety-critical for validation |
| `domain_kind` | `domain_kind` | open/enumerated — governance intent |
| `hierarchy_type` | `hierarchy_type` | flat/parent_child/ordinal/calendar_rollup — non-obvious |
| `parent_dimension_ref` | `parent_dimension` | Hierarchy structure |
| `supports_grouping` | `supports_grouping` | Safety constraint (blocks invalid decomposition) |
| `time_derived_requirement` | `time_derived_requirement` | Compiler validation needs this |

**OSI output:** Fully conformant. Standard tools see dimension name, expression, is_time, label.

### 3.3 Time Semantic → OSI Field + dimension: {is_time: true}

**Core mapping:**

| Marivo Field | OSI Field | Notes |
|---|---|---|
| `time_ref` | `Field.name` | Strip `time.` prefix |
| `description` | `Field.description` | Direct |
| Time flag | `Field.dimension.is_time: true` | Always true for time semantics |

**MARIVO extension:**

| Marivo Field | Extension Key | Rationale |
|---|---|---|
| `semantic_roles` | `semantic_roles` | business_anchor/measurement/operational_support — compositional, non-obvious |
| `time_granularity` | `granularity` | second/minute/hour/day — derivable but safety-critical for rollup |
| Time binding resolution | `time_binding` | timestamp_column/date_column/date_hour_columns — physical layout |
| Calendar alignment | `calendar_alignment` | Holiday/weekday/trading-day policy — complex, Marivo-specific |

**OSI output:** Partial — time fields output as `is_time: true`, but roles, granularity, calendar are extension-only.

### 3.4 Metric → OSI Metric

**Core mapping:**

| Marivo Field | OSI Field | Notes |
|---|---|---|
| `metric_ref` | `Metric.name` | Strip `metric.` prefix |
| `description` | `Metric.description` | Direct |
| MeasurementComponent → SQL | `Metric.expression.dialects[]` | Flattened aggregate SQL |
| Predicate filter logic | Embedded in expression | `FILTER (WHERE ...)` or `CASE WHEN` |

**MARIVO extension:**

| Marivo Field | Extension Key | Rationale |
|---|---|---|
| `metric_family` | `metric_family` | count/sum/rate/average/distribution — intent + safety constraint |
| `sample_kind` | `sample_kind` | numeric/rate/binary — drives real branching in test/validate |
| `observed_entity_ref` | `observed_entity` | Cross-object composition, not derivable |
| `observation_grain_ref` | `observation_grain` | Grain constraint, not derivable |
| `population_subject_ref` | `population_subject` | Cross-object composition, heavily used |
| `additivity_constraints` | `additivity` | Safety-critical — prevents silent wrong numbers |
| `default_predicate_refs` | `default_predicates` | Business predicates, not derivable |
| `required_inputs` | `required_inputs` | Binding validation, revision dependency |
| `distribution_spec` | `distribution_spec` | kind/percentile for distribution compilation |
| `primary_time_ref` | `primary_time` | Time axis for metric |

**OSI output:** Fully conformant — metric name + expression + description. Standard tools can compute the metric.

### 3.5 Predicate → Metric Extension + Embedded SQL

Predicates are NOT a separate OSI entity in the current spec (v0.1.1). A Filter object is proposed as an OSI spec contribution (Section 4.1). Until that contribution is accepted, filter logic is embedded in metric expressions (OSI-conformant), and governance metadata lives in MARIVO metric extension.

| Marivo Field | Mapping | Type |
|---|---|---|
| Filter expression → SQL | Embedded in `Metric.expression` | **Core** (for output) |
| `predicate_ref` | `default_predicates[].name` in extension | Extension |
| `allowed_usage` | `default_predicates[].allowed_usage` in extension | Extension |
| `time_policy` | `default_predicates[].time_policy` in extension | Extension |

**OSI output:** Yes — filter logic appears in SQL expressions. Governance is Marivo-specific.

### 3.6 Binding → Dataset.source + Field.expression + Extension

**Core mapping:**

| Marivo Field | OSI Field | Notes |
|---|---|---|
| `CarrierLocatorSpec` | `Dataset.source` | catalog.schema.table |
| `FieldSurfaceSpec.physical_name` | `Field.expression` | Column reference SQL |
| Simple joins (inner/left) | `Relationship` | from/to with column mapping |

**MARIVO extension:**

| Marivo Field | Extension Key | Rationale |
|---|---|---|
| `carrier_kind` | `carrier_kind` | table/view distinction |
| `binding_role` | `binding_role` | primary/auxiliary carrier |
| `binding_scope` / `bound_object_ref` | `binding_scope` / `bound_object` | Cross-object reference |
| `JoinRelation` (full) | `join_relations` | Complex joins beyond many-to-one |
| `BindingImport` | `binding_imports` | Binding composition mechanism |
| `ConsumptionPolicySpec` | `consumption_policies` | Late arrival / incomplete window handling |
| `TimeBindingSpec` | `time_bindings` | Physical time column resolution |
| `FieldBinding` typed targets | `field_bindings` | Semantic ref → physical column mapping |

**OSI output:** Partial — source and basic field references output. Complex binding details are extension-only.

### 3.7 Relationship → OSI Relationship

**Core mapping:**

| Marivo Source | OSI Field | Notes |
|---|---|---|
| Entity parent (many_to_one) | `Relationship` | from=child, to=parent |
| Simple join (many_to_one) | `Relationship` | Standard mapping |
| Column pairs | `from_columns` / `to_columns` | Positional correspondence |

**MARIVO extension:**

| Marivo Source | Extension Key | Notes |
|---|---|---|
| `join_kind` (inner/left/semi/anti) | `join_type` | OSI only supports implicit many-to-one |
| `cardinality` (one_to_one, one_to_many, many_to_many) | `cardinality` | OSI only supports many-to-one |
| `key_ref_pairs` (semantic refs) | `semantic_key_pairs` | Marivo uses semantic refs not physical columns |

**OSI output:** Yes for simple relationships. Complex joins are extension-only.

### 3.8 Compatibility Profile → MARIVO Extension Only

Purely compile-time concern. No OSI output needed. Stored in model-level custom_extensions.

### 3.9 Lifecycle / Revision / Readiness → MARIVO Extension Only

Runtime management concerns (status, readiness, revision, blocking_requirements). Not part of the semantic model definition. No OSI output needed.

## 4. Proposed OSI Specification Contributions

### 4.1 Filter Object (High Impact, High Acceptance Likelihood)

Every semantic layer needs filters. Currently OSI has no way to define reusable, named filters.

```yaml
semantic_model:
  - name: retail
    filters:                              # NEW top-level entity
      - name: exclude_test_data
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "is_test = false"
        description: "Excludes test records"
        ai_context: ...
        custom_extensions: ...
    datasets: [...]
    relationships: [...]
    metrics: [...]
```

Fields on metrics and fields can reference filters:

```yaml
metrics:
  - name: active_revenue
    expression: { dialects: [...] }
    filters: [exclude_test_data]          # NEW optional field
```

### 4.2 Measure Components (High Impact, High Acceptance Likelihood)

Ratio and composite metrics cannot be expressed as a single opaque SQL expression with structure. Proposed optional `components` field on Metric:

```yaml
metrics:
  - name: conversion_rate
    expression:                           # Flat SQL for standard tools
      dialects:
        - dialect: ANSI_SQL
          expression: "SUM(CASE WHEN purchased THEN 1 ELSE 0 END) / COUNT(DISTINCT user_id)"
    components:                           # NEW - structured for rich tools
      - name: numerator
        aggregation: sum
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "CASE WHEN purchased THEN 1 ELSE 0 END"
      - name: denominator
        aggregation: count_distinct
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "user_id"
```

### 4.3 Additivity Policy (High Impact, High Acceptance Likelihood)

Every semantic tool (dbt, Looker, Cube) defines additivity differently. A unified model:

```yaml
metrics:
  - name: revenue
    expression: { dialects: [...] }
    additivity:                           # NEW
      dimension_policy: all | subset | none
      additive_dimensions: [...]          # Required when subset
      time_axis_policy: additive | non_additive
```

### 4.4 Metric Type Enum (Medium Impact, Medium Acceptance Likelihood)

Classifying metrics helps tools understand semantics without parsing SQL:

```yaml
metrics:
  - name: dau
    expression: { dialects: [...] }
    metric_type: count | sum | rate | average | distribution  # NEW
```

### 4.5 Field Data Type (Medium Impact, High Acceptance Likelihood)

No type information exists in OSI today — an obvious gap:

```yaml
fields:
  - name: revenue
    expression: { dialects: [...] }
    data_type: number                     # NEW
```

### 4.6 Field Mapping (Medium Impact, Medium Acceptance Likelihood)

Mapping logical fields to physical columns:

```yaml
datasets:
  - name: users
    source: analytics.public.users
    field_mappings:                       # NEW
      - field: user_id
        physical_column: uid
        data_type: integer
```

### 4.7 Relationship Cardinality and Join Type (Medium Impact, Medium Acceptance Likelihood)

Extending relationships beyond many-to-one:

```yaml
relationships:
  - name: user_orders
    from: orders
    to: users
    from_columns: [user_id]
    to_columns: [id]
    cardinality: many_to_one              # NEW (currently implicit)
    join_type: inner | left | semi | anti # NEW
```

## 5. OSI-Conformant Output Capability

When exporting Marivo's model as an OSI document, the following is fully conformant without vendor extensions:

| Entity | Exportable OSI Content |
|---|---|
| **Entity** | Dataset: name, description, source, primary_key, fields, relationships |
| **Dimension** | Field: name, expression, dimension.is_time, label, description |
| **Time** | Field: name, expression, dimension.is_time: true, description |
| **Metric** | Metric: name, expression (flattened SQL), description |
| **Relationship** | Relationship: name, from, to, from_columns, to_columns |
| **Predicate** | Filter logic embedded in metric expressions |

The following is available only to MARIVO-aware consumers via custom_extensions:

| Capability | Location |
|---|---|
| Additivity constraints | Metric custom_extensions |
| Metric family / sample_kind | Metric custom_extensions |
| Entity identity semantics | Dataset custom_extensions |
| Dimension value domain / hierarchy | Field custom_extensions |
| Time roles / granularity / calendar | Field custom_extensions |
| Predicate governance | Metric custom_extensions |
| Binding details (joins, imports, time bindings) | Dataset custom_extensions |
| Compatibility profiles | SemanticModel custom_extensions |
| Lifecycle / readiness / revision | SemanticModel custom_extensions |

## 6. Structural Constraints

### 6.1 MARIVO Vendor Namespace

Must register `MARIVO` as a new Vendor enum value in OSI. This requires:
- A PR to the OSI specification
- Minimum 7-day discussion period
- TSC vote (2+ binding +1, no vetoes)

Until accepted, use `COMMON` namespace with `{"_vendor": "marivo"}` in the data field.

### 6.2 additionalProperties: false

OSI JSON Schema enforces `additionalProperties: false` on every entity. To add proposed new fields (components, additivity, metric_type, data_type, field_mappings, filters, cardinality, join_type):

**Short term:** Fork the JSON Schema locally, add optional fields. Marivo validates against the forked schema.

**Long term:** Contribute fields upstream. Once accepted, switch to the official schema.

### 6.3 CustomExtension Data Format

`custom_extensions[].data` must be a JSON string (not a native object). All MARIVO extension content must be JSON-serialized. Example:

```yaml
custom_extensions:
  - vendor_name: MARIVO
    data: |
      {
        "additivity": {
          "dimension_policy": "subset",
          "additive_dimensions": ["country", "channel"],
          "time_axis_policy": "additive"
        },
        "metric_family": "rate_metric",
        "sample_kind": "rate",
        "observed_entity": "entity.user"
      }
```

## 7. Feasibility Assessment

**Verdict: Feasible**

After removing dead fields and the survival/score metric families, the mapping is significantly simpler:

| Metric | Value |
|---|---|
| Entity types with OSI core mapping | 5/7 (Entity, Dimension, Time, Metric, Relationship) |
| Entity types as extensions only | 2/7 (Compatibility Profile, Lifecycle) |
| Dead fields removed | 13 |
| Metric families | 5 (from 7) |
| Estimated core field coverage | ~45% |
| Core coverage with spec contributions accepted | ~65-70% |
| OSI-conformant export | Yes (all structural + metric SQL) |

**Remaining risks:**
1. MARIVO vendor namespace registration (governance dependency)
2. Spec contribution timeline (must maintain forked schema until accepted)
3. `additionalProperties: false` requires local schema fork

**Not a risk:**
- Loss of capability — all active business logic is preserved in MARIVO extensions
- OSI output quality — standard tools can consume entity structure, dimensions, metrics, and relationships

## 8. Spec Contribution Priority

| Priority | Proposal | Impact | Acceptance Likelihood |
|---|---|---|---|
| 1 | Filter object | High | High |
| 2 | Measure components | High | High |
| 3 | Additivity policy | High | High |
| 4 | Field data type | Medium | High |
| 5 | Metric type enum | Medium | Medium |
| 6 | Field mapping | Medium | Medium |
| 7 | Relationship cardinality/join_type | Medium | Medium |
