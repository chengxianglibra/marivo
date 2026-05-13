---
name: marivo-semantic-layer
description: Use when the task is to intake business definitions, then build, inspect, validate, import, export, or troubleshoot reusable Marivo semantic model documents through the current stdio MCP tools.
---

# Marivo Semantic-Layer Skill

Use this skill for current Marivo stdio MCP semantic-layer work only.

It owns business knowledge intake, reusable semantic contracts, OSI-Marivo document drafting,
validation, import, export, and deciding when to hand off to analysis. It does not own
datasource-only browse or session-scoped investigation loops.

## Responsibility Split

- `SKILL.md` is the entrypoint: scope, routing, non-negotiable rules, and which reference to read.
- `references/modeling.md` is the authoring runbook: business intake, dataset/field/metric/
  relationship decisions, schema reading, validation/import, examples, and repair rules.
- `references/osi-marivo.schema.json` is the canonical contract reference for object shape.
- `references/readiness.md` is for validation failures, usability issues, and imported-model
  troubleshooting.

## Scope

- extracting candidate business definitions from user-provided metric docs, glossary material, or
  reporting references
- drafting reusable semantic contracts and getting key metric definitions approved before import
- reading or exporting current semantic model documents
- validating draft OSI-Marivo documents and repairing validation issues
- importing validated documents after explicit user approval
- deciding when to hand off to analysis for a smoke test or real investigation

## Tool Routing

- Need physical metadata before authoring: use `marivo-datasource`.
- Need current semantic state: `marivo-list_semantic_models`, `marivo-get_semantic_model`, or
  `marivo-export_osi_semantic_models`.
- Need to check a draft: `marivo-validate_osi_semantic_models`.
- Draft is validated and user approved it: `marivo-import_osi_semantic_models`.
- Reusable graph is imported and now needs a representative run: switch to `marivo-analysis`.

## Non-Negotiable Rules

- Read `references/modeling.md` before authoring or repairing reusable semantic model documents.
- Before generating semantic model object data, read `references/osi-marivo.schema.json`; do not
  rely on memory, examples, or generated-model intuition alone.
- Build a complete OSI-Marivo JSON document. Do not create datasets, fields, metrics, or
  relationships through separate management tools.
- Validate and import newly generated semantic model JSON through a document file with `input_path`;
  do not use inline JSON payloads for validation or import.
- Import only after explicit user approval. A valid document is not approval.
- After import, report the local semantic model JSON document path used for validation/import.

## Default Loop

1. Ask for business definitions, metric docs, reporting requirements, or domain context.
2. Confirm datasource and live relations through `marivo-datasource`.
3. Use `references/modeling.md` for dataset, field, metric, relationship, and option decisions.
4. Write the complete OSI-Marivo JSON document to a local file.
5. Validate the file with `marivo-validate_osi_semantic_models` using `input_path`.
6. Repair and revalidate until clean.
7. Ask for explicit import approval.
8. Import the approved file with `marivo-import_osi_semantic_models` using `input_path`.
9. Confirm stored state, report the local JSON document path, then hand off to `marivo-analysis`
   when a representative run is needed.

## Load References

- Read `references/modeling.md` for all authoring, validation, import, and repair work.
- Read `references/osi-marivo.schema.json` immediately before preparing semantic model object data
  or when schema details are uncertain.
- Read `references/readiness.md` when validation succeeds but the imported model is not usable or
  readiness is unclear.

## Read Next

- `marivo-analysis` once the reusable graph is imported and approved for a smoke test or investigation
