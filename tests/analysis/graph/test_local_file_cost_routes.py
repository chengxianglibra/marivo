"""Exact local-file consumers for the original integer-total question."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

from marivo.analysis.core.data_types import ScalarDataType
from marivo.datasource.adapters import CompiledRead, SourceSession
from tests.analysis.graph.physical_workloads import _rows, workload
from tests.analysis.numeric.pandas_coordinates import ordinary_columns


@pytest.mark.parametrize(
    ("profile", "composition"),
    (
        ("csv", "source"),
        ("local-json", "source"),
        ("csv", "rows"),
        ("local-json", "rows"),
        ("parquet", "rows"),
    ),
)
def test_file_cost_original_sum_executes_exact_real_file_route(
    profile: str,
    composition: Literal["source", "rows"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = _rows(4, "baseline")
    amounts = [row["amount"] for row in facts]
    assert all(amount is None or type(amount) is int for amount in amounts)
    total = sum(amount for amount in amounts if isinstance(amount, int))
    captures: list[tuple[CompiledRead, pd.DataFrame]] = []
    execute = SourceSession.execute

    def captured(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
        frame = execute(source, read)
        captures.append((read, frame))
        return frame

    monkeypatch.setattr(SourceSession, "execute", captured)
    with workload("duckdb", profile, len(facts), "baseline", tmp_path, monkeypatch) as work:
        values = work.values()
        query = (
            work.total()
            if composition == "source"
            else values.reduce({"value": values["value"].sum()})
        )
        result = query.persist()
        assert work.validate(result)["passed"] is True
        assert result.to_pandas()[("value", "value")].tolist() == [total]
        assert captures and any("SUM(" in read.sql.upper() for read, _ in captures)
        observed = values.persist()
        frame = observed.to_pandas()
        assert set(frame[ordinary_columns(frame)[0]]) == {
            ("a", 2**53 + index, 1) for index in range(len(facts))
        }
        assert pd.isna(frame[("value", "value")].iloc[0])
        assert frame[("value", "value")].iloc[1:].tolist() == [2, 3, 4]
        assert frame[("value", "value")].notna().sum() == 3
        assert [
            column.dtype for column in observed.artifact._manifest.data.schema.axes[1].columns
        ] == [
            ScalarDataType("string"),
            ScalarDataType("int64"),
            ScalarDataType("int64"),
        ]
        assert work.session._runtime.store.resources(work.session.id) == ()
