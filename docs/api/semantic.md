# Semantic Layer

Marivo semantic authoring is document-first. Agents inspect live datasource metadata, draft a
complete OSI-Marivo JSON document, validate it, import it after user approval, and then use the
stored model for analysis.

Catalog metadata is live. Inspect datasource browse endpoints before writing semantic documents:

```text
GET /datasources/{datasource_id}/browse/schemas
GET /datasources/{datasource_id}/browse/tables?schema_name=...
GET /datasources/{datasource_id}/browse/columns?schema_name=...&table_name=...
GET /datasources/{datasource_id}/catalog/preview?schema=...&table=...
```

## Document Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/semantic-models` | List visible semantic models as an OSI-Marivo document. |
| `GET` | `/semantic-models/{model}` | Get one stored semantic model as an OSI-Marivo document. |
| `POST` | `/semantic-models/validate` | Validate a draft OSI-Marivo document without writing it. |
| `POST` | `/semantic-models/import` | Validate and import a complete OSI-Marivo document. |
| `GET` | `/semantic-models/export` | Export stored semantic model documents. |
| `DELETE` | `/semantic-models/{model}` | Delete the caller's private semantic model working copy. |

Authoring writes go through `/semantic-models/import`. The import document is the source of truth
for datasets, fields, metrics, and relationships. `DELETE /semantic-models/{model}` is the cleanup
path for removing the caller's private working copy during modeling; it never deletes official
public semantic models.

`POST /semantic-models/import`, `GET /semantic-models/export`, and
`DELETE /semantic-models/{model}` require the transport to set caller identity
with `X-Marivo-User`. List/get operations use the same identity to decide which
private models are visible to the caller.

## Authoring Loop

1. Browse the datasource to confirm schemas, tables, columns, and preview rows.
2. Draft a complete OSI-Marivo JSON document.
3. Call `POST /semantic-models/validate`.
4. Fix every validation issue using `json_pointer`, `message`, and `hint`.
5. Repeat validation until `valid` is `true`.
6. Show the validated summary to the user and wait for explicit approval.
7. Call `POST /semantic-models/import`.
8. Confirm stored state with `GET /semantic-models/{model}` or `GET /semantic-models/export`.
9. If the user wants to discard a private working copy, call `DELETE /semantic-models/{model}`.

`DELETE /semantic-models/{model}` returns `204` on success, `404` when the caller has no matching
private model, `403` when only an official public model has that name, and `422` when the transport
has not set a user identity.

## Document Shape

All OSI-Marivo documents use this envelope:

```json
{
  "version": "0.1.1",
  "semantic_model": []
}
```

Physical grounding lives in the semantic document:

| OSI field | Meaning |
| --- | --- |
| `dataset.custom_extensions[].data.datasource_id` | Marivo datasource id that owns the relation. |
| `dataset.source` | Datasource-local relation FQN, usually `schema.table` or `catalog.schema.table`. |
| `field.expression.dialects[]` | Column name or computed SQL expression for a semantic field. |

## Complete Document Example

```json
{
  "version": "0.1.1",
  "semantic_model": [
    {
      "name": "commerce",
      "description": "Commerce analytics model",
      "datasets": [
        {
          "name": "orders",
          "source": "analytics.orders",
          "primary_key": ["order_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "datasource_id": "ds_a1b2c3d4e5f6"
              }
            }
          ],
          "fields": [
            {
              "name": "order_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "order_id"
                  }
                ]
              }
            },
            {
              "name": "order_date",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "order_date"
                  }
                ]
              },
              "dimension": {
                "is_time": true
              },
              "custom_extensions": [
                {
                  "vendor_name": "MARIVO",
                  "data": {
                    "data_type": "date"
                  }
                }
              ]
            },
            {
              "name": "customer_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "customer_id"
                  }
                ]
              }
            },
            {
              "name": "amount",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "amount"
                  }
                ]
              },
              "custom_extensions": [
                {
                  "vendor_name": "MARIVO",
                  "data": {
                    "data_type": "number"
                  }
                }
              ]
            }
          ]
        },
        {
          "name": "customers",
          "source": "analytics.customers",
          "primary_key": ["customer_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "datasource_id": "ds_a1b2c3d4e5f6"
              }
            }
          ],
          "fields": [
            {
              "name": "customer_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "customer_id"
                  }
                ]
              }
            },
            {
              "name": "country",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "country"
                  }
                ]
              }
            }
          ]
        }
      ],
      "relationships": [
        {
          "name": "orders_to_customers",
          "from": "orders",
          "to": "customers",
          "from_columns": ["customer_id"],
          "to_columns": ["customer_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "cardinality": "many_to_one"
              }
            }
          ]
        }
      ],
      "metrics": [
        {
          "name": "order_revenue",
          "description": "Total order revenue.",
          "expression": {
            "dialects": [
              {
                "dialect": "ANSI_SQL",
                "expression": "SUM(amount)"
              }
            ]
          },
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "observed_dataset": "orders",
                "observation_grain": ["day"],
                "primary_time_field": "order_date",
                "additive_dimensions": ["customer_id"],
                "additivity": {
                  "dimension_policy": "all",
                  "time_axis_policy": "additive"
                }
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## Validation Response

`POST /semantic-models/validate` and failed imports return structured validation results:

```json
{
  "valid": false,
  "schema_version": "0.1.1",
  "errors": [
    {
      "code": "UNKNOWN_FIELD",
      "message": "Metric order_revenue references missing field amount.",
      "json_pointer": "/semantic_model/0/metrics/0/custom_extensions/0/data",
      "severity": "error",
      "hint": "Add the field to the observed dataset or update the metric expression.",
      "context": {
        "model": "commerce",
        "metric": "order_revenue"
      }
    }
  ],
  "warnings": [],
  "summary": {
    "models": 1,
    "datasets": 2,
    "fields": 6,
    "metrics": 1,
    "relationships": 1
  }
}
```

Typical validation failures:

| Symptom | Fix |
| --- | --- |
| Dataset is missing datasource id. | Add a MARIVO dataset custom extension with `datasource_id`. |
| Dataset has an empty `source`. | Set `dataset.source` to the live relation FQN from browse. |
| Relationship references an unknown dataset. | Set `from` and `to` to dataset names in the same model. |
| Relationship references an unknown field. | Set `from_columns` and `to_columns` to field names in their datasets. |
| Metric references an unknown dataset or field. | Update metric extension fields and expressions to match current datasets and fields. |

## Naming Rules

Use OSI names for persisted semantic identity:

- semantic model: `semantic_model[].name`
- dataset: `datasets[].name`
- field: `datasets[].fields[].name`
- relationship: `relationships[].name`
- metric: `metrics[].name`

Names should be stable and business-facing. Do not put datasource ids, engine ids, table ids, or
runtime-generated object ids into semantic names.
