"""Symbolic helper binding preserves complete Population definitions."""

from collections.abc import Callable
from types import ModuleType

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.deferred import inspect_capture
from marivo._definition.expressions import MemberCount
from marivo._definition.graph import computation_fingerprint
from marivo._definition.relations import PopulationParameter, population_keys
from marivo._definition.types import DefinitionGraphError
from marivo.analysis.core.model import CoreRuleError
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value

_MODEL = """import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["tenant", "id"])
customer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
channel = ms.dimension_column(name="channel", entity=orders, column="channel")
customers = ms.entity(name="customers", datasource=ms.ref.datasource("warehouse"), source=md.table("customers"), primary_key=["id"])
cid = ms.dimension_column(name="id", entity=customers, column="id")
segment = ms.dimension_column(name="segment", entity=customers, column="segment")
customer = ms.relationship(name="customer", from_entity=orders, to_entity=customers, keys=[ms.join_on(customer_id, cid)])
"""


def _catalog(factory: Callable[..., SemanticProject], helper: str) -> SemanticCatalog:
    project = factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _MODEL + helper,
        }
    )
    assert not project.errors(), "\n".join(str(error) for error in project.errors())
    return SemanticCatalog(project)


def _population(catalog: SemanticCatalog) -> PopulationParameter:
    metric = consume_metric(catalog.get("metric:sales.selected_count"))
    body = next(
        body for reference, body in metric._declared if reference.ref.path == "sales.selected_count"
    )
    assert isinstance(body, MemberCount)
    return PopulationParameter(body.population)


def test_symbolic_set_projection_and_membership_keep_complete_null_tuples(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    catalog = _catalog(
        semantic_project_factory,
        """
requested = mv.coordinates(by=(region, channel), keys=(("east", "web"), ("east", "store"), (None, "web")))
extra = mv.coordinates(by=(region, channel), keys=(("west", "web"),))
allowed = mv.coordinates(by=(channel,), keys=(("web",),))
selected = requested.union(extra).intersection(requested).difference(extra).where(mv.in_population(allowed)).project(by=(requested.axes[0],))
captured_count = selected.count()
@ms.metric()
def selected_count():
    return captured_count
""",
    )
    assert population_keys(_population(catalog)._definition) == ((None,), ("east",))


def test_symbolic_product_binds_same_definition_and_copies_finite_keys(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    catalog = _catalog(
        semantic_project_factory,
        """
keys = [["east"], ["west"]]
regions = mv.coordinates(by=(region,), keys=keys)
keys[0][0] = "changed"
channels = mv.coordinates(by=(channel,), keys=(("web",), ("store",)))
selected = mv.product(regions, channels)
captured_count = selected.count()
@ms.metric()
def selected_count():
    return captured_count
""",
    )
    region = catalog_value(catalog, "sales.orders.region", kind="field")
    channel = catalog_value(catalog, "sales.orders.channel", kind="field")
    expected = mv.product(
        mv.coordinates(by=(region,), keys=(("east",), ("west",))),
        mv.coordinates(by=(channel,), keys=(("web",), ("store",))),
    )
    assert isinstance(expected, mv.Population)
    actual = _population(catalog)
    assert population_keys(actual._definition) == (
        ("east", "store"),
        ("east", "web"),
        ("west", "store"),
        ("west", "web"),
    )
    assert computation_fingerprint(actual._definition) == computation_fingerprint(
        expected._definition
    )


def test_symbolic_identity_classify_and_relationship_programs_match_closed_helpers(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    catalog = _catalog(
        semantic_project_factory,
        """
orders_domain = mv.coordinates(by=(mv.identity(orders),), keys=((("t1", 1),), (("t2", 1),)))
customers_domain = orders_domain.image(via=(customer,))
selected = orders_domain.preimage(customers_domain, via=(customer,)).classify(by=(orders_domain.axes[0], region))
captured_count = selected.count()
@ms.metric()
def selected_count():
    return captured_count
""",
    )
    orders = catalog.get("entity:sales.orders")
    customer = catalog.get("relationship:sales.customer")
    region = catalog_value(catalog, "sales.orders.region", kind="field")
    domain = mv.coordinates(by=(mv.identity(orders),), keys=((("t1", 1),), (("t2", 1),)))
    assert isinstance(domain, mv.Population)
    expected = domain.preimage(domain.image(via=(customer,)), via=(customer,)).classify(
        by=(domain.axes[0], region)
    )
    assert isinstance(expected, mv.Population)
    assert computation_fingerprint(_population(catalog)._definition) == computation_fingerprint(
        expected._definition
    )


def test_symbolic_population_count_keeps_decorator_call_boundary(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _MODEL
            + """
selected = mv.coordinates(by=(region,), keys=(("east",),))
@ms.metric()
def selected_count():
    return selected.count()
""",
        }
    )
    assert project.errors()
    assert (
        "module" in str(project.errors()[0]).lower() or "helper" in str(project.errors()[0]).lower()
    )


@pytest.mark.parametrize("operation", ("project", "classify", "image", "preimage"))
def test_symbolic_population_capture_requires_owned_parameters(operation: str) -> None:
    population = mv.coordinates(by=(ms.ref.dimension("sales.orders.region"),), keys=(("east",),))
    recipe = (
        getattr(population, operation)(population)
        if operation == "preimage"
        else getattr(population, operation)()
    )
    with pytest.raises(DefinitionGraphError, match="requires explicit"):
        inspect_capture(recipe)


def test_symbolic_helper_capture_checks_known_semantic_roles() -> None:
    with pytest.raises(CoreRuleError, match="Dimension, TimeDimension or Measure"):
        mv.coordinates(by=(ms.ref.entity("sales.orders"),), keys=((1,),))
    with pytest.raises(CoreRuleError, match="Dimension, TimeDimension or Measure"):
        mv.membership(ms.ref.metric("sales.revenue"), via=(), unmatched="exclude")
    grouping = mv.identity(ms.ref.entity("sales.orders"))
    with pytest.raises(DefinitionGraphError, match="Grouping parameters"):
        inspect_capture(grouping.count())


def test_symbolic_population_count_keeps_numeric_composition(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    catalog = _catalog(
        semantic_project_factory,
        """
selected = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
captured_count = selected.count().sqrt()
@ms.metric()
def selected_count():
    return captured_count.abs()
""",
    )
    region = catalog_value(catalog, "sales.orders.region", kind="field")
    population = mv.coordinates(by=(region,), keys=(("east",), ("west",)))
    assert isinstance(population, mv.Population)
    expected = consume_metric(population.count().sqrt().abs()).node
    actual = consume_metric(catalog.get("metric:sales.selected_count"))
    body = next(
        body for reference, body in actual._declared if reference.ref.path == "sales.selected_count"
    )
    assert body == expected


def test_symbolic_helper_inspects_named_measure_dependencies(
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    catalog = _catalog(
        semantic_project_factory,
        """
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
@ms.measure()
def amount_alias():
    return amount
selected = mv.coordinates(by=(amount_alias,), keys=((1,), (2,)))
captured_count = selected.count()
@ms.metric()
def selected_count():
    return captured_count
""",
    )
    selected = _population(catalog)
    assert population_keys(selected._definition) == ((1,), (2,))
    assert selected._definition.axes[0].field.ref.path == "sales.orders.amount"


@pytest.mark.parametrize("operation", ("image", "preimage"))
@pytest.mark.parametrize("invalid_step", ("42", "region + 1"))
def test_population_path_is_rejected_by_public_decorator_capture(
    semantic_project_factory: Callable[..., SemanticProject],
    operation: str,
    invalid_step: str,
) -> None:
    argument = "targets, " if operation == "preimage" else ""
    project = semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics")\n',
            "sales/objects.py": _MODEL
            + f"""
selected = mv.coordinates(by=(mv.identity(orders),), keys=((("tenant", 1),),))
targets = selected.image(via=(customer,))
counted = selected.{operation}({argument}via=({invalid_step},)).count()
@ms.metric()
def selected_count():
    return counted
raise AssertionError("decorator accepted an invalid relationship path")
""",
        }
    )
    errors = project.errors()
    assert len(errors) == 1
    assert isinstance(errors[0], SemanticExpressionError)
    assert "via" in str(errors[0])
    assert "native Relationship symbols or closed Relationship values" in str(errors[0])


def test_population_path_capture_keeps_closed_relationship_values(
    semantic_project_factory: Callable[..., SemanticProject], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    first = _catalog(semantic_project_factory, "")
    captured = ModuleType("_population_closed_path_test")
    captured.relationship = first.get("relationship:sales.customer")
    monkeypatch.setitem(sys.modules, captured.__name__, captured)
    catalog = _catalog(
        semantic_project_factory,
        """
from _population_closed_path_test import relationship
selected = mv.coordinates(by=(mv.identity(orders),), keys=((("tenant", 1),),))
targets = selected.image(via=(relationship,))
counted = selected.preimage(targets, via=(relationship,)).count()
@ms.metric()
def selected_count():
    return counted
""",
    )
    definition = _population(catalog)._definition
    assert definition.root.path[0].ref.ref.path == "sales.customer"
