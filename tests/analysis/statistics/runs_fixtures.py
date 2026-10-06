"""Shared builders for runs fixtures tests."""

import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    Signature,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.time_grid import bind_grid
from marivo.analysis.materialization.deviation_execution import save
from marivo.analysis.materialization.runs_execution import Capture
from marivo.analysis.methods.physical import ScalarType


def capture(
    states: tuple[int, ...],
    *,
    zone: str = "UTC",
    start: str = "2026-03-06",
    end: str = "2026-03-11",
) -> Capture:
    grid = bind_grid(mv.time_scope(start=start, end=end), mv.grain("day"), report_timezone=zone)
    binding = Binding("session", "owner", "input", "scope")
    coord = Coordinate(ms.ref.entity("sales.order"), "time:" + grid.identity, "anchor")
    signature = Signature(
        DomainSignature(binding, "group", (coord,), (coord,), "original", time_grid=grid),
        DerivedQuantity("amount", "test@v1", ("input",), None, "time", "input_owned"),
    )
    rows = pa.table(
        {
            "key_0": [c.identity for c in grid.cells],
            "value": pa.array([None if x < 0 else x for x in states], type=pa.int64()),
            "cell_tag": ["undefined" if x < 0 else "defined" for x in states],
            "cell_reason": pa.array(
                ["no_scale" if x < 0 else None for x in states], type=pa.string()
            ),
        }
    )
    coverage = pa.table({"key_0": rows["key_0"], "complete": [True] * len(states)})
    return Capture(
        signature,
        "runs",
        ValuePredicate(binding, "gt", 0),
        ("key_0",),
        (save(rows),),
        save(coverage),
        None,
        ("fixture",),
        (signature,),
        (ScalarType("int64"),),
        ((("undefined", ("no_scale",)),),),
    )
