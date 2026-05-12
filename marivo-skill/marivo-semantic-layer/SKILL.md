---
name: marivo-semantic-layer
description: Use when the task is to build, inspect, update, or troubleshoot reusable Marivo semantic models, datasets, metrics, relationships, or readiness through the current stdio MCP tools.
---

# Marivo Semantic-Layer Skill

Use this skill for **current Marivo stdio MCP semantic-layer work** only.

It owns reusable semantic models, datasets, fields, metrics, relationships, and readiness. It does
not own datasource-only browse or session-scoped investigation loops.

## What This Skill Owns

- creating and reading semantic models
- adding or updating datasets and dataset fields
- adding or updating metrics and relationships
- checking model readiness before reuse
- deciding when to hand off to analysis for a smoke test or real investigation

## Choose The Next Tool

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
2. Decide whether to create the model in one payload or incrementally.
3. Ground the dataset first: `dataset.source`, `custom_extensions`, and `fields`.
4. Add dependent metrics and relationships only after the dataset fields exist.
5. Check readiness before treating the model as reusable.
6. Hand off to `marivo-analysis` for a smoke test or an actual investigation.

## High-Value Guardrails

- Physical grounding belongs in `dataset.source`, the dataset MARIVO datasource extension, and
  `field.expression`.
- Create dataset fields before metrics and relationships that depend on them.
- Use the current stdio MCP semantic tools only; do not invent separate entity, predicate, time, or
  dimension write flows that the current tool surface does not expose.
- Creation success is not the same as usability; check readiness explicitly.
- Keep payloads minimal and tool-shaped. If a tool rejects a payload, follow the live tool guidance
  instead of copying examples from another surface.

## Common Mistakes

- writing a metric before the underlying dataset fields exist
- embedding physical table or column locators directly into downstream metric or relationship design
- treating model creation as proof that the model is ready for repeated analysis

## Read Next

- `references/modeling.md` for the authoring sequence
- `references/readiness.md` for readiness repair
- `marivo-analysis` once the reusable graph is ready for a smoke test or investigation
