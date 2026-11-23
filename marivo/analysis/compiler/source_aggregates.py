"""Native aggregate admission for schema-bound source compilation."""

from __future__ import annotations

from collections.abc import Mapping

import ibis.expr.types as ir

from marivo._definition.expressions import SourceAggregate
from marivo.analysis.compiler.reduction_input import ReductionInput
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.datasource.spi import (
    aggregate_operation,
    aggregate_repair_action,
    native_aggregate,
)
from marivo.semantic.spi import lower_row


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
