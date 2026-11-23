"""Construct exact Catalog references from validated kind and path."""

from __future__ import annotations

from typing import (
    Literal,
    overload,
)

from marivo._kernel.refs import (
    DatasourceKind,
    DimensionKind,
    DomainKind,
    EntityKind,
    MeasureKind,
    MetricKind,
    Ref,
    RelationshipKind,
    SemanticKind,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo._kernel.refs import (
    ref as ref_factory,
)


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DOMAIN]) -> Ref[DomainKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DATASOURCE]) -> Ref[DatasourceKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.ENTITY]) -> Ref[EntityKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.DIMENSION]) -> Ref[DimensionKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.TIME_DIMENSION]) -> Ref[TimeDimensionKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.MEASURE]) -> Ref[MeasureKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.METRIC]) -> Ref[MetricKind]: ...


@overload
def _make_ref(path: str, kind: Literal[SemanticKind.RELATIONSHIP]) -> Ref[RelationshipKind]: ...


@overload
def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]: ...


def _make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
    }[kind]
    return factory(path)
