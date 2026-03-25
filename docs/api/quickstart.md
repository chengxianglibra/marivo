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
    "name": "avg_watch_time_minutes",
    "display_name": "Average Watch Time (minutes)",
    "description": "Average video watch duration per session",
    "definition_sql": "AVG(watch_duration_sec) / 60.0",
    "dimensions": ["device_type", "region"]
  }' | jq .
```

Save the returned `metric_id`.

## Step 6 - Map the Metric to a Table

Link the metric to the physical table (get the `object_id` from Step 4 sync results):

```bash
curl -s -X POST http://localhost:8000/semantic/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "semantic_type": "metric",
    "semantic_id": "met_...",
    "object_id": "obj_...",
    "mapping_type": "direct",
    "mapping_json": {
      "time_column": "event_date"
    }
  }' | jq .
```

## Step 7 - Publish the Metric

```bash
curl -s -X POST http://localhost:8000/semantic/metrics/met_.../publish | jq .
```

## Step 8 - Create a Session

```bash
curl -s -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Investigate watch time drop in January 2024",
    "constraints": {"platform": "mobile"},
    "budget": {"max_scan_bytes": 100000000000, "max_latency_sec": 60}
  }' | jq .
```

Save the returned `session_id`.

## Step 9 - Run Analysis Steps

**Compare the metric:**

```bash
curl -s -X POST http://localhost:8000/sessions/sess_.../steps/compare_metric \
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

**Synthesize findings:**

```bash
curl -s -X POST http://localhost:8000/sessions/sess_.../steps/synthesize_findings \
  -H "Content-Type: application/json" \
  -d '{}' | jq .
```

## Step 10 - Review the Evidence Graph

```bash
curl -s http://localhost:8000/sessions/sess_.../evidence | jq '{
  observations: (.observations | length),
  claims: (.claims | length),
  recommendations: (.recommendations | length)
}'
```

---

## Using Plans for Structured Workflows

Instead of running steps individually, you can define a plan:

```bash
# Draft the plan
PLAN=$(curl -s -X POST http://localhost:8000/sessions/sess_.../plans \
  -H "Content-Type: application/json" \
  -d '{
    "steps": [
      {
        "step_id": "s1",
        "step_type": "compare_metric",
        "params": {
          "table": "events.user_video_watch",
          "metric": "avg_watch_time_minutes",
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
          }
        },
        "depends_on": []
      },
      {
        "step_id": "s2",
        "step_type": "synthesize_findings",
        "params": {},
        "depends_on": ["s1"]
      }
    ]
  }')

PLAN_ID=$(echo $PLAN | jq -r .plan_id)

# Validate (auto-approves if clean)
curl -s -X POST http://localhost:8000/sessions/sess_.../plans/$PLAN_ID/validate | jq .

# Execute
curl -s -X POST http://localhost:8000/sessions/sess_.../plans/$PLAN_ID/execute \
  -H "Content-Type: application/json" \
  -d '{"continue_on_failure": false}' | jq .
```

---

## Next Steps

- [Sessions & Steps](sessions.md) - full step type reference
- [Semantic Layer](semantic.md) - entities, metrics, mappings, and catalog search
- [Governance](governance.md) - policies and quality rules
- [Planning](planning.md) - multi-step plans with validation and cost estimation
