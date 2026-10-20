"""Additional remote originals preserve native-reader and local finish policies."""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_postgres import _native_scalar
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime

DATA = SourceData(
    "id BIGINT NOT NULL, tenant VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL, category VARCHAR(16) COLLATE utf8mb4_0900_bin, amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
    "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
    "(2,'t2','A',1,NULL,1,DATE '2026-07-01'),"
    "(3,'t3','B',2,NULL,1,DATE '2026-07-01'),"
    "(4,'t4',NULL,3,NULL,1,DATE '2026-07-02')",
    "",
    [],
)


@pytest.mark.parametrize(
    "method", ("sum", "count", "count_distinct", "min", "max", "mean", "weighted_mean")
)
def test_mysql_original_integer_recompute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    method: Literal[
        "sum",
        "count",
        "count_distinct",
        "min",
        "max",
        "median",
        "quantile",
        "mean",
        "weighted_mean",
    ],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, DATA) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("mysql-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = (
            amount.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(amount, method)()
        )
        query = session.observe({"value": metric})
        native = _native_scalar(case, method)
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        if method == "sum":
            assert frame["value", "value"].tolist() == [2**53 + 7]
        assert frame["value", "value"].tolist() == [native]
        if method in ("mean", "weighted_mean"):
            assert native == float(2**53 + 7) / 4


def test_mysql_original_drill_and_share(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, DATA) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("mysql-original-related", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        query = session.observe({"value": amount.sum()}, by=(category, tenant))
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [4]
        assert trace.uploaded_key_rows == []
        assert sorted(frame["value", "value"].tolist()) == [1, 2, 3, 9007199254740993]

        actual = [
            (None if pd.isna(category_key) else category_key, tenant_key, value)
            for category_key, tenant_key, value in zip(
                frame.iloc[:, 0], frame.iloc[:, 1], frame[("value", "value")], strict=True
            )
        ]
        assert sorted(actual, key=lambda row: row[1]) == [
            ("A", "t1", 2**53 + 1),
            ("A", "t2", 1),
            ("B", "t3", 2),
            (None, "t4", 3),
        ]


@pytest.mark.parametrize("backend,profile", (("postgres", "table"), ("mysql", "innodb-table")))
def test_remote_decimal_weighted_recompute_keeps_fractional_native_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    columns = (
        DATA.columns
        if backend == "mysql"
        else "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE"
    )
    data = SourceData(
        columns.replace("amount BIGINT NOT NULL", "amount DECIMAL(18,2)"),
        "(1,'t1','A',1.25,NULL,1,DATE '2026-07-01'),(2,'t2','A',2.75,NULL,2,DATE '2026-07-01'),(3,'t3',NULL,NULL,NULL,3,DATE '2026-07-02')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-decimal-weighted", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        trace = capture_source(monkeypatch)
        frame = (
            session.observe({"value": amount.weighted_mean(weight=weight)}).compute().to_pandas()
        )
        assert frame["value", "value"].tolist() == [2.25]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("boundary", ("nullable", "collation", "parent_selection"))
def test_mysql_unqualified_original_retains_actual_host_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    boundary: str,
) -> None:
    data = DATA
    if boundary == "nullable":
        data = SourceData(
            DATA.columns.replace("amount BIGINT NOT NULL", "amount BIGINT"),
            "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),(3,'t3','A',NULL,NULL,1,DATE '2026-07-01')",
            "",
            [],
        )
    elif boundary == "collation":
        data = SourceData(
            DATA.columns.replace(
                "category VARCHAR(16) COLLATE utf8mb4_0900_bin",
                "category VARCHAR(16) COLLATE utf8mb4_0900_ai_ci",
            ),
            "(1,'t1','A',1,NULL,1,DATE '2026-07-01'),(2,'t2','a',2,NULL,1,DATE '2026-07-01'),(3,'t3','a',3,NULL,1,DATE '2026-07-01')",
            "",
            [],
        )
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("mysql-original-boundary", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        metric = amount.count_distinct() if boundary == "nullable" else amount.sum()
        fine = session.observe({"value": metric}, by=(category,))
        if boundary == "parent_selection":
            fixed = fine.top(1, by=fine["value"]).compute()
            keys = session.population_from(fixed, on=((fixed.axes[0], category),))
            query = session.observe({"value": metric}, where=mv.in_population(keys))
        else:
            query = session.observe({"value": metric})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value", "value"].tolist() == [
            2 if boundary == "nullable" else 6 if boundary == "collation" else 2**53 + 2
        ]
        assert trace.returned_rows


@pytest.mark.parametrize("backend,profile", (("trino", "iceberg"), ("clickhouse", "mergetree")))
def test_remote_original_recompute_does_not_require_input_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',1,NULL,1,DATE '2026-07-01'),(2,'t2','B',2,NULL,1,DATE '2026-07-01')",
        "id Int64, tenant String, category Nullable(String), amount Int64, big Nullable(Int64), weight Int64, day Date",
        [
            {
                "id": 1,
                "tenant": "t1",
                "category": "A",
                "amount": 1,
                "big": None,
                "weight": 1,
                "day": date(2026, 7, 1),
            },
            {
                "id": 2,
                "tenant": "t2",
                "category": "B",
                "amount": 2,
                "big": None,
                "weight": 1,
                "day": date(2026, 7, 1),
            },
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-original-input", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value", "value"].tolist() == [3]
        assert trace.returned_rows == [1]
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )
