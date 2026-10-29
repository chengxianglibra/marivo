"""Drill discovers related schemas through selected original Query definitions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value


@pytest.fixture
def related_drill_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "related-drill.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, customer_id BIGINT, channel VARCHAR, tag VARCHAR, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,1,'Web','keep',10),(2,2,'Web','other',30),"
            "(3,1,'Store','other',20),(4,3,'Other','other',5)"
        )
        connection.execute("CREATE TABLE customers(id BIGINT, region VARCHAR, country_id BIGINT)")
        connection.execute("INSERT INTO customers VALUES (1,'North',1),(2,'South',2),(3,'West',3)")
        connection.execute("CREATE TABLE countries(id BIGINT, name VARCHAR)")
        connection.execute("INSERT INTO countries VALUES (1,'North'),(2,'South'),(3,'West')")
        connection.execute("CREATE TABLE refunds(id BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO refunds VALUES (1,999)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            + "ms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource('warehouse')
orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'), primary_key=['id'])
customer_id = ms.dimension_column(name='customer_id', entity=orders, column='customer_id')
channel = ms.dimension_column(name='channel', entity=orders, column='channel')
tag = ms.dimension_column(name='tag', entity=orders, column='tag')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
customers = ms.entity(name='customers', datasource=warehouse, source=md.table('customers'), primary_key=['id'])
id = ms.dimension_column(name='id', entity=customers, column='id')
region = ms.dimension_column(name='region', entity=customers, column='region')
country_id = ms.dimension_column(name='country_id', entity=customers, column='country_id')
buyer = ms.relationship(name='buyer', from_entity=orders, to_entity=customers, keys=[ms.join_on(customer_id, id)])
countries = ms.entity(name='countries', datasource=warehouse, source=md.table('countries'), primary_key=['id'])
country_key = ms.dimension_column(name='id', entity=countries, column='id')
country_name = ms.dimension_column(name='name', entity=countries, column='name')
home = ms.relationship(name='home', from_entity=customers, to_entity=countries, keys=[ms.join_on(country_id, country_key)])
refunds = ms.entity(name='refunds', datasource=warehouse, source=md.table('refunds'), primary_key=['id'])
refund_amount = ms.measure_column(name='amount', entity=refunds, column='amount', additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("related-drill", timezone="UTC")


@pytest.mark.parametrize("operation", ("direct", "where", "top", "select", "rollup"))
@pytest.mark.parametrize("member_count", (False, True), ids=("sum", "member_count"))
@pytest.mark.parametrize("explicit_path", (False, True), ids=("related_field", "two_hop_path"))
def test_drill_after_original_operations_prepares_related_fields(
    related_drill_session: Session, operation: str, member_count: bool, explicit_path: bool
) -> None:
    session = related_drill_session
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    tag = catalog_value(session.catalog, "sales.orders.tag", kind="field")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    members = (
        session.members(session.catalog.get("entity:sales.orders").value) if member_count else None
    )
    if operation == "rollup" and member_count:
        members = session.members(
            session.catalog.get("entity:sales.orders").value, where=tag == "keep"
        )
    if members is not None:
        members = members.classify(by=(members.axes[0], channel))
    metric = members.count() if members is not None else amount.sum()
    if operation == "rollup" and not member_count:
        metric = metric.where(tag == "keep")
    fine = session.observe({"total": metric}, by=(channel,))
    name = "total"
    if operation == "where":
        parent = fine.where(fine["total"] >= (2 if member_count else 40))
    elif operation in ("top", "select"):
        parent = fine.top(1, by=fine["total"])
        if operation == "select":
            parent = parent.select({"retained": parent["total"]})
            name = "retained"
    else:
        parent = fine
    added = catalog_value(session.catalog, "sales.customers.region", kind="field")
    if explicit_path:
        added = catalog_value(session.catalog, "sales.countries.name", kind="field").via(
            (
                session.catalog.get("relationship:sales.buyer").value,
                session.catalog.get("relationship:sales.home").value,
            )
        )
    fixed = parent.compute()
    keys = session.population_from(fixed, on=((fixed.axes[0], channel),))
    where = mv.in_population(keys)
    if member_count:
        entity = session.catalog.get("entity:sales.orders").value
        selected_members = (
            session.members(session.catalog.get("entity:sales.orders").value, where=where)
            if operation != "rollup"
            else session.members(
                session.catalog.get("entity:sales.orders").value, where=tag == "keep"
            )
        )
        selected_ids = session.frame(selected_members).compute()
        expected_ids = (
            [1, 2, 3, 4] if operation == "direct" else [1] if operation == "rollup" else [1, 2]
        )
        assert selected_ids.to_pandas().iloc[:, 0].tolist() == expected_ids
        fixed_members = session.population_from(selected_ids, on=((selected_ids.axes[0], entity),))
        where = mv.in_population(fixed_members)
        metric = entity.count()
        members = None
    result = session.observe({name: metric}, by=(channel, added), where=where).compute().to_pandas()
    actual = list(
        zip(
            result.iloc[:, 0],
            result[result.attrs["axes"][1]["name"].rsplit(".", 1)[-1]],
            result[name],
            strict=True,
        )
    )
    if operation == "direct":
        expected = [
            ("Other", "West", 1 if member_count else 5),
            ("Store", "North", 1 if member_count else 20),
            ("Web", "North", 1 if member_count else 10),
            ("Web", "South", 1 if member_count else 30),
        ]
    elif operation == "rollup":
        expected = [("Web", "North", 1 if member_count else 10)]
    else:
        expected = [
            ("Web", "North", 1 if member_count else 10),
            ("Web", "South", 1 if member_count else 30),
        ]
    assert actual == expected


def test_drill_projection_does_not_require_paths_for_removed_metric_roots(
    related_drill_session: Session,
) -> None:
    session = related_drill_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    result = session.observe({"retained": amount.sum()}, by=(region,)).compute().to_pandas()
    assert result.iloc[:, 0].tolist() == ["North", "South", "West"]
    assert result["retained"].tolist() == [30, 30, 5]
