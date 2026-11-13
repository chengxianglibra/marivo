"""Native SQL witnesses for complete multivalued membership tuples."""

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.datasource as md
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value, datasource_arguments_source


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
def test_source_membership_requires_one_shared_witness(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    orders = SourceData(
        "id BIGINT NOT NULL, amount BIGINT NOT NULL",
        "(1,10),(2,30),(3,100)",
        "id Int64, amount Int64",
        [{"id": identity, "amount": amount} for identity, amount in ((1, 10), (2, 30), (3, 100))],
    )
    links = SourceData(
        "id BIGINT NOT NULL, order_id BIGINT NOT NULL, tag_id BIGINT NOT NULL",
        "(1,1,1),(2,1,1),(3,1,2),(4,2,1)",
        "id Int64, order_id Int64, tag_id Int64",
        [
            {"id": identity, "order_id": order, "tag_id": tag}
            for identity, order, tag in ((1, 1, 1), (2, 1, 1), (3, 1, 2), (4, 2, 1))
        ],
    )
    tags = SourceData("id BIGINT NOT NULL", "(1),(2)", "id Int64", [{"id": 1}, {"id": 2}])
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        cases = [
            stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
            for data in (orders, links, tags)
        ]
        tables = []
        for case in cases:
            assert isinstance(case.source, TableSourceIR)
            tables.append(f"md.table({case.source.table!r}, database={case.source.database!r})")
        datasource = cases[0].session.datasource
        arguments = {
            **datasource.fields,
            **{key: md.env(value) for key, value in datasource.env_refs.items()},
        }
        if isinstance(arguments.get("user"), str):
            monkeypatch.setenv("MARIVO_WITNESS_READER", str(arguments.pop("user")))
            arguments["user"] = md.env("MARIVO_WITNESS_READER")
        arguments_text = datasource_arguments_source(arguments)
        semantic_project_factory(
            {
                "datasources/warehouse.py": f"import marivo.datasource as md\nmd.{backend}(wall_clock_timezone='UTC', name='warehouse', {arguments_text})\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource('warehouse')
orders = ms.entity(name='orders', datasource=warehouse, source={tables[0]}, primary_key=['id'])
links = ms.entity(name='links', datasource=warehouse, source={tables[1]}, primary_key=['id', 'order_id'])
tags = ms.entity(name='tags', datasource=warehouse, source={tables[2]}, primary_key=['id'])
order_id = ms.dimension_column(name='id', entity=orders, column='id')
link_order = ms.dimension_column(name='order_id', entity=links, column='order_id')
link_tag = ms.dimension_column(name='tag_id', entity=links, column='tag_id')
tag_id = ms.dimension_column(name='id', entity=tags, column='id')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
order_links = ms.relationship(name='order_links', from_entity=orders, to_entity=links, keys=[ms.join_on(order_id, link_order)])
link_tags = ms.relationship(name='link_tags', from_entity=links, to_entity=tags, keys=[ms.join_on(link_tag, tag_id)])
""",
            }
        )
        session = mv.session.get_or_create("source-membership-witness", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
        order_links = catalog_value(session.catalog, "sales.order_links", kind="relationship")
        link_tags = catalog_value(session.catalog, "sales.link_tags", kind="relationship")
        link = mv.identity(mv.path(order_links))
        tag = mv.identity(mv.path(order_links, link_tags))
        impossible = mv.in_population(mv.coordinates(by=(link, tag), keys=(((1, 1), (2,)),)))
        valid = mv.in_population(
            mv.coordinates(by=(link, tag), keys=(((1, 1), (1,)), ((2, 1), (1,)), ((4, 1), (1,))))
        )
        # Order 1 has two valid witnesses, and order 2's complete link key is (4, 2).
        total = amount.sum()
        result = (
            session.observe(
                {
                    "impossible": total.where(impossible),
                    "valid": total.where(valid),
                    "other": total.where(~valid),
                }
            )
            .compute()
            .to_pandas()
        )
        assert result.to_dict("list") == {"impossible": [0], "valid": [10], "other": [130]}
