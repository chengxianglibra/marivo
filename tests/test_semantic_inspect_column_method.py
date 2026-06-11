from __future__ import annotations

import pandas as pd
import pytest

import marivo.semantic as ms
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.metadata import ColumnMetadata, TableMetadata
from marivo.semantic.reader import SemanticProject


def _metadata(datasource: str, table: TableSourceIR) -> TableMetadata:
    return TableMetadata(
        datasource=datasource,
        table=table.table,
        database=table.database,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata("status", "VARCHAR", True, "status", 1),
            ColumnMetadata("amount", "DOUBLE", True, "amount", 2),
        ),
        partitions=(),
        warnings=(),
    )


def test_inspect_columns_uses_fixed_five_row_sample_and_disconnects(tmp_path, monkeypatch):
    project = SemanticProject(workspace_dir=tmp_path)
    limits: list[int] = []
    selected_columns: list[str] = []
    disconnected: list[bool] = []

    class FakeTable:
        def select(self, *columns):
            selected_columns.extend(columns)
            return self

        def limit(self, limit):
            limits.append(limit)
            return self

        def execute(self):
            return pd.DataFrame(
                {
                    "status": ["paid", "paid", "void", "paid", "pending"],
                    "amount": [10.0, 20.0, 5.0, None, 40.0],
                }
            )

    class FakeBackend:
        def table(self, table):
            assert table == "orders"
            return FakeTable()

        def disconnect(self):
            disconnected.append(True)

    monkeypatch.setattr(project, "_inspect_metadata", _metadata)
    monkeypatch.setattr(project, "_build_datasource_backend", lambda _datasource: FakeBackend())

    evidence = project.inspect_columns(
        "warehouse",
        ms.table("orders"),
        columns=("status", "amount"),
    )

    assert selected_columns == ["status", "amount"]
    assert limits == [5]
    assert disconnected == [True]
    by_col = {item.column: item for item in evidence}
    assert by_col["status"].sample_values == ("paid", "paid", "void", "paid", "pending")
    assert by_col["amount"].min_value == 5.0
    assert by_col["amount"].max_value == 40.0


def test_inspect_columns_disconnects_when_sampling_raises(tmp_path, monkeypatch):
    project = SemanticProject(workspace_dir=tmp_path)
    disconnected: list[bool] = []

    class FakeTable:
        def select(self, *_columns):
            return self

        def limit(self, _limit):
            return self

        def execute(self):
            raise RuntimeError("sample failed")

    class FakeBackend:
        def table(self, _table):
            return FakeTable()

        def disconnect(self):
            disconnected.append(True)

    monkeypatch.setattr(project, "_inspect_metadata", _metadata)
    monkeypatch.setattr(project, "_build_datasource_backend", lambda _datasource: FakeBackend())

    with pytest.raises(RuntimeError, match="sample failed"):
        project.inspect_columns("warehouse", ms.table("orders"), columns=("status",))

    assert disconnected == [True]
