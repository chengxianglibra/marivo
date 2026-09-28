"""Registered caller-owned fixed-input methods, without Artifact reading."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.compiler.graph_plan import LocalMethodStage
from marivo.analysis.core.model import Cell, Defined, Null, Undefined, Unknown, reject
from marivo.analysis.core.rules import RowState
from marivo.analysis.methods.builtin import admit


@dataclass(frozen=True, slots=True)
class CountResult:
    cell: Defined
    count: int


def count(stage: LocalMethodStage, cells: tuple[Cell, ...]) -> CountResult:
    """R4 supplies validated fixed rows; this method never reads or publishes."""
    admit(stage.implementation, stage.node.parameters)
    if (
        not isinstance(stage.node.parameters, RowState)
        or stage.node.parameters.method != "count"
        or stage.implementation.key.route != "artifact_python"
    ):
        reject(
            "the registered fixed count stage",
            str(stage.node.method),
            "Use fixed row.count.",
            "analysis.local",
        )
    limit = stage.implementation.resources.max_rows
    if (
        type(cells) is not tuple
        or any(type(c) not in (Defined, Null, Undefined, Unknown) for c in cells)
        or any(
            isinstance(c, Defined) and (type(c.value) is not int or not -(2**63) <= c.value < 2**63)
            for c in cells
        )
        or limit is None
        or len(cells) > limit
    ):
        reject(
            f"validated Cells within {limit} rows",
            str(len(cells)),
            "Supply validated retained Cells within the registered resource limit.",
            "analysis.local",
        )
    return CountResult(Defined(len(cells)), len(cells))
