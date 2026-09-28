"""Closed value predicates carried in the private definition graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.analysis.core.model import Binding, reject


@dataclass(frozen=True, slots=True)
class ValuePredicate:
    binding: Binding
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge"]
    value: int
    unknown: Literal["reject", "drop"] = "reject"

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not Binding
            or self.operator not in ("eq", "ne", "lt", "le", "gt", "ge")
            or type(self.value) is not int
            or not -(2**63) <= self.value < 2**63
            or self.unknown not in ("reject", "drop")
        ):
            reject(
                "a bound int64 predicate and explicit unknown policy",
                repr(self),
                "Bind an exact int64 comparison in the input scope.",
                "analysis.predicate",
            )
