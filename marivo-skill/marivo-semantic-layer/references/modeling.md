# Marivo Semantic-Layer Modeling Reference

Use this file when the task is to **author or repair reusable semantic objects** with the current
stdio MCP tools.

Skip this file if the real problem is still datasource discovery or a session-scoped investigation.

## Tool Routing

| Need | Tool |
| --- | --- |
| Create the top-level model | `marivo-create_semantic_model` |
| Read one model | `marivo-get_semantic_model` |
| List models | `marivo-list_semantic_models` |
| Add a dataset | `marivo-create_dataset` |
| Read or list datasets | `marivo-get_dataset`, `marivo-list_datasets` |
| Add a metric | `marivo-create_metric` |
| Read or list metrics | `marivo-get_metric`, `marivo-list_metrics` |
| Add a relationship | `marivo-create_relationship` |
| Read or list relationships | `marivo-get_relationship`, `marivo-list_relationships` |

## Preferred Build Order

1. confirm datasource, schema, table, and source columns with `marivo-datasource`
2. create the semantic model and first dataset
3. define fields on the dataset
4. add metrics and relationships that consume those fields
5. check readiness

## Minimal Model Example

```text
marivo-create_semantic_model(
  payload={
    "name": "video_analytics",
    "datasets": [
      {
        "name": "watch_events",
        "source": "main.watch_events",
        "primary_key": ["event_id"],
        "custom_extensions": [
          {
            "vendor_name": "MARIVO",
            "data": {"datasource_id": "ds_local"}
          }
        ],
        "fields": [
          {
            "name": "event_id",
            "expression": {
              "dialects": [
                {"dialect": "ANSI_SQL", "expression": "event_id"}
              ]
            }
          },
          {
            "name": "event_time",
            "expression": {
              "dialects": [
                {"dialect": "ANSI_SQL", "expression": "event_time"}
              ]
            },
            "dimension": {"is_time": true}
          },
          {
            "name": "watch_seconds",
            "expression": {
              "dialects": [
                {"dialect": "ANSI_SQL", "expression": "watch_seconds"}
              ]
            }
          }
        ]
      }
    ]
  }
)
```

Use `marivo-create_dataset` instead when the model already exists and you are extending it.

## Minimal Metric Example

```text
marivo-create_metric(
  model="video_analytics",
  payload={
    "name": "watch_time_seconds",
    "expression": {
      "dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(watch_seconds)"}
      ]
    },
    "description": "Total watch time in seconds"
  }
)
```

## Minimal Relationship Example

```text
marivo-create_relationship(
  model="video_analytics",
  payload={
    "name": "watch_events_to_users",
    "from": "watch_events",
    "to": "users",
    "from_columns": ["user_id"],
    "to_columns": ["user_id"]
  }
)
```

Create a relationship only when a reusable cross-dataset join is needed. Do not embed raw join SQL
or engine-specific hints into the relationship contract.

## Repair Rules

- datasource or relation changed: update the dataset first
- field name or expression changed: update the dataset fields before touching dependent metrics
- metric expression changed: update the metric, not the datasource metadata
- cross-dataset metric broke: inspect or repair the relationship before rewriting analysis steps

## Common Mistakes

- creating large speculative graphs before confirming the live relation
- adding downstream objects that reference fields not yet defined on the dataset
- hiding physical drift inside ad hoc metric SQL instead of fixing the dataset or relationship
