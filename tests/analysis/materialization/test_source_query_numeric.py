"""Native Query numeric selections preserve missing values, keys and checks."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import ProjectionRead, QueryPlan, SourceRead
from marivo.analysis.core.data_types import DataColumn, ScalarDataType
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

CASES = [("trino", "iceberg"), ("clickhouse", "mergetree")]


def _data(boundary: str = "ordinary") -> SourceData:
    amounts = (2.5, None, None, 2.5, -1.0, 0.0)
    if boundary == "nan":
        amounts = (2.5, None, float("nan"), 2.5, -1.0, 0.0)
    integers: tuple[int | None, ...] = (10, 20, 30, 40, 50, 60)
    if boundary == "strict":
        amounts = (2.5, None, 1.0, 2.5, -1.0, 0.0)
    if boundary == "precision":
        integers = (2**53 + 1, None, 0, 0, 0, 0)
    elif boundary == "overflow":
        integers = (2**63 - 1, 1, 0, 0, 0, 0)
    rows: list[dict[str, object]] = [
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
            if isinstance(row["amount"], float) and pd.isna(row["amount"])
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
def test_source_fields_have_no_native_finite_checks(provider: str) -> None:
    table = ibis.table({"value": "float64"}, name="facts")
    column = DataColumn("value", "value", ScalarDataType("float64"), True)
    stage = SourceRead(
        "read", "facts", (), (column,), (), ProjectionRead((("value", "value"),)), (), (), None
    )
    plan = QueryPlan((stage,), (), "read", ())
    read = lower_query(plan, {"facts": table}).reads[0]
    sql = ibis.to_sql(read.expression, dialect=provider).upper()
    assert ("IS_NAN" if provider == "trino" else "ISNAN") not in sql
    assert ("IS_INFINITE" if provider == "trino" else "ISINFINITE") not in sql
    assert read.numeric_checks == ()


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_numeric_field_filters_and_top_preserve_nulls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("numeric-query", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
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
            frame = query.compute().to_pandas()
            assert [key[-1] for key in frame[frame.attrs["axes"][0]["name"]]] == identities
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("method", ("sum", "min", "max", "mean", "weighted_mean"))
def test_source_numeric_aggregate_frame_selection_and_explicit_bridge(
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
        session = mv.session.get_or_create("numeric-aggregate-query", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        metric = (
            big.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(big, method)()
        )
        fine = session.observe({"value": metric}, by=(category,))
        eligible = fine.where(fine["value"] > 40.5)
        selected = eligible.top(1, by=eligible["value"])
        fixed = selected.compute()
        keys = session.population_from(fixed, on=((fixed.axes[0], category),))
        consumer = session.observe({"n": big.count()}, where=mv.in_population(keys))
        trace = capture_source(monkeypatch)
        frame = fixed.to_pandas()
        assert frame[frame.attrs["axes"][0]["name"]].tolist() == ["C"]
        assert frame["value"].tolist() == [
            {"sum": 110, "min": 50, "max": 60, "mean": 55, "weighted_mean": 55}[method]
        ]
        counts = consumer.compute().to_pandas()
        assert counts["n"].tolist() == [2]
        assert trace.uploaded_key_rows == [1]


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
        session = mv.session.get_or_create("numeric-native-precision", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        # This is now an explicit source row predicate, independently checked
        # before its admitted native pandas handoff, not a Frame fusion claim.
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value, where=big > 2**53),
            {"big": big},
        )
        table = case.session.bind(case.source, source_identity="integer-predicate-oracle").relation
        native = case.session._backend.execute(table.filter(table.big > 2**53).select(table.big))
        assert isinstance(native, pd.DataFrame)
        frame = source.compute().to_pandas()
        assert frame.iloc[:, 0].tolist() == [("t1", 1)]
        assert frame["big"].tolist() == native.big.tolist()
        # Fixed host integer data retains its exact current-row predicate.
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        fixed = session.values(
            by=(category,),
            columns={"big": None},
            rows=((("A",), {"big": 2**53 + 1}), (("B",), {"big": 2**53 - 1})),
        ).compute()
        selected = fixed.where(fixed["big"] > 2**53).compute().to_pandas()
        assert selected.iloc[:, 0].tolist() == ["A"]
        assert selected["big"].tolist() == [2**53 + 1]


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
        session = mv.session.get_or_create("numeric-native-errors", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        metric = (
            big.sum()
            if boundary == "overflow"
            else amount.sum(nulls="error")
            if boundary == "strict"
            else big.weighted_mean(weight=weight)
        )
        fine = session.observe({"value": metric}, by=(category,))
        trace = capture_source(monkeypatch)
        if boundary == "overflow":
            actual = fine.compute().to_pandas()["value"]
            assert actual.iloc[0] == Decimal(2**63)
            assert fine.top(0, by=fine["value"]).compute().to_pandas().empty
        else:
            with pytest.raises(
                MaterializationError, match={"strict": "non-NA", "zero": "zero"}[boundary]
            ):
                fine.top(0, by=fine["value"]).compute()


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_decimal_field_filter_keeps_actual_source_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from decimal import Decimal

    data = _data("nan")
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
                if isinstance(row["amount"], float) and pd.isna(row["amount"])
                else Decimal(str(row["amount"])),
            }
            for row in data.rows
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("decimal-native-query", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        trace = capture_source(monkeypatch)
        frame = source.where(source["amount"] > Decimal("2.5")).compute().to_pandas()
        assert frame[frame.attrs["axes"][0]["name"]].tolist() == [("t3", 3)]
        assert frame["amount"].tolist() == [Decimal("3.75")]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_numeric_attribute_output_key_failure_survives_empty_top(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:

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
        session = mv.session.get_or_create("numeric-attribute-error", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        filtered = source.where(source["amount"] > 100.5)
        query = filtered.top(0, by=filtered["amount"])
        with pytest.raises(MaterializationError, match="complete unique stage key"):
            query.compute()
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError, match="complete unique stage key"):
            query.compute()


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_nonnullable_float_source_keeps_valid_nan_through_filter_and_top(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = _data("nan")
    data = SourceData(
        data.columns.replace("amount DOUBLE,", "amount DOUBLE NOT NULL,"),
        data.values.replace(",'A',NULL,", ",'A',0.0,"),
        data.clickhouse_columns.replace("amount Nullable(Float64)", "amount Float64"),
        [{**row, "amount": 0.0 if row["amount"] is None else row["amount"]} for row in data.rows],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("nonnull-float-native-query", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        trace = capture_source(monkeypatch)
        values = pa.array(source.compute().to_pandas()["amount"])
        assert values.null_count == 0
        assert sum(value.as_py() != value.as_py() for value in values) == 1
        for frame in (source.where(source["amount"].is_null()), source.top(0, by=source["amount"])):
            assert frame.compute().to_pandas().empty
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "mergetree")])
)
def test_native_integer_overflow_keeps_backend_behavior_and_fixed_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    """Native wrapping is an accepted value, while fixed integer arithmetic is checked."""
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data("overflow")) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-overflow", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        source = session.observe({"value": (big + 1).sum() + 1}, by=(category,))
        trace = capture_source(monkeypatch)
        if backend == "duckdb":
            with pytest.raises(MaterializationError, match=r"overflow|Overflow") as error:
                source.compute()
            assert error.value.expected and error.value.received and error.value.repair
        else:
            selected = source.where(source["value"] < 0)
            selected = selected.top(1, by=selected["value"])
            saved = selected.persist()
            values = saved.to_pandas()["value"]
            assert values.tolist() == [Decimal(-(2**63) + 3)]
            assert str(values.dtype) == "decimal128(38, 0)[pyarrow]"
            assert session.persisted_frame(saved.artifact_id).to_pandas().equals(saved.to_pandas())
            assert trace.native_sql
        fixed = session.read(
            session.members(session.catalog.get("entity:sales.facts").value), {"big": big}
        ).compute()
        with pytest.raises(MaterializationError, match="overflow"):
            fixed.select({"value": fixed["big"] + 1}).compute()


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("clickhouse", "mergetree")])
)
@pytest.mark.parametrize("operation", ["add", "sum"])
def test_decimal_overflow_matches_native_result_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    operation: str,
) -> None:
    from dataclasses import replace

    import duckdb
    import pyarrow as pa

    from marivo.datasource.errors import DatasourceError

    maximum = Decimal("9" * 38)
    original = _data()
    raw_rows = [
        {**row, "big": maximum if index < 2 else None} for index, row in enumerate(original.rows)
    ]
    data = replace(
        original,
        columns=original.columns.replace("big BIGINT", "big DECIMAL(38,0)"),
        clickhouse_columns=original.clickhouse_columns.replace(
            "Nullable(Int64)", "Nullable(Decimal(38,0))", 1
        ),
        rows=raw_rows,
        values=",".join(
            f"({row['id']},'{row['tenant']}','{row['category']}',0,{row['big'] if row['big'] is not None else 'NULL'},1,DATE '2026-07-01')"
            for row in raw_rows
        ),
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("decimal-native-overflow", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        native = case.session.bind(case.source, source_identity="decimal-overflow-oracle").relation
        operand = (
            "CAST(big + CAST(1 AS DECIMAL(1,0)) AS DECIMAL(38,0))" if operation == "add" else "big"
        )
        sql = f"SELECT CAST(SUM({operand}) AS DECIMAL(38,0)) FROM ({ibis.to_sql(native, dialect=backend)}) AS input_rows"
        native_error: type[Exception] = duckdb.Error
        if backend == "clickhouse":
            from clickhouse_connect.driver.exceptions import ClickHouseError

            native_error = ClickHouseError
        expected: Decimal | None = None
        failed = False
        try:
            cursor = case.session._backend.raw_sql(sql)
            row = cursor.result_rows[0] if backend == "clickhouse" else cursor.fetchone()
            assert row is not None
            expected = row[0]
            assert isinstance(expected, Decimal)
            pa.scalar(expected, type=pa.decimal128(38, 0))
        except (native_error, pa.ArrowInvalid):
            failed = True
        source = session.observe(
            {"value": (big + Decimal(1)).sum() if operation == "add" else big.sum()}
        )
        if failed:
            with pytest.raises((MaterializationError, DatasourceError)) as error:
                source.compute()
            assert error.value.expected and error.value.received and error.value.repair
        else:
            saved = source.persist()
            assert saved.to_pandas()["value"].tolist() == [expected]
            assert session.persisted_frame(saved.artifact_id).to_pandas().equals(saved.to_pandas())
