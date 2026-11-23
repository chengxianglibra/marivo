"""Prepared field projections and the interface used by Population preparation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import ibis.expr.types as ir

from marivo._definition.observations import ContributionBinding
from marivo.analysis.model.time_authority import SourceTimeAuthority
from marivo.analysis.model.time_facts import FrameSourceTime
from marivo.semantic.spi import CompiledSemanticState


@dataclass(frozen=True, slots=True)
class PreparedFields:
    table: ir.Table
    columns: tuple[tuple[str, str], ...]
    checks: tuple[tuple[str, str, str], ...]
    originals: tuple[tuple[str, ir.Value], ...]
    dependencies: tuple[ir.Value, ...]
    time_authorities: tuple[SourceTimeAuthority, ...] = ()
    source_times: tuple[FrameSourceTime, ...] = ()


class PrepareFields(Protocol):
    def __call__(
        self,
        contribution: ContributionBinding,
        entities: Mapping[str, ir.Table],
        state: CompiledSemanticState,
        *,
        root_table: ir.Table | None = None,
        consumption_timezone: str = "UTC",
    ) -> PreparedFields: ...
