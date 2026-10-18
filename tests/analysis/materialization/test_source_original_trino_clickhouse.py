"""Original recomputation follows qualified native precision and numeric policies."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_postgres import _unoptimized
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime
CASES = [("trino", "iceberg"), ("clickhouse", "mergetree")]
DATA = SourceData(
    "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
    "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
    "(2,'t2','A',0,NULL,1,DATE '2026-07-01'),"
    "(3,'t3',NULL,0,NULL,1,DATE '2026-07-02')",
    "id Int64, tenant String, category Nullable(String), amount Int64, big Nullable(Int64), weight Int64, day Date",
    [
        {
            "id": index,
            "tenant": f"t{index}",
            "category": category,
            "amount": amount,
            "big": None,
            "weight": 1,
            "day": date(2026, 7, day),
        }
        for index, category, amount, day in (
            (1, "A", 2**53 + 1, 1),
            (2, "A", 0, 1),
            (3, None, 0, 2),
        )
    ],
)


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize(
    "method", ("sum", "count", "min", "max", "count_distinct", "mean", "weighted_mean")
)
def test_remote_original_recompute_uses_qualified_integer_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-original", report_timezone="UTC")
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
        if backend == "trino" and method in ("mean", "weighted_mean"):
            # Native Decimal state transport retains the provider's precision.
            expected[("value", "value")] = pd.Series(
                3002399751580330.5, index=expected.index, dtype=expected[("value", "value")].dtype
            )
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )
        if backend == "clickhouse" and method == "count_distinct":
            assert "uniqExact" in trace.native_sql[0]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("operation", ("drill", "share"))
def test_remote_original_drill_and_share_preserve_complete_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-original-related", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        fine = session.observe({"value": amount.sum()}, by=(category,))
        query = fine.drill(add=(tenant,)) if operation == "drill" else fine.share("value")
        expected = _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == [3 if operation == "drill" else 2]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("method", ("mean", "weighted_mean"))
@pytest.mark.parametrize("samples", ("cancellation", "odd_total"))
def test_remote_integer_states_follow_native_precision_and_host_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    method: str,
    samples: str,
) -> None:
    numbers = (2**53 + 1, -(2**53), 0) if samples == "cancellation" else (2**53 + 1, 1, 1)
    data = replace(
        DATA,
        values=f"(1,'t1','A',{numbers[0]},NULL,1,DATE '2026-07-01'),"
        f"(2,'t2','A',{numbers[1]},NULL,1,DATE '2026-07-01'),"
        f"(3,'t3',NULL,{numbers[2]},NULL,1,DATE '2026-07-02')",
        rows=[{**row, "amount": value} for row, value in zip(DATA.rows, numbers, strict=True)],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-original-cancellation", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = amount.mean() if method == "mean" else amount.weighted_mean(weight=weight)
        query = session.observe({"value": metric}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        expected_value = sum(numbers) / 3
        if backend == "trino":
            expected_value = 1 / 3 if samples == "cancellation" else 3002399751580332.0
            expected[("value", "value")] = pd.Series(
                expected_value, index=expected.index, dtype=expected[("value", "value")].dtype
            )
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [expected_value]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_nullable_integer_distinct_recomputes_before_native_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = replace(
        DATA,
        columns=DATA.columns.replace("amount BIGINT NOT NULL", "amount BIGINT"),
        values="(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','A',NULL,NULL,1,DATE '2026-07-02')",
        clickhouse_columns=DATA.clickhouse_columns.replace(
            "amount Int64", "amount Nullable(Int64)"
        ),
        rows=[
            {**row, "category": "A", "amount": value}
            for row, value in zip(DATA.rows, (2**53 + 1, 2**53, None), strict=True)
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-original-nullable", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        expected[("value", "value")] = pd.Series(
            2, index=expected.index, dtype=expected[("value", "value")].dtype
        )
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [2]
        assert trace.returned_rows == [1]
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize(
    "kind,method,expected",
    (
        ("float", "sum", 4.0),
        ("float", "mean", 2.0),
        ("float", "weighted_mean", 2.25),
        ("decimal", "sum", Decimal("4.00")),
        ("decimal", "mean", 2.0),
        ("decimal", "weighted_mean", 2.25),
        ("integer", "mean", 6.0),
        ("integer", "weighted_mean", 19 / 3),
    ),
)
def test_remote_nullable_native_values_keep_fractional_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    kind: str,
    method: str,
    expected: float | Decimal,
) -> None:
    sql_type = {"float": "DOUBLE", "decimal": "DECIMAL(18,2)", "integer": "BIGINT"}[kind]
    native_type = {"float": "Float64", "decimal": "Decimal(18,2)", "integer": "Int64"}[kind]
    numbers = (5, 7, None) if kind == "integer" else (1.25, 2.75, None)
    data = replace(
        DATA,
        columns=DATA.columns.replace("amount BIGINT NOT NULL", f"amount {sql_type}"),
        values=f"(1,'t1','A',{numbers[0]},NULL,1,DATE '2026-07-01'),"
        f"(2,'t2','A',{numbers[1]},NULL,2,DATE '2026-07-01'),"
        "(3,'t3',NULL,NULL,NULL,3,DATE '2026-07-02')",
        clickhouse_columns=DATA.clickhouse_columns.replace(
            "amount Int64", f"amount Nullable({native_type})"
        ),
        rows=[
            {**row, "amount": value, "weight": index + 1}
            for index, (row, value) in enumerate(zip(DATA.rows, numbers, strict=True))
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-native-fractions", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = (
            amount.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(amount, method)()
        )
        trace = capture_source(monkeypatch)
        frame = session.observe({"value": metric}, by=(category,)).rollup().execute().to_pandas()
        assert frame[("value", "value")].tolist() == [expected]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


@pytest.mark.parametrize("backend,profile", source_params([("trino", "non-iceberg")]))
def test_trino_basic_integer_recompute_does_not_require_iceberg_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("trino-connector-original", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [2**53 + 1]
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_weighted_original_uses_wide_accumulator_after_fine_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = replace(
        DATA,
        values="(1,'t1','A',4611686018427387904,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','B',4611686018427387904,NULL,1,DATE '2026-07-01')",
        rows=[
            {**DATA.rows[index], "category": category, "amount": 2**62}
            for index, category in enumerate(("A", "B"))
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-original-wide-weighted", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        query = session.observe(
            {"value": amount.weighted_mean(weight=weight)}, by=(category,)
        ).rollup()
        expected = _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert frame[("value", "value")].tolist() == [float(2**62)]
        assert trace.returned_rows == [1]
