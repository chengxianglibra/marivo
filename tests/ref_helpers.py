"""Private test construction helper for dynamic semantic kinds."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory

_FACTORY_BY_KIND: dict[SemanticKind, Callable[[str], Ref[SemanticKindTag]]] = {
    SemanticKind.DOMAIN: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.domain),
    SemanticKind.DATASOURCE: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.datasource),
    SemanticKind.ENTITY: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.entity),
    SemanticKind.DIMENSION: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.dimension),
    SemanticKind.TIME_DIMENSION: cast(
        "Callable[[str], Ref[SemanticKindTag]]", ref_factory.time_dimension
    ),
    SemanticKind.MEASURE: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.measure),
    SemanticKind.METRIC: cast("Callable[[str], Ref[SemanticKindTag]]", ref_factory.metric),
    SemanticKind.RELATIONSHIP: cast(
        "Callable[[str], Ref[SemanticKindTag]]", ref_factory.relationship
    ),
}


def make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    return _FACTORY_BY_KIND[kind](path)


def as_ref(value: object) -> Ref[SemanticKindTag] | None:
    if type(value) is Ref:
        return value
    candidate = getattr(value, "ref", None)
    return candidate if type(candidate) is Ref else None


def as_ref_id(value: object) -> str:
    ref = as_ref(value)
    if ref is not None:
        return ref.path
    if isinstance(value, str):
        return value
    raise TypeError(f"expected Ref, CatalogEntry, or str, got {type(value).__name__}")
