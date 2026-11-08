"""Compact calendar ranges and exact JSON parameters on old and current MySQL servers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path

import ibis
import pandas as pd
import pytest
import sqlglot as sg
import sqlglot.expressions as sge

import marivo.analysis as mv
from marivo.datasource._calendar_relations import (
    calendar_data,
    calendar_range,
    compile_calendar_range,
)
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.source_cases import Case, SourceData, source_case
from tests.shared_fixtures import catalog_value, time_population
from tests.support.source_trace import capture_source

SERVERS = [
    pytest.param(23306, "MARIVO_MYSQL_ANALYSIS_TEST", id="mysql-84", marks=pytest.mark.runtime),
    pytest.param(
        23307, "MARIVO_MARIADB_ANALYSIS_TEST", id="mariadb-114", marks=pytest.mark.runtime
    ),
    pytest.param(
        23309, "MARIVO_MARIADB_103_ANALYSIS_TEST", id="mariadb-103", marks=pytest.mark.runtime
    ),
]


@pytest.fixture
def mysql_case(
    port: int, flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Case]:
    if os.environ.get(flag) != "1":
        pytest.skip(f"Explicit ready service required: {flag}=1")
    monkeypatch.setenv("MARIVO_MYSQL_ANALYSIS_TEST", "1")
    monkeypatch.setattr(mysql, "connection", partial(mysql.connection, port=port))
    monkeypatch.setattr(mysql, "setup", partial(mysql.setup, port=port))
    monkeypatch.setattr(mysql, "PORT", port)
    data = SourceData(
        "id BIGINT, point DATETIME(6), amount BIGINT",
        "(1,'2025-12-31 15:59:59.999999',100),"
        "(2,'2025-12-31 16:00:00',2),"
        "(3,'2026-01-01 15:59:59.999999',3),"
        "(4,'2026-01-01 16:00:00',7),"
        "(5,'2026-01-14 15:59:59.999999',11),"
        "(6,'2026-01-14 16:00:00',200)",
        "",
        [],
    )
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        if port == 23309:
            assert str(case.environment["version"]).startswith("10.3.18-MariaDB")
        yield case


def test_mysql_range_sql_stays_compact_at_high_cardinality() -> None:
    sizes = []
    for count in (1_000, 1_000_000, 1_000_000_000_000):
        sql = compile_calendar_range(calendar_range(count).op(), "mysql").sql(dialect="mysql")
        sizes.append(len(sql.encode()))
    assert sizes[2] < 8192
    assert sizes[1] < 3 * sizes[0]
    assert sizes[2] < 3 * sizes[1]


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_calendar_range_exact_positions(mysql_case: Case) -> None:
    for count in (0, 1, 9, 10, 11, 1001):
        relation = calendar_range(count)
        rows = mysql_case.session.collect_bounded(
            relation.order_by("position"), purpose="calendar.positions", max_rows=max(1, count)
        )
        assert rows.position.tolist() == list(range(count))
        assert pd.api.types.is_integer_dtype(rows.position.dtype)


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_calendar_parameters_retain_types_and_nulls(mysql_case: Case) -> None:
    schema = ibis.schema(
        {
            "id": "!int64",
            "key.0": "timestamp('UTC', 6)",
            "day": "date",
            "signed": "int64",
            "unsigned": "uint64",
            "flag": "boolean",
            "text": "string",
            "decimal": "decimal(24, 6)",
            "number": "float64",
        }
    )
    point = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
    special = "quote'\\\n雪"
    values = (
        (
            0,
            point,
            date(1960, 1, 3),
            -(2**63),
            2**64 - 1,
            True,
            special,
            Decimal("123456789012.123456"),
            1.25,
        ),
        (1, *(None,) * 8),
        (2, point, date(2026, 1, 2), 2**63 - 1, 0, False, "null", Decimal("-0.000001"), -1.25e-15),
    )
    relation = calendar_data(schema, values)
    rows = mysql_case.session.collect_bounded(
        relation.order_by("id"), purpose="calendar.parameters", max_rows=3
    )
    assert rows.id.tolist() == [0, 1, 2]
    assert rows.loc[1].iloc[1:].isna().all()
    assert rows["key.0"].take([0, 2]).tolist() == [pd.Timestamp(point)] * 2
    assert rows.day.dt.date.take([0, 2]).tolist() == [date(1960, 1, 3), date(2026, 1, 2)]
    assert rows.signed.take([0, 2]).tolist() == [-(2**63), 2**63 - 1]
    assert rows.unsigned.take([0, 2]).tolist() == [2**64 - 1, 0]
    assert rows.flag.take([0, 2]).tolist() == [True, False]
    assert rows.text.take([0, 2]).tolist() == [special, "null"]
    assert rows["decimal"].take([0, 2]).tolist() == [
        Decimal("123456789012.123456"),
        Decimal("-0.000001"),
    ]
    assert rows.number.take([0, 2]).tolist() == [1.25, -1.25e-15]
    assert pd.api.types.is_bool_dtype(rows.flag.dtype)
    assert pd.api.types.is_integer_dtype(rows.signed.dtype)
    assert pd.api.types.is_integer_dtype(rows.unsigned.dtype)
    sql = mysql_case.session.submissions[-1].sql
    literals = sg.parse_one(sql, read="mysql").find_all(sge.Literal)
    assert (
        sum(literal.is_string and literal.this == relation.op().payload for literal in literals)
        == 1
    )
    assert len(sql.encode()) < 65536
    empty = mysql_case.session.collect_bounded(
        calendar_data(schema, ()), purpose="calendar.empty_parameters", max_rows=1
    )
    assert empty.empty and empty.columns.tolist() == list(schema.names)


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_public_daily_output_is_sparse_unless_domain_is_explicit(
    mysql_case: Case,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    case = mysql_case
    assert isinstance(case.source, TableSourceIR)
    monkeypatch.chdir(tmp_path)
    semantic_project_factory(
        {
            "datasources/warehouse.py": _datasource_model("mysql", case, monkeypatch),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test',default=True)\n",
            "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
            + "point=ms.time_dimension_column(name='point',entity=facts,column='point')\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
        }
    )
    session = mv.session.get_or_create("mysql-calendar", timezone="Asia/Shanghai")
    point = catalog_value(session.catalog, "sales.facts.point", kind="time_dimension")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
    axis_scope = session.time_scope(start="2026-01-01", end="2026-01-15")
    axis = axis_scope.by(mv.grain("day"))
    trace = capture_source(monkeypatch)
    rows = session.observe({"total": amount.sum(time=point)}, by=(axis,)).compute().to_pandas()
    first = datetime(2025, 12, 31, 16, tzinfo=timezone.utc)
    assert rows["calendar:day"].dt.tz_convert("UTC").tolist() == [
        first + timedelta(days=i) for i in (0, 1, 13)
    ]
    assert rows.total.tolist() == [5, 7, 11]

    rows = (
        session.observe({"total": amount.sum(time=point)}, by=time_population(axis))
        .compute()
        .to_pandas()
    )
    assert rows["calendar:day"].dt.tz_convert("UTC").tolist() == [
        first + timedelta(days=i) for i in range(14)
    ]
    assert rows.total.tolist() == [5, 7, *([0] * 11), 11]
    assert trace.native_sql
    assert all(len(sql.encode()) < 65536 for sql in trace.native_sql)
