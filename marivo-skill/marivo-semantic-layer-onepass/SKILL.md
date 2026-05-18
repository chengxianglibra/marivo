---
name: marivo-semantic-layer-onepass
description: Use when an automation agent such as bxk is building Marivo semantic-layer objects and its prompt already includes a business knowledge-base entry plus Trino datasource information.
---

# Marivo Semantic-Layer Onepass Skill

Use this skill only for one-pass semantic-layer construction through the current Marivo stdio MCP
tools, including bxk agent runs where the prompt already injects the required inputs.

It owns the fully automated path from prompt-provided business knowledge and Trino datasource
information to a validated and imported OSI-Marivo semantic model document. It does not own
generic interactive semantic modeling, datasource-only discovery, or session-scoped investigation.

## Onepass Input Contract

Assume the prompt already contains:

- a business knowledge-base address or readable entry
- complete Trino datasource information
- enough task intent to decide which semantic objects should be created

If any required input is missing, unreadable, or unusable, do **not** ask the user for more
information. Produce a concise failure report that names the missing or blocked input and stop.

## Autonomous Rules

- Do not ask the user questions.
- Do not stop for dataset, field, metric, relationship, validation, or import approval.
- Treat use of this onepass skill as approval to validate and import the semantic document.
- Do not copy the interactive approval gates from `marivo-semantic-layer`.
- Do not start a Marivo analysis session from this skill.

## Default Operating Loop

1. Parse the prompt for the knowledge-base entry and Trino datasource information.
2. Read the knowledge base and extract business entities, metric definitions, dimensions, time
   semantics, exclusions, and target scenarios.
3. Create or reuse the Trino datasource, then require `readiness_status: "ready"` before browsing.
4. Browse schemas, tables, and columns; preview bounded rows only for grounding ambiguous fields.
5. Read `references/osi-marivo.schema.json` to understand the semantic object schema and meaning.
6. Choose the smallest dataset scope that satisfies the knowledge-base scenario and schema.
7. Draft a complete OSI-Marivo JSON document to a local file, preferring Trino SQL dialect
   expressions.
8. Validate with `marivo-validate_osi_semantic_models` using `input_path`.
9. Repair validation errors by `json_pointer`, then revalidate until valid or blocked.
10. Import the validated document with `marivo-import_osi_semantic_models`.
11. Confirm the imported semantic model with list, get, or export tools, then report the result.

## Guardrails

- The business knowledge base is the semantic source of truth; datasource browse and preview are
  physical grounding only.
- Never infer business meaning from column names alone when the knowledge base gives a definition.
- Never invent Trino connection parameters that are absent from the prompt.
- Prefer Trino SQL dialect expressions over ANSI SQL because this onepass flow is grounded in a
  Trino datasource.
- When a time partition field such as `log_date` exists, use it as the time field unless the
  knowledge base explicitly requires another time semantics.
- Prefer explicit time fields and carry time parsing uncertainty into the document description or
  failure report.
- If validation cannot be repaired from the knowledge base and live metadata, fail closed instead of
  importing a partial or guessed contract.
- Keep analysis out of scope; hand off only after the semantic layer is imported and confirmed.

## Read Next

- `references/workflow.md` for the fully automated onepass semantic-layer construction loop
- `marivo-semantic-layer` only for interactive, approval-gated semantic authoring
