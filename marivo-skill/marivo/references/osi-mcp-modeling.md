# OSI Semantic Modeling via MCP Tools

Use this file when the task is **building semantic layer objects through MCP tools** —
`create_semantic_model`, `create_dataset`, `create_metric`, `create_relationship`, and the
associated browse/preview/readiness endpoints.

Skip this file if you need the older entity/dimension/predicate/metric.* HTTP contracts
(`semantic-layer.md` covers those), or if the task is session-scoped investigation rather
than semantic modeling.

## Scope

This reference covers the OSI-aligned semantic model surface exposed through MCP tools:

| MCP Tool | Purpose |
|----------|---------|
| `list_datasources` / `get_datasource` / `create_datasource` | Register and inspect external data sources |
| `browse_schemas` / `browse_tables` / `browse_columns` | Discover live catalog metadata |
| `preview_table` | Sample rows for column value inspection |
| `create_semantic_model` | Create a top-level model container with datasets |
| `create_dataset` / `update_dataset` / `list_datasets` | Manage logical datasets within a model |
| `create_metric` / `update_metric` / `list_metrics` | Define measurable quantities |
| `create_relationship` / `update_relationship` | Connect datasets by shared keys |
| `get_semantic_model_readiness` | Validate model completeness |

These tools compose into a deterministic workflow: discover → model → dataset → fields →
relationships → metrics → readiness.

## Workflow Overview

Build semantic layer objects in this order:

```
1. Discover   — list datasources, browse schemas/tables/columns, preview rows
2. Model      — create_semantic_model (container + initial dataset)
3. Dataset    — add fields with dimensions and expressions
4. Relationship — connect datasets when metrics cross boundaries
5. Metrics    — create_metric for each measurable business concept
6. Validate   — get_semantic_model_readiness, then smoke-test with observe
```

Each step depends on the previous one. Do not create metrics before their dataset and
fields exist. Do not create relationships before both datasets exist.

## Step 1: Discover Source Metadata

Before writing any semantic object, inspect the live catalog to ground your model in real
schema metadata.

### 1a. List datasources

```
list_datasources → [{"datasource_id": "ds_abc123", "datasource_type": "trino", ...}]
```

Select the datasource that contains the target table. Record the `datasource_id` — every
dataset must carry it in the MARIVO extension.

### 1b. Browse schemas and tables

```
browse_schemas(datasource_id="ds_abc123")
browse_tables(datasource_id="ds_abc123", schema_name="iceberg_inf")
```

Identify the target table and note its column count. For large tables (>30 columns), plan
which subset of columns to expose as explicit fields — not every column needs a field
definition.

### 1c. Browse columns

```
browse_columns(datasource_id="ds_abc123", schema_name="iceberg_inf", table_name="my_table")
```

Categorize each column into one of:

| Category | Criteria | Treatment |
|----------|----------|-----------|
| **Identity** | Primary key, unique identifier | `primary_key` on dataset, field required |
| **Time dimension** | Timestamp, date, partition column | `dimension.is_time = true` |
| **Descriptor dimension** | Low-cardinality string for grouping/filtering | `dimension.is_time = false` |
| **Measure** | Numeric column used in metric aggregation | Field without `dimension` |
| **Auxiliary** | Rarely used in analysis (raw SQL, debug info) | Omit from field list |

### 1d. Preview rows (optional)

```
preview_table(datasource_id="ds_abc123", schema="iceberg_inf", table="my_table", limit=20)
```

Use preview when column metadata alone is ambiguous — e.g., to distinguish an ISO timestamp
varchar from a free-text varchar, or to understand cardinality of a dimension.

## Step 2: Create the Semantic Model

Use `create_semantic_model` to create the top-level container. You have two strategies:

### Strategy A: Model + dataset in one call

Include one dataset (with fields) in the `create_semantic_model` payload. This is useful
when the model contains a single fact table.

```json
{
  "name": "my_analytics",
  "description": "Analytics model for X",
  "datasets": [
    {
      "name": "my_fact",
      "source": "schema.table_name",
      "primary_key": ["id_column"],
      "custom_extensions": [
        {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_abc123"}}
      ],
      "fields": [
        {"name": "id_column", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "id_column"}]}},
        {"name": "event_time", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "event_time"}]}, "dimension": {"is_time": true}}
      ]
    }
  ]
}
```

### Strategy B: Minimal model, then add objects incrementally

Create the model with a minimal dataset, then use `create_dataset`, `create_metric`, and
`create_relationship` to build up the graph. This is **recommended for complex models**
because:

- Large single payloads may hit JSON validation limits on some MCP transports
- Incremental creation lets you validate each object independently
- Errors are easier to isolate and fix per-object

**Practical tip**: When `create_semantic_model` fails with a validation error on a large
payload, split it: create the model with a minimal dataset, then add metrics and additional
datasets via their individual create endpoints.

## Step 3: Define Dataset and Fields

The dataset is the physical grounding layer. Every downstream object references dataset and
field names — not physical table/column locators.

### 3a. Dataset structure

Required fields:
- `name` — logical identifier used by metrics and relationships
- `source` — the physical relation FQN (`schema.table_name`)

Required extension:
- `custom_extensions[0].data.datasource_id` — routes queries to the correct engine

Common optional fields:
- `primary_key` — array of field names forming the unique row identifier
- `unique_keys` — additional unique constraints
- `description`

### 3b. Field structure

Every field needs:
- `name` — logical identifier (used in metrics, relationships, observe dimensions)
- `expression` — `{"dialects": [{"dialect": "ANSI_SQL", "expression": "physical_column"}]}`

For dimensions, add:
- `dimension` — `{"is_time": true}` or `{"is_time": false}`

### 3c. Field design rules

**Time fields**: Mark at least one field with `dimension.is_time = true`. This is the
field that Marivo's observe/detect/diagnose intents use for time-scoped analysis. Common
patterns:

| Source column type | Field definition | Notes |
|--------------------|-----------------|-------|
| ISO timestamp varchar | `is_time: true` | Works for time comparison in most SQL engines |
| DATE column | `is_time: true` | Preferred when available |
| Partition column (e.g. `log_date`) | `is_time: true` | Efficient for partition pruning |
| Hour partition (e.g. `log_hour`) | `is_time: false` | Not a standalone time field, but a useful dimension |

**SQL reserved words**: When a physical column name collides with a SQL reserved word
(e.g., `schema`, `user`, `order`, `group`), rename the logical field and map the
expression:

```json
{
  "name": "query_schema",
  "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "schema"}]},
  "dimension": {"is_time": false}
}
```

**Computed fields**: Use SQL expressions when the semantic concept differs from the raw
column:

```json
{
  "name": "data_size_gb",
  "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "data_size_bytes / 1073741824.0"}]}
}
```

Keep computed expressions simple. If a field requires a subquery or window function,
consider whether the metric expression can handle it instead.

**Field selection**: Not every table column needs a field. Include columns that:
- Participate in at least one metric expression
- Serve as a grouping/filtering dimension for analysis
- Are part of the primary key or a relationship key

Omit columns that are only useful for raw debugging (e.g., full SQL text, stack traces,
large JSON blobs) unless there is a specific analytical need.

## Step 4: Create Relationships

Create a relationship when a metric or analysis needs fields from more than one dataset.

### Structure

```json
{
  "name": "orders_to_customers",
  "from": "orders",
  "to": "customers",
  "from_columns": ["customer_id"],
  "to_columns": ["customer_id"]
}
```

Rules:
- `from` is the many-side dataset, `to` is the one-side dataset
- Column lists must match in length and order
- Use dataset field names, not physical column names
- Do not embed join SQL, optimizer hints, or CTEs

When to create:
- A metric references fields from two or more datasets
- Two datasets share natural keys and join eligibility must be reusable
- Cardinality matters for correct metric aggregation

When NOT to create:
- Only one dataset exists in the model
- The metric can be expressed entirely within one dataset's fields

## Step 5: Create Metrics

Metrics define measurable business quantities. Each metric belongs to a model and
references dataset fields through SQL expressions.

### 5a. Metric structure

Required fields:
- `name` — unique identifier within the model
- `expression` — `{"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(query_id)"}]}`

Common optional fields:
- `description` — what the metric measures
- `custom_extensions[0].data.additive_dimensions` — which dimensions the metric is additive over

### 5b. Common metric expression patterns

| Metric type | Expression pattern | Use when |
|-------------|-------------------|----------|
| Count | `COUNT(column)` | Measuring volume, event counts |
| Distinct count | `COUNT(DISTINCT column)` | Measuring unique subjects |
| Sum | `SUM(column)` | Measuring totals (bytes, seconds, dollars) |
| Average | `AVG(column)` | Measuring central tendency (latency, size) |
| Rate/ratio | `CAST(SUM(filter) AS DOUBLE) / NULLIF(COUNT(x), 0)` | Measuring proportions (failure rate, hit rate) |
| Conditionally filtered sum | `SUM(CASE WHEN col = 'X' THEN 1 ELSE 0 END)` | Counting subsets (failed queries, premium users) |

### 5c. Unit conversion in metrics

When the raw column uses inconvenient units (bytes, milliseconds), apply conversion in the
metric expression rather than defining a computed field:

```json
{
  "name": "total_input_data_gb",
  "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(raw_input_data_size) / 1073741824.0"}]}
}
```

This keeps the field layer close to the physical schema and moves analytical semantics
into the metric where it belongs.

### 5d. Additive dimensions

The `additive_dimensions` extension tells Marivo which dimensions a metric can be
decomposed over. This affects `diagnose` and `decompose` behavior.

Set additive dimensions for:
- Time fields: `ctime`, `log_date`
- Grouping dimensions that the metric naturally splits by: `cluster`, `user`, `department`
- Any dimension used in the metric's GROUP BY equivalent

For non-additive metrics (averages, rates, distinct counts), set additive dimensions only
for the dimensions where the metric can be meaningfully summed across segments. Averages
are typically not additive over any dimension, but the rate numerator/denominator may be.

```json
{
  "name": "failed_query_rate",
  "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "CAST(SUM(CASE WHEN state = 'FAILED' THEN 1 ELSE 0 END) AS DOUBLE) / NULLIF(COUNT(query_id), 0)"}]},
  "custom_extensions": [
    {"vendor_name": "MARIVO", "data": {"additive_dimensions": ["ctime", "log_date", "cluster", "error_type"]}}
  ]
}
```

### 5e. Metric naming conventions

Choose names that are:
- Self-documenting: `avg_elapsed_time` not `aet`
- Unit-aware: `total_cpu_time` (seconds), `total_input_data_size_gb` (GB)
- Pattern-consistent: prefix with `avg_`, `total_`, `failed_`, `cache_` for grouping

## Step 6: Validate Readiness

After creating all objects, verify the model is usable:

```
get_semantic_model_readiness(model="my_analytics")
```

Expected response: `{"status": "ready", "blockers": []}`

### Common blockers

| Blocker code | Cause | Fix |
|--------------|-------|-----|
| `datasource_not_found` | Dataset references a deleted or mis-typed datasource_id | Update dataset's MARIVO extension |
| `relation_not_found` | `dataset.source` doesn't match any table in the datasource | Browse tables and update source FQN |
| `field_expression_invalid` | Field expression doesn't resolve against the table schema | Browse columns and fix the expression |
| `datasource_not_ready` | Datasource connection is failing | Check datasource configuration |

Fix blockers in order: datasource → relation → field. Downstream metric errors are often
caused by upstream dataset/field problems.

## Practical Tips

### Large payloads

When `create_semantic_model` rejects a large payload (JSON validation error on MCP
transport), split the work:

1. Create the model with one minimal dataset (identity + time fields only)
2. Add remaining fields via `update_dataset` or recreate the dataset
3. Add metrics one-by-one via `create_metric`
4. Add relationships via `create_relationship`

### Incremental metric creation

Create metrics in batches of 3-4 parallel calls rather than one giant call. This:

- Isolates errors to individual metrics
- Lets you verify each metric independently
- Avoids payload size limits

### Column selection heuristic

For a 50+ column table, include ~25-35 fields:
- All identity and time columns
- All columns used in metric expressions
- The 10-15 most useful grouping/filtering dimensions
- Omit: raw SQL text, stack traces, large JSON blobs, internal debug columns

### Multi-dialect expressions

When the datasource uses a dialect-specific syntax (e.g., Trino `AT TIME ZONE`), set the
dialect explicitly:

```json
{"dialects": [{"dialect": "ANSI_SQL", "expression": "CAST(create_time AS TIMESTAMP)"}]}
```

For Trino datasources, `ANSI_SQL` dialect expressions work for most standard operations.
Use `SNOWFLAKE` or `DATABRICKS` dialects only when the expression uses engine-specific
functions.

### Relationship cardinality

When creating relationships, the `from` side is the "many" side (fact table) and the `to`
side is the "one" side (dimension table). For example:

- `from: fact_orders` → `to: dim_customers` (many orders per customer)
- `from: fact_query_info` → `to: dim_users` (many queries per user)

If both sides are fact tables with no clear many-to-one, consider whether a relationship
is actually needed, or whether the metrics can be expressed within a single dataset.

## Full Example: Trino Query Analytics

This example models a Trino query info fact table for observability analysis.

### Source discovery

```
list_datasources → ds_858440257c50 (Trino Bilibili)
browse_tables(datasource_id="ds_858440257c50", schema_name="iceberg_inf")
  → dwd_olap_trino_query_info_i_hr (68 columns)
browse_columns(datasource_id="ds_858440257c50", schema_name="iceberg_inf",
               table_name="dwd_olap_trino_query_info_i_hr")
  → 68 columns with types and comments
```

### Column categorization

| Category | Columns |
|----------|---------|
| Identity | query_id |
| Time | ctime (采集时间), log_date (日期分区) |
| Time aux | log_hour (小时分区) |
| Dimensions | cluster, state, query_type, user, source, catalog, query_schema→schema, resource_group, sla, department, project_id, error_type, error_name, execution_failure_type |
| Measures | elapsed_time, queued_time, analysis_time, planning_time, execution_time, total_cpu_time, total_scheduled_time, total_blocked_time, total_drivers, raw_input_positions, raw_input_data_size, output_positions, output_datasize, peak_user_memory_reservation, peak_total_memory_reservation, physical_input_positions, physical_input_data_size, physical_input_cache_data_size, cumulative_user_memory, origin_total_cpu_time, origin_physical_input_data_size |
| Omitted | query (raw SQL), self (UI URL), md5, client_address, session_properties, client_name, queued_info, user_memory_reservation, total_memory_reservation, peak_task_total_memory, connector_metrics, columns_in_unenforced_predicate, dynamic_filters_stats, execution_failure_message, execution_failure_cause_message, warnings, inputs, job_id, project_history_id, job_history_id |

### Model creation (minimal first)

```
create_semantic_model({
  name: "trino_query_analytics",
  datasets: [{
    name: "trino_query_info",
    source: "iceberg_inf.dwd_olap_trino_query_info_i_hr",
    primary_key: ["query_id"],
    custom_extensions: [{"vendor_name": "MARIVO", "data": {"datasource_id": "ds_858440257c50"}}],
    fields: [/* 40 selected fields */]
  }]
})
```

### Metric creation (parallel batches)

Batch 1 — volume and latency:
```
create_metric(query_count,       COUNT(query_id))
create_metric(avg_elapsed_time,  AVG(elapsed_time))
create_metric(avg_execution_time, AVG(execution_time))
create_metric(avg_queued_time,   AVG(queued_time))
```

Batch 2 — resource and failure:
```
create_metric(total_cpu_time,           SUM(total_cpu_time))
create_metric(total_input_data_size_gb, SUM(raw_input_data_size)/1073741824.0)
create_metric(failed_query_count,       SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END))
create_metric(failed_query_rate,        CAST(SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END) AS DOUBLE) / NULLIF(COUNT(query_id), 0))
```

Batch 3 — memory and cache:
```
create_metric(avg_peak_memory_gb,       AVG(peak_total_memory_reservation)/1073741824.0)
create_metric(cache_hit_data_size_gb,   SUM(physical_input_cache_data_size)/1073741824.0)
create_metric(cache_savings_cpu_time,   SUM(origin_total_cpu_time))
```

### Readiness check

```
get_semantic_model_readiness(model="trino_query_analytics")
→ {"status": "ready", "blockers": []}
```

## Read Next

- Read `semantic-layer.md` for the older entity/dimension/predicate/metric.* HTTP contract surface.
- Read `payload-cheatsheet.md` for minimum useful request shapes for entity-based objects.
- Read `steps.md` for smoke-testing the model with observe/detect intents.
- Read `infrastructure.md` for datasource registration and connection troubleshooting.
