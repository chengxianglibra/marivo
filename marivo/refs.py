"""Cross-layer semantic reference base.

This module sits below every other Marivo layer (datasource, semantic,
analysis) so a single ``SemanticRef`` base can be shared by all of them
without import cycles. It owns the ``SymbolKind`` enum for the same reason.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class SymbolKind(StrEnum):
    """The kind of a semantic object. One ref subclass exists per member."""

    DOMAIN = "domain"
    DATASOURCE = "datasource"
    ENTITY = "entity"
    DIMENSION = "dimension"
    MEASURE = "measure"
    TIME_DIMENSION = "time_dimension"
    METRIC = "metric"
    RELATIONSHIP = "relationship"


class SemanticRef:
    """Stable identity for a semantic object, shared across all layers.

    Identity (``id`` + ``kind``) is fixed at construction; ``__eq__`` /
    ``__hash__`` are stable. This is deliberately not a frozen dataclass:
    field-kind subclasses attach a single late-bound resolver (see
    ``marivo.semantic.refs``). ``kind`` is normally encoded by the subclass.
    """

    __slots__ = ("id", "kind")

    id: str
    kind: SymbolKind

    def __init__(self, id: str, kind: SymbolKind) -> None:
        normalized = id.strip()
        if not normalized:
            raise ValueError("ref id must be non-empty")
        object.__setattr__(self, "id", normalized)
        object.__setattr__(self, "kind", kind)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_resolver":
            # Allow field refs to attach a late-bound resolver.
            object.__setattr__(self, name, value)
            return
        raise AttributeError("SemanticRef instances are immutable")

    def __str__(self) -> str:
        return self.id

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.id!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticRef):
            return NotImplemented
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        return hash((type(self), self.id))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(
            f"{self.id!r} is a declared semantic object, not a decorator. "
            "Body-free constructors (ms.ratio / ms.weighted_average / ms.linear / "
            "ms.aggregate / ms.relationship) return a ref — assign it, e.g. "
            "`loss_rate = ms.ratio(name=..., numerator=..., denominator=...)`. "
            "They have no function body."
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> Any:
        """Allow ref subclasses to be used as Pydantic field types."""
        from pydantic_core import core_schema

        ref_cls: type[SemanticRef] = _source_type if _source_type is not SemanticRef else cls

        def validate(value: Any) -> SemanticRef:
            if isinstance(value, ref_cls):
                return value
            if isinstance(value, str):
                return ref_cls(value)  # type: ignore[call-arg]
            raise ValueError(f"expected str or {ref_cls.__name__}, got {type(value).__name__}")

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.id,
                info_arg=False,
            ),
        )
