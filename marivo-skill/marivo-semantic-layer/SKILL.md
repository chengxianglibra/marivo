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

## Default Operating Loop

1. Confirm the datasource and live relation with `marivo-datasource`.
2. Intake user business material and extract candidate grain, population, time semantics, and
   exclusions.
3. Draft the semantic contract and get key definitions approved before writing reusable objects.
4. Decide whether to create the model in one payload or incrementally.
5. Ground the dataset first: `dataset.source`, `custom_extensions`, and `fields`.
6. Add dependent metrics and relationships only after the dataset fields exist.
7. Check readiness before treating the model as reusable.
8. Hand off to `marivo-analysis` only after approval and readiness are both in place.

## High-Value Guardrails

- Physical grounding belongs in `dataset.source`, the dataset MARIVO datasource extension, and
  `field.expression`.
- Reusable metrics start from approved business definitions, not from column names alone.
- Create dataset fields before metrics and relationships that depend on them.
- Use the current stdio MCP semantic tools only; do not invent separate entity, predicate, time, or
  dimension write flows that the current tool surface does not expose.
- Creation success is not the same as usability; check readiness explicitly.
- If user materials and live metadata disagree, surface the conflict and pause for a user decision
  instead of guessing.
- Do not hand off to analysis on provisional or unapproved semantic contracts.
- Keep payloads minimal and tool-shaped. If a tool rejects a payload, follow the live tool guidance
  instead of copying examples from another surface.

## Common Mistakes

- turning field names into metric definitions without user business material
- writing a metric before the underlying dataset fields exist
- embedding physical table or column locators directly into downstream metric or relationship design
- treating model creation as proof that the model is ready for repeated analysis
- using analysis as a workaround for missing contract approval

## Read Next

- `references/modeling.md` for the authoring sequence
- `references/readiness.md` for readiness repair
- `marivo-analysis` once the reusable graph is ready for a smoke test or investigation
