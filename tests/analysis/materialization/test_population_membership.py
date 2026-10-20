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
from tests.shared_fixtures import DslCaseFactory

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
    return a.sum(empty="zero")
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


@pytest.mark.parametrize("consumer_window", (False, True))
def test_named_metric_population_dependency_owns_its_window_scope(
    analysis_dsl_case_factory: DslCaseFactory, consumer_window: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.metrics.get("sales.revenue")
    count = session.catalog.metrics.get("sales.order_count")
    channel = session.catalog.fields.get("sales.order.channel")
    producer = session.observe(
        {"total": revenue.window(mv.all_history())},
        by=(channel,),
        during=session.time_scope(start="2026-08-01", end="2026-09-01"),
    )
    chosen = producer.where(producer["total"] > 900)
    selected = session.population_from(chosen, on=((chosen.axes[0], channel),))
    condition = mv.in_population(selected)
    observed = session.observe(
        {"rows": count.where(condition).window(mv.all_history()) if consumer_window else count},
        where=None if consumer_window else condition,
        during=session.time_scope(start="2026-07-31", end="2026-08-01"),
    )
    # The upstream all-history endpoint selects web (927). The consumer's
    # July endpoint includes only the earlier web fact under either rule.
    assert observed.compute().to_pandas()[("value", "rows")].tolist() == [1]
    assert producer.compute().to_pandas()[("value", "total")].tolist() == [150, 927]


def test_members_scalar_predicate_retains_its_relationship_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = session.catalog.fields.get("sales.customer.region")
    buyer = session.catalog.relationships.get("sales.order_buyer")
    orders = session.members(ms.ref.entity("sales.order"), where=region.via((buyer,)) == "east")
    keys = session.frame(orders).compute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["j1_a", "j1_b", "j1_july", "j1_september"]
    revenue = session.catalog.metrics.get("sales.revenue")
    result = (
        session.observe(
            {"revenue": revenue},
            during=session.time_scope(start="2026-08-01", end="2026-09-01"),
            members=orders,
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "revenue")].tolist() == [600]


def test_members_scalar_predicate_retains_intermediate_relationship_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    session = case.session
    customer_id = session.catalog.fields.get("sales.customer.customer_id")
    path = (
        session.catalog.relationships.get("sales.line_order"),
        session.catalog.relationships.get("sales.order_buyer"),
    )
    lines = session.members(ms.ref.entity("sales.order_line"), where=customer_id.via(path) == "A")
    keys = session.frame(lines).compute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["j3_l1", "j3_l2"]
    revenue = session.catalog.metrics.get("sales.line_revenue")
    result = session.observe({"revenue": revenue}, members=lines).compute().to_pandas()
    assert result[("value", "revenue")].tolist() == [100]


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
        .compute()
        .to_pandas()
    )
    assert result[("value", "aov_mean")].tolist() == [60.0]
    assert result[("value", "fixed_revenue_mean")].tolist() == [pytest.approx(140 / 3)]
    assert population_keys(
        full.difference(mv.coordinates(by=(customer,), keys=((2,),)))._definition
    ) == ((1,), (3,))
    assert population_keys(full.project(by=())._definition) == ((),)
    assert len(session.frame(full).compute().to_pandas()) == 3
    omitted = mv.coordinates(by=(customer,), keys=((1,),))
    observed = session.observe({"revenue": revenue}, by=omitted)
    captured = session.population_from(observed, on=((observed.axes[0], customer),))
    with pytest.raises(MaterializationError) as failure:
        session.frame(captured).compute()
    assert failure.value.expected is not None
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
    result = session.observe({"revenue": revenue}, by=full).compute().to_pandas()
    assert result[("value", "revenue")].tolist() == [20, 10, 0]
    unmatched = mv.membership(label, via=(path,), unmatched="null")
    with_null = session.observe({"revenue": revenue}, by=(unmatched,)).compute().to_pandas()
    assert sorted(with_null[("value", "revenue")].tolist()) == [10, 20, 100]
    samples = session.observe({"mean": revenue.per(full).mean()}).compute().to_pandas()
    assert samples[("value", "mean")].tolist() == [10]
    classified = session.members(ms.ref.entity("sales.tags")).classify(by=(buckets,))
    result = session.observe({"revenue": revenue}, by=classified).compute().to_pandas()
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
    correlated = session.observe({"revenue": revenue}, by=(joint,)).compute().to_pandas()
    independent = session.observe({"revenue": revenue}, by=(tags, channels)).compute().to_pandas()
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
    selected_rows = session.frame(selected).compute().to_pandas()
    assert selected_rows.shape[0] == 1
    assert selected_rows.iloc[0, 0] == 1
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    reached = orders.image(via=path)
    assert session.frame(reached).compute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    inverse = orders.preimage(selected, via=path)
    assert session.frame(inverse).compute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("INSERT INTO links VALUES (5,3,99)")
    # A complete foreign key is an identity reference even without a tag profile.
    assert session.frame(reached).compute().to_pandas().iloc[:, 0].tolist() == [1, 2, 99]


def test_metric_population_condition_selects_facts_without_multiplicity(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        .compute()
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
        observed = session.observe({"revenue": revenue}, by=(customer,))
        domain = session.population_from(observed, on=((observed.axes[0], customer),))
    else:
        domain = session.members(ms.ref.entity("sales.orders"))
    unit = domain.project(by=())
    if source == "product":
        unit = mv.product(unit, session.members(ms.ref.entity("sales.tags"))).project(by=())
    result = session.frame(unit).compute().to_pandas()
    assert result.shape == (0 if empty else 1, 0)


def test_medium_complete_identity_domain_materializes_and_filters_preimage(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    entity = session.catalog.entities.get("sales.tags")
    literal = mv.identities(entity, keys=tuple((key,) for key in range(1, 2001)))
    frame = session.frame(literal).compute().to_pandas()
    assert sorted(frame.iloc[:, 0].tolist()) == list(range(1, 2001))
    fixed = session.frame(literal).compute()
    targets = session.population_from(fixed, on=((fixed.axes[0], entity),))
    path = (
        session.catalog.relationships.get("sales.order_links"),
        session.catalog.relationships.get("sales.link_tags"),
    )
    selected = session.members(ms.ref.entity("sales.orders")).preimage(targets, via=path)
    revenue = session.catalog.metrics.get("sales.revenue")
    result = session.observe({"value": revenue}, members=selected).compute().to_pandas()
    assert result[("value", "value")].tolist() == [40]
