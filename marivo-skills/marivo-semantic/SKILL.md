---
name: marivo-semantic
description: Use for any Marivo datasource definition or semantic-layer authoring task: datasource declarations/refs, datasets, fields, time fields, metrics, relationships, evidence, readiness, and analysis handoff.
---

# marivo-semantic

Use this skill when defining project datasources or building reusable semantic
objects. For metric-centered analysis on an already-ready model, use
`marivo-analysis`.

## Runtime Assumptions

This skill is written for pip-installed Marivo in an ordinary project. Do not
assume the Marivo source checkout, repo fixtures, `make`, or a fixed `.venv`
name. Identify the project Python environment first, then use its explicit
Python path such as `<venv>/bin/python`.

The current project should contain `.marivo/semantic/`. If it does not, create
the project structure before authoring semantic objects.

## Non-Negotiable Rules

- At the start of each authoring session, inspect the installed runtime catalog
  with `ms.help(format="json")`. The top-level call returns a compact typed
  directory (~2KB); drill into `ms.help("<kind>", format="json")` for per-symbol
  detail including constraints. Do not call `ms.help("constraints", format="json")`
  at session start — access constraints on demand through per-symbol calls.
- Before declaring an object kind for the first time in the session, inspect
  `ms.help("<object_kind>", format="json")` for `dataset`, `field`,
  `time_field`, `metric`, or `relationship`. For metrics, also inspect
  `ms.help("decomposition", format="json")`; for derived metrics, also inspect
  `ms.help("component", format="json")`. Do not repeat this per object.
- Python files under `.marivo/semantic/<model>/` are the only semantic source of
  truth.
- Names are candidate signals only. Business meaning must come from comments,
  source SQL, knowledge, preview evidence, or user confirmation.
- Before authoring new objects from datasource evidence, inspect metadata with
  `mv.datasources.inspect_source(...)`, then call `project.propose_candidates(...)`
  with `inspect_source=mv.datasources.inspect_source`.
- `propose_candidates` returns structural signal only and is **not exhaustive**. Iterate
  `result.residual_columns` and decide which are measures, primary keys, or dimensions
  worth declaring. Do not treat the candidates list as the complete worklist.
- Classify candidate uncertainty with `project.open_questions(...)`. This works
  before `_model.py` exists; without a loaded registry, question `blast_radius`
  falls back to `0`. `blast_radius` is a non-negative integer count of distinct
  transitive dependents, not a ref tuple/list or candidate list.
- Ask users only for unresolved blockers or business decisions evidence cannot
  settle.
- Record user confirmations for real `OpenQuestion` objects with
  `project.answer(...)`; do not use it to answer readiness-only blockers.
- Reload after authoring `@ms.metric` or `@ms.time_field` declarations so Marivo
  can auto-record their object-level `metric_decomposition` and
  `time_field_identity` decisions.
- Use `project.record_decision(semantic_id, record)` only when a complete evidence-backed
  `DecisionRecord` can be built from the question, chosen value, evidence
  fingerprint, and cited source.
- Do not hand off to `marivo-analysis` while readiness is blocked.
- Run `project.richness(...)` at closeout and report richness gaps separately
  from readiness blockers.

`table.schema()` returns types but not comments. Do not mention target preview
surfaces as APIs until they are implemented.

## Default Workflow

Read `references/workflow.md` first for object construction. The short form is:

1. Discover the project and existing refs.
2. Inspect datasource metadata and bounded previews.
3. Generate candidates with `project.propose_candidates(...)`. The result is a
   **non-exhaustive structural starting set** — iterate `result.residual_columns`
   for measures, primary keys, dimensions, and non-conventional foreign keys the
   heuristics omit. Do not treat `result.candidates` as the complete worklist.
4. Classify questions with `project.open_questions(...)`.
5. Author a single `.marivo/semantic/<model>/_model.py` using ref variables.
6. Record confirmations and complete decisions in the ledger.
7. Reload successfully, preview, and run parity where source SQL exists.
8. Run `project.audit(...)`, readiness, and richness.

## Authoring Defaults

- Default to one `.marivo/semantic/<model>/_model.py` per model.
- Use `md.ref("<datasource>")` for datasource references.
- Use Python ref variables between semantic objects.
- Prefer a partition time field such as `dt`, `log_date`, or `event_date` as
  the dataset `@ms.time_field`.
- For sortable day/hour partition columns, keep the raw string/integer column
  body and declare `date_format`; use `required_prefix` for hour-only fields
  such as `HH`.
- Use a non-partition business event time only when evidence establishes that
  axis; record the reason in `description`, `ai_context`, and the ledger when
  material.
- Include `ai_context.business_definition` and `ai_context.guardrails` for
  analyzable handoff refs.

## Reference Routing

| Need | Read |
| --- | --- |
| End-to-end semantic construction | `references/workflow.md` |
| Python declaration patterns | `references/authoring-patterns.md` |
| Evidence, questions, confirmations, ledger | `references/evidence-and-ledger.md` |
| Preview, parity, readiness, richness | `references/closeout.md` |
| Datasource setup and Trino access | `references/datasource.md` |
| Preview behavior and failures | `references/preview.md` |
| Known failure modes | `references/pitfalls.md` |
