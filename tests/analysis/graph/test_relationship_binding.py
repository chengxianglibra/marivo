"""Relationship roles and independent contribution axes use original source facts."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import zlib
from dataclasses import replace

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode, topology
from marivo.analysis.core.rules import ObserveMetric
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_snapshot import (
    MethodRecord,
    document_json,
    graph_document,
    thaw_graph,
)
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.documentation import _example
from tests.support.paths import PROJECT_ROOT

CUSTOMER = ms.ref.entity("sales.customer")
ORDER = ms.ref.entity("sales.order")
LINE = ms.ref.entity("sales.order_line")
BUYER = ms.ref.relationship("sales.order_buyer")
LINE_ORDER = ms.ref.relationship("sales.line_order")
REVENUE = ms.ref.metric("sales.revenue")
LINE_REVENUE = ms.ref.metric("sales.line_revenue")
REGION = ms.ref.dimension("sales.customer.region")
CATEGORY = ms.ref.dimension("sales.product.category")
LINE_PRODUCT = ms.ref.relationship("sales.line_product")
RECIPIENT = ms.ref.relationship("sales.order_recipient")
WINDOW = mv.time_scope(start="2026-08-01", end="2026-09-01")


def _extend(case: DslCase, *, roles: bool = False, order_product: bool = False) -> mv.Session:
    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute("CREATE TABLE product (id VARCHAR, category VARCHAR)")
        connection.execute("INSERT INTO product VALUES ('P', 'book'), ('Q', 'game')")
        connection.execute("ALTER TABLE order_line ADD COLUMN product_id VARCHAR")
        connection.execute(
            "INSERT INTO order_line VALUES ('a1','j1_a',100,'P'), ('a2','j1_a',200,'P'), ('a3','j1_a',150,'Q'), ('b1','j1_b',150,'Q'), ('c1','j1_c',400,'P'), ('outside','j1_july',77,'missing')"
        )
        if roles:
            connection.execute('ALTER TABLE "order" ADD COLUMN recipient_id VARCHAR')
            connection.execute(
                "UPDATE \"order\" SET recipient_id = CASE order_id WHEN 'j1_a' THEN 'C' WHEN 'j1_b' THEN 'A' ELSE 'B' END"
            )
        if order_product:
            connection.execute('ALTER TABLE "order" ADD COLUMN product_id VARCHAR')
            connection.execute(
                "UPDATE \"order\" SET product_id = CASE order_id WHEN 'j1_b' THEN 'Q' ELSE 'P' END"
            )
    finally:
        connection.close()
    model = case.root / "models/semantic/sales/models.py"
    extra = """
product = ms.entity(name='product', datasource=warehouse, source=md.table('product'), primary_key=['id'])
product_id = ms.dimension_column(name='id', entity=product, column='id')
product_category = ms.dimension_column(name='category', entity=product, column='category')
line_product_id = ms.dimension_column(name='product_id', entity=lines, column='product_id')
line_product = ms.relationship(name='line_product', from_entity=lines, to_entity=product,
    keys=[ms.join_on(line_product_id, product_id)])
"""
    if roles:
        extra += """
recipient_id = ms.dimension_column(name='recipient_id', entity=orders, column='recipient_id')
recipient = ms.relationship(name='order_recipient', from_entity=orders, to_entity=customer,
    keys=[ms.join_on(recipient_id, customer_id)])
"""
    if order_product:
        extra += """
order_product_id = ms.dimension_column(name='product_id', entity=orders, column='product_id')
order_product = ms.relationship(name='order_product', from_entity=orders, to_entity=product,
    keys=[ms.join_on(order_product_id, product_id)])
"""
    model.write_text(model.read_text() + extra)
    ms.load(workspace_dir=case.root)
    return mv.session.get_or_create("relationship-bindings", report_timezone="UTC")


def test_inference_and_root_identity(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    customers = case.session.members(CUSTOMER)
    automatic = customers.observe(LINE_REVENUE, during=WINDOW)
    explicit = customers.observe(
        LINE_REVENUE, during=WINDOW, via=mv.route(LINE, through=(LINE_ORDER, BUYER))
    )
    assert automatic._node.root.signature.quantity == explicit._node.root.signature.quantity
    orders = case.session.members(ORDER)
    assert (
        orders.read(REGION)._node.classification_coordinate()
        == orders.read(REGION, via=BUYER)._node.classification_coordinate()
    )
    assert customers.read(REGION, via=None)._node.classification_coordinate().binding_id == "direct"
    ratio = ms.ref.metric("sales.aov_from_lines")
    forward = mv.routes(
        mv.route(LINE, through=(LINE_ORDER, BUYER)), mv.route(ORDER, through=(BUYER,))
    )
    reverse = mv.routes(*reversed(forward.routes))
    partial = mv.routes(mv.route(LINE, through=(LINE_ORDER, BUYER)))
    quantities = [
        customers.observe(ratio, during=WINDOW, via=via)._node.root.signature.quantity
        for via in (None, forward, reverse, partial)
    ]
    assert all(q == quantities[0] for q in quantities)
    with pytest.raises(AnalysisError):
        customers.read(REGION, via=forward)
    identity_root = orders.observe(ratio, during=WINDOW, via=mv.routes(mv.route(ORDER, through=())))
    assert identity_root._node.root.signature.quantity is not None


def test_coordinate_tampering_and_frozen_roles(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    customers = case.session.members(CUSTOMER)
    original = customers.observe(
        REVENUE, during=WINDOW, by=(ms.ref.dimension("sales.order.channel"),)
    )
    document = graph_document(original._node.root)
    observed = next(
        n
        for n in document.nodes
        if isinstance(n, MethodRecord) and isinstance(n.parameters, ObserveMetric)
    )
    coordinate = observed.parameters.classification_coordinates[0]
    altered = replace(
        observed,
        parameters=replace(
            observed.parameters,
            classification_coordinates=(replace(coordinate, binding_id="tampered"),),
        ),
    )
    document = replace(
        document,
        nodes=tuple(altered if n.identity == observed.identity else n for n in document.nodes),
    )
    from marivo.analysis.materialization.graph_snapshot import PREFIX

    with pytest.raises(AnalysisError):
        thaw_graph(
            PREFIX
            + base64.b64encode(zlib.compress(document_json(document).encode(), level=9)).decode()
        )
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text()
        + "\nalternative = ms.relationship(name='alternative_buyer', from_entity=orders, to_entity=customer, keys=[ms.join_on(order_customer_id, customer_id)])\n"
    )
    ms.load(workspace_dir=case.root)
    with pytest.raises(AnalysisError):
        mv.session.get_or_create("new-role", report_timezone="UTC").members(CUSTOMER).observe(
            REVENUE, during=WINDOW
        )
    frozen = next(
        n
        for n in topology(original._node.root)
        if isinstance(n, MethodNode) and isinstance(n.parameters, ObserveMetric)
    )
    assert tuple(hop.ref.path for hop in frozen.parameters.path) == (BUYER.path,)


@pytest.mark.runtime
def test_roles_and_independent_read_paths(analysis_dsl_case_factory: DslCaseFactory) -> None:
    session = _extend(analysis_dsl_case_factory("j1"), roles=True)
    customers, orders = session.members(CUSTOMER), session.members(ORDER)
    with pytest.raises(AnalysisError) as error:
        customers.observe(REVENUE, during=WINDOW)
    assert error.value.repair is not None
    assert set(error.value.repair.candidates) == {BUYER.path, RECIPIENT.path}
    assert error.value.repair.snippet is not None
    namespace: dict[str, object] = {"mv": mv, "ms": ms}
    exec(compile(error.value.repair.snippet, "relationship-repair", "exec"), namespace)
    choices = namespace["choices"]
    assert isinstance(choices, tuple)
    expected_roles = (
        {"A": 450, "B": 150, "C": 400},
        {"A": 150, "B": 400, "C": 450},
    )
    for choice, expected in zip(choices, expected_roles, strict=True):
        assert isinstance(choice, mv.RootRoute)
        observed = customers.observe(REVENUE, during=WINDOW, via=choice, by=(mv.member(),))
        frame = observed.execute().to_pandas().set_index("member")
        assert frame.loc[frame["cell_tag"] == "defined", "value"].to_dict() == expected
        assert frame.loc["D", "cell_tag"] == "null"
    with pytest.raises(AnalysisError):
        orders.read(REGION)
    buyer, recipient = orders.read(REGION, via=BUYER), orders.read(REGION, via=RECIPIENT)
    assert buyer._node.classification_coordinate() != recipient._node.classification_coordinate()
    grouped = customers.observe(REVENUE, during=WINDOW, via=BUYER, by=(recipient,))
    assert grouped.execute().to_pandas().set_index("group")["value"].to_dict() == {
        "east": 550,
        "south": 450,
    }
    both = customers.observe(REVENUE, during=WINDOW, via=BUYER, by=(buyer, recipient))
    assert both.execute().to_pandas().set_index(["group", "coord_0"])["value"].to_dict() == {
        ("east", "south"): 450,
        ("east", "east"): 150,
        ("south", "east"): 400,
    }
    with pytest.raises(AnalysisError):
        both.group_by(REGION)
    assert both.group_by(recipient).execute().to_pandas().set_index("group")["value"].to_dict() == {
        "east": 550,
        "south": 450,
    }
    current = both.rollup()
    baseline = customers.observe(
        REVENUE,
        during=mv.time_scope(start="2026-07-01", end="2026-08-01"),
        via=BUYER,
        by=(buyer, recipient),
    ).rollup()
    for change in (current.compare(baseline), current.compare(baseline).execute()):
        with pytest.raises(AnalysisError):
            change.attribute(axes=(REGION,))
        attributed = change.attribute(axes=(buyer, recipient)).execute()
        assert attributed.contribution.to_pandas().set_index(["coord_0", "coord_1"])[
            "value"
        ].to_dict() == {
            ("east", "south"): 450,
            ("east", "east"): 73,
            ("south", "east"): 400,
        }


def test_classification_scope_and_complete_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = _extend(analysis_dsl_case_factory("j1"))
    members, lines = session.members(CUSTOMER), session.members(LINE)
    category = lines.read(CATEGORY)
    with pytest.raises(AnalysisError):
        members.observe(LINE_REVENUE, during=WINDOW, by=(category, category))
    with pytest.raises(AnalysisError):
        members.observe(
            LINE_REVENUE,
            during=WINDOW,
            by=(session.members(ms.ref.entity("sales.product")).read(CATEGORY),),
        )
    other = mv.session.get_or_create("another-session", report_timezone="UTC")
    with pytest.raises(AnalysisError):
        members.observe(LINE_REVENUE, during=WINDOW, by=(other.members(LINE).read(CATEGORY),))
    ratio = mv.runtime_metric.ratio(
        numerator=LINE_REVENUE, denominator=REVENUE, label="missing map"
    )
    with pytest.raises(AnalysisError):
        members.observe(ratio, during=WINDOW, by=(category,))
    grid = mv.time_grid(during=WINDOW, grain=mv.grain("week"))
    with pytest.raises(AnalysisError):
        members.observe(LINE_REVENUE, during=WINDOW, by=(lines.read(CATEGORY, at=grid.before_end),))


@pytest.mark.runtime
def test_branch_and_consumed_coverage(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case)
    customers, lines = session.members(CUSTOMER), session.members(LINE)
    category = lines.read(CATEGORY, via=LINE_PRODUCT)
    automatic = customers.observe(LINE_REVENUE, during=WINDOW, by=(CATEGORY,))
    explicit = customers.observe(LINE_REVENUE, during=WINDOW, by=(category,))
    assert automatic._node.root.signature.quantity == explicit._node.root.signature.quantity
    expected = {"book": 700, "game": 300}
    fixed = automatic.execute()
    facts = dict(fixed.contract()._facts)
    assert LINE_ORDER.path in facts["contribution_binding_0"]
    assert LINE_PRODUCT.path in facts["classification_role_0_0"]
    assert LINE.path in facts["classification_role_0_0"]
    assert fixed.to_pandas().set_index("group")["value"].to_dict() == expected
    assert explicit.execute().to_pandas().set_index("group")["value"].to_dict() == expected
    selected = category.where(category.value.eq("book"))
    with pytest.raises(AnalysisError):
        customers.observe(LINE_REVENUE, during=WINDOW, by=(selected,)).execute()
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    assert fixed.rollup().execute().to_pandas()["value"].tolist() == [1000]
    script = """
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
def forbidden(*args, **kwargs):
    raise AssertionError('Fixed branch accessed semantic facts or source data')
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
fixed = mv.session.resume(sys.argv[1], by='id').artifact(sys.argv[2])
assert fixed.rollup().execute().to_pandas()['value'].tolist() == [1000]
assert fixed.group_by(ms.ref.dimension('sales.product.category')).execute().to_pandas().set_index('group')['value'].to_dict() == {'book': 700, 'game': 300}
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, fixed.state.artifact_ref.ref],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.runtime
def test_multiroot_branch_arithmetic(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case, order_product=True)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("INSERT INTO product VALUES ('R', 'spare')")
        connection.execute("UPDATE order_line SET product_id='R' WHERE line_id='outside'")
    members, lines = session.members(CUSTOMER), session.members(LINE)
    category = lines.read(CATEGORY, via=LINE_PRODUCT)
    ratio = mv.runtime_metric.ratio(numerator=LINE_REVENUE, denominator=REVENUE, label="line share")
    linear = mv.runtime_metric.linear(add=[LINE_REVENUE], subtract=[REVENUE], label="line balance")
    for metric, expected in (
        (ratio, {"book": 700 / 850, "game": 2.0}),
        (linear, {"book": -150, "game": 150}),
    ):
        result = members.observe(metric, during=WINDOW, by=(category,)).execute()
        frame = result.to_pandas().set_index("group")
        assert frame.loc[list(expected), "value"].to_dict() == pytest.approx(expected)
        assert set(frame.index) == {"book", "game"}
        assert result.rollup().execute().to_pandas()["value"].tolist() == [
            1.0 if metric is ratio else 0
        ]


@pytest.mark.runtime
def test_bilingual_branch_example_and_time_grid(analysis_dsl_case_factory: DslCaseFactory) -> None:
    session = _extend(analysis_dsl_case_factory("j1"))
    namespace: dict[str, object] = {"session": session, "ms": ms, "mv": mv}
    source = _example("en", "contribution-classification")
    assert source == _example("zh", "contribution-classification")
    exec(compile(source, "contribution-classification-example", "exec"), namespace)
    result = namespace["category_revenue"]
    assert isinstance(result, mv.MaterializedNumericRelation)
    assert result.to_pandas().set_index("group")["value"].to_dict() == {"book": 700, "game": 300}
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    timed = session.members(LINE).read(CATEGORY, at=grid.before_end, via=LINE_PRODUCT)
    grouped = session.members(CUSTOMER).observe(LINE_REVENUE, during=grid, by=(timed,)).execute()
    rows = grouped.to_pandas()
    assert len(rows) == 4
    assert rows["value"].sum() == 1000


@pytest.mark.runtime
@pytest.mark.parametrize("missing_bucket", [False, True])
def test_timed_classification_cannot_remove_consumed_contributions(
    analysis_dsl_case_factory: DslCaseFactory, missing_bucket: bool
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE order_line SET product_id='P' WHERE line_id='outside'")
        if missing_bucket:
            connection.execute("ALTER TABLE product ADD COLUMN beginning DATE")
            connection.execute("ALTER TABLE product ADD COLUMN ending DATE")
            connection.execute(
                "UPDATE product SET category='book', beginning=DATE '2026-08-01', ending=DATE '2026-09-01'"
            )
            connection.execute(
                "INSERT INTO product VALUES ('P','game','2026-09-01',NULL), ('Q','game','2026-09-01',NULL)"
            )
            connection.execute("INSERT INTO order_line VALUES ('september','j1_september',99,'P')")
    if missing_bucket:
        model = case.root / "models/semantic/sales/models.py"
        model.write_text(
            model.read_text().replace(
                "source=md.table('product'), primary_key=['id'])",
                "source=md.table('product'), primary_key=['id'], versioning=ms.validity("
                "valid_from=ms.ref.time_dimension('sales.product.beginning'), "
                "valid_to=ms.ref.time_dimension('sales.product.ending'), "
                "interval='closed_open', open_end=(None,), timezone='UTC'))",
            )
            + "\nproduct_beginning = ms.time_dimension_column(name='beginning', entity=product, column='beginning', granularity='day')\n"
            + "product_ending = ms.time_dimension_column(name='ending', entity=product, column='ending', granularity='day')\n"
        )
        ms.load(workspace_dir=case.root)
        session = mv.session.get_or_create("timed-coverage", report_timezone="UTC")
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    category = session.members(LINE).read(CATEGORY, at=grid.before_end)
    customers = session.members(CUSTOMER)
    complete = customers.observe(LINE_REVENUE, during=grid, by=(category,)).execute().to_pandas()
    assert complete["value"].sum() == (1099 if missing_bucket else 1000)
    selected = category.where(category.value.eq("book"))
    with pytest.raises(AnalysisError, match="Expected: mapping_total"):
        customers.observe(LINE_REVENUE, during=grid, by=(selected,)).execute()
