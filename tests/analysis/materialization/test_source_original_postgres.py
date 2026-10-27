"""PostgreSQL original rewrites retain exact physical integer fibers."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import Case, SourceData, source_case
from tests.shared_fixtures import catalog_value, terminal_runs
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
        yield mv.session.get_or_create("postgres-original", timezone="UTC"), case


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
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    metric = amount.quantile(0.9) if method == "quantile" else getattr(amount, method)()
    query = session.observe({"value": metric})
    native = _native_scalar(case, method)
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    if method == "sum":
        assert result["value"].tolist() == [2**53 + 7]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )
    assert result["value"].tolist() == [native]


def test_postgres_drill_preserves_large_integer_values_and_null_categories(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    from psycopg import sql

    from marivo.datasource.ir import TableSourceIR
    from tests.datasource.environment import postgres_analysis as pg

    session, case = postgres_original_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    query = session.observe({"value": amount.sum()}, by=(category, tenant))
    assert isinstance(case.source, TableSourceIR)
    assert isinstance(case.source.database, str)
    with pg.connection() as reader:
        native = reader.execute(
            sql.SQL(
                "SELECT category, tenant, SUM(amount) FROM {} "
                "GROUP BY category, tenant ORDER BY tenant, category NULLS FIRST"
            ).format(sql.Identifier(case.source.database, case.source.table))
        ).fetchall()
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    ordered = result.sort_values(result.columns[1])
    assert [
        tuple(None if pd.isna(value) else value for value in row)
        for row in ordered.itertuples(index=False, name=None)
    ] == native
    assert sorted(result["value"].tolist()) == [1, 2, 3, 2**53 + 1]
    assert result.iloc[:, 0].isna().sum() == 1
    assert 2**53 + 1 in result["value"].tolist()
    assert trace.returned_rows == [4]
    assert trace.uploaded_key_rows == []


def test_postgres_empty_original_scalar_returns_zero(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, case = postgres_original_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    query = session.observe({"value": amount.sum().where(amount > 2**63 - 1)})
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value"].tolist() == [0]
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
        session = mv.session.get_or_create("postgres-nullable-original", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        query = session.observe({"value": amount.count_distinct()})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value"].tolist() == [2]
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
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    metric = amount.mean() if method == "mean" else amount.weighted_mean(weight=weight)
    query = session.observe({"value": metric})
    native = _native_scalar(case, method)
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert trace.returned_rows == [1]
    assert not any(
        "__original_value" in column for columns in trace.returned_columns for column in columns
    )
    assert result["value"].tolist() == [native]
    assert native == float(2**53 + 7) / 4


def test_postgres_original_period_binding_preserves_selected_input(
    postgres_original_session: tuple[Session, Case], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, case = postgres_original_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    day = catalog_value(session.catalog, "sales.facts.day", kind="field")
    period = session.time_scope(start="2026-07-01", end="2026-07-02")
    query = session.observe({"value": amount.sum(time=day)}, by=(period,))
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["value"].tolist() == [2**53 + 4]
    assert trace.returned_rows == [1]


def test_postgres_native_sum_transport_failure_precedes_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
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
        session = mv.session.get_or_create("postgres-overflow-original", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        query = session.observe({"value": amount.sum()}, by=(category,))
        before = publication_counts(session)
        with pytest.raises(MaterializationError, match="tried to convert to int64"):
            query.compute()
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError, match="tried to convert to int64"):
            query.persist()
        assert publication_counts(session) == before
        assert trace.returned_rows == []
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
        session = mv.session.get_or_create("postgres-decimal-original", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        query = session.observe({"value": amount.count_distinct()})
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert result["value"].tolist() == [2]
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
                session = mv.session.get_or_create("postgres-collated-original", timezone="UTC")
                amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
                category = catalog_value(session.catalog, "sales.facts.category", kind="field")
                tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
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
                assert ordered["value"].tolist() == native.value.tolist() == [1, 2, 3]
                assert trace.returned_rows
                assert trace.qualifications
        finally:
            admin.execute(sql.SQL("DROP COLLATION {}").format(sql.Identifier(collation)))


def test_postgres_attribute_default_preserves_exact_values_and_fixed_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    from psycopg import sql

    from marivo.datasource.ir import TableSourceIR
    from tests.datasource.environment import postgres_analysis as pg

    data = SourceData(
        "id BIGINT NOT NULL, tenant CHAR(4) NOT NULL, category VARCHAR(4), amount BIGINT, big BIGINT, weight BIGINT NOT NULL, day DATE",
        "(1,'EAST','A',9007199254740993,NULL,1,DATE '2026-10-08'),"
        "(2,'EAST','B',80,NULL,1,DATE '2026-10-08'),"
        "(3,'EAST',NULL,NULL,NULL,1,DATE '2026-10-08'),"
        "(4,'EAST','A',9007199254740933,NULL,1,DATE '2026-10-07'),"
        "(5,'EAST','B',100,NULL,1,DATE '2026-10-07'),"
        "(6,'WEST','C',7,NULL,1,DATE '2026-10-08')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("postgres-attribute-default", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        day = catalog_value(session.catalog, "sales.facts.day", kind="field")
        metric = amount.sum(time=day)
        current = session.time_scope(start="2026-10-08", end="2026-10-09")
        baseline = session.time_scope(start="2026-10-07", end="2026-10-08")
        selection = session.observe({"q": metric}, by=(current, tenant))
        selection = selection.reduce({"q": selection["q"].sum()}, by=(selection.axes[1],))
        parents = selection.top(1, by=selection["q"]).compute()
        parent_rows = parents.to_pandas()
        assert parent_rows.iloc[:, 0].tolist() == ["EAST"]
        assert parent_rows["q"].tolist() == [9007199254741073]

        assert isinstance(case.source, TableSourceIR)
        assert isinstance(case.source.database, str)
        table = sql.Identifier(case.source.database, case.source.table)
        with pg.connection(admin=True) as admin:
            admin.execute(
                sql.SQL("UPDATE {} SET amount = %s WHERE tenant = %s AND id = %s").format(table),
                (2**54, "WEST", 6),
            )
        expected = [
            ("EAST", None, None, None, None, True, False),
            ("EAST", "A", 9007199254740993, 9007199254740933, 60, True, True),
            ("EAST", "B", 80, 100, -20, True, True),
        ]
        with pg.connection() as reader:
            reranked = reader.execute(
                sql.SQL(
                    "SELECT tenant, SUM(amount)::BIGINT AS q FROM {} "
                    "WHERE day >= DATE '2026-10-08' AND day < DATE '2026-10-09' "
                    "GROUP BY tenant ORDER BY q DESC, tenant LIMIT 1"
                ).format(table)
            ).fetchall()
            assert reranked == [("WEST", 2**54)]
            native = reader.execute(
                sql.SQL(
                    "WITH current_groups AS ("
                    "SELECT tenant, category, SUM(amount)::BIGINT AS q, COUNT(*) AS n "
                    "FROM {table} WHERE tenant = 'EAST' "
                    "AND day >= DATE '2026-10-08' AND day < DATE '2026-10-09' "
                    "GROUP BY tenant, category), baseline_groups AS ("
                    "SELECT tenant, category, SUM(amount)::BIGINT AS q, COUNT(*) AS n "
                    "FROM {table} WHERE tenant = 'EAST' "
                    "AND day >= DATE '2026-10-07' AND day < DATE '2026-10-08' "
                    "GROUP BY tenant, category), keys AS ("
                    "SELECT tenant, category FROM current_groups UNION "
                    "SELECT tenant, category FROM baseline_groups) "
                    "SELECT k.tenant, k.category, c.q, b.q, (c.q - b.q)::BIGINT, "
                    "c.n IS NOT NULL, b.n IS NOT NULL FROM keys k "
                    "LEFT JOIN current_groups c ON c.tenant = k.tenant "
                    "AND c.category IS NOT DISTINCT FROM k.category "
                    "LEFT JOIN baseline_groups b ON b.tenant = k.tenant "
                    "AND b.category IS NOT DISTINCT FROM k.category "
                    "ORDER BY k.tenant, k.category NULLS FIRST"
                ).format(table=table)
            ).fetchall()
        assert native == expected

        trace = capture_source(monkeypatch)
        assert terminal_runs(session) == ()
        keys = session.population_from(parents, on=((parents.axes[0], tenant),))
        left = session.observe(
            {"q": metric}, by=(current, tenant, category), where=mv.in_population(keys)
        )
        right = session.observe(
            {"q": metric}, by=(baseline, tenant, category), where=mv.in_population(keys)
        )
        compared = left.compare(
            {
                "reference": mv.baseline(
                    right, on=tuple(zip(left.axes, right.axes, strict=True)), match=mv.ordinal()
                )
            }
        )
        pending = compared.select(
            {
                "left_q": compared["q"],
                "right_q": compared["reference.baseline.q"],
                "absolute_q": compared["reference.absolute.q"],
            }
        )
        assert not trace.native_sql and not trace.returned_rows and not trace.uploaded_key_rows
        assert terminal_runs(session) == ()
        pending.show(n=0)
        assert not trace.native_sql and not trace.returned_rows and not trace.uploaded_key_rows
        assert terminal_runs(session) == ()
        assert pending.output_names == ("left_q", "right_q", "absolute_q")
        assert pending.presence_names == ("current.present", "reference.present")

        fixed = pending.compute()
        assert isinstance(fixed, mv.AnalysisFrame)
        assert trace.native_sql and trace.returned_rows
        completed_reads = (tuple(trace.native_sql), tuple(trace.returned_rows))
        rows = fixed.to_pandas()
        assert (tuple(trace.native_sql), tuple(trace.returned_rows)) == completed_reads
        assert terminal_runs(session) == ()
        assert fixed.method_facts == ()
        ordered = rows.sort_values("sales.facts.category", na_position="first", kind="stable")
        actual = [
            tuple(None if pd.isna(value) else value for value in row)
            for row in ordered[
                [
                    "sales.facts.tenant",
                    "sales.facts.category",
                    *fixed.output_names,
                    *fixed.presence_names,
                ]
            ].itertuples(index=False, name=None)
        ]
        assert actual == native == expected
        for name in fixed.output_names:
            values = rows[name]
            assert values.dtype == pd.ArrowDtype(pa.int64())
            assert all(isinstance(value, int) for value in values.dropna())
        for name in fixed.presence_names:
            presence = rows[name]
            assert pd.api.types.is_bool_dtype(presence.dtype)
            assert not presence.isna().any()
        trace.record(fixed)
        schema = trace.frames[-1]["schema"]
        assert isinstance(schema, dict)
        value_columns = schema["values"]
        presence_columns = schema["presence"]
        assert isinstance(value_columns, list) and len(value_columns) == 3
        assert isinstance(presence_columns, list) and len(presence_columns) == 2
        for column in value_columns:
            assert isinstance(column, dict)
            assert (column["dtype"], column["nullable"], column["unit"]) == (
                {"name": "int64"},
                True,
                None,
            )
        for column in presence_columns:
            assert isinstance(column, dict)
            assert (column["dtype"], column["nullable"]) == ({"name": "boolean"}, False)

        current_parent = session.observe(
            {"q": metric},
            by=(current, tenant),
            where=mv.in_population(keys) & ~amount.isnull(),
        )
        baseline_parent = session.observe(
            {"q": metric},
            by=(baseline, tenant),
            where=mv.in_population(keys) & ~amount.isnull(),
        )
        parent_comparison = current_parent.compare(
            {
                "reference": mv.baseline(
                    baseline_parent,
                    on=tuple(zip(current_parent.axes, baseline_parent.axes, strict=True)),
                    match=mv.ordinal(),
                )
            }
        )
        target = parent_comparison.select(
            {"change": parent_comparison["reference.absolute.q"]}
        ).compute()
        observed = target.to_pandas()
        assert observed["change"].tolist() == [40]
        with pg.connection(admin=True) as admin:
            admin.execute(
                sql.SQL("UPDATE {} SET amount = amount + 20 WHERE tenant = %s AND id = %s").format(
                    table
                ),
                ("EAST", 1),
            )
        report = target.attribute(by=(category,)).compute()
        attributed = report.to_pandas()
        assert list(
            attributed[["sales.facts.tenant", "sales.facts.category"]].itertuples(
                index=False, name=None
            )
        ) == [
            ("EAST", "A"),
            ("EAST", "B"),
        ]
        assert attributed["detail.current.change"].tolist() == [
            9007199254741013,
            80,
        ]
        assert attributed["contribution.change"].tolist() == [80, -20]
        assert attributed["total.change"].tolist() == [60, 60]
        assert attributed["observed_total.change"].tolist() == [40, 40]
        assert attributed["contribution_share.change"].tolist() == pytest.approx([4 / 3, -1 / 3])
        pd.testing.assert_frame_equal(target.to_pandas(), observed)
        assert terminal_runs(session) == ()


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
    # The oracle is also consumed by MySQL cases; submit its native dialect
    # through the actual fixture backend rather than a PostgreSQL connection.
    if case.session.provider.name == "clickhouse":
        native_rows = case.session._backend.raw_sql(
            case.session._backend.compile(expression)
        ).result_rows
        row = native_rows[0] if native_rows else None
    else:
        with case.session._backend.raw_sql(case.session._backend.compile(expression)) as reader:
            row = reader.fetchone()
    assert row is not None and len(row) == 1
    result = row[0]
    if result is None:
        return None
    if isinstance(result, Decimal):
        return float(result) if method in ("mean", "weighted_mean") else result
    if isinstance(result, Integral):
        return int(result)
    if isinstance(result, Real):
        return float(result)
    raise TypeError(f"Expected a native numerical scalar, received {type(result).__name__}")
