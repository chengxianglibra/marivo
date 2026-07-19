"""Exact semantic-reference boundaries for analysis operators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NoReturn

from marivo.analysis._semantic_types import AnalysisDimensionRef as AnalysisDimensionRef
from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.analysis.runtime_metric import RuntimeMetricExpr
from marivo.analysis.slice_types import SliceValue
from marivo.refs import SemanticRef
from marivo.semantic.catalog import CatalogObject, SemanticCatalog, SemanticKind
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.refs import DimensionRef, MetricRef, TimeDimensionRef

type ObserveMetricInput = MetricRef | RuntimeMetricExpr


def _typed_catalog_id(ref: str, kind: SemanticKind) -> str:
    return f"{kind.value}.{ref}"


def _available_ids(catalog: SemanticCatalog, *, kind: SemanticKind) -> list[str]:
    return sorted(catalog._require_index().semantic_ids(kind))


def _available_dimension_ids(catalog: SemanticCatalog) -> list[str]:
    index = catalog._require_index()
    ids: list[str] = []
    for entity_ref in index.semantic_ids(SemanticKind.ENTITY):
        scope_id = f"entity.{entity_ref}"
        ids.extend(index.semantic_ids(SemanticKind.DIMENSION, scope_id=scope_id))
        ids.extend(index.semantic_ids(SemanticKind.TIME_DIMENSION, scope_id=scope_id))
    return sorted(ids)


def _resolve_unique_typed_id(ref: str, available_ids: Sequence[str]) -> str:
    """Resolve an exact typed ref's id to one canonical catalog id."""

    if ref in available_ids:
        return ref
    matches = [candidate for candidate in available_ids if candidate.endswith(f".{ref}")]
    return matches[0] if len(matches) == 1 else ref


def _repair_snippets() -> list[str]:
    return [
        "Use loaded_object.ref instead of the loaded catalog object.",
        "session.catalog.domains.show()",
        "session.catalog.domains.get('<domain>').metrics.refs()",
        ("session.catalog.domains.get('<domain>').entities.get('<entity>').dimensions.refs()"),
        ("session.catalog.domains.get('<domain>').entities.get('<entity>').time_dimensions.refs()"),
    ]


def _received(value: object) -> tuple[str, str]:
    if isinstance(value, CatalogObject):
        return str(value.ref.kind), type(value).__name__
    if isinstance(value, SemanticRef):
        return str(value.kind), type(value).__name__
    return type(value).__name__, type(value).__name__


def _reject_exact(
    value: object,
    *,
    argument: str,
    expected_kind: str,
    expected_type: str,
    available_ids: Sequence[str] | None = None,
    actual_kind_override: str | None = None,
) -> NoReturn:
    actual_kind, actual_type = _received(value)
    if actual_kind_override is not None:
        actual_kind = actual_kind_override
    ref = getattr(getattr(value, "ref", value), "id", str(value))
    context: dict[str, object] = {
        "argument": argument,
        "ref": ref,
        "expected_kind": expected_kind,
        "expected_type": expected_type,
        "actual_kind": actual_kind,
        "actual_type": actual_type,
        "repair": _repair_snippets(),
    }
    if available_ids is not None:
        context["available_ids"] = list(available_ids)
    raise SemanticKindMismatchError(
        message=(f"{argument} requires exact {expected_type}; got {actual_type} ({actual_kind})."),
        hint=(
            "Pass the exact typed .ref from catalog navigation; loaded catalog objects, "
            "generic SemanticRef values, bare ids, and wrong-kind refs are not accepted."
        ),
        context=context,
    )


def _actual_catalog_kind(catalog: SemanticCatalog, ref: str) -> SemanticKind | None:
    return catalog._require_index().kind_of(ref)


def normalize_metric_input(catalog: SemanticCatalog, metric: MetricRef) -> str:
    """Validate and return one exact catalog metric ref id."""
    if not isinstance(metric, MetricRef):
        _reject_exact(
            metric,
            argument="metric",
            expected_kind="metric",
            expected_type="MetricRef",
            available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
        )
    ref = _resolve_unique_typed_id(
        metric.id,
        _available_ids(catalog, kind=SemanticKind.METRIC),
    )
    try:
        obj = catalog.get(_typed_catalog_id(ref, SemanticKind.METRIC))
    except SemanticRuntimeError as exc:
        if exc.kind != "not_found":
            raise
        actual_kind = _actual_catalog_kind(catalog, ref)
        if actual_kind is not None:
            _reject_exact(
                metric,
                argument="metric",
                expected_kind="metric",
                expected_type="MetricRef",
                available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
                actual_kind_override=str(actual_kind),
            )
        raise MetricNotFoundError(
            message=f"metric {ref!r} not found",
            hint="Use session.catalog.domains.get('<name>').metrics.refs() to browse refs.",
            context={
                "metric": ref,
                "metric_id": ref,
                "available_ids": _available_ids(catalog, kind=SemanticKind.METRIC),
            },
        ) from exc
    if obj.ref.kind != SemanticKind.METRIC:
        _reject_exact(
            metric,
            argument="metric",
            expected_kind="metric",
            expected_type="MetricRef",
            available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
        )
    return ref


def normalize_dimension_input(
    catalog: SemanticCatalog,
    dimension: AnalysisDimensionRef,
    *,
    argument: str = "dimension",
) -> str:
    """Validate and return one exact dimension or time-dimension ref id."""
    if not isinstance(dimension, DimensionRef | TimeDimensionRef):
        _reject_exact(
            dimension,
            argument=argument,
            expected_kind="dimension",
            expected_type="DimensionRef or TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
        )
    expected_kind = (
        SemanticKind.TIME_DIMENSION
        if isinstance(dimension, TimeDimensionRef)
        else SemanticKind.DIMENSION
    )
    available_dimension_ids = _available_dimension_ids(catalog)
    expected_ids = [
        semantic_id
        for semantic_id in available_dimension_ids
        if catalog._require_index().registry.dimensions[semantic_id].is_time_dimension
        == (expected_kind == SemanticKind.TIME_DIMENSION)
    ]
    ref = _resolve_unique_typed_id(dimension.id, expected_ids)
    if not available_dimension_ids:
        return ref
    try:
        obj = catalog.get(_typed_catalog_id(ref, expected_kind))
    except SemanticRuntimeError as exc:
        if exc.kind != "not_found":
            raise
        actual_kind = _actual_catalog_kind(catalog, dimension.id)
        if actual_kind is not None:
            _reject_exact(
                dimension,
                argument=argument,
                expected_kind="dimension",
                expected_type="DimensionRef or TimeDimensionRef",
                available_ids=_available_dimension_ids(catalog),
                actual_kind_override=str(actual_kind),
            )
        _reject_exact(
            dimension,
            argument=argument,
            expected_kind="dimension",
            expected_type="DimensionRef or TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
            actual_kind_override="not_found",
        )
    if obj.ref.kind != expected_kind:
        _reject_exact(
            dimension,
            argument=argument,
            expected_kind="dimension",
            expected_type="DimensionRef or TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
        )
    return ref


def normalize_time_dimension_input(
    catalog: SemanticCatalog,
    time_dimension: TimeDimensionRef,
    *,
    argument: str = "time_dimension",
) -> str:
    """Validate and return one exact time-dimension ref id."""
    if not isinstance(time_dimension, TimeDimensionRef):
        _reject_exact(
            time_dimension,
            argument=argument,
            expected_kind="time_dimension",
            expected_type="TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
        )
    expected_ids = [
        semantic_id
        for semantic_id, dimension in catalog._require_index().registry.dimensions.items()
        if dimension.is_time_dimension
    ]
    ref = _resolve_unique_typed_id(time_dimension.id, expected_ids)
    try:
        obj = catalog.get(_typed_catalog_id(ref, SemanticKind.TIME_DIMENSION))
    except SemanticRuntimeError as exc:
        if exc.kind != "not_found":
            raise
        _reject_exact(
            time_dimension,
            argument=argument,
            expected_kind="time_dimension",
            expected_type="TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
        )
    if obj.ref.kind != SemanticKind.TIME_DIMENSION:
        _reject_exact(
            time_dimension,
            argument=argument,
            expected_kind="time_dimension",
            expected_type="TimeDimensionRef",
            available_ids=_available_dimension_ids(catalog),
        )
    return ref


def normalize_dimension_boundary(
    catalog: SemanticCatalog,
    dimension: AnalysisDimensionRef,
    *,
    argument: str = "dimension",
) -> str:
    """Normalize an exact analysis dimension ref at a public boundary."""
    return normalize_dimension_input(catalog, dimension, argument=argument)


def normalize_dimension_inputs(
    catalog: SemanticCatalog,
    dimensions: Sequence[AnalysisDimensionRef] | None,
) -> list[str]:
    """Normalize a dimension sequence to semantic ids."""
    return [
        normalize_dimension_input(catalog, dimension, argument="dimensions")
        for dimension in dimensions or ()
    ]


def normalize_where_inputs(
    catalog: SemanticCatalog,
    where: Mapping[AnalysisDimensionRef, SliceValue] | None,
) -> dict[str, SliceValue]:
    """Normalize a typed read-only slice mapping to an isolated string-key copy."""
    if where is None:
        return {}
    return {
        normalize_dimension_input(catalog, key, argument="slice_by"): value
        for key, value in where.items()
    }
