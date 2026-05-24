from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from numbers import Number
from typing import Any

from marivo.semantic_py import reader
from marivo.semantic_py.errors import PySemanticNotFound, SemanticParityError
from marivo.semantic_py.registry import SemanticProject

BackendFactory = Callable[[str], Any]


@dataclass(frozen=True)
class ParityResult:
    ok: bool
    metric_value: Any
    sql_value: Any
    source_sql: str
    source_dialect: str | None
    source_document: str | None


def _scalar(value: Any) -> Any:
    return _extract_scalar(value, kind="ResultShapeInvalid", refs=[])


def _extract_scalar(value: Any, *, kind: str, refs: list[str]) -> Any:
    if hasattr(value, "iloc"):
        shape = getattr(value, "shape", None)
        if shape == (1, 1):
            return value.iloc[0, 0]
        if shape == (1,):
            return value.iloc[0]
        raise _parity_error(
            kind=kind,
            message=f"Expected a scalar result, got shape {shape}.",
            hint="Parity checks require exactly one row and one column.",
            refs=refs,
        )
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError as exc:
            raise _parity_error(
                kind=kind,
                message="Expected a scalar result, got a non-scalar array-like value.",
                hint="Parity checks require exactly one scalar item.",
                refs=refs,
            ) from exc
    if isinstance(value, list | tuple):
        if len(value) == 1:
            return _extract_scalar(value[0], kind=kind, refs=refs)
        raise _parity_error(
            kind=kind,
            message=f"Expected a scalar result, got {len(value)} items.",
            hint="Parity checks require exactly one scalar item.",
            refs=refs,
        )
    return value


def _parity_error(
    *,
    kind: str,
    message: str,
    refs: list[str],
    hint: str | None = None,
    function: str | None = None,
) -> SemanticParityError:
    return SemanticParityError(
        phase="parity",
        kind=kind,
        location=None,
        function=function,
        message=message,
        hint=hint,
        refs=refs,
    )


def compare_metric_to_source_sql(
    *,
    project: SemanticProject,
    model: str,
    metric: str,
    backend_factory: BackendFactory,
    rel_tol: float = 0.0,
    abs_tol: float = 0.0,
) -> ParityResult:
    try:
        metric_ir = reader.get_metric(model=model, metric=metric, project=project)
    except PySemanticNotFound as exc:
        raise _not_found_parity_error(exc=exc, model=model, metric=metric) from exc

    if metric_ir.source is None or not metric_ir.source.sql or not metric_ir.source.sql.strip():
        raise SemanticParityError(
            phase="parity",
            kind="SourceSqlMissing",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Metric '{model}.{metric}' does not define source_sql.",
            hint="Add source_sql to the metric decorator before running parity checks.",
            refs=[f"metric:{model}.{metric}"],
        )

    metric_ref = f"metric:{model}.{metric}"
    datasource_name = _metric_datasource(project=project, model=model, metric=metric)
    datasource_backend_type = _datasource_backend_type(
        project=project,
        model=model,
        datasource=datasource_name,
        metric_ref=metric_ref,
    )
    _validate_source_dialect(
        source_dialect=metric_ir.source.dialect,
        backend_type=datasource_backend_type,
        metric_ref=metric_ref,
        datasource_ref=f"datasource:{model}.{datasource_name}",
        function=metric_ir.fn.__name__,
    )
    backend_cache: dict[str, Any] = {}

    def cached_backend_factory(name: str) -> Any:
        if name not in backend_cache:
            backend_cache[name] = backend_factory(name)
        return backend_cache[name]

    try:
        metric_expr = reader.materialize_metric(
            model=model,
            metric=metric,
            backend_factory=cached_backend_factory,
            project=project,
        )
    except PySemanticNotFound as exc:
        raise _not_found_parity_error(
            exc=exc,
            model=model,
            metric=metric,
            function=metric_ir.fn.__name__,
            location=metric_ir.source_location,
        ) from exc
    except Exception as exc:
        raise SemanticParityError(
            phase="parity",
            kind="MetricExecutionFailed",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Failed to materialize metric '{model}.{metric}': {exc}",
            hint="Check the metric function, referenced datasets, and backend factory.",
            refs=[metric_ref],
        ) from exc

    try:
        backend = cached_backend_factory(datasource_name)
        sql_expr = backend.sql(metric_ir.source.sql)
    except Exception as exc:
        raise SemanticParityError(
            phase="parity",
            kind="SourceSqlExecutionFailed",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Failed to prepare source_sql for metric '{model}.{metric}': {exc}",
            hint="Check the metric source_sql and source SQL datasource.",
            refs=[metric_ref],
        ) from exc

    try:
        metric_raw_value = metric_expr.execute()
    except Exception as exc:
        raise SemanticParityError(
            phase="parity",
            kind="MetricExecutionFailed",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Failed to execute metric '{model}.{metric}': {exc}",
            hint="Check the metric expression and backend connection.",
            refs=[metric_ref],
        ) from exc

    try:
        sql_raw_value = sql_expr.execute()
    except Exception as exc:
        raise SemanticParityError(
            phase="parity",
            kind="SourceSqlExecutionFailed",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Failed to execute source_sql for metric '{model}.{metric}': {exc}",
            hint="Check the metric source_sql and source SQL datasource.",
            refs=[metric_ref],
        ) from exc

    metric_value = _extract_scalar(
        metric_raw_value,
        kind="MetricResultShapeInvalid",
        refs=[metric_ref],
    )
    sql_value = _extract_scalar(
        sql_raw_value,
        kind="SourceSqlResultShapeInvalid",
        refs=[metric_ref],
    )
    ok = _comparison_ok(
        metric_value,
        sql_value,
        metric_ref=metric_ref,
        rel_tol=rel_tol,
        abs_tol=abs_tol,
    )
    return ParityResult(
        ok=ok,
        metric_value=metric_value,
        sql_value=sql_value,
        source_sql=metric_ir.source.sql,
        source_dialect=metric_ir.source.dialect,
        source_document=metric_ir.source.document,
    )


def _comparison_ok(
    metric_value: Any,
    sql_value: Any,
    *,
    metric_ref: str,
    rel_tol: float = 0.0,
    abs_tol: float = 0.0,
) -> bool:
    if _is_numeric_scalar(metric_value) and _is_numeric_scalar(sql_value):
        return math.isclose(
            float(metric_value),
            float(sql_value),
            rel_tol=rel_tol,
            abs_tol=abs_tol,
        )
    comparison = metric_value == sql_value
    if isinstance(comparison, bool):
        return comparison
    if hasattr(comparison, "item"):
        try:
            item = comparison.item()
        except ValueError as exc:
            raise _parity_error(
                kind="ComparisonResultInvalid",
                message="Metric/source SQL comparison did not produce a scalar boolean.",
                hint="Parity comparison requires scalar metric and source SQL values.",
                refs=[metric_ref],
            ) from exc
        if isinstance(item, bool):
            return item
    raise _parity_error(
        kind="ComparisonResultInvalid",
        message="Metric/source SQL comparison did not produce a boolean.",
        hint="Parity comparison requires scalar metric and source SQL values.",
        refs=[metric_ref],
    )


def _is_numeric_scalar(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def _metric_datasource(*, project: SemanticProject, model: str, metric: str) -> str:
    try:
        metric_ir = reader.get_metric(model=model, metric=metric, project=project)
    except PySemanticNotFound as exc:
        raise _not_found_parity_error(exc=exc, model=model, metric=metric) from exc

    metric_ref = f"metric:{model}.{metric}"
    if not metric_ir.references.datasets:
        raise SemanticParityError(
            phase="parity",
            kind="MetricDatasetMissing",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Metric '{model}.{metric}' does not reference a dataset for source SQL execution.",
            hint="Source SQL parity checks need at least one referenced dataset datasource.",
            refs=[metric_ref],
        )

    datasources: dict[str, list[str]] = {}
    dataset_refs: list[str] = []
    for dataset_name in metric_ir.references.datasets:
        dataset_ref = f"dataset:{model}.{dataset_name}"
        dataset_refs.append(dataset_ref)
        try:
            dataset_ir = reader.get_dataset(model=model, dataset=dataset_name, project=project)
        except PySemanticNotFound as exc:
            raise _not_found_parity_error(
                exc=exc,
                model=model,
                metric=metric,
                function=metric_ir.fn.__name__,
                location=metric_ir.source_location,
            ) from exc
        if not dataset_ir.datasource_name:
            raise SemanticParityError(
                phase="parity",
                kind="SourceSqlDatasourceMissing",
                location=dataset_ir.source_location,
                function=dataset_ir.fn.__name__,
                message=f"Dataset '{model}.{dataset_name}' does not define a datasource.",
                hint="Attach the dataset to a datasource before running source SQL parity checks.",
                refs=[metric_ref, dataset_ref],
            )
        datasources.setdefault(dataset_ir.datasource_name, []).append(dataset_name)

    if len(datasources) > 1:
        raise SemanticParityError(
            phase="parity",
            kind="SourceSqlDatasourceAmbiguous",
            location=metric_ir.source_location,
            function=metric_ir.fn.__name__,
            message=f"Metric '{model}.{metric}' references datasets from multiple datasources.",
            hint="Source SQL parity checks require all referenced datasets to share one datasource.",
            refs=[metric_ref, *dataset_refs],
        )

    return next(iter(datasources))


def _datasource_backend_type(
    *,
    project: SemanticProject,
    model: str,
    datasource: str,
    metric_ref: str,
) -> str | None:
    datasource_ref = f"datasource:{model}.{datasource}"
    try:
        return project.registry.models[model].datasources[datasource].backend_type
    except KeyError as exc:
        raise _parity_error(
            kind="ParityReferenceMissing",
            message=f"Datasource '{model}.{datasource}' referenced by parity inputs was not found.",
            hint="Check dataset datasource references before running source SQL parity checks.",
            refs=[metric_ref, datasource_ref],
        ) from exc


def _validate_source_dialect(
    *,
    source_dialect: str | None,
    backend_type: str | None,
    metric_ref: str,
    datasource_ref: str,
    function: str,
) -> None:
    normalized_source_dialect = source_dialect.strip().lower() if source_dialect else ""
    if not normalized_source_dialect:
        raise _parity_error(
            kind="SourceDialectMissing",
            message="Metric source_sql does not declare source_dialect.",
            hint="Set source_dialect to the SQL dialect used by source_sql before running parity checks.",
            refs=[metric_ref, datasource_ref],
            function=function,
        )
    normalized_backend_type = backend_type.strip().lower() if backend_type else ""
    if not normalized_backend_type:
        raise _parity_error(
            kind="SourceBackendTypeMissing",
            message="Datasource used for source SQL parity does not declare backend_type.",
            hint="Set backend_type on the datasource before running source SQL parity checks.",
            refs=[metric_ref, datasource_ref],
            function=function,
        )
    if normalized_source_dialect == normalized_backend_type:
        return
    raise _parity_error(
        kind="SourceDialectMismatch",
        message=(
            f"Metric source_dialect '{source_dialect}' does not match datasource "
            f"backend_type '{backend_type}'."
        ),
        hint="Use source_sql written for the datasource backend before running parity checks.",
        refs=[metric_ref, datasource_ref],
        function=function,
    )


def _not_found_parity_error(
    *,
    exc: PySemanticNotFound,
    model: str,
    metric: str,
    function: str | None = None,
    location: Any | None = None,
) -> SemanticParityError:
    metric_ref = f"metric:{model}.{metric}"
    if exc.entity == "metric":
        return SemanticParityError(
            phase="parity",
            kind="MetricMissing",
            location=location,
            function=function,
            message=f"Metric '{model}.{metric}' was not found.",
            hint="Check the model and metric name before running parity checks.",
            refs=[metric_ref],
        )
    if exc.entity == "dataset":
        dataset_ref = f"dataset:{exc.name}"
        return SemanticParityError(
            phase="parity",
            kind="MetricDatasetMissing",
            location=location,
            function=function,
            message=f"Dataset '{exc.name}' referenced by metric '{model}.{metric}' was not found.",
            hint="Check the metric function parameters and registered datasets.",
            refs=[metric_ref, dataset_ref],
        )
    return SemanticParityError(
        phase="parity",
        kind="ParityReferenceMissing",
        location=location,
        function=function,
        message=f"Reference '{exc.name}' of kind '{exc.entity}' was not found.",
        hint="Check semantic project references before running parity checks.",
        refs=[metric_ref],
    )
