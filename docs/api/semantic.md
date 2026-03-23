# Semantic Layer

The semantic layer provides a catalog of named entities, metrics, and mappings that agents and analysts use instead of raw table names and SQL. Metrics are defined once with a SQL expression, linked to physical tables via mappings, and then referenced by name in `compare_metric` steps.

All semantic objects follow the lifecycle: `draft` → `published` → `deprecated`. Only `published` objects are available for step execution. Publishing increments the object's `revision`.

## Endpoints

### Entities

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/semantic/entities` | Create an entity |
| `GET` | `/semantic/entities` | List entities |
| `GET` | `/semantic/entities/{entity_id}` | Get an entity |
| `PUT` | `/semantic/entities/{entity_id}` | Update an entity |
| `POST` | `/semantic/entities/{entity_id}/publish` | Publish an entity |

### Metrics

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/semantic/metrics` | Create a metric |
| `GET` | `/semantic/metrics` | List metrics |
| `GET` | `/semantic/metrics/{metric_id}` | Get a metric |
| `PUT` | `/semantic/metrics/{metric_id}` | Update a metric |
| `POST` | `/semantic/metrics/{metric_id}/publish` | Publish a metric |

### Mappings

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/semantic/mappings` | Create a mapping |
| `GET` | `/semantic/mappings` | List mappings |
| `DELETE` | `/semantic/mappings/{mapping_id}` | Delete a mapping |

### Catalog & Discovery

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/catalog/search` | Search the semantic catalog |
| `GET` | `/semantic/resolve/{name}` | Resolve a semantic term by name |
| `GET` | `/sessions/{session_id}/planner-context` | Get planner context for a session |
| `GET` | `/catalog/graph` | Graph traversal from a root node |

---

## Entities

An **entity** represents a business object (user, video, content item) with identity keys, join constraints, and optional upstream dependencies. Entities are the subjects that metrics are measured over.

### Create Entity

```
POST /semantic/entities
```

**Request body:**

```json
{
  "name": "user",
  "display_name": "User",
  "description": "A registered platform user",
  "keys": ["user_id"],
  "level": "user",
  "join_constraints": {
    "min_sessions": 1
  },
  "upstream_dependencies": [],
  "lineage": ["events.user_sessions"],
  "quality_expectations": {
    "null_rate_threshold": 0.001
  },
  "properties": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique internal name (snake_case) |
| `display_name` | string | yes | Human-readable name |
| `description` | string | no | Purpose description (default: `""`) |
| `keys` | array[string] | yes | Primary key columns |
| `level` | string | no | Hierarchy level (e.g., `"user"`, `"session"`, `"event"`) |
| `join_constraints` | object | no | Conditions for joining this entity to other tables |
| `upstream_dependencies` | array[string] | no | Entity names this entity depends on |
| `lineage` | array[string] | no | Source table FQNs used to populate this entity |
| `quality_expectations` | object | no | Quality thresholds to check during sync |
| `properties` | object | no | Arbitrary metadata |

**Response:**

```json
{
  "entity_id": "ent_a1b2c3d4e5f6",
  "name": "user",
  "display_name": "User",
  "description": "A registered platform user",
  "keys": ["user_id"],
  "level": "user",
  "status": "draft",
  "revision": 0,
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

### List Entities

```
GET /semantic/entities
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `draft`, `published`, `deprecated` |

### Update Entity

```
PUT /semantic/entities/{entity_id}
```

Updates a `draft` entity. Updating a `published` entity creates a new draft revision.

Request body: same fields as Create Entity (all optional).

### Publish Entity

```
POST /semantic/entities/{entity_id}/publish
```

Transitions the entity to `published`. Increments `revision`. Once published, the entity is available for use in metrics and `compare_metric` steps.

**Response:** Entity object with `status: "published"` and incremented `revision`.

---

## Metrics

A **metric** is a named, reusable aggregation expression with a SQL definition, associated dimensions, and an optional entity. Published metrics are referenced by name in `compare_metric` steps.

### Create Metric

```
POST /semantic/metrics
```

**Request body:**

```json
{
  "name": "avg_watch_time_minutes",
  "display_name": "Average Watch Time (minutes)",
  "description": "Average video watch duration per user session, in minutes",
  "definition_sql": "AVG(watch_duration_sec) / 60.0",
  "dimensions": ["device_type", "region", "content_type"],
  "entity_id": "ent_...",
  "grain": "daily",
  "measure_type": "average",
  "allowed_dimensions": ["device_type", "region", "content_type", "age_group"],
  "lineage": ["events.user_video_watch"],
  "quality_expectations": {},
  "properties": {}
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique internal name (snake_case) |
| `display_name` | string | yes | Human-readable name |
| `description` | string | no | Description of what this metric measures |
| `definition_sql` | string | yes | SQL aggregate expression (e.g., `AVG(col) / 60.0`) |
| `dimensions` | array[string] | yes | Default breakdown dimensions |
| `entity_id` | string | no | Entity this metric belongs to |
| `grain` | string | no | Intended time grain: `"daily"`, `"weekly"`, `"monthly"` |
| `measure_type` | string | no | `"average"`, `"sum"`, `"count"`, `"ratio"`, `"percentile"` |
| `allowed_dimensions` | array[string] | no | Full set of valid breakdown dimensions (superset of `dimensions`) |
| `lineage` | array[string] | no | Source table FQNs |
| `quality_expectations` | object | no | Quality thresholds |
| `properties` | object | no | Arbitrary metadata |

**Response:**

```json
{
  "metric_id": "met_a1b2c3d4e5f6",
  "name": "avg_watch_time_minutes",
  "display_name": "Average Watch Time (minutes)",
  "definition_sql": "AVG(watch_duration_sec) / 60.0",
  "dimensions": ["device_type", "region", "content_type"],
  "entity_id": "ent_...",
  "grain": "daily",
  "measure_type": "average",
  "status": "draft",
  "revision": 0,
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

### List Metrics

```
GET /semantic/metrics
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `draft`, `published`, `deprecated` |

### Publish Metric

```
POST /semantic/metrics/{metric_id}/publish
```

Publishes the metric, making it available for step execution. Increments `revision`.

---

## Mappings

A **mapping** links a semantic object (entity or metric) to a physical source object (synced table). Mappings tell the QueryRouter where to find the data for a metric.

### Create Mapping

```
POST /semantic/mappings
```

**Request body:**

```json
{
  "semantic_type": "metric",
  "semantic_id": "met_a1b2c3d4e5f6",
  "object_id": "obj_a1b2c3d4e5f6",
  "mapping_type": "direct",
  "mapping_json": {
    "table_column": "watch_duration_sec",
    "time_column": "event_date",
    "partition_column": "event_date"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `semantic_type` | string | yes | `"entity"` or `"metric"` |
| `semantic_id` | string | yes | ID of the semantic entity or metric |
| `object_id` | string | yes | ID of the synced source object (table) |
| `mapping_type` | string | yes | How data is sourced: `"direct"`, `"view"`, `"derived"` |
| `mapping_json` | object | no | Adapter-specific mapping detail |

**Common `mapping_json` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `table_column` | string | Primary data column for this metric |
| `time_column` | string | Column used for time-window filtering |
| `partition_column` | string | Partition column (required for partitioned tables) |
| `filter` | string | Additional SQL filter to apply when reading this table |

**Response:**

```json
{
  "mapping_id": "map_a1b2c3d4e5f6",
  "semantic_type": "metric",
  "semantic_id": "met_...",
  "object_id": "obj_...",
  "mapping_type": "direct",
  "mapping_json": {...},
  "created_at": "2024-01-15T10:00:00+00:00"
}
```

### List Mappings

```
GET /semantic/mappings
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `semantic_type` | string | Filter by `"entity"` or `"metric"` |
| `semantic_id` | string | Filter by semantic object ID |

### Delete Mapping

```
DELETE /semantic/mappings/{mapping_id}
```

**Response:**

```json
{"status": "deleted", "mapping_id": "map_..."}
```

---

## Catalog Search

```
GET /catalog/search?q={query}
```

Full-text search across published entities, metrics, and source objects. Returns ranked matches.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | yes | Search query |
| `type` | string | no | Filter by type: `"entity"`, `"metric"`, `"table"` |

### Response

```json
[
  {
    "id": "met_...",
    "type": "metric",
    "name": "avg_watch_time_minutes",
    "display_name": "Average Watch Time (minutes)",
    "description": "Average video watch duration per user session, in minutes",
    "status": "published",
    "score": 0.95
  }
]
```

---

## Resolve Term

```
GET /semantic/resolve/{name}
```

Resolves a semantic term (entity or metric name) to its full definition, including the backing source object and engine information.

### Response

```json
{
  "resolved": true,
  "type": "metric",
  "metric": {
    "metric_id": "met_...",
    "name": "avg_watch_time_minutes",
    "definition_sql": "AVG(watch_duration_sec) / 60.0",
    "dimensions": ["device_type", "region"],
    "status": "published"
  },
  "source_object": {
    "object_id": "obj_...",
    "fqn": "events.user_video_watch",
    "source_id": "src_..."
  },
  "engine": {
    "engine_id": "eng_...",
    "engine_type": "duckdb"
  },
  "mapping": {
    "mapping_id": "map_...",
    "time_column": "event_date"
  }
}
```

---

## Planner Context

```
GET /sessions/{session_id}/planner-context
```

Returns a structured context object designed for LLM-based planners. Includes the session goal, constraints, available published metrics and entities, and source information.

### Response

```json
{
  "session_id": "sess_...",
  "goal": "Investigate watch time drop...",
  "constraints": {"platform": "mobile"},
  "budget": {"max_scan_bytes": 500000000000},
  "available_metrics": [
    {
      "name": "avg_watch_time_minutes",
      "display_name": "Average Watch Time (minutes)",
      "description": "...",
      "dimensions": ["device_type", "region"],
      "grain": "daily"
    }
  ],
  "available_entities": [...],
  "available_step_types": [
    "compare_metric",
    "profile_table",
    "sample_rows",
    "aggregate_query",
    "correlate_metrics",
    "synthesize_findings"
  ],
  "sources": [
    {
      "source_id": "src_...",
      "display_name": "Analytics DuckDB",
      "table_count": 12
    }
  ]
}
```

---

## Catalog Graph

```
GET /catalog/graph?root={name}&depth={depth}
```

Traverses the semantic graph starting from a root node, returning connected entities, metrics, and source objects up to the specified depth.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `root` | string | yes | Name of the starting entity or metric |
| `depth` | integer | no | Traversal depth, 1–5 (default: `2`) |

### Response

```json
{
  "root": "avg_watch_time_minutes",
  "nodes": [
    {"id": "met_...", "type": "metric", "name": "avg_watch_time_minutes"},
    {"id": "ent_...", "type": "entity", "name": "user"},
    {"id": "obj_...", "type": "table", "fqn": "events.user_video_watch"}
  ],
  "edges": [
    {
      "from": "met_...",
      "to": "ent_...",
      "relationship": "belongs_to_entity"
    },
    {
      "from": "met_...",
      "to": "obj_...",
      "relationship": "mapped_to_table"
    }
  ]
}
```
