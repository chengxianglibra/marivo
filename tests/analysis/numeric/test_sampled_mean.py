"""Native sample means preserve timestamp weighting and checked completion."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isnan
from pathlib import Path
from typing import Literal

import ibis
import ibis.expr.operations as ops
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler import sampled_mean
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize("reducer", ["mean", "min", "max"])
def test_sample_folds_compile_completed_mean_and_coverage_summaries(
    backend: str,
    reducer: str,
) -> None:
    from marivo._definition.expressions import RowField, SourceAggregate
    from marivo._definition.observations import SampleCoverage
    from marivo._definition.types import ExactRef, SumAll
    from marivo.analysis.compiler.observation import ReductionInput, lower_observation
    from marivo.refs import ref

    table = ibis.table(
        {
            "amount": "float64",
            "point": ibis.dtype("timestamp('UTC', 6)").copy(nullable=False),
            "wall": ibis.dtype("timestamp('UTC', 6)").copy(nullable=False),
            "category": "string",
        },
        name="samples",
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
        preserve_sample_presence=True,
        coverage=(
            (
                SampleCoverage(
                    "key.1", (datetime(2026, 10, 1, tzinfo=timezone.utc),), wall_timezone="UTC"
                ),
            )
            if reducer != "mean"
            else ()
        ),
        mean_fold=("key.1", "error", reducer),
    )
    relation, checks = lower_observation(
        prepared,
        table,
        {
            "sales.facts.amount": "amount",
            "category": "category",
            "point": "point",
            "__coverage_key.1": "wall",
        },
        (),
    )
    sql = ibis.to_sql(relation, dialect=backend).lower()
    assert "avg(" in sql
    assert f"{'avg' if reducer == 'mean' else reducer}(" in sql
    if reducer == "mean":
        assert "distinct" not in sql
        assert "row_number" not in sql
        assert " over " not in sql
        assert "__mean_safe" not in sql
        assert "__coverage_" not in sql
        assert "sample_check.null" in relation.columns
        assert {name for name, _ in checks} == {"sample_check.null"}
    else:
        assert "distinct" in sql
        assert "sample_check.coverage" in relation.columns
        assert {name for name, _ in checks} >= {"sample_check.null", "sample_check.coverage"}
    assert relation["key.1"].type().nullable
    if backend == "clickhouse":
        assert "cast(null as datetime64" not in sql


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
) -> Iterator[tuple[Session, Case]]:
    times: tuple[str, ...] = ("00:00:00", "00:30:00", "00:30:00", "00:30:00", "01:00:00")
    if sample_count is not None:
        times = tuple(
            (datetime(2026, 10, 1) + timedelta(seconds=i)).strftime("%H:%M:%S")
            for i in range(sample_count)
        )
        values = tuple(float(i % 11) for i in range(sample_count))
    rows: list[dict[str, object]] = [
        {
            "id": i,
            "point": datetime.fromisoformat("2026-10-01 " + times[i]).replace(tzinfo=timezone.utc)
            if aware
            else "2026-10-01 " + times[i],
            "category": "A",
            "amount": Decimal(str(value))
            if amount_type == "DECIMAL(18,2)" and value is not None
            else value,
        }
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
    points = [
        f"TIMESTAMP '2026-10-01 {point} UTC'" if aware else f"'2026-10-01 {point}'"
        for point in times
    ]
    physical_amount = (
        "DOUBLE PRECISION" if backend == "postgres" and amount_type == "DOUBLE" else amount_type
    )
    data = SourceData(
        f"id BIGINT, point {point_type}, category VARCHAR(8), amount {physical_amount}",
        ",".join(f"({i},{points[i]},'A',{literal})" for i, literal in enumerate(literals)),
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
        assert all("__quantile_count" in columns for columns in trace.returned_columns)
        assert trace.returned_rows == [3, 3]
        if backend == "trino":
            assert all("ROW_NUMBER()" in sql.upper() for sql in trace.native_sql)
            assert all("APPROX_PERCENTILE" not in sql.upper() for sql in trace.native_sql)
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
        metric = session.catalog.get("metric:sales.average").value
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        point = catalog_value(session.catalog, "sales.facts.point", kind="field")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        result = session.observe({"v": metric}, by=(scope, category)).compute()
        assert result.to_pandas()["v"].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows == [1]
        assert "__mean_folded" in trace.returned_columns[0]
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
            legacy.setattr(sampled_mean, "mean_occurrences", lambda *args: {})
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
        query = session.observe(
            {"v": session.catalog.get("metric:sales.average").value}, by=(scope,)
        )
        trace = capture_source(monkeypatch)
        value = query.compute().to_pandas()["v"].iloc[0]
        assert pd.isna(value) if expected is None else value == pytest.approx(expected)
        assert trace.returned_rows == [1]
        assert "__mean_folded" in trace.returned_columns[0]
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
        query = session.observe(
            {"v": session.catalog.get("metric:sales.average").value}, by=(scope,)
        )
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
                i for i, name in enumerate(trace.returned_columns[0]) if name.endswith("/value")
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
            assert "__mean_folded" in trace.returned_columns[0]
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
            session.observe({"v": session.catalog.get("metric:sales.average").value}, by=(scope,))
            .compute()
            .to_pandas()
        )
        if amount_type == "DECIMAL(18,2)":
            assert rows["v"].tolist() == expected
        else:
            assert rows["v"].tolist() == pytest.approx(expected)
        assert trace.returned_rows == [1, 1]
        assert "__mean_folded" in trace.returned_columns[-1]
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
def test_native_extrema_reduce_completed_means_with_bounded_checks(
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
        with pytest.raises(MaterializationError, match="missing state sample"):
            session.observe({"v": metric}, by=(missing, category)).compute()
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
        assert trace.returned_rows == [1, 1]
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
        metric = session.catalog.get("metric:sales.average").value
        scope = session.time_scope(start="2026-10-01", end="2026-10-05")
        measurements: list[dict[str, object]] = []
        # Toggle native folding without changing the captured sample policy.
        # Local means transport represented samples only, never coverage rows.
        for route, days in (("legacy", 4), ("compact", 4), ("legacy", 1), ("compact", 1)):
            scope = session.time_scope(start="2026-10-01", end=f"2026-10-{1 + days:02}")
            with monkeypatch.context() as config:
                if route == "legacy":
                    config.setattr(sampled_mean, "mean_occurrences", lambda *args: {})
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
        original = SourceSession.execute

        def checked(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            result = original(owner, read)
            if "__mean_folded" in result:
                assert result["__mean_folded"].all()
            return result

        monkeypatch.setattr(SourceSession, "execute", checked)
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
            mv.product(scope, session.members(session.catalog.get("entity:sales.facts").value))
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
        assert all("__mean_folded" not in columns for columns in trace.returned_columns)


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
                        "quantile_mean": amount.quantile(0.95, time=point).per(point).mean(),
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
            assert frame["quantile_mean"].tolist() == [15.0]
            assert frame["weighted"].tolist() == pytest.approx([1300 / 70])


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
def test_sample_coverage_failure_has_exact_bounded_same_read_witnesses(
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
        with pytest.raises(MaterializationError, match="missing state sample") as captured:
            session.observe({"latency": metric}, by=(scope, category)).compute()
        context = captured.value.sampling_context
        assert context is not None, str(captured.value)
        assert context.metric == "latency"
        assert context.field == "sales.facts.point"
        assert context.reducer == ("max" if native else "quantile")
        assert context.period.start == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert len(context.groups) == 1 and not context.groups_truncated
        group = context.groups[0]
        assert group.coordinates == (("sales.facts.category", "A"),)
        assert group.expected_count == 5 and group.observed_count == 1
        assert group.missing_points == tuple(
            datetime(2026, 10, 1, hour, tzinfo=timezone.utc) for hour in (1, 2, 3)
        )
        assert group.missing_truncated
        assert "not hourly buckets" in str(captured.value)
        assert len(trace.returned_rows) == 1
        if native:
            assert trace.returned_rows == [1]
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
def test_sampling_witnesses_exclude_filtered_clocks(
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
        with pytest.raises(MaterializationError) as captured:
            session.observe(
                {"filtered": samples.max() if native else samples.quantile(0.95)}, by=(scope,)
            ).compute()
        context = captured.value.sampling_context
        assert context is not None and context.metric == "filtered"
        assert (context.groups[0].expected_count, context.groups[0].observed_count) == (3, 1)
        assert context.groups[0].missing_points == tuple(
            datetime(2026, 10, 1, hour, tzinfo=timezone.utc) for hour in (0, 2)
        )
        assert len(trace.returned_rows) == 1
        if native:
            assert trace.returned_rows == [1]
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
                }
            )
            .compute()
            .to_pandas()
        )
        assert frame["sum_samples"].tolist() == [5.0]
        assert frame["mean_samples"].tolist() == [10.0]
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
