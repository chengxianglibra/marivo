# Marivo Semantic-Layer Modeling Reference

Use this file when the task is to author or repair reusable semantic model documents with the
current stdio MCP tools.

Skip this file if the real problem is still datasource discovery or a session-scoped investigation.

## Tool Routing

| Need | Tool |
| --- | --- |
| Inspect source schemas, tables, columns, or previews | `marivo-datasource` |
| List stored semantic models | `marivo-list_semantic_models` |
| Read one stored model | `marivo-get_semantic_model` |
| Export stored document JSON | `marivo-export_osi_semantic_models` |
| Validate draft document JSON | `marivo-validate_osi_semantic_models` |
| Import approved document JSON | `marivo-import_osi_semantic_models` |

## OSI-Marivo Schema Reference

The canonical JSON Schema lives at:

```text
osi-marivo-spec/schema/osi-marivo.schema.json
```

Generated Python models live under:

```text
marivo/contracts/generated/
```

Use the schema and the examples below before drafting. Use validation feedback, especially
`json_pointer` and `hint`, to repair drafts.

## Start With Business Knowledge

Before you write or repair reusable semantic documents, ask for the user's business material first.
Prefer existing metric docs, KPI definitions, dashboard notes, field glossaries, reporting SQL, or
other written references over freeform guessing.

Extract and confirm at least:

- business entity or grain
- population and exclusions
- measure definition and aggregation rule
- time semantics, including which field owns the analysis window
- deduplication rule if counts can repeat
- required dimensions or relationship path
- one or two concrete positive or negative examples

If the user material is incomplete, pause and ask for the missing rule instead of inferring it from
column names alone.

## Preferred Build Order

1. Confirm datasource, schema, table, and source columns with `marivo-datasource`.
2. Collect business knowledge material and draft the semantic contract.
3. Get user approval on grain, population, measure, time semantics, and exclusions.
4. Draft the complete OSI-Marivo document.
5. Validate the draft.
6. Repair validation issues and revalidate.
7. Present the validated summary to the user for import approval.
8. Import the approved document.
9. Read or export the stored document before handing off to analysis.

## Contract Drafting Checklist

Use this checklist before adding a reusable metric to the document:

1. What business object does one row or one observation represent?
2. Who is included and excluded from the metric population?
3. What event, state, or amount is being measured?
4. What aggregation rule should the metric use?
5. Which time field controls the analysis window?
6. Does the metric need deduplication or a distinct rule?
7. Which dimensions are valid cuts of the metric?
8. Does the metric rely on a cross-dataset relationship? If so, what join path and cardinality are
   approved?

If any answer is still provisional, keep the document in draft form and do not import it.

## Complete Document Example

```json
{
  "version": "0.1.1",
  "semantic_model": [
    {
      "name": "video_analytics",
      "description": "Reusable video analytics model.",
      "datasets": [
        {
          "name": "watch_events",
          "source": "main.watch_events",
          "primary_key": ["event_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "datasource_id": "ds_local"
              }
            }
          ],
          "fields": [
            {
              "name": "event_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "event_id"
                  }
                ]
              }
            },
            {
              "name": "user_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "user_id"
                  }
                ]
              }
            },
            {
              "name": "event_time",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "event_time"
                  }
                ]
              },
              "dimension": {
                "is_time": true
              }
            },
            {
              "name": "watch_seconds",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "watch_seconds"
                  }
                ]
              }
            }
          ]
        },
        {
          "name": "users",
          "source": "main.users",
          "primary_key": ["user_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "datasource_id": "ds_local"
              }
            }
          ],
          "fields": [
            {
              "name": "user_id",
              "expression": {
                "dialects": [
                  {
                    "dialect": "ANSI_SQL",
                    "expression": "user_id"
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
          "name": "watch_events_to_users",
          "from": "watch_events",
          "to": "users",
          "from_columns": ["user_id"],
          "to_columns": ["user_id"],
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
          "name": "watch_time_seconds",
          "expression": {
            "dialects": [
              {
                "dialect": "ANSI_SQL",
                "expression": "SUM(watch_seconds)"
              }
            ]
          },
          "description": "Total watch time in seconds.",
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "observed_dataset": "watch_events",
                "primary_time_field": "event_time",
                "additive_dimensions": ["user_id"]
              }
            }
          ]
        }
      ]
    }
  ]
}
```

## Repair Rules

- business definition changed or was clarified: update the contract first, then repair the document
- datasource or relation changed: update `dataset.source` and datasource extension data
- field name or expression changed: update dataset fields before changing dependent metrics
- metric expression changed: update the metric and revalidate the whole document
- cross-dataset metric broke: inspect the relationship path before rewriting analysis steps
- validation reports a `json_pointer`: repair that document location first, then revalidate

## Common Mistakes

- creating large speculative graphs before confirming the live relation
- skipping business knowledge intake and modeling directly from source column names
- adding metrics that reference fields not present in the observed dataset
- hiding physical drift inside ad hoc metric SQL instead of fixing the dataset or relationship
- treating an unapproved draft as ready for formal investigation
