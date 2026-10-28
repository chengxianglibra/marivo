"""Backend builtin extraction composes with separate numeric field roles."""

from collections.abc import Callable
from pathlib import Path

import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("trino", "iceberg")])
)
def test_builtin_json_extraction_preserves_dimension_and_measure_roles(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT, category BIGINT, payload VARCHAR(100)",
        "(1,2,'{\"value\":10}'),(2,3,'{\"value\":null}'),(3,4,'{\"value\":25}')",
        "id Int64, category Int64, payload String",
        [
            {"id": 1, "category": 2, "payload": '{"value":10}'},
            {"id": 2, "category": 3, "payload": '{"value":null}'},
            {"id": 3, "category": 4, "payload": '{"value":25}'},
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        function_name = "json_extract_scalar" if backend == "trino" else "json_extract_string"
        project = semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n",
                "sales/facts.py": f"""import ibis
import marivo.datasource as md
import marivo.semantic as ms

@ibis.udf.scalar.builtin(name={function_name!r})
def extract_value(payload: str, path: str) -> str:
    raise AssertionError("builtin declaration bodies must not execute")

@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])
def facts(raw):
    return raw.mutate(extracted=extract_value(raw['payload'], '$.value').cast('int64'))

category = ms.dimension_column(name='category', entity=facts, column='category')
quantity_label = ms.dimension_column(name='quantity_label', entity=facts, column='extracted')
quantity = ms.measure_column(name='quantity', entity=facts, column='extracted', unit='1', additivity=ms.additive_all())

@ms.metric()
def selected_total():
    return quantity.sum().where(category.isin([2, 4]))
""",
            }
        )
        assert project.is_ready(), project.errors()
        session = mv.session.get_or_create("builtin-json-extraction", timezone="UTC")
        quantity = catalog_value(session.catalog, "sales.facts.quantity", kind="field")
        label = catalog_value(session.catalog, "sales.facts.quantity_label", kind="field")
        total = catalog_value(session.catalog, "sales.selected_total", kind="metric")
        assert isinstance(quantity, Deferred) and isinstance(label, Deferred)
        assert isinstance(total, Deferred)
        trace = capture_source(monkeypatch)
        result = session.observe({"total": total, "mean": quantity.mean()}).compute()
        rows = result.to_pandas()
        assert rows["total"].tolist() == [35]
        assert rows["mean"].tolist() == [17.5]
        if backend == "trino":
            assert any(function_name in sql.lower() for sql in trace.native_sql)
        grouped = session.observe({"count": quantity.count()}, by=(label,)).compute().to_pandas()
        assert sorted(grouped["count"].tolist()) == [0, 1, 1]
