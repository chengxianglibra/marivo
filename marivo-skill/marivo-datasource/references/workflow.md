# Marivo Datasource Workflow Reference

Use this file when the task is about **datasource configuration and live metadata discovery** through
the current stdio MCP tools.

Skip this file if the datasource and target relation are already known and the next task is reusable
semantic modeling or session analysis.

## Tool Routing

| Need | Tool |
| --- | --- |
| Find existing datasources | `marivo-list_datasources` |
| Inspect one datasource | `marivo-get_datasource` |
| Create a datasource | `marivo-create_datasource` |
| Rename or revise a datasource | `marivo-update_datasource` |
| Remove an unused datasource | `marivo-delete_datasource` |
| List schemas | `marivo-browse_schemas` |
| List tables | `marivo-browse_tables` |
| Inspect columns | `marivo-browse_columns` |
| Preview sample rows | `marivo-preview_table` |

## Minimal Workflow

### 1. Confirm or create the datasource

Use the smallest valid create call that matches the current MCP surface:

```text
marivo-create_datasource(
  datasource_type="duckdb",
  display_name="Local DuckDB"
)
```

If your runtime exposes a concrete non-null `connection` shape, use that live tool guidance. Do not
copy request bodies from another surface into this skill.

### 2. Browse live metadata

```text
marivo-browse_schemas(datasource_id="ds_local")

marivo-browse_tables(
  datasource_id="ds_local",
  schema_name="main"
)

marivo-browse_columns(
  datasource_id="ds_local",
  schema_name="main",
  table_name="watch_events"
)
```

Use `schema_name` when you already know the schema. Leave it unset only when the question is still
"which schema should I use?"

### 3. Preview bounded rows only when needed

```text
marivo-preview_table(
  datasource_id="ds_local",
  schema="main",
  table="watch_events",
  limit=20
)
```

Optional narrowing:

```text
marivo-preview_table(
  datasource_id="ds_local",
  schema="main",
  table="watch_events",
  columns="event_id,event_time,platform,country",
  filters="{\"country\":\"US\"}"
)
```

## What "Done With Datasource Work" Looks Like

Stop in this skill and hand off to `marivo-semantic-layer` once you can name:

- the `datasource_id`
- the target schema and table
- candidate key fields
- candidate time field
- candidate measure or descriptor fields

## Common Mistakes

- creating a datasource and immediately writing semantic objects without browsing the live relation
- previewing huge tables when `marivo-browse_columns` already answers the question
- treating browse or preview output as session evidence
