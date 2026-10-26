"""Actual multi-Metric reads share compatible aggregates on each provider."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization import frame_source_preparation
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
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        exact = catalog_value(session.catalog, "sales.facts.big", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        by = (
            session.time_scope(start="2026-07-01", end="2026-07-04").by(mv.grain("day"))
            if calendar
            else category,
        )
        query = session.observe(
            {
                "total": amount.sum(),
                "exact": exact.sum(),
                "average": weight.mean(),
                "minimum": amount.min(),
                "maximum": weight.max(),
                "count": amount.count(),
            },
            by=by,
        )
        with monkeypatch.context() as control:
            control.setattr(frame_source_preparation, "optimize_query", lambda plan: plan)
            baseline = query.compute().to_pandas()
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        pd.testing.assert_frame_equal(result, baseline)
        assert len(trace.native_sql) == 1
        assert trace.uploaded_key_rows == []
        assert result["total"].iloc[0] == 40
        assert result["exact"].iloc[0] == Decimal("40.03")
        assert result["average"].iloc[0] == 2.0
        assert result["minimum"].iloc[0] == 10
        assert result["maximum"].iloc[0] == 3.0
        assert result["count"].tolist() == ([2, 0, 0] if calendar else [2, 0])
        assert result.iloc[1][["total", "exact", "average", "minimum", "maximum"]].isna().all()
        if calendar:
            assert result["total"].iloc[2] == 0
            assert result["exact"].iloc[2] == 0
            assert result.iloc[2][["average", "minimum", "maximum"]].isna().all()
