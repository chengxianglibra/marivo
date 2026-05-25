from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterable
from typing import Any

from marivo.semantic_py.errors import (
    DatasourceNotRegisteredError,
    SemanticAssemblyError,
    SemanticDecoratorError,
    SemanticLoadError,
    SourceLocation,
)
from marivo.semantic_py.ir import FieldIR, MetricIR, ModelIR, RelationshipIR
from marivo.semantic_py.registry import PySemanticRegistry

_FORBIDDEN_AST_NODES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Raise,
    ast.Global,
    ast.Nonlocal,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.NamedExpr,
)


def _source_location(fn: Any, *, relative_line: int = 1) -> SourceLocation:
    return SourceLocation(
        file=inspect.getsourcefile(fn) or "<unknown>",
        line=fn.__code__.co_firstlineno + relative_line - 1,
    )


def _decorator_error(
    *,
    kind: str,
    fn: Any,
    decorator: str,
    message: str,
    hint: str | None,
    relative_line: int = 1,
) -> SemanticDecoratorError:
    return SemanticDecoratorError(
        phase="decorator",
        kind=kind,
        location=_source_location(fn, relative_line=relative_line),
        function=getattr(fn, "__name__", None),
        message=message,
        hint=hint,
        refs=[decorator],
    )


def _function_node(fn: Any, *, decorator: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError) as exc:
        raise _decorator_error(
            kind="SourceUnavailable",
            fn=fn,
            decorator=decorator,
            message=f"Could not inspect source for @{decorator} function.",
            hint="Define semantic functions in regular Python source files.",
        ) from exc

    try:
        module = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise _decorator_error(
            kind="SourceUnavailable",
            fn=fn,
            decorator=decorator,
            message=f"Could not parse source for @{decorator} function.",
            hint="Check that the decorated function source is syntactically valid.",
        ) from exc

    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node

    raise _decorator_error(
        kind="SourceUnavailable",
        fn=fn,
        decorator=decorator,
        message=f"Could not locate @{decorator} function definition in source.",
        hint="Decorate a named function defined in regular Python source.",
    )


def validate_function_body(fn: Any, *, decorator: str) -> None:
    function_node = _function_node(fn, decorator=decorator)
    if isinstance(function_node, ast.AsyncFunctionDef):
        raise _decorator_error(
            kind="AstNodeForbidden",
            fn=fn,
            decorator=decorator,
            message=f"@{decorator} function body cannot be async.",
            hint="Use a regular function that returns an ibis expression.",
            relative_line=getattr(function_node, "lineno", 1),
        )
    statements = list(function_node.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]

    for statement in statements:
        for node in ast.walk(statement):
            if isinstance(node, _FORBIDDEN_AST_NODES):
                node_name = type(node).__name__
                raise _decorator_error(
                    kind="AstNodeForbidden",
                    fn=fn,
                    decorator=decorator,
                    message=f"@{decorator} function body cannot contain {node_name}.",
                    hint="Use a single return expression without assignments, control flow, imports, or nested definitions.",
                    relative_line=getattr(node, "lineno", 1),
                )

    if (
        len(statements) != 1
        or not isinstance(statements[0], ast.Return)
        or statements[0].value is None
    ):
        relative_line = (
            getattr(statements[0], "lineno", getattr(function_node, "lineno", 1))
            if statements
            else 1
        )
        raise _decorator_error(
            kind="FunctionBodyInvalid",
            fn=fn,
            decorator=decorator,
            message=f"@{decorator} function body must contain exactly one return expression.",
            hint="Use a single return expression built from ibis operations; an optional leading docstring is allowed.",
            relative_line=relative_line,
        )


def _assembly_error(
    *,
    kind: str,
    location: SourceLocation,
    function: str | None,
    message: str,
    hint: str | None,
    refs: Iterable[str] = (),
) -> SemanticAssemblyError:
    return SemanticAssemblyError(
        phase="assembly",
        kind=kind,
        location=location,
        function=function,
        message=message,
        hint=hint,
        refs=list(refs),
    )


def _metric_reference_names(metric: MetricIR) -> list[str]:
    references: list[str] = []
    for name in (
        metric.decomposition.numerator,
        metric.decomposition.denominator,
        metric.decomposition.weight,
    ):
        if name is not None:
            references.append(name)
    references.extend(metric.references.metrics)
    return references


def _validate_metric(model: ModelIR, metric: MetricIR) -> list[SemanticAssemblyError]:
    errors: list[SemanticAssemblyError] = []
    for dataset_name in metric.references.datasets:
        if dataset_name not in model.datasets:
            errors.append(
                _assembly_error(
                    kind="MetricDatasetMissing",
                    location=metric.source_location,
                    function=metric.fn.__name__,
                    message=f"Metric '{metric.name}' references missing dataset '{dataset_name}'.",
                    hint="Register a dataset with the same name as the metric function parameter.",
                    refs=[f"dataset:{dataset_name}", f"metric:{metric.name}"],
                )
            )

    for metric_name in _metric_reference_names(metric):
        if metric_name not in model.metrics:
            errors.append(
                _assembly_error(
                    kind="MetricReferenceMissing",
                    location=metric.source_location,
                    function=metric.fn.__name__,
                    message=f"Metric '{metric.name}' references missing metric '{metric_name}'.",
                    hint="Register referenced metrics before loading the semantic project.",
                    refs=[f"metric:{metric.name}", f"metric:{metric_name}"],
                )
            )
    return errors


def _validate_time_field(field: FieldIR) -> list[SemanticAssemblyError]:
    if not field.is_time or field.time_meta is None:
        return []
    if field.time_meta.granularity != "hour" or field.time_meta.required_prefix is not None:
        return []
    return [
        _assembly_error(
            kind="TimeFieldPrefixMissing",
            location=field.source_location,
            function=field.fn.__name__,
            message=f"Hour time field '{field.name}' requires a prefix time field.",
            hint="Set required_prefix to the day/date time field that owns this hour partition.",
            refs=[f"dataset:{field.dataset_name}", f"time_field:{field.name}"],
        )
    ]


def _validate_dataset(model: ModelIR, dataset_name: str) -> list[SemanticAssemblyError]:
    dataset = model.datasets[dataset_name]
    if not dataset.datasource_name:
        return [
            _assembly_error(
                kind="DatasetDatasourceMissing",
                location=dataset.source_location,
                function=dataset.fn.__name__,
                message=f"Dataset '{dataset.name}' does not define a datasource.",
                hint="Register the dataset with a datasource before loading the semantic project.",
                refs=[f"dataset:{dataset.name}"],
            )
        ]
    if dataset.datasource_name not in model.datasources:
        return [
            DatasourceNotRegisteredError(
                phase="assembly",
                kind="DatasetDatasourceMissing",
                location=dataset.source_location,
                function=dataset.fn.__name__,
                message=(
                    f"Dataset '{dataset.name}' references missing datasource "
                    f"'{dataset.datasource_name}'."
                ),
                hint=("Register the referenced datasource before loading the semantic project."),
                refs=[
                    f"dataset:{dataset.name}",
                    f"datasource:{dataset.datasource_name}",
                ],
            )
        ]
    return []


def _validate_time_prefix(
    dataset_name: str, field: FieldIR, model: ModelIR
) -> list[SemanticAssemblyError]:
    if not field.is_time or field.time_meta is None or field.time_meta.required_prefix is None:
        return []
    dataset = model.datasets[dataset_name]
    prefix = dataset.fields.get(field.time_meta.required_prefix)
    if prefix is not None and prefix.is_time:
        return []
    return [
        _assembly_error(
            kind="TimeFieldPrefixMissing",
            location=field.source_location,
            function=field.fn.__name__,
            message=(
                f"Time field '{field.name}' requires prefix time field "
                f"'{field.time_meta.required_prefix}' on dataset '{dataset_name}'."
            ),
            hint="Set required_prefix to an existing time field on the same dataset.",
            refs=[
                f"dataset:{dataset_name}",
                f"time_field:{field.name}",
                f"time_field:{field.time_meta.required_prefix}",
            ],
        )
    ]


def _validate_relationship(
    model: ModelIR, relationship: RelationshipIR
) -> list[SemanticAssemblyError]:
    errors: list[SemanticAssemblyError] = []
    if not relationship.from_columns or not relationship.to_columns:
        errors.append(
            _assembly_error(
                kind="RelationshipColumnsEmpty",
                location=relationship.source_location,
                function=None,
                message=f"Relationship '{relationship.name}' must define at least one join column on each side.",
                hint="Set both from_columns and to_columns with one or more semantic field names.",
                refs=[f"relationship:{relationship.name}"],
            )
        )
    elif len(relationship.from_columns) != len(relationship.to_columns):
        errors.append(
            _assembly_error(
                kind="RelationshipColumnArityMismatch",
                location=relationship.source_location,
                function=None,
                message=(
                    f"Relationship '{relationship.name}' has {len(relationship.from_columns)} from columns "
                    f"but {len(relationship.to_columns)} to columns."
                ),
                hint="Join column lists must have the same number of fields in the same order.",
                refs=[f"relationship:{relationship.name}"],
            )
        )

    for label, dataset_name in (
        ("from", relationship.from_dataset),
        ("to", relationship.to_dataset),
    ):
        if dataset_name not in model.datasets:
            errors.append(
                _assembly_error(
                    kind="RelationshipDatasetMissing",
                    location=relationship.source_location,
                    function=None,
                    message=f"Relationship '{relationship.name}' references missing {label} dataset '{dataset_name}'.",
                    hint="Register both relationship endpoint datasets.",
                    refs=[f"relationship:{relationship.name}", f"dataset:{dataset_name}"],
                )
            )
            continue

        dataset = model.datasets[dataset_name]
        columns = relationship.from_columns if label == "from" else relationship.to_columns
        for column in columns:
            if column not in dataset.fields:
                errors.append(
                    _assembly_error(
                        kind="RelationshipColumnMissing",
                        location=relationship.source_location,
                        function=None,
                        message=(
                            f"Relationship '{relationship.name}' references missing "
                            f"{label} column '{column}' on dataset '{dataset_name}'."
                        ),
                        hint="Register relationship columns as fields on their endpoint datasets.",
                        refs=[
                            f"relationship:{relationship.name}",
                            f"dataset:{dataset_name}",
                            f"column:{dataset_name}.{column}",
                        ],
                    )
                )
    return errors


def validate_all(registry: PySemanticRegistry) -> None:
    errors: list[SemanticAssemblyError] = []
    for model in registry.models.values():
        for dataset_name, dataset in model.datasets.items():
            errors.extend(_validate_dataset(model, dataset_name))
            for field in dataset.fields.values():
                errors.extend(_validate_time_field(field))
                errors.extend(_validate_time_prefix(dataset_name, field, model))
        for metric in model.metrics.values():
            errors.extend(_validate_metric(model, metric))
        for relationship in model.relationships.values():
            errors.extend(_validate_relationship(model, relationship))

    if errors:
        registry.state = "errored"
        registry.load_errors = list(errors)
        raise SemanticLoadError(list(errors))

    registry.state = "ready"
    registry.load_errors.clear()
