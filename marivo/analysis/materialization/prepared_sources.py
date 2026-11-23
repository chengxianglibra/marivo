"""Invocation-local prepared relations and qualified source reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from marivo.analysis.model.optimization_policy import (
    DEFAULT_OPTIMIZATION_POLICY,
    OptimizationPolicy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import ibis.expr.operations as ops
    import ibis.expr.types as ir
    import pandas as pd

    from marivo.analysis.binding.graph import BoundGraph
    from marivo.analysis.compiler.lowered_query import LoweredQuery, PopulationCheck
    from marivo.analysis.compiler.query_stages import SourceSchema
    from marivo.analysis.methods.carriers.stages import StageOutput
    from marivo.analysis.model.frame_contracts import CheckReceipt
    from marivo.analysis.model.time_facts import FrameSourceTime
    from marivo.datasource.spi import BoundSource, CompiledRead, SourceSession


@dataclass(frozen=True, slots=True, eq=False)
class PreparedSources:
    """Prepared relations for one Query; HTTP JSON retains acquired response data."""

    source: SourceSession
    schemas: tuple[SourceSchema, ...]
    relations: dict[str, ir.Table]
    bound: dict[str, BoundSource]
    sources: dict[str, SourceSession] = field(default_factory=dict)
    entities: dict[str, ir.Table] = field(default_factory=dict)
    graph: BoundGraph | None = None
    source_times: tuple[FrameSourceTime, ...] = ()
    population_checks: dict[str, tuple[PopulationCheck, ...]] = field(default_factory=dict)
    native_expressions: dict[tuple[str, ops.Node], ir.Table] = field(default_factory=dict)
    # Unparameterized reads qualified before admission are issued once and executed as-is.
    compiled_reads: dict[tuple[SourceSession, ops.Node], CompiledRead] = field(default_factory=dict)
    original_input_qualifications: dict[
        tuple[SourceSession, ops.Node], Literal["qualified", "other", "unavailable"]
    ] = field(default_factory=dict)
    collation_qualifications: dict[tuple[SourceSession, ops.Node, str], str | None] = field(
        default_factory=dict
    )
    optimization_policy: OptimizationPolicy = DEFAULT_OPTIMIZATION_POLICY


@dataclass(frozen=True, slots=True, eq=False)
class PreparedWindowRead:
    """A bounded nested query over a captured window domain."""

    sources: PreparedSources
    lowered: LoweredQuery
    frame_key_inputs: Mapping[str, StageOutput]
    report_domain: pd.DataFrame
    receipts: tuple[CheckReceipt, ...]
    sample_domains: Mapping[tuple[int, ...], pd.DataFrame] | None = None
    checked_inputs: Mapping[str, StageOutput] | None = None
