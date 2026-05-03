# Semantic Layer

Marivo's current semantic grounding contract is dataset-native and OSI-aligned. The persisted
physical grounding source is the semantic model itself:

- `Dataset` selects the relation and datasource.
- `Field` selects physical columns or computed expressions.
- metrics, dimensions, predicates, and relationships reference datasets and fields.

There is no separate persisted physical binding layer in the current contract. Catalog metadata is
live and should be inspected through datasource browse endpoints before semantic authoring.

## Dataset-Native Physical Grounding

Use this shape for physical grounding:

| OSI field | Meaning |
|-----------|---------|
| `dataset.custom_extensions[].data.datasource_id` | Marivo datasource id that owns the relation |
| `dataset.source` | datasource-local relation FQN, usually `schema.table` or `catalog.schema.table` |
| `field.expression.dialects[]` | column name or computed SQL expression for a semantic field |

The datasource id lives in a MARIVO custom extension:

```json
{
  "vendor_name": "MARIVO",
  "data": "{\"datasource_id\":\"ds_a1b2c3d4e5f6\"}"
}
```

Use live datasource metadata to choose dataset and field values:

```text
GET /datasources/{datasource_id}/browse/schemas
GET /datasources/{datasource_id}/browse/tables?schema_name=...
GET /datasources/{datasource_id}/browse/columns?schema_name=...&table_name=...
GET /datasources/{datasource_id}/catalog/preview?schema=...&table=...
```

The browse endpoints do not persist catalog snapshots. They are inspection surfaces for authoring
and troubleshooting the semantic model.

## OSI Semantic Model Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/semantic-models` | Create one semantic model from an OSI `SemanticModel` payload |
| `GET` | `/semantic-models` | List semantic models visible to the requester |
| `POST` | `/semantic-models/import` | Import an OSI document as the latest semantic layer |
| `GET` | `/semantic-models/{model}` | Get one semantic model as an OSI document |
| `PUT` | `/semantic-models/{model}` | Update top-level semantic model fields |
| `DELETE` | `/semantic-models/{model}` | Delete a semantic model |
| `POST` | `/semantic-models/{model}/datasets` | Create a dataset |
| `GET` | `/semantic-models/{model}/datasets` | List datasets |
| `GET` | `/semantic-models/{model}/datasets/{name}` | Get a dataset |
| `PUT` | `/semantic-models/{model}/datasets/{name}` | Update dataset description |
| `DELETE` | `/semantic-models/{model}/datasets/{name}` | Delete a dataset |
| `POST` | `/semantic-models/{model}/relationships` | Create a relationship |
| `GET` | `/semantic-models/{model}/relationships` | List relationships |
| `GET` | `/semantic-models/{model}/relationships/{name}` | Get a relationship |
| `PUT` | `/semantic-models/{model}/relationships/{name}` | Replace a relationship |
| `DELETE` | `/semantic-models/{model}/relationships/{name}` | Delete a relationship |
| `POST` | `/semantic-models/{model}/metrics` | Create a metric |
| `GET` | `/semantic-models/{model}/metrics` | List metrics |
| `GET` | `/semantic-models/{model}/metrics/{name}` | Get a metric |
| `PUT` | `/semantic-models/{model}/metrics/{name}` | Replace a metric |
| `DELETE` | `/semantic-models/{model}/metrics/{name}` | Delete a metric |
| `GET` | `/semantic-models/{model}/readiness` | Evaluate semantic model readiness |

All OSI document responses use:

```json
{
  "version": "0.1.1",
  "semantic_model": []
}
```

## Ref And Naming Rules

Semantic model v2 uses OSI names rather than legacy typed refs for persisted object identity:

- semantic model: `semantic_model[].name`
- dataset: `datasets[].name`
- field: `datasets[].fields[].name`
- relationship: `relationships[].name`
- metric: `metrics[].name`

Use names that are stable and business-facing. Do not put datasource ids, engine ids, table ids, or
runtime-generated object ids into semantic names.

## Minimal Import Payload

```json
{
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
              "data": "{\"datasource_id\":\"ds_a1b2c3d4e5f6\"}"
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
}
```

## Dataset Contract

A dataset must include:

- `name`: stable model-local dataset name
- `source`: datasource-local relation FQN
- `custom_extensions` with MARIVO `datasource_id`
- `fields`: semantic fields backed by expressions

`source` accepts relation names that the datasource adapter can browse. Two-part
`schema.table` and three-part `catalog.schema.table` forms are supported by readiness checks.

Dataset primary keys and unique keys are semantic constraints. They should reference field names in
the same dataset.

## Field Contract

Each field has:

- `name`: model-local field name
- `expression.dialects[]`: one or more SQL dialect expressions
- optional `dimension.is_time`
- optional MARIVO field extension such as `data_type`

The simplest field expression is a physical column name:

```json
{
  "name": "customer_id",
  "expression": {
    "dialects": [
      {"dialect": "ANSI_SQL", "expression": "customer_id"}
    ]
  }
}
```

Computed fields are also represented through `field.expression`:

```json
{
  "name": "order_day",
  "expression": {
    "dialects": [
      {"dialect": "ANSI_SQL", "expression": "DATE(order_ts)"}
    ]
  },
  "dimension": {"is_time": true}
}
```

Keep expressions scoped to fields. Do not put physical table names, datasource ids, or routing
state inside metric, relationship, predicate, or dimension definitions.

## Metric Contract

Metrics are model-local measures over datasets and fields. A metric must include:

- `name`
- `expression.dialects[]`
- optional MARIVO metric extension

MARIVO metric extension fields include:

| Field | Meaning |
|-------|---------|
| `observed_dataset` | dataset name the metric observes |
| `observation_grain` | grain labels used by the metric |
| `primary_time_field` | field name used as the primary time axis |
| `additivity` | dimension/time additivity contract |
| `filters` | reusable metric filters with expression dialects |

Example:

```json
{
  "name": "orders",
  "expression": {
    "dialects": [
      {"dialect": "ANSI_SQL", "expression": "COUNT(DISTINCT order_id)"}
    ]
  },
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"observed_dataset\":\"orders\",\"observation_grain\":[\"day\"],\"primary_time_field\":\"order_date\",\"additivity\":{\"dimension_policy\":\"none\",\"time_axis_policy\":\"non_additive\"}}"
    }
  ]
}
```

## Relationship Contract

Relationships connect datasets by field names:

```json
{
  "name": "orders_to_customers",
  "from": "orders",
  "to": "customers",
  "from_columns": ["customer_id"],
  "to_columns": ["customer_id"],
  "custom_extensions": [
    {
      "vendor_name": "MARIVO",
      "data": "{\"cardinality\":\"many_to_one\"}"
    }
  ]
}
```

Relationships do not own physical join SQL. They describe semantic join eligibility between
dataset fields already grounded by their datasets.

## Readiness

Use:

```text
GET /semantic-models/{model}/readiness
```

Readiness combines stored semantic validation with live datasource checks. The response shape is:

```json
{
  "status": "not_ready",
  "semantic_version_id": null,
  "evaluated_semantic_version_id": null,
  "blockers": [
    {
      "code": "relation_not_found",
      "message": "Dataset orders source analytics.orders was not found in datasource ds_...",
      "dataset": "orders",
      "datasource_id": "ds_...",
      "source": "analytics.orders"
    }
  ]
}
```

Common blockers:

| Code | Meaning | Recovery |
|------|---------|----------|
| `datasource_not_found` | Dataset points at a missing datasource id | Create/select a datasource and put its id in the dataset MARIVO extension |
| `relation_not_found` | `dataset.source` cannot be resolved by live browse | Browse schemas/tables and update `dataset.source` to the live FQN |
| `field_expression_invalid` | A field expression cannot be compiled or resolved | Update `field.expression.dialects[]` for the target datasource dialect |
| `datasource_not_ready` | Datasource cannot currently browse or execute | Repair datasource connection/configuration |

## Validation Errors

Create and import routes validate OSI shape and MARIVO extension requirements. Typical failures:

| Symptom | Correct structure |
|---------|-------------------|
| dataset is missing datasource id | add a MARIVO dataset custom extension with `datasource_id` |
| dataset has an empty `source` | set `dataset.source` to the live relation FQN from browse |
| relationship references an unknown dataset | set `from` and `to` to dataset names in the same model |
| relationship references an unknown field | set `from_columns` and `to_columns` to field names in their datasets |
| metric references an unknown dataset or field in MARIVO extension data | update `observed_dataset`, `primary_time_field`, or filter expressions to match current datasets and fields |

Validation errors are returned as `422`. Ref conflicts and ownership errors are returned as
service-level validation errors rather than silently overwriting the current semantic model.

## Legacy Typed Object Routes

Older typed semantic routes may still exist for non-v2 compatibility in a checkout, but they are not
the dataset-native physical grounding contract. New authoring should use `/semantic-models` and
datasource live browse.

Do not introduce new physical grounding through carrier-owned or binding-owned surfaces. If an
analysis object needs physical data, add or update the relevant dataset and field definitions in the
OSI semantic model.
