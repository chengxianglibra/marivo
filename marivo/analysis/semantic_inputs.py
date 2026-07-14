"""Normalize semantic catalog objects at analysis operator boundaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NoReturn

from marivo.analysis.errors import MetricNotFoundError, SemanticKindMismatchError
from marivo.analysis.intents._types import SliceValue
from marivo.refs import SemanticRef
from marivo.semantic.catalog import CatalogObject, SemanticCatalog, SemanticKind
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.refs import make_ref

SemanticInput = CatalogObject | SemanticRef
MetricInput = SemanticInput
DimensionInput = SemanticInput


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


def _ref_and_kind(value: object) -> tuple[str, SemanticKind | None, str]:
    if isinstance(value, CatalogObject):
        return value.ref.id, value.ref.kind, str(value.ref.kind)
    if isinstance(value, SemanticRef):
        return value.id, value.kind, str(value.kind)
    return str(value), None, type(value).__name__


def _expected_label(argument: str, expected_kind: str) -> str:
    """Return the human-readable label for what the argument requires.

    A ``time_dimension`` argument specifically requires a time dimension.
    A ``dimension`` expected kind accepts either a plain dimension or a
    time dimension, so the label mentions both.
    """
    if argument == "time_dimension":
        return "time dimension"
    if expected_kind == "dimension":
        return "dimension or time dimension"
    return expected_kind


def _repair_snippets() -> list[str]:
    """Return copyable catalog recovery snippets for kind-mismatch errors.

    Snippets use the typed collection form with placeholder syntax, never
    hard-coded ids.
    """
    return [
        "session.catalog.domains.show()",
        "session.catalog.domains.get('<domain>').metrics.show()",
        "session.catalog.domains.get('<domain>').entities.get('<entity>').dimensions.show()",
        "session.catalog.domains.get('<domain>').entities.get('<entity>').time_dimensions.show()",
        "session.catalog.get('<kind>.<domain>.<object>').details().show()",
    ]


def _reject_kind(
    *,
    ref: str,
    actual_kind: str,
    expected_kind: str,
    argument: str,
    available_ids: Sequence[str] | None = None,
) -> NoReturn:
    label = _expected_label(argument, expected_kind)
    details: dict[str, object] = {
        "argument": argument,
        "ref": ref,
        "expected_kind": expected_kind,
        "actual_kind": actual_kind,
        "repair": _repair_snippets(),
    }
    if available_ids is not None:
        details["available_ids"] = list(available_ids)
    raise SemanticKindMismatchError(
        message=f"{argument} requires a {label} SemanticRef or CatalogObject",
        context=details,
        hint=f"Use session.catalog.{expected_kind}s to find a matching {label}.",
    )


def _require_catalog_input(
    value: object,
    *,
    argument: str,
    expected_kind: str,
    catalog: SemanticCatalog | None = None,
) -> tuple[str, SemanticKind]:
    ref, kind, actual = _ref_and_kind(value)
    if kind is None:
        available_ids: Sequence[str] | None = None
        if catalog is not None:
            if expected_kind == "dimension":
                available_ids = _available_dimension_ids(catalog)
            elif expected_kind == "metric":
                available_ids = _available_ids(catalog, kind=SemanticKind.METRIC)
        _reject_kind(
            ref=ref,
            actual_kind=actual,
            expected_kind=expected_kind,
            argument=argument,
            available_ids=available_ids,
        )
    return ref, kind


def _actual_catalog_kind(catalog: SemanticCatalog, ref: str) -> SemanticKind | None:
    return catalog._require_index().kind_of(ref)


def normalize_metric_input(catalog: SemanticCatalog, metric: MetricInput) -> str:
    """Return a metric semantic id from a catalog object/ref."""
    ref, kind = _require_catalog_input(
        metric, argument="metric", expected_kind="metric", catalog=catalog
    )
    if kind != SemanticKind.METRIC:
        _reject_kind(
            ref=ref,
            actual_kind=str(kind),
            expected_kind="metric",
            argument="metric",
            available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
        )
    try:
        obj = catalog.get(_typed_catalog_id(ref, kind))
    except SemanticRuntimeError as exc:
        if exc.kind != "not_found":
            raise
        actual_kind = _actual_catalog_kind(catalog, ref)
        if actual_kind is not None:
            _reject_kind(
                ref=ref,
                actual_kind=str(actual_kind),
                expected_kind="metric",
                argument="metric",
                available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
            )
        raise MetricNotFoundError(
            message=f"metric {ref!r} not found",
            hint=("Use session.catalog.domains.get('<name>').metrics to browse metric refs."),
            context={
                "metric": ref,
                "metric_id": ref,
                "available_ids": _available_ids(catalog, kind=SemanticKind.METRIC),
            },
        ) from exc
    if obj.ref.kind != SemanticKind.METRIC:
        _reject_kind(
            ref=ref,
            actual_kind=str(obj.ref.kind),
            expected_kind="metric",
            argument="metric",
            available_ids=_available_ids(catalog, kind=SemanticKind.METRIC),
        )
    return ref


def normalize_dimension_input(
    catalog: SemanticCatalog,
    dimension: DimensionInput,
    *,
    argument: str = "dimension",
) -> str:
    """Return a dimension/time-dimension semantic id from a catalog object/ref."""
    ref, kind = _require_catalog_input(
        dimension, argument=argument, expected_kind="dimension", catalog=catalog
    )
    if kind == SemanticKind.MEASURE:
        raise SemanticKindMismatchError(
            message=(
                f"{ref!r} is a measure, which is aggregated, not a group-by axis; "
                "slice by a categorical dimension or aggregate it into a metric."
            ),
            context={
                "ref": ref,
                "actual_kind": "measure",
                "expected_kind": "dimension",
                "repair": _repair_snippets(),
            },
        )
    if kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        _reject_kind(
            ref=ref,
            actual_kind=str(kind),
            expected_kind="dimension",
            argument=argument,
            available_ids=_available_dimension_ids(catalog),
        )
    try:
        obj = catalog.get(_typed_catalog_id(ref, kind))
    except SemanticRuntimeError as exc:
        if exc.kind != "not_found":
            raise
        actual_kind = _actual_catalog_kind(catalog, ref)
        if actual_kind is not None:
            if actual_kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
                obj = catalog.get(_typed_catalog_id(ref, actual_kind))
            else:
                _reject_kind(
                    ref=ref,
                    actual_kind=str(actual_kind),
                    expected_kind="dimension",
                    argument=argument,
                    available_ids=_available_dimension_ids(catalog),
                )
        else:
            available_ids = _available_dimension_ids(catalog)
            if "." not in ref:
                candidates = [
                    candidate for candidate in available_ids if candidate.rsplit(".", 1)[-1] == ref
                ]
                if len(candidates) == 1:
                    return normalize_dimension_input(
                        catalog,
                        make_ref(candidates[0], kind),
                        argument=argument,
                    )
                if len(candidates) > 1:
                    _reject_kind(
                        ref=ref,
                        actual_kind="ambiguous",
                        expected_kind="dimension",
                        argument=argument,
                        available_ids=available_ids,
                    )
            _reject_kind(
                ref=ref,
                actual_kind="not_found",
                expected_kind="dimension",
                argument=argument,
                available_ids=available_ids,
            )
    if obj.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        _reject_kind(
            ref=ref,
            actual_kind=str(obj.ref.kind),
            expected_kind="dimension",
            argument=argument,
            available_ids=_available_dimension_ids(catalog),
        )
    return ref


def normalize_dimension_boundary(
    catalog: SemanticCatalog,
    dimension: DimensionInput,
    *,
    argument: str = "dimension",
) -> str:
    """Normalize catalog dimension inputs at public analysis boundaries."""
    available_ids = _available_dimension_ids(catalog)
    if not available_ids:
        if isinstance(dimension, CatalogObject):
            if dimension.ref.kind == SemanticKind.MEASURE:
                raise SemanticKindMismatchError(
                    message=(
                        f"{dimension.ref.id!r} is a measure, which is aggregated, not a group-by axis; "
                        "slice by a categorical dimension or aggregate it into a metric."
                    ),
                    context={
                        "ref": dimension.ref.id,
                        "actual_kind": "measure",
                        "expected_kind": "dimension",
                        "repair": _repair_snippets(),
                    },
                )
            if dimension.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
                _reject_kind(
                    ref=dimension.ref.id,
                    actual_kind=str(dimension.ref.kind),
                    expected_kind="dimension",
                    argument=argument,
                )
            return dimension.ref.id
        if isinstance(dimension, SemanticRef) and dimension.kind in {
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
        }:
            return dimension.id
    return normalize_dimension_input(catalog, dimension, argument=argument)


def normalize_dimension_inputs(
    catalog: SemanticCatalog,
    dimensions: Sequence[DimensionInput] | None,
) -> list[str]:
    """Normalize a dimension list to full semantic ids."""
    return [
        normalize_dimension_input(catalog, dim, argument="dimensions") for dim in dimensions or ()
    ]


def normalize_where_inputs(
    catalog: SemanticCatalog,
    where: Mapping[DimensionInput, SliceValue] | None,
) -> dict[str, SliceValue]:
    """Normalize where keys to full semantic ids."""
    if where is None:
        return {}
    return {
        normalize_dimension_input(catalog, key, argument="slice_by"): value
        for key, value in where.items()
    }
