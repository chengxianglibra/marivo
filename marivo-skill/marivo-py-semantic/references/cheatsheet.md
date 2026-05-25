# marivo-py-semantic cheatsheet

## Decorators

| Decorator          | Lives on               | Required kwargs                         | Notes                                                 |
|--------------------|------------------------|-----------------------------------------|-------------------------------------------------------|
| `@ms.datasource`   | bare function          | `name=`, `backend_type=`                | Must return an ibis backend.                          |
| `@ms.dataset`      | function taking backend | `name=`, `datasource=<fn or ref>`       | `primary_key=` is optional but recommended.           |
| `@ms.field`        | function taking dataset | `dataset="<name>"`                      | Non-aggregated per-row expression.                    |
| `@ms.time_field`   | function taking dataset | `dataset=`, `data_type=`, `granularity=` | Calendar axis for time-aware analysis.                |
| `@ms.metric`       | function taking datasets | `decomposition=`, optional `name=`      | Body returns an ibis expression for the metric value. |
| `@ms.relationship` | bare function          | `from_=`, `to=`, `from_columns=`, `to_columns=` | Declares cross-dataset joins.                  |

## Builders

| Builder                            | Purpose                                           |
|------------------------------------|---------------------------------------------------|
| `ms.ref("metric.name")`            | Reference a registered metric by local name.      |
| `ms.ref("datasource.name")`        | Reference a datasource by local name.             |
| `ms.ratio(numerator=..., denominator=...)` | Derived metric decomposition marker.       |
| `ms.weighted_average(numerator=..., weight=...)` | Weighted-average decomposition marker. |
| `ms.sum()`                         | Additive metric decomposition marker.             |

## Context

| Helper                                     | Purpose                                                   |
|--------------------------------------------|-----------------------------------------------------------|
| `ms.model(name=...)`                       | Open a model namespace inside the active registry.        |
| `marivo.semantic_py.registry.use_registry` | Swap to a non-default registry for tests/examples.        |
| `ms.reload(project=None)`                  | Rebuild IR from current Python source files.              |

## Introspection

| Call                            | Output                                                        |
|---------------------------------|---------------------------------------------------------------|
| `ms.list_models()`              | Model names.                                                  |
| `ms.list_datasources()`         | `model.datasource` qualified ids.                             |
| `ms.list_datasets(model=...)`   | `model.dataset` ids, optionally filtered by model.            |
| `ms.list_metrics(dataset=...)`  | `model.metric` ids, optionally filtered by `model.dataset`.   |
| `ms.describe("model.x")`        | Dict with `kind=datasource|dataset|metric` plus identity fields. |
| `ms.help()` / `ms.help("name")` | Top-level entry list or per-symbol signature/docstring.       |

## Help

| Call                                      | Output                                      |
|-------------------------------------------|---------------------------------------------|
| `ms.help()`                               | Top-level entry list.                       |
| `ms.help("dataset")`                      | Signature and docstring for a decorator.    |
| `ms.help("DatasourceNotRegisteredError")` | Docstring for an exception class.           |
