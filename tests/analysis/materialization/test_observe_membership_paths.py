"""Membership allocation preserves other explicit paths to the same field."""

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value


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
def test_membership_preserves_explicit_coordinate_and_predicate_paths(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    orders = SourceData(
        "id BIGINT NOT NULL, buyer BIGINT NOT NULL, seller BIGINT NOT NULL, amount BIGINT NOT NULL",
        "(1,1,3,10),(2,2,3,20)",
        "id Int64, buyer Int64, seller Int64, amount Int64",
        [
            {"id": 1, "buyer": 1, "seller": 3, "amount": 10},
            {"id": 2, "buyer": 2, "seller": 3, "amount": 20},
        ],
    )
    people = SourceData(
        "id BIGINT NOT NULL, region VARCHAR(16)",
        "(1,'east'),(2,'south'),(3,'west')",
        "id Int64, region String",
        [{"id": key, "region": region} for key, region in ((1, "east"), (2, "south"), (3, "west"))],
    )
    monkeypatch.chdir(tmp_path)
    with ExitStack() as stack:
        cases = []
        for data in (orders, people):
            case = stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
            cases.append(case)
            if backend == "duckdb":
                # Release the read-only handle before creating the next fixture table.
                case.session.close()
        sources = []
        for case in cases:
            assert isinstance(case.source, TableSourceIR)
            sources.append(f"md.table({case.source.table!r}, database={case.source.database!r})")
        datasource = cases[0].session.datasource
        arguments = {
            **datasource.fields,
            **{key + "_env": value for key, value in datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_MEMBERSHIP_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_MEMBERSHIP_READER"
        arguments_text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        semantic_project_factory(
            {
                "datasources/warehouse.py": f"import marivo.datasource as md\nmd.{backend}(name='warehouse', {arguments_text})\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)\n",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource('warehouse')
orders = ms.entity(name='orders', datasource=warehouse, source={sources[0]}, primary_key=['id'])
people = ms.entity(name='people', datasource=warehouse, source={sources[1]}, primary_key=['id'])
person_id = ms.dimension_column(name='id', entity=people, column='id')
buyer_id = ms.dimension_column(name='buyer', entity=orders, column='buyer')
seller_id = ms.dimension_column(name='seller', entity=orders, column='seller')
region = ms.dimension_column(name='region', entity=people, column='region')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
buyer = ms.relationship(name='buyer', from_entity=orders, to_entity=people, keys=[ms.join_on(buyer_id, person_id)])
seller = ms.relationship(name='seller', from_entity=orders, to_entity=people, keys=[ms.join_on(seller_id, person_id)])
""",
            }
        )
        session = mv.session.get_or_create("membership-paths", timezone="UTC")
        region = catalog_value(session.catalog, "sales.people.region", kind="field")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
        buyer = session.catalog.get("relationship:sales.buyer").value
        seller = session.catalog.get("relationship:sales.seller").value
        allocation = mv.membership(region, via=((buyer,),), unmatched="exclude")
        seller_region = region.via((seller,))
        grouped = session.observe({"v": amount.sum()}, by=(allocation, seller_region))
        fixed = grouped.compute()
        actual = {
            "coordinates": list(
                fixed.to_pandas(
                    axis_names={
                        fixed.axes[0].token: "buyer_region",
                        fixed.axes[1].token: "seller_region",
                    }
                ).itertuples(index=False, name=None)
            )
        }
        for name, field, key in (("seller", seller_region, "west"), ("buyer", region, "east")):
            condition = mv.in_population(mv.coordinates(by=(field,), keys=((key,),)))
            for entry in ("observe", "metric"):
                frame = session.observe(
                    {"v": amount.sum().where(condition) if entry == "metric" else amount.sum()},
                    by=(allocation,),
                    where=condition if entry == "observe" else None,
                )
                actual[f"{name}.{entry}"] = list(
                    frame.compute().to_pandas().itertuples(index=False, name=None)
                )
        assert actual == {
            "coordinates": [("east", "west", 10), ("south", "west", 20)],
            "seller.observe": [("east", 10), ("south", 20)],
            "seller.metric": [("east", 10), ("south", 20)],
            "buyer.observe": [("east", 10)],
            "buyer.metric": [("east", 10)],
        }
