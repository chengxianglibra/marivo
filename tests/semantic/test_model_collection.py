"""Model collection and Catalog publication of pure Measure/Metric declarations (R1.4)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, TypeAlias

import pytest

import marivo.semantic as ms
from marivo._definition.expressions import ContributionFilter, DeclaredMetricRef, MetricDivide
from marivo._definition.graph import computation_fingerprint
from marivo._definition.types import DefinitionGraphError, ExactRef
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions import (
    DeclaredMeasure,
    Metric,
    catalog_metric_definition,
    resolve,
)
from marivo.semantic.reader import SemanticProject

ProjectFactory: TypeAlias = Callable[..., SemanticProject]

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_PHYSICAL_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders_ref = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
status_ref = ms.dimension_column(name="status", entity=orders_ref, column="status")
paid_at_ref = ms.time_dimension_column(
    name="paid_at",
    entity=orders_ref,
    column="paid_at",
    granularity="day",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)
amount_ref = ms.measure_column(
    name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="CNY"
)
cost_ref = ms.measure_column(
    name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all(), unit="CNY"
)

finalized = ms.finalize()
orders = finalized.resolve(orders_ref)
status = finalized.resolve(status_ref)
paid_at = finalized.resolve(paid_at_ref)
amount = finalized.resolve(amount_ref)
cost = finalized.resolve(cost_ref)
"""

_DECLARATIONS_PY = (
    _PHYSICAL_PY
    + """

@ms.measure()
def margin():
    return amount - cost


@ms.metric()
def revenue():
    return amount.sum(time=paid_at).where(status == "paid")


@ms.metric()
def order_count():
    return orders.count(time=paid_at)


revenue_alias = revenue
_base = amount.sum(time=paid_at)


def _hidden():
    @ms.metric()
    def hidden_base():
        return amount.sum(time=paid_at)

    return hidden_base


_private_declaration = [_hidden()]


@ms.metric()
def average_order_value():
    return revenue / order_count


@ms.metric()
def doubled():
    return _base * 2
"""
)


def _project(
    factory: ProjectFactory, objects: str, *, extra: dict[str, str] | None = None
) -> SemanticProject:
    files = {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": objects, **(extra or {})}
    return factory(files, load=False)


def _catalog(factory: ProjectFactory, objects: str = _DECLARATIONS_PY) -> SemanticCatalog:
    project = _project(factory, objects)
    result = project.load()
    assert result.status == "ready", result.errors
    return SemanticCatalog(project)


def test_module_level_declarations_publish_once_under_their_function_name(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    registry = catalog._state.registry

    assert sorted(registry.declared_metrics) == [
        "sales.average_order_value",
        "sales.doubled",
        "sales.order_count",
        "sales.revenue",
    ]
    assert sorted(registry.declared_measures) == ["sales.orders.margin"]
    # Aliases reference the same declaration and never add a second name.
    assert "sales.revenue_alias" not in registry.declared_metrics
    # A declaration not bound to a module global stays a private dependency.
    assert "sales.hidden_base" not in registry.declared_metrics
    assert ms.ref.metric("sales.revenue") in catalog._state.definitions


def test_catalog_metric_resolves_through_declared_metric_ref(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = resolve(catalog, ms.ref.metric("sales.revenue"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))

    assert type(revenue) is Metric
    assert isinstance(revenue.node, DeclaredMetricRef)
    assert revenue.node.ref.ref == ms.ref.metric("sales.revenue")
    assert revenue.node.ref.version.startswith("sha256:")
    assert revenue.roots == (orders.ref,)
    assert revenue.unit == "CNY"

    stored = catalog._state.registry.declared_metrics["sales.revenue"]
    assert catalog_metric_definition(catalog, revenue.node.ref) is stored.node
    # Composition over Catalog values keeps the declared dependency by reference.
    aov = revenue / resolve(catalog, ms.ref.metric("sales.order_count"))
    assert isinstance(aov.node, MetricDivide)
    assert isinstance(aov.node.numerator, DeclaredMetricRef)


def test_catalog_closure_expands_to_the_stored_declaration_body(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo.semantic.expressions import catalog_closure

    catalog = _catalog(semantic_project_factory)
    revenue = resolve(catalog, ms.ref.metric("sales.revenue"))
    stored = catalog._state.registry.declared_metrics["sales.revenue"]

    closure = catalog_closure(catalog, revenue)
    assert stored.node in closure.order
    assert computation_fingerprint(closure.order[-1]) == computation_fingerprint(revenue.node)


def test_published_versions_follow_consumed_definitions(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    first = _catalog(semantic_project_factory)
    changed = _DECLARATIONS_PY.replace('status == "paid"', 'status == "settled"')
    root = tmp_path / "changed"
    root.mkdir()
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": changed},
        load=False,
        workspace_dir=root,
    )
    assert project.load().status == "ready"
    second = SemanticCatalog(project)

    revenue_ref = ms.ref.metric("sales.revenue")
    count_ref = ms.ref.metric("sales.order_count")
    first_revenue = resolve(first, revenue_ref)
    second_revenue = resolve(second, revenue_ref)
    assert isinstance(first_revenue.node, DeclaredMetricRef)
    assert isinstance(second_revenue.node, DeclaredMetricRef)
    assert first_revenue.node.ref.version != second_revenue.node.ref.version
    first_count = resolve(first, count_ref).node
    second_count = resolve(second, count_ref).node
    assert isinstance(first_count, DeclaredMetricRef)
    assert isinstance(second_count, DeclaredMetricRef)
    assert first_count.ref.version == second_count.ref.version


def test_published_measure_resolves_to_its_unchanged_declaration(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    margin = resolve(catalog, ms.ref.measure("sales.orders.margin"))

    assert type(margin) is DeclaredMeasure
    assert margin is catalog._state.registry.declared_measures["sales.orders.margin"]
    assert margin.unit == "CNY"


def test_distinct_declarations_cannot_share_a_full_name(
    semantic_project_factory: ProjectFactory,
) -> None:
    other = """\
import marivo.semantic as ms
from .objects import amount, paid_at


@ms.metric()
def revenue():
    return amount.sum(time=paid_at)
"""
    project = _project(semantic_project_factory, _DECLARATIONS_PY, extra={"sales/other.py": other})
    result = project.load()

    assert result.status == "errored"
    duplicate = [error for error in result.errors if error.kind == ErrorKind.DUPLICATE_NAME]
    assert len(duplicate) == 1
    assert "sales.revenue" in duplicate[0].semantic_refs
    assert any("other.py" in str(ref) for ref in duplicate[0].semantic_refs)
    assert any("objects.py" in str(ref) for ref in duplicate[0].semantic_refs)


def test_pure_measure_cannot_take_a_physical_field_name(
    semantic_project_factory: ProjectFactory,
) -> None:
    other = """\
import marivo.semantic as ms
from .objects import amount as base_amount, cost


@ms.measure()
def amount():
    return base_amount - cost
"""
    project = _project(semantic_project_factory, _PHYSICAL_PY, extra={"sales/other.py": other})
    result = project.load()

    assert result.status == "errored"
    duplicate = [error for error in result.errors if error.kind == ErrorKind.DUPLICATE_NAME]
    assert [error.semantic_refs[0] for error in duplicate] == ["sales.orders.amount"]


def test_explicit_unknown_domain_lists_real_choices(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL_PY
        + """

@ms.metric(domain=ms.ref.domain("finance"))
def revenue():
    return amount.sum(time=paid_at)
"""
    )
    result = _project(semantic_project_factory, source).load()

    assert result.status == "errored"
    missing = [error for error in result.errors if error.kind == ErrorKind.MISSING_DOMAIN]
    assert len(missing) == 1
    assert "finance" in (missing[0].received or "")
    assert "sales" in (missing[0].expected or "")


def test_session_bound_dependency_is_not_admitted(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL_PY
        + """
from marivo._definition.expressions import PerReduce
from marivo._definition.relations import PopulationProgram, SourceIdentities
from marivo._definition.types import Owner
from marivo.semantic.expressions import Metric

_base = amount.sum(time=paid_at)
_members = PopulationProgram(SourceIdentities(orders.ref), Owner("session-a", "owner-a"))
_session_bound = Metric(PerReduce(_base.node, _members, "mean"), _base.roots, _base.unit)


@ms.metric()
def member_mean():
    return _session_bound
"""
    )
    result = _project(semantic_project_factory, source).load()

    assert result.status == "errored"
    rejected = [
        error for error in result.errors if error.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    ]
    assert len(rejected) == 1
    assert "sales.member_mean" in rejected[0].semantic_refs
    assert "session-a" in (rejected[0].received or "")


def test_collection_reads_no_business_source(
    semantic_project_factory: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("model collection read a business source")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    catalog = _catalog(semantic_project_factory)
    assert "sales.revenue" in catalog._state.registry.declared_metrics


@pytest.mark.parametrize(
    ("definition_kind", "error_kind"),
    [("cycle", ErrorKind.DEFINITION_CYCLE), ("unresolved", ErrorKind.MISSING_METRIC_REF)],
)
def test_graph_failures_keep_their_definition_kind(
    definition_kind: str, error_kind: ErrorKind
) -> None:
    error = DefinitionGraphError(
        "boom",
        kind=definition_kind,  # type: ignore[arg-type]  # parametrized literal
        path=("DeclaredMetricRef.metric[0]",),
    )
    converted = SemanticExpressionError.from_definition(error, operation="metric closure")
    assert converted.kind == error_kind
    assert converted.details["definition_kind"] == definition_kind


def test_catalog_closure_reports_an_unpublished_metric(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo.semantic.expressions import catalog_closure

    catalog = _catalog(semantic_project_factory)
    missing = Metric(
        DeclaredMetricRef(ExactRef(ms.ref.metric("sales.missing"), "sha256:0")),
        resolve(catalog, ms.ref.metric("sales.revenue")).roots,
        "CNY",
    )
    with pytest.raises(SemanticExpressionError) as raised:
        catalog_closure(catalog, missing)
    assert raised.value.kind == ErrorKind.MISSING_METRIC_REF


def test_collection_rejects_definitions_missing_from_the_model_scope(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL_PY
        + """
from marivo._definition.expressions import RowField, SourceAggregate
from marivo._definition.types import ExactRef, SumAll
from marivo.semantic.expressions import Metric

_ghost_root = ExactRef(ms.ref.entity("sales.ghost"), "sha256:0")
_ghost_amount = RowField(
    ExactRef(ms.ref.measure("sales.ghost.amount"), "sha256:0"), _ghost_root, "CNY", SumAll()
)
_foreign = Metric(SourceAggregate(_ghost_amount, "sum", None), (_ghost_root,), "CNY")


@ms.metric()
def foreign_revenue():
    return _foreign
"""
    )
    result = _project(semantic_project_factory, source).load()

    assert result.status == "errored"
    rejected = [error for error in result.errors if "sales.foreign_revenue" in error.semantic_refs]
    assert len(rejected) == 1
    assert rejected[0].kind == ErrorKind.MISSING_ENTITY_REF
    assert "entity:sales.ghost" in (rejected[0].received or "")
    assert "measure:sales.ghost.amount" in (rejected[0].received or "")


def test_collection_rejects_a_changed_field_kind(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL_PY
        + """
from marivo._definition.expressions import RowField, SourceAggregate
from marivo._definition.types import ExactRef, SumNone
from marivo.semantic.expressions import Metric

_as_dimension = RowField(
    ExactRef(ms.ref.dimension("sales.orders.paid_at"), "sha256:0"), orders.ref, None, SumNone()
)
_miskinded = Metric(SourceAggregate(_as_dimension, "count", None), (orders.ref,), "1")


@ms.metric()
def paid_count():
    return _miskinded
"""
    )
    result = _project(semantic_project_factory, source).load()

    assert result.status == "errored"
    rejected = [error for error in result.errors if "sales.paid_count" in error.semantic_refs]
    assert len(rejected) == 1
    assert rejected[0].kind == ErrorKind.INVALID_EXPRESSION_VALUE
    assert "dimension:sales.orders.paid_at" in (rejected[0].received or "")


def test_published_single_source_metric_keeps_where(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    status = catalog.fields.get("sales.orders.status")
    order_count = catalog.metrics.get("sales.order_count")

    paid = order_count.where(status == "paid")
    assert isinstance(paid.node, ContributionFilter)
    assert isinstance(paid.node.operand, DeclaredMetricRef)
    settled = paid.where(status != "void")
    assert isinstance(settled.node, ContributionFilter)
    assert isinstance(
        catalog.metrics.get("sales.revenue").where(status == "paid").node, ContributionFilter
    )

    with pytest.raises(SemanticExpressionError) as raised:
        catalog.metrics.get("sales.average_order_value").where(status == "paid")
    assert raised.value.kind == ErrorKind.INVALID_WHERE_SCOPE


def _changed_catalog(
    factory: ProjectFactory, tmp_path: Path, old: str, new: str
) -> SemanticCatalog:
    root = tmp_path / "changed"
    root.mkdir()
    project = factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _DECLARATIONS_PY.replace(old, new)},
        load=False,
        workspace_dir=root,
    )
    assert project.load().status == "ready"
    return SemanticCatalog(project)


def test_closure_resolves_an_updated_metric_by_current_path(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    from marivo.semantic.expressions import catalog_closure

    first = _catalog(semantic_project_factory)
    old_revenue = resolve(first, ms.ref.metric("sales.revenue"))
    assert isinstance(old_revenue.node, DeclaredMetricRef)
    old_version = old_revenue.node.ref.version
    second = _changed_catalog(
        semantic_project_factory, tmp_path, 'status == "paid"', 'status == "settled"'
    )
    current = resolve(second, ms.ref.metric("sales.revenue"))
    assert isinstance(current.node, DeclaredMetricRef)
    assert current.node.ref.version != old_version

    closure = catalog_closure(second, old_revenue)

    assert computation_fingerprint(closure.order[-1]) == computation_fingerprint(current.node)
    assert current.node.ref in closure.refs
    assert old_revenue.node.ref not in closure.refs
    assert second._state.registry.declared_metrics["sales.revenue"].node in closure.order
    # The original value is immutable and still names the definition it was built from.
    assert old_revenue.node.ref.version == old_version


def test_closure_rechecks_where_scope_against_the_current_definition(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    from marivo.semantic.expressions import catalog_closure

    first = _catalog(semantic_project_factory)
    status = first.fields.get("sales.orders.status")
    paid_orders = first.metrics.get("sales.order_count").where(status == "paid")
    second = _changed_catalog(
        semantic_project_factory,
        tmp_path,
        "return orders.count(time=paid_at)",
        "return orders.count(time=paid_at) + orders.count(time=paid_at)",
    )

    with pytest.raises(SemanticExpressionError) as raised:
        catalog_closure(second, paid_orders)
    assert raised.value.kind == ErrorKind.INVALID_WHERE_SCOPE
    assert "metric:sales.order_count" in raised.value.semantic_refs
