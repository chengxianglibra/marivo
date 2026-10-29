"""Relationship roles and independent contribution axes use original source facts."""

from __future__ import annotations

import os
import subprocess
import sys

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.types import AxisBinding
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.refs import ref as private_ref
from tests.analysis.graph.execution_fixtures import source_graph
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import DslCase, DslCaseFactory, catalog_value, terminal_runs
from tests.support.paths import PROJECT_ROOT

CUSTOMER = private_ref.entity("sales.customer")
ORDER = private_ref.entity("sales.order")
LINE = private_ref.entity("sales.order_line")
BUYER = private_ref.relationship("sales.order_buyer")
LINE_ORDER = private_ref.relationship("sales.line_order")
REVENUE = private_ref.metric("sales.revenue")
LINE_REVENUE = private_ref.metric("sales.line_revenue")
REGION = private_ref.dimension("sales.customer.region")
CATEGORY = private_ref.dimension("sales.product.category")
LINE_PRODUCT = private_ref.relationship("sales.line_product")
RECIPIENT = private_ref.relationship("sales.order_recipient")


def _window(session: mv.Session) -> mv.TimeScope:
    return session.time_scope(start="2026-08-01", end="2026-09-01")


def _extend(case: DslCase, *, roles: bool = False, order_product: bool = False) -> mv.Session:
    connection = duckdb.connect(str(case.database_path))
    try:
        connection.execute("CREATE TABLE product (id VARCHAR, category VARCHAR)")
        connection.execute("INSERT INTO product VALUES ('P', 'book'), ('Q', 'game')")
        connection.execute("ALTER TABLE order_line ADD COLUMN product_id VARCHAR")
        connection.execute(
            "INSERT INTO order_line VALUES ('a1','j1_a',100,'P'), ('a2','j1_a',200,'P'), ('a3','j1_a',150,'Q'), ('b1','j1_b',150,'Q'), ('c1','j1_c',400,'P'), ('outside','j1_july',77,'P')"
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
    model.write_text(
        model.read_text().replace("# Named expressions", extra + "\n# Named expressions", 1)
    )
    ms.load(workspace_dir=case.root)
    return mv.session.get_or_create("relationship-bindings", timezone="UTC")


def test_inference_and_root_identity(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = case.session
    customers, orders = (
        session.members(catalog_value(session.catalog, CUSTOMER.path, kind="entity")),
        session.members(catalog_value(session.catalog, ORDER.path, kind="entity")),
    )
    automatic = session.observe(
        {"value": catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")},
        by=mv.product(_window(session), customers),
    )
    binding = source_graph(automatic).signature.observation
    assert binding is not None
    assert tuple(hop.ref.path for hop in binding.contributions[0].coordinates[0].path) == (
        LINE_ORDER.path,
        BUYER.path,
    )
    region = catalog_value(session.catalog, REGION.path, kind="field")
    buyer = catalog_value(session.catalog, BUYER.path, kind="relationship")
    inferred = session.read(orders, {"region": region})
    explicit = session.read(orders, {"region": region.via((buyer,))})
    assert source_graph(inferred).fingerprint == source_graph(explicit).fingerprint
    assert (
        source_graph(session.read(customers, {"region": region})).signature.owner
        == source_graph(automatic).signature.owner
    )
    ratio = session.catalog.get("metric:sales.aov_from_lines")
    observed = session.observe({"value": ratio}, by=mv.product(_window(session), customers))
    binding = source_graph(observed).signature.observation
    assert binding is not None
    assert len(binding.contributions) == 2
    assert {
        tuple(hop.ref.path for hop in item.coordinates[0].path) for item in binding.contributions
    } == {(LINE_ORDER.path, BUYER.path), (BUYER.path,)}
    line_order = catalog_value(session.catalog, LINE_ORDER.path, kind="relationship")
    with pytest.raises((AnalysisError, ms.errors.SemanticExpressionError)):
        session.read(orders, {"region": region.via((line_order, buyer))})
    identity = session.observe({"value": ratio}, by=mv.product(_window(session), orders))
    binding = source_graph(identity).signature.observation
    assert binding is not None
    assert {
        tuple(hop.ref.path for hop in item.coordinates[0].path) for item in binding.contributions
    } == {(LINE_ORDER.path,), ()}


def test_frozen_coordinate_roles_survive_later_model_changes(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import FrozenInstanceError

    case = analysis_dsl_case_factory("j1")
    session = case.session
    customers = session.members(catalog_value(session.catalog, CUSTOMER.path, kind="entity"))
    original = session.observe(
        {"value": catalog_value(session.catalog, REVENUE.path, kind="metric")},
        by=mv.product(_window(session), customers),
    )
    observation = source_graph(original).signature.observation
    assert observation is not None
    coordinate = observation.contributions[0].coordinates[0]
    with pytest.raises(FrozenInstanceError):
        coordinate.__setattr__("path", ())
    model = case.root / "models/semantic/sales/models.py"
    extra = "alternative = ms.relationship(name='alternative_buyer', from_entity=orders, to_entity=customer, keys=[ms.join_on(order_customer_id, customer_id)])\n"
    model.write_text(
        model.read_text().replace("# Named expressions", extra + "# Named expressions", 1)
    )
    other = mv.session.get_or_create("new-role", timezone="UTC")
    with pytest.raises(AnalysisError):
        other.observe(
            {"value": catalog_value(other.catalog, REVENUE.path, kind="metric")},
            by=mv.product(
                _window(other),
                other.members(catalog_value(other.catalog, CUSTOMER.path, kind="entity")),
            ),
        )
    assert tuple(hop.ref.path for hop in coordinate.path) == (BUYER.path,)
    frame = original.persist().to_pandas()
    assert frame["value"].tolist() == [450, 150, 400, 0]


def test_roles_and_independent_read_paths(analysis_dsl_case_factory: DslCaseFactory) -> None:
    session = _extend(analysis_dsl_case_factory("j1"), roles=True)
    customers, orders = (
        session.members(catalog_value(session.catalog, CUSTOMER.path, kind="entity")),
        session.members(catalog_value(session.catalog, ORDER.path, kind="entity")),
    )
    revenue = catalog_value(session.catalog, REVENUE.path, kind="metric")
    customer_id = catalog_value(session.catalog, "sales.customer.customer_id", kind="field")
    region = catalog_value(session.catalog, REGION.path, kind="field")
    buyer = catalog_value(session.catalog, BUYER.path, kind="relationship")
    recipient = catalog_value(session.catalog, RECIPIENT.path, kind="relationship")
    with pytest.raises(AnalysisError) as ambiguous:
        session.observe({"value": revenue}, by=mv.product(_window(session), customers))
    assert ambiguous.value.repair
    expected_roles = (
        {"A": 450, "B": 150, "C": 400, "D": 0},
        {"A": 150, "B": 400, "C": 450, "D": 0},
    )
    for route, expected in zip((buyer, recipient), expected_roles, strict=True):
        axis = customer_id.via((route,))
        complete = mv.coordinates(by=(axis,), keys=(("A",), ("B",), ("C",), ("D",)))
        frame = (
            session.observe({"value": revenue}, by=mv.product(_window(session), complete))
            .persist()
            .to_pandas()
        )
        assert frame.set_index(ordinary_columns(frame)[0])["value"].to_dict() == expected
    with pytest.raises(AnalysisError):
        session.read(orders, {"region": region})
    buyer_region, recipient_region = region.via((buyer,)), region.via((recipient,))
    current_frame = session.observe(
        {"value": revenue}, by=(_window(session), buyer_region, recipient_region)
    ).persist()
    baseline_frame = session.observe(
        {"value": revenue},
        by=(
            session.time_scope(start="2026-07-01", end="2026-08-01"),
            buyer_region,
            recipient_region,
        ),
    ).persist()
    current = current_frame.to_pandas(
        axis_names={
            current_frame.axes[1].token: "buyer_region",
            current_frame.axes[2].token: "recipient_region",
        }
    )
    baseline = baseline_frame.to_pandas(
        axis_names={
            baseline_frame.axes[1].token: "buyer_region",
            baseline_frame.axes[2].token: "recipient_region",
        }
    )
    current_values = current.set_index(["buyer_region", "recipient_region"])["value"]
    baseline_values = baseline.set_index(["buyer_region", "recipient_region"])["value"]
    assert current_values.to_dict() == {
        ("east", "south"): 450,
        ("east", "east"): 150,
        ("south", "east"): 400,
    }
    assert current_values.astype(object).subtract(
        baseline_values.astype(object), fill_value=0
    ).to_dict() == {
        ("east", "south"): 450,
        ("east", "east"): 73,
        ("south", "east"): 400,
    }


def test_classification_scope_and_complete_components(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    session = _extend(analysis_dsl_case_factory("j1"))
    revenue = catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")
    category = catalog_value(session.catalog, CATEGORY.path, kind="field")
    with pytest.raises((AnalysisError, ms.errors.SemanticExpressionError)):
        session.observe({"value": revenue}, by=(_window(session), category, category))
    lines = session.members(catalog_value(session.catalog, LINE.path, kind="entity"))
    read = session.read(lines, {"category": category})
    with pytest.raises((AnalysisError, ms.errors.SemanticExpressionError)):
        # Intentional runtime negative: a Query cannot be used as an observation axis.
        session.observe({"value": revenue}, by=(_window(session), read))  # type: ignore[arg-type]  # Runtime probe: a Frame is not a grouping.
    other = mv.session.get_or_create("another-session", timezone="UTC")
    with pytest.raises(AnalysisError):
        session.frame(other.members(catalog_value(other.catalog, LINE.path, kind="entity")))
    ratio = catalog_value(session.catalog, LINE_REVENUE.path, kind="metric") / catalog_value(
        session.catalog, REVENUE.path, kind="metric"
    )
    with pytest.raises((AnalysisError, ms.errors.SemanticExpressionError)):
        session.observe({"value": ratio}, by=(_window(session), category))
    with pytest.raises((AnalysisError, ms.errors.SemanticExpressionError)):
        session.observe(
            {"value": revenue}, by=(_window(session), category.at(session.instant("2026-08-01")))
        )


@pytest.mark.parametrize("explicit", (False, True))
def test_classification_read_preserves_missing_owner_as_null(
    analysis_dsl_case_factory: DslCaseFactory,
    explicit: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE order_line SET product_id='missing' WHERE line_id='outside'")
    category = catalog_value(session.catalog, CATEGORY.path, kind="field")
    route = catalog_value(session.catalog, LINE_PRODUCT.path, kind="relationship")
    if explicit:
        category = category.via((route,))
    attributes = (
        session.read(
            session.members(catalog_value(session.catalog, LINE.path, kind="entity")),
            {"category": category},
        )
        .persist()
        .to_pandas()
    )
    assert attributes.iloc[:, 0].tolist() == ["a1", "a2", "a3", "b1", "c1", "outside"]
    assert attributes["category"].iloc[:5].tolist() == [
        "book",
        "book",
        "game",
        "game",
        "book",
    ]
    assert attributes["category"].isna().tolist() == [False] * 5 + [True]
    assert len(terminal_runs(session)) == 1
    # The separate observation consumes only August contributions, so a bad July
    # relationship is outside this read scope.
    frame = (
        session.observe(
            {"value": catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")},
            by=(_window(session), category),
        )
        .persist()
        .to_pandas()
    )
    assert frame.set_index(ordinary_columns(frame)[0])["value"].to_dict() == {
        "book": 700,
        "game": 300,
    }
    assert session._runtime.store.resources(session.id) == ()


@pytest.mark.release
@pytest.mark.slow
def test_branch_and_consumed_coverage(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case)
    category = catalog_value(session.catalog, CATEGORY.path, kind="field")
    route = catalog_value(session.catalog, LINE_PRODUCT.path, kind="relationship")
    metric = catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")
    automatic = session.observe({"value": metric}, by=(_window(session), category))
    explicit = session.observe({"value": metric}, by=(_window(session), category.via((route,))))
    automatic_axis = next(
        axis
        for axis in source_graph(automatic).signature.axes
        if isinstance(axis, AxisBinding) and axis.role == "field"
    )
    explicit_axis = next(
        axis
        for axis in source_graph(explicit).signature.axes
        if isinstance(axis, AxisBinding) and axis.role == "field"
    )
    assert isinstance(automatic_axis, AxisBinding) and isinstance(explicit_axis, AxisBinding)
    assert automatic_axis.entity == explicit_axis.entity
    assert automatic_axis.field == explicit_axis.field
    assert automatic_axis.role == explicit_axis.role == "field"
    automatic_binding = source_graph(automatic).signature.observation
    explicit_binding = source_graph(explicit).signature.observation
    assert automatic_binding is not None and explicit_binding is not None
    assert automatic_binding.contributions == explicit_binding.contributions
    fixed = automatic.persist()
    frame = fixed.to_pandas()
    assert frame.set_index(ordinary_columns(frame)[0])["value"].to_dict() == {
        "book": 700,
        "game": 300,
    }
    assert explicit.persist().to_pandas().equals(frame)
    total = automatic.reduce({"value": automatic["value"].sum()}).persist()
    assert total.to_pandas()["value"].tolist() == [1000]
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    script = """
import sys
import marivo.analysis as mv
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.analysis.graph.execution_fixtures import source_graph
import marivo.semantic as ms
from marivo.datasource.runtime import DatasourceConnectionService
def forbidden(*args, **kwargs):
    raise AssertionError('Artifact branch accessed current Semantic or source')
ms.load = forbidden
DatasourceConnectionService.use_backend = forbidden
session = mv.session.resume(sys.argv[1])
frame = session.persisted_frame(sys.argv[2]).to_pandas()
assert frame.set_index(ordinary_columns(frame)[0])['value'].to_dict() == {'book':700,'game':300}
assert frame['value'].sum() == 1000
assert session.persisted_frame(sys.argv[3]).to_pandas()['value'].tolist() == [1000]
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, session.id, fixed.artifact_id, total.artifact_id],
        cwd=case.root,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_multiroot_branch_arithmetic(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j1")
    session = _extend(case, order_product=True)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("INSERT INTO product VALUES ('R', 'spare')")
        connection.execute("UPDATE order_line SET product_id='R' WHERE line_id='outside'")
    category = catalog_value(session.catalog, CATEGORY.path, kind="field")
    line_revenue = catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")
    revenue = catalog_value(session.catalog, REVENUE.path, kind="metric")
    buckets = mv.membership(
        category,
        via=(
            (catalog_value(session.catalog, LINE_PRODUCT.path, kind="relationship"),),
            (session.catalog.get("relationship:sales.order_product"),),
        ),
        unmatched="exclude",
    )
    ratio, linear = line_revenue / revenue, line_revenue - revenue
    grouped = session.observe(
        {"ratio": ratio, "linear": linear}, by=(_window(session), buckets)
    ).persist()
    exported = grouped.to_pandas()
    frame = exported.set_index(ordinary_columns(exported)[0])
    assert set(frame.index) == {"book", "game"}
    assert frame["ratio"].to_dict() == pytest.approx({"book": 700 / 850, "game": 2.0})
    assert frame["linear"].to_dict() == pytest.approx({"book": -150, "game": 150})

    overall = (
        session.observe({"ratio": ratio, "linear": linear}, by=(_window(session),))
        .persist()
        .to_pandas()
    )
    assert overall["ratio"].tolist() == pytest.approx([1.0])
    assert overall["linear"].tolist() == [0]


def test_timed_classification_cannot_remove_consumed_contributions(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:

    case = analysis_dsl_case_factory("j1")
    session = _extend(case)
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE order_line SET product_id='P' WHERE line_id='outside'")
        connection.execute("ALTER TABLE product ADD COLUMN beginning DATE")
        connection.execute("ALTER TABLE product ADD COLUMN ending DATE")
        connection.execute(
            "UPDATE product SET category='book', beginning=DATE '2026-08-01', ending=DATE '2026-09-01'"
        )
        connection.execute(
            "INSERT INTO product VALUES ('P','game','2026-09-01',NULL), ('Q','game','2026-09-01',NULL)"
        )
        connection.execute("INSERT INTO order_line VALUES ('september','j1_september',99,'P')")
    model = case.root / "models/semantic/sales/models.py"
    contents = model.read_text().replace(
        "source=md.table('product'), primary_key=['id'])",
        "source=md.table('product'), primary_key=['id'], versioning=ms.validity("
        "valid_from=ms.ref.time_dimension('sales.product.beginning'), "
        "valid_to=ms.ref.time_dimension('sales.product.ending'), "
        "interval='closed_open', open_end=(None,), timezone='UTC'))",
    )
    fields = "product_beginning = ms.time_dimension_column(name='beginning', entity=product, column='beginning')\nproduct_ending = ms.time_dimension_column(name='ending', entity=product, column='ending')\n"
    model.write_text(contents.replace("# Named expressions", fields + "# Named expressions", 1))
    session = mv.session.get_or_create("timed-coverage", timezone="UTC")
    metric = catalog_value(session.catalog, LINE_REVENUE.path, kind="metric")
    field = catalog_value(session.catalog, CATEGORY.path, kind="field")
    observed = []
    for start, end, instant, key, expected in (
        ("2026-08-01", "2026-09-01", "2026-08-31T23:59:59.999999+00:00", "book", 1000),
        ("2026-09-01", "2026-10-01", "2026-09-30T23:59:59.999999+00:00", "game", 99),
    ):
        category = field.at(session.instant(instant))
        scope = session.time_scope(start=start, end=end)
        complete = session.observe({"value": metric}, by=(scope, category))
        frame = complete.persist().to_pandas()
        assert frame[ordinary_columns(frame)[0]].tolist() == [key]
        assert frame["value"].tolist() == [expected]
        observed.append(expected)
        book_domain = mv.coordinates(by=(category,), keys=(("book",),))
        selected = session.observe({"value": metric}, by=mv.product(scope, book_domain))
        if key == "book":
            assert selected.persist().to_pandas()["value"].tolist() == [1000]
        else:
            with pytest.raises(MaterializationError, match="missing output coordinates"):
                selected.persist()
    assert sum(observed) == 1099
