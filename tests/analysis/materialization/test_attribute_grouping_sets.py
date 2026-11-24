"""Native Attribute parent and fine roles share one exact aggregation."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from psycopg import sql

import marivo.analysis as mv
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.frame_evaluation import evaluate_frame
from marivo.analysis.model.attribute_failures import AttributeCheckWitness, AttributeParentFiber
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.analysis.session.core import Session
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.environment import postgres_analysis as pg
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace, capture_source


def _project_source(
    backend: str,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Session:
    assert isinstance(case.source, TableSourceIR)
    semantic_project_factory(
        {
            "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
            "sales/facts.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                f"facts = ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                "region = ms.dimension_column(name='region', entity=facts, column='region')\n"
                "channel = ms.dimension_column(name='channel', entity=facts, column='channel')\n"
                "value = ms.measure_column(name='value', entity=facts, column='value', additivity=ms.additive_all())\n"
            ),
        }
    )
    return mv.session.get_or_create("attribute-grouping", timezone="UTC")


@pytest.mark.parametrize("reducer", ("count", "sum"))
@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_attribute_grouping_separates_native_null_class_from_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    reducer: str,
) -> None:
    empty = reducer == "count"
    data = SourceData(
        "id BIGINT NOT NULL, region VARCHAR(10), channel VARCHAR(10), value DECIMAL(18,0)",
        f"(1,NULL,NULL,10),(2,NULL,NULL,20),(3,NULL,'A',{'NULL' if empty else 0}),(4,'east','B',30)",
        "id Int64, region Nullable(String), channel Nullable(String), value Nullable(Decimal(18,0))",
        [
            {"id": 1, "region": None, "channel": None, "value": Decimal(10)},
            {"id": 2, "region": None, "channel": None, "value": Decimal(20)},
            {"id": 3, "region": None, "channel": "A", "value": None if empty else Decimal(0)},
            {"id": 4, "region": "east", "channel": "B", "value": Decimal(30)},
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        session = _project_source(backend, case, monkeypatch, semantic_project_factory)
        region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
        channel = catalog_value(session.catalog, "sales.facts.channel", kind="dimension")
        value = catalog_value(session.catalog, "sales.facts.value", kind="measure")
        metric = value.count() if reducer == "count" else value.sum()
        selected = session.observe({"q": metric}).compute()
        assert selected.to_pandas()["q"].tolist() == ([3] if reducer == "count" else [60])

        trace = capture_source(monkeypatch)
        pending = selected.attribute(by=(region, channel))
        completed = pending.compute()
        output = completed.to_pandas()
        actual = {
            (None if pd.isna(r) else r, None if pd.isna(c) else c): amount
            for r, c, amount in zip(
                output["region"], output["channel"], output["contribution.q"], strict=True
            )
        }
        assert actual == (
            {(None, None): 2, (None, "A"): 0, ("east", "B"): 1}
            if reducer == "count"
            else {(None, None): 30, (None, "A"): 0, ("east", "B"): 30}
        )
        if backend == "mysql":
            assert trace.returned_rows == [1, 3]
            assert all("GROUPING SETS" not in sql.upper() for sql in trace.native_sql)
        else:
            assert trace.returned_rows == [4]
            assert len(trace.native_sql) == 1
            assert "GROUPING SETS" in trace.native_sql[0].upper()

        first_submissions = len(trace.native_sql)
        reference = evaluate_frame(
            pending,
            policy=OptimizationPolicy.default().with_rule("attribute_grouping_sets", enabled=False),
        )
        assert isinstance(completed._backing, MemoryFrameBacking)
        pd.testing.assert_frame_equal(
            completed._backing.data.copy_rows(), reference.data.copy_rows()
        )
        assert trace.returned_rows[first_submissions:] == [1, 3]
        assert all(
            "GROUPING SETS" not in sql.upper() for sql in trace.native_sql[first_submissions:]
        )
        if backend == "postgres" and reducer == "sum":
            assert isinstance(case.source, TableSourceIR)
            _check_postgres_decimal_nan(pending, case.source.table, trace)


def _check_postgres_decimal_nan(pending: AnalysisFrame, table: str, trace: SourceTrace) -> None:
    """A PostgreSQL numeric NaN remains an original parent conversion failure."""
    with pg.connection(admin=True) as admin:
        admin.execute(sql.SQL("UPDATE {} SET value='NaN' WHERE id=4").format(sql.Identifier(table)))
    failures = []
    issued = len(trace.native_sql)
    for enabled in (True, False):
        with pytest.raises(MaterializationError) as caught:
            evaluate_frame(
                pending,
                policy=OptimizationPolicy.default().with_rule(
                    "attribute_grouping_sets", enabled=enabled
                ),
            )
        failures.append((caught.value.stage, caught.value.expected, caught.value.received))
    assert failures[0] == failures[1]
    assert len(trace.native_sql[issued:]) == 2
    assert "GROUPING SETS" in trace.native_sql[issued]
    assert "GROUPING SETS" not in trace.native_sql[issued + 1]


@pytest.mark.parametrize("backend,profile", source_params([("postgres", "view")]))
def test_attribute_view_keeps_original_native_failure_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, region VARCHAR(10), channel VARCHAR(10), value BIGINT",
        "(1,'west','A',10),(2,'east','B',20)",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        session = _project_source(backend, case, monkeypatch, semantic_project_factory)
        value = catalog_value(session.catalog, "sales.facts.value", kind="measure")
        region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
        selected = session.observe({"q": value.count(nulls="error")}).compute()
        assert isinstance(case.source, TableSourceIR)
        view = sql.Identifier(case.source.table)
        table = sql.Identifier(case.source.table.removesuffix("_view"))
        with pg.connection(admin=True) as admin:
            admin.execute(sql.SQL("UPDATE {} SET value=NULL WHERE id=2").format(table))
            admin.execute(
                sql.SQL(
                    "CREATE OR REPLACE VIEW {} AS SELECT id, CAST(1 / (id - 1) AS VARCHAR(10)) "
                    "AS region, channel, value FROM {}"
                ).format(view, table)
            )

        trace = capture_source(monkeypatch)
        pending = selected.attribute(by=(region,))
        failures = []
        for enabled in (True, False):
            with pytest.raises(MaterializationError) as caught:
                evaluate_frame(
                    pending,
                    policy=OptimizationPolicy.default().with_rule(
                        "attribute_grouping_sets", enabled=enabled
                    ),
                )
            failures.append((caught.value.stage, caught.value.expected, caught.value.received))
        assert failures[0] == failures[1]
        assert failures[0][0] == "source_execution"
        assert len(trace.native_sql) == 2
        assert all("GROUPING SETS" not in sql for sql in trace.native_sql)


@pytest.mark.parametrize(
    "fine_type,bad,expected_grouping",
    (
        ("NUMERIC(18,2)", "'NaN'", False),
        ("DATE", "'infinity'", False),
        ("BIGINT", "88", True),
    ),
)
@pytest.mark.parametrize("backend,profile", source_params([("postgres", "table")]))
def test_attribute_additional_fine_key_transport_cannot_preempt_parent_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    fine_type: str,
    bad: str,
    expected_grouping: bool,
) -> None:
    first, second = ("'2020-01-01'", "'2020-02-01'") if fine_type == "DATE" else ("1", "2")
    rows = SourceData(
        f"id BIGINT NOT NULL, region {fine_type}, channel VARCHAR(10), value BIGINT",
        f"(1,{first},'A',10),(2,{second},'B',20)",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, rows) as case:
        assert isinstance(case.source, TableSourceIR)
        session = _project_source(backend, case, monkeypatch, semantic_project_factory)
        region = catalog_value(session.catalog, "sales.facts.region", kind="dimension")
        value = catalog_value(session.catalog, "sales.facts.value", kind="measure")
        parent = session.observe({"q": value.count(nulls="error")}).compute()
        with pg.connection(admin=True) as admin:
            table = sql.Identifier(case.source.table)
            admin.execute(sql.SQL("UPDATE {} SET region={} WHERE id=2").format(table, sql.SQL(bad)))
            admin.execute(sql.SQL("UPDATE {} SET value=NULL WHERE id=1").format(table))
        query = parent.attribute(by=(region,))
        for enabled in (False, True):
            policy = OptimizationPolicy.default().with_rule(
                "attribute_grouping_sets", enabled=enabled
            )
            with monkeypatch.context() as observed:
                trace = capture_source(observed)
                with pytest.raises(MaterializationError) as failure:
                    evaluate_frame(query, policy=policy)
            assert failure.value.stage == "numeric_value"
            assert failure.value.expected == "non-NA source values"
            context = failure.value.attribute_context
            assert context is not None and isinstance(context.fiber, AttributeParentFiber)
            assert isinstance(context.witness, AttributeCheckWitness)
            assert context.witness.coordinates == ((),)
            (issued_sql,) = trace.native_sql
            assert ("GROUPING SETS" in issued_sql.upper()) is (enabled and expected_grouping)
