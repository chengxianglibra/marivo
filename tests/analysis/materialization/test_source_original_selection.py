"""Selected complete keys parameterize later current-source original reductions."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import ibis
import ibis.expr.operations as ops
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization import query_parameters
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.environment import (
    clickhouse_analysis,
    mysql_analysis,
    postgres_analysis,
    trino_analysis,
)
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
from tests.support.query_keys import integer_string_keys as _keys
from tests.support.source_trace import capture_source

CASES = [("trino", "iceberg"), ("clickhouse", "mergetree")]
COLUMNS = (
    DataColumn("key.0", "id", ScalarDataType("int64"), False),
    DataColumn("key.1", "tenant", ScalarDataType("string"), True),
)


def _data() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": i,
            "tenant": "t1" if i <= 40 else "t2",
            "category": None if i <= 40 else "B",
            "amount": 3 if i <= 40 else 2,
            "big": None,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for i in range(1, 81)
    ]
    values = ",".join(
        f"({row['id']},'{row['tenant']}',"
        + ("NULL" if row["category"] is None else "'B'")
        + f",{row['amount']},NULL,1,DATE '2026-07-01')"
        for row in rows
    )
    return SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
        values,
        "id Int64, tenant String, category Nullable(String), amount Int64, big Nullable(Int64), weight Int64, day Date",
        rows,
    )


def _mutate(backend: str, case: Case, mutation: str) -> None:
    assert isinstance(case.source, TableSourceIR)
    if backend == "trino":
        with trino_analysis.connection(admin=True) as connection:
            cursor = connection.cursor()
            try:
                table = f"iceberg.analysis.{case.source.table}"
                sql = (
                    f"DELETE FROM {table} WHERE category IS NULL"
                    if mutation == "delete"
                    else f"INSERT INTO {table} VALUES (81,'t3',NULL,7,NULL,1,DATE '2026-07-01'),"
                    "(82,'t2','B',10000,NULL,1,DATE '2026-07-01')"
                )
                cursor.execute(sql).fetchall()
            finally:
                cursor.close()
    elif backend in ("postgres", "mysql"):
        table = case.source.table
        sql = (
            f"DELETE FROM {table} WHERE category IS NULL"
            if mutation == "delete"
            else f"INSERT INTO {table} VALUES (81,'t1',NULL,7,NULL,1,DATE '2026-07-01'),"
            "(82,'t2','B',10000,NULL,1,DATE '2026-07-01')"
        )
        if backend == "postgres":
            with postgres_analysis.connection(admin=True) as connection:
                connection.execute(sql)
        else:
            with mysql_analysis.connection(admin=True) as connection:
                cursor = connection.cursor()
                try:
                    cursor.execute(sql)
                finally:
                    cursor.close()
    else:
        with clickhouse_analysis.connection(admin=True) as connection:
            table = "qualification." + case.source.table
            if mutation == "delete":
                connection.command(
                    f"ALTER TABLE {table} DELETE WHERE category IS NULL SETTINGS mutations_sync=2"
                )
            else:
                connection.insert(
                    table,
                    [
                        [81, "t3", None, 7, None, 1, date(2026, 7, 1)],
                        [82, "t2", "B", 10000, None, 1, date(2026, 7, 1)],
                    ],
                    column_names=["id", "tenant", "category", "amount", "big", "weight", "day"],
                )


@pytest.mark.parametrize("provider", ("trino", "clickhouse"))
def test_bulk_selected_keys_have_bounded_expression_and_typed_empty(provider: str) -> None:
    table = ibis.table({"id": "int64", "tenant": "string"}, name="facts")
    parameter = ibis.table({"key.0": "int64", "key.1": "string"}, name="selected")
    expression = table.semi_join(
        parameter,
        [table.id.identical_to(parameter["key.0"]), table.tenant.identical_to(parameter["key.1"])],
    )
    sizes = []
    for count in (0, 10, 1000):
        result = query_parameters.instantiate(
            expression,
            "selected",
            COLUMNS,
            _keys(tuple((2**53 + i, "a" if i % 2 else None) for i in range(count))),
            provider=provider,
        )
        sql = ibis.to_sql(result, dialect=provider).upper()
        assert ("UNNEST" if provider == "trino" else "ARRAY JOIN") in sql
        assert "UNION" not in sql
        if count:
            sizes.append(len(result.op().find(ops.Node)))
    assert sizes[0] == sizes[1]


def _selected_original_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    operation: str,
    mutation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _data()
    if backend == "mysql":
        data = SourceData(
            data.columns.replace("VARCHAR(16)", "VARCHAR(16) COLLATE utf8mb4_0900_bin"),
            data.values,
            data.clickhouse_columns,
            data.rows,
        )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        if backend == "mysql":
            assert isinstance(case.source, TableSourceIR)
            with mysql_analysis.connection(admin=True) as connection:
                cursor = connection.cursor()
                try:
                    cursor.execute(f"ALTER TABLE {case.source.table} ENGINE=MyISAM")
                finally:
                    cursor.close()
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("selected-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        axes = (tenant, category) if operation == "rollup" else (category,)
        fine = session.observe({"value": amount.sum()}, by=axes)
        fixed = fine.top(0 if operation == "empty" else 1, by=fine["value"]).compute()
        initial = fixed.to_pandas()
        if operation != "empty":
            assert len(initial) == 1
            assert pd.isna(initial.iloc[0, len(axes) - 1])
            if operation == "rollup":
                assert initial.iloc[:, 0].tolist() == ["t1"]
        keys = session.population_from(fixed, on=tuple(zip(fixed.axes, axes, strict=True)))
        _mutate(backend, case, mutation)
        if operation == "rollup":
            query = session.observe(
                {"value": amount.sum()},
                by=keys.project(by=(tenant,)),
                where=mv.in_population(keys),
            )
        elif operation == "drill":
            query = session.observe(
                {"value": amount.sum()},
                by=(category, tenant),
                where=mv.in_population(keys),
            )
        else:
            query = session.observe({"value": amount.sum()}, where=mv.in_population(keys))
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        if operation == "rollup":
            assert frame.iloc[:, 0].tolist() == ["t1"]
            assert frame["value", "value"].tolist() == [
                0 if mutation == "delete" else 127 if backend in ("postgres", "mysql") else 120
            ]
        elif operation == "drill":
            assert frame["value", "value"].tolist() == ([] if mutation == "delete" else [120, 7])
        else:
            assert frame["value", "value"].tolist() == [0]
        pd.testing.assert_frame_equal(fixed.to_pandas(), initial, check_exact=True)
        assert trace.returned_rows
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize(
    "operation,mutation",
    (
        ("rollup", "insert"),
        ("drill", "insert"),
        ("rollup", "delete"),
        ("drill", "delete"),
        ("empty", "insert"),
    ),
)
def test_selected_original_uses_fixed_keys_and_current_contributions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    operation: str,
    mutation: str,
) -> None:
    _selected_original_case(
        tmp_path, monkeypatch, semantic_project_factory, backend, profile, operation, mutation
    )


@pytest.mark.parametrize(
    "backend,profile", source_params([("postgres", "table"), ("mysql", "innodb-table")])
)
def test_selected_original_rereads_current_postgres_and_myisam_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    _selected_original_case(
        tmp_path, monkeypatch, semantic_project_factory, backend, profile, "rollup", "insert"
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_selected_bulk_keys_preserve_large_integer_tuple_correlation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, profile: str
) -> None:
    from marivo.datasource.adapters import PhysicalRequirement

    large = 2**53 + 1
    data = SourceData(
        "id BIGINT, amount BIGINT, tenant VARCHAR(10)",
        f"({large},2,'a'),({large + 1},4,'b'),({large + 2},8,NULL)",
        "id Int64, amount Int64, tenant Nullable(String)",
        [
            {"id": large, "amount": 2, "tenant": "a"},
            {"id": large + 1, "amount": 4, "tenant": "b"},
            {"id": large + 2, "amount": 8, "tenant": None},
        ],
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        bound = case.session.bind(case.source, source_identity="selected-bulk")
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
        for rows, expected in (
            (((large, "a"), (large + 1, "a"), (large + 2, None)), 10),
            ((), 0 if backend == "clickhouse" else None),
        ):
            result = query_parameters.instantiate(
                expression, "selected", COLUMNS, _keys(rows), provider=backend
            )
            issued = case.session.compile(
                qualified,
                result,
                purpose="query.selected_bulk",
                expected_schema=result.schema().to_pyarrow(),
            )
            value = case.session.execute(issued).iloc[0, 0]
            assert pd.isna(value) if expected is None else value == expected
            assert "UNION" not in issued.sql.upper()


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_empty_selection_retains_widened_parent_sum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:

    data = _data()
    data = SourceData(
        data.columns,
        data.values.replace(",3,NULL,1,", f",{2**63 - 1},NULL,1,"),
        data.clickhouse_columns,
        [{**row, "amount": 2**63 - 1 if row["tenant"] == "t1" else 2} for row in data.rows],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("selected-overflow", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        fine = session.observe({"value": amount.sum()}, by=(category,))
        query = fine.top(0, by=fine["value"])
        trace = capture_source(monkeypatch)
        assert fine.compute().to_pandas()["value", "value"].max() >= 2**63
        assert query.compute().to_pandas().empty
        assert len(trace.returned_rows) == 2
        assert trace.returned_rows == [2, 2]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_parent_mean_filter_keeps_initial_native_pandas_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = _data()
    rows = [{**row, "amount": 2**53 + 1 if i == 0 else 0} for i, row in enumerate(data.rows[:3])]
    values = ",".join(
        f"({row['id']},'t1',NULL,{row['amount']},NULL,1,DATE '2026-07-01')" for row in rows
    )
    data = SourceData(data.columns, values, data.clickhouse_columns, rows)
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("selected-parent-mean", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        fine = session.observe({"value": amount.mean()}, by=(category,))
        fixed = fine.compute()
        threshold = fixed.to_pandas()["value", "value"].iloc[0]
        selected = fixed.where(fixed["value"] > float(threshold)).compute()
        assert selected.to_pandas().empty
        keys = session.population_from(selected, on=((selected.axes[0], category),))
        trace = capture_source(monkeypatch)
        frame = (
            session.observe({"value": amount.mean()}, where=mv.in_population(keys))
            .compute()
            .to_pandas()
        )
        assert len(frame) == 1
        assert pd.isna(frame["value", "value"].iloc[0])
        assert fixed.to_pandas()["value", "value"].iloc[0] == threshold
        assert trace.returned_rows


@pytest.mark.parametrize("mutation", ("insert", "delete"))
def test_parquet_selected_original_rereads_file_without_reselecting_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    mutation: str,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "facts.parquet"
    rows = [
        {key: row[key] for key in ("id", "tenant", "category", "amount")} for row in _data().rows
    ]
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\nmd.duckdb(name='warehouse')\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.parquet({str(path)!r}),primary_key=['id'])\n"
            + "tenant=ms.dimension_column(name='tenant',entity=facts,column='tenant')\n"
            + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("selected-file", timezone="UTC")
    amount = session.catalog.fields.get("sales.facts.amount")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    category = session.catalog.fields.get("sales.facts.category")
    fine = session.observe({"value": amount.sum()}, by=(tenant, category))
    fixed = fine.top(1, by=fine["value"]).compute()
    initial = fixed.to_pandas()
    assert initial.iloc[:, 0].tolist() == ["t1"]
    assert pd.isna(initial.iloc[0, 1])
    updated = (
        [row for row in rows if row["tenant"] != "t1"]
        if mutation == "delete"
        else [
            *rows,
            {"id": 81, "tenant": "t1", "category": None, "amount": 7},
            {"id": 82, "tenant": "t2", "category": "B", "amount": 10000},
        ]
    )
    pq.write_table(pa.Table.from_pylist(updated, schema=table.schema), path)
    keys = session.population_from(fixed, on=((fixed.axes[0], tenant), (fixed.axes[1], category)))
    trace = capture_source(monkeypatch)
    frame = (
        session.observe(
            {"value": amount.sum()},
            by=keys.project(by=(tenant,)),
            where=mv.in_population(keys),
        )
        .compute()
        .to_pandas()
    )
    assert frame.iloc[:, 0].tolist() == ["t1"]
    assert frame["value", "value"].tolist() == [0 if mutation == "delete" else 127]
    pd.testing.assert_frame_equal(fixed.to_pandas(), initial, check_exact=True)
    assert trace.returned_rows
    assert not any("__original_value" in name for names in trace.returned_columns for name in names)
