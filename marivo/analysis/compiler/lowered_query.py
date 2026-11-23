"""Lowered Query payloads and their checked read obligations."""

from __future__ import annotations

from dataclasses import dataclass

import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.query_stages import QueryPlan
from marivo.semantic.spi import NumericCheck


@dataclass(frozen=True, slots=True, eq=False)
class PopulationCheck:
    """One Population obligation in its original row scope."""

    relation: ir.Table
    flag: str
    expected: str
    received: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True, eq=False)
class LoweredRead:
    """An Ibis payload associated with the authoritative plan stage by ID."""

    stage_id: str
    expression: ir.Table
    numeric_checks: tuple[tuple[str, NumericCheck], ...] = ()
    population_checks: tuple[PopulationCheck, ...] = ()


@dataclass(frozen=True, slots=True, eq=False)
class LoweredQuery:
    plan: QueryPlan
    reads: tuple[LoweredRead, ...]

    def read(self, stage_id: str) -> LoweredRead:
        for item in self.reads:
            if item.stage_id == stage_id:
                return item
        raise compilation_error(f"a lowered read for {stage_id}", "missing lowered stage")
