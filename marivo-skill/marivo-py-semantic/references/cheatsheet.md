# marivo-py-semantic cheatsheet

## Decorators

| Decorator          | Lives on               | Required kwargs                         | Notes                                                 |
|--------------------|------------------------|-----------------------------------------|-------------------------------------------------------|
| `ms.datasource`   | bare function          | `name=`, `backend_type=`                | Must return an ibis backend.                          |
| `@ms.dataset`      | function taking backend | `name=`, `datasource=<ref>`             | `primary_key=` is optional but recommended.           |
| `@ms.field`        | function taking dataset | `dataset=<ref or str>`                  | Non-aggregated per-row expression.                    |
| `@ms.time_field`   | function taking dataset | `dataset=`, `data_type=`, `granularity=` | Calendar axis for time-aware analysis.                |
| `@ms.metric`       | function taking datasets | `datasets=`, `decomposition=`, `name=`  | Body returns an ibis expression for the metric value. |
| `@ms.relationship` | bare function          | `from_=`, `to=`, `from_fields=`, `to_fields=` | Declares cross-dataset joins.                  |

## Builders

| Builder                            | Purpose                                           |
|------------------------------------|---------------------------------------------------|
| `ms.ref("metric.name")`            | Reference a registered metric by local name.      |
| `ms.component("numerator")`        | Access a component inside a derived metric body.  |
| `ms.ratio(numerator=..., denominator=...)` | Derived metric decomposition marker.       |
| `ms.weighted_average(numerator=..., weight=...)` | Weighted-average decomposition marker. |
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
| `project.list_models()`                | ModelIR objects.                                              |
| `project.list_datasources()`           | DatasourceIR objects.                                         |
| `project.list_datasets(model=...)`     | DatasetIR objects, optionally filtered by model.              |
| `project.list_metrics(dataset=...)`    | MetricIR objects, optionally filtered.                        |
| `project.describe("model.x")`          | IR object or text description.                                |
| `project.search("query")`              | SearchResult list.                                            |
| `project.dependencies("model.x")`      | LineageGraph of upstream dependencies.                        |

## CLI

| Command                                          | Purpose                                      |
|--------------------------------------------------|----------------------------------------------|
| `python -m marivo.semantic_py check`             | Validate the semantic project.               |
| `python -m marivo.semantic_py check --strict-provenance` | Non-zero exit if any metric is unverified. |
| `python -m marivo.semantic_py check --parity`    | Run parity checks for metrics with source_sql. |
| `python -m marivo.semantic_py check --format=json` | JSON output with schema_version "1".        |

## Help

| Call                                      | Output                                      |
|-------------------------------------------|---------------------------------------------|
| `ms.help()`                               | Top-level entry list.                       |
| `ms.help("dataset")`                      | Signature and docstring for a decorator.    |
