"""Recover named float/Decimal distributions with complete retained parts."""

from decimal import Decimal

import pandas as pd

import marivo.analysis as mv
from marivo.analysis.materialization.cell_arrow import required


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(name): None if pd.isna(value) else value for name, value in row.items()}
        for row in frame.to_dict("records")
    ]


def check_original(value: mv.MaterializedNumericRelation, physical: str, kind: str) -> None:
    assert value._dataset is not None
    table = value._dataset.verified().primary
    records = sorted(_records(table), key=lambda record: required(record["key_0"], str))
    assert [record["key_0"] for record in records] == ["A", "B", "C", "D"]
    if physical == "decimal":
        assert kind == "quantile"
        assert [record["value"] for record in records[:3]] == [
            Decimal("1.01"),
            Decimal("1.02"),
            Decimal("1.04"),
        ]
        assert all(isinstance(record["value"], Decimal) for record in records[:3])
        assert [record["cell_tag"] for record in records] == ["defined"] * 3 + ["null"]
    elif "distinct" in kind:
        assert [record["value"] for record in records] == [1, 1, 1, 0]
        assert all(record["cell_tag"] == "defined" for record in records)
    else:
        assert [record["value"] for record in records[:3]] == [450, 150, 400]
        assert [record["cell_tag"] for record in records] == ["defined"] * 3 + ["null"]
    assert all(record["value"] is None for record in records if record["cell_tag"] == "null")
    assert "relation.rollup()" not in {action.call for action in value.contract().actions}
