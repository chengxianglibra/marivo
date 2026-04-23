# Quickstart

This guide walks through a complete end-to-end analysis workflow using the Marivo API: registering a data source, publishing a metric, creating a session, and running analysis steps to produce evidence-backed recommendations.

## Prerequisites

- Marivo service running at `http://localhost:8000`
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

## Step 3 - Create a Mapping

Link the source to the engine with an explicit authority-to-execution mapping:

```bash
curl -s -X POST http://localhost:8000/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "src_...",
    "engine_id": "eng_...",
    "priority": 10,
    "catalog_mappings": [
      {
        "authority_catalog": "main",
        "execution_catalog": "main"
      }
    ]
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

## Step 5 - Create the Semantic Closure

Build a minimal reusable semantic layer before running analysis. This example uses one `time`,
one `entity`, one `metric`, and one typed `binding`.

Create a time semantic:

```bash
curl -s -X POST http://localhost:8000/semantic/time \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "time_ref": "time.watch_event_date",
      "display_name": "Watch Event Date",
      "semantic_roles": ["measurement"],
      "time_contract_version": "time.v1"
    }
  }' | jq .
```

Create an entity:

```bash
curl -s -X POST http://localhost:8000/semantic/entities \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "entity_ref": "entity.user",
      "display_name": "User",
      "entity_contract_version": "entity.v1"
    },
    "interface_contract": {
      "identity": {
        "key_refs": ["key.user_id"],
        "uniqueness_scope": "global",
        "id_stability": "stable"
      },
      "primary_time_ref": "time.watch_event_date"
    }
  }' | jq .
```

Create a metric:

```bash
curl -s -X POST http://localhost:8000/semantic/metrics \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "metric_ref": "metric.daily_active_users",
      "display_name": "Daily Active Users",
      "metric_family": "count_metric",
      "observed_entity_ref": "entity.user",
      "observation_grain_ref": "grain.day",
      "sample_kind": "numeric",
      "value_semantics": "count",
      "aggregation_scope": "window",
      "primary_time_ref": "time.watch_event_date",
      "additivity_constraints": {
        "dimension_policy": "none",
        "time_axis_policy": "non_additive"
      },
      "metric_contract_version": "metric.v1"
    },
    "payload": {
      "metric_family": "count_metric",
      "count_target": {
        "name": "active_users",
        "semantics": "Distinct active users",
        "aggregation": "count_distinct"
      }
    }
  }' | jq .
```

Save the returned `time_contract_id`, `entity_contract_id`, and `metric_contract_id`.

## Step 6 - Create a Typed Binding

Link the metric to the physical table with a typed binding. Use the table FQN or `object_id` from
Step 4 sync results:

```bash
curl -s -X POST http://localhost:8000/semantic/bindings \
  -H "Content-Type: application/json" \
  -d '{
    "header": {
      "binding_ref": "binding.daily_active_users_primary",
      "display_name": "Daily Active Users Binding",
      "binding_scope": "metric",
      "bound_object_ref": "metric.daily_active_users",
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
            { "surface_ref": "field.user_id", "physical_name": "user_id" },
            { "surface_ref": "field.event_date", "physical_name": "event_date" }
          ]
        }
      ],
      "field_bindings": [
        {
          "carrier_binding_key": "primary",
          "target": {
            "target_kind": "primary_time",
            "target_key": "time.watch_event_date"
          },
          "semantic_ref": "time.watch_event_date",
          "surface_ref": "field.event_date"
        },
        {
          "carrier_binding_key": "primary",
          "target": {
            "target_kind": "metric_input",
            "target_key": "count_target"
          },
          "semantic_ref": "metric_input.active_users",
          "surface_ref": "field.user_id"
        }
      ]
    }
  }' | jq .
```

## Step 7 - Publish the Semantic Closure

```bash
curl -s -X POST http://localhost:8000/semantic/time/timec_.../publish | jq .
curl -s -X POST http://localhost:8000/semantic/entities/entc_.../publish | jq .
curl -s -X POST http://localhost:8000/semantic/metrics/metc_.../publish | jq .
curl -s -X POST http://localhost:8000/semantic/bindings/bind_.../publish | jq .
```

Verify that runtime resolution sees only published typed refs:

```bash
curl -s http://localhost:8000/semantic/resolve/metric.daily_active_users | jq .
```

Metric bindings use family slot names for `target.target_key`. Common values are `count_target`,
`measure`, `numerator`, `denominator`, `value_component`, and `score_source`. Do not use a
`metric_input.*` ref as `target_key`; that belongs in `semantic_ref`.

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
- [Mappings](mappings.md) - minimal source-to-engine projection write/read surface
- [Semantic Layer](semantic.md) - entities, metrics, mappings, and catalog search
- [Governance](governance.md) - policies and quality rules
