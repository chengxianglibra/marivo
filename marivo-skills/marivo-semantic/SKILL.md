---
name: marivo-semantic
description: Use when declaring or modifying a Marivo semantic model: datasource refs, datasets, fields, time fields, metrics, relationships, evidence, readiness, and richness.
---

# marivo-semantic

Use this skill when building or changing semantic objects under
`.marivo/semantic/<model>/`: datasets, fields, time fields, metrics, and
relationships. For analysis on top of an already-ready model, use
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
  with `ms.help(format="json")` and `ms.help("constraints", format="json")`.
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
  `mv.datasources.inspect_table(...)`, then call `project.propose_candidates(...)`
  with `inspect_table=mv.datasources.inspect_table`.
- Classify candidate uncertainty with `project.open_questions(...)`.
- Ask users only for unresolved blockers or business decisions evidence cannot
  settle.
- Record user confirmations with `project.answer(...)`.
- Use `project.record_decision(...)` only when a complete evidence-backed
  `DecisionRecord` can be built from the question, chosen value, evidence
  fingerprint, and cited table.
- Do not hand off to `marivo-analysis` while readiness is blocked.
- Run `project.richness(...)` at closeout and report richness gaps separately
  from readiness blockers.

`table.schema()` returns types but not comments. Do not mention target preview
surfaces as APIs until they are implemented.

## Default Workflow

Read `references/workflow.md` first for object construction. The short form is:

1. Discover the project and existing refs.
2. Inspect datasource metadata and bounded previews.
3. Generate candidates with `project.propose_candidates(...)`.
4. Classify questions with `project.open_questions(...)`.
5. Author a single `.marivo/semantic/<model>/_model.py` using ref variables.
6. Record confirmations and complete decisions in the ledger.
7. Reload, preview, and run parity where source SQL exists.
8. Run `project.audit(...)`, readiness, and richness.

## Authoring Defaults

- Default to one `.marivo/semantic/<model>/_model.py` per model.
- Use `md.ref("<datasource>")` for datasource references.
- Use decorated Python ref variables between semantic objects.
- Prefer a partition time field such as `dt`, `log_date`, or `event_date` as
  the dataset `@ms.time_field`.
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
