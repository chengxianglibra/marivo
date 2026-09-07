"""Typed source recipe handed to the private materialization runtime."""

from __future__ import annotations

from dataclasses import dataclass

import ibis.expr.types as ir


@dataclass(frozen=True, slots=True, repr=False)
class CompiledValidation:
    """A named scalar relation whose violations column must equal zero."""

    name: str
    expression: ir.Table


@dataclass(frozen=True, slots=True)
class RetainedPartSpec:
    """A keyed projection of the sole transfer carrying sufficient Metric state."""

    role: str
    contract_id: str
    contract_version: int
    column_names: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class CompiledDataset:
    """One final source expression with separate, named assertion preflights."""

    expression: ir.Table
    validations: tuple[CompiledValidation, ...]
    primary_columns: tuple[str, ...]
    retained_parts: tuple[RetainedPartSpec, ...]
