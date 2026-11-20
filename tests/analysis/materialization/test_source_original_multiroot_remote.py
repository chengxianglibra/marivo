"""Same-owner originals reduce each root before native key alignment."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pyarrow as pa
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
from marivo.analysis.compiler.native_formula import align_native_inputs
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

CASES = [
    ("duckdb", "table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
]


def test_domain_alignment_preserves_null_keys_without_full_outer_join() -> None:
    connection = ibis.duckdb.connect()
    try:
        left = connection.create_table(
            "left_values", pd.DataFrame({"key": ["A", None], "x": [10, 20]})
        )
        right = connection.create_table(
            "right_values", pd.DataFrame({"key": ["B", None], "y": [30, 40]})
        )
        aligned = align_native_inputs(
            (left, right), ("key",), ("x", "y"), provider="duckdb", alignment="domain"
        )
        frame = connection.execute(aligned.order_by(aligned.key.asc(nulls_first=True)))
        assert frame.x.tolist() == [20, 10, 0]
        assert frame.y.tolist() == [40, 0, 30]
        sql = ibis.to_sql(
            align_native_inputs(
                (left, right), ("key",), ("x", "y"), provider="mysql", alignment="domain"
            ),
            dialect="mysql",
        ).upper()
        assert "UNION" in sql and "LEFT OUTER JOIN" in sql
    finally:
        connection.disconnect()


def test_domain_alignment_keeps_complete_keys_and_each_branch_presence() -> None:
    connection = ibis.duckdb.connect()
    try:
        connection.raw_sql(
            "CREATE TABLE first_values (label VARCHAR COLLATE NOCASE, bucket SMALLINT NOT NULL, "
            "w BIGINT); "
            "INSERT INTO first_values VALUES ('A', 1, 10), (NULL, 1, NULL); "
            "CREATE TABLE second_values (label VARCHAR COLLATE NOCASE, bucket BIGINT, x BIGINT); "
            "INSERT INTO second_values VALUES "
            "('B', 2, 20), (NULL, 1, 30), ('A ', 1, 40), ('B', NULL, 80); "
            "CREATE TABLE third_values (label VARCHAR COLLATE NOCASE, bucket BIGINT, y BIGINT); "
            f"INSERT INTO third_values VALUES ('a', 1, 50), ('A', 1, 60), ('B', {2**40}, 70), "
            "('B', NULL, 90); "
            "CREATE TABLE empty_values (label VARCHAR COLLATE NOCASE, bucket INTEGER, z BIGINT)"
        )
        aligned = align_native_inputs(
            tuple(
                connection.table(name)
                for name in ("first_values", "second_values", "third_values", "empty_values")
            ),
            ("label", "bucket"),
            ("w", "x", "y", "z"),
            provider="duckdb",
            alignment="domain",
        )
        result = connection.to_pyarrow(aligned)
        assert result.schema.types == [pa.string()] + [pa.int64()] * 5
        rows = result.to_pylist()
        assert len(rows) == 7
        assert {
            (row["label"], row["bucket"]): (row["w"], row["x"], row["y"], row["z"]) for row in rows
        } == {
            ("A", 1): (10, 0, 60, 0),
            ("B", 2): (0, 20, 0, 0),
            (None, 1): (None, 30, 0, 0),
            ("A ", 1): (0, 40, 0, 0),
            ("a", 1): (0, 0, 50, 0),
            ("B", 2**40): (0, 0, 70, 0),
            ("B", None): (0, 80, 90, 0),
        }
    finally:
        connection.disconnect()


@pytest.mark.parametrize("backend", ("trino", "clickhouse"))
@pytest.mark.parametrize(
    ("key_type", "deduplication"), (("int64", "distinct"), ("string", "group"))
)
def test_domain_alignment_deduplicates_the_union_of_four_key_branches(
    backend: str,
    key_type: str,
    deduplication: str,
) -> None:
    tables = tuple(
        ibis.table({"key": key_type, f"value_{index}": "int64"}, name=f"reduced_{index}")
        for index in range(4)
    )
    aligned = align_native_inputs(tables, ("key",), (), provider=backend, alignment="domain")
    sql = sqlglot.parse_one(ibis.to_sql(aligned, dialect=backend), read=backend)
    unions = list(sql.find_all(sge.Union))
    assert len(unions) == 3
    assert all(union.args.get("distinct") is False for union in unions)
    deduplications = [
        select for select in sql.find_all(sge.Select) if select.args.get(deduplication)
    ]
    assert len(deduplications) == 1
    assert len(list(deduplications[0].find_all(sge.Union))) == 3
    assert [join.side for join in sql.find_all(sge.Join)] == ["LEFT"] * 4


@contextmanager
def _session(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    boundary: str = "ordinary",
) -> Iterator[Session]:
    rows: list[dict[str, object]] = [
        {
            "id": i,
            "tenant": "tenant",
            "category": "A" if i <= 40 else "B" if i <= 80 else None,
            "amount": i % 7 + 1,
            "weight": 2,
            "day": date(2026, 7, 1),
        }
        for i in range(1, 121)
    ]
    if boundary in ("formula_overflow", "row_overflow"):
        rows = [
            {
                **row,
                "amount": (2**63 - (2 if boundary == "formula_overflow" else 1)) if i == 0 else 0,
            }
            for i, row in enumerate(rows)
        ]
    elif boundary == "row_zero":
        rows[0]["weight"] = 0
    values = ",".join(
        f"({row['id']},'tenant',"
        + ("NULL" if row["category"] is None else f"'{row['category']}'")
        + f",{row['amount']},{row['weight']},DATE '2026-07-01')"
        for row in rows
    )
    collation = " COLLATE utf8mb4_0900_bin" if backend == "mysql" else ""
    data = SourceData(
        f"id BIGINT NOT NULL, tenant VARCHAR(16){collation} NOT NULL, category VARCHAR(16){collation}, amount BIGINT NOT NULL, weight BIGINT NOT NULL, day DATE",
        values,
        "id Int64, tenant String, category Nullable(String), amount Int64, weight Int64, day Date",
        rows,
    )
    monkeypatch.chdir(root)
    with source_case(backend, profile, root, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        source = f"md.table({case.source.table!r},database={case.source.database!r})"
        code = f"""import marivo.datasource as md
import marivo.semantic as ms
facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source={source},primary_key=['tenant','id'])
@ms.entity(datasource=ms.ref.datasource('warehouse'),source={source},primary_key=['tenant','id'])
def subset(rows):
    return rows.filter(rows.category=='A')
tenant=ms.dimension_column(name='tenant',entity=facts,column='tenant')
identity=ms.dimension_column(name='identity',entity=facts,column='id')
category=ms.dimension_column(name='category',entity=facts,column='category')
subset_tenant=ms.dimension_column(name='tenant',entity=subset,column='tenant')
subset_id=ms.dimension_column(name='identity',entity=subset,column='id')
to_facts=ms.relationship(name='to_facts',from_entity=subset,to_entity=facts,keys=[ms.join_on(subset_tenant,tenant),ms.join_on(subset_id,identity)])
amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())
weight=ms.measure_column(name='weight',entity=facts,column='weight',additivity=ms.additive_all())
subset_weight=ms.measure_column(name='weight',entity=subset,column='weight',additivity=ms.additive_all())
# Named expressions
a=amount
w=weight
s=subset_weight
@ms.metric()
def combined():
    return a.sum()+s.sum()
@ms.measure(additivity=ms.additive_all())
def adjusted():
    return a+w
@ms.measure(additivity=ms.additive_all())
def fractional():
    return a/2+w/4
@ms.measure(additivity=ms.additive_all())
def guarded():
    return a+w.log()
@ms.metric()
def adjusted_total():
    return adjusted.sum()
@ms.metric()
def fractional_total():
    return fractional.sum()
@ms.metric()
def guarded_total():
    return guarded.sum()
"""
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
                "sales/model.py": code,
            }
        )
        yield mv.session.get_or_create("original-multiple-roots", timezone="UTC")


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("operation", ("rollup", "selected_rollup", "selected_drill"))
def test_same_owner_roots_reduce_separately_before_formula_alignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    operation: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as session:
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        metric = session.catalog.get("metric:sales.combined")
        assert metric.kind == "metric"
        if operation == "rollup":
            query = session.observe({"combined": metric, "amount": amount.sum()})
        else:
            fine = session.observe({"combined": metric, "amount": amount.sum()}, by=(category,))
            fixed = fine.top(1, by=fine["combined"]).compute()
            assert fixed.to_pandas().iloc[:, 0].tolist() == ["A"]
            selected = session.population_from(fixed, on=((fixed.axes[0], category),))
            query = session.observe(
                {"combined": metric, "amount": amount.sum()},
                by=(category, tenant) if operation == "selected_drill" else (),
                where=mv.in_population(selected),
            )
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["combined"].tolist() == [558 if operation == "rollup" else 240]
        assert frame["amount"].tolist() == [478 if operation == "rollup" else 160]
        assert trace.returned_rows


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("name", ("adjusted_total", "fractional_total"))
def test_row_recipes_recompute_at_source_with_both_physical_operands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    name: str,
) -> None:
    with _session(backend, profile, tmp_path, monkeypatch, semantic_project_factory) as session:
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        metric = catalog_value(session.catalog, "sales." + name, kind="metric")
        query = session.observe({"value": metric})
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert trace.returned_rows == [1]
        assert frame["value"].tolist() == [718 if name == "adjusted_total" else 299.0]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("boundary", ("formula_overflow", "row_overflow", "row_zero"))
def test_native_recipe_and_formula_checks_survive_empty_parent_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    boundary: str,
) -> None:
    with _session(
        backend, profile, tmp_path, monkeypatch, semantic_project_factory, boundary
    ) as session:
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        name = {
            "formula_overflow": "combined",
            "row_overflow": "adjusted_total",
            "row_zero": "guarded_total",
        }[boundary]
        fine = session.observe(
            {"value": catalog_value(session.catalog, "sales." + name, kind="metric")},
            by=(category,),
        )
        trace = capture_source(monkeypatch)
        if backend == "clickhouse" and boundary != "row_zero":
            # Both native Int64 additions wrap; no protective widening is promised.
            expected = -(2**63) + (78 if boundary == "formula_overflow" else 79)
            other = 0 if boundary == "formula_overflow" else 80
            assert sorted(fine.compute().to_pandas()["value"].tolist()) == [expected, other, other]
            assert fine.top(0, by=fine["value"]).compute().to_pandas().empty
        elif boundary == "formula_overflow" and backend == "mysql":
            for query in (fine, fine.top(0, by=fine["value"])):
                with pytest.raises(MaterializationError) as failure:
                    query.compute()
                assert failure.value.stage == "numeric_value"
                received = failure.value.received
                assert received is not None and "int64" in received and "overflow" in received
        elif boundary == "formula_overflow":
            with pytest.raises(AnalysisError):
                fine.compute()
            with pytest.raises(AnalysisError):
                fine.top(0, by=fine["value"]).compute()
        else:
            with pytest.raises(AnalysisError):
                fine.top(0, by=fine["value"]).compute()
