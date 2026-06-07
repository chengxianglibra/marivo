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

- Use runtime help as the authoritative per-object contract. For the object you
  are about to author or call, inspect `ms.help('<name>', format='json')`;
  examples: `ms.help('metric', format='json')`,
  `ms.help('derived_metric', format='json')`,
  `ms.help('decomposition', format='json')`, and
  `ms.help('SemanticProject', format='json')`. The descriptor exposes
  `signature`, `doc`, bounded `constraints`, runnable `examples`, `methods`,
  and drill-down ids. Consult it per object when the contract matters; do not
  turn help into a blanket ritual for each call.
- Before authoring `*_env` credential fields on a `DatasourceSpec`, read
  `~/.marivo/secrets.toml` to discover cached env var names. Reuse an existing
  name when the same credential type is already cached (e.g., reuse
  `TRINO_PASSWORD` for a second Trino datasource). Do not ask the user for a
  secret that the cache already holds.
- Python files under `.marivo/semantic/<model>/` are the only semantic source of
  truth.
- Use `project.assess_authoring(...)` before writing each candidate semantic object. Branch on `AuthoringAssessment.status`: `blocked` stops authoring, `needs_input` requires user or project context, and `supported` can be written.
- Do not dispatch on choreography enums; that pattern has been removed.
- Write one `.marivo/semantic/<model>/_model.py` per model and defer reload until closeout.
- Closeout uses `project.readiness(...)`; it reloads, runs required previews, reports parity warnings, folds richness warnings, and returns analysis-ready refs.
- Collect source evidence before authoring. Bind datasource access once with
  `project.bind_datasource_access(inspect_source=mv.datasources.inspect_source,
  backend_factory=mv.datasources.build_backend)`, then call
  `project.inspect_source_context(datasource=..., source=ms_evidence.DatasetSource(...),
  sample_policy=...)`. It folds metadata inspection and bounded preview into one call and
  persists evidence metadata under `.marivo/semantic/.evidence/`.
- Sample-derived values (`top_values`, `distinct_count`, `min_value`/`max_value`) are facts
  about the bounded sample only (`sample_scope="bounded_sample"`, `approximate=True`). Never
  treat them as full-column cardinality, complete enums, or global ranges.
- Rank columns yourself from pack facts (type, comments, nullable, partition hints, sampled
  values). The project returns no candidate worklist. Deep-dive a small set with
  `project.inspect_column_context(...)`.
- Record non-sample evidence (source SQL, BI definitions, knowledge, owner notes, user
  confirmations) with `project.record_authoring_evidence(AuthoringEvidenceInput(...))` and cite
  the returned `EvidenceRef.id` in checks.
- After authoring and `project.reload()`, run `project.inspect_authored_object(ref)` (cheap,
  backend-free) before any runtime preview/parity.
- `blast_radius` is a non-negative integer count of distinct transitive dependents,
  not a ref tuple/list or candidate list.
- Ask users only for unresolved blockers or business decisions evidence cannot
  settle. Record user confirmations with
  `project.record_authoring_evidence(ms.AuthoringEvidenceInput(kind="user_confirmation", ...))`.
- Confirm relationships with
  `project.record_authoring_evidence(ms.AuthoringEvidenceInput(kind="relationship_confirmation", subject_refs=(relationship_semantic_id,), content=...))`.
- Do not hand off to `marivo-analysis` while readiness is blocked.

`table.schema()` returns types but not comments.

## Default Workflow

Read `references/workflow.md` first. The short form is:

1. Discover the project and existing refs; search for reuse before authoring.
2. Bind datasource access once with `project.bind_datasource_access(...)`.
3. For each source, call `project.inspect_source_context(...)`; deep-dive selected columns with `project.inspect_column_context(...)` when needed.
4. Decide candidate datasets, fields, time fields, metrics, relationships, and derived metrics yourself from source facts and project context.
5. For each candidate object, call `project.assess_authoring(...)`; resolve `blocked` or `needs_input` issues before writing.
6. Author a single `.marivo/semantic/<model>/_model.py` in dependency order. Do not reload between objects in the same file.
7. Close with `project.readiness(...)`. Do not hand off to `marivo-analysis` while readiness is blocked; report readiness warnings as follow-up work.

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
| Datasource selection and setup | `references/datasource.md` |
| Preview behavior and failures | `references/preview.md` |
| Known failure modes | `references/pitfalls.md` |
