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

## Supported Datasource Types

Currently **only Trino** datasources are supported. Do not attempt to create DuckDB, ClickHouse,
Iceberg, or any other datasource type — they will fail at connection time.

## Connection Parameters: Mandatory User Confirmation

**The agent MUST NOT guess or invent connection parameters.** The `create_datasource` tool does not
enumerate required fields per type, so guessing leads to silent failures (`not_ready` /
`datasource_invalid_connection`).

When a datasource needs to be created, **always ask the user** to provide the connection JSON
explicitly. Prompt with the known Trino connection shape:

```
请提供 Trino 连接信息：
{
  "host": "<hostname>",
  "port": <port>,
  "user": "<username>",
  "catalog": "<catalog>",
  ...其他可选参数
}
```

Only proceed with `marivo-create_datasource` after the user has supplied these values.

## Default Operating Loop

1. Confirm whether the datasource already exists via `marivo-list_datasources`.
2. If no datasource exists, **ask the user for connection parameters** — do not guess.
3. Create the datasource with user-provided connection info.
4. Verify readiness: datasource must return `readiness_status: "ready"` before proceeding.
5. Browse schemas, then tables, then columns.
6. Preview rows only when metadata alone is not enough.
7. Stop at metadata grounding and hand off once the datasource, schema, table, and key columns are
   known.

## High-Value Guardrails

- Browse and preview are **metadata surfaces**, not analytical evidence.
- **Never guess datasource type or connection parameters.** Only Trino is supported; always ask the
  user for the full connection JSON.
- Do not invent extra paths, sync flows, or connection JSON that is not present in the current
  stdio MCP tool surface.
- Prefer one bounded browse step at a time instead of asking for a full schema dump.
- Do not jump into semantic modeling or session analysis before the datasource and relation choice is
  stable.
- After creating a datasource, check `readiness_status`; if `not_ready`, surface the
  `failure_code` to the user before continuing.

## Common Mistakes

- **guessing datasource type or connection parameters** instead of asking the user
- **creating a DuckDB or other unsupported datasource type** — only Trino works
- treating preview rows as proof of a business conclusion
- guessing key or time columns without `marivo-browse_columns`
- mixing datasource setup, semantic design, and session analysis into one answer
- ignoring `readiness_status: "not_ready"` and proceeding to browse anyway

## Read Next

- `references/workflow.md` for step-by-step datasource work
- `marivo-semantic-layer` once the target relation and field candidates are known
