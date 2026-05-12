---
name: marivo-semantic-layer
description: Use when the task is to intake business definitions, then build, inspect, update, or troubleshoot reusable Marivo semantic models, datasets, metrics, relationships, or readiness through the current stdio MCP tools.
---

# Marivo Semantic-Layer Skill

Use this skill for **current Marivo stdio MCP semantic-layer work** only.

It owns business knowledge intake, reusable semantic contracts, semantic models, datasets, fields,
metrics, relationships, and readiness. It does not own datasource-only browse or session-scoped
investigation loops.

## What This Skill Owns

- extracting candidate business definitions from user-provided metric docs, glossary material, or
  reporting references
- drafting reusable semantic contracts and getting key metric definitions approved before writes
- creating and reading semantic models
- adding or updating datasets and dataset fields
- adding or updating metrics and relationships
- checking model readiness before reuse
- deciding when to hand off to analysis for a smoke test or real investigation

## Choose The Next Tool

- business definition is still unclear: stop writes, collect user business material, then draft the
  contract in `references/modeling.md`
- model does not exist yet: `marivo-create_semantic_model`
- model exists but needs another dataset: `marivo-create_dataset`
- dataset exists but measurement is missing: `marivo-create_metric`
- metric crosses datasets: `marivo-create_relationship`
- object exists but needs repair: `marivo-update_dataset`, `marivo-update_metric`, or
  `marivo-update_relationship`
- model usability is unclear: `marivo-get_semantic_model_readiness`
- reusable graph is ready and now needs a representative run: switch to `marivo-analysis`

## Option Presentation Rules

Follow the superpowers brainstorming pattern when presenting options to the user:

- **Present options with clear labels** — use Option 1/2/3 or A/B/C with one-line descriptions.
  When trade-offs exist, state them explicitly and recommend one option with reasoning.
- **One decision at a time** — ask one question per message. Do not bundle multiple independent
  choices into a single message.
- **Only present options when there is genuine choice.** If a decision can be unambiguously
  determined from the schema (e.g. only one column can serve as PK, only one time column exists),
  propose the single answer directly and ask the user to confirm — do not fabricate fake
  alternatives.
- **Prefer tables for structured comparisons** — when comparing options across multiple
  dimensions (e.g. simple vs standard vs rich), use a markdown table with columns for the option
  name, contents, and a one-line justification.

### When to Skip Options

Skip presenting multiple options and go directly to confirmation when:

- There is only one viable candidate (single table, single PK column, single time column)
- The user has already specified their preference ("use all columns", "I want option 3")
- The choice is obvious from prior context (e.g. user said "build a model on table X")

In these cases, present the single proposal and ask "Confirm?" — do not waste the user's time with
synthetic alternatives.

## Staged Build With Mandatory User Confirmation

The semantic layer build is divided into per-dataset stages plus metric and relationship stages.
**The agent MUST wait for explicit user confirmation before writing any object.**

### Prerequisite — Knowledge Intake

**Before any technical work, the agent MUST ask the user for business knowledge.** The agent
must not proceed to dataset selection until the user has provided at least one of the following:

- Metric definitions (names, formulas, business meaning)
- Reporting requirements (what questions should the model answer)
- Glossary or data dictionary references
- Existing dashboards or analysis templates to replicate
- Domain context (what does this data represent, who uses it, what decisions does it inform)

Ask the user directly:

> "Please share any business context you have — metric definitions, reporting requirements,
> glossary docs, or existing dashboards. What questions should this semantic model answer?"

**If the user provides no business material** (e.g. "just build from the table"), the agent
MUST NOT fabricate business definitions. In this case, note the lack of business context and
proceed with column-name-derived defaults, but flag to the user that metrics will be
mechanically derived rather than business-meaningful.

### Stage A — Dataset Selection

Given the business domain goal and available tables from the datasource, present dataset
candidates:

- **Option 1 (minimal)**: a single core fact table only
- **Option 2 (standard)**: core fact table + 1–2 key dimension tables
- **Option 3 (rich)**: fact table + all related dimension tables

For each option, list the table names and a one-line justification. Wait for the user to pick one
before proceeding.

**If there is only one table available**, skip the options and confirm directly: "Only table X is available, use it?"

### Stage B, C, D... — Field Selection, One Stage Per Dataset

When the model has multiple datasets, each dataset gets its own independent field-selection
stage. Do NOT merge fields from different datasets into a single stage.

For each dataset, present the candidate fields organized by role:

| Role | Description | User Decision Needed |
|------|-------------|---------------------|
| Primary key | Uniquely identifies each row | Confirm PK column(s) |
| Unique keys | Additional business uniqueness constraints | Confirm or skip |
| Time fields | Controls the analysis time window, must have `dimension.is_time: true` | Confirm which column(s) |
| Dimensions | Group-by / filter columns | Select from available columns |

**Measures (numeric aggregation columns) belong in the Metric stage, NOT in the dataset field
stage.** The dataset stage focuses on identity (PK), time semantics, and grouping (dimensions)
only.

**Rule**: When the agent is uncertain about PK, unique keys, or time fields, it MUST flag the
uncertainty and ask the user to decide. Offer 2–3 concrete proposals (e.g. "A: use `create_time`
as the sole time field; B: use `log_date` + `log_hour` as a composite time dimension").

After the user confirms the field list for one dataset, immediately create that dataset before
moving to the next dataset's field selection stage.

### Metric Stage

After all datasets are created, present metric candidates.

For each metric, describe:

- **Name**: semantic name (e.g. `p90_elapsed_time_success`)
- **Expression**: the ANSI SQL aggregation (e.g. `APPROX_PERCENTILE(...)`)
- **Description**: one-line business meaning
- **additive_dimensions**: list of dimensions across which this metric can be summed and
  decomposed.

**CRITICAL — `additive_dimensions` reminder**:
- Every metric MUST declare `additive_dimensions` in its MARIVO custom extension.
- SUM-based metrics (COUNT, SUM) are fully additive — declare all intended dimensions.
- AVG / APPROX_PERCENTILE are non-additive and **cannot be decomposed**. The agent MUST warn the
  user of this limitation when proposing such metrics.
- The `additive_dimensions` value must be a list of field names, e.g.
  `["cluster","source","query_type"]`.

Present metric proposals from simple to comprehensive:

- **Option 1 (minimal)**: 2–3 core metrics
- **Option 2 (standard)**: 4–6 metrics covering basic KPIs
- **Option 3 (rich)**: 8+ metrics with per-dimension breakdowns

**If the user has already specified their preference** (e.g. "all columns" or "all measures"), skip the
options and present the final metric list directly for confirmation.

Wait for user confirmation before creating metrics.

### Relationship Stage

If the model has multiple datasets, present the join paths with options:

| Relationship | From | To | Join Columns | Cardinality |
|-------------|------|----|-------------|-------------|
| name | dataset_a | dataset_b | col → col | many_to_one |

For each relationship, state:
- `from_dataset.column → to_dataset.column`
- Cardinality (many_to_one, one_to_one)

**Present relationship options when ambiguity exists:**
- If join columns are unambiguous (e.g. same PK name), present the single relationship set and
  ask for confirmation.
- If multiple join paths are possible, offer 2–3 options (e.g. different join columns, different
  cardinalities).

**Wait for user confirmation before creating relationships.**

**If the model has only one dataset, skip this stage** — no relationships are needed.

### After All Stages

1. Create the model (one payload or incremental, depending on complexity).
2. Check readiness with `marivo-get_semantic_model_readiness`.
3. If readiness fails, surface the blockers to the user before attempting analysis.
4. Hand off to `marivo-analysis` only after user approval and readiness are both in place.

## Default Operating Loop (Summary)

1. **Prerequisite — Knowledge Intake**: ask the user for business definitions, metric docs,
   reporting requirements, or domain context. Do not proceed until the user responds.
2. Confirm the datasource and live relation with `marivo-datasource`.
3. **Stage A** — present dataset options, get user confirmation, create the model with the first
   dataset.
4. **Stages B, C, D...** — one stage per remaining dataset. For each: present field options (PK,
   time, dimensions only — no measures), get confirmation, create the dataset. Repeat until all
   datasets are created.
5. **Metric stage** — present metric options, get user confirmation, create all metrics.
6. **Relationship stage** — if multiple datasets, present join paths with options, get user
   confirmation, create relationships.
7. Check readiness before treating the model as reusable.
8. Hand off to `marivo-analysis` only after approval and readiness are both in place.

## High-Value Guardrails

- **Knowledge intake comes first.** Before any datasource browsing or model creation, ask the user
  for business definitions, metric docs, reporting requirements, or domain context. Never skip
  this step.
- **Follow the option presentation rules.** Only present multiple options when there is genuine
  choice; otherwise propose directly and ask for confirmation.
- **One stage per dataset.** When the model has multiple datasets, give each its own field-selection
  stage. Never merge fields from different datasets into a single stage.
- **Dataset stage = identity + time + grouping only.** Measures (numeric aggregation columns)
  belong in the metric stage. Do not include them in dataset field selection.
- **Relationship stage requires user confirmation.** Present join paths with options when
  ambiguity exists; always wait for confirmation before creating relationships.
- **Without business material, flag the gap.** If the user provides no business context, proceed
  with column-name-derived defaults but warn the user that metrics lack business grounding.
- Physical grounding belongs in `dataset.source`, the dataset MARIVO datasource extension, and
  `field.expression`.
- Reusable metrics start from approved business definitions, not from column names alone.
- Create dataset fields before metrics and relationships that depend on them.
- Every metric MUST declare `additive_dimensions` in its MARIVO custom extension; warn the user
  when proposing non-additive metrics (AVG, APPROX_PERCENTILE) that cannot be decomposed.
- Use the current stdio MCP semantic tools only; do not invent separate entity, predicate, time, or
  dimension write flows that the current tool surface does not expose.
- Creation success is not the same as usability; check readiness explicitly.
- If user materials and live metadata disagree, surface the conflict and pause for a user decision
  instead of guessing.
- Do not hand off to analysis on provisional or unapproved semantic contracts.
- Keep payloads minimal and tool-shaped. If a tool rejects a payload, follow the live tool guidance
  instead of copying examples from another surface.

## Common Mistakes

- **jumping into datasource browsing or model creation before asking for business knowledge** —
  always ask the user for metric definitions and domain context first
- **presenting fake options when there is only one viable choice** — if only one PK or time column
  exists, propose directly and confirm; don't waste the user's time
- **merging multiple datasets into a single field-selection stage** — each dataset must have its
  own independent stage
- **including measures in the dataset field stage** — measures belong in the metric stage only
- **skipping user confirmation in the relationship stage** — relationships need approval like
  every other stage
- **omitting `additive_dimensions` on metrics** — leads to decompose failures during analysis
- **not warning that AVG/APPROX_PERCENTILE metrics are non-decomposable** — user should know
  before analysis
- turning field names into metric definitions without user business material
- writing a metric before the underlying dataset fields exist
- embedding physical table or column locators directly into downstream metric or relationship design
- treating model creation as proof that the model is ready for repeated analysis
- using analysis as a workaround for missing contract approval

## Read Next

- `references/modeling.md` for the authoring sequence
- `references/readiness.md` for readiness repair
- `marivo-analysis` once the reusable graph is ready for a smoke test or investigation
