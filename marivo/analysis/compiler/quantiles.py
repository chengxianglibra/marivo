"""Algorithm admission for schema-bound source quantiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import ibis.expr.types as ir

from marivo._definition.expressions import SourceAggregate
from marivo._quantiles import source_quantile_algorithm
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.semantic._numeric_lowering import lower_row

if TYPE_CHECKING:
    from marivo.analysis.compiler.observation import ReductionInput


def quantile_bounds(prepared: ReductionInput, fields: Mapping[str, ir.Value]) -> bool:
    """Select exact bounds or an admitted native algorithm before business reads."""
    node = prepared.node
    if not isinstance(node, SourceAggregate) or node.reducer not in (
        "median",
        "quantile",
        "approx_median",
        "approx_quantile",
    ):
        return False
    dtype = lower_row(node.operand, fields).value.type()
    approximate = node.reducer.startswith("approx_")
    algorithm = source_quantile_algorithm(prepared.provider, approximate=approximate)
    if algorithm is None or not dtype.is_numeric():
        raise AnalysisStageUnavailableError(
            stage="query_execution",
            expected="a qualified native algorithm and numeric input for this source reduction",
            received=f"{prepared.provider}: {node.reducer}({dtype})",
            repair="Use a backend supporting the declared algorithm and numeric type. Change to an exact or approximate definition only if that matches the intended metric. No business row was read.",
            target="analysis_frame.compute",
        )
    return not approximate and (
        prepared.duration is not None
        or (dtype.is_decimal() and not prepared.native_quantile_value)
        or prepared.retain_quantile_bounds
        or algorithm == "rank_bounds"
    )
