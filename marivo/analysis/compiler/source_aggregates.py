"""Native aggregate admission for schema-bound source compilation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis.expr.types as ir

from marivo._definition.expressions import SourceAggregate
from marivo._source_aggregates import aggregate_operation, aggregate_repair_action, native_aggregate
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.semantic._numeric_lowering import lower_row

if TYPE_CHECKING:
    from marivo.analysis.compiler.observation import ReductionInput


def admit_source_aggregate(prepared: ReductionInput, fields: Mapping[str, ir.Value]) -> None:
    """Reject unavailable accuracy/type routes before any source execution."""
    node = prepared.node
    if not isinstance(node, SourceAggregate) or aggregate_operation(node.reducer) is None:
        return
    dtype = lower_row(node.operand, fields).value.type()
    duration = prepared.duration is not None
    input_type = (
        f"duration[{prepared.duration.unit}]" if prepared.duration is not None else str(dtype)
    )
    if native_aggregate(prepared.provider, node.reducer, dtype, duration=duration) is None:
        raise AnalysisStageUnavailableError(
            stage="query_execution",
            expected="a qualified native algorithm preserving the declared accuracy and input type",
            received=f"{prepared.provider}: {node.reducer}({input_type})",
            repair=aggregate_repair_action(
                prepared.provider, node.reducer, dtype, duration=duration
            ),
            target="analysis_frame.compute",
        )
