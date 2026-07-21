"""Exact semantic-reference boundaries for analysis operators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NoReturn, cast

from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.analysis.slice_types import SliceValue
from marivo.refs import FieldKind, MetricKind, Ref, SemanticKind, SemanticKindTag
from marivo.semantic.catalog import CatalogEntry, SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def _available_refs(
    catalog: SemanticCatalog,
    *,
    kinds: frozenset[SemanticKind],
) -> list[str]:
    return sorted(
        entry.ref.key for entry in catalog._require_index()._by_ref.values() if entry.kind in kinds
    )


def _repair_snippets() -> list[str]:
    return [
        "Use loaded_entry.ref instead of the CatalogEntry.",
        "session.catalog.domains.show()",
        "session.catalog.domains.get('<domain>').metrics.refs",
        "session.catalog.domains.get('<domain>').entities.get('<entity>').dimensions.refs",
        "session.catalog.domains.get('<domain>').entities.get('<entity>').time_dimensions.refs",
    ]


def _received(value: object) -> tuple[str, str]:
    if isinstance(value, CatalogEntry):
        return value.kind.value, type(value).__name__
    if type(value) is Ref:
        return value.kind.value, "Ref"
    return type(value).__name__, type(value).__name__


def _reject_exact(
    value: object,
    *,
    argument: str,
    expected_kind: str,
    expected_type: str,
    available_refs: Sequence[str] | None = None,
) -> NoReturn:
    actual_kind, actual_type = _received(value)
    received_ref = value.ref if isinstance(value, CatalogEntry) else value
    ref_key = received_ref.key if type(received_ref) is Ref else None
    context: dict[str, object] = {
        "argument": argument,
        "ref": ref_key,
        "expected_kind": expected_kind,
        "expected_type": expected_type,
        "actual_kind": actual_kind,
        "actual_type": actual_type,
        "repair": _repair_snippets(),
    }
    if available_refs is not None:
        context["available_refs"] = list(available_refs)
    raise SemanticKindMismatchError(
        message=f"{argument} requires exact {expected_type}; got {actual_type} ({actual_kind}).",
        hint="Pass entry.ref or construct one exact ms.ref.<kind>(path).",
        context=context,
    )


def _require_member(
    catalog: SemanticCatalog,
    ref: Ref[SemanticKindTag],
    *,
    argument: str,
    expected_kind: str,
    expected_type: str,
    available_refs: Sequence[str],
) -> str:
    try:
        return catalog.require(ref).path
    except SemanticRuntimeError as exc:
        if exc.kind != ErrorKind.NOT_FOUND:
            raise
        if ref.kind is SemanticKind.METRIC:
            raise MetricNotFoundError(
                message=f"metric {ref.path!r} not found",
                hint="Use session.catalog.metrics.show() to browse refs.",
                context={
                    "metric": ref.path,
                    "metric_ref": ref.key,
                    "available_refs": list(available_refs),
                },
            ) from exc
        _reject_exact(
            ref,
            argument=argument,
            expected_kind=expected_kind,
            expected_type=expected_type,
            available_refs=available_refs,
        )


def normalize_metric_input(catalog: SemanticCatalog, metric: Ref[MetricKind]) -> str:
    """Validate and return one exact catalog metric path."""
    available = _available_refs(catalog, kinds=frozenset({SemanticKind.METRIC}))
    if type(metric) is not Ref or metric.kind is not SemanticKind.METRIC:
        _reject_exact(
            metric,
            argument="metric",
            expected_kind="metric",
            expected_type="Ref[metric]",
            available_refs=available,
        )
    return _require_member(
        catalog,
        cast("Ref[SemanticKindTag]", metric),
        argument="metric",
        expected_kind="metric",
        expected_type="Ref[metric]",
        available_refs=available,
    )


def normalize_dimension_input(
    catalog: SemanticCatalog,
    dimension: Ref[FieldKind],
    *,
    argument: str = "dimension",
) -> str:
    """Validate and return one exact dimension or time-dimension path."""
    kinds = frozenset({SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION})
    available = _available_refs(catalog, kinds=kinds)
    if type(dimension) is not Ref or dimension.kind not in kinds:
        _reject_exact(
            dimension,
            argument=argument,
            expected_kind="dimension or time_dimension",
            expected_type="Ref[dimension | time_dimension]",
            available_refs=available,
        )
    return _require_member(
        catalog,
        cast("Ref[SemanticKindTag]", dimension),
        argument=argument,
        expected_kind="dimension or time_dimension",
        expected_type="Ref[dimension | time_dimension]",
        available_refs=available,
    )


def normalize_time_dimension_input(
    catalog: SemanticCatalog,
    time_dimension: Ref[FieldKind],
    *,
    argument: str = "time_dimension",
) -> str:
    """Validate and return one exact time-dimension path."""
    available = _available_refs(
        catalog,
        kinds=frozenset({SemanticKind.TIME_DIMENSION}),
    )
    if type(time_dimension) is not Ref or time_dimension.kind is not SemanticKind.TIME_DIMENSION:
        _reject_exact(
            time_dimension,
            argument=argument,
            expected_kind="time_dimension",
            expected_type="Ref[time_dimension]",
            available_refs=available,
        )
    return _require_member(
        catalog,
        cast("Ref[SemanticKindTag]", time_dimension),
        argument=argument,
        expected_kind="time_dimension",
        expected_type="Ref[time_dimension]",
        available_refs=available,
    )


def normalize_dimension_boundary(
    catalog: SemanticCatalog,
    dimension: Ref[FieldKind],
    *,
    argument: str = "dimension",
) -> str:
    return normalize_dimension_input(catalog, dimension, argument=argument)


def normalize_dimension_inputs(
    catalog: SemanticCatalog,
    dimensions: Sequence[Ref[FieldKind]] | None,
) -> list[str]:
    return [
        normalize_dimension_input(catalog, dimension, argument="dimensions")
        for dimension in dimensions or ()
    ]


def normalize_where_inputs(
    catalog: SemanticCatalog,
    where: Mapping[Ref[FieldKind], SliceValue] | None,
) -> dict[str, SliceValue]:
    if where is None:
        return {}
    return {
        normalize_dimension_input(catalog, key, argument="slice_by"): value
        for key, value in where.items()
    }
