"""Native sample means preserve timestamp weighting and checked completion."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal

import ibis
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
from tests.support.source_trace import capture_source


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
    amount_type: Literal["DOUBLE", "DECIMAL(18,2)"] = "DOUBLE",
    aware: bool = False,
) -> Iterator[tuple[Session, Case]]:
    times = ("00:00:00", "00:30:00", "00:30:00", "00:30:00", "01:00:00")
    rows: list[dict[str, object]] = [
        {"id": i, "point": "2026-10-01 " + times[i], "category": "A", "amount": value}
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
    data = SourceData(
        f"id BIGINT, point {point_type}, category VARCHAR(8), amount {amount_type}",
        ",".join(f"({i},{points[i]},'A',{literal})" for i, literal in enumerate(literals)),
        "id Int64, point String, category String, amount Nullable(Float64)",
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
                + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all(except_=(point,)),status_time_dimension=point)\n"
                + "@ms.metric()\ndef average():\n    return amount.mean(time=point).per(point).mean()\n",
            }
        )
        yield mv.session.create("sampled-mean", timezone="UTC"), case


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
        amount = session.catalog.fields.get("sales.facts.amount")
        point = session.catalog.fields.get("sales.facts.point")
        category = session.catalog.fields.get("sales.facts.category")
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
            actual = rows[("value", name)].tolist()
            assert actual == [Decimal(expected) if amount_type != "DOUBLE" else float(expected)]
            if amount_type != "DOUBLE":
                assert isinstance(actual[0], Decimal) and actual[0].as_tuple().exponent == -2
        assert all("__quantile_count" in columns for columns in trace.returned_columns)
        assert trace.returned_rows == ([3, 3] if aware else [5, 5])
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
        with pytest.raises(MaterializationError, match="missing state sample"):
            session.observe({"v": quantile.per(point).mean()}, by=(missing,)).compute()
        empty = (
            session.observe({"v": quantile.where(amount < 0).per(point).mean()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert pd.isna(empty[("value", "v")].iloc[0])


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("trino", "iceberg")])
)
def test_native_mean_preserves_sample_weighting_and_fixed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    with mean_session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as (
        session,
        _,
    ):
        metric = session.catalog.metrics.get("sales.average")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        point = session.catalog.fields.get("sales.facts.point")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        trace = capture_source(monkeypatch)
        result = session.observe({"v": metric}, by=(scope, category)).compute()
        assert result.to_pandas()[("value", "v")].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows == [1]
        assert "__mean_folded" in trace.returned_columns[0]
        mixed = session.observe(
            {
                "v": amount.mean(time=point).where(amount >= 0).per(point).mean(),
                "max": amount.max(time=point),
            },
            by=(scope,),
        ).compute()
        assert mixed.to_pandas()[("value", "v")].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows[-2:] == [1, 1]
        with monkeypatch.context() as legacy:
            legacy.setattr(sampled_mean, "mean_occurrences", lambda *args: {})
            old = session.observe({"v": metric}, by=(scope, category)).compute().to_pandas()
        assert old[("value", "v")].tolist() == pytest.approx([100 / 3])
        assert trace.returned_rows[-1] == 5  # three numeric and two scheduled clock rows
        stored = result.persist()

        def offline(*args: object, **kwargs: object) -> None:
            raise AssertionError("fixed result must not read its source")

        monkeypatch.setattr("marivo.datasource.adapters.SourceSession.execute", offline)
        assert stored.to_pandas()[("value", "v")].tolist() == pytest.approx([100 / 3])


@pytest.mark.parametrize(
    "values,cadence,error,expected",
    [
        ((None, None, None, None, None), True, None, None),
        ((0, None, 100, 100, 0), True, None, 100 / 3),
        ((0, 100, 100, 100), True, "missing state sample", None),
        ((0, 100, 100, 100), False, None, 50),
        ((1e308, 1e308, None, None, 1e308), True, "overflow", None),
        ((1e308, -1e308, None, None, 1), True, None, 1 / 3),
        ((float("inf"), 100, 100, 100, 0), True, "finite", None),
    ],
)
def test_mean_checked_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    values: tuple[float | None, ...],
    cadence: bool,
    error: str | None,
    expected: float | None,
) -> None:
    # Local kernel witnesses include arithmetic that is unsafe to compact.
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
        query = session.observe({"v": session.catalog.metrics.get("sales.average")}, by=(scope,))
        if error:
            with pytest.raises(MaterializationError, match=error):
                query.compute()
        else:
            value = query.compute().to_pandas()[("value", "v")].iloc[0]
            assert pd.isna(value) if expected is None else value == pytest.approx(expected)


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
        metric = session.catalog.metrics.get("sales.average")
        scope = session.time_scope(start="2026-10-01", end="2026-10-05")
        measurements: list[dict[str, object]] = []
        # Toggle both optimizations over the same source and numeric kernels.
        # Presence aggregation remains shared in both measurements.
        for route, days in (("legacy", 4), ("compact", 4), ("legacy", 1), ("compact", 1)):
            scope = session.time_scope(start="2026-10-01", end=f"2026-10-{1 + days:02}")
            with monkeypatch.context() as config:
                if route == "legacy":
                    config.setattr(sampled_mean, "mean_occurrences", lambda *args: {})
                    config.setattr(
                        sampled_mean, "coverage_selection", lambda *args: ibis.literal(True)
                    )
                started = perf_counter()
                frame = session.observe({"v": metric}, by=(scope,)).compute()
                rows = frame.to_pandas()
                elapsed = perf_counter() - started
            # 86400 is divisible by 100, so each day averages 0..99 equally.
            assert rows[("value", "v")].tolist() == [49.5]
            assert trace.returned_rows[-1] == (1 if route == "compact" else days * 172800)
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
    "backend,profile", source_params([("duckdb", "table"), ("trino", "iceberg")])
)
def test_mean_strict_and_empty_scope_keep_errors(
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
                # SQL boolean windows must cover the whole read, rather than
                # a default prefix that could discard later failed samples.
                assert result["__mean_folded"].nunique() <= 1
            return result

        monkeypatch.setattr(SourceSession, "execute", checked)
        point = session.catalog.fields.get("sales.facts.point")
        amount = session.catalog.fields.get("sales.facts.amount")
        scope = session.time_scope(start="2026-10-01", end="2026-10-01T02:00:00")
        for metric in (
            amount.mean(time=point).per(point).mean(nulls="error"),
            amount.mean(time=point, nulls="error").per(point).mean(),
        ):
            with pytest.raises(MaterializationError, match="non-NA"):
                session.observe({"v": metric}, by=(scope,)).compute()
        empty = (
            session.observe(
                {"v": amount.mean(time=point).where(amount < 0).per(point).mean()}, by=(scope,)
            )
            .compute()
            .to_pandas()
        )
        assert pd.isna(empty[("value", "v")].iloc[0])
        clipped = session.time_scope(start="2026-10-01T00:15:00", end="2026-10-01T01:15:00")
        rows = (
            session.observe({"v": amount.mean(time=point).per(point).mean()}, by=(clipped,))
            .compute()
            .to_pandas()
        )
        assert rows[("value", "v")].tolist() == [0.0]
