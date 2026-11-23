"""Required nested membership checks on native sample reductions."""

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_population_membership import _MODEL
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_native_empty_sample_checks_nested_membership_identity(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    definitions = {
        "orders": SourceData(
            "id BIGINT, customer BIGINT, amount BIGINT NOT NULL",
            "(1,1,10),(2,2,20),(NULL,3,30)",
            "id Nullable(Int64), customer Int64, amount Int64",
            [
                {"id": key, "customer": customer, "amount": amount}
                for key, customer, amount in ((1, 1, 10), (2, 2, 20), (None, 3, 30))
            ],
        ),
        "links": SourceData(
            "id BIGINT NOT NULL, order_id BIGINT NOT NULL, tag_id BIGINT NOT NULL",
            "(1,1,5),(2,2,5)",
            "id Int64, order_id Int64, tag_id Int64",
            [{"id": key, "order_id": key, "tag_id": 5} for key in (1, 2)],
        ),
        "tags": SourceData(
            "id BIGINT NOT NULL, label VARCHAR(16), channel VARCHAR(16)",
            "(5,'A','web')",
            "id Int64, label String, channel String",
            [{"id": 5, "label": "A", "channel": "web"}],
        ),
    }
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        model = _MODEL
        datasource_model = ""
        for name, data in definitions.items():
            case = stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
            assert isinstance(case.source, TableSourceIR)
            if not datasource_model:
                datasource_model = _datasource_model(backend, case, monkeypatch)
            model = model.replace(
                f'md.table("{name}")',
                f"md.table({case.source.table!r}, database={case.source.database!r})",
            )
        semantic_project_factory(
            {
                "datasources/warehouse.py": datasource_model,
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/objects.py": model,
            }
        )
        session = mv.session.get_or_create("native-sample-membership", timezone="UTC")
        catalog = session.catalog
        orders = catalog_value(catalog, "sales.orders", kind="entity")
        identity = catalog_value(catalog, "sales.orders.id", kind="dimension")
        revenue = catalog_value(catalog, "sales.revenue", kind="metric")
        path = mv.path(
            catalog_value(catalog, "sales.order_links", kind="relationship"),
            catalog_value(catalog, "sales.link_tags", kind="relationship"),
        )
        empty = mv.coordinates(by=(identity,), keys=())
        report = mv.coordinates(by=(identity,), keys=((1,), (2,)))
        selected = session.members(orders)
        invalid = selected.preimage(selected.image(via=path), via=path)
        query = session.observe(
            {"mean": revenue.per(empty).mean()}, by=report, where=mv.in_population(invalid)
        )
        with pytest.raises(MaterializationError) as error:
            query.compute()
        assert error.value.stage == "population_identity"

        valid = session.members(orders, where=identity.notnull())
        matched = valid.preimage(valid.image(via=path), via=path)
        trace = capture_source(monkeypatch)
        result = (
            session.observe(
                {"mean": revenue.per(empty).mean()}, by=(identity,), where=mv.in_population(matched)
            )
            .compute()
            .to_pandas()
        )
        assert sorted(result.iloc[:, 0].tolist()) == [1, 2]
        assert result["mean"].isna().all()
        assert trace.returned_rows and max(trace.returned_rows) <= 2
