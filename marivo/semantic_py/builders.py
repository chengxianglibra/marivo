from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from marivo.semantic_py.errors import SemanticDecoratorError
from marivo.semantic_py.ir import SymbolRef

SymbolKind = Literal["metric", "field", "time_field", "datasource"]


@dataclass(frozen=True)
class DecompositionSpec:
    kind: Literal["sum", "ratio", "weighted_average"]
    numerator: SymbolRef | object | None = None
    denominator: SymbolRef | object | None = None
    weight: SymbolRef | object | None = None


def ref(value: str) -> SymbolRef:
    kind, sep, name = value.partition(".")
    if sep != "." or kind not in {"metric", "field", "time_field", "datasource"} or not name:
        raise SemanticDecoratorError(
            phase="decorator",
            kind="ReferenceInvalid",
            location=None,
            function=None,
            message="ref must look like 'metric.total_users'.",
            hint="Use one of metric.<name>, field.<name>, time_field.<name>, or datasource.<name>.",
            refs=[f"ref:{value}"],
        )
    return SymbolRef(kind=cast("SymbolKind", kind), name=name)


def sum() -> DecompositionSpec:
    return DecompositionSpec(kind="sum")


def ratio(*, numerator: SymbolRef | object, denominator: SymbolRef | object) -> DecompositionSpec:
    return DecompositionSpec(kind="ratio", numerator=numerator, denominator=denominator)


def weighted_average(
    *, numerator: SymbolRef | object, weight: SymbolRef | object
) -> DecompositionSpec:
    return DecompositionSpec(kind="weighted_average", numerator=numerator, weight=weight)
