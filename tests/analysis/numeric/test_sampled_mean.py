"""Native sample reducers preserve timestamp weighting and NULL checks."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isnan
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd
import pytest
import sqlglot
import sqlglot.expressions as sge

import marivo.analysis as mv
from marivo.analysis.materialization import sample_fusion
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


def _assert_no_true_filters(sql: str, dialect: str) -> None:
    query = sqlglot.parse_one(sql, read=dialect)
    for clause in query.find_all(sge.Where):
        assert not any(
            isinstance(node, sge.Boolean) and node.this is True
            for node in clause.this.walk(prune=lambda node: isinstance(node, sge.Query))
        ), clause.sql(dialect=dialect)


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("reducer", ["mean", "min", "max"])
@pytest.mark.parametrize("presence", [False, True])
def test_sample_folds_compile_native_aggregates_without_clock_completion(
    backend: str,
    reducer: str,
    presence: bool,
) -> None:
    from marivo._definition.expressions import RowField, SourceAggregate
    from marivo._definition.types import ExactRef, SumAll
    from marivo.analysis.compiler.observation import ReductionInput, lower_observation
    from marivo.refs import ref

    table = ibis.table(
        {
            "amount": "float64",
            "point": ibis.dtype("timestamp('UTC', 6)").copy(nullable=False),
            "category": "string",
        },
        name="samples",
    )
    table = table.filter(
        table.point >= ibis.literal("2026-10-01").cast(table.point.type()),
        table.point < ibis.literal("2026-10-02").cast(table.point.type()),
    )
    field = RowField(
        ExactRef(ref.measure("sales.facts.amount"), "v1"),
        ExactRef(ref.entity("sales.facts"), "v1"),
        None,
        SumAll(),
    )
    prepared = ReductionInput(
        SourceAggregate(field, "mean", None),
        (("key.0", "category"), ("key.1", "point")),
        "value",
        provider=backend,
        preserve_sample_presence=presence,
    )
    relation, checks = lower_observation(
        prepared,
        table,
        {
            "sales.facts.amount": "amount",
            "category": "category",
            "point": "point",
        },
        (),
    )
    from marivo._definition.expressions import PerReduce
    from marivo._definition.types import AxisBinding
    from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
    from marivo.analysis.compiler.sample_reductions import SampleLowerer
    from marivo.analysis.core.data_types import DataColumn, ScalarDataType

    sample = AxisBinding(field.root, field.ref, (), "field")
    node = PerReduce(prepared.node, (sample,), reducer, nulls="error")
    binding = FormulaInput(
        (0, 0),
        "source",
        DataColumn("value", "value", ScalarDataType("float64"), True),
        False,
        keys=("key.0", "key.1"),
        key_groups=(("key.0",), ("key.1",)),
    )

    def unavailable(*args: object) -> ir.Table:
        raise AssertionError("implicit samples must not synthesize a grid")

    result = SampleLowerer(
        FormulaProgram((("v", node),), (), (binding,)),
        {"source": relation},
        ("key.0",),
        backend,
        unavailable,
        unavailable,
    ).lower(node, (0,), ("key.0",))
    sql = ibis.to_sql(result.table, dialect=backend).lower()
    assert "avg(" in sql
    assert f"{'avg' if reducer == 'mean' else reducer}(" in sql
    assert "row_number" not in sql
    assert "__coverage_" not in sql
    assert len(result.checks) == 1
    assert result.checks[0][1] == "non-NA internal samples"
    assert "key.1" not in result.table.columns
    _assert_no_true_filters(sql, backend)
    assert "2026-10-01" in sql and "2026-10-02" in sql


@pytest.mark.parametrize(
    "marker", [None, "__sample_scope_1", "__sample_present", "__numeric_present"]
)
def test_sample_presence_filters_rows_without_discarding_present_nulls(marker: str | None) -> None:
    from marivo._definition.expressions import RowField, SourceAggregate
    from marivo._definition.types import ExactRef, SumAll
    from marivo.analysis.compiler.observation import FormulaInput, FormulaProgram
    from marivo.analysis.compiler.sample_reductions import SampleLowerer
    from marivo.analysis.core.data_types import DataColumn, ScalarDataType
    from marivo.refs import ref

    table = ibis.memtable(
        {"key.0": [1, 2, 3, 4], "value": [10.0, 1000.0, 2000.0, None]},
        schema={"key.0": "int64", "value": "float64"},
    )
    if marker is not None:
        table = ibis.memtable(
            {
                "key.0": [1, 2, 3, 4],
                "value": [10.0, 1000.0, 2000.0, None],
                marker: [True, False, None, True],
            },
            schema={"key.0": "int64", "value": "float64", marker: "boolean"},
        )
    field = RowField(
        ExactRef(ref.measure("sales.facts.amount"), "v1"),
        ExactRef(ref.entity("sales.facts"), "v1"),
        None,
        SumAll(),
    )
    node = SourceAggregate(field, "mean", None)
    binding = FormulaInput(
        (0,),
        "source",
        DataColumn("value", "value", ScalarDataType("float64"), True),
        False,
        keys=("key.0",),
        key_groups=(("key.0",),),
    )

    def unavailable(*args: object) -> ir.Table:
        raise AssertionError("observed samples must not synthesize a domain")

    lowerer = SampleLowerer(
        FormulaProgram((("v", node),), (), (binding,)),
        {"source": table},
        ("key.0",),
        "duckdb",
        unavailable,
        unavailable,
    )
    result = lowerer.lower(node, (0,), ("key.0",))
    backend = ibis.duckdb.connect()
    try:
        domain = backend.execute(lowerer.domain(("key.0",), (0,)).order_by("key.0"))
        rows = backend.execute(result.table.order_by("key.0"))
    finally:
        backend.disconnect()
    expected = [1, 2, 3, 4] if marker is None else [1, 4]
    assert domain["key.0"].tolist() == expected
    assert rows["key.0"].tolist() == expected
    assert rows[result.value].iloc[0] == 10.0
    assert pd.isna(rows[result.value].iloc[-1])
    assert rows["__sample_present"].tolist() == [True] * len(expected)


@contextmanager
def mean_session(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    *,
    values: tuple[float | None, ...] = (0, 100, 100, 100, 0),
    cadence: bool = True,
    amount_type: Literal["BIGINT", "DOUBLE", "DECIMAL(18,2)"] = "DOUBLE",
    aware: bool = False,
    sample_count: int | None = None,
    alternate_clock: bool = False,
    nullable_amount: bool = True,
    sample_points: tuple[str, ...] | None = None,
    sample_groups: int = 1,
) -> Iterator[tuple[Session, Case]]:
    times: tuple[str, ...] = ("00:00:00", "00:30:00", "00:30:00", "00:30:00", "01:00:00")
    if sample_count is not None:
        times = tuple(
            (datetime(2026, 10, 1) + timedelta(seconds=i)).strftime("%H:%M:%S")
            for i in range(sample_count)
        )
        values = tuple(float(i % 11) for i in range(sample_count))
    timestamps = sample_points or tuple("2026-10-01 " + point for point in times)
    rows: list[dict[str, object]] = [
        {
            "id": group * len(values) + i,
            "point": datetime.fromisoformat(timestamps[i]).replace(tzinfo=timezone.utc)
            if aware
            else timestamps[i],
            "category": chr(65 + group),
            "amount": Decimal(str(value))
            if amount_type == "DECIMAL(18,2)" and value is not None
            else value,
        }
        for group in range(sample_groups)
        for i, value in enumerate(values)
    ]
    literals = [
        "NULL"
        if value is None
        else "CAST('Infinity' AS DOUBLE)"
        if value == float("inf")
        else str(value)
        for value in values
    ]
    point_type = "TIMESTAMP WITH TIME ZONE" if aware else "VARCHAR(32)"
    points = [f"TIMESTAMP '{point} UTC'" if aware else f"'{point}'" for point in timestamps]
    physical_amount = (
        "DOUBLE PRECISION" if backend == "postgres" and amount_type == "DOUBLE" else amount_type
    )
    data = SourceData(
        f"id BIGINT, point {point_type}, category VARCHAR(8), amount {physical_amount}",
        ",".join(
            f"({group * len(values) + i},{points[i]},'{chr(65 + group)}',{literal})"
            for group in range(sample_groups)
            for i, literal in enumerate(literals)
        ),
        "id Int64, point "
        + ("DateTime64(6, 'UTC')" if aware else "String")
        + ", category String, amount "
        + ("Nullable(" if nullable_amount else "")
        + {"BIGINT": "Int64", "DOUBLE": "Float64", "DECIMAL(18,2)": "Decimal(18, 2)"}[amount_type]
        + (")" if nullable_amount else ""),
        rows,
    )
    monkeypatch.chdir(root)
    with source_case(backend, profile, root, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "point=ms.time_dimension_column(name='point',entity=facts,column='point'"
                + ("" if aware else ",parse=ms.strptime('%Y-%m-%d %H:%M:%S')")
                + (",sample_interval=(1,'hour')" if cadence else "")
                + ")\n"
                + (
                    "other_point=ms.time_dimension_column(name='other_point',entity=facts,column='point',parse=ms.strptime('%Y-%m-%d %H:%M:%S'),sample_interval=(1,'hour'))\n"
                    if alternate_clock
                    else ""
                )
                + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_except(point),status_time_dimension=point)\n"
                + "@ms.metric()\ndef average():\n    return amount.mean(time=point).per(point).mean()\n",
            }
        )
        yield mv.session.get_or_create("sampled-mean", timezone="UTC"), case


@pytest.mark.runtime
def test_clickhouse_set_index_sample_mean_omits_true_filters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from tests.datasource.environment import clickhouse_analysis as ch

    with mean_session(
        "clickhouse",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        aware=True,
        cadence=False,
    ) as (session, case):
        assert isinstance(case.source, TableSourceIR)
        table = f"qualification.{case.source.table}"
        with ch.connection(admin=True) as admin:
            admin.command(
                f"ALTER TABLE {table} ADD INDEX sample_clock point TYPE set(0) GRANULARITY 1"
            )
            admin.command(
                f"ALTER TABLE {table} MATERIALIZE INDEX sample_clock SETTINGS mutations_sync=1"
            )
        assert case.session._backend.raw_sql(
            "SELECT getSetting('readonly'), getSetting('use_skip_indexes')"
        ).result_rows == [(1, 1)]
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        rows = (
            session.observe({"v": amount.mean(time=point).per(point).mean()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        # Completed timestamp means are [0, 100, 0], with equal sample weights.
        assert rows["v"].tolist() == pytest.approx([100 / 3])
        assert trace.native_sql
        for sql in trace.native_sql:
            _assert_no_true_filters(sql, "clickhouse")
            assert "2026-10-01" in sql
        assert trace.returned_rows == [1]


@pytest.mark.parametrize(
    "backend,profile,amount_type,aware",
    source_params(
        [
            ("duckdb", "table", "DECIMAL(18,2)", True),
            ("duckdb", "table", "DECIMAL(18,2)", False),
            ("trino", "iceberg", "DOUBLE", False),
        ]
    ),
)
def test_exact_quantiles_with_cadence_finish_before_sample_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    amount_type: Literal["DOUBLE", "DECIMAL(18,2)"],
    aware: bool,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 0, 10, None, None),
        amount_type=amount_type,
        aware=aware,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        quantile = amount.quantile(0.95, time=point)
        median = amount.median(time=point)
        trace = capture_source(monkeypatch)
        if backend == "trino":
            from marivo.analysis.errors import AnalysisStageUnavailableError

            with pytest.raises(AnalysisStageUnavailableError, match="trino: quantile"):
                session.observe({"q": quantile.per(point).mean()}, by=(scope, category)).compute()
            assert trace.returned_rows == []
            return
        rows = (
            session.observe(
                {"q": quantile.per(point).mean(), "median": median.per(point).mean()},
                by=(scope, category),
            )
            .compute()
            .to_pandas()
        )
        # Completed samples: q=[0, 9.5, NULL], median=[0, 5, NULL].
        # The off-grid 00:30 sample participates; the present-NULL 01:00 does not.
        for name, expected in (("q", "4.75"), ("median", "2.50")):
            actual = rows[name].tolist()
            assert actual == [Decimal(expected) if amount_type != "DOUBLE" else float(expected)]
            if amount_type != "DOUBLE":
                assert isinstance(actual[0], Decimal) and actual[0].as_tuple().exponent == -2
        assert all("__quantile_count" not in columns for columns in trace.returned_columns)
        assert trace.returned_rows == [1, 1]
        for metric in (
            amount.quantile(0.95, time=point, nulls="error").per(point).mean(),
            median.per(point).mean(nulls="error"),
        ):
            with pytest.raises(MaterializationError, match="non-NA"):
                session.observe({"v": metric}, by=(scope,)).compute()
        missing = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
        rows = (
            session.observe({"v": quantile.per(point).mean()}, by=(missing,)).compute().to_pandas()
        )
        assert rows["v"].tolist() == [Decimal("4.75") if amount_type != "DOUBLE" else 4.75]
        empty = (
            session.observe({"v": quantile.where(amount < 0).per(point).mean()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert pd.isna(empty["v"].iloc[0])


@pytest.mark.parametrize(
    "backend,profile,aware",
    source_params(
        [
            ("duckdb", "table", False),
            ("postgres", "table", False),
            ("mysql", "table", False),
            ("trino", "iceberg", False),
            ("clickhouse", "table", False),
            ("clickhouse", "table", True),
        ]
    ),
)
def test_native_mean_preserves_sample_weighting_and_fixed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    aware: bool,
) -> None:
    with mean_session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, aware=aware
    ) as (
        session,
        _,
    ):
        metric = session.catalog.get("metric:sales.average")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        result = session.observe({"v": metric}, by=(scope, category)).compute()
        assert result.to_pandas()["v"].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows == [1]
        mixed = session.observe(
            {
                "v": amount.mean(time=point).where(amount >= 0).per(point).mean(),
                "max": amount.max(time=point),
            },
            by=(scope,),
        ).compute()
        assert mixed.to_pandas()["v"].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows[-2:] == [1, 1]
        with monkeypatch.context() as legacy:
            legacy.setattr(
                sample_fusion, "fuse_sample_reductions", lambda prepared, lowered: lowered
            )
            old = session.observe({"v": metric}, by=(scope, category)).compute().to_pandas()
        assert old["v"].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows[-1] == 3
        assert not trace.uploaded_key_rows
        stored = result.persist()

        def offline(*args: object, **kwargs: object) -> None:
            raise AssertionError("fixed result must not read its source")

        monkeypatch.setattr("marivo.datasource.adapters.SourceSession.execute", offline)
        assert stored.to_pandas()["v"].tolist() == pytest.approx([100 / 3])


@pytest.mark.parametrize(
    "values,cadence,expected",
    [
        ((None, None, None, None, None), True, None),
        ((0, None, 100, 100, 0), True, 100 / 3),
        ((0, 100, 100, 100), True, 50),
        ((0, 100, 100, 100), False, 50),
        ((1e308, 1e308, None, None, 1e308), True, float("inf")),
        ((1e308, -1e308, None, None, 1), True, 1 / 3),
        ((float("inf"), 100, 100, 100, 0), True, float("inf")),
    ],
)
def test_native_mean_keeps_backend_null_and_large_value_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    values: tuple[float | None, ...],
    cadence: bool,
    expected: float | None,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=values,
        cadence=cadence,
    ) as (session, _):
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        query = session.observe({"v": session.catalog.get("metric:sales.average")}, by=(scope,))
        trace = capture_source(monkeypatch)
        value = query.compute().to_pandas()["v"].iloc[0]
        assert pd.isna(value) if expected is None else value == pytest.approx(expected)
        assert trace.returned_rows == [1]
        assert not trace.uploaded_key_rows


def _driver_rows(backend: str, case: Case, sql: str) -> tuple[tuple[object, ...], ...]:
    if backend == "postgres":
        from tests.datasource.environment.postgres_analysis import connection

        with connection() as pg_reader, pg_reader.cursor() as pg_cursor:
            pg_cursor.execute(sql)
            return tuple(pg_cursor.fetchall())
    if backend == "mysql":
        from tests.datasource.environment.mysql_analysis import connection as mysql_connection

        with mysql_connection() as mysql_reader, mysql_reader.cursor() as mysql_cursor:
            mysql_cursor.execute(sql)
            return tuple(mysql_cursor.fetchall())
    if backend == "trino":
        from tests.datasource.environment.trino_analysis import connection as trino_connection

        with trino_connection() as trino_reader:
            trino_cursor = trino_reader.cursor()
            try:
                trino_cursor.execute(sql)
                return tuple(tuple(row) for row in trino_cursor.fetchall())
            finally:
                trino_cursor.close()
    if backend == "clickhouse":
        from tests.datasource.environment.clickhouse_analysis import connection as ch_connection

        with ch_connection() as ch_reader:
            return tuple(tuple(row) for row in ch_reader.query(sql).result_rows)
    # Reuse the fixture's DuckDB driver connection and its exact configuration.
    return tuple(case.session._backend.raw_sql(sql).fetchall())


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_native_mean_uses_backend_large_value_arithmetic_without_local_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(1e308, 1e308, None, None, 1e308),
    ) as (session, case):
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        query = session.observe({"v": session.catalog.get("metric:sales.average")}, by=(scope,))
        trace = capture_source(monkeypatch)
        actual: pd.DataFrame | None = None
        failure: MaterializationError | None = None
        try:
            actual = query.compute().to_pandas()
        except MaterializationError as error:
            failure = error
        assert len(trace.native_sql) == 1
        # Overflow is backend-owned. Compare the actual issued SQL through the
        # provider driver to verify transport/error authority; algebraically
        # equivalent SQL can have different native overflow behavior.
        try:
            native = _driver_rows(backend, case, trace.native_sql[0])
        except Exception as error:
            assert failure is not None
            assert failure.stage in ("source_execution", "source_conversion")
            assert failure.received is not None
            assert type(error).__name__ in failure.received
            assert not trace.returned_rows
        else:
            assert failure is None
            assert actual is not None
            index = next(
                i for i, name in enumerate(trace.returned_columns[0]) if name.startswith("sample:")
            )
            expected = native[0][index]
            value = actual["v"].iloc[0]
            if expected is None:
                assert pd.isna(value)
            elif isinstance(expected, float) and isnan(expected):
                assert isnan(value)
            else:
                assert value == expected
            assert trace.returned_rows == [1]
        assert not trace.uploaded_key_rows
        assert len(trace.native_sql) == 1
        assert "__mean_safe" not in trace.native_sql[0]


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
@pytest.mark.parametrize("amount_type", ["BIGINT", "DECIMAL(18,2)"])
def test_native_mean_finishes_integer_and_decimal_samples_in_one_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    amount_type: Literal["BIGINT", "DECIMAL(18,2)"],
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        amount_type=amount_type,
    ) as (session, case):
        source = case.session.bind(case.source, source_identity="numeric-oracle").relation
        assert (
            source.amount.type().is_integer()
            if amount_type == "BIGINT"
            else source.amount.type().is_decimal()
        )
        inner = source.amount.mean()
        if source.amount.type().is_integer() or inner.type().is_decimal():
            inner = ops.Cast(inner, to=inner.type()).to_expr()
        means = source.group_by("point").aggregate(sample=inner)
        outer = means["sample"].mean()
        if outer.type().is_decimal():
            outer = ops.Cast(outer, to=outer.type()).to_expr()
        expected = means.aggregate(value=outer).execute()["value"].tolist()
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        direct = session.observe({"v": amount.mean(time=point)}, by=(scope,)).compute().to_pandas()
        # The standalone AVG shares the declared carrier boundary, including
        # PostgreSQL's native numeric AVG over bigint before the output cast.
        assert direct["v"].tolist() == [
            Decimal("60.00") if amount_type == "DECIMAL(18,2)" else 60.0
        ]
        rows = (
            session.observe({"v": session.catalog.get("metric:sales.average")}, by=(scope,))
            .compute()
            .to_pandas()
        )
        if amount_type == "DECIMAL(18,2)":
            assert rows["v"].tolist() == expected
        else:
            assert rows["v"].tolist() == pytest.approx(expected)
        assert trace.returned_rows == [1, 1]
        assert not trace.uploaded_key_rows
        assert "ROW_NUMBER" not in trace.native_sql[0]
        assert "__coverage_" not in trace.native_sql[0]


@pytest.mark.parametrize("reducer,expected", [("min", 0.0), ("max", 80.0)])
@pytest.mark.parametrize(
    "backend,profile,aware",
    source_params(
        [
            ("duckdb", "table", False),
            ("postgres", "table", False),
            ("mysql", "table", False),
            ("trino", "iceberg", False),
            ("clickhouse", "table", False),
            ("clickhouse", "table", True),
        ]
    ),
)
def test_native_extrema_reduce_sparse_completed_means_with_null_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    reducer: str,
    expected: float,
    backend: str,
    profile: str,
    aware: bool,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 40, 100, 100, 20),
        aware=aware,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        samples = amount.mean(time=point).per(point)
        metric = samples.min() if reducer == "min" else samples.max()
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        rows = session.observe({"v": metric}, by=(scope, category)).compute().to_pandas()
        # Three completed timestamp means are [0, 80, 20], despite a raw maximum of 100.
        assert rows["v"].tolist() == [expected]
        assert trace.returned_rows == [1]
        assert "sample_check.null" not in trace.returned_columns[0]
        missing = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
        rows = session.observe({"v": metric}, by=(missing, category)).compute().to_pandas()
        assert rows["v"].tolist() == [expected]
        assert trace.returned_rows == [1, 1]


@pytest.mark.runtime
@pytest.mark.parametrize("reducer,expected", [("min", 0.0), ("max", 20.0)])
def test_clickhouse_native_nonnull_sample_clock_preserves_extrema_policies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    reducer: str,
    expected: float,
) -> None:
    with mean_session(
        "clickhouse",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, None, None, 20),
        aware=True,
        cadence=False,
    ) as (session, case):
        native = case.session.bind(case.source, source_identity="native-clock").relation
        assert not native.point.type().nullable
        assert native.point.type().timezone == "UTC"
        assert native.point.type().scale == 6
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        samples = amount.mean(time=point).per(point)
        metric = samples.min() if reducer == "min" else samples.max()
        trace = capture_source(monkeypatch)
        rows = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
        # The native timestamp means are [0, NULL, 20].
        assert rows["v"].tolist() == [expected]
        strict = samples.min(nulls="error") if reducer == "min" else samples.max(nulls="error")
        with pytest.raises(MaterializationError, match="non-NA internal samples"):
            session.observe({"v": strict}, by=(scope,)).compute()
        empty = amount.mean(time=point).where(amount < 0).per(point)
        metric = empty.min() if reducer == "min" else empty.max()
        rows = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
        assert rows["v"].isna().all() and len(rows) == 1
        # Empty grouped input returns no source row; the fixed scope supplies
        # the one NA output cell.
        assert trace.returned_rows == [1, 1, 0]
        assert not trace.uploaded_key_rows


@pytest.mark.parametrize(
    "values,strict,error,expected",
    [
        ((None, None, None, None, None), False, None, None),
        ((0, None, None, None, 20), True, "non-NA", None),
        ((float("inf"), 40, 100, 100, 20), False, None, float("inf")),
        ((0, None, None, None, 20), False, None, 20.0),
    ],
)
def test_native_extrema_preserve_null_policy_and_backend_floats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    values: tuple[float | None, ...],
    strict: bool,
    error: str | None,
    expected: float | None,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=values,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        metric = amount.mean(time=point).per(point).max(nulls="error" if strict else "ignore")
        trace = capture_source(monkeypatch)
        query = session.observe({"v": metric}, by=(scope,))
        if error:
            with pytest.raises(MaterializationError, match=error):
                query.compute()
        else:
            value = query.compute().to_pandas()["v"].iloc[0]
            assert pd.isna(value) if expected is None else value == expected
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("reducer,expected", [("min", 20.0), ("max", 100.0)])
@pytest.mark.parametrize("grouped", [False, True])
def test_sample_extrema_without_cadence_exclude_filtered_away_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    reducer: str,
    expected: float,
    grouped: bool,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, 100, 100, 20),
        cadence=False,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        for nulls in ("ignore", "error"):
            selected = amount.mean(time=point).where(amount > 0).per(point)
            metric = selected.min(nulls=nulls) if reducer == "min" else selected.max(nulls=nulls)
            rows = (
                session.observe({"v": metric}, by=(scope, category) if grouped else (scope,))
                .compute()
                .to_pandas()
            )
            # The selected timestamp means are [100, 20]; the zero group is absent.
            assert rows["v"].tolist() == [expected]
            empty = amount.mean(time=point).where(amount < 0).per(point)
            metric = empty.min(nulls=nulls) if reducer == "min" else empty.max(nulls=nulls)
            rows = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
            assert rows["v"].isna().all() and len(rows) == 1
        invalid = amount.mean(time=point).where(amount * 1e308 < 0).per(point)
        metric = invalid.min() if reducer == "min" else invalid.max()
        rows = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
        assert rows["v"].isna().all() and len(rows) == 1


@pytest.mark.parametrize("reducer", ["min", "max"])
def test_sample_extrema_without_cadence_keep_present_null_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    reducer: str,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, None, None, 20),
        cadence=False,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        samples = amount.mean(time=point).where(category == "A").per(point)
        metric = samples.min(nulls="error") if reducer == "min" else samples.max(nulls="error")
        with pytest.raises(MaterializationError, match="non-NA internal samples"):
            session.observe({"v": metric}, by=(scope,)).compute()


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_native_sample_extrema_strict_filter_uses_selected_sample_presence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, 100, 100, 20),
        cadence=False,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        metric = amount.mean(time=point).where(amount > 0).per(point).max(nulls="error")
        trace = capture_source(monkeypatch)
        rows = session.observe({"v": metric}, by=(scope,)).compute().to_pandas()
        assert rows["v"].tolist() == [100.0]
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("sample_count", [100, 10_000])
def test_native_sample_extrema_return_final_rows_at_scale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    sample_count: int,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        sample_count=sample_count,
    ) as (session, case):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(
            start="2026-10-01",
            end=(datetime(2026, 10, 1) + timedelta(seconds=sample_count)).isoformat(),
        )
        samples = amount.mean(time=point).per(point)
        trace = capture_source(monkeypatch, measure_cost=True)
        result = session.observe(
            {"min": samples.min(), "max": samples.max()}, by=(scope,)
        ).compute()
        rows = result.to_pandas()
        assert rows["min"].tolist() == [0.0]
        assert rows["max"].tolist() == [10.0]
        assert trace.returned_rows == [1]
        assert not trace.uploaded_key_rows
        trace.save(
            f"samples-extrema-{sample_count}",
            case.environment,
            {"samples": sample_count},
            {"min": 0.0, "max": 10.0},
            result,
            (case.session,),
        )


@pytest.mark.runtime
def test_trino_four_day_mean_transport_and_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    import json
    import os
    from time import perf_counter

    from tests.datasource.environment.trino_analysis import connection

    if os.environ.get("MARIVO_TRINO_ANALYSIS_TEST") != "1":
        pytest.skip("Enable the ready Colima Trino Runtime service")
    with mean_session("trino", "iceberg", tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        case,
    ):
        assert isinstance(case.source, TableSourceIR)
        with connection(admin=True) as admin:
            cursor = admin.cursor()
            try:
                target = f'iceberg.analysis."{case.source.table}"'
                cursor.execute(f"DELETE FROM {target}").fetchall()
                cursor.execute(
                    f"INSERT INTO {target} SELECT CAST(d * 86400 + h * 3600 + s AS BIGINT), "
                    "format_datetime(date_add('second', d * 86400 + h * 3600 + s, TIMESTAMP '2026-10-01 00:00:00'), 'yyyy-MM-dd HH:mm:ss'), "
                    "'A', CAST(mod(s,100) AS DOUBLE) FROM UNNEST(sequence(0,3)) AS days(d) "
                    "CROSS JOIN UNNEST(sequence(0,23)) AS hours(h) CROSS JOIN UNNEST(sequence(0,3599)) AS seconds(s)"
                ).fetchall()
            finally:
                cursor.close()
        trace = capture_source(monkeypatch, measure_cost=True)
        metric = session.catalog.get("metric:sales.average")
        scope = session.time_scope(start="2026-10-01", end="2026-10-05")
        measurements: list[dict[str, object]] = []
        # Toggle native folding without changing the captured sample policy.
        # Local means transport represented samples only, never coverage rows.
        for route, days in (("legacy", 4), ("compact", 4), ("legacy", 1), ("compact", 1)):
            scope = session.time_scope(start="2026-10-01", end=f"2026-10-{1 + days:02}")
            with monkeypatch.context() as config:
                if route == "legacy":
                    config.setattr(
                        sample_fusion, "fuse_sample_reductions", lambda prepared, lowered: lowered
                    )
                started = perf_counter()
                frame = session.observe({"v": metric}, by=(scope,)).compute()
                rows = frame.to_pandas()
                elapsed = perf_counter() - started
            # 86400 is divisible by 100, so each day averages 0..99 equally.
            assert rows["v"].tolist() == [49.5]
            assert trace.returned_rows[-1] == (1 if route == "compact" else days * 86400)
            measurements.append(
                {
                    "route": route,
                    "days": days,
                    "seconds": elapsed,
                    "source_seconds": trace.source_seconds[-1],
                    "returned_rows": trace.returned_rows[-1],
                    "returned_bytes": trace.returned_arrow_bytes[-1],
                }
            )
        directory = os.environ.get("MARIVO_MEAN_EVIDENCE_DIR")
        if directory:
            output = Path(directory)
            output.mkdir(parents=True, exist_ok=True)
            (output / "trino-mean-cost.json").write_text(json.dumps(measurements, indent=2))
            for index, sql in enumerate(trace.native_sql):
                (output / f"trino-mean-{index}.sql").write_text(sql)
            with connection() as reader:
                cursor = reader.cursor()
                try:
                    plan = cursor.execute(
                        "EXPLAIN (TYPE DISTRIBUTED) " + trace.native_sql[1]
                    ).fetchone()[0]
                    (output / "trino-compact-plan.txt").write_text(plan)
                finally:
                    cursor.close()


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_native_mean_checks_nulls_without_requiring_clock_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, None, None, 0),
    ) as (session, _):
        trace = capture_source(monkeypatch)
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        for metric in (
            amount.mean(time=point).per(point).mean(nulls="error"),
            amount.mean(time=point, nulls="error").per(point).mean(),
        ):
            query = session.observe({"v": metric}, by=(scope,))
            for selected in (query, query.top(0, by=query["v"])):
                with pytest.raises(MaterializationError, match="non-NA"):
                    selected.compute()
        empty = (
            session.observe(
                {"v": amount.mean(time=point).where(amount < 0).per(point).mean()}, by=(scope,)
            )
            .compute()
            .to_pandas()
        )
        assert pd.isna(empty["v"].iloc[0])
        clipped = session.time_scope(start="2026-10-01T00:15:00", end="2026-10-01T01:15:00")
        rows = (
            session.observe({"v": amount.mean(time=point).per(point).mean()}, by=(clipped,))
            .compute()
            .to_pandas()
        )
        assert rows["v"].tolist() == [0.0]
        missing = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
        rows = (
            session.observe({"v": amount.mean(time=point).per(point).mean()}, by=(missing,))
            .compute()
            .to_pandas()
        )
        assert rows["v"].tolist() == [0.0]
        unbounded = (
            session.observe({"v": amount.mean(time=point).per(point).mean()}, by=(category,))
            .compute()
            .to_pandas()
        )
        assert unbounded["v"].tolist() == [0.0]
        assert trace.returned_rows == [1, 1, 1, 1, 0, 1, 1, 1]
        assert not trace.uploaded_key_rows
        assert all("__coverage_" not in sql and "ROW_NUMBER" not in sql for sql in trace.native_sql)


@pytest.mark.parametrize("shape", ["direct", "add_zero", "multiply_one"])
def test_sample_mean_strict_domain_is_independent_of_child_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    shape: str,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(10, None, None, None, 30),
    ) as (session, _):
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        child = amount.mean(time=point)
        child = (
            child + 0 if shape == "add_zero" else child * 1 if shape == "multiply_one" else child
        )
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        samples = child.per(point)
        trace = capture_source(monkeypatch)
        ignored = session.observe({"v": samples.mean()}, by=(scope,)).compute().to_pandas()
        assert ignored["v"].tolist() == [20.0]
        assert all("sample_check.null" not in names for names in trace.returned_columns)
        with pytest.raises(MaterializationError, match="non-NA") as captured:
            session.observe({"v": samples.mean(nulls="error")}, by=(scope,)).compute()
        assert captured.value.expected == "non-NA internal samples"
        assert "mean(nulls='error')" in (captured.value.received or "")
        assert not trace.uploaded_key_rows


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "table")])
)
@pytest.mark.parametrize("nullable", [False, True])
def test_native_mean_strict_empty_domain_returns_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    nullable: bool,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 10, 10, 10, 20),
        nullable_amount=nullable,
    ) as (session, case):
        source = case.session.bind(case.source, source_identity="empty-native-mean").relation
        if backend == "clickhouse":
            assert source.amount.type().nullable == nullable
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        child = amount.mean(time=point, nulls="error").where(amount < 0)
        trace = capture_source(monkeypatch)
        for metric in (child, child.per(point).mean(nulls="error")):
            frame = session.observe({"v": metric}).compute().to_pandas()
            assert frame["v"].isna().tolist() == [True]
        assert trace.returned_rows == [1, 1]
        assert len(trace.native_sql) == 2 and not trace.uploaded_key_rows


def test_native_mean_strict_does_not_demand_filtered_away_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, None, 100, 100, 20),
        cadence=False,
    ) as (session, _):
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        child = amount.mean(time=point, nulls="error").where(amount > 0)
        rows = session.observe({"v": child.per(point).mean(nulls="error")}).compute().to_pandas()
        assert rows["v"].tolist() == [60.0]


@pytest.mark.parametrize(
    "excluded,bounded", [("wrong_clock", True), ("wrong_clock", False), ("population", True)]
)
def test_local_sample_mean_uses_observed_samples_for_excluded_native_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    excluded: str,
    bounded: bool,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 100, 100, 100),
        alternate_clock=excluded == "wrong_clock",
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        sample = (
            catalog_value(session.catalog, "sales.facts.other_point", kind="field")
            if excluded == "wrong_clock"
            else point
        )
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        by = (
            mv.product(scope, session.members(session.catalog.get("entity:sales.facts")))
            if excluded == "population"
            else (scope,)
            if bounded
            else ()
        )
        assert isinstance(by, (mv.Population, tuple))
        trace = capture_source(monkeypatch)
        result = (
            session.observe({"v": amount.mean(time=point).per(sample).mean()}, by=by)
            .compute()
            .to_pandas()
        )
        assert sorted(result["v"].tolist()) == (
            [0.0, 100.0, 100.0, 100.0] if excluded == "population" else [50.0]
        )
        assert trace.returned_rows


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_time_means_share_observed_sample_policy_across_formulas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, values=(10, 20, 20, 20)
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
        native = amount.mean(time=point).per(point).mean()
        for by in ((scope, category), (category,)):
            frame = (
                session.observe(
                    {
                        "native": native,
                        "wrapped": native + 0,
                        "sum_mean": amount.sum(time=point).per(point).mean(),
                        "weighted": amount.mean(time=point)
                        .per(point)
                        .weighted_mean(weight=amount.sum(time=point)),
                    },
                    by=by,
                )
                .compute()
                .to_pandas()
            )
            assert frame["native"].tolist() == [15.0]
            assert frame["wrapped"].tolist() == [15.0]
            assert frame["sum_mean"].tolist() == [35.0]
            assert frame["weighted"].tolist() == pytest.approx([1300 / 70])
            quantile = amount.quantile(0.95, time=point).per(point).mean()
            if backend in {"mysql", "trino"}:
                from marivo.analysis.errors import AnalysisStageUnavailableError

                with pytest.raises(AnalysisStageUnavailableError, match=backend + ": quantile"):
                    session.observe({"quantile_mean": quantile}, by=by).compute()
            else:
                rows = session.observe({"quantile_mean": quantile}, by=by).compute().to_pandas()
                assert rows["quantile_mean"].tolist() == [15.0]


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
@pytest.mark.parametrize("native", [False, True])
def test_sparse_time_samples_preserve_nulls_and_native_extrema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    native: bool,
) -> None:
    with mean_session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, values=(None, 20, 20, 20)
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T05:00:00")
        samples = amount.mean(time=point).per(point)
        metric = samples.max() if native else samples.quantile(0.95)
        trace = capture_source(monkeypatch)
        rows = session.observe({"latency": metric}, by=(scope, category)).compute().to_pandas()
        assert rows["latency"].tolist() == [20.0]
        assert trace.returned_rows == ([2] if not native and backend in {"mysql", "trino"} else [1])
        assert not trace.uploaded_key_rows


@pytest.mark.parametrize(
    "backend,profile,aware",
    source_params(
        [
            ("duckdb", "table", False),
            ("duckdb", "table", True),
            ("postgres", "table", True),
            ("mysql", "table", False),
            ("trino", "iceberg", True),
            ("clickhouse", "table", True),
        ]
    ),
)
@pytest.mark.parametrize("native", [False, True])
def test_sparse_samples_exclude_filtered_clocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    aware: bool,
    native: bool,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(10, 20, 20, 20, 30),
        aware=aware,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
        samples = amount.mean(time=point).where(amount > 10).per(point)
        trace = capture_source(monkeypatch)
        rows = (
            session.observe(
                {"filtered": samples.max() if native else samples.quantile(0.95)}, by=(scope,)
            )
            .compute()
            .to_pandas()
        )
        assert rows["filtered"].tolist() == [30.0 if native else 29.5]
        assert trace.returned_rows == ([2] if not native and backend in {"mysql", "trino"} else [1])
        assert not trace.uploaded_key_rows


def test_time_mean_keeps_explicit_sample_domain_and_existing_null_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(10, 20, 20, 20),
        aware=True,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        samples = mv.coordinates(
            by=(point,),
            keys=tuple((datetime(2026, 10, 1, hour, tzinfo=timezone.utc),) for hour in (0, 1)),
        )
        # A declared domain still includes its absent 01:00 sample: sum -> 0,
        # mean -> NULL. The separate field sample at 00:30 is not selected.
        frame = (
            session.observe(
                {
                    "sum_samples": amount.sum(time=point).per(samples).mean(),
                    "mean_samples": amount.mean(time=point).per(samples).mean(),
                    "min_sum": amount.sum(time=point).per(samples).min(),
                    "p95_sum": amount.sum(time=point).per(samples).quantile(0.95),
                }
            )
            .compute()
            .to_pandas()
        )
        assert frame["sum_samples"].tolist() == [5.0]
        assert frame["mean_samples"].tolist() == [10.0]
        assert frame["min_sum"].tolist() == [0.0]
        assert frame["p95_sum"].tolist() == [9.5]
        with pytest.raises(MaterializationError, match="non-NA"):
            session.observe(
                {"strict": amount.mean(time=point).per(samples).mean(nulls="error")}
            ).compute()
        observed = (
            session.observe({"strict": amount.sum(time=point).per(point).mean(nulls="error")})
            .compute()
            .to_pandas()
        )
        assert observed["strict"].tolist() == [35.0]


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "table")])
)
@pytest.mark.parametrize("grain", ["day", "hour"])
def test_calendar_sample_folds_keep_joint_keys_without_cross_bucket_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    grain: str,
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 10, 10, 100, 300, None),
        sample_points=(
            "2026-10-01 23:00:00",
            "2026-10-01 23:30:00",
            "2026-10-01 23:30:00",
            "2026-10-02 00:00:00",
            "2026-10-02 00:30:00",
            "2026-10-02 00:45:00",
        ),
        cadence=False,
        aware=True,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        samples = amount.sum(time=point).per(point)
        mean, p95 = samples.mean(), samples.quantile(0.95)
        reducer = Mock(wraps=_sample_numeric.reduce_samples)
        monkeypatch.setattr(_sample_numeric, "reduce_samples", reducer)
        for days in (1, 2, 3):
            calendar = session.time_scope(start="2026-10-01", end=f"2026-10-0{days + 1}").by(
                mv.grain(grain)
            )
            reducer.reset_mock()
            rows = (
                session.observe(
                    {"mean": mean, "p95": p95, "ratio": p95 / mean, "count": samples.count()},
                    by=(calendar, category) if grain == "day" else (category, calendar),
                )
                .compute()
                .to_pandas()
            )
            # Spatial sums finish first: [0, 20] and [100, 300, NULL].
            present = rows.loc[rows["mean"].notna()]
            expected = 1 if days == 1 else 2
            assert present["mean"].tolist() == [10.0, 200.0][:expected]
            assert present["p95"].tolist() == pytest.approx([19.0, 290.0][:expected])
            assert present["ratio"].tolist() == pytest.approx([1.9, 1.45][:expected])
            assert present["count"].tolist() == [2] * expected
            absent = rows.loc[rows["mean"].isna()]
            assert absent[["p95", "ratio"]].isna().all().all()
            assert absent["count"].tolist() == [0] * len(absent)
            assert len(rows) == days * (24 if grain == "hour" else 1)
            assert not reducer.call_args_list


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "table")])
)
def test_calendar_sample_domains_keep_sparse_samples_explicit_keys_and_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 20, 100, 300),
        sample_points=(
            "2026-10-01 23:00:00",
            "2026-10-01 23:30:00",
            "2026-10-02 00:00:00",
            "2026-10-02 00:30:00",
        ),
        aware=True,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        calendar = session.time_scope(start="2026-10-01T23:00:00", end="2026-10-02T01:00:00").by(
            mv.grain("day")
        )
        total = amount.sum(time=point)
        rows = (
            session.observe({"p95": total.per(point).quantile(0.95)}, by=(calendar, category))
            .compute()
            .to_pandas()
        )
        assert rows["p95"].tolist() == pytest.approx([19.0, 290.0])
        filtered = (
            session.observe(
                {"p95": total.where(amount > 0).per(point).quantile(0.95)},
                by=(calendar, category),
            )
            .compute()
            .to_pandas()
        )
        assert filtered["p95"].tolist() == pytest.approx([20.0, 290.0])
        explicit = mv.coordinates(
            by=(point,),
            keys=tuple(
                (datetime(2026, 10, day, hour, 30, tzinfo=timezone.utc),)
                for day, hour in ((1, 23), (2, 0))
            ),
        )
        rows = (
            session.observe({"mean": total.per(explicit).mean()}, by=(calendar, category))
            .compute()
            .to_pandas()
        )
        # Explicit tuples retain absent samples as sum zero in each output bucket.
        assert rows["mean"].tolist() == [10.0, 150.0]
        rows = (
            session.observe(
                {
                    "nested": total.per(point).mean().per(category).mean(),
                    "history": total.per(point).mean().window(mv.all_history()),
                },
                by=(calendar,),
            )
            .compute()
            .to_pandas()
        )
        assert rows["nested"].tolist() == [10.0, 200.0]
        assert rows["history"].tolist() == [10.0, 105.0]


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "table")])
)
@pytest.mark.parametrize("aware", [False, True])
@pytest.mark.parametrize("grain", ["day", "hour"])
def test_time_samples_keep_empty_calendar_buckets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    aware: bool,
    grain: str,
) -> None:
    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 20),
        sample_points=("2026-10-01 23:00:00", "2026-10-01 23:30:00"),
        aware=aware,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        calendar = session.time_scope(start="2026-10-01T23:00:00", end="2026-10-02T01:00:00").by(
            mv.grain(grain)
        )
        total = amount.sum(time=point)
        rows = (
            session.observe({"mean": total.per(point).mean()}, by=(category, calendar))
            .compute()
            .to_pandas()
        )
        assert rows["mean"].iloc[0] == 10.0
        assert pd.isna(rows["mean"].iloc[1])
        for nested in (False, True):
            metric = total.per(point).quantile(0.95)
            metric = metric.per(category).mean() if nested else metric
            rows = (
                session.observe({"p95": metric}, by=(calendar,) if nested else (category, calendar))
                .compute()
                .to_pandas()
            )
            assert rows["p95"].iloc[0] == pytest.approx(19.0)
            assert pd.isna(rows["p95"].iloc[1])


@pytest.mark.parametrize("cadence", [False, True])
@pytest.mark.parametrize(
    "reducer,expected",
    [
        ("sum", 100.0),
        ("mean", 50.0),
        ("min", 0.0),
        ("max", 100.0),
        ("median", 50.0),
        ("quantile", 95.0),
        ("count", 2),
        ("count_distinct", 2),
        ("approx_median", 50.0),
        ("approx_quantile", 100.0),
        ("first", 0.0),
        ("last", 100.0),
    ],
)
def test_all_time_sample_reducers_allow_missing_clocks_without_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    cadence: bool,
    reducer: str,
    expected: float,
) -> None:
    with mean_session(
        "duckdb",
        "table",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 100, 100, 100, None),
        cadence=cadence,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        samples = amount.mean(time=point).per(point)
        method = getattr(samples, reducer)
        metric = method(0.95) if reducer in ("quantile", "approx_quantile") else method()
        trace = capture_source(monkeypatch)
        rows = session.observe({"v": metric}).compute().to_pandas()
        # Complete each represented timestamp first: [0, 100, NULL]. The
        # off-grid 00:30 participates; no hourly schedule is synthesized.
        assert rows["v"].tolist() == pytest.approx([expected])
        if reducer in ("mean", "min", "max"):
            assert trace.returned_rows == [1]
        assert all(
            "__coverage_" not in sql and "__sampling_" not in sql for sql in trace.native_sql
        )
        assert not trace.uploaded_key_rows


@pytest.mark.parametrize(
    "backend,profile",
    source_params([("duckdb", "table"), ("postgres", "table"), ("clickhouse", "table")]),
)
def test_sample_summary_shares_one_native_read_and_returns_only_final_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        sample_count=7200,
        sample_groups=8,
        aware=True,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        calendar = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00").by(
            mv.grain("hour")
        )
        samples = amount.sum(time=point).per(point)
        for by, expected_means, count, size in (
            ((calendar, category), [17988 / 3600] * 8 + [17997 / 3600] * 8, 3600, 16),
            ((category,), [35985 / 7200] * 8, 7200, 8),
        ):
            with monkeypatch.context() as probe:
                trace = capture_source(probe, measure_cost=True)
                reducer = Mock(wraps=_sample_numeric.reduce_samples)
                probe.setattr(_sample_numeric, "reduce_samples", reducer)
                result = session.observe(
                    {
                        "average": samples.mean(),
                        "p50": samples.quantile(0.5),
                        "p95": samples.quantile(0.95),
                        "p99": samples.quantile(0.99),
                        "count": samples.count(),
                    },
                    by=by,
                ).compute()
                rows = result.to_pandas()
                assert len(rows) == size
                assert sorted(rows["average"].tolist()) == pytest.approx(expected_means)
                assert rows["p50"].tolist() == [5.0] * size
                assert rows["p95"].tolist() == [10.0] * size
                assert rows["p99"].tolist() == [10.0] * size
                assert rows["count"].tolist() == [count] * size
                assert trace.returned_rows == [size]
                assert not reducer.call_args_list
                sql = trace.native_sql[0].lower()
                assert sql.count("sum(") == 1
                if backend == "clickhouse":
                    assert "quantilesexactinclusive" in sql
        stored = result.persist()

        def offline(*args: object, **kwargs: object) -> None:
            raise AssertionError("captured summaries must not replay their source")

        monkeypatch.setattr(SourceSession, "execute", offline)
        assert stored.to_pandas().equals(rows)


@pytest.mark.runtime
def test_distributed_sample_sum_finishes_globally_before_exact_fold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        "clickhouse",
        "distributed",
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(0, 10, 10, 100),
        aware=True,
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        samples = amount.sum(time=point).per(point)
        trace = capture_source(monkeypatch)
        reducer = Mock(wraps=_sample_numeric.reduce_samples)
        monkeypatch.setattr(_sample_numeric, "reduce_samples", reducer)
        rows = (
            session.observe({"average": samples.mean(), "p95": samples.quantile(0.95)})
            .compute()
            .to_pandas()
        )
        # The 00:30 sample spans both shards and completes to 120 before fold.
        assert rows["average"].tolist() == [60.0]
        assert rows["p95"].tolist() == [114.0]
        assert trace.returned_rows == [1]
        assert not reducer.call_args_list


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_native_time_sample_fold_keeps_overlapping_window_occurrences(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, aware=backend != "mysql"
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        calendar = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00").by(
            mv.grain("hour")
        )
        metric = (
            amount.sum(time=point)
            .per(point)
            .mean()
            .window(mv.trailing_duration(duration=timedelta(hours=2)))
        )
        trace = capture_source(monkeypatch)
        reducer = Mock(wraps=_sample_numeric.reduce_samples)
        monkeypatch.setattr(_sample_numeric, "reduce_samples", reducer)
        rows = session.observe({"average": metric}, by=(calendar,)).compute().to_pandas()
        # Each window independently completes the SUM samples: [0, 300] and [0, 300, 0].
        assert rows["average"].tolist() == [150.0, 100.0]
        assert trace.returned_rows == [2]
        assert not reducer.call_args_list


@pytest.mark.runtime
def test_postgres_sample_domain_preserves_nonnullable_char_padding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from psycopg import sql

    from tests.datasource.environment import postgres_analysis as pg

    with mean_session("postgres", "table", tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        case,
    ):
        assert isinstance(case.source, TableSourceIR)
        with pg.connection(admin=True) as connection:
            connection.execute(
                sql.SQL("DROP VIEW {}.{}").format(
                    sql.Identifier(case.source.database or "public"),
                    sql.Identifier(case.source.table + "_view"),
                )
            )
            connection.execute(
                sql.SQL(
                    "ALTER TABLE {}.{} ALTER COLUMN category TYPE CHAR(2), ALTER COLUMN category SET NOT NULL"
                ).format(
                    sql.Identifier(case.source.database or "public"),
                    sql.Identifier(case.source.table),
                )
            )
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        samples = mv.coordinates(by=(category,), keys=(("A ",),))
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        rows = (
            session.observe({"average": amount.sum(time=point).per(samples).mean()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert rows["average"].tolist() == [300.0]
        assert trace.returned_rows == [1]


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_native_ordered_samples_preserve_null_endpoints_and_duplicate_time_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(None, 20, 20, 20, None),
        sample_groups=2,
        aware=backend != "mysql",
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        samples = amount.mean(time=point).per(point)
        trace = capture_source(monkeypatch)
        reducer = Mock(wraps=_sample_numeric.reduce_samples)
        monkeypatch.setattr(_sample_numeric, "reduce_samples", reducer)
        rows = (
            session.observe(
                {
                    "first": samples.first(),
                    "first_null": samples.first(include_null=True),
                    "last": samples.last(),
                    "last_null": samples.last(include_null=True),
                },
                by=(scope,),
            )
            .compute()
            .to_pandas()
        )
        assert rows["first"].tolist() == [20.0]
        assert rows["last"].tolist() == [20.0]
        assert rows[["first_null", "last_null"]].isna().all().all()
        assert trace.returned_rows == [1, 1, 1, 1]
        assert not reducer.call_args_list
        duplicate = amount.mean(time=point).per((point, category)).first(order_by=point)
        with pytest.raises(MaterializationError, match="one internal sample per time key"):
            session.observe({"first": duplicate}, by=(scope,)).compute()


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "table"),
            ("trino", "iceberg"),
            ("clickhouse", "table"),
        ]
    ),
)
def test_nested_ordered_samples_keep_explicit_parent_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.methods import _sample_numeric

    with mean_session(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        semantic_project_factory,
        values=(10, 20, 20, 20, 100),
        aware=backend != "mysql",
    ) as (session, _):
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        samples = amount.sum(time=point).per(point)
        endpoints = {
            "first": samples.first(order_by=point),
            "last": samples.last(order_by=point),
        }
        reducer = Mock(wraps=_sample_numeric.reduce_samples)
        monkeypatch.setattr(_sample_numeric, "reduce_samples", reducer)
        for keys in ((("A",),), (("B",),), (("A",), ("B",))):
            domain = mv.coordinates(by=(category,), keys=keys)
            rows = (
                session.observe(
                    {name: value.per(domain).mean() for name, value in endpoints.items()}
                )
                .compute()
                .to_pandas()
            )
            # Only A has facts: B contributes a missing endpoint, never A's value.
            if keys == (("B",),):
                assert rows.isna().all().all()
            else:
                assert rows["first"].tolist() == [10.0]
                assert rows["last"].tolist() == [100.0]
        assert not reducer.call_args_list
        for endpoint in endpoints.values():
            with pytest.raises(MaterializationError, match="non-NA internal samples"):
                session.observe({"strict": endpoint.per(domain).mean(nulls="error")}).compute()
