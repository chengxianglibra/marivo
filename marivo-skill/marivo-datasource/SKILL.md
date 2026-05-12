---
name: marivo-datasource
description: Use when the task is to register, inspect, browse, preview, or troubleshoot a Marivo datasource through the current stdio MCP tools before semantic modeling or analysis.
---

# Marivo Datasource Skill

Use this skill for **current Marivo stdio MCP datasource work** only.

It owns datasource setup, live catalog browse, and preview-driven table discovery. It does not own
reusable semantic modeling or session-scoped analysis.

## What This Skill Owns

- creating, updating, listing, reading, or deleting datasources
- browsing schemas, tables, and columns
- previewing bounded sample rows
- deciding when the datasource phase is complete enough to hand off

## Choose The Next Tool

- datasource unknown or may already exist: `marivo-list_datasources`, then `marivo-get_datasource`
- datasource must be added or corrected: `marivo-create_datasource` or `marivo-update_datasource`
- relation is unknown: `marivo-browse_schemas` then `marivo-browse_tables`
- table is known but shape is unclear: `marivo-browse_columns`
- column meaning is ambiguous: `marivo-preview_table`
- datasource and target relation are stable: switch to `marivo-semantic-layer`

## Default Operating Loop

1. Confirm whether the datasource already exists.
2. Get or create the `datasource_id`.
3. Browse schemas, then tables, then columns.
4. Preview rows only when metadata alone is not enough.
5. Stop at metadata grounding and hand off once the datasource, schema, table, and key columns are
   known.

## High-Value Guardrails

- Browse and preview are **metadata surfaces**, not analytical evidence.
- Do not invent extra paths, sync flows, or connection JSON that is not present in the current
  stdio MCP tool surface.
- Prefer one bounded browse step at a time instead of asking for a full schema dump.
- Do not jump into semantic modeling or session analysis before the datasource and relation choice is
  stable.

## Common Mistakes

- treating preview rows as proof of a business conclusion
- guessing key or time columns without `marivo-browse_columns`
- mixing datasource setup, semantic design, and session analysis into one answer

## Read Next

- `references/workflow.md` for step-by-step datasource work
- `marivo-semantic-layer` once the target relation and field candidates are known
