"""MySQL-family Attribute strings retain native source comparison semantics."""

import os
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.predicate import native_predicate_equality
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.source_cases import Case, SourceData, source_case
from tests.shared_fixtures import catalog_value

SERVERS = [
    pytest.param(23306, "MARIVO_MYSQL_ANALYSIS_TEST", id="mysql-84", marks=pytest.mark.runtime),
    pytest.param(
        23307, "MARIVO_MARIADB_ANALYSIS_TEST", id="mariadb-114", marks=pytest.mark.runtime
    ),
    pytest.param(
        23309, "MARIVO_MARIADB_103_ANALYSIS_TEST", id="mariadb-103", marks=pytest.mark.runtime
    ),
]


@pytest.fixture
def collation() -> str | None:
    return None


@pytest.fixture
def amount_type() -> str:
    return "BIGINT"


@pytest.fixture
def string_case(
    port: int,
    flag: str,
    collation: str | None,
    amount_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Iterator[Case]:
    if os.environ.get(flag) != "1":
        pytest.skip(f"Explicit ready service required: {flag}=1")
    monkeypatch.setenv("MARIVO_MYSQL_ANALYSIS_TEST", "1")
    monkeypatch.setattr(mysql, "connection", partial(mysql.connection, port=port))
    monkeypatch.setattr(mysql, "setup", partial(mysql.setup, port=port))
    monkeypatch.setattr(mysql, "PORT", port)
    suffix = " CHARACTER SET utf8mb4" + ("" if collation is None else f" COLLATE {collation}")
    values = []
    for phase, offset in (("base", 0), ("current", 10)):
        for identity, category, amount, weight in (
            (1, "'A'", 1 if phase == "base" else 3, 1),
            (2, "'a'", 2, 1),
            (3, "'a '", 4, 2),
            (4, "''", 8, 2),
            (5, "'雪'", 16, 4),
            (6, "NULL", 32, 4),
            (7, "'B'", 64, 8),
        ):
            values.append(
                f"({identity + offset},'{phase}',{category},{amount},1,{weight},'2026-07-01')"
            )
    data = SourceData(
        f"id BIGINT NOT NULL, tenant VARCHAR(10){suffix} NOT NULL, "
        f"category VARCHAR(10){suffix}, amount {amount_type}, big BIGINT, weight BIGINT, day DATE",
        ",".join(values),
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, data) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        yield case


@pytest.mark.parametrize("port,flag", SERVERS)
@pytest.mark.parametrize("collation", (None, "utf8mb4_bin"))
def test_mysql_attribute_groups_and_metrics_follow_native_collation(string_case: Case) -> None:
    session = mv.session.get_or_create("mysql-native-strings", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    parent = session.observe(
        {"sum": amount.sum(), "mean": amount.mean(), "ratio": amount.sum() / weight.sum()},
        where=tenant == "base",
    ).compute()
    rows = parent.attribute(by=(tenant, category)).compute().to_pandas()
    assert isinstance(string_case.source, TableSourceIR)
    with mysql.connection() as con, con.cursor() as cursor:
        cursor.execute(
            f"SELECT category FROM `{string_case.source.table}` "
            "WHERE tenant = 'base' GROUP BY tenant, category"
        )
        assert len(rows) == len(cursor.fetchall())
        members = []
        for index, row in rows.iterrows():
            label = row.iloc[1]
            cursor.execute(
                "SELECT GROUP_CONCAT(id ORDER BY id), SUM(amount), COUNT(amount), SUM(weight) "
                f"FROM `{string_case.source.table}` WHERE tenant <=> %s AND category <=> %s",
                (row.iloc[0], None if pd.isna(label) else label),
            )
            native = cursor.fetchone()
            assert native is not None
            identities, total, count, weights = native
            members.append(identities)
            assert rows.loc[index, "detail.value.sum"] == total
            assert float(rows.loc[index, "detail.value.mean"]) == pytest.approx(
                float(total) / count
            )
            assert float(rows.loc[index, "detail.value.ratio"]) == pytest.approx(
                float(total / weights)
            )
            assert float(rows.loc[index, "contribution.mean"]) == pytest.approx(float(total) / 7)
    assert len(set(members)) == len(members)
    assert rows["contribution.sum"].sum() == 127
    assert rows["contribution.mean"].sum() == pytest.approx(127 / 7)
    assert rows["contribution.ratio"].sum() == pytest.approx(127 / 22)


@pytest.mark.parametrize("port,flag", SERVERS)
@pytest.mark.parametrize("amount_type", ("DECIMAL(18,2)",))
def test_mysql_attribute_paired_shapley_keeps_native_groups(string_case: Case) -> None:
    session = mv.session.get_or_create("mysql-native-shapley", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    metrics = {
        "product": amount.sum() * amount.count(),
        "distinct": category.count_distinct() * amount.sum(),
    }
    current = session.observe(metrics, where=tenant == "current")
    baseline = session.observe(metrics, where=tenant == "base")
    compared = current.compare({"base": mv.baseline(baseline, on=())}).compute()
    selected = compared.select(
        {
            "change": compared["base.absolute.product"],
            "distinct_change": compared["base.absolute.distinct"],
        }
    )
    report = selected.attribute(by=(category,), permutations=2, seed=23).compute()
    rows = report.to_pandas()
    assert isinstance(string_case.source, TableSourceIR)
    with mysql.connection() as con, con.cursor() as cursor:
        cursor.execute(
            f"SELECT category FROM `{string_case.source.table}` "
            "WHERE tenant = 'base' GROUP BY category"
        )
        assert len(rows) == len(cursor.fetchall())
        cursor.execute(
            f"SELECT COUNT(DISTINCT category) FROM `{string_case.source.table}` "
            "WHERE tenant = 'base'"
        )
        cardinality = cursor.fetchone()
        assert cardinality is not None
    assert rows["contribution.change"].sum() == pytest.approx(14)
    assert sorted(rows["contribution.change"].tolist()) == pytest.approx(
        [0] * (len(rows) - 1) + [14]
    )
    assert sorted(rows["contribution.distinct_change"].tolist()) == pytest.approx(
        [0] * (len(rows) - 1) + [2 * cardinality[0]]
    )
    assert all(item.method.kind == "shapley" for item in report.method_facts[0].parents[0].metrics)


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_attribute_population_and_parent_subset_use_native_membership(
    string_case: Case,
) -> None:
    session = mv.session.get_or_create("mysql-native-population", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    members = mv.coordinates(by=(category,), keys=(("a",),))
    observed = session.observe(
        {"sum": amount.sum()}, by=(tenant,), where=mv.in_population(members)
    ).compute()
    parent = observed.where(observed.coord(observed.axes[0]) == "base").compute()
    rows = parent.attribute(by=(category,)).compute().to_pandas()
    assert isinstance(string_case.source, TableSourceIR)
    with mysql.connection() as con, con.cursor() as cursor:
        cursor.execute(
            f"SELECT SUM(amount) FROM `{string_case.source.table}` "
            "WHERE tenant = 'base' AND category = 'a'"
        )
        native = cursor.fetchone()
        assert native is not None
    assert len(rows) == 1
    assert rows["contribution.sum"].tolist() == [native[0]]
    assert rows["total.sum"].tolist() == [native[0]]


@pytest.mark.parametrize("port,flag", SERVERS)
@pytest.mark.parametrize("amount_type", ("DECIMAL(18,2)",))
def test_mysql_attribute_alignment_uses_returned_representatives(string_case: Case) -> None:
    assert isinstance(string_case.source, TableSourceIR)
    with mysql.connection(admin=True) as con, con.cursor() as cursor:
        cursor.execute(
            f"UPDATE `{string_case.source.table}` "
            "SET category = CASE WHEN tenant = 'base' THEN 'A' ELSE 'a' END "
            "WHERE category = 'a'"
        )
        cursor.execute(
            f"SELECT tenant, SUM(amount) FROM `{string_case.source.table}` "
            "WHERE category = 'a' GROUP BY tenant"
        )
        totals = dict(cursor.fetchall())
    session = mv.session.get_or_create("mysql-returned-strings", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    current = session.observe({"sum": amount.sum()}, where=tenant == "current")
    baseline = session.observe({"sum": amount.sum()}, where=tenant == "base")
    compared = current.compare({"base": mv.baseline(baseline, on=())}).compute()
    selected = compared.select({"change": compared["base.absolute.sum"]})
    rows = selected.attribute(by=(category,)).compute().to_pandas()
    actual = dict(zip(rows.iloc[:, 0], rows["contribution.change"], strict=True))
    assert actual["A"] == -totals["base"]
    assert actual["a"] == totals["current"]
    assert rows["contribution.change"].sum() == 2


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_attribute_source_condition_keeps_native_equality(string_case: Case) -> None:
    session = mv.session.get_or_create("mysql-native-condition", timezone="UTC")
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    predicate = (tenant == "BASE") & ~(category == "b")
    parent = session.observe({"sum": amount.sum()}, where=predicate).compute()
    # The native filter excludes both B and NULL. Freezing and re-reading
    # the same facts must apply the same condition at both boundaries.
    assert parent.to_pandas()["sum"].tolist() == [31]
    rows = parent.attribute(by=(category,)).compute().to_pandas()
    assert rows["contribution.sum"].sum() == 31
    assert rows["total.sum"].tolist() == [31] * len(rows)


@pytest.mark.parametrize("port,flag", SERVERS)
def test_mysql_attribute_string_equality_preserves_sql_nulls(string_case: Case) -> None:
    import ibis

    table = string_case.session.bind(string_case.source, source_identity="native-equality").relation
    equal = table.category == "a"
    expression = table.select(
        "id",
        equal=native_predicate_equality(equal),
        negated=native_predicate_equality(~equal),
        null_equal=native_predicate_equality(table.category == ibis.null().cast("string")),
    ).order_by("id")
    rows = string_case.session.collect_bounded(
        expression, purpose="attribute.native_equality", max_rows=14
    )
    assert isinstance(string_case.source, TableSourceIR)
    with mysql.connection() as con, con.cursor() as cursor:
        cursor.execute(
            "SELECT id, category = 'a', NOT(category = 'a'), category = CAST(NULL AS CHAR) "
            f"FROM `{string_case.source.table}` ORDER BY id"
        )
        expected = cursor.fetchall()
    assert (
        tuple(
            (identity, *(None if pd.isna(value) else bool(value) for value in values))
            for identity, *values in rows.itertuples(index=False, name=None)
        )
        == expected
    )
