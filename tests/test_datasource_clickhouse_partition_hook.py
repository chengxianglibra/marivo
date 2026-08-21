"""ClickHouse partition-value listing contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest
from ibis.backends import BaseBackend

from marivo.datasource.authoring import ClickHouseSpec
from marivo.datasource.engines.base import PartitionProbeRequest
from marivo.datasource.engines.clickhouse import inspect_partition_values
from marivo.datasource.ir import DatasourceSourceLocation, TableSourceIR


@dataclass
class _Cursor:
    description: tuple[tuple[str, Any], ...]
    rows: tuple[tuple[Any, ...], ...]

    def fetchall(self) -> tuple[tuple[Any, ...], ...]:
        return self.rows


class _Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def raw_sql(self, sql: str) -> _Cursor:
        self.calls.append(sql)
        return _Cursor(description=(("dt", "String"),), rows=(("20260101",),))


def test_partition_enumeration_applies_requested_ascending_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    datasource_ir = ClickHouseSpec(name="warehouse", host="clickhouse.example").to_ir(
        location=DatasourceSourceLocation(file="<test>", line=1)
    )
    monkeypatch.setattr(
        "marivo.datasource.engines.clickhouse.clickhouse_system_parts_target",
        lambda *_args: ("analytics", "orders"),
    )
    request = PartitionProbeRequest(
        backend=cast("BaseBackend", backend),
        datasource_ir=datasource_ir,
        source=TableSourceIR(table="orders", database="analytics"),
        partition_columns=("dt",),
        limit=2,
        order="asc",
    )

    result = inspect_partition_values(request)

    assert result.rows == ({"dt": "20260101"},)
    assert "ORDER BY partition ASC LIMIT 2" in backend.calls[-1]
