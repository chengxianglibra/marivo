"""Model collection and Catalog publication of pure Measure/Metric declarations (R1.4)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, TypeAlias

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo._definition.deferred import DeclarationRecord, declare
from marivo._definition.expressions import ContributionFilter, DeclaredMetricRef, MetricExpression
from marivo._definition.graph import computation_fingerprint
from marivo._definition.types import DefinitionGraphError, ExactRef
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_metric, consume_row
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions import catalog_metric_definition, entity_exact
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import resolve_test_symbol as resolve

ProjectFactory: TypeAlias = Callable[..., SemanticProject]
_DOMAIN_PY = (
    'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n'
)
_PHYSICAL_PY = 'import marivo.datasource as md\nimport marivo.semantic as ms\n\norders_ref = ms.entity(\n    name="orders",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("orders"),\n    primary_key=["order_id"],\n)\nstatus_ref = ms.dimension_column(name="status", entity=orders_ref, column="status")\npaid_at_ref = ms.time_dimension_column(\n    name="paid_at",\n    entity=orders_ref,\n    column="paid_at",\n    \n    parse=ms.timestamp(timezone="UTC"),\n    is_default=True,\n)\namount_ref = ms.measure_column(\n    name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="CNY"\n)\ncost_ref = ms.measure_column(\n    name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all(), unit="CNY"\n)\n\norders = orders_ref\nstatus = status_ref\npaid_at = paid_at_ref\namount = amount_ref\ncost = cost_ref\n'
_DECLARATIONS_PY = (
    _PHYSICAL_PY
    + '\n\n@ms.measure()\ndef margin():\n    return amount - cost\n\n\n@ms.metric()\ndef revenue():\n    return amount.sum(time=paid_at).where(status == "paid")\n\n\n@ms.metric()\ndef order_count():\n    return orders.count(time=paid_at)\n\n\nrevenue_alias = revenue\n_base = amount.sum(time=paid_at)\n\n\ndef _hidden():\n    @ms.metric()\n    def hidden_base():\n        return amount.sum(time=paid_at)\n\n    return hidden_base\n\n\n_private_declaration = [_hidden()]\n\n\n@ms.metric()\ndef average_order_value():\n    return revenue / order_count\n\n\n@ms.metric()\ndef doubled():\n    return _base * 2\n'
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
    assert "sales.revenue_alias" not in registry.declared_metrics
    assert "sales.hidden_base" not in registry.declared_metrics
    assert ref_factory.metric("sales.revenue") in catalog._state.definitions


def test_catalog_metric_resolves_through_declared_metric_ref(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    revenue = resolve(catalog, ms.ref.metric("sales.revenue"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    assert type(revenue) is Deferred
    assert isinstance(consume_metric(revenue).node, DeclaredMetricRef)
    assert consume_metric(revenue).node.ref.ref == ref_factory.metric("sales.revenue")
    assert consume_metric(revenue).node.ref.version.startswith("sha256:")
    assert consume_metric(revenue).roots == (entity_exact(orders),)
    assert consume_metric(revenue).unit == "CNY"
    stored = catalog._state.registry.declared_metrics["sales.revenue"]
    assert (
        catalog_metric_definition(catalog, consume_metric(revenue).node.ref)
        == consume_metric(stored).node
    )
    aov = revenue / resolve(catalog, ms.ref.metric("sales.order_count"))
    assert isinstance(consume_metric(aov).node, MetricExpression)
    assert isinstance(consume_metric(aov).node.operands[0], DeclaredMetricRef)


def test_catalog_closure_expands_to_the_stored_declaration_body(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo.semantic.expressions import catalog_closure

    catalog = _catalog(semantic_project_factory)
    revenue = resolve(catalog, ms.ref.metric("sales.revenue"))
    stored = catalog._state.registry.declared_metrics["sales.revenue"]
    closure = catalog_closure(catalog, revenue)
    assert consume_metric(stored).node in closure.order
    assert computation_fingerprint(closure.order[-1]) == computation_fingerprint(
        consume_metric(revenue).node
    )


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
    assert isinstance(consume_metric(first_revenue).node, DeclaredMetricRef)
    assert isinstance(consume_metric(second_revenue).node, DeclaredMetricRef)
    assert (
        consume_metric(first_revenue).node.ref.version
        != consume_metric(second_revenue).node.ref.version
    )
    first_count = consume_metric(resolve(first, count_ref)).node
    second_count = consume_metric(resolve(second, count_ref)).node
    assert isinstance(first_count, DeclaredMetricRef)
    assert isinstance(second_count, DeclaredMetricRef)
    assert first_count.ref.version == second_count.ref.version


def test_published_measure_resolves_to_its_unchanged_declaration(
    semantic_project_factory: ProjectFactory,
) -> None:
    catalog = _catalog(semantic_project_factory)
    margin = resolve(catalog, ms.ref.measure("sales.orders.margin"))
    assert type(margin) is Deferred
    assert margin is catalog._state.registry.declared_measures["sales.orders.margin"]
    assert consume_row(margin).unit == "CNY"


def test_distinct_declarations_cannot_share_a_full_name(
    semantic_project_factory: ProjectFactory,
) -> None:
    other = "import marivo.semantic as ms\nfrom .objects import amount, paid_at\n\n\n@ms.metric()\ndef revenue():\n    return amount.sum(time=paid_at)\n"
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
    other = "import marivo.semantic as ms\nfrom .objects import amount as base_amount, cost\n\n\n@ms.measure()\ndef amount():\n    return base_amount - cost\n"
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
        + '\n\n@ms.metric(domain=ms.ref.domain("finance"))\ndef revenue():\n    return amount.sum(time=paid_at)\n'
    )
    result = _project(semantic_project_factory, source).load()
    assert result.status == "errored"
    missing = [error for error in result.errors if error.kind == ErrorKind.MISSING_DOMAIN]
    assert len(missing) == 1
    assert "finance" in (missing[0].received or "")
    assert "sales" in (missing[0].expected or "")


def test_session_bound_dependency_is_not_admitted(semantic_project_factory: ProjectFactory) -> None:
    source = (
        _PHYSICAL_PY
        + '\nfrom marivo._definition.relations import PopulationProgram, PopulationParameter, SourceIdentities\nfrom marivo._definition.types import Owner, ExactRef\nfrom marivo.refs import ref as private_ref\n\n_base = amount.sum(time=paid_at)\n_members = PopulationProgram(SourceIdentities(ExactRef(private_ref.entity("sales.orders"), "sha256:0")), Owner("session-a", "owner-a"))\n_session_bound = _base.per(PopulationParameter(_members)).mean()\n\n\n@ms.metric()\ndef member_mean():\n    return _session_bound\n'
    )
    result = _project(semantic_project_factory, source).load()
    assert result.status == "errored"
    rejected = [
        error for error in result.errors if error.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    ]
    assert len(rejected) == 1
    assert "metric:sales.member_mean" in rejected[0].semantic_refs
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
        "boom", kind=definition_kind, path=("DeclaredMetricRef.metric[0]",)
    )
    converted = SemanticExpressionError.from_definition(error, operation="metric closure")
    assert converted.kind == error_kind
    assert converted.details["definition_kind"] == definition_kind


def test_catalog_closure_reports_an_unpublished_metric(
    semantic_project_factory: ProjectFactory,
) -> None:
    from marivo.semantic.expressions import catalog_closure

    catalog = _catalog(semantic_project_factory)
    missing = declare(
        catalog.metrics.get("sales.revenue"),
        DeclarationRecord(
            kind="metric",
            name="missing",
            reference=ExactRef(ref_factory.metric("sales.missing"), "sha256:0"),
        ),
    )
    with pytest.raises(SemanticExpressionError) as raised:
        catalog_closure(catalog, missing)
    assert raised.value.kind == ErrorKind.MISSING_METRIC_REF


def test_collection_rejects_definitions_missing_from_the_model_scope(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = (
        _PHYSICAL_PY
        + '\nfrom marivo._definition.deferred import semantic_input, SemanticInput\nfrom marivo._definition.types import ExactRef, SumAll\nfrom marivo.refs import ref as private_ref\n\n_ghost_root = ExactRef(private_ref.entity("sales.ghost"), "sha256:0")\n_ghost_amount = semantic_input(SemanticInput(\n    ExactRef(private_ref.measure("sales.ghost.amount"), "sha256:0"), _ghost_root, "CNY", SumAll()\n))\n_foreign = _ghost_amount.sum()\n\n\n@ms.metric()\ndef foreign_revenue():\n    return _foreign\n'
    )
    result = _project(semantic_project_factory, source).load()
    assert result.status == "errored"
    rejected = [error for error in result.errors if "sales.foreign_revenue" in error.semantic_refs]
    assert len(rejected) == 1
    assert rejected[0].kind == ErrorKind.MISSING_ENTITY_REF
    assert "entity:sales.ghost" in (rejected[0].received or "")
    assert "measure:sales.ghost.amount" in (rejected[0].received or "")


def test_collection_rejects_a_changed_field_kind(semantic_project_factory: ProjectFactory) -> None:
    source = (
        _PHYSICAL_PY
        + '\nfrom marivo._definition.deferred import semantic_input, SemanticInput\nfrom marivo._definition.types import ExactRef, SumNone\nfrom marivo.refs import ref as private_ref\n\n_as_dimension = semantic_input(SemanticInput(\n    ExactRef(private_ref.dimension("sales.orders.paid_at"), "sha256:0"), ExactRef(private_ref.entity("sales.orders"), "sha256:0"), None, SumNone()\n))\n_miskinded = _as_dimension.count()\n\n\n@ms.metric()\ndef paid_count():\n    return _miskinded\n'
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
    assert isinstance(consume_metric(paid).node, ContributionFilter)
    assert isinstance(consume_metric(paid).node.operand, DeclaredMetricRef)
    settled = paid.where(status != "void")
    assert isinstance(consume_metric(settled).node, ContributionFilter)
    assert isinstance(
        consume_metric(catalog.metrics.get("sales.revenue").where(status == "paid")).node,
        ContributionFilter,
    )
    with pytest.raises(SemanticExpressionError) as raised:
        consume_metric(catalog.metrics.get("sales.average_order_value").where(status == "paid"))
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
    assert isinstance(consume_metric(old_revenue).node, DeclaredMetricRef)
    old_version = consume_metric(old_revenue).node.ref.version
    second = _changed_catalog(
        semantic_project_factory, tmp_path, 'status == "paid"', 'status == "settled"'
    )
    current = resolve(second, ms.ref.metric("sales.revenue"))
    assert isinstance(consume_metric(current).node, DeclaredMetricRef)
    assert consume_metric(current).node.ref.version != old_version
    closure = catalog_closure(second, old_revenue)
    assert computation_fingerprint(closure.order[-1]) == computation_fingerprint(
        consume_metric(current).node
    )
    assert consume_metric(current).node.ref in closure.refs
    assert consume_metric(old_revenue).node.ref not in closure.refs
    assert (
        consume_metric(second._state.registry.declared_metrics["sales.revenue"]).node
        in closure.order
    )
    assert consume_metric(old_revenue).node.ref.version == old_version


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
