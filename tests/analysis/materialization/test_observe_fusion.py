"""Actual multi-Metric reads share compatible aggregates on each provider."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization import frame_source_preparation
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("calendar", (False, True))
def test_multi_field_aggregate_keeps_nulls_types_and_empty_buckets(
    backend: str,
    profile: str,
    calendar: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), "
        "amount BIGINT, big DECIMAL(18,2), weight DOUBLE PRECISION, day DATE",
        "(1,'t','A',10,10.01,1,DATE '2026-07-01'),"
        "(2,'t','A',30,30.02,3,DATE '2026-07-01'),"
        "(3,'t',NULL,NULL,NULL,NULL,DATE '2026-07-02')",
        "id Int64, tenant String, category Nullable(String), amount Nullable(Int64), "
        "big Nullable(Decimal(18,2)), weight Nullable(Float64), day Date",
        [
            {
                "id": 1,
                "tenant": "t",
                "category": "A",
                "amount": 10,
                "big": Decimal("10.01"),
                "weight": 1.0,
                "day": date(2026, 7, 1),
            },
            {
                "id": 2,
                "tenant": "t",
                "category": "A",
                "amount": 30,
                "big": Decimal("30.02"),
                "weight": 3.0,
                "day": date(2026, 7, 1),
            },
            {
                "id": 3,
                "tenant": "t",
                "category": None,
                "amount": None,
                "big": None,
                "weight": None,
                "day": date(2026, 7, 2),
            },
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("observe-fusion", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        exact = catalog_value(session.catalog, "sales.facts.big", kind="measure")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        day = catalog_value(session.catalog, "sales.facts.day", kind="time_dimension")
        scope = session.time_scope(start="2026-07-01", end="2026-07-04")
        by = (scope.by(mv.grain("day")),) if calendar else (category,)
        query = session.observe(
            {
                "total": amount.sum(time=day),
                "exact": exact.sum(time=day),
                "average": weight.mean(time=day),
                "minimum": amount.min(time=day),
                "maximum": weight.max(time=day),
                "count": amount.count(time=day),
                "distinct": amount.count_distinct(time=day),
                "weighted": amount.weighted_mean(weight=weight, time=day),
                **(
                    {"median": amount.median(time=day), "q25": amount.quantile(0.25, time=day)}
                    if backend in ("duckdb", "postgres", "clickhouse")
                    else {}
                ),
                **(
                    {
                        "approx_median": amount.approx_median(time=day),
                        "approx_distinct": amount.approx_nunique(time=day),
                    }
                    if backend in ("duckdb", "trino", "clickhouse")
                    else {}
                ),
            },
            by=by,
        )
        with monkeypatch.context() as control:
            control.setattr(frame_source_preparation, "optimize_query", lambda plan: plan)
            baseline = query.compute().to_pandas()
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        # MySQL's isolated SUM returns Decimal; the existing common aggregate
        # explicitly binds its declared integer carrier. Check that carrier below.
        pd.testing.assert_frame_equal(result, baseline, check_dtype=backend != "mysql")
        assert str(result["total"].dtype) == "int64[pyarrow]"
        assert len(trace.native_sql) == (2 if calendar else 1)
        assert result["total"].iloc[0] == 40
        assert result["exact"].iloc[0] == Decimal("40.03")
        assert result["average"].iloc[0] == 2.0
        assert result["minimum"].iloc[0] == 10
        assert result["maximum"].iloc[0] == 3.0
        assert result["count"].tolist() == [2, 0]
        assert result["distinct"].tolist() == [2, 0]
        assert result["weighted"].iloc[0] == 25
        if backend in ("duckdb", "postgres", "clickhouse"):
            assert result["median"].iloc[0] == 20
            assert result["q25"].iloc[0] == 15
        if backend in ("duckdb", "trino", "clickhouse"):
            assert isinstance(case.source, TableSourceIR)
            algorithm = {
                "duckdb": "approx_quantile(amount, 0.5)",
                "trino": "approx_percentile(amount, 0.5)",
                "clickhouse": "quantileTDigest(0.5)(amount)",
            }[backend]
            sql = f"SELECT {algorithm} AS median FROM {case.source.table} WHERE category = 'A'"
            if backend == "clickhouse":
                from tests.datasource.environment.clickhouse_analysis import connection

                with connection() as reader:
                    row = reader.query(sql).first_row
                    assert row is not None
                    expected_median = row[0]
            else:
                expected_median = case.session._backend.sql(sql).execute().iloc[0, 0]
            assert result["approx_median"].iloc[0] == pytest.approx(expected_median)
            assert result["approx_distinct"].iloc[0] == 2
        assert result.iloc[1][["total", "exact", "average", "minimum", "maximum"]].isna().all()
        assert len(result) == 2


@pytest.mark.parametrize(
    "backend,profile,approximate",
    source_params(
        [
            ("duckdb", "table", False),
            ("duckdb", "table", True),
            ("postgres", "table", False),
            ("trino", "iceberg", True),
            ("clickhouse", "mergetree", False),
            ("clickhouse", "mergetree", True),
        ]
    ),
)
@pytest.mark.parametrize("domain", ("overall", "interval", "grouped"))
def test_count_and_quantiles_share_actual_scan(
    backend: str,
    profile: str,
    approximate: bool,
    domain: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis.materialization import query_key_fusion
    from tests.analysis.numeric.test_remote_numeric_query import DATA
    from tests.datasource.source_cases import receipt

    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("quantile-fusion", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        day = catalog_value(session.catalog, "sales.facts.day", kind="time_dimension")
        scope = session.time_scope(start="2026-07-01", end="2026-07-03")
        by = (category,) if domain == "grouped" else ()
        where = scope if domain == "interval" else None
        quantile = amount.approx_quantile if approximate else amount.quantile
        metrics = {
            "count": amount.count(time=day),
            "p50": quantile(0.5, time=day),
            "p90": quantile(0.9, time=day),
            "p99": quantile(0.99, time=day),
        }
        query = session.observe(metrics, by=by, where=where)
        with monkeypatch.context() as control:
            control.setattr(frame_source_preparation, "optimize_query", lambda plan: plan)
            control.setattr(
                query_key_fusion, "fuse_query_terminal", lambda prepared, lowered: lowered
            )
            baseline_trace = capture_source(control)
            baseline = query.compute().to_pandas()
        with monkeypatch.context() as control:
            single_trace = capture_source(control)
            session.observe({"count": amount.count(time=day)}, by=by, where=where).compute()
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        pd.testing.assert_frame_equal(result, baseline, check_exact=not approximate, rtol=1e-5)
        assert len(trace.native_sql) == len(single_trace.native_sql) == 1
        assert len(baseline_trace.native_sql) == 4
        assert result["count"].tolist() == ([2, 1] if domain == "grouped" else [3])
        assert result["p50"].between(1, 100).all()
        assert (result["p50"] <= result["p90"]).all()
        assert (result["p90"] <= result["p99"]).all()
        if not approximate and domain != "grouped":
            assert result["p50"].tolist() == [3.0]
            assert result["p90"].iloc[0] == pytest.approx(80.6)
            assert result["p99"].iloc[0] == pytest.approx(98.06)
        plans: list[str] = []
        scanned: list[int] = []
        if backend == "duckdb":
            plan = case.session._backend.raw_sql("EXPLAIN " + trace.native_sql[0]).fetchall()
            plans = [str(row[1]) for row in plan]
            assert sum(plan.count("SEQ_SCAN") for plan in plans) == 1
        elif backend == "clickhouse":
            from tests.datasource.environment.clickhouse_analysis import connection

            with connection() as reader:
                plans = [
                    str(row[0])
                    for row in reader.query("EXPLAIN " + trace.native_sql[0]).result_rows
                ]
                assert sum(plan.count("ReadFromMergeTree") for plan in plans) == 1
                for sql in [
                    *trace.native_sql,
                    *single_trace.native_sql,
                    *baseline_trace.native_sql,
                ]:
                    scanned.append(int(reader.query(sql).summary["read_rows"]))
            assert scanned[0] == scanned[1] > 0
            assert sum(scanned[2:]) == 4 * scanned[0]
        receipt(
            f"observe-fusion-{backend}-{approximate}-{domain}",
            {
                "environment": case.environment,
                "sql": trace.native_sql,
                "unmerged_sql": baseline_trace.native_sql,
                "plans": plans,
                "diagnostic_read_rows": scanned,
                "merged_seconds": trace.source_seconds,
                "single_seconds": single_trace.source_seconds,
                "unmerged_seconds": baseline_trace.source_seconds,
            },
        )
