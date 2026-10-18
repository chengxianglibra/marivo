"""PostgreSQL original rewrites retain exact physical integer fibers."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_lowering import LoweredQuery
from marivo.analysis.materialization import source_original
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime


@pytest.fixture
def postgres_original_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Iterator[Session]:
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
        yield mv.session.get_or_create("postgres-original", report_timezone="UTC")


def _unoptimized(query: mv.Query, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    def unchanged(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
        return lowered

    with monkeypatch.context() as scoped:
        scoped.setattr(source_original, "fuse_source_original", unchanged)
        return query.execute().to_pandas()


@pytest.mark.parametrize(
    "method", ("sum", "count", "count_distinct", "min", "max", "median", "quantile")
)
def test_postgres_integer_original_rollup_preserves_native_values(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    metric = amount.quantile(0.9) if method == "quantile" else getattr(amount, method)()
    query = session.observe({"value": metric}, by=(category,)).rollup()
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    if method == "sum":
        assert result[("value", "value")].tolist() == [2**53 + 7]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )


def test_postgres_drill_preserves_large_integer_values_and_null_categories(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    query = session.observe({"value": amount.sum()}, by=(category,)).drill(add=(tenant,))
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert 2**53 + 1 in result[("value", "value")].tolist()
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []


def test_postgres_share_finishes_exact_integer_division_on_host(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    query = session.observe({"value": amount.sum()}, by=(category,)).share("value")
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert trace.returned_rows == [3]
    assert trace.uploaded_key_rows == []


def test_postgres_empty_original_scalar_preserves_null(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    query = session.observe(
        {"value": amount.sum().where(amount > 2**63 - 1)}, by=(category,)
    ).rollup()
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert result[("value", "value")].isna().all()
    assert trace.returned_rows == [1]


def test_postgres_nullable_integer_distinct_recomputes_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category CHAR(4), amount BIGINT, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'t1','A',9007199254740993,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',9007199254740992,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','A',NULL,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("postgres-nullable-original", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        expected[("value", "value")] = pd.Series(
            2, index=expected.index, dtype=expected[("value", "value")].dtype
        )
        trace = capture_source(monkeypatch)
        result = query.execute().to_pandas()
        pd.testing.assert_frame_equal(result, expected)
        assert result[("value", "value")].tolist() == [2]
        assert trace.returned_rows == [1]
        assert not any(
            "__original_value" in column for columns in trace.returned_columns for column in columns
        )


@pytest.mark.parametrize("method", ("mean", "weighted_mean"))
def test_postgres_integer_means_use_native_compact_states(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    weight = session.catalog.fields.get("sales.facts.weight")
    category = session.catalog.fields.get("sales.facts.category")
    metric = amount.mean() if method == "mean" else amount.weighted_mean(weight=weight)
    query = session.observe({"value": metric}, by=(category,)).rollup()
    expected = _unoptimized(query, monkeypatch)
    expected[("value", "value")] = pd.Series(
        float(2**53 + 7) / 4, index=expected.index, dtype=expected[("value", "value")].dtype
    )
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected, check_exact=True)
    assert trace.returned_rows == [1]
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )


def test_postgres_original_period_binding_preserves_selected_input(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    day = session.catalog.fields.get("sales.facts.day")
    period = session.time_scope(start="2026-07-01", end="2026-07-02")
    query = session.observe({"value": amount.sum(time=day)}, by=(category,), during=period).rollup()
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert result[("value", "value")].tolist() == [2**53 + 4]
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
        "(1,'t1','A',9223372036854775807,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',1,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','B',-9223372036854775808,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("postgres-overflow-original", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.sum()}, by=(category,)).rollup()
        before = publication_counts(session)
        with pytest.raises(AnalysisError, match="signed int64 sum"):
            _unoptimized(query, monkeypatch)
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError, match="signed int64 sum"):
            query.execute()
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
        "(1,'t1','A',9007199254740993.01,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',9007199254740993.02,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("postgres-decimal-original", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe({"value": amount.count_distinct()}, by=(category,)).rollup()
        expected = _unoptimized(query, monkeypatch)
        expected[("value", "value")] = pd.Series(
            2, index=expected.index, dtype=expected[("value", "value")].dtype
        )
        trace = capture_source(monkeypatch)
        result = query.execute().to_pandas()
        pd.testing.assert_frame_equal(result, expected)
        assert result[("value", "value")].tolist() == [2]
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
                "(1,'t1','A',1,NULL,1,DATE '2026-07-01'),"
                "(2,'t2','a',2,NULL,1,DATE '2026-07-01'),"
                "(3,'t3','a',3,NULL,1,DATE '2026-07-01')",
                "",
                [],
            )
            monkeypatch.chdir(tmp_path)
            with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
                _project("postgres", case, monkeypatch, semantic_project_factory)
                session = mv.session.get_or_create(
                    "postgres-collated-original", report_timezone="UTC"
                )
                amount = session.catalog.fields.get("sales.facts.amount")
                category = session.catalog.fields.get("sales.facts.category")
                tenant = session.catalog.fields.get("sales.facts.tenant")
                query = session.observe({"value": amount.sum()}, by=(category,)).drill(
                    add=(tenant,)
                )
                expected = _unoptimized(query, monkeypatch)
                trace = capture_source(monkeypatch)
                result = query.execute().to_pandas()
                pd.testing.assert_frame_equal(result, expected)
                assert trace.returned_rows == [3, 3]
                assert "UNNEST" in trace.native_sql[-1].upper()
        finally:
            admin.execute(sql.SQL("DROP COLLATION {}").format(sql.Identifier(collation)))


def test_postgres_original_reuses_invocation_metadata_across_occurrences(
    postgres_original_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.capabilities import provider_statement_log

    session = postgres_original_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    query = session.observe(
        {
            "sum": amount.sum(),
            "count": amount.count(),
            "distinct": amount.count_distinct(),
            "min": amount.min(),
            "max": amount.max(),
        },
        by=(category,),
    ).rollup()
    expected = _unoptimized(query, monkeypatch)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert trace.returned_rows == [1]
    statements = [
        entry.statement_id
        for owner in trace.owners
        for entry in provider_statement_log(owner._backend)
    ]
    assert "postgres.tables.kind" not in statements
    assert statements.count("postgres.column.collation") == 2
