"""Current Frame comparison recipes over the shared three-row source."""

from typing import Literal

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.core.artifact_data import DataSchema
from marivo.analysis.core.data_types import ScalarDataType

Operation = Literal["nested", "union", "ratio"]
COMPLETE = {("a", 2**53, 1), ("a", 2**53 + 1, 2), ("b", 2**53 + 1, 1)}
DEFINED = {("a", 2**53, 1): 2, ("a", 2**53 + 1, 2): None, ("b", 2**53 + 1, 1): 4}


def comparison_queries(session: mv.Session) -> dict[Operation, mv.AnalysisFrame]:
    members = session.members(session.catalog.get("entity:sales.facts").value)
    metric = session.catalog.get("metric:sales.total").value

    def observe(month: int) -> mv.AnalysisFrame:
        return session.observe(
            {"value": metric},
            by=mv.product(
                session.time_scope(start=f"2026-{month:02}-01", end=f"2026-{month + 1:02}-01"),
                members,
            ),
        )

    current, baseline, earlier = observe(8), observe(7), observe(6)
    first = current.compare(
        {
            "reference": mv.baseline(
                baseline,
                on=tuple(zip(current.axes, baseline.axes, strict=True)),
                match=mv.ordinal(),
            )
        }
    )
    first = first.reduce({"value": first["reference.absolute.value"].sum()}, by=(first.axes[1],))
    second = baseline.compare(
        {
            "reference": mv.baseline(
                earlier, on=tuple(zip(baseline.axes, earlier.axes, strict=True)), match=mv.ordinal()
            )
        }
    )
    second = second.reduce(
        {"value": second["reference.absolute.value"].sum()}, by=(second.axes[1],)
    )
    nested = first.compare(
        {"reference": mv.baseline(second, on=tuple(zip(first.axes, second.axes, strict=True)))}
    )
    nested = nested.select({"value": nested["reference.absolute.value"]})
    left, right = current.where(current["value"] > 0), baseline.where(baseline["value"] > 0)
    union = left.compare(
        {
            "reference": mv.baseline(
                right, on=tuple(zip(left.axes, right.axes, strict=True)), match=mv.ordinal()
            )
        }
    )
    union = union.select({"value": union["reference.absolute.value"]})
    return {
        "nested": nested,
        "union": union,
        "ratio": current.select({"value": current["value"] / current["value"]}),
    }


def assert_comparison(result: mv.AnalysisFrame, operation: Operation) -> None:
    frame = result.to_pandas()
    identity = next(axis for axis in result.axes if axis.role == "identity")
    values = dict(zip(frame[identity.name.rsplit(".", 1)[-1]], frame["value"], strict=True))
    assert isinstance(result._schema, DataSchema)
    assert [
        column.dtype
        for column in next(
            axis for axis in result._schema.axes if axis.token == identity.token
        ).columns
    ] == [
        ScalarDataType("string"),
        ScalarDataType("int64"),
        ScalarDataType("int64"),
    ]
    if operation == "nested":
        assert {key: None if pd.isna(value) else value for key, value in values.items()} == DEFINED
        assert set(values) == COMPLETE
    elif operation == "union":
        assert set(values) == {
            key for key, value in DEFINED.items() if value is not None and value > 0
        }
        assert frame["value"].isna().all()
        assert frame["current.present"].all()
        assert not frame["reference.present"].any()
    else:
        assert set(values) == COMPLETE
        for key, value in DEFINED.items():
            assert values[key] == 1.0 if value else pd.isna(values[key])
