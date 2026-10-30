"""Typed PostgreSQL array keys preserve complete tuples without predicate expansion."""

from pathlib import Path

import ibis
import ibis.expr.operations as ops
import pytest

from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization.query_parameters import instantiate
from marivo.datasource.adapters import PhysicalRequirement
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.query_keys import integer_string_keys as _keys


def test_postgres_array_parameter_has_bounded_expression_size() -> None:
    table = ibis.table({"id": "int64", "tenant": "string"}, name="facts")
    columns = (
        DataColumn("selected.id", "id", ScalarDataType("int64"), False),
        DataColumn("selected.tenant", "tenant", ScalarDataType("string"), True),
    )
    parameter = ibis.table({"selected.id": "int64", "selected.tenant": "string"}, name="selected")
    expression = table.semi_join(
        parameter,
        [
            table.id.identical_to(parameter["selected.id"]),
            table.tenant.identical_to(parameter["selected.tenant"]),
        ],
    )
    sizes = []
    for count in (10, 1_000):
        output = _keys(tuple((2**53 + index, "a" if index % 2 else None) for index in range(count)))
        result = instantiate(expression, "selected", columns, output, provider="postgres")
        sql = ibis.to_sql(result, dialect="postgres")
        assert "UNNEST" in sql
        assert "UNION" not in sql
        sizes.append(len(result.op().find(ops.Node)))
    assert sizes[0] == sizes[1]


@pytest.mark.parametrize("backend,profile", source_params([("postgres", "table")]))
@pytest.mark.parametrize("string_type", ("VARCHAR(10)", "CHAR(4)"))
def test_array_key_relation_preserves_large_integer_nulls_and_tuple_correlation(
    backend: str,
    profile: str,
    string_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large = 2**53 + 1
    data = SourceData(
        f"id BIGINT, amount BIGINT, tenant {string_type}, revision BIGINT",
        f"({large},2,'a',1),({large + 1},4,'b',2),({large + 2},8,NULL,3)",
        "",
        [
            {"id": large, "amount": 2, "tenant": "a", "revision": 1},
            {"id": large + 1, "amount": 4, "tenant": "b", "revision": 2},
            {"id": large + 2, "amount": 8, "tenant": None, "revision": 3},
        ],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="array-keys")
        qualified = case.session.qualify(
            bound, PhysicalRequirement("query.source_read", 1, frozenset({"scan", "join", "group"}))
        )
        table = bound.relation
        parameter = ibis.table({"key.0": "int64", "key.1": "string"}, name="selected")
        selected = table.semi_join(
            parameter,
            [
                table.id.identical_to(parameter["key.0"]),
                table.tenant.identical_to(parameter["key.1"]),
            ],
        )
        expression = selected.aggregate(total=selected.amount.sum())
        columns = (
            DataColumn("key.0", "id", ScalarDataType("int64"), False),
            DataColumn("key.1", "tenant", ScalarDataType("string"), True),
        )
        tenant = "a   " if string_type == "CHAR(4)" else "a"
        for rows, expected in (
            (((large, tenant), (large + 1, tenant), (large + 2, None)), 10),
            (((large + 2, None),), 8),
        ):
            result = instantiate(expression, "selected", columns, _keys(rows), provider="postgres")
            issued = case.session.compile(
                qualified,
                result,
                purpose="query.array_keys",
                expected_schema=result.schema().to_pyarrow(),
            )
            frame = case.session.execute(issued)
            assert frame["total"].tolist() == [expected]
            assert "UNNEST" in issued.sql.upper()
            assert "UNION" not in issued.sql.upper()


@pytest.mark.parametrize("backend,profile", source_params([("postgres", "table")]))
@pytest.mark.parametrize("weight", (1.5, float("inf")))
def test_timestamp_keys_preserve_char_padding_and_native_infinity(
    backend: str, profile: str, weight: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    import pandas as pd

    from marivo.analysis.core.data_types import TimestampDataType
    from marivo.analysis.materialization.graph_exchange import StageOutput

    data = SourceData(
        "id BIGINT, point TIMESTAMPTZ, tenant CHAR(4), weight DOUBLE PRECISION",
        "(1,'2026-01-01 00:00:00.000001+00','a',1.5),(2,'2026-01-01 00:00:00.000001+00','a','Infinity'),(3,'2026-01-01 00:00:00.000002+00','a',1.5)",
        "",
        [],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="calendar-mixed-keys")
        table = bound.relation
        parameter = ibis.table(
            {"point": "timestamp('UTC', 6)", "tenant": "string", "weight": "float64"},
            name="selected",
        )
        expression = table.semi_join(
            parameter, [table[name].identical_to(parameter[name]) for name in parameter.columns]
        ).select("id")
        columns = (
            DataColumn("point", "point", TimestampDataType("us", "UTC"), False),
            DataColumn("tenant", "tenant", ScalarDataType("string"), False),
            DataColumn("weight", "weight", ScalarDataType("float64"), False),
        )
        keys = StageOutput.from_frame(
            pd.DataFrame(
                {
                    "point": [datetime(2026, 1, 1, microsecond=1, tzinfo=timezone.utc)],
                    "tenant": ["a   "],
                    "weight": [weight],
                }
            ),
            columns,
            ("point", "tenant", "weight"),
        )
        expression = instantiate(expression, "selected", columns, keys, provider=backend)
        result = case.session.collect_bounded(expression, purpose="calendar.mixed_keys", max_rows=3)
        assert result.id.tolist() == ([1] if weight == 1.5 else [2])
