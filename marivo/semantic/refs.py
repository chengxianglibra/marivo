"""Semantic-layer ref subclasses, factory, and input normalizers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from marivo.datasource.authoring import DatasourceRef
from marivo.refs import SemanticRef, SymbolKind


class _NonCallableRef(SemanticRef):
    """Base for semantic refs that are not callable as decorators.

    Overrides ``SemanticRef.__call__`` to raise a structured
    ``SemanticDecoratorError`` via the semantic errors module.
    """

    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise

        _raise(
            ErrorKind.INVALID_REF,
            f"{self.id!r} is a declared semantic object, not a decorator. "
            "Body-free constructors (ms.ratio / ms.weighted_average / ms.linear / "
            "ms.aggregate / ms.relationship) return a ref — assign it, e.g. "
            "`loss_rate = ms.ratio(name=..., numerator=..., denominator=...)`. "
            "They have no function body.",
            cls=SemanticDecoratorError,
        )


class EntityRef(_NonCallableRef):
    """Ref returned by ms.entity().  Not callable."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.ENTITY)


class _FieldRef(SemanticRef):
    """Base for callable field refs (dimension/measure/time_dimension)."""

    __slots__ = ("_resolver",)

    def __init__(self, semantic_id: str, kind: SymbolKind) -> None:
        super().__init__(semantic_id, kind)
        self._resolver: Callable[[str, Any], Any] | None = None

    def __call__(self, parent_table: Any) -> Any:
        if self._resolver is None:
            raise RuntimeError(
                f"{type(self).__name__}({self.id!r}) has no resolver. "
                "Field refs can only be called inside a loaded semantic project."
            )
        return self._resolver(self.id, parent_table)


class DimensionRef(_FieldRef):
    """Ref returned by ms.dimension().  Callable in base metric bodies."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.DIMENSION)


class TimeDimensionRef(_FieldRef):
    """Ref returned by ms.time_dimension().  Callable like DimensionRef."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.TIME_DIMENSION)


class MeasureRef(_FieldRef):
    """Ref returned by ms.measure().  Callable like DimensionRef."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.MEASURE)


class MetricRef(_NonCallableRef):
    """Ref returned by ms.aggregate(), @ms.metric(), and derived constructors."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        normalized = semantic_id.strip()
        model, separator, metric = normalized.partition(".")
        if not separator or not model or not metric:
            raise ValueError(f"metric ref must be '<model>.<metric>', got {semantic_id!r}")
        super().__init__(normalized, SymbolKind.METRIC)


class RelationshipRef(_NonCallableRef):
    """Ref returned by ms.relationship().  Not callable."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.RELATIONSHIP)


class DomainRef(_NonCallableRef):
    """Ref returned by ms.domain().  Not callable."""

    __slots__ = ()

    def __init__(self, semantic_id: str) -> None:
        super().__init__(semantic_id, SymbolKind.DOMAIN)


_KIND_TO_REF: dict[SymbolKind, Callable[[str], SemanticRef]] = {
    SymbolKind.DOMAIN: DomainRef,
    SymbolKind.DATASOURCE: DatasourceRef.from_id,
    SymbolKind.ENTITY: EntityRef,
    SymbolKind.DIMENSION: DimensionRef,
    SymbolKind.MEASURE: MeasureRef,
    SymbolKind.TIME_DIMENSION: TimeDimensionRef,
    SymbolKind.METRIC: MetricRef,
    SymbolKind.RELATIONSHIP: RelationshipRef,
}


def make_ref(semantic_id: str, kind: SymbolKind) -> SemanticRef:
    """Construct the per-kind SemanticRef subclass for ``kind``."""
    return _KIND_TO_REF[kind](semantic_id)


def as_ref(value: object) -> SemanticRef | None:
    """Return the SemanticRef for a ref or a CatalogObject; None for a str/other."""
    if isinstance(value, SemanticRef):
        return value
    obj_ref = getattr(value, "ref", None)
    if isinstance(obj_ref, SemanticRef):
        return obj_ref
    return None


def as_ref_id(value: object) -> str:
    """Extract the id string from a ref, a CatalogObject, or a plain string."""
    if isinstance(value, str):
        return value
    if isinstance(value, SemanticRef):
        return value.id
    obj_ref = getattr(value, "ref", None)
    if isinstance(obj_ref, SemanticRef):
        return obj_ref.id
    raise TypeError(f"expected SemanticRef, CatalogObject, or str, got {type(value).__name__}")
