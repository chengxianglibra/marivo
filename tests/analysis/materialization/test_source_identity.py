"""Semantic primary keys admit nullable physical columns without a preflight."""

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.graph_plan import plan_query
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization.graph_source_execution import open_query_sources
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

CASES = [
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("nullable", [False, True])
@pytest.mark.parametrize("composite", [False, True])
def test_source_identity_metadata_preserves_binding_and_compact_aggregates(
    backend: str,
    profile: str,
    nullable: bool,
    composite: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    required = "" if nullable else " NOT NULL"
    collation = " COLLATE utf8mb4_0900_bin" if backend == "mysql" else ""
    identities = [2**53 + 1, 2**53 + (1 if composite else 2), 2**53 + 3, 2**53 + 4]
    rows: list[dict[str, object]] = [
        {"id": identity, "tenant": tenant, "category": category, "amount": amount}
        for identity, (tenant, category, amount) in zip(
            identities,
            (("east", "A", 10), ("west", "A", 20), ("east", "B", 30), ("west", None, None)),
            strict=True,
        )
    ]
    data = SourceData(
        f"id BIGINT{required}, tenant VARCHAR(16){collation}{required}, "
        f"category VARCHAR(16){collation}, amount BIGINT",
        f"({identities[0]},'east','A',10),({identities[1]},'west','A',20),"
        f"({identities[2]},'east','B',30),({identities[3]},'west',NULL,NULL)",
        ("id Nullable(Int64), tenant Nullable(String)" if nullable else "id Int64, tenant String")
        + ", category Nullable(String), amount Nullable(Int64)",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        physical = case.session.bind(case.source, source_identity="identity-metadata").facts.schema
        assert physical.field("id").nullable is nullable
        assert physical.field("tenant").nullable is nullable
        primary_key = ["tenant", "id"] if composite else ["id"]
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={primary_key!r})\n"
                + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("identity-metadata", timezone="UTC")
        entity = session.catalog.get("entity:sales.facts")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        shared = session.observe({"total": amount.sum(), "n": entity.count()})
        assert isinstance(shared._backing, ExpressionFrameBacking)
        assert isinstance(shared._backing.root, SourceFrameBinding)
        graph = shared._backing.root.graph
        trace = capture_source(monkeypatch)
        with open_query_sources(
            session._runtime.connection_service(), graph, session.catalog._state
        ) as prepared:
            assert all(schema.binding_id for schema in prepared.schemas)
            assert len({schema.binding_id for schema in prepared.schemas}) == 1
            plan = optimize_query(plan_query(graph, prepared.schemas))
            assert any(group.law == "source_block" for group in plan.fusion_groups)
        assert trace.native_sql == []

        ordinary = session.observe({"total": amount.sum()}).compute().to_pandas()
        assert ordinary["total"].tolist() == [60]
        combined = shared.compute().to_pandas()
        assert combined["total"].tolist() == [60]
        assert combined["n"].tolist() == [4]
        grouped = (
            session.observe({"total": amount.sum(), "n": entity.count()}, by=(category,))
            .compute()
            .to_pandas()
        )
        actual = {
            None if pd.isna(key) else key: (None if pd.isna(total) else total, count)
            for key, total, count in grouped.itertuples(index=False, name=None)
        }
        assert actual == {"A": (30, 2), "B": (30, 1), None: (None, 1)}
        # Each question reads only its terminal aggregate; nullable metadata adds
        # neither a NULL preflight nor a transport of original identity rows.
        assert len(trace.native_sql) == 3
        assert trace.returned_rows == [1, 1, 3]
        assert trace.uploaded_key_rows == []

        members = session.frame(session.members(entity)).compute()
        assert all(not column.nullable for axis in members._schema.axes for column in axis.columns)
        keys = members.to_pandas().iloc[:, 0].tolist()
        expected = [(row["tenant"], row["id"]) for row in rows] if composite else identities
        assert set(keys) == set(expected)
        assert trace.returned_rows == [1, 1, 3, 4]
