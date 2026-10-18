"""Same-owner originals reduce each root before native key alignment."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_lowering import LoweredQuery
from marivo.analysis.compiler.native_formula import align_native_inputs
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import source_original_dependent
from marivo.analysis.materialization.graph_source_execution import PreparedSources
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_original_postgres import _unoptimized
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [
    ("duckdb", "table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
]


def _baseline(query: mv.Query, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    def unchanged(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
        return lowered

    with monkeypatch.context() as scoped:
        scoped.setattr(source_original_dependent, "fuse_dependent_original", unchanged)
        return _unoptimized(query, scoped)


def test_domain_alignment_preserves_null_keys_without_full_outer_join() -> None:
    connection = ibis.duckdb.connect()
    try:
        left = connection.create_table(
            "left_values", pd.DataFrame({"key": ["A", None], "x": [10, 20]})
        )
        right = connection.create_table(
            "right_values", pd.DataFrame({"key": ["B", None], "y": [30, 40]})
        )
        aligned = align_native_inputs((left, right), ("key",), ("x", "y"), alignment="domain")
        frame = connection.execute(aligned.order_by(aligned.key.asc(nulls_first=True)))
        assert frame.x.tolist() == [20, 10, 0]
        assert frame.y.tolist() == [40, 0, 30]
        sql = ibis.to_sql(aligned, dialect="mysql").upper()
        assert "UNION" in sql and "LEFT OUTER JOIN" in sql
        assert "FULL OUTER" not in sql
    finally:
        connection.disconnect()


@contextmanager
def _session(
    backend: str,
    profile: str,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    boundary: str = "ordinary",
) -> Iterator[Session]:
    rows = [
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
frozen=ms.finalize()
a=frozen.resolve(amount)
w=frozen.resolve(weight)
s=frozen.resolve(subset_weight)
@ms.metric()
def combined():
    return a.sum(empty='zero')+s.sum(empty='zero')
@ms.measure(additivity=ms.additive_all())
def adjusted():
    return a+w
@ms.measure(additivity=ms.additive_all())
def fractional():
    return a/2+w/4
@ms.measure(additivity=ms.additive_all())
def guarded():
    return a.divide(w,zero_denominator='error')
@ms.metric()
def adjusted_total():
    return adjusted.sum(empty='zero')
@ms.metric()
def fractional_total():
    return fractional.sum(empty='zero')
@ms.metric()
def guarded_total():
    return guarded.sum(empty='zero')
"""
        factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
                "sales/model.py": code,
            }
        )
        yield mv.session.get_or_create("original-multiple-roots", report_timezone="UTC")


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
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        amount = session.catalog.fields.get("sales.facts.amount")
        metric = session.catalog.metrics.get("sales.combined")
        fine = session.observe({"combined": metric, "amount": amount.sum()}, by=(category,))
        selected = fine.top(1, by=fine["combined"]) if operation != "rollup" else fine
        query = (
            selected.drill(add=(tenant,)) if operation == "selected_drill" else selected.rollup()
        )
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == (
            [1] if backend == "duckdb" or operation == "rollup" else [3, 1]
        )
        assert len(trace.uploaded_key_rows) == (0 if len(trace.returned_rows) == 1 else 1)
        assert not any(
            "__original_value" in name for names in trace.returned_columns for name in names
        )


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
        category = session.catalog.fields.get("sales.facts.category")
        metric = session.catalog.metrics.get("sales." + name)
        query = session.observe({"value": metric}, by=(category,)).rollup()
        expected = _baseline(query, monkeypatch)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []


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
        category = session.catalog.fields.get("sales.facts.category")
        name = {
            "formula_overflow": "combined",
            "row_overflow": "adjusted_total",
            "row_zero": "guarded_total",
        }[boundary]
        fine = session.observe(
            {"value": session.catalog.metrics.get("sales." + name)}, by=(category,)
        )
        trace = capture_source(monkeypatch)
        with pytest.raises(AnalysisError):
            fine.top(0, by=fine["value"]).rollup().execute()
        assert trace.returned_rows == ([1] if backend == "duckdb" else [3])
