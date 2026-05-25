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


def list_metrics(project: SemanticProject | None = None) -> list[str]:
    target = _project(project)
    ensure_loaded(target)
    return sorted(
        f"{model_name}.{metric_name}"
        for model_name, model_ir in target.registry.models.items()
        for metric_name in model_ir.metrics
    )


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
