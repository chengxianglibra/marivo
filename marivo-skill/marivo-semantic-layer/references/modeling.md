# Marivo Semantic-Layer Modeling Reference

Use this file when the task is to author or repair reusable semantic model documents with the
current stdio MCP tools.

Skip this file if the real problem is still datasource discovery or a session-scoped investigation.

## OSI-Marivo Schema Reference

The skill-local canonical JSON Schema reference is:

```text
references/osi-marivo.schema.json
```

It is a symlink to the repository source of truth:

```text
osi-marivo-spec/schema/osi-marivo.schema.json
```

Use the schema and the examples below before drafting. Use validation feedback, especially
`json_pointer` and `hint`, to repair drafts.

Before generating any semantic model object data, read
`references/osi-marivo.schema.json` and inspect the relevant object definitions, required fields,
`additionalProperties`, enum values, and MARIVO custom extension shapes. Treat the schema as the
source of truth when examples or memory disagree.

For generated semantic model JSON, always create a document file first. Validate and import with
`input_path`; do not pass newly generated JSON through an inline `document` payload.

## Option Presentation

Present options with clear labels, such as Option 1/2/3 or A/B/C, and one-line descriptions. State
trade-offs explicitly and recommend one option when the evidence supports it.

Ask one decision question per message. Do not bundle independent choices. Prefer tables when
comparing options across multiple dimensions.

Only present options when there is genuine choice. If there is one viable table, primary key, time
column, or schema-determined answer, present the single proposal and ask "Confirm?"

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
4. Read `references/osi-marivo.schema.json` before generating semantic model object data.
5. Draft the complete OSI-Marivo document in a file.
6. Validate the draft file with `input_path`.
7. Repair validation issues and revalidate.
8. Present the validated summary to the user for import approval.
9. Import the approved document file with `input_path`.
10. Read or export the stored document.
11. Give the user the local semantic model JSON document path before handing off to analysis.

## Dataset Selection

Given the business domain goal and available tables from the datasource, present dataset
candidates:

| Option | Contents | When to use |
| --- | --- | --- |
| Option 1 | Single core fact table only. | The user needs a narrow first model. |
| Option 2 | Core fact table plus 1-2 key dimension tables. | The model needs common cuts or labels. |
| Option 3 | Fact table plus all clearly related dimension tables. | The user wants a broad reusable graph. |

Wait for the user to pick one before finalizing the document. If only one table is available,
present it directly and ask for confirmation.

## Field Selection

When the model has multiple datasets, each dataset gets its own independent field-selection stage.
Do not merge fields from different datasets into a single stage.

For each dataset, present candidate fields organized by role:

| Role | Description | User decision needed |
| --- | --- | --- |
| Primary key | Uniquely identifies each row. | Confirm key column or columns. |
| Unique keys | Additional business uniqueness constraints. | Confirm or skip. |
| Time fields | Controls analysis time windows and should set `dimension.is_time: true`. | Confirm time column or columns. |
| Dimensions | Group-by and filter columns. | Select from available columns. |

Measures belong in the metric stage, not the dataset field stage. If primary key, unique key, or
time semantics are uncertain, flag the uncertainty and ask the user to decide.

Physical grounding belongs in `dataset.source`, the dataset MARIVO datasource extension, and
`field.expression`.

## Metric Stage

Reusable metrics start from approved business definitions, not column names alone. For each metric,
include:

- semantic name
- ANSI SQL aggregation expression
- one-line business meaning
- observed dataset
- primary time field
- additive dimensions or non-additivity warning

SUM-based metrics are additive only across approved dimensions. AVG and percentile-style metrics are
non-additive and cannot be decomposed; warn the user before import.

## Relationship Stage

If the model has multiple datasets, present join paths with options:

| Relationship | From | To | Join columns | Cardinality |
| --- | --- | --- | --- | --- |
| name | dataset_a | dataset_b | column to column | many_to_one |

Create relationships only in the draft document, and only after the user confirms the join path. If
the model has one dataset, skip this stage.

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

## Minimal Document Shape

Use this as an orientation skeleton only. Before filling any object, read
`references/osi-marivo.schema.json`; required fields, allowed enum values, `additionalProperties`,
and MARIVO extension data shapes are defined by the schema, not by this example. Keep this example
schema-valid so it can be copied into a file and validated before editing.

```json
{
  "version": "0.1.1",
  "semantic_model": [
    {
      "name": "model_name",
      "datasets": [
        {
          "name": "dataset_name",
          "source": "schema.table",
          "primary_key": ["id"],
          "fields": [
            {
              "name": "id",
              "expression": {
                "dialects": [
                  { "dialect": "ANSI_SQL", "expression": "id" }
                ]
              },
              "dimension": {}
            }
          ],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": { "datasource_id": "datasource_id" }
            }
          ]
        }
      ],
      "relationships": [
        {
          "name": "relationship_name",
          "from": "dataset_name",
          "to": "other_dataset_name",
          "from_columns": ["field_name"],
          "to_columns": ["field_name"],
          "custom_extensions": []
        }
      ],
      "metrics": [
        {
          "name": "metric_name",
          "expression": {
            "dialects": [
              { "dialect": "ANSI_SQL", "expression": "COUNT(*)" }
            ]
          },
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": { "additive_dimensions": ["id"] }
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
- presenting fake options when there is only one viable choice
- merging multiple datasets into a single field-selection stage
- including measures in the dataset field stage
- skipping user confirmation for relationship paths
- omitting additive behavior on metrics
- not warning that AVG or percentile metrics are non-decomposable
- adding metrics that reference fields not present in the observed dataset
- hiding physical drift inside ad hoc metric SQL instead of fixing the dataset or relationship
- treating validation success as permission to import
- importing without telling the user where the local semantic model JSON document lives
- treating an unapproved draft as ready for formal investigation
