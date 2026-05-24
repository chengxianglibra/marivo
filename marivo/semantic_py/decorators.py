from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from typing import Any, Literal, NoReturn, TypeVar

from marivo.semantic_py.builders import DecompositionSpec
from marivo.semantic_py.errors import SemanticDecoratorError, SourceLocation
from marivo.semantic_py.ir import (
    DatasetIR,
    DatasourceIR,
    DecompositionIR,
    FieldIR,
    MetricIR,
    MetricReferences,
    ModelIR,
    RelationshipIR,
    SourceProvenance,
    SymbolRef,
    TimeFieldMeta,
)
from marivo.semantic_py.registry import active_model_names, active_registry
from marivo.semantic_py.validator import validate_function_body

F = TypeVar("F", bound=Callable[..., Any])
SymbolKind = Literal["metric", "field", "time_field", "datasource"]


def _raise_decorator_error(
    *,
    kind: str,
    message: str,
    function: Callable[..., Any] | None = None,
    hint: str | None = None,
    refs: list[str] | None = None,
) -> NoReturn:
    raise SemanticDecoratorError(
        phase="decorator",
        kind=kind,
        location=_location(function) if function is not None else None,
        function=function.__name__ if function is not None else None,
        message=message,
        hint=hint,
        refs=refs or [],
    )


def _location(fn: Callable[..., Any]) -> SourceLocation:
    return SourceLocation(
        file=inspect.getsourcefile(fn) or "<unknown>",
        line=fn.__code__.co_firstlineno,
    )


def _current_model() -> ModelIR:
    registry = active_registry()
    selected_models = active_model_names()
    if selected_models:
        for candidate in selected_models:
            if candidate in registry.models:
                return registry.models[candidate]
        selected_model = "|".join(selected_models)
        raise SemanticDecoratorError(
            phase="decorator",
            kind="ModelRegistrationMissing",
            location=None,
            function=None,
            message=f"Active model '{selected_model}' is not registered.",
            hint="Call marivo.semantic_py.model() before declaring datasources, datasets, fields, metrics, or relationships.",
            refs=[f"model:{selected_model}"],
        )
    if not registry.models:
        _raise_decorator_error(
            kind="ModelRegistrationMissing",
            message="no active semantic model is registered.",
            hint="Call marivo.semantic_py.model() before declaring semantic objects.",
        )
    if len(registry.models) > 1:
        _raise_decorator_error(
            kind="ActiveModelAmbiguous",
            message="Active registry has multiple models; select one explicitly.",
            hint="Load through load_project() or use an explicit model context.",
            refs=[f"model:{name}" for name in sorted(registry.models)],
        )
    return next(iter(registry.models.values()))


def _name_from_ref(value: object, attr: str, expected_kind: SymbolKind) -> str:
    if isinstance(value, SymbolRef):
        if value.kind != expected_kind:
            _raise_decorator_error(
                kind="ReferenceKindMismatch",
                message=f"Expected {expected_kind} ref, got {value.kind}.",
                refs=[f"ref:{value.kind}.{value.name}"],
            )
        return value.name

    metadata = value if isinstance(value, dict) else getattr(value, attr, None)
    if isinstance(metadata, dict):
        name = metadata.get("name")
        if isinstance(name, str) and name:
            return name

    _raise_decorator_error(
        kind="ReferenceInvalid",
        message=f"Expected {expected_kind} ref or decorated function.",
    )


def _metadata_name_in_current_model(
    value: object,
    attr: str,
    expected_kind: SymbolKind,
) -> str | None:
    metadata = getattr(value, attr, None)
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    if not isinstance(name, str) or not name:
        return None
    model = metadata.get("model")
    current_model = _current_model()
    if isinstance(model, str) and model and model != current_model.name:
        _raise_decorator_error(
            kind="CrossModelReference",
            message=(
                f"{expected_kind} ref '{name}' belongs to model '{model}', "
                f"not '{current_model.name}'."
            ),
            refs=[f"{expected_kind}:{model}.{name}", f"model:{current_model.name}"],
        )
    return name


def _time_prefix_from_ref(value: object) -> tuple[str, str | None, str | None]:
    if isinstance(value, SymbolRef):
        if value.kind != "time_field":
            _raise_decorator_error(
                kind="ReferenceKindMismatch",
                message=f"Expected time_field ref, got {value.kind}.",
                refs=[f"ref:{value.kind}.{value.name}"],
            )
        return value.name, None, None
    metadata = getattr(value, "__marivo_time_field__", None)
    if isinstance(metadata, dict):
        name = metadata.get("name")
        dataset = metadata.get("dataset")
        model = metadata.get("model")
        if isinstance(name, str) and name:
            return (
                name,
                dataset if isinstance(dataset, str) and dataset else None,
                model if isinstance(model, str) and model else None,
            )
    _raise_decorator_error(
        kind="ReferenceInvalid",
        message="Expected time_field ref or decorated function.",
    )


def _dataset_name(value: str | object) -> str:
    if isinstance(value, str):
        return value
    metadata = getattr(value, "__marivo_dataset__", None)
    if isinstance(metadata, dict):
        name = metadata.get("name")
        model = metadata.get("model")
        if isinstance(name, str) and name:
            current_model = _current_model()
            if isinstance(model, str) and model and model != current_model.name:
                _raise_decorator_error(
                    kind="CrossModelReference",
                    message=(
                        f"dataset ref '{name}' belongs to model '{model}', "
                        f"not '{current_model.name}'."
                    ),
                    refs=[f"dataset:{model}.{name}", f"model:{current_model.name}"],
                )
            return name
    _raise_decorator_error(
        kind="ReferenceInvalid",
        message="Expected dataset name or decorated dataset function.",
    )


def _ensure_dataset(model_ir: ModelIR, dataset_name: str, location: SourceLocation) -> DatasetIR:
    dataset_ir = model_ir.datasets.get(dataset_name)
    if dataset_ir is not None:
        return dataset_ir

    def _placeholder(*_args: Any, **_kwargs: Any) -> None:
        return None

    dataset_ir = DatasetIR(
        name=dataset_name,
        fn=_placeholder,
        datasource_name="",
        primary_key=[],
        unique_keys=[],
        fields={},
        description=None,
        ai_context=None,
        source_location=location,
    )
    model_ir.datasets[dataset_name] = dataset_ir
    return dataset_ir


def _source(
    *,
    source_sql: str | None,
    source_dialect: str | None,
    source_document: str | None,
    source_notes: str | None,
) -> SourceProvenance | None:
    if all(value is None for value in (source_sql, source_dialect, source_document, source_notes)):
        return None
    return SourceProvenance(
        sql=source_sql,
        dialect=source_dialect,
        document=source_document,
        notes=source_notes,
    )


def _decomposition(spec: DecompositionSpec) -> DecompositionIR:
    numerator = _optional_ref_name(spec.numerator, "numerator")
    denominator = _optional_ref_name(spec.denominator, "denominator")
    weight = _optional_ref_name(spec.weight, "weight")
    return DecompositionIR(
        kind=spec.kind,
        numerator=numerator,
        denominator=denominator,
        weight=weight,
    )


def _optional_ref_name(value: object | None, label: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, SymbolRef):
        if value.kind != "metric":
            _raise_decorator_error(
                kind="ReferenceKindMismatch",
                message=f"{label} decomposition reference must be a metric ref.",
                refs=[f"ref:{value.kind}.{value.name}"],
            )
        return value.name
    metadata = getattr(value, "__marivo_metric__", None)
    if isinstance(metadata, dict):
        name = metadata.get("name")
        model = metadata.get("model")
        if isinstance(name, str) and name:
            current_model = _current_model()
            if isinstance(model, str) and model and model != current_model.name:
                _raise_decorator_error(
                    kind="CrossModelReference",
                    refs=[f"metric:{model}.{name}", f"model:{current_model.name}"],
                    message=(
                        f"{label} decomposition reference '{name}' belongs to model "
                        f"'{model}', not '{current_model.name}'"
                    ),
                )
            return name
    _raise_decorator_error(
        kind="ReferenceInvalid",
        message=f"Could not resolve {label} reference.",
    )


def model(
    *,
    name: str,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
) -> ModelIR:
    registry = active_registry()
    if name in registry.models:
        _raise_decorator_error(
            kind="DuplicateModel",
            message=f"Model '{name}' is already registered.",
            refs=[f"model:{name}"],
        )
    model_ir = ModelIR(name=name, description=description, ai_context=ai_context)
    registry.models[name] = model_ir
    return model_ir


def datasource(
    *,
    name: str | None = None,
    backend_type: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        ds_name = name or fn.__name__
        model_ir = _current_model()
        if ds_name in model_ir.datasources:
            _raise_decorator_error(
                kind="DuplicateDatasource",
                function=fn,
                message=f"Datasource '{ds_name}' is already registered.",
                refs=[f"datasource:{model_ir.name}.{ds_name}"],
            )
        model_ir.datasources[ds_name] = DatasourceIR(
            name=ds_name,
            backend_type=backend_type,
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
        )

        fn.__marivo_datasource__ = {"name": ds_name, "model": model_ir.name}  # type: ignore[attr-defined]
        return fn

    return decorate


def dataset(
    *,
    name: str | None = None,
    datasource: object,
    primary_key: Sequence[str] | None = None,
    unique_keys: Sequence[Sequence[str]] | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        dataset_name = name or fn.__name__
        model_ir = _current_model()
        datasource_name = _metadata_name_in_current_model(
            datasource, "__marivo_datasource__", "datasource"
        ) or _name_from_ref(datasource, "__marivo_datasource__", "datasource")
        existing = model_ir.datasets.get(dataset_name)
        if existing is not None and existing.datasource_name:
            _raise_decorator_error(
                kind="DuplicateDataset",
                function=fn,
                message=f"Dataset '{dataset_name}' is already registered.",
                refs=[f"dataset:{model_ir.name}.{dataset_name}"],
            )
        model_ir.datasets[dataset_name] = DatasetIR(
            name=dataset_name,
            fn=fn,
            datasource_name=datasource_name,
            primary_key=list(primary_key or []),
            unique_keys=[list(key) for key in unique_keys or []],
            fields={} if existing is None else existing.fields,
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
        )

        fn.__marivo_dataset__ = {"name": dataset_name, "model": model_ir.name}  # type: ignore[attr-defined]
        return fn

    return decorate


def field(
    *,
    dataset: str | object,
    name: str | None = None,
    label: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        field_name = name or fn.__name__
        dataset_name = _dataset_name(dataset)
        model_ir = _current_model()
        dataset_ir = _ensure_dataset(model_ir, dataset_name, _location(fn))
        if field_name in dataset_ir.fields:
            _raise_decorator_error(
                kind="DuplicateField",
                function=fn,
                message=f"Field '{field_name}' is already registered.",
                refs=[f"field:{model_ir.name}.{dataset_name}.{field_name}"],
            )
        dataset_ir.fields[field_name] = FieldIR(
            name=field_name,
            dataset_name=dataset_name,
            fn=fn,
            is_time=False,
            time_meta=None,
            label=label,
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
            source=_source(
                source_sql=source_sql,
                source_dialect=source_dialect,
                source_document=source_document,
                source_notes=source_notes,
            ),
        )

        fn.__marivo_field__ = {  # type: ignore[attr-defined]
            "name": field_name,
            "dataset": dataset_name,
            "model": model_ir.name,
        }
        return fn

    return decorate


def time_field(
    *,
    dataset: str | object,
    data_type: Literal["date", "timestamp", "string", "integer"],
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"],
    name: str | None = None,
    format: str | None = None,
    required_prefix: object | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        field_name = name or fn.__name__
        dataset_name = _dataset_name(dataset)
        model_ir = _current_model()
        dataset_ir = _ensure_dataset(model_ir, dataset_name, _location(fn))
        if field_name in dataset_ir.fields:
            _raise_decorator_error(
                kind="DuplicateTimeField",
                function=fn,
                message=f"Time field '{field_name}' is already registered.",
                refs=[f"time_field:{model_ir.name}.{dataset_name}.{field_name}"],
            )
        prefix, prefix_dataset, prefix_model = (
            (None, None, None)
            if required_prefix is None
            else _time_prefix_from_ref(required_prefix)
        )
        if prefix_model is not None and prefix_model != model_ir.name:
            _raise_decorator_error(
                kind="CrossModelReference",
                function=fn,
                message=(
                    f"Time field prefix '{prefix}' belongs to model '{prefix_model}', "
                    f"not '{model_ir.name}'."
                ),
                refs=[f"time_field:{prefix_model}.{prefix}", f"model:{model_ir.name}"],
            )
        if prefix_dataset is not None and prefix_dataset != dataset_name:
            _raise_decorator_error(
                kind="CrossDatasetReference",
                function=fn,
                message=(
                    f"Time field prefix '{prefix}' belongs to dataset '{prefix_dataset}', "
                    f"not '{dataset_name}'."
                ),
                refs=[
                    f"time_field:{model_ir.name}.{prefix_dataset}.{prefix}",
                    f"dataset:{model_ir.name}.{dataset_name}",
                ],
            )
        dataset_ir.fields[field_name] = FieldIR(
            name=field_name,
            dataset_name=dataset_name,
            fn=fn,
            is_time=True,
            time_meta=TimeFieldMeta(
                data_type=data_type,
                granularity=granularity,
                format=format,
                required_prefix=prefix,
            ),
            label="time",
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
        )

        fn.__marivo_time_field__ = {  # type: ignore[attr-defined]
            "name": field_name,
            "dataset": dataset_name,
            "model": model_ir.name,
        }
        return fn

    return decorate


def metric(
    *,
    decomposition: DecompositionSpec,
    name: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        validate_function_body(fn, decorator="metric")
        metric_name = name or fn.__name__
        model_ir = _current_model()
        if metric_name in model_ir.metrics:
            _raise_decorator_error(
                kind="DuplicateMetric",
                function=fn,
                message=f"Metric '{metric_name}' is already registered.",
                refs=[f"metric:{model_ir.name}.{metric_name}"],
            )
        model_ir.metrics[metric_name] = MetricIR(
            name=metric_name,
            model_name=model_ir.name,
            fn=fn,
            decomposition=_decomposition(decomposition),
            description=description,
            ai_context=ai_context,
            references=MetricReferences(datasets=list(inspect.signature(fn).parameters)),
            source_location=_location(fn),
            source=_source(
                source_sql=source_sql,
                source_dialect=source_dialect,
                source_document=source_document,
                source_notes=source_notes,
            ),
        )

        fn.__marivo_metric__ = {"name": metric_name, "model": model_ir.name}  # type: ignore[attr-defined]
        return fn

    return decorate


def relationship(
    *,
    name: str,
    from_: str,
    to: str,
    from_columns: Sequence[str],
    to_columns: Sequence[str],
    description: str | None = None,
) -> Callable[[F], F]:
    def decorate(fn: F) -> F:
        model_ir = _current_model()
        if name in model_ir.relationships:
            _raise_decorator_error(
                kind="DuplicateRelationship",
                function=fn,
                message=f"Relationship '{name}' is already registered.",
                refs=[f"relationship:{model_ir.name}.{name}"],
            )
        model_ir.relationships[name] = RelationshipIR(
            name=name,
            from_dataset=from_,
            to_dataset=to,
            from_columns=list(from_columns),
            to_columns=list(to_columns),
            source_location=_location(fn),
            description=description,
        )

        fn.__marivo_relationship__ = {"name": name}  # type: ignore[attr-defined]
        return fn

    return decorate
