"""PostgreSQL original rewrites retain exact physical integer fibers."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import Case, SourceData, source_case
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime


@pytest.fixture
def postgres_original_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Iterator[tuple[Session, Case]]:
    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category CHAR(4), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',1,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','B',2,NULL,1,DATE '2026-07-01'),"
        "(4,'t4',NULL,3,NULL,1,DATE '2026-07-02')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        yield mv.session.create("postgres-original", timezone="UTC"), case


@pytest.mark.parametrize(
    "method", ("sum", "count", "count_distinct", "min", "max", "median", "quantile")
)
def test_postgres_integer_original_rollup_preserves_native_values(
    postgres_original_session: tuple[Session, Case],
    monkeypatch: pytest.MonkeyPatch,
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
    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    metric = amount.quantile(0.9) if method == "quantile" else getattr(amount, method)()
    query = session.observe({"value": metric})
    native = _native_scalar(case, method)
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    if method == "sum":
        assert result["value", "value"].tolist() == [2**53 + 7]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )
    assert result["value", "value"].tolist() == [native]


def test_postgres_drill_preserves_large_integer_values_and_null_categories(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    query = session.observe({"value": amount.sum()}, by=(category, tenant))
    native_table = case.session.bind(case.source, source_identity="complete-source-oracle").relation
    native = case.session._backend.execute(
        native_table.group_by("category", "tenant")
        .aggregate(value=native_table.amount.sum())
        .order_by("tenant")
    )
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    ordered = result.sort_values(result.columns[1])
    assert ordered.iloc[:, 0].fillna("<NULL>").tolist() == native.category.fillna("<NULL>").tolist()
    assert ordered.iloc[:, 1].tolist() == native.tenant.tolist()
    assert ordered[("value", "value")].tolist() == native.value.tolist()
    assert sorted(result[("value", "value")].tolist()) == [1, 2, 3, 2**53 + 1]
    assert result.iloc[:, 0].isna().sum() == 1
    assert 2**53 + 1 in result["value", "value"].tolist()
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []


def test_postgres_empty_original_scalar_preserves_null(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    query = session.observe({"value": amount.sum().where(amount > 2**63 - 1)})
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value", "value"].isna().all()
    assert trace.returned_rows == [1]


def test_postgres_nullable_integer_distinct_recomputes_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category CHAR(4), amount BIGINT, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),(3,'t3','A',NULL,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("postgres-nullable-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value", "value"].tolist() == [2]
        assert trace.returned_rows == [1]
        assert not any(
            "__original_value" in column for columns in trace.returned_columns for column in columns
        )


@pytest.mark.parametrize("method", ("mean", "weighted_mean"))
def test_postgres_integer_means_use_native_compact_states(
    postgres_original_session: tuple[Session, Case],
    monkeypatch: pytest.MonkeyPatch,
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
    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    category = session.catalog.fields.get("sales.facts.category")
    metric = amount.mean() if method == "mean" else amount.weighted_mean(weight=weight)
    query = session.observe({"value": metric})
    native = _native_scalar(case, method)
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert trace.returned_rows == [1]
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )
    assert result["value", "value"].tolist() == [native]
    assert native == float(2**53 + 7) / 4


def test_postgres_original_period_binding_preserves_selected_input(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    day = session.catalog.fields.get("sales.facts.day")
    period = session.time_scope(start="2026-07-01", end="2026-07-02")
    query = session.observe({"value": amount.sum(time=day)}, by=(period,))
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value", "value"].tolist() == [2**53 + 4]
    assert trace.returned_rows == [1]


def test_postgres_original_parent_overflow_prevents_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from marivo.analysis.errors import AnalysisError
    from tests.analysis.materialization.publication_fixtures import publication_counts

    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category CHAR(4), amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',9223372036854775807,NULL,1,DATE '2026-07-01'),(2,'t2','A',1,NULL,1,DATE '2026-07-01'),(3,'t3','B',-9223372036854775808,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("postgres-overflow-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()}, by=(category,))
        before = publication_counts(session)
        with pytest.raises(AnalysisError, match="signed int64 sum"):
            query.compute()
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="signed int64 sum"):
            query.persist()
        assert publication_counts(session) == before
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []


def test_postgres_decimal_distinct_recomputes_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category CHAR(4), amount DECIMAL(30,2) NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',9007199254740993.01,NULL,1,DATE '2026-07-01'),(2,'t2','A',9007199254740993.02,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.create("postgres-decimal-original", timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value", "value"].tolist() == [2]
        assert trace.returned_rows == [1]


def test_postgres_nondeterministic_coordinate_collation_keeps_parent_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    import os
    from uuid import uuid4

    from psycopg import sql

    from tests.datasource.environment import postgres_analysis as pg

    if os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1":
        pytest.skip("Explicit ready PostgreSQL service required")
    collation = "original_" + uuid4().hex
    with pg.connection(admin=True) as admin:
        admin.execute(
            sql.SQL(
                "CREATE COLLATION {} (provider=icu,locale='und-u-ks-level2',deterministic=false)"
            ).format(sql.Identifier(collation))
        )
        try:
            data = SourceData(
                f'id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category VARCHAR(10) COLLATE "{collation}", amount BIGINT NOT NULL, big BIGINT, weight BIGINT NOT NULL, day DATE',
                "(1,'t1','A',1,NULL,1,DATE '2026-07-01'),(2,'t2','a',2,NULL,1,DATE '2026-07-01'),(3,'t3','a',3,NULL,1,DATE '2026-07-01')",
                "",
                [],
            )
            monkeypatch.chdir(tmp_path)
            with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
                _project("postgres", case, monkeypatch, semantic_project_factory)
                session = mv.session.create("postgres-collated-original", timezone="UTC")
                amount = session.catalog.fields.get("sales.facts.amount")
                category = session.catalog.fields.get("sales.facts.category")
                tenant = session.catalog.fields.get("sales.facts.tenant")
                query = session.observe({"value": amount.sum()}, by=(category, tenant))
                native_table = case.session.bind(
                    case.source, source_identity="collation-oracle"
                ).relation
                native = case.session._backend.execute(
                    native_table.group_by("category", "tenant")
                    .aggregate(value=native_table.amount.sum())
                    .order_by("tenant")
                )
                trace = capture_source(monkeypatch)
                result = query.compute().to_pandas()
                ordered = result.sort_values(result.columns[1])
                assert ordered.iloc[:, 0].tolist() == native.category.tolist() == ["A", "a", "a"]
                assert ordered.iloc[:, 1].tolist() == native.tenant.tolist()
                assert ordered[("value", "value")].tolist() == native.value.tolist() == [1, 2, 3]
                assert trace.returned_rows
                assert trace.qualifications
        finally:
            admin.execute(sql.SQL("DROP COLLATION {}").format(sql.Identifier(collation)))


def test_postgres_original_reuses_invocation_metadata_across_occurrences(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.capabilities import provider_statement_log

    session, case = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    query = session.observe(
        {
            "sum": amount.sum(),
            "count": amount.count(),
            "distinct": amount.count_distinct(),
            "min": amount.min(),
            "max": amount.max(),
        }
    )
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result[("value", "sum")].tolist() == [2**53 + 7]
    assert result[("value", "count")].tolist() == [4]
    assert result[("value", "distinct")].tolist() == [4]
    assert result[("value", "min")].tolist() == [1]
    assert result[("value", "max")].tolist() == [2**53 + 1]
    assert trace.returned_rows == [1]
    statements = [
        entry.statement_id
        for owner in trace.owners
        for entry in provider_statement_log(owner._backend)
    ]
    assert "postgres.tables.kind" not in statements
    assert statements.count("postgres.column.collation") == 2


def _native_scalar(
    case: Case,
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
) -> int | float | Decimal | None:
    table = case.session.bind(case.source, source_identity="native-oracle").relation
    if method == "weighted_mean":
        table = table.filter(table.amount.notnull() & table.weight.notnull())
        expression = (
            table.amount.cast("decimal(38,0)") * table.weight.cast("decimal(38,0)")
        ).sum() / table.weight.cast("decimal(38,0)").sum()
    elif method == "quantile":
        expression = table.amount.quantile(0.9)
    elif method == "mean":
        expression = table.amount.mean()
    elif method == "sum":
        expression = table.amount.sum()
    elif method == "count":
        expression = table.amount.count()
    elif method == "count_distinct":
        expression = table.amount.nunique()
    elif method == "min":
        expression = table.amount.min()
    elif method == "max":
        expression = table.amount.max()
    else:
        expression = table.amount.median()
    result = case.session._backend.execute(expression)
    if result is None:
        return None
    if isinstance(result, Decimal):
        return float(result) if method in ("mean", "weighted_mean") else result
    if isinstance(result, Integral):
        return int(result)
    if isinstance(result, Real):
        return float(result)
    raise TypeError(f"Expected a native numerical scalar, received {type(result).__name__}")
