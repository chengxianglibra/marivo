# Quickstart

This guide walks through a minimal Marivo workflow: register a datasource, inspect the live catalog,
publish dataset-native semantic grounding, create a session, and run an analysis step.

## Prerequisites

- Marivo service running at `http://localhost:8000`
- A DuckDB database with an `analytics.orders` table

## Step 1 - Register a Datasource

```bash
curl -s -X POST http://localhost:8000/datasources \
  -H "Content-Type: application/json" \
  -d '{
    "datasource_type": "duckdb",
    "display_name": "Analytics DB",
    "connection": {"path": "/data/analytics.duckdb"},
    "policy": {
      "allow_live_browse": true,
      "allow_identity_reuse": false
    }
  }' | jq .
```

Save the returned `datasource_id`.

## Step 2 - Browse The Live Catalog

List schemas:

```bash
curl -s "http://localhost:8000/datasources/ds_.../browse/schemas" | jq .
```

List tables:

```bash
curl -s "http://localhost:8000/datasources/ds_.../browse/tables?schema_name=analytics" | jq .
```

List columns:

```bash
curl -s "http://localhost:8000/datasources/ds_.../browse/columns?schema_name=analytics&table_name=orders" | jq .
```

Use the live browse output to choose:

- `dataset.source`: `analytics.orders`
- `field.expression`: physical columns such as `order_id`, `order_date`, and `amount`

## Step 3 - Import A Dataset-Native Semantic Model

Dataset and Field are the only persisted physical grounding contract:

- `dataset.custom_extensions[].data.datasource_id` selects the datasource
- `dataset.source` names the datasource-local relation FQN
- `field.expression` names a physical column or computed SQL expression
- metrics, dimensions, predicates, and relationships reference datasets and fields

```bash
curl -s -X POST http://localhost:8000/semantic-models/import \
  -H "Content-Type: application/json" \
  -d '{
    "version": "0.1.1",
    "semantic_model": [
      {
        "name": "commerce",
        "description": "Commerce analytics model",
        "custom_extensions": [
          {
            "vendor_name": "MARIVO",
            "data": "{\"visibility\":\"private\",\"owner_user\":\"alice\"}"
          }
        ],
        "datasets": [
          {
            "name": "orders",
            "source": "analytics.orders",
            "primary_key": ["order_id"],
            "custom_extensions": [
              {
                "vendor_name": "MARIVO",
                "data": "{\"datasource_id\":\"ds_...\"}"
              }
            ],
            "fields": [
              {
                "name": "order_id",
                "expression": {
                  "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "order_id"}
                  ]
                }
              },
              {
                "name": "order_date",
                "expression": {
                  "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "order_date"}
                  ]
                },
                "dimension": {"is_time": true},
                "custom_extensions": [
                  {
                    "vendor_name": "MARIVO",
                    "data": "{\"data_type\":\"date\"}"
                  }
                ]
              },
              {
                "name": "amount",
                "expression": {
                  "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "amount"}
                  ]
                },
                "custom_extensions": [
                  {
                    "vendor_name": "MARIVO",
                    "data": "{\"data_type\":\"number\"}"
                  }
                ]
              }
            ]
          }
        ],
        "metrics": [
          {
            "name": "order_revenue",
            "expression": {
              "dialects": [
                {"dialect": "ANSI_SQL", "expression": "SUM(amount)"}
              ]
            },
            "custom_extensions": [
              {
                "vendor_name": "MARIVO",
                "data": "{\"observed_dataset\":\"orders\",\"observation_grain\":[\"day\"],\"primary_time_field\":\"order_date\",\"additivity\":{\"dimension_policy\":\"all\",\"time_axis_policy\":\"additive\"}}"
              }
            ]
          }
        ]
      }
    ]
  }' | jq .
```

## Step 4 - Check Semantic Readiness

```bash
curl -s http://localhost:8000/semantic-models/commerce/readiness | jq .
```

Common readiness blockers:

| Code | Recovery |
|------|----------|
| `datasource_not_found` | Create/select a datasource and put its id in the dataset MARIVO extension |
| `relation_not_found` | Browse schemas/tables and update `dataset.source` to the live FQN |
| `field_expression_invalid` | Update `field.expression.dialects[]` for the target datasource dialect |

## Step 5 - Preview The Grounded Dataset

```bash
curl -s "http://localhost:8000/datasources/ds_.../catalog/preview?schema=analytics&table=orders&limit=20" | jq .
```

## Step 6 - Create a Session

```bash
curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "goal": {
      "question": "Investigate revenue movement in January 2026"
    }
  }' | jq .
```

Save the returned `session_id`.

## Step 7 - Run Analysis Steps

The examples in this section use currently implemented step endpoints. For the target-state
per-intent write contract, see [Intent Step Submission](intent-steps.md).

```bash
curl -s -X POST http://localhost:8000/sessions/sess_.../steps/metric_query \
  -H "Content-Type: application/json" \
  -d '{
    "table": "analytics.orders",
    "metric": "order_revenue",
    "dimensions": ["order_date"],
    "time_scope": {
      "mode": "compare",
      "grain": "day",
      "current": {
        "start": "2026-01-01",
        "end": "2026-02-01"
      },
      "baseline": {
        "start": "2025-01-01",
        "end": "2025-02-01"
      }
    }
  }' | jq .
```

Record returned artifact and finding refs for follow-up state/context reads.

## Step 8 - Read Session Evidence

```bash
curl -s http://localhost:8000/sessions/sess_.../state | jq .
```

Use evidence refs from session state and proposition context as the durable output surface. Live
catalog browse is for grounding and inspection, not for replacing session evidence.
