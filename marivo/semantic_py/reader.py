from __future__ import annotations

from collections.abc import Callable
from typing import Any

from marivo.semantic_py.errors import PySemanticNotFound, SemanticRuntimeError
from marivo.semantic_py.ir import DatasetIR, FieldIR, MetricIR, ModelIR
from marivo.semantic_py.registry import SemanticProject, default_project

BackendFactory = Callable[[str], Any]


def _project(project: SemanticProject | None) -> SemanticProject:
    return default_project() if project is None else project


def ensure_loaded(project: SemanticProject | None = None) -> None:
    target = _project(project)
    if target.registry.state in {"unloaded", "errored"}:
        from marivo.semantic_py.loader import load_project

        load_project(target)


def reload(project: SemanticProject | None = None) -> None:
    target = _project(project)
    from marivo.semantic_py.loader import load_project

    load_project(target, reload=True)


def list_models(project: SemanticProject | None = None) -> list[str]:
    target = _project(project)
    ensure_loaded(target)
    return sorted(target.registry.models)


def list_datasources(project: SemanticProject | None = None) -> list[str]:
    target = _project(project)
    ensure_loaded(target)
    return sorted(
        f"{model_name}.{datasource_name}"
        for model_name, model_ir in target.registry.models.items()
        for datasource_name in model_ir.datasources
    )


def list_datasets(
    model: str | None = None,
    project: SemanticProject | None = None,
) -> list[str]:
    target = _project(project)
    ensure_loaded(target)
    if model is not None:
        model_ir = target.registry.models.get(model)
        if model_ir is None:
            return []
        return sorted(f"{model}.{dataset_name}" for dataset_name in model_ir.datasets)
    return sorted(
        f"{model_name}.{dataset_name}"
        for model_name, model_ir in target.registry.models.items()
        for dataset_name in model_ir.datasets
    )


def list_metrics(
    project: SemanticProject | None = None,
    *,
    dataset: str | None = None,
) -> list[str]:
    target = _project(project)
    ensure_loaded(target)

    if dataset is not None:
        model_name, separator, dataset_name = dataset.partition(".")
        if not separator or not model_name or not dataset_name:
            return []
        model_ir = target.registry.models.get(model_name)
        if model_ir is None or dataset_name not in model_ir.datasets:
            return []
        return sorted(
            f"{model_name}.{metric_name}"
            for metric_name, metric_ir in model_ir.metrics.items()
            if _metric_depends_on_dataset(model_ir, metric_ir, dataset_name)
        )

    return sorted(
        f"{model_name}.{metric_name}"
        for model_name, model_ir in target.registry.models.items()
        for metric_name in model_ir.metrics
    )


def _metric_depends_on_dataset(
    model_ir: ModelIR,
    metric_ir: MetricIR,
    dataset_name: str,
    seen: set[str] | None = None,
) -> bool:
    if dataset_name in metric_ir.references.datasets:
        return True
    if metric_ir.references.datasets:
        return False

    seen = set() if seen is None else seen
    if metric_ir.name in seen:
        return False
    seen.add(metric_ir.name)

    referenced_metrics = {
        metric_name
        for metric_name in (
            *metric_ir.references.metrics,
            metric_ir.decomposition.numerator,
            metric_ir.decomposition.denominator,
            metric_ir.decomposition.weight,
        )
        if metric_name is not None
    }
    return any(
        _metric_depends_on_dataset(model_ir, referenced_metric_ir, dataset_name, seen)
        for metric_name in referenced_metrics
        if (referenced_metric_ir := model_ir.metrics.get(metric_name)) is not None
    )


def describe(name: str, project: SemanticProject | None = None) -> dict[str, Any]:
    target = _project(project)
    ensure_loaded(target)

    model_name, separator, leaf_name = name.partition(".")
    if not separator or not model_name or not leaf_name:
        raise PySemanticNotFound("semantic object", name)

    model_ir = target.registry.models.get(model_name)
    if model_ir is None:
        raise PySemanticNotFound("semantic object", name)

    datasource_ir = model_ir.datasources.get(leaf_name)
    dataset_ir = model_ir.datasets.get(leaf_name)
    metric_ir = model_ir.metrics.get(leaf_name)
    match_count = sum(
        semantic_ir is not None for semantic_ir in (datasource_ir, dataset_ir, metric_ir)
    )
    if match_count > 1:
        raise PySemanticNotFound("ambiguous semantic object", name)

    if datasource_ir is not None:
        return {
            "kind": "datasource",
            "model": model_name,
            "name": datasource_ir.name,
            "backend_type": datasource_ir.backend_type,
            "description": datasource_ir.description,
        }

    if dataset_ir is not None:
        return {
            "kind": "dataset",
            "model": model_name,
            "name": dataset_ir.name,
            "datasource": dataset_ir.datasource_name,
            "primary_key": list(dataset_ir.primary_key),
            "description": dataset_ir.description,
        }

    if metric_ir is not None:
        dataset_ref = metric_ir.references.datasets[0] if metric_ir.references.datasets else None
        return {
            "kind": "metric",
            "model": model_name,
            "name": metric_ir.name,
            "dataset": dataset_ref,
            "description": metric_ir.description,
        }

    raise PySemanticNotFound("semantic object", name)


def get_model(name: str, project: SemanticProject | None = None) -> ModelIR:
    target = _project(project)
    ensure_loaded(target)
    try:
        return target.registry.models[name]
    except KeyError as exc:
        raise PySemanticNotFound("model", name) from exc


def get_dataset(
    model: str,
    dataset: str,
    project: SemanticProject | None = None,
) -> DatasetIR:
    model_ir = get_model(model, project)
    try:
        return model_ir.datasets[dataset]
    except KeyError as exc:
        raise PySemanticNotFound("dataset", f"{model}.{dataset}") from exc


def get_metric(
    model: str,
    metric: str,
    project: SemanticProject | None = None,
) -> MetricIR:
    model_ir = get_model(model, project)
    try:
        return model_ir.metrics[metric]
    except KeyError as exc:
        raise PySemanticNotFound("metric", f"{model}.{metric}") from exc


def get_field(
    model: str,
    dataset: str,
    field: str,
    project: SemanticProject | None = None,
) -> FieldIR:
    dataset_ir = get_dataset(model, dataset, project)
    try:
        return dataset_ir.fields[field]
    except KeyError as exc:
        raise PySemanticNotFound("field", f"{model}.{dataset}.{field}") from exc


def _backend_for(
    datasource_name: str,
    backend_factory: BackendFactory,
    backend_cache: dict[str, Any] | None,
) -> Any:
    if backend_cache is None:
        return backend_factory(datasource_name)
    if datasource_name not in backend_cache:
        backend_cache[datasource_name] = backend_factory(datasource_name)
    return backend_cache[datasource_name]


def _materialize_dataset_ir(
    dataset_ir: DatasetIR,
    backend_factory: BackendFactory,
    backend_cache: dict[str, Any] | None = None,
) -> Any:
    backend = _backend_for(dataset_ir.datasource_name, backend_factory, backend_cache)
    return dataset_ir.fn(backend)


def materialize_dataset(
    model: str,
    dataset: str,
    backend_factory: BackendFactory,
    project: SemanticProject | None = None,
) -> Any:
    dataset_ir = get_dataset(model, dataset, project)
    try:
        return _materialize_dataset_ir(dataset_ir, backend_factory)
    except PySemanticNotFound:
        raise
    except SemanticRuntimeError:
        raise
    except Exception as exc:
        raise SemanticRuntimeError(
            phase="runtime",
            kind="DatasetMaterializationFailed",
            location=dataset_ir.source_location,
            function=dataset_ir.fn.__name__,
            message=f"Failed to materialize dataset '{model}.{dataset}': {exc}",
            hint="Check the dataset function and backend factory.",
            refs=[f"dataset:{model}.{dataset}"],
        ) from exc


def materialize_field(
    model: str,
    dataset: str,
    field: str,
    backend_factory: BackendFactory,
    project: SemanticProject | None = None,
) -> Any:
    field_ir = get_field(model, dataset, field, project)
    try:
        table = materialize_dataset(model, dataset, backend_factory, project)
        return field_ir.fn(table)
    except PySemanticNotFound:
        raise
    except SemanticRuntimeError:
        raise
    except Exception as exc:
        raise SemanticRuntimeError(
            phase="runtime",
            kind="FieldMaterializationFailed",
            location=field_ir.source_location,
            function=field_ir.fn.__name__,
            message=f"Failed to materialize field '{model}.{dataset}.{field}': {exc}",
            hint="Check the field function and referenced dataset.",
            refs=[f"field:{model}.{dataset}.{field}"],
        ) from exc


def materialize_metric(
    model: str,
    metric: str,
    backend_factory: BackendFactory,
    project: SemanticProject | None = None,
) -> Any:
    metric_ir = get_metric(model, metric, project)
    try:
        backend_cache: dict[str, Any] = {}
        datasets = {
            dataset_name: _materialize_dataset_ir(
                get_dataset(model, dataset_name, project),
                backend_factory,
                backend_cache,
            )
            for dataset_name in metric_ir.references.datasets
        }
        return metric_ir.fn(**datasets)
    except PySemanticNotFound:
        raise
    except SemanticRuntimeError:
        raise
    except Exception as exc:
        raise SemanticRuntimeError(
            phase="runtime",
            kind="MetricMaterializationFailed",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Failed to materialize metric '{model}.{metric}': {exc}",
            hint="Check the metric function, referenced datasets, and backend factory.",
            refs=[f"metric:{model}.{metric}"],
        ) from exc
