"""Additional remote originals preserve native-reader and local finish policies."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_postgres import _unoptimized
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
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, DATA) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-original", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = (
            amount.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(amount, method)()
        )
        query = session.observe({"value": metric}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        if method in ("mean", "weighted_mean"):
            expected[("value", "value")] = pd.Series(
                float(2**53 + 7) / 4,
                index=expected.index,
                dtype=expected[("value", "value")].dtype,
            )
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        if method == "sum":
            assert frame[("value", "value")].tolist() == [2**53 + 7]


@pytest.mark.parametrize("operation", ("drill", "share"))
def test_mysql_original_drill_and_share(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, DATA) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-original-related", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        fine = session.observe({"value": amount.sum()}, by=(category,))
        query = fine.drill(add=(tenant,)) if operation == "drill" else fine.share("value")
        expected = _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == [4 if operation == "drill" else 3]
        assert trace.uploaded_key_rows == []


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
        "(1,'t1','A',1.25,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',2.75,NULL,2,DATE '2026-07-01'),"
        "(3,'t3',NULL,NULL,NULL,3,DATE '2026-07-02')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-decimal-weighted", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        trace = capture_source(monkeypatch)
        frame = (
            session.observe({"value": amount.weighted_mean(weight=weight)}, by=(category,))
            .rollup()
            .execute()
            .to_pandas()
        )
        assert frame[("value", "value")].tolist() == [2.25]
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
            "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
            "(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),"
            "(3,'t3','A',NULL,NULL,1,DATE '2026-07-01')",
            "",
            [],
        )
    elif boundary == "collation":
        data = SourceData(
            DATA.columns.replace(
                "category VARCHAR(16) COLLATE utf8mb4_0900_bin",
                "category VARCHAR(16) COLLATE utf8mb4_0900_ai_ci",
            ),
            "(1,'t1','A',1,NULL,1,DATE '2026-07-01'),"
            "(2,'t2','a',2,NULL,1,DATE '2026-07-01'),"
            "(3,'t3','a',3,NULL,1,DATE '2026-07-01')",
            "",
            [],
        )
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-original-boundary", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        metric = amount.count_distinct() if boundary == "nullable" else amount.sum()
        fine = session.observe({"value": metric}, by=(category,))
        selected = fine.top(1, by=fine["value"]) if boundary == "parent_selection" else fine
        query = selected.rollup()
        from marivo.analysis.materialization import source_original_dependent

        with monkeypatch.context() as scoped:
            scoped.setattr(
                source_original_dependent,
                "fuse_dependent_original",
                lambda prepared, lowered: lowered,
            )
            expected = _unoptimized(query, scoped)
        trace = capture_source(monkeypatch)
        result = query.execute().to_pandas()
        if boundary == "nullable":
            expected[("value", "value")] = pd.Series(
                2, index=expected.index, dtype=expected[("value", "value")].dtype
            )
        pd.testing.assert_frame_equal(result, expected, check_exact=True)
        if boundary == "parent_selection":
            assert trace.returned_rows == [3, 1]
            assert not any(
                "__original_value" in name for names in trace.returned_columns for name in names
            )
        elif boundary == "nullable":
            assert trace.returned_rows == [1]
            assert not any(
                "__original_value" in name for names in trace.returned_columns for name in names
            )
        else:
            assert trace.returned_rows == [3]
            assert any(
                "__original_value" in name for names in trace.returned_columns for name in names
            )


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
        session = mv.session.get_or_create("remote-original-input", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        result = query.execute().to_pandas()
        pd.testing.assert_frame_equal(result, expected, check_exact=True)
        assert result[("value", "value")].tolist() == [3]
        assert trace.returned_rows == [1]
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )
