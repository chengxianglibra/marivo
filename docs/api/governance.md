# Governance

Governance enforces data access policies and quality rules across all analysis sessions. Policies restrict what queries can be run (e.g., aggregate-only access, field masking, row filtering, row count limits). Quality rules assert expectations about data freshness, null rates, and minimum row counts.

## Endpoints

### Policies

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/policies` | Create a policy |
| `GET` | `/policies` | List policies |
| `GET` | `/policies/{policy_id}` | Get a policy |
| `PUT` | `/policies/{policy_id}` | Update a policy |
| `DELETE` | `/policies/{policy_id}` | Delete a policy |

### Quality Rules

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/quality-rules` | Create a quality rule |
| `GET` | `/quality-rules` | List quality rules |
| `DELETE` | `/quality-rules/{rule_id}` | Delete a quality rule |

### Governance Check

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/governance/check` | Check a step against active policies |

---

## Policies

Policies control what data operations are permitted. They are applied globally (or scoped to specific tables/sources) during step validation.

### Policy Types

| Type | Description |
|------|-------------|
| `aggregate_only` | Disallow row-level queries; all queries must aggregate |
| `field_mask` | Mask specified columns in query results |
| `row_filter` | Inject a mandatory WHERE clause into all queries |
| `max_rows` | Limit result set size |

---

### Create Policy

```
POST /policies
```

**Request body:**

```json
{
  "name": "no_raw_pii",
  "policy_type": "aggregate_only",
  "definition": {
    "min_group_size": 100
  },
  "scope": {
    "tables": ["events.user_video_watch"]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique policy name |
| `policy_type` | string | yes | See policy types above |
| `definition` | object | no | Policy-specific configuration |
| `scope` | object | no | Restriction scope (default: global) |

**Definition by policy type:**

**`aggregate_only`:**
```json
{
  "min_group_size": 100
}
```

**`field_mask`:**
```json
{
  "columns": ["email", "phone_number"],
  "mask_value": "***"
}
```

**`row_filter`:**
```json
{
  "filter_expr": "region != 'EU'",
  "reason": "GDPR data residency"
}
```

**`max_rows`:**
```json
{
  "limit": 10000
}
```

**Scope fields:**

| Field | Type | Description |
|-------|------|-------------|
| `tables` | array[string] | Apply only to these table FQNs |
| `sources` | array[string] | Apply to all tables in these source IDs |
| `step_types` | array[string] | Apply only to these step types |

**Response:**

```json
{
  "policy_id": "pol_a1b2c3d4e5f6",
  "name": "no_raw_pii",
  "policy_type": "aggregate_only",
  "definition": {"min_group_size": 100},
  "scope": {"tables": ["events.user_video_watch"]},
  "enabled": true,
  "created_at": "2024-01-15T10:00:00+00:00",
  "updated_at": "2024-01-15T10:00:00+00:00"
}
```

---

### List Policies

```
GET /policies
```

Returns all policies (enabled and disabled).

---

### Get Policy

```
GET /policies/{policy_id}
```

---

### Update Policy

```
PUT /policies/{policy_id}
```

Request body: same as Create Policy (all fields optional). Can also toggle `enabled`.

```json
{
  "enabled": false
}
```

---

### Delete Policy

```
DELETE /policies/{policy_id}
```

**Response:**

```json
{"status": "deleted", "policy_id": "pol_..."}
```

---

## Quality Rules

Quality rules assert expectations about data freshness and completeness. They are checked during catalog sync and can generate warnings or block sync completion.

### Rule Types

| Type | Description |
|------|-------------|
| `freshness` | Assert that the table was updated within a specified interval |
| `null_rate` | Assert that a column's null rate is below a threshold |
| `row_count_min` | Assert that the table has at least N rows |

---

### Create Quality Rule

```
POST /quality-rules
```

**Request body:**

```json
{
  "name": "watch_events_freshness",
  "rule_type": "freshness",
  "table_name": "events.user_video_watch",
  "threshold": {
    "max_age_hours": 24
  },
  "severity": "warn"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique rule name |
| `rule_type` | string | yes | See rule types above |
| `table_name` | string | yes | Fully qualified table name |
| `threshold` | object | yes | Rule-specific threshold configuration |
| `severity` | string | no | `"warn"` or `"error"` (default: `"warn"`) |

**Threshold by rule type:**

**`freshness`:**
```json
{
  "max_age_hours": 24
}
```

**`null_rate`:**
```json
{
  "column": "user_id",
  "max_null_rate": 0.01
}
```

**`row_count_min`:**
```json
{
  "min_rows": 1000000
}
```

**Response:**

```json
{
  "rule_id": "qr_a1b2c3d4e5f6",
  "name": "watch_events_freshness",
  "rule_type": "freshness",
  "table_name": "events.user_video_watch",
  "threshold": {"max_age_hours": 24},
  "severity": "warn",
  "enabled": true,
  "created_at": "2024-01-15T10:00:00+00:00"
}
```

---

### List Quality Rules

```
GET /quality-rules
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `table` | string | Filter by table name |

---

### Delete Quality Rule

```
DELETE /quality-rules/{rule_id}
```

**Response:**

```json
{"status": "deleted", "rule_id": "qr_..."}
```

---

## Governance Check

```
POST /governance/check
```

Checks a proposed step against all active, in-scope policies. Used internally by plan validation and step execution; also available directly for pre-flight checks.

### Request Body

```json
{
  "session_id": "sess_...",
  "step_type": "sample_rows",
  "params": {
    "table_name": "events.user_video_watch",
    "limit": 500
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | Session context for policy evaluation |
| `step_type` | string | yes | Proposed step type |
| `params` | object | no | Proposed step parameters |

### Response

```json
{
  "passed": false,
  "violations": [
    {
      "policy_id": "pol_...",
      "policy_name": "no_raw_pii",
      "policy_type": "aggregate_only",
      "message": "Step type 'sample_rows' is disallowed by aggregate_only policy on table events.user_video_watch"
    }
  ],
  "warnings": []
}
```

When passed:

```json
{
  "passed": true,
  "violations": [],
  "warnings": [
    {
      "policy_id": "pol_...",
      "policy_name": "max_rows",
      "message": "Result will be capped at 10000 rows by policy"
    }
  ]
}
```

**Violations** block step execution. **Warnings** allow execution but are surfaced for awareness.
