# Quickstart

This guide walks through a complete end-to-end analysis workflow using the Factum API: registering a data source, publishing a metric, creating a session, and running analysis steps to produce evidence-backed recommendations.

## Prerequisites

- Factum service running at `http://localhost:8000`
- A DuckDB database with data to analyze

## Step 1 - Register a Source

Register the DuckDB database as a data source:

```bash
curl -s -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "duckdb",
    "display_name": "Analytics DB",
    "connection": {"db_path": "/data/analytics.duckdb"}
  }' | jq .
```

Save the returned `source_id`.

## Step 2 - Register an Engine

Register an analytics engine (can share the same DuckDB file):

```bash
curl -s -X POST http://localhost:8000/engines \
  -H "Content-Type: application/json" \
  -d '{
    "engine_type": "duckdb",
    "display_name": "DuckDB Engine",
    "connection": {"db_path": "/data/analytics.duckdb"}
  }' | jq .
```

Save the returned `engine_id`.

## Step 3 - Create a Binding

Link the source to the engine:

```bash
curl -s -X POST http://localhost:8000/bindings \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "src_...",
    "engine_id": "eng_...",
    "priority": 10
  }' | jq .
```

## Step 4 - Sync the Catalog

Trigger a catalog sync to snapshot the source's schemas and tables:

```bash
# Trigger sync
curl -s -X POST http://localhost:8000/sources/src_.../sync | jq .

# Poll for completion
curl -s http://localhost:8000/sources/src_.../sync/sync_... | jq .status
```

## Step 5 - Create a Metric

Define a semantic metric in draft status:

```bash
curl -s -X POST http://localhost:8000/semantic/metrics \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "metric_ref": "metric.avg_watch_time_minutes",
      "display_name": "Average Watch Time (minutes)",
      "description": "Average video watch duration per session",
      "metric_family": "average_metric",
      "observed_entity_ref": "entity.session",
      "observation_grain_ref": "grain.session",
      "sample_kind": "numeric",
      "value_semantics": "average",
      "additivity": "non_additive",
      "metric_contract_version": "metric.v1"
    },
    "payload": {
      "metric_family": "average_metric",
      "average_target": {
        "name": "watch_duration_sec",
        "semantics": "average watch duration",
        "aggregation": "average"
      }
    }
  }' | jq .
```

Save the returned `metric_contract_id`.

## Step 6 - Create a Typed Binding

Link the metric to the physical table with a typed binding (get the table FQN or `object_id` from Step 4 sync results):

```bash
curl -s -X POST http://localhost:8000/semantic/bindings \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "binding_ref": "binding.avg_watch_time_minutes_primary",
      "display_name": "Average Watch Time Binding",
      "binding_scope": "metric",
      "bound_object_ref": "metric.avg_watch_time_minutes",
      "binding_contract_version": "binding.v1"
    },
    "interface_contract": {
      "carrier_bindings": [
        {
          "binding_key": "primary",
          "carrier_kind": "table",
          "carrier_locator": "analytics.watch_events",
          "binding_role": "primary",
          "field_surfaces": [
            { "surface_ref": "field.watch_duration_sec", "physical_name": "watch_duration_sec" }
          ]
        }
      ],
      "field_bindings": [
        {
          "carrier_binding_key": "primary",
          "target": {
            "target_kind": "metric_input",
            "target_key": "measure.watch_duration_sec"
          },
          "semantic_ref": "measure.watch_duration_sec",
          "surface_ref": "field.watch_duration_sec"
        }
      ]
    }
  }' | jq .
```

## Step 7 - Publish the Metric

```bash
curl -s -X POST http://localhost:8000/semantic/metrics/metc_.../publish | jq .
curl -s -X POST http://localhost:8000/semantic/bindings/bind_.../publish | jq .
```

## Step 8 - Create a Session

```bash
curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "goal": {
      "question": "Investigate watch time drop in January 2024"
    },
    "governance": {
      "budget": {
        "max_scan_bytes": 100000000000,
        "max_latency_sec": 60
      },
      "warnings": [
        "Initial quickstart session"
      ]
    }
  }' | jq .
```

Save the returned `session_id`.

## Step 9 - Run Analysis Steps

The examples in this section use currently implemented step endpoints. For the target-state per-intent write contract, see [Intent Step Submission](intent-steps.md).

**Compare the metric:**

```bash
curl -s -X POST http://localhost:8000/sessions/sess_.../steps/metric_query \
  -H "Content-Type: application/json" \
  -d '{
    "table": "events.user_video_watch",
    "metric": "avg_watch_time_minutes",
    "dimensions": ["device_type"],
    "time_scope": {
      "mode": "compare",
      "grain": "day",
      "current": {
        "start": "2024-01-24",
        "end": "2024-01-31"
      },
      "baseline": {
        "start": "2024-01-17",
        "end": "2024-01-24"
      }
    },
    "scope": {
      "constraints": {"region": "us"}
    }
  }' | jq .
```

**Run an aggregate query:**

```bash
curl -s -X POST http://localhost:8000/sessions/sess_.../steps/aggregate_query \
  -H "Content-Type: application/json" \
  -d '{
    "table": "events.user_video_watch",
    "group_by": ["device_type", "os_version"],
    "measures": [
      {"expr": "AVG(watch_duration_sec)", "as": "avg_watch_sec"},
      {"expr": "COUNT(*)", "as": "session_count"}
    ],
    "time_scope": {
      "mode": "single_window",
      "grain": "day",
      "current": {
        "start": "2024-01-24",
        "end": "2024-01-31"
      }
    },
    "scope": {
      "predicate": "watch_duration_sec > 30"
    }
  }' | jq .
```

## Step 10 - Review Canonical State

```bash
curl -s http://localhost:8000/sessions/sess_.../state | jq .
```

---

## Next Steps

- [Intent Step Submission](intent-steps.md) - target-state per-intent step write contract
- [Session Lifecycle](session-lifecycle.md) - session root lifecycle contract
- [Session State Surface](session-state.md) - canonical session-level decision surface
- [Context Surface](context-surface.md) - canonical proposition-level minimal closure
- [Progressive OpenAPI Access](openapi.md) - path- and schema-focused contract retrieval
- [Semantic Layer](semantic.md) - entities, metrics, mappings, and catalog search
- [Governance](governance.md) - policies and quality rules
