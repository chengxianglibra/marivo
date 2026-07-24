"""Exact semantic-reference boundaries for analysis operators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from marivo.analysis.errors import (
    AnalysisRepair,
    MetricNotFoundError,
    RepairKind,
    SemanticKindMismatchError,
)
from marivo.analysis.slice_types import SliceValue
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import (
    DimensionKind,
    MetricKind,
    Ref,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.semantic.catalog import (
    CatalogEntry,
    SemanticCatalog,
    _normalize_semantic_input,
    _SemanticInput,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError


def _available_refs(
    catalog: SemanticCatalog,
    *,
    kinds: frozenset[SemanticKind],
) -> list[str]:
    return sorted(
        entry.ref.key for entry in catalog._require_index()._by_ref.values() if entry.kind in kinds
    )


def _analysis_repair(
    exc: SemanticRuntimeError,
    *,
    help_target: str,
) -> AnalysisRepair | None:
    repair = exc.repair
    if repair is None:
        return None
    if repair.kind == "retry" and repair.snippet:
        kind: RepairKind = "retry"
    elif repair.kind == "reacquire":
        kind = "inspect"
    elif repair.kind in {"reauthor", "register", "configure"}:
        kind = "semantic_authoring"
    elif repair.kind == "environment":
        kind = "environment"
    else:
        kind = "inspect"
    return AnalysisRepair(
        kind=kind,
        action=repair.action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=help_target),
        snippet=repair.snippet if kind == "retry" else None,
        candidates=repair.candidates,
    )


def _normalize_ref_input[KindT: SemanticKindTag](
    catalog: SemanticCatalog,
    value: _SemanticInput[KindT],
    *,
    argument: str,
    allowed_kinds: frozenset[SemanticKind],
    help_target: str,
) -> Ref[KindT]:
    try:
        return _normalize_semantic_input(
            catalog,
            value,
            allowed_kinds=allowed_kinds,
            location=argument,
        )
    except SemanticRuntimeError as exc:
        if exc.kind == ErrorKind.NOT_FOUND and allowed_kinds == frozenset({SemanticKind.METRIC}):
            ref = value if type(value) is Ref else None
            metric_path = ref.path if ref is not None else str(value)
            raise MetricNotFoundError(
                message=f"metric {metric_path!r} not found",
                expected=exc.expected,
                received=exc.received,
                location=argument,
                repair=_analysis_repair(exc, help_target=help_target),
                context={
                    "metric": metric_path,
                    "metric_ref": ref.key if ref is not None else None,
                    "available_refs": _available_refs(catalog, kinds=allowed_kinds),
                },
            ) from exc
        received_ref = value.ref if isinstance(value, CatalogEntry) else value
        ref_key = received_ref.key if type(received_ref) is Ref else None
        actual_kind = received_ref.kind.value if type(received_ref) is Ref else type(value).__name__
        expected_kind = " or ".join(sorted(kind.value for kind in allowed_kinds))
        raise SemanticKindMismatchError(
            message=exc.message,
            expected=exc.expected,
            received=exc.received,
            location=argument,
            repair=_analysis_repair(exc, help_target=help_target),
            context={
                **exc.details,
                "argument": argument,
                "ref": ref_key,
                "actual_kind": actual_kind,
                "actual_type": type(value).__name__,
                "expected_kind": expected_kind,
                "expected_type": exc.expected,
                "semantic_error_kind": exc.kind,
                "available_refs": _available_refs(catalog, kinds=allowed_kinds),
            },
        ) from exc


def normalize_metric_ref_input(
    catalog: SemanticCatalog,
    metric: _SemanticInput[MetricKind],
    *,
    argument: str = "metric",
) -> Ref[MetricKind]:
    """Validate and return one canonical current-catalog metric ref."""
    return _normalize_ref_input(
        catalog,
        metric,
        argument=argument,
        allowed_kinds=frozenset({SemanticKind.METRIC}),
        help_target="observe",
    )


def normalize_metric_input(
    catalog: SemanticCatalog,
    metric: _SemanticInput[MetricKind],
) -> str:
    """Validate and return one exact catalog metric path."""
    return normalize_metric_ref_input(catalog, metric).path


def normalize_dimension_input(
    catalog: SemanticCatalog,
    dimension: _SemanticInput[DimensionKind | TimeDimensionKind],
    *,
    argument: str = "dimension",
    help_target: str = "observe",
) -> str:
    """Validate and return one exact dimension or time-dimension path."""
    kinds = frozenset({SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION})
    return _normalize_ref_input(
        catalog,
        dimension,
        argument=argument,
        allowed_kinds=kinds,
        help_target=help_target,
    ).path


def normalize_time_dimension_input(
    catalog: SemanticCatalog,
    time_dimension: _SemanticInput[TimeDimensionKind],
    *,
    argument: str = "time_dimension",
) -> str:
    """Validate and return one exact time-dimension path."""
    return _normalize_ref_input(
        catalog,
        time_dimension,
        argument=argument,
        allowed_kinds=frozenset({SemanticKind.TIME_DIMENSION}),
        help_target="observe",
    ).path


def normalize_dimension_boundary(
    catalog: SemanticCatalog,
    dimension: _SemanticInput[DimensionKind | TimeDimensionKind],
    *,
    argument: str = "dimension",
    help_target: str = "observe",
) -> str:
    return normalize_dimension_input(
        catalog,
        dimension,
        argument=argument,
        help_target=help_target,
    )


def normalize_dimension_inputs(
    catalog: SemanticCatalog,
    dimensions: Sequence[_SemanticInput[DimensionKind | TimeDimensionKind]] | None,
) -> list[str]:
    return [
        normalize_dimension_input(catalog, dimension, argument="dimensions")
        for dimension in dimensions or ()
    ]


def normalize_where_inputs(
    catalog: SemanticCatalog,
    where: Mapping[
        _SemanticInput[DimensionKind | TimeDimensionKind],
        SliceValue,
    ]
    | None,
    *,
    help_target: str = "observe",
) -> dict[str, SliceValue]:
    if where is None:
        return {}
    normalized: dict[str, SliceValue] = {}
    for key, value in where.items():
        dimension_id = normalize_dimension_input(
            catalog,
            key,
            argument="slice_by",
            help_target=help_target,
        )
        if dimension_id in normalized:
            canonical_ref = key.ref if isinstance(key, CatalogEntry) else key
            assert type(canonical_ref) is Ref
            raise SemanticKindMismatchError(
                message="slice_by keys must remain unique after semantic input normalization",
                expected="one predicate per canonical dimension identity",
                received=dimension_id,
                location="slice_by",
                repair=AnalysisRepair(
                    kind="inspect",
                    action=(
                        "Keep either the current catalog entry or its exact ref for this "
                        "dimension, then choose one predicate value."
                    ),
                    help_target=LiveHelpTarget(
                        surface="analysis",
                        canonical_id=help_target,
                    ),
                    candidates=(canonical_ref.key,),
                ),
                context={"duplicate_dimension": dimension_id},
            )
        normalized[dimension_id] = value
    return normalized
