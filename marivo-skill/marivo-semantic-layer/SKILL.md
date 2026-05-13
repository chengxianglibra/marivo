---
name: marivo-semantic-layer
description: Use when the task is to intake business definitions, then build, inspect, validate, import, export, or troubleshoot reusable Marivo semantic model documents through the current stdio MCP tools.
---

# Marivo Semantic-Layer Skill

Use this skill for current Marivo stdio MCP semantic-layer work only.

It owns business knowledge intake, reusable semantic contracts, OSI-Marivo document drafting,
validation, import, export, and deciding when to hand off to analysis. It does not own
datasource-only browse or session-scoped investigation loops.

## What This Skill Owns

- extracting candidate business definitions from user-provided metric docs, glossary material, or
  reporting references
- drafting reusable semantic contracts and getting key metric definitions approved before import
- reading or exporting current semantic model documents
- validating draft OSI-Marivo documents and repairing validation issues
- importing validated documents after explicit user approval
- deciding when to hand off to analysis for a smoke test or real investigation

## Choose The Next Tool

- Need physical metadata before authoring: use `marivo-datasource`.
- Need current semantic state: `marivo-list_semantic_models`, `marivo-get_semantic_model`, or
  `marivo-export_osi_semantic_models`.
- Need to check a draft: `marivo-validate_osi_semantic_models`.
- Draft is validated and user approved it: `marivo-import_osi_semantic_models`.
- Reusable graph is imported and now needs a representative run: switch to `marivo-analysis`.

## Option Presentation Rules

Follow the superpowers brainstorming pattern when presenting options to the user:

- Present options with clear labels: use Option 1/2/3 or A/B/C with one-line descriptions.
  When trade-offs exist, state them explicitly and recommend one option with reasoning.
- One decision at a time: ask one question per message. Do not bundle multiple independent
  choices into a single message.
- Only present options when there is genuine choice. If a decision can be unambiguously determined
  from the schema, propose the single answer directly and ask the user to confirm.
- Prefer tables for structured comparisons when comparing options across multiple dimensions.

### When To Skip Options

Skip presenting multiple options and go directly to confirmation when:

- there is only one viable candidate, such as a single table, primary key column, or time column
- the user has already specified their preference
- the choice is obvious from prior context

In these cases, present the single proposal and ask "Confirm?"

## Document-First Build With Mandatory User Confirmation

The agent drafts a complete OSI-Marivo JSON document, validates it, fixes validation errors, and
only imports it after explicit user confirmation. Do not create datasets, fields, metrics, or
relationships through separate management tools.

### Prerequisite: Knowledge Intake

Before any technical work, ask the user for business knowledge. Do not proceed to datasource
selection until the user has provided at least one of the following:

- metric definitions, names, formulas, or business meaning
- reporting requirements or the questions the model should answer
- glossary or data dictionary references
- existing dashboards or analysis templates to replicate
- domain context for the data, users, and decisions

Ask directly:

> Please share any business context you have: metric definitions, reporting requirements, glossary
> docs, or existing dashboards. What questions should this semantic model answer?

If the user provides no business material, do not fabricate business definitions. Proceed with
column-name-derived defaults only after telling the user that metrics will be mechanically derived
rather than business-meaningful.

### Authoring Stages

1. Collect business knowledge before technical work.
2. Browse datasource metadata to choose physical grounding.
3. Draft the full OSI-Marivo JSON document in a file for non-trivial models.
4. Run `marivo-validate_osi_semantic_models` with `input_path` or inline `document`.
5. Fix every validation error using `json_pointer`, `message`, and `hint`.
6. Repeat validation until `valid: true`.
7. Present the validated document summary to the user and wait for approval.
8. Run `marivo-import_osi_semantic_models`.
9. Confirm with `marivo-get_semantic_model` or `marivo-export_osi_semantic_models`.

### Dataset Selection

Given the business domain goal and available tables from the datasource, present dataset
candidates:

| Option | Contents | When to use |
| --- | --- | --- |
| Option 1 | Single core fact table only. | The user needs a narrow first model. |
| Option 2 | Core fact table plus 1-2 key dimension tables. | The model needs common cuts or labels. |
| Option 3 | Fact table plus all clearly related dimension tables. | The user wants a broad reusable graph. |

Wait for the user to pick one before finalizing the document. If only one table is available, skip
the options and confirm directly.

### Field Selection

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

### Metric Stage

After dataset fields are drafted, present metric candidates. For each metric, include:

- semantic name
- ANSI SQL aggregation expression
- one-line business meaning
- observed dataset
- primary time field
- additive dimensions or non-additivity warning

SUM-based metrics are additive only across approved dimensions. AVG and percentile-style metrics
are non-additive and cannot be decomposed; warn the user before import.

### Relationship Stage

If the model has multiple datasets, present join paths with options:

| Relationship | From | To | Join columns | Cardinality |
| --- | --- | --- | --- | --- |
| name | dataset_a | dataset_b | column to column | many_to_one |

Create relationships only in the draft document, and only after the user confirms the join path.
If the model has one dataset, skip this stage.

## Default Operating Loop

1. Ask for business definitions, metric docs, reporting requirements, or domain context.
2. Confirm datasource and live relations through `marivo-datasource`.
3. Present dataset options and get user confirmation.
4. Draft one dataset section at a time: identity, time, and grouping fields only.
5. Present metric options and get user confirmation.
6. Present relationship options when multiple datasets are involved.
7. Validate the complete OSI-Marivo document.
8. Repair validation issues and revalidate until clean.
9. Ask for explicit import approval.
10. Import the document, confirm stored state, then hand off to `marivo-analysis`.

## High-Value Guardrails

- Knowledge intake comes first. Never skip the request for metric definitions and domain context.
- Only present multiple options when there is genuine choice.
- One dataset field-selection stage per dataset.
- Dataset stage equals identity, time, and grouping only.
- Relationships require user confirmation.
- Without business material, flag the gap before using column-name-derived defaults.
- Physical grounding belongs in `dataset.source`, the dataset MARIVO datasource extension, and
  `field.expression`.
- Reusable metrics start from approved business definitions, not from column names alone.
- Every metric must declare additive behavior or carry a clear non-additivity warning.
- Use current stdio MCP semantic document tools only; do not invent separate entity, predicate,
  time, or dimension write flows.
- A valid document is not user approval. Import only after explicit approval.
- If user materials and live metadata disagree, surface the conflict and pause for a user decision.
- Do not hand off to analysis on provisional or unapproved semantic contracts.
- Keep payloads minimal and tool-shaped. If a tool rejects a payload, follow the live tool guidance.

## Common Mistakes

- jumping into datasource browsing or document drafting before asking for business knowledge
- presenting fake options when there is only one viable choice
- merging multiple datasets into a single field-selection stage
- including measures in the dataset field stage
- skipping user confirmation for relationship paths
- omitting additive behavior on metrics
- not warning that AVG or percentile metrics are non-decomposable
- turning field names into metric definitions without user business material
- embedding physical table or column locators directly into downstream metric or relationship design
- treating validation success as permission to import
- using analysis as a workaround for missing contract approval

## Read Next

- `references/modeling.md` for document examples and schema guidance
- `references/readiness.md` for validation and usability troubleshooting
- `marivo-analysis` once the reusable graph is imported and approved for a smoke test or investigation
