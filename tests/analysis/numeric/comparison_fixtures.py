"""Current Frame comparison recipes over the shared three-row source."""

from typing import Literal

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.artifact_data import DataSchema
from marivo.analysis.core.data_types import ScalarDataType

Operation = Literal["nested", "union", "ratio"]
COMPLETE = {("a", 2**53, 1), ("a", 2**53 + 1, 2), ("b", 2**53 + 1, 1)}
DEFINED = {("a", 2**53, 1): 2, ("a", 2**53 + 1, 2): 0, ("b", 2**53 + 1, 1): 4}


def comparison_queries(session: mv.Session) -> dict[Operation, mv.AnalysisFrame]:
    members = session.members(ms.ref.entity("sales.facts"))
    metric = session.catalog.metrics.get("sales.total")

    def observe(month: int) -> mv.AnalysisFrame:
        return session.observe(
            {"value": metric},
            during=session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
            by=members,
        )

    current, baseline, earlier = observe(8), observe(7), observe(6)
    first = current.compare(baseline, on=tuple(zip(current.axes, baseline.axes, strict=True)))
    first = first.select({"value": first["absolute.value"]})
    second = baseline.compare(earlier, on=tuple(zip(baseline.axes, earlier.axes, strict=True)))
    second = second.select({"value": second["absolute.value"]})
    nested = first.compare(second, on=tuple(zip(first.axes, second.axes, strict=True)))
    nested = nested.select({"value": nested["absolute.value"]})
    left, right = current.where(current["value"] > 0), baseline.where(baseline["value"] > 0)
    union = left.compare(right, how="outer", on=tuple(zip(left.axes, right.axes, strict=True)))
    union = union.select({"value": union["absolute.value"]})
    return {
        "nested": nested,
        "union": union,
        "ratio": current.select({"value": current["value"] / current["value"]}),
    }


def assert_comparison(result: mv.AnalysisFrame, operation: Operation) -> None:
    frame = result.to_pandas()
    values = dict(zip(frame.iloc[:, 0], frame[("value", "value")], strict=True))
    assert isinstance(result._schema, DataSchema)
    assert [column.dtype for column in result._schema.axes[0].columns] == [
        ScalarDataType("string"),
        ScalarDataType("int64"),
        ScalarDataType("int64"),
    ]
    if operation == "nested":
        assert values == DEFINED and set(values) == COMPLETE
    elif operation == "union":
        assert set(values) == {key for key, value in DEFINED.items() if value > 0}
        assert frame[("value", "value")].isna().all()
        assert frame[("presence", "left_present")].all()
        assert not frame[("presence", "right_present")].any()
    else:
        assert set(values) == COMPLETE
        for key, value in DEFINED.items():
            assert values[key] == 1.0 if value else pd.isna(values[key])
