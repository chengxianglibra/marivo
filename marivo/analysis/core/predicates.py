"""Closed value predicates carried in the private definition graph."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from marivo.analysis.core.model import Binding, reject


@dataclass(frozen=True, slots=True)
class ValuePredicate:
    binding: Binding
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    value: int | float | str
    unknown: Literal["reject", "drop"] = "reject"

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not Binding
            or self.operator not in ("eq", "ne", "lt", "le", "gt", "ge")
            or (
                (type(self.value) is int and not -(2**63) <= self.value < 2**63)
                or (type(self.value) is str and self.operator != "eq")
                or (type(self.value) is float and not isfinite(self.value))
                or type(self.value) not in (int, float, str)
            )
            or self.unknown not in ("reject", "drop")
        ):
            reject(
                "a bound int64 or finite float64 predicate or exact string equality with explicit unknown policy",
                repr(self),
                "Bind an exact numeric comparison or string equality in the input scope.",
                "analysis.predicate",
            )
