"""Original recomputation follows qualified native precision and numeric policies."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pandas as pd
import pytest
from trino.exceptions import TrinoUserError

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.analysis.materialization.test_source_original_postgres import _native_scalar
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
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
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = (
            amount.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(amount, method)()
        )
        query = session.observe({"value": metric})
        native = (
            _native_clickhouse_weighted_scalar(case)
            if backend == "clickhouse" and method == "weighted_mean"
            else _native_scalar(case, method)
        )
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )
        if backend == "clickhouse" and method == "count_distinct":
            assert "uniqExact" in trace.native_sql[0]
        assert frame["value", "value"].tolist() == [native]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_original_drill_and_share_preserve_complete_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-original-related", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        query = session.observe({"value": amount.sum()}, by=(category, tenant))
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [3]
        assert trace.uploaded_key_rows == []
        assert sorted(frame["value", "value"].tolist()) == [0, 0, 9007199254740993]

        actual = [
            (None if pd.isna(category_key) else category_key, tenant_key, value)
            for category_key, tenant_key, value in zip(
                frame.iloc[:, 0], frame.iloc[:, 1], frame[("value", "value")], strict=True
            )
        ]
        assert sorted(actual, key=lambda row: row[1]) == [
            ("A", "t1", 2**53 + 1),
            ("A", "t2", 0),
            (None, "t3", 0),
        ]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("method", ("mean", "weighted_mean"))
@pytest.mark.parametrize("samples", ("cancellation", "odd_total"))
def test_remote_integer_states_follow_native_precision_and_host_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
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
    samples: str,
) -> None:
    numbers = (2**53 + 1, -(2**53), 0) if samples == "cancellation" else (2**53 + 1, 1, 1)
    data = replace(
        DATA,
        values=f"(1,'t1','A',{numbers[0]},NULL,1,DATE '2026-07-01'),(2,'t2','A',{numbers[1]},NULL,1,DATE '2026-07-01'),(3,'t3',NULL,{numbers[2]},NULL,1,DATE '2026-07-02')",
        rows=[{**row, "amount": value} for row, value in zip(DATA.rows, numbers, strict=True)],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-original-cancellation", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = amount.mean() if method == "mean" else amount.weighted_mean(weight=weight)
        query = session.observe({"value": metric})
        native = (
            _native_clickhouse_weighted_scalar(case)
            if backend == "clickhouse" and method == "weighted_mean"
            else _native_scalar(case, method)
        )
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )
        assert frame["value", "value"].tolist() == [native]

        tenant = session.catalog.fields.get("sales.facts.tenant")
        fixed = session.values(
            by=(tenant,),
            columns={"amount": None},
            rows=tuple(((f"t{index}",), {"amount": value}) for index, value in enumerate(numbers)),
        ).compute()
        # All weights are one, so the exact weighted mean equals this row mean.
        exact = float(Fraction(sum(numbers), len(numbers)))
        assert exact == (1 / 3 if samples == "cancellation" else (2**53 + 3) / 3)
        finished = fixed.reduce({"mean": fixed["amount"].mean()}).compute()
        assert finished.to_pandas()[("value", "mean")].tolist() == [exact]


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
        values="(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),(3,'t3','A',NULL,NULL,1,DATE '2026-07-02')",
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
        session = mv.session.create("remote-original-nullable", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()})
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["value", "value"].tolist() == [2]
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
    expected: float | Decimal,
) -> None:
    sql_type = {"float": "DOUBLE", "decimal": "DECIMAL(18,2)", "integer": "BIGINT"}[kind]
    native_type = {"float": "Float64", "decimal": "Decimal(18,2)", "integer": "Int64"}[kind]
    numbers = (5, 7, None) if kind == "integer" else (1.25, 2.75, None)
    data = replace(
        DATA,
        columns=DATA.columns.replace("amount BIGINT NOT NULL", f"amount {sql_type}"),
        values=f"(1,'t1','A',{numbers[0]},NULL,1,DATE '2026-07-01'),(2,'t2','A',{numbers[1]},NULL,2,DATE '2026-07-01'),(3,'t3',NULL,NULL,NULL,3,DATE '2026-07-02')",
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
        session = mv.session.create("remote-native-fractions", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        metric = (
            amount.weighted_mean(weight=weight)
            if method == "weighted_mean"
            else getattr(amount, method)()
        )
        trace = capture_source(monkeypatch)
        frame = session.observe({"value": metric}).compute().to_pandas()
        assert frame["value", "value"].tolist() == [expected]
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
        session = mv.session.create("trino-connector-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()})
        native = _native_scalar(case, "sum")
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["value", "value"].tolist() == [2**53 + 1]
        assert trace.returned_rows == [1]
        assert frame["value", "value"].tolist() == [native]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_remote_weighted_native_boundary_and_fixed_wide_mean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = replace(
        DATA,
        values="(1,'t1','A',4611686018427387904,NULL,1,DATE '2026-07-01'),(2,'t2','B',4611686018427387904,NULL,1,DATE '2026-07-01')",
        rows=[
            {**DATA.rows[index], "category": category, "amount": 2**62}
            for index, category in enumerate(("A", "B"))
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.create("remote-original-wide-weighted", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        query = session.observe({"value": amount.weighted_mean(weight=weight)})
        table = case.session.bind(case.source, source_identity="native-unwidened").relation
        expression = (table.amount * table.weight).sum() / table.weight.sum()
        # This overall observation uses native Int64 accumulation: Trino errors,
        # while ClickHouse wraps. The fixed Frame below owns the exact wide mean.
        if backend == "trino":
            with pytest.raises(TrinoUserError) as native_failure:
                case.session._backend.execute(expression)
            assert native_failure.value.error_name == "NUMERIC_VALUE_OUT_OF_RANGE"
            assert native_failure.value.error_code == 19
        else:
            assert backend == "clickhouse"
            native: object = case.session._backend.execute(expression)
            assert native == -float(2**62)
        before_runs = session.runs().items
        before_publication = publication_counts(session)
        trace = capture_source(monkeypatch)
        if backend == "trino":
            with pytest.raises(MaterializationError) as failure:
                query.compute()
            cause = failure.value.__cause__
            assert isinstance(cause, TrinoUserError)
            assert cause.error_name == "NUMERIC_VALUE_OUT_OF_RANGE"
            assert cause.error_code == 19
            assert trace.returned_rows == []
        else:
            frame = query.compute().to_pandas()
            assert frame["value", "value"].tolist() == [native]
            assert trace.returned_rows == [1]
        assert len(trace.native_sql) == 1
        assert trace.uploaded_key_rows == []
        assert trace.owners and all(owner._closed for owner in trace.owners)
        submissions = [item for owner in trace.owners for item in owner.submissions]
        assert len(submissions) == 1
        assert submissions[0].state == ("failed" if backend == "trino" else "succeeded")
        assert submissions[0].connection_disconnected
        assert session.runs().items == before_runs
        assert publication_counts(session) == before_publication
        assert session._runtime.store.resources(session.id) == ()

        exact = Fraction(2**63, 2)
        assert exact == 2**62
        fixed = session.values(
            by=(category,),
            columns={"amount": None},
            rows=tuple(((key,), {"amount": 2**62}) for key in ("A", "B")),
        ).compute()
        # All source weights are one; this exact row mean is a separate witness.
        finished = fixed.reduce({"mean": fixed["amount"].mean()}).compute()
        assert finished.to_pandas()[("value", "mean")].tolist() == [float(exact)]


def _native_clickhouse_weighted_scalar(case: Case) -> float:
    table = case.session.bind(case.source, source_identity="native-weighted-oracle").relation
    table = table.filter(table.amount.notnull() & table.weight.notnull())
    expression = (table.amount * table.weight).sum() / table.weight.sum()
    native = float(case.session._backend.execute(expression))
    from marivo.datasource.ir import TableSourceIR
    from tests.datasource.environment.clickhouse_analysis import connection

    assert isinstance(case.source, TableSourceIR)
    # Independent native SQL uses the same uncast arithmetic on the source rows.
    with connection() as client:
        row = client.query(
            "SELECT sum(amount * weight) / sum(weight) "
            f"FROM qualification.{case.source.table} "
            "WHERE amount IS NOT NULL AND weight IS NOT NULL"
        ).first_row
    assert row is not None
    assert native == float(row[0])
    return native
