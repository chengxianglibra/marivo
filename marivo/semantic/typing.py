"""Core protocols and typed dicts for marivo.semantic v1.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from marivo.datasource.typing import AiContext

if TYPE_CHECKING:
    import ibis


__all__ = [
    "AiContext",
    "ComponentExpr",
    "IbisBackend",
]


@runtime_checkable
class IbisBackend(Protocol):
    """Protocol for ibis backend objects used by dataset functions."""

    def table(
        self,
        name: str,
        /,
        *,
        database: str | tuple[str, ...] | None = None,
    ) -> ibis.Table: ...
    def read_parquet(self, path: str, /, **options: object) -> ibis.Table: ...
    def read_csv(self, path: str, /, **options: object) -> ibis.Table: ...
    def sql(self, query: str, /) -> ibis.Table: ...


class ComponentExpr(Protocol):
    """Protocol for sentinel expressions returned by ms.component().

    These support arithmetic composition in derived metric bodies.
    """

    def __add__(self, other: ComponentExpr | int | float) -> ComponentExpr: ...
    def __sub__(self, other: ComponentExpr | int | float) -> ComponentExpr: ...
    def __mul__(self, other: ComponentExpr | int | float) -> ComponentExpr: ...
    def __truediv__(self, other: ComponentExpr | int | float) -> ComponentExpr: ...
    def __neg__(self) -> ComponentExpr: ...

    # Reverse arithmetic operators
    def __radd__(self, other: int | float) -> ComponentExpr: ...
    def __rsub__(self, other: int | float) -> ComponentExpr: ...
    def __rmul__(self, other: int | float) -> ComponentExpr: ...
    def __rtruediv__(self, other: int | float) -> ComponentExpr: ...
