"""Capture the bound endpoints of an align-derived Attribute target."""

from dataclasses import replace
from typing import Literal

from marivo.analysis.core.frame_data import DerivedOutput, FrameNode
from marivo.analysis.core.frame_interpretation import remap_context, unary_facts
from marivo.analysis.core.frame_operations import FixFrameValues
from marivo.analysis.core.source_frame_schema import pending_schema
from marivo.analysis.core.source_lineage import source_lineage
from marivo.analysis.model.data_schema import (
    ComparisonValueBinding,
    DataComparison,
    OrdinaryComparisonBinding,
    PairedDataAxis,
)
from marivo.analysis.model.frame_contracts import frame_logical_schema
from marivo.analysis.model.layout_facts import FrameValueContext
from marivo.analysis.model.lineage import all_origins


def target_comparison(parent: FrameNode, target: DerivedOutput) -> DataComparison | None:
    """Capture align-derived change endpoints at their actual source binding boundary."""

    if target.target.mode == "level" or any(
        v.value_path == target.path and v.role == target.target.mode
        for fact in frame_logical_schema(parent).comparisons
        for v in fact.values
    ):
        return None
    baseline = target.target.baseline
    assert baseline is not None
    contexts: list[FrameValueContext | None] = []
    fixed = []
    for side, metric in (("left", target.target.current), ("right", baseline)):
        schema = pending_schema(metric.binding.graph, metric.binding.state)
        tokens: dict[str, tuple[str, Literal["self", "left", "right"]]] = {}
        for before, after in metric.axis_positions:
            axis = frame_logical_schema(parent).axes[after]
            endpoint: Literal["self", "left", "right"] = "left" if side == "left" else "right"
            tokens[schema.axes[before].token] = (
                axis.token,
                endpoint if isinstance(axis, PairedDataAxis) else "self",
            )
        contexts.append(
            remap_context(dict(schema.value_contexts).get(".".join(metric.path)), tokens)
        )
        lineage = source_lineage(schema, metric.binding)
        selections = []
        for position, key in metric.fixed_axes:
            axis = schema.axes[position]
            operation = FixFrameValues(position, key, all_origins(lineage.axes[axis.token]))
            restricted = replace(
                schema,
                axes=tuple(a for i, a in enumerate(schema.axes) if i != position),
                value_contexts=(),
            )
            selections.append(unary_facts(schema, restricted, operation).fixed_axes[-1])
        fixed.append(tuple(selections))
    return DataComparison(
        "endpoints",
        (ComparisonValueBinding(target.target.mode, target.path, target.path),),
        OrdinaryComparisonBinding(),
        None,
        None,
        namespace=("attribute", *target.path),
        contexts=((target.path, contexts[0], contexts[1]),),
        current_fixed_axes=fixed[0],
        baseline_fixed_axes=fixed[1],
        named=False,
    )
