# marivo-semantic cheatsheet

## Decorators

| Decorator          | Lives on               | Required kwargs                         | Notes                                                 |
|--------------------|------------------------|-----------------------------------------|-------------------------------------------------------|
| `@ms.dataset`      | function taking backend | `name=`, `datasource=<ref>`             | `primary_key=` is optional but recommended.           |
| `@ms.field`        | function taking dataset | `dataset=<ref or str>`                  | Non-aggregated per-row expression.                    |
| `@ms.time_field`   | function taking dataset | `dataset=`, `data_type=`, `granularity=` | Calendar axis for time-aware analysis. Prefer partition time fields when available. |
| `@ms.metric`       | function taking datasets | `datasets=`, `decomposition=`, `name=`  | Body returns an ibis expression for the metric value. |
| `@ms.relationship` | bare function          | `from_dataset=`, `to_dataset=`, `from_fields=`, `to_fields=` | Declares cross-dataset joins.                  |

## Builders

| Builder                            | Purpose                                           |
|------------------------------------|---------------------------------------------------|
| `ms.ref("metric.name")`            | Reference a registered metric by local name.      |
| `ms.component("numerator")`        | Access a component inside a derived metric body.  |
| `ms.ratio(numerator=..., denominator=...)` | Derived metric decomposition marker.       |
| `ms.weighted_average(value=..., weight=...)` | Weighted-average decomposition marker. |
| `ms.sum()`                         | Additive metric decomposition marker.             |

## Context

| Helper                                     | Purpose                                                   |
|--------------------------------------------|-----------------------------------------------------------|
| `ms.model(name=...)`                       | Open a model namespace inside the active registry.        |
| `ms.find_project()`                        | Walk up from cwd to find .marivo/semantic/ project.       |
| `ms.SemanticProject(root=...)`             | Create a SemanticProject pointing at a semantic root.     |

## Introspection (via SemanticProject)

| Call                                   | Output                                                        |
|----------------------------------------|---------------------------------------------------------------|
| Inspect table metadata/comments | `mv.datasources.inspect_table("warehouse", table="orders")` |
| `project.list_models()`                | ModelSummary objects.                                         |
| `project.list_datasources()`           | DatasourceSummary objects.                                    |
| `project.list_datasets(model=...)`     | DatasetSummary objects, optionally filtered by model.         |
| `project.list_metrics(dataset=...)`    | MetricSummary objects, optionally filtered.                   |
| `project.describe("model.x")`          | Description object; call `.to_text()` for human-readable text. |
| `project.search("query")`              | SearchHit list.                                               |
| `project.dependencies("model.x")`      | DependencyNode tree.                                          |

## Project Checks

| Command | Purpose |
|---------|---------|
| `.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; print(project.load())'` | Validate and load the semantic project. |
| `.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); print(project.list_metrics())'` | Inspect registered metrics. |
| `.venv/bin/python -c 'import marivo.semantic as ms; project = ms.find_project(); assert project is not None; project.load(); print(project.describe("model.metric").to_text())'` | Inspect one object. |

## Phase 0 Authoring Loop

| Step | Reference |
|------|-----------|
| Discover project and existing refs | `references/authoring-workflow.md` |
| Collect datasource, metadata, preview, and knowledge evidence | `references/evidence.md` |
| Run raw and semantic previews with current APIs | `references/preview.md` |
| Report blockers, warnings, and analysis-ready refs | `references/readiness.md` |

For materialization, compile, and parity calls, pass a callable backend factory:

```python
import marivo.analysis as mv

backend_factory = lambda name: mv.datasources.build_backend(name)
```

## Help

| Call                                      | Output                                      |
|-------------------------------------------|---------------------------------------------|
| `ms.help()`                               | Top-level entry list.                       |
| `ms.help("dataset")`                      | Signature, docstring, and constraints.      |
| `ms.help("metric", format="json")`        | Structured signature, constraints, and examples. |
| `ms.help("constraints", format="json")`   | Full constraint catalog with `constraint_id`, hints, and AST specs. |

## Metric Body Rules

- `datasets=[]` is only for derived metrics with component decompositions such
  as `ms.ratio(...)`; `datasets=[]` with `ms.sum()` is invalid.
- Dataset-backed metrics must not call `ms.component(...)`.
- Derived metrics can use `dimensions=` when component metric datasets and
  relationships provide a unique path to the requested dimension.
- Return one ibis expression. Do not call an aggregate such as `.mean()` on the
  result of `.count()` or `.sum()`.
- Decorator bodies are AST-whitelisted: use exactly one `return` expression;
  do not use imports, local assignment, control flow, lambdas, or raw SQL calls
  inside the body. Inspect `ms.help("constraints", format="json")` for the
  current machine-readable rules.
