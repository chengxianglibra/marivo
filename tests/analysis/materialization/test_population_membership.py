"""Population selection and membership allocation retain contribution identities."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.dsl.population import population_keys
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory, catalog_value
from tests.support.source_trace import capture_source

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
# Named expressions
a = amount
@ms.metric()
def revenue():
    return a.sum()
"""


def _session(
    factory: Callable[..., SemanticProject],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str = _MODEL,
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
            "sales/objects.py": model,
        },
        load=False,
    )
    monkeypatch.chdir(root)
    return mv.session.get_or_create("population", timezone="UTC")


@pytest.mark.parametrize("consumer_window", (False, True))
def test_named_metric_population_dependency_owns_its_window_scope(
    analysis_dsl_case_factory: DslCaseFactory, consumer_window: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    revenue = session.catalog.get("metric:sales.revenue").value
    count = session.catalog.get("metric:sales.order_count").value
    channel = catalog_value(session.catalog, "sales.order.channel", kind="field")
    producer = session.observe(
        {"total": revenue.window(mv.all_history())},
        by=(session.time_scope(start="2026-08-01", end="2026-09-01"), channel),
    )
    chosen = producer.where(producer["total"] > 900)
    selected = session.population_from(chosen, on=((chosen.axes[1], channel),))
    condition = mv.in_population(selected)
    observed = session.observe(
        {"rows": count.where(condition).window(mv.all_history()) if consumer_window else count},
        by=(session.time_scope(start="2026-07-31", end="2026-08-01"),),
        where=None if consumer_window else condition,
    )
    # The upstream all-history endpoint selects web (927). The consumer's
    # July endpoint includes only the earlier web fact under either rule.
    assert observed.compute().to_pandas()["rows"].tolist() == [1]
    assert producer.compute().to_pandas()["total"].tolist() == [150, 927]


def test_members_scalar_predicate_retains_its_relationship_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    region = catalog_value(session.catalog, "sales.customer.region", kind="field")
    buyer = session.catalog.get("relationship:sales.order_buyer").value
    orders = session.members(
        session.catalog.get("entity:sales.order").value, where=region.via((buyer,)) == "east"
    )
    keys = session.frame(orders).compute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["j1_a", "j1_b", "j1_july", "j1_september"]
    revenue = session.catalog.get("metric:sales.revenue").value
    result = (
        session.observe(
            {"revenue": revenue},
            by=(session.time_scope(start="2026-08-01", end="2026-09-01"),),
            where=mv.in_population(orders),
        )
        .compute()
        .to_pandas()
    )
    assert result["revenue"].tolist() == [600]


def test_members_scalar_predicate_retains_intermediate_relationship_sources(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j3")
    session = case.session
    customer_id = catalog_value(session.catalog, "sales.customer.customer_id", kind="field")
    path = (
        session.catalog.get("relationship:sales.line_order").value,
        session.catalog.get("relationship:sales.order_buyer").value,
    )
    lines = session.members(
        session.catalog.get("entity:sales.order_line").value,
        where=customer_id.via(path) == "A",
    )
    keys = session.frame(lines).compute().to_pandas()
    assert keys.iloc[:, 0].tolist() == ["j3_l1", "j3_l2"]
    revenue = session.catalog.get("metric:sales.line_revenue").value
    result = (
        session.observe({"revenue": revenue}, where=mv.in_population(lines)).compute().to_pandas()
    )
    assert result["revenue"].tolist() == [100]


def test_identity_membership_uses_equality_while_null_field_categories_still_match(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("UPDATE orders SET customer=NULL WHERE id=3")
    entity = session.catalog.get("entity:sales.orders").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    selected = mv.coordinates(by=(mv.identity(entity), customer), keys=(((1,), 1), ((3,), None)))
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {"value": session.catalog.get("metric:sales.revenue").value},
            where=mv.in_population(selected),
        )
        .compute()
        .to_pandas()
    )
    assert result["value"].tolist() == [110]
    assert sum(sql.count("IS NOT DISTINCT FROM") for sql in trace.native_sql) == 1


def test_per_completes_ratio_per_sample_and_retains_empty_samples(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    revenue = session.catalog.get("metric:sales.revenue").value
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
    assert result["aov_mean"].tolist() == [60.0]
    assert result["fixed_revenue_mean"].tolist() == [47]
    assert population_keys(
        full.difference(mv.coordinates(by=(customer,), keys=((2,),)))._definition
    ) == ((1,), (3,))
    assert population_keys(full.project(by=())._definition) == ((),)
    assert len(session.frame(full).compute().to_pandas()) == 3
    omitted = mv.coordinates(by=(customer,), keys=((1,),))
    observed = session.observe({"revenue": revenue}, by=omitted)
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        observed.compute()
    assert error.value.stage == "population_coverage"
    assert session.observe({"v": revenue.per(omitted).mean()}).compute().to_pandas()[
        "v"
    ].tolist() == [40]


def test_membership_deduplicates_root_bucket_and_complete_domain(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=10 WHERE id=2")
    revenue = session.catalog.get("metric:sales.revenue").value
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    buckets = mv.membership(label, via=(path,), unmatched="exclude")
    full = mv.coordinates(by=(label,), keys=(("A",), ("B",), ("C",)))
    result = session.observe({"revenue": revenue}, by=(full, buckets)).compute().to_pandas()
    assert result["revenue"].tolist() == [20, 10, 0]
    unmatched = mv.membership(label, via=(path,), unmatched="null")
    with_null = session.observe({"revenue": revenue}, by=(unmatched,)).compute().to_pandas()
    assert sorted(with_null["revenue"].tolist()) == [10, 20, 100]
    samples = session.observe({"mean": revenue.per((full, buckets)).mean()}).compute().to_pandas()
    assert samples["mean"].tolist() == [10]
    classified = session.members(session.catalog.get("entity:sales.tags").value).classify(
        by=(label,)
    )
    result = session.observe({"revenue": revenue}, by=(classified, buckets)).compute().to_pandas()
    assert result["revenue"].tolist() == [20, 10, 0]


def test_membership_correlated_tuple_differs_from_independent_product(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.get("metric:sales.revenue").value
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    channel = catalog_value(session.catalog, "sales.tags.channel", kind="field")
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    joint = mv.membership((label, channel), via=(path,), unmatched="exclude")
    tags = mv.membership(label, via=(path,), unmatched="exclude")
    channels = mv.membership(channel, via=(path,), unmatched="exclude")
    correlated = session.observe({"revenue": revenue}, by=(joint,)).compute().to_pandas()
    independent = session.observe({"revenue": revenue}, by=(tags, channels)).compute().to_pandas()
    assert len(correlated) == 2
    assert len(independent) == 4
    assert sorted(independent["revenue"].tolist()) == [10, 10, 10, 40]


@pytest.mark.parametrize("outer_route", ("direct", "association"))
def test_nested_per_keeps_each_layers_membership_rule(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_route: str,
) -> None:
    model = _MODEL.replace(
        "# Named expressions",
        'order_direct = ms.relationship(name="order_direct", from_entity=orders, '
        "to_entity=tags, keys=[ms.join_on(customer, tid)])\n# Named expressions",
    )
    session = _session(semantic_project_factory, tmp_path, monkeypatch, model=model)
    revenue = session.catalog.get("metric:sales.revenue").value
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    association_path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    association = mv.membership(label, via=(association_path,), unmatched="null")
    direct = mv.membership(
        label,
        via=((session.catalog.get("relationship:sales.order_direct").value,),),
        unmatched="null",
    )
    inner, outer = (association, direct) if outer_route == "direct" else (direct, association)
    samples = mv.coordinates(by=(label,), keys=(("A",), ("B",), (None,)))
    inner_samples = revenue.per((samples, inner))
    association_subset = mv.coordinates(by=(label,), keys=(("A",), ("B",)))
    direct_subset = mv.coordinates(by=(label,), keys=(("A",), ("C",)))
    inner_domain, outer_domain = (
        (association_subset, direct_subset)
        if outer_route == "direct"
        else (direct_subset, association_subset)
    )
    result = (
        session.observe(
            {
                "mean_of_means": inner_samples.mean().per((samples, outer)).mean(),
                "mean_of_maxima": inner_samples.max().per((samples, outer)).mean(),
                "subset_maxima": revenue.per((inner_domain, inner))
                .max()
                .per((outer_domain, outer))
                .mean(),
            }
        )
        .compute()
        .to_pandas()
    )
    # Association/direct bucket pairs carry 40, 10, 100 across nine complete cells.
    # Reversing the layers preserves their sum but changes their three maxima.
    # Bound Decimal(38,0) rounds each inner mean: direct layers produce
    # [17,33,0], association layers [13,3,33], before the outer mean.
    assert result["mean_of_means"].tolist() == [17 if outer_route == "direct" else 16]
    assert result["mean_of_maxima"].tolist() == [47 if outer_route == "direct" else 50]
    # The selected rectangle carries A/A=40 and B/A=10, with empty C samples.
    # Grouping by direct first yields maxima 40/0; association first yields 40/10.
    assert result["subset_maxima"].tolist() == [20 if outer_route == "direct" else 25]


def test_population_filters_and_relationship_images_preserve_typed_keys(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    orders = session.members(session.catalog.get("entity:sales.orders").value)
    tags = session.members(session.catalog.get("entity:sales.tags").value)
    tag_entity = session.catalog.get("entity:sales.tags").value
    allowed = mv.coordinates(by=(mv.identity(tag_entity),), keys=(((1,),), ((3,),)))
    selected = tags.where(
        mv.in_population(allowed)
        & ~mv.in_population(mv.coordinates(by=(mv.identity(tag_entity),), keys=(((3,),),)))
    )
    selected_rows = session.frame(selected).compute().to_pandas()
    assert selected_rows.shape[0] == 1
    assert selected_rows.iloc[0, 0] == 1
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
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
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
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
    assert result["selected"].tolist() == [40]
    assert result["other"].tolist() == [100]


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
        revenue = session.catalog.get("metric:sales.revenue").value
        customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
        observed = session.observe({"revenue": revenue}, by=(customer,))
        domain = session.population_from(observed, on=((observed.axes[0], customer),))
    else:
        domain = session.members(session.catalog.get("entity:sales.orders").value)
    unit = domain.project(by=())
    if source == "product":
        unit = mv.product(
            unit, session.members(session.catalog.get("entity:sales.tags").value)
        ).project(by=())
    result = session.frame(unit).compute().to_pandas()
    assert result.shape == (0 if empty else 1, 0)


def test_medium_complete_identity_domain_materializes_and_filters_preimage(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    entity = session.catalog.get("entity:sales.tags").value
    literal = mv.coordinates(
        by=(mv.identity(entity),),
        keys=tuple(((key,),) for key in range(1, 2001)),
    )
    fixed = session.frame(literal).compute()
    frame = fixed.to_pandas()
    assert sorted(frame.iloc[:, 0].tolist()) == list(range(1, 2001))
    targets = session.population_from(fixed, on=((fixed.axes[0], entity),))
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    selected = session.members(session.catalog.get("entity:sales.orders").value).preimage(
        targets, via=path
    )
    revenue = session.catalog.get("metric:sales.revenue").value
    result = (
        session.observe({"value": revenue}, where=mv.in_population(selected)).compute().to_pandas()
    )
    assert result["value"].tolist() == [40]


def test_output_coverage_and_internal_subset_have_distinct_policies(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.get("metric:sales.revenue").value
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    channel = catalog_value(session.catalog, "sales.tags.channel", kind="field")
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    allocation = mv.membership((label, channel), via=(path,), unmatched="exclude")
    selected = mv.coordinates(by=(label, channel), keys=(("A", "web"),))
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        session.observe({"v": revenue}, by=(selected, allocation)).compute()
    assert error.value.stage == "population_coverage"
    assert "B" in error.value.received and "store" in error.value.received
    # Selecting a fact through A still retains that fact's B allocation.
    with pytest.raises(MaterializationError, match="missing output coordinates"):
        session.observe(
            {"v": revenue}, by=(selected, allocation), where=mv.in_population(selected)
        ).compute()
    complete = mv.coordinates(
        by=(label, channel), keys=(("A", "web"), ("B", "store"), ("C", "other"))
    )
    result = session.observe({"v": revenue}, by=(complete, allocation)).compute().to_pandas()
    assert list(result.itertuples(index=False, name=None)) == [
        ("A", "web", 40),
        ("B", "store", 10),
        ("C", "other", 0),
    ]
    # Every required component exists in these projections, but the tuples differ.
    crossed = mv.coordinates(by=(label, channel), keys=(("A", "store"), ("B", "web")))
    with pytest.raises(MaterializationError, match="missing output coordinates"):
        session.observe({"v": revenue}, by=(crossed, allocation)).compute()
    with_empty = mv.coordinates(by=(label, channel), keys=(("A", "web"), ("C", "other")))
    result = (
        session.observe(
            {
                "selected": revenue.per((selected, allocation)).mean(),
                "completed": revenue.per((with_empty, allocation)).mean(),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result["selected"].tolist() == [40]
    assert result["completed"].tolist() == [20]


@pytest.mark.parametrize(
    "sampling", ("missing", "empty", "nested_missing", "nested_empty", "outer_empty")
)
def test_sample_selection_preserves_required_external_coordinates(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sampling: str,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    order = catalog_value(session.catalog, "sales.orders.id", kind="field")
    missing = mv.coordinates(by=(order,), keys=((999,),))
    empty = mv.coordinates(by=(order,), keys=())
    another = mv.coordinates(by=(order,), keys=((1,), (2,)))
    metric = revenue.per(empty if sampling in ("empty", "nested_empty") else missing).mean()
    if sampling.startswith("nested"):
        metric = metric.per(another).mean()
    elif sampling == "outer_empty":
        metric = revenue.per(another).mean().per(empty).mean()
    complete = mv.coordinates(by=(customer,), keys=((1,), (2,)))
    result = session.observe({"v": metric}, by=complete).compute().to_pandas()
    assert result.iloc[:, 0].tolist() == [1, 2]
    if "empty" in sampling:
        assert result["v"].isna().tolist() == [True, True]
    else:
        assert result["v"].tolist() == [0, 0]
    subset = mv.coordinates(by=(customer,), keys=((1,),))
    failed = session.observe({"v": metric}, by=subset)
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        failed.top(0, by=failed["v"]).compute()
    assert error.value.stage == "population_coverage"
    assert "2" in error.value.received
    filtered = session.observe({"v": metric}, by=subset, where=customer == 1).compute().to_pandas()
    assert filtered.iloc[:, 0].tolist() == [1]
    assert pd.isna(filtered["v"].iloc[0]) if "empty" in sampling else filtered["v"].tolist() == [0]


def test_empty_output_population_checks_fact_keys_including_zero_axis(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    for domain in (mv.coordinates(by=(customer,), keys=()), mv.coordinates(by=(), keys=())):
        with pytest.raises(MaterializationError, match="missing output coordinates"):
            session.observe({"v": revenue}, by=domain).compute()
        result = (
            session.observe({"v": revenue}, by=domain, where=customer == 999).compute().to_pandas()
        )
        assert result.empty
    singleton = mv.coordinates(by=(), keys=((),))
    result = session.observe({"v": revenue}, by=singleton).compute().to_pandas()
    assert result["v"].tolist() == [140]


def test_output_coverage_keeps_null_allocation_and_independent_fact_roots(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = (
        _MODEL.replace(
            "# Named expressions",
            'refunds = ms.entity(name="refunds", datasource=warehouse, source=md.table("refunds"), primary_key=["id"])\n'
            'refund_customer = ms.dimension_column(name="customer", entity=refunds, column="customer")\n'
            'refund_amount = ms.measure_column(name="amount", entity=refunds, column="amount", additivity=ms.additive_all())\n'
            'refund_tags = ms.relationship(name="refund_tags", from_entity=refunds, to_entity=tags, keys=[ms.join_on(refund_customer, tid)])\n'
            "# Named expressions",
        )
        + "\nr = refund_amount\n@ms.metric()\ndef refunded():\n    return r.sum()\n"
    )
    session = _session(semantic_project_factory, tmp_path, monkeypatch, model=model)
    with duckdb.connect(str(tmp_path / "population.duckdb")) as connection:
        connection.execute("CREATE TABLE refunds(id BIGINT, customer BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO refunds VALUES (1,3,7)")
    revenue = session.catalog.get("metric:sales.revenue").value
    refunded = session.catalog.get("metric:sales.refunded").value
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    allocation = mv.membership(label, via=(path,), unmatched="null")
    nonnull = mv.coordinates(by=(label,), keys=(("A",), ("B",)))
    with pytest.raises(MaterializationError, match="missing output coordinates"):
        session.observe({"v": revenue}, by=(nonnull, allocation)).compute()
    full = mv.coordinates(by=(label,), keys=(("A",), ("B",), (None,)))
    result = session.observe({"v": revenue}, by=(full, allocation)).compute().to_pandas()
    assert dict(zip(result.iloc[:, 0].fillna("<null>"), result["v"], strict=True)) == {
        "A": 40,
        "B": 10,
        "<null>": 100,
    }
    independent = mv.membership(
        label,
        via=(path, (session.catalog.get("relationship:sales.refund_tags").value,)),
        unmatched="exclude",
    )
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        session.observe({"v": revenue + refunded}, by=(nonnull, independent)).compute()
    assert "C" in error.value.received
    complete = mv.coordinates(by=(label,), keys=(("A",), ("B",), ("C",)))
    result = (
        session.observe({"v": revenue + refunded}, by=(complete, independent)).compute().to_pandas()
    )
    assert result["v"].tolist() == [40, 10, 7]


@pytest.mark.parametrize("sampling", ("selected", "open", "nested"))
def test_internal_membership_exclusion_preserves_parent_coordinates(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sampling: str,
) -> None:
    session = _session(semantic_project_factory, tmp_path, monkeypatch)
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    label = catalog_value(session.catalog, "sales.tags.label", kind="field")
    path = (
        session.catalog.get("relationship:sales.order_links").value,
        session.catalog.get("relationship:sales.link_tags").value,
    )
    allocation = mv.membership(label, via=(path,), unmatched="exclude")
    samples = mv.coordinates(by=(label,), keys=(("A",), ("B",), ("C",)))
    metric = revenue.per((allocation,) if sampling == "open" else (samples, allocation)).mean()
    if sampling == "nested":
        order = catalog_value(session.catalog, "sales.orders.id", kind="field")
        metric = metric.per(mv.coordinates(by=(order,), keys=((999,),))).mean()
    complete = mv.coordinates(by=(customer,), keys=((1,), (2,)))
    for output in ((customer,), complete):
        rows = session.observe({"v": metric}, by=output).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == [1, 2]
        expected = [25, None] if sampling == "open" else [0, 0] if sampling == "nested" else [17, 0]
        assert rows["v"].iloc[0] == expected[0]
        assert (
            pd.isna(rows["v"].iloc[1]) if expected[1] is None else rows["v"].iloc[1] == expected[1]
        )
    subset = mv.coordinates(by=(customer,), keys=((1,),))
    with pytest.raises(MaterializationError, match="missing output coordinates") as error:
        session.observe({"v": metric}, by=subset).compute()
    assert "2" in error.value.received
    rows = session.observe({"v": metric}, by=subset, where=customer == 1).compute().to_pandas()
    assert rows.iloc[:, 0].tolist() == [1]
