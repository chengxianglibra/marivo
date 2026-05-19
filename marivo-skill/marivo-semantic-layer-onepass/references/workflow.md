# Onepass Semantic-Layer Automation Workflow

Use this file when an automation agent such as bxk has loaded
`marivo-semantic-layer-onepass` and the prompt contains both business knowledge-base information
and Trino datasource information.

Skip this file for generic semantic authoring with human approval gates.

## Tool Routing

| Need | Tool |
| --- | --- |
| Find reusable datasource candidates | `marivo-list_datasources` |
| Inspect one datasource | `marivo-get_datasource` |
| Create a Trino datasource | `marivo-create_datasource` |
| Correct a Trino datasource | `marivo-update_datasource` |
| Browse schemas | `marivo-browse_schemas` |
| Browse tables | `marivo-browse_tables` |
| Browse columns | `marivo-browse_columns` |
| Preview bounded rows | `marivo-preview_table` |
| List semantic models | `marivo-list_semantic_models` |
| Read one semantic model | `marivo-get_semantic_model` |
| Validate OSI-Marivo JSON | `marivo-validate_osi_semantic_models` |
| Import OSI-Marivo JSON | `marivo-import_osi_semantic_models` |
| Export stored OSI-Marivo JSON | `marivo-export_osi_semantic_models` |

## Automation Loop

### 1. Parse prompt inputs

Extract the business knowledge-base address or readable entry, Trino datasource display name or
identity, and connection fields. Required Trino connection fields are whatever the prompt supplies
as the datasource contract, commonly `host`, `port`, `user`, `catalog`, `http_scheme`, and
`session_properties`.

Do not ask follow-up questions. If a required value is missing, stop with a failure report.

### 2. Read and normalize the knowledge base

Read the knowledge-base entry before touching table metadata. Extract:

- business domain and entity names
- dataset grain and population rules
- metric names, meanings, formulas, and aggregation intent
- dimensions and enum meanings
- time semantics, calendar rules, and partition expectations
- exclusion, filter, deduplication, and null-handling rules
- target analysis scenarios the prompt expects the semantic layer to support

When the knowledge base conflicts with physical metadata, keep the knowledge-base definition as the
semantic intent and use metadata only to find the best physical grounding. If no grounding exists,
fail closed.

### 3. Create or reuse the Trino datasource

Call `marivo-list_datasources` first. Reuse an existing Trino datasource only when its identity and
connection information match the prompt closely enough for the task. Otherwise call
`marivo-create_datasource` with the prompt-provided Trino details, or `marivo-update_datasource`
when the prompt clearly identifies an existing datasource that must be corrected.

After create, update, or reuse, inspect the datasource and require `readiness_status: "ready"`.
If it is not ready, report the datasource id, readiness status, and failure code, then stop.

### 4. Ground business concepts in live metadata

Browse from broad to narrow:

1. `marivo-browse_schemas`
2. `marivo-browse_tables`
3. `marivo-browse_columns`
4. `marivo-preview_table` with bounded `limit` only when column metadata is not enough

Use previews to confirm value shapes for candidate keys, time fields, dimensions, enum-like fields,
filters, null patterns, and date formats. Treat previews as physical metadata grounding, not
analytical evidence.

### 5. Read the OSI-Marivo schema

Before choosing semantic scope or generating any semantic objects, read
`references/osi-marivo.schema.json`. Use it to understand the current object schema, required
fields, object meanings, extension locations, and expression shape. Do not choose datasets, fields,
metrics, relationships, or extensions from memory when the schema is available.

### 6. Choose semantic scope automatically

Select the smallest dataset set that can satisfy the knowledge-base scenarios. Prefer a
single-dataset model when the business definitions can be grounded in one relation. Add
relationships only when the knowledge base requires cross-dataset semantics and live metadata
contains viable join columns.

Use the knowledge base to choose:

- dataset names, descriptions, source relations, and grains
- fields, including primary keys, unique keys, dimensions, and time fields
- metrics, including Trino SQL dialect aggregation expressions and additivity notes
- relationships, including join columns and cardinality

When live metadata contains a time partition field such as `log_date`, choose that partition field
as the dataset time field by default. Prefer it over event, creation, update, ingestion, or other
timestamp-like fields because partition fields give Marivo a stable pruning and time-scope
grounding path. Use a different time field only when the knowledge base explicitly defines another
time semantics as the business time axis, and carry that decision into the document description.

For every time field, write a MARIVO field extension with `support_min_granularity`, `data_type`,
and (when required) `format` and `required_prefix`. Every time field must declare `data_type`.
When data_type is "string" or "integer", `format` is also required. When format is "hh" or "h",
`required_prefix` is required.

Infer `data_type` from the SQL column type returned by browse_columns:

| SQL column type | OSI `data_type` |
|---|---|
| DATE | `"date"` |
| TIMESTAMP, TIMESTAMPTZ, DATETIME | `"timestamp"` |
| VARCHAR, TEXT, CHAR, STRING | `"string"` |
| INTEGER, BIGINT, SMALLINT, TINYINT | `"integer"` |

When data_type is "string" or "integer", infer `format` from preview sample values:

| Sample value | `format` | Notes |
|---|---|---|
| `'20260325'` (8-char string) | `"yyyymmdd"` | Date partition |
| `'2026-03-25'` (ISO string) | `"yyyy-mm-dd"` | ISO date partition |
| `'2026032514'` (10-char string) | `"yyyymmddhh"` | Hour-precision single column |
| `'14'` or `'03'` (1-2 digit string) | `"hh"` | Hour-only, requires `required_prefix` |
| `14` or `3` (integer 0-23) | `"h"` | Hour-only integer, requires `required_prefix` |
| `20260325` (8-digit integer) | `"yyyymmdd"` | Integer date partition |
| `1711344000` (large integer) | `"epoch_seconds"` | Unix epoch seconds |
| `18836` (moderate integer) | `"epoch_days"` | Days since epoch |

See `references/time-field-patterns.md` for the complete format catalog, composite pattern guide,
and JSON examples for all five time field layouts.

Infer `support_min_granularity` from metadata and samples: date partition fields such as `log_date`
are normally `day`; timestamp fields or explicit date+hour expressions are `hour` only when sample
values prove hour-level precision.

When two time-like columns appear together (e.g., `log_date` with values `'20260325'` and
`log_hour` with values `'14'` or `14`), model them as composite time fields: the date field gets a
date format, and the hour field gets format `"hh"` (or `"h"` if integer) with `required_prefix`
set to the date field name.

Do not wait for approval at this stage.

### 7. Draft the OSI-Marivo document

Write a complete OSI-Marivo JSON document to a local file. Include Marivo datasource grounding in
the document extensions so imported datasets resolve to the Trino datasource selected above.

Prefer Trino SQL dialect expressions over ANSI SQL for fields, metrics, filters, and time parsing
because this onepass flow is grounded in a Trino datasource. Include explicit Trino dialect
expressions for timestamp parsing, date partitions, casts, and aggregation expressions instead of
leaving dialect behavior implicit.

### 8. Validate, repair, and revalidate

Validate from the local file:

```text
marivo-validate_osi_semantic_models(
  input={ "input_path": "<local_json_file_path>" }
)
```

If validation fails, repair the earliest actionable error using `json_pointer`, `message`, `hint`,
and the owning dataset, field, metric, or relationship. Re-run validation from `input_path` after
each repair.

Stop with a failure report only when the error cannot be repaired from the prompt-provided
knowledge base and live metadata. Do not import invalid or partially guessed documents.

### 9. Import and confirm

Once validation succeeds, import the same local file without asking for user approval:

```text
marivo-import_osi_semantic_models(
  input={ "input_path": "<local_json_file_path>" }
)
```

After import, confirm the result with `marivo-list_semantic_models`, `marivo-get_semantic_model`, or
`marivo-export_osi_semantic_models`.

The final report should include the datasource id, semantic model name, local JSON path, validation
summary, import status, and any known limitations.

## Failure Report

When automation cannot continue, report only actionable facts:

- which prompt-provided input was missing or unreadable
- which datasource operation or readiness check failed
- which knowledge-base concept could not be grounded in live metadata
- which validation `json_pointer` could not be repaired
- what semantic objects were not imported

Do not ask the user to provide more information inside this skill.

## Common Mistakes

- asking for Trino connection details even though the automation prompt is responsible for injecting
  them
- waiting for dataset, field, metric, relationship, or import approval
- treating table or column names as final business definitions
- generating semantic objects before reading `references/osi-marivo.schema.json`
- defaulting to ANSI SQL when the datasource and prompt indicate Trino
- importing before validation passes from `input_path`
- using session analysis to compensate for an incomplete semantic contract
- starting `marivo-analysis` instead of ending after import confirmation
- omitting data_type, format, or required_prefix on time fields, which causes schema validation
  to fail; every time field needs data_type, and string/integer time fields also need format
