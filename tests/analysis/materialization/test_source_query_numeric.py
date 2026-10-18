"""Native Query numeric selections preserve missing values, keys and checks."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler.source_predicates import query_numeric_value
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("trino", "iceberg"), ("clickhouse", "mergetree")]


def _data(boundary: str = "ordinary") -> SourceData:
    amounts = (2.5, None, float("nan"), 2.5, -1.0, 0.0)
    integers = (10, 20, 30, 40, 50, 60)
    if boundary == "strict":
        amounts = (2.5, None, 1.0, 2.5, -1.0, 0.0)
    if boundary == "precision":
        integers = (2**53 + 1, None, 0, 0, 0, 0)
    elif boundary == "overflow":
        integers = (2**63 - 1, 1, 0, 0, 0, 0)
    rows = [
        {
            "id": i + 1,
            "tenant": f"t{i + 1}",
            "category": ("A", "A", "B", "B", "C", "C")[i],
            "amount": amount,
            "big": integers[i],
            "weight": 0 if boundary == "zero" else 1,
            "day": date(2026, 7, 1),
        }
        for i, amount in enumerate(amounts)
    ]
    values = ",".join(
        f"({row['id']},'{row['tenant']}','{row['category']}',"
        + (
            "NULL"
            if row["amount"] is None
            else "nan()"
            if pd.isna(row["amount"])
            else str(row["amount"])
        )
        + ","
        + ("NULL" if row["big"] is None else str(row["big"]))
        + f",{row['weight']},DATE '2026-07-01')"
        for row in rows
    )
    return SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount DOUBLE, big BIGINT, weight BIGINT NOT NULL, day DATE",
        values,
        "id Int64, tenant String, category Nullable(String), amount Nullable(Float64), big Nullable(Int64), weight Int64, day Date",
        rows,
    )


@pytest.mark.parametrize("provider", ("trino", "clickhouse"))
def test_numeric_missing_value_compiles_before_filter_and_order(provider: str) -> None:
    table = ibis.table(
        {"id": "int64", "value": ibis.dtype("float64").copy(nullable=False)}, name="facts"
    )
    value = query_numeric_value(table.value, provider)
    result = (
        table.mutate(value=value)
        .filter(value > 0.5)
        .order_by(value.desc(nulls_first=False), table.id.asc())
    )
    sql = ibis.to_sql(result, dialect=provider).upper()
    assert ("IS_NAN" if provider == "trino" else "ISNAN") in sql
    assert "NULL" in sql
    if provider == "clickhouse":
        assert "NULLABLE(FLOAT64)" in sql


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_numeric_field_filters_and_top_keep_nan_as_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-query", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        queries = (
            (source.where(source["amount"] > 0.5), [1, 4]),
            (source.where(source["amount"] != 2.5), [5, 6]),
            (source.where(source["amount"].is_null()), [2, 3]),
            (source.top(6, by=source["amount"]), [1, 4, 6, 5, 2, 3]),
            (source.top(2, by=source["amount"]), [1, 4]),
        )
        trace = capture_source(monkeypatch)
        for query, identities in queries:
            frame = query.execute().to_pandas()
            assert [key[-1] for key in frame[("coordinate", "axis.0")]] == identities
        assert trace.returned_rows == [2, 2, 2, 6, 2]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("method", ("sum", "min", "max", "mean", "weighted_mean"))
def test_native_numeric_aggregate_where_top_and_population_stay_at_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    method: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-aggregate-query", report_timezone="UTC")
        big = session.catalog.fields.get("sales.facts.big")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        metric = (
            big.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(big, method)()
        )
        fine = session.observe({"value": metric}, by=(category,))
        eligible = fine.where(fine["value"] > 40.5)
        selected = eligible.top(1, by=eligible["value"])
        consumer = session.observe(
            {"n": big.count()}, where=mv.in_population(selected.population())
        )
        trace = capture_source(monkeypatch)
        frame = selected.execute().to_pandas()
        assert frame[("coordinate", "axis.0")].tolist() == ["C"]
        assert frame[("value", "value")].tolist() == [
            {"sum": 110, "min": 50, "max": 60, "mean": 55, "weighted_mean": 55}[method]
        ]
        counts = consumer.execute().to_pandas()
        assert counts[("value", "n")].tolist() == [2]
        assert trace.returned_rows == [1, 1]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_integer_predicate_uses_actual_source_value_before_pandas_rounding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data("precision")) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-native-precision", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"big": session.catalog.fields.get("sales.facts.big")},
        )
        trace = capture_source(monkeypatch)
        frame = source.where(source["big"] > 2**53).execute().to_pandas()
        assert frame[("coordinate", "axis.0")].tolist() == [("t1", 1)]
        assert frame[("value", "big")].tolist() == [2**53 + 1]
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("boundary", ("overflow", "strict", "zero"))
def test_native_numeric_parent_failures_survive_top_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    boundary: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data(boundary)) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-native-errors", report_timezone="UTC")
        big = session.catalog.fields.get("sales.facts.big")
        amount = session.catalog.fields.get("sales.facts.amount")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        metric = (
            big.sum()
            if boundary == "overflow"
            else amount.sum(nulls="error")
            if boundary == "strict"
            else big.weighted_mean(weight=weight, zero_denominator="error")
        )
        fine = session.observe({"value": metric}, by=(category,))
        trace = capture_source(monkeypatch)
        with pytest.raises(
            MaterializationError,
            match={"overflow": "overflow", "strict": "non-NA", "zero": "zero"}[boundary],
        ):
            fine.top(0, by=fine["value"]).execute()
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_decimal_field_filter_keeps_actual_source_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from decimal import Decimal

    data = _data()
    data = SourceData(
        data.columns.replace("amount DOUBLE", "amount DECIMAL(18,2)"),
        data.values.replace("nan()", "3.75"),
        data.clickhouse_columns.replace("Nullable(Float64)", "Nullable(Decimal(18,2))"),
        [
            {
                **row,
                "amount": None
                if row["amount"] is None
                else Decimal("3.75")
                if pd.isna(row["amount"])
                else Decimal(str(row["amount"])),
            }
            for row in data.rows
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("decimal-native-query", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        trace = capture_source(monkeypatch)
        frame = source.where(source["amount"] > Decimal("2.5")).execute().to_pandas()
        assert frame[("coordinate", "axis.0")].tolist() == [("t3", 3)]
        assert frame[("value", "amount")].tolist() == [Decimal("3.75")]
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_numeric_attribute_cardinality_failure_survives_empty_top(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from tests.analysis.materialization.test_source_query_fusion import _execute

    data = _data()
    data = SourceData(
        data.columns,
        data.values + ",(1,'t1','A',9.5,10,1,DATE '2026-07-01')",
        data.clickhouse_columns,
        [*data.rows, {**data.rows[0], "amount": 9.5}],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-attribute-error", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        filtered = source.where(source["amount"] > 100.5)
        query = filtered.top(0, by=filtered["amount"])
        with pytest.raises(MaterializationError, match="exactly one attribute row"):
            _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError, match="exactly one attribute row"):
            query.execute()
        assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_nonnullable_float_source_uses_nullable_nan_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = _data()
    data = SourceData(
        data.columns.replace("amount DOUBLE,", "amount DOUBLE NOT NULL,"),
        data.values.replace(",'A',NULL,", ",'A',0.0,"),
        data.clickhouse_columns.replace("amount Nullable(Float64)", "amount Float64"),
        [{**row, "amount": 0.0 if row["amount"] is None else row["amount"]} for row in data.rows],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("nonnull-float-native-query", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        trace = capture_source(monkeypatch)
        frame = source.where(source["amount"].is_null()).execute().to_pandas()
        assert frame[("coordinate", "axis.0")].tolist() == [("t3", 3)]
        top = source.top(2, by=source["amount"]).execute().to_pandas()
        assert top[("coordinate", "axis.0")].tolist() == [("t1", 1), ("t4", 4)]
        assert trace.returned_rows == [1, 2]
