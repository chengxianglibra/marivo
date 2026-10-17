"""Population selection and membership allocation retain contribution identities."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.dsl.population import population_keys
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
oid = ms.dimension_column(name="id", entity=orders, column="id")
customer = ms.dimension_column(name="customer", entity=orders, column="customer")
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
tags = ms.entity(name="tags", datasource=warehouse, source=md.table("tags"), primary_key=["id"])
tid = ms.dimension_column(name="id", entity=tags, column="id")
label = ms.dimension_column(name="label", entity=tags, column="label")
channel = ms.dimension_column(name="channel", entity=tags, column="channel")
links = ms.entity(name="links", datasource=warehouse, source=md.table("links"), primary_key=["id"])
order_id = ms.dimension_column(name="order_id", entity=links, column="order_id")
tag_id = ms.dimension_column(name="tag_id", entity=links, column="tag_id")
order_links = ms.relationship(name="order_links", from_entity=orders, to_entity=links, keys=[ms.join_on(oid, order_id)])
link_tags = ms.relationship(name="link_tags", from_entity=links, to_entity=tags, keys=[ms.join_on(tag_id, tid)])
frozen = ms.finalize()
a = frozen.resolve(amount)
@ms.metric()
def revenue():
    return a.sum()
"""


def _session(
    factory: Callable[..., SemanticProject], root: Path, monkeypatch: pytest.MonkeyPatch
) -> Session:
    database = root / "population.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads=1")
        connection.execute("CREATE TABLE orders(id BIGINT, customer BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO orders VALUES (1,1,10),(2,1,30),(3,2,100)")
        connection.execute("CREATE TABLE tags(id BIGINT,label VARCHAR,channel VARCHAR)")
        connection.execute("INSERT INTO tags VALUES (1,'A','web'),(2,'B','store'),(3,'C','other')")
        connection.execute("CREATE TABLE links(id BIGINT,order_id BIGINT,tag_id BIGINT)")
        connection.execute("INSERT INTO links VALUES (1,1,1),(2,1,1),(3,1,2),(4,2,1)")
    datasource_dir = root / "models" / "datasources"
    datasource_dir.mkdir(parents=True)
    (datasource_dir / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Analytics",default=True)\n',
            "sales/objects.py": _MODEL,
        },
        load=False,
    )
    monkeypatch.chdir(root)
    return mv.session.get_or_create("population", report_timezone="UTC")


def test_per_completes_ratio_per_sample_and_retains_empty_samples(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    customer = session.catalog.fields.get("sales.orders.customer")
    amount = session.catalog.fields.get("sales.orders.amount")
    revenue = session.catalog.metrics.get("sales.revenue")
    full = mv.coordinates(by=(customer,), keys=((1,), (2,), (3,)))
    result = (
        session.observe(
            {
                "aov_mean": (revenue / amount.count()).per(customer).mean(),
                "fixed_revenue_mean": revenue.per(full).mean(),
            }
        )
        .execute()
        .to_pandas()
    )
    assert result[("value", "aov_mean")].tolist() == [60.0]
    assert result[("value", "fixed_revenue_mean")].tolist() == [pytest.approx(140 / 3)]
    assert population_keys(
        full.difference(mv.coordinates(by=(customer,), keys=((2,),))).definition
    ) == ((1,), (3,))
    assert population_keys(full.project(by=()).definition) == ((),)
    assert len(session.query(full).execute().to_pandas()) == 3
    omitted = mv.coordinates(by=(customer,), keys=((1,),))
    captured = session.observe({"revenue": revenue}, by=omitted).population()
    with pytest.raises(MaterializationError) as failure:
        session.query(captured).execute()
    assert "covering every observed coordinate" in failure.value.expected


def test_membership_deduplicates_root_bucket_and_complete_domain(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=10 WHERE id=2")
    revenue = session.catalog.metrics.get("sales.revenue")
    label = session.catalog.fields.get("sales.tags.label")
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    buckets = mv.membership(label, via=(path,), unmatched="exclude")
    full = mv.coordinates(by=(buckets,), keys=(("A",), ("B",), ("C",)))
    result = session.observe({"revenue": revenue}, by=full).execute().to_pandas()
    assert result[("value", "revenue")].tolist() == [20, 10, 0]
    unmatched = mv.membership(label, via=(path,), unmatched="null")
    with_null = session.observe({"revenue": revenue}, by=(unmatched,)).execute().to_pandas()
    assert sorted(with_null[("value", "revenue")].tolist()) == [10, 20, 100]
    samples = session.observe({"mean": revenue.per(full).mean()}).execute().to_pandas()
    assert samples[("value", "mean")].tolist() == [10]
    classified = session.members(ms.ref.entity("sales.tags")).classify(by=(buckets,))
    result = session.observe({"revenue": revenue}, by=classified).execute().to_pandas()
    assert result[("value", "revenue")].tolist() == [20, 10, 0]


def test_membership_correlated_tuple_differs_from_independent_product(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.metrics.get("sales.revenue")
    label = session.catalog.fields.get("sales.tags.label")
    channel = session.catalog.fields.get("sales.tags.channel")
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    joint = mv.membership((label, channel), via=(path,), unmatched="exclude")
    tags = mv.membership(label, via=(path,), unmatched="exclude")
    channels = mv.membership(channel, via=(path,), unmatched="exclude")
    correlated = session.observe({"revenue": revenue}, by=(joint,)).execute().to_pandas()
    independent = session.observe({"revenue": revenue}, by=(tags, channels)).execute().to_pandas()
    assert len(correlated) == 2
    assert len(independent) == 4
    assert sorted(independent[("value", "revenue")].tolist()) == [10, 10, 10, 40]


def test_population_filters_and_relationship_images_preserve_typed_keys(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(ms.ref.entity("sales.orders"))
    tags = session.members(ms.ref.entity("sales.tags"))
    tag_entity = session.catalog.entities.get("sales.tags")
    allowed = mv.identities(tag_entity, keys=((1,), (3,)))
    selected = tags.where(
        mv.in_population(allowed) & ~mv.in_population(mv.identities(tag_entity, keys=((3,),)))
    )
    selected_rows = session.query(selected).execute().to_pandas()
    assert selected_rows.shape[0] == 1
    assert selected_rows.iloc[0, 0] == 1
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    reached = orders.image(via=path)
    assert session.query(reached).execute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    inverse = orders.preimage(selected, via=path)
    assert session.query(inverse).execute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("INSERT INTO links VALUES (5,3,99)")
    # A complete foreign key is an identity reference even without a tag profile.
    assert session.query(reached).execute().to_pandas().iloc[:, 0].tolist() == [1, 2, 99]


def test_metric_population_condition_selects_facts_without_multiplicity(
    semantic_project_factory, tmp_path, monkeypatch
):
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    selected = mv.coordinates(by=(customer,), keys=((1,), (1,)))
    result = (
        session.observe(
            {
                "selected": revenue.where(mv.in_population(selected)),
                "other": revenue.where(~mv.in_population(selected)),
            }
        )
        .execute()
        .to_pandas()
    )
    assert result[("value", "selected")].tolist() == [40]
    assert result[("value", "other")].tolist() == [100]


@pytest.mark.parametrize("source", ["members", "query", "product"])
@pytest.mark.parametrize("empty", [False, True])
def test_zero_axis_population_preserves_the_unit_tuple(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    empty: bool,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    if empty:
        with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
            connection.execute("DELETE FROM orders")
    if source == "query":
        revenue = session.catalog.metrics.get("sales.revenue")
        customer = session.catalog.fields.get("sales.orders.customer")
        domain = session.observe({"revenue": revenue}, by=(customer,)).population()
    else:
        domain = session.members(ms.ref.entity("sales.orders"))
    unit = domain.project(by=())
    if source == "product":
        unit = mv.product(unit, session.members(ms.ref.entity("sales.tags"))).project(by=())
    result = session.query(unit).execute().to_pandas()
    assert result.shape == (0 if empty else 1, 0)
