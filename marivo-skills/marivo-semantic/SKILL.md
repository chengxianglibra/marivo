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
  are about to author or call, inspect `ms.help('<name>')`;
  examples: `ms.help('metric')`,
  `ms.help('derived_metric')`,
  `ms.help('decomposition')`, and
  `ms.help('SemanticCatalog')`. The descriptor exposes
  `signature`, `doc`, bounded `constraints`, runnable `examples`, `methods`,
  and drill-down ids. Consult it per object when the contract matters; do not
  turn help into a blanket ritual for each call.
- Use `ms.load()` to obtain a `SemanticCatalog` for browsing and inspecting
  loaded semantic objects. Do not construct `SemanticProject` directly for
  read-only consumption.
- Before authoring `*_env` credential fields on a `DatasourceSpec`, read
  `~/.marivo/secrets.toml` to discover cached env var names. Reuse an existing
  name when the same credential type is already cached (e.g., reuse
  `TRINO_PASSWORD` for a second Trino datasource). Do not ask the user for a
  secret that the cache already holds.
- Python files under `.marivo/semantic/<domain>/` are the only semantic source of
  truth.
- Collect source evidence before authoring. If you need custom
  `inspect_source` or `backend_factory`, pass them explicitly:
  `project.inspect_source_context(datasource=..., source=ms_evidence.DatasetSource(...),
  inspect_source=..., backend_factory=..., sample_policy=...)`.
  It folds metadata inspection and bounded preview into one call.
  When no overrides are given, the kernel defaults (`md.inspect_source` and
  `md.connect`) are used automatically.
- Sample-derived values (`top_values`, `distinct_count`, `min_value`/`max_value`) are facts
  about the bounded sample only (`sample_scope="bounded_sample"`, `approximate=True`). Never
  treat them as full-column cardinality, complete enums, or global ranges.
- Rank columns yourself from pack facts (type, comments, nullable, partition hints, sampled
  values). The project returns no candidate worklist. Deep-dive a small set with
  `project.inspect_column_context(...)`.
- Before writing each candidate object, run `project.assess_authoring(...)` with
  `sources=(ms.AuthoringSourceInput(...),)` and `semantic_refs=...` where relevant. Branch
  on `AuthoringAssessment.status`, then inspect `issues` and `questions`; never string-parse
  messages. Ask the user only for `AuthoringQuestion`s the assessment raises.
- After authoring and `project.load()`, run `project.inspect_authored_object(ref)` (cheap,
  backend-free) before any runtime preview/parity.
- `blast_radius` is a non-negative integer count of distinct transitive dependents,
  not a ref tuple/list or candidate list.
- Ask users only for unresolved blockers or business decisions evidence cannot
  settle.
- Reload after authoring `@ms.metric` or `@ms.time_dimension` declarations so Marivo
  can auto-record their object-level `metric_decomposition` and
  `time_dimension_identity` decisions.
- Do not hand off to `marivo-analysis` while readiness is blocked.
- Run `project.readiness(...)` once at closeout. Richness gaps are folded into
  readiness warnings and `richness_summary`.

`table.schema()` returns types but not comments.

## Inspecting semantic objects

Use `ms.load()` to obtain a `SemanticCatalog`, then browse and inspect:

```python
catalog = ms.load()
catalog.list().show()                         # top-level: models and datasources
catalog.list("sales").show()                  # datasets and metrics under a model
catalog.list("sales.orders").show()           # fields, time fields, relationships, filtered metrics
revenue = catalog.get("sales.revenue")
revenue.details()                             # kind-specific details
```

Use `ms.help(ref)` for a bounded consumption briefing on any semantic ref.
This is the default path before passing an object to analysis APIs:

```python
mv.help(revenue)                    # bounded consumption context
mv.help(revenue, project=project)   # explicit project when not in CWD
```

Read `_domain.py` only when you need to modify the semantic model, inspect
implementation expressions, or debug authoring behavior.

## Default Workflow

Read `references/workflow.md` first. The short form is:

1. Discovery/source inspection: discover the project and existing refs, inspect
   source context, and deep-dive only the columns that matter.
2. Assess and author each candidate object: call `project.assess_authoring(...)`,
   resolve blockers/questions, author one `.marivo/semantic/<domain>/_domain.py` using ref
   variables, load, and inspect the authored object.
3. Closeout: call `project.readiness(...)` once for the target refs and hand off only when
   it is not blocked.

## Authoring Defaults

- Default to one `.marivo/semantic/<domain>/_domain.py` per domain.
- Use `md.ref("<datasource>")` for datasource references.
- Use Python ref variables between semantic objects.
- Prefer a partition time dimension such as `dt`, `log_date`, or `event_date` as
  the entity `@ms.time_dimension`.
- For sortable day/hour partition columns, keep the raw string/integer column
  body and declare `date_format` as a Python strptime string (e.g. `"%Y%m%d"`);
  use `required_prefix` (no `date_format`) for hour-only fields.
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
| Preview, parity, readiness | `references/closeout.md` |
| Datasource selection and setup | `references/datasource.md` |
| Preview behavior and failures | `references/preview.md` |
| Known failure modes | `references/pitfalls.md` |
