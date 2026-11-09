"""Catalog resolution into private semantic expression values (R1.2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, NoReturn, TypeAlias, TypeVar, overload

import duckdb
import ibis
import ibis.expr.types as ibis_ir
import pytest
from ibis.common.deferred import Deferred, var

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.deferred import (
    DeclarationRecord,
    RelationshipParameter,
    resolver_of,
    root_declaration,
    semantic_input,
)
from marivo._definition.expressions import (
    ContributionFilter,
    EntityCount,
    MeasureAssertion,
    MetricExpression,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import children, computation_fingerprint
from marivo._definition.types import AxisBinding, ExactRef, SumAll, SumAllExcept, SumAxes, SumNone
from marivo.analysis.errors import AnalysisError
from marivo.refs import Ref, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import (
    MetricFacts,
    RowFacts,
    _consume,
    _Facts,
    _inspect,
    _Samples,
    consume_field,
    consume_metric,
    consume_predicate,
    consume_row,
)
from marivo.semantic._expression_values import _as_deferred, _ExpressionCarrier
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticExpressionError
from marivo.semantic.expressions import Dimension, Field, Measure, TimeField
from marivo.semantic.expressions import (
    _Entity as Entity,
)
from marivo.semantic.expressions import (
    _Relationship as Relationship,
)
from marivo.semantic.expressions import (
    resolve as resolve_private,
)
from marivo.semantic.loader import LoaderContext, loader_context
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import captured_catalog, catalog_value
from tests.shared_fixtures import resolve_test_symbol as resolve
from tests.support.refs import as_ref

_DOMAIN_PY = (
    'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n'
)
_OBJECTS_PY = 'import marivo.datasource as md\nimport marivo.semantic as ms\n\norders = ms.entity(\n    name="orders",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("orders"),\n    primary_key=["order_id"],\n)\ncustomers = ms.entity(\n    name="customers",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("customers"),\n    primary_key=["id"],\n)\n\nstatus = ms.dimension_column(name="status", entity=orders, column="status")\ncustomer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")\nid = ms.dimension_column(name="id", entity=customers, column="id")\npaid_at = ms.time_dimension_column(\n    name="paid_at",\n    entity=orders,\n    column="paid_at",\n    \n    parse=ms.timestamp(timezone="UTC"),\n)\ncreated_at = ms.time_dimension_column(\n    name="created_at",\n    entity=customers,\n    column="created_at",\n    \n    parse=ms.timestamp(timezone="UTC"),\n)\n\namount = ms.measure_column(\n    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY"\n)\nunit_price = ms.measure_column(\n    name="unit_price", entity=orders, column="unit_price", additivity=ms.non_additive(), unit="CNY"\n)\nquantity = ms.measure_column(\n    name="quantity", entity=orders, column="quantity", additivity=ms.additive_over(status)\n)\ncost = ms.measure_column(\n    name="cost", entity=orders, column="cost", additivity=ms.additive_all(), unit="CNY"\n)\namount_usd = ms.measure_column(\n    name="amount_usd", entity=orders, column="amount_usd", additivity=ms.additive_all(), unit="USD"\n)\ncredit = ms.measure_column(\n    name="credit", entity=customers, column="credit", additivity=ms.additive_all(), unit="CNY"\n)\nbalance = ms.measure_column(\n    name="balance",\n    entity=orders,\n    column="balance",\n    additivity=ms.additive_except(paid_at),\n    unit="CNY",\n)\n\nms.relationship(\n    name="orders_to_customers",\n    from_entity=orders,\n    to_entity=customers,\n    keys=[ms.join_on(customer_id, id)],\n)\n\nregions = ms.entity(\n    name="regions",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("regions"),\n    primary_key=["id"],\n)\nregion_id = ms.dimension_column(name="region_id", entity=customers, column="region_id")\nregion_key = ms.dimension_column(name="id", entity=regions, column="id")\nopened_at = ms.time_dimension_column(\n    name="opened_at",\n    entity=regions,\n    column="opened_at",\n    \n    parse=ms.timestamp(timezone="UTC"),\n)\nms.relationship(\n    name="customers_to_regions",\n    from_entity=customers,\n    to_entity=regions,\n    keys=[ms.join_on(region_id, region_key)],\n)\nms.relationship(\n    name="customers_to_orders",\n    from_entity=customers,\n    to_entity=orders,\n    keys=[ms.join_on(id, customer_id)],\n)\n'
ProjectFactory: TypeAlias = Callable[..., SemanticProject]


_T = TypeVar("_T")


@overload
def _facts(value: Deferred | _ExpressionCarrier) -> _Facts: ...


@overload
def _facts(value: _T) -> _T: ...


def _facts(value: _T | Deferred | _ExpressionCarrier) -> _T | _Facts:
    if isinstance(value, (Deferred, _ExpressionCarrier)):
        return _consume(_inspect(value).recipe)
    return value


def _row_facts(value: object) -> RowFacts | Field:
    result = _facts(value)
    assert isinstance(result, (RowFacts, Dimension, TimeField, Measure))
    return result


def _measure_facts(value: object) -> RowFacts | Measure:
    result = _facts(value)
    assert isinstance(result, (RowFacts, Measure))
    return result


def _unit_facts(value: object) -> RowFacts | MetricFacts | Measure | MeasureAssertion:
    result = _facts(value)
    assert isinstance(result, (RowFacts, MetricFacts, Measure, MeasureAssertion))
    return result


def _node_facts(value: object) -> RowFacts | MetricFacts:
    result = _facts(value)
    assert isinstance(result, (RowFacts, MetricFacts))
    return result


def _row_node(value: object) -> RowExpression:
    result = _node_facts(value).node
    assert isinstance(result, RowExpression)
    return result


def _metric_node(value: object) -> MetricExpression:
    result = _node_facts(value).node
    assert isinstance(result, MetricExpression)
    return result


def _aggregate_node(value: object) -> SourceAggregate:
    result = _node_facts(value).node
    assert isinstance(result, SourceAggregate)
    return result


def _weighted_node(value: object) -> SourceWeightedMean:
    result = _node_facts(value).node
    assert isinstance(result, SourceWeightedMean)
    return result


def _filtered_node(value: object) -> ContributionFilter:
    result = _node_facts(value).node
    assert isinstance(result, ContributionFilter)
    return result


def _per_node(value: object) -> PerReduce:
    result = _node_facts(value).node
    assert isinstance(result, PerReduce)
    return result


def _metric_facts(value: object) -> MetricFacts:
    result = _facts(value)
    assert isinstance(result, MetricFacts)
    return result


def _sample_facts(value: object) -> _Samples:
    result = _facts(value)
    assert isinstance(result, _Samples)
    return result


@overload
def _reference(value: ExactRef) -> Ref[SemanticKindTag]: ...


@overload
def _reference(
    value: Deferred | _ExpressionCarrier | Entity | Relationship | Field,
) -> ExactRef: ...


def _reference(
    value: ExactRef | Deferred | _ExpressionCarrier | Entity | Relationship | Field,
) -> ExactRef | Ref[SemanticKindTag]:
    if isinstance(value, (Deferred, _ExpressionCarrier)):
        return consume_field(value).reference
    if isinstance(value, Entity):
        return value._exact_ref
    if isinstance(value, Relationship):
        return value._parameter.ref
    return value.ref


def _consume_value(value: object) -> object:
    return _facts(value)


def _declaration(value: Deferred) -> DeclarationRecord:
    result = root_declaration(value)
    assert result is not None
    return result


def _resolved_field(catalog: SemanticCatalog, selector: object) -> Deferred:
    result = resolve(catalog, selector)
    assert isinstance(result, Deferred)
    return result


def _resolved_entity(catalog: SemanticCatalog, selector: object) -> Entity:
    result = resolve(catalog, selector)
    assert isinstance(result, Entity)
    return result


def _resolved_relationship(catalog: SemanticCatalog, selector: object) -> Relationship:
    result = resolve(catalog, selector)
    assert isinstance(result, Relationship)
    return result


def _physical(scope: SemanticCatalog, target: object) -> Deferred:
    """Resolve a physical measure column; published pure Measures resolve separately."""
    value = resolve(scope, target)
    assert type(value) is Deferred
    return value


@pytest.fixture
def catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _OBJECTS_PY}
    )
    return SemanticCatalog(project)


def test_fields_resolve_with_exact_versions_and_owning_root(catalog: SemanticCatalog) -> None:
    amount = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    assert type(amount) is Deferred
    assert type(status) is Deferred
    assert type(paid_at) is Deferred
    assert type(orders) is Entity
    assert _reference(_reference(amount)) == ref_factory.measure("sales.orders.amount")
    assert _reference(amount).version.startswith("sha256:")
    assert (
        _row_facts(amount).root
        == _reference(orders)
        == _row_facts(status).root
        == _row_facts(paid_at).root
    )
    assert _unit_facts(amount).unit == "CNY"
    assert orders.primary_key == ("order_id",)
    again = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    assert (
        _reference(again),
        _row_facts(again).root,
        _unit_facts(again).unit,
        _measure_facts(again).permission,
    ) == (
        _reference(amount),
        _row_facts(amount).root,
        _unit_facts(amount).unit,
        _measure_facts(amount).permission,
    )
    assert _reference(amount).version != _reference(status).version


def test_versions_follow_definition_dependencies(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    first = SemanticCatalog(
        semantic_project_factory({"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _OBJECTS_PY})
    )
    changed_source = _OBJECTS_PY.replace('column="amount"', 'column="gross_amount"')
    second_root = tmp_path / "changed"
    second_root.mkdir()
    second = SemanticCatalog(
        semantic_project_factory(
            {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": changed_source},
            workspace_dir=second_root,
        )
    )
    amount_ref = ms.ref.measure("sales.orders.amount")
    status_ref = ms.ref.dimension("sales.orders.status")
    assert (
        _reference(_physical(first, amount_ref)).version
        != _reference(_physical(second, amount_ref)).version
    )
    assert (
        _reference(_resolved_field(first, status_ref)).version
        == _reference(_resolved_field(second, status_ref)).version
    )


def test_declaration_state_resolves_same_exact_values_as_catalog(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("declaration resolution opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    ctx = LoaderContext(model_name="sales", file_path="<declared>", current_model_file="<declared>")
    with loader_context(ctx):
        for source in (_DOMAIN_PY, _OBJECTS_PY):
            exec(compile(source, "<declared>", "exec"), {})
    state = captured_catalog(
        [ctx], datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    entity_ref = ms.ref.entity("sales.orders")
    measure_ref = ms.ref.measure("sales.orders.balance")
    declared_entity, loaded_entity = (
        _resolved_entity(state, entity_ref),
        _resolved_entity(catalog, entity_ref),
    )
    declared, loaded = (_physical(state, measure_ref), _physical(catalog, measure_ref))
    assert declared_entity == loaded_entity
    assert _reference(declared) == _reference(loaded)
    assert _reference(declared).version.startswith("sha256:")
    assert (
        _row_facts(declared).root,
        _unit_facts(declared).unit,
        _measure_facts(declared).permission,
    ) == (
        _row_facts(loaded).root,
        _unit_facts(loaded).unit,
        _measure_facts(loaded).permission,
    )
    assert _measure_facts(declared).permission == SumAllExcept(
        (_reference(resolve_private(catalog, ref_factory.time_dimension("sales.orders.paid_at"))),)
    )


def test_declared_constructors_capture_catalog_versions_without_business_reads(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends
    from marivo.semantic.loader import loader_context

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("constructor read a business source")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    ctx = LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    namespace: dict[str, object] = {}
    with loader_context(ctx):
        exec(_DOMAIN_PY, namespace)
        exec(
            _OBJECTS_PY.replace("ms.relationship(", "relationship_value = ms.relationship(", 1),
            namespace,
        )
        finalized = captured_catalog()
    for name, key in (
        ("orders", ms.ref.entity("sales.orders")),
        ("amount", ms.ref.measure("sales.orders.amount")),
        ("paid_at", ms.ref.time_dimension("sales.orders.paid_at")),
        ("status", ms.ref.dimension("sales.orders.status")),
        ("relationship_value", ms.ref.relationship("sales.orders_to_customers")),
    ):
        captured = namespace[name]
        assert isinstance(captured, Deferred)
        value = resolve(finalized, captured)
        loaded = resolve(catalog, key)
        assert isinstance(value, (Deferred, Entity, Relationship))
        assert isinstance(loaded, (Deferred, Entity, Relationship))
        assert _reference(value) == _reference(loaded)
    amount = _resolved_field(finalized, ms.ref.measure("sales.orders.amount"))
    paid_at = _resolved_field(finalized, ms.ref.time_dimension("sales.orders.paid_at"))
    assert isinstance(amount, Deferred) and isinstance(paid_at, Deferred)
    assert computation_fingerprint(
        _node_facts(amount.sum(time=paid_at)).node
    ) == computation_fingerprint(
        _node_facts(
            _measure(catalog, "amount").sum(
                time=resolve_private(catalog, ref_factory.time_dimension("sales.orders.paid_at"))
            )
        ).node
    )


def test_constructor_snapshots_bind_explicit_time_in_either_declaration_order(
    catalog: SemanticCatalog,
) -> None:
    from marivo.semantic.validator import assembly_validate

    field_first: bool
    for field_first in (False, True):
        ctx = LoaderContext(
            default_domain="sales",
            datasource_irs=tuple(catalog._state.registry.datasources.values()),
        )
        with loader_context(ctx):
            ms.domain(name="sales", owner="Analytics", default=True)
            orders_ref = ms.entity(
                name="orders",
                datasource=ms.ref.datasource("warehouse"),
                source=md.table("orders"),
                primary_key=["order_id"],
            )
            if field_first:
                paid_at_ref = ms.time_dimension_column(
                    name="paid_at",
                    entity=orders_ref,
                    column="paid_at",
                    parse=ms.timestamp(timezone="UTC"),
                )
            amount_ref = ms.measure_column(
                name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all()
            )
            if not field_first:
                paid_at_ref = ms.time_dimension_column(
                    name="paid_at",
                    entity=orders_ref,
                    column="paid_at",
                    parse=ms.timestamp(timezone="UTC"),
                )
            cost_ref = ms.measure_column(
                name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all()
            )
            state = captured_catalog()
            amount = _resolved_field(state, amount_ref)
            paid_at = _resolved_field(state, paid_at_ref)
            cost = _resolved_field(state, cost_ref)
        errors, _ = assembly_validate(state._state.registry, state._state.sidecar)
        assert errors == []
        finalized = _physical(state, ms.ref.measure("sales.orders.amount"))
        assert _reference(amount) == _reference(finalized)
        forward = _node_facts((amount + cost).sum(time=paid_at)).node
        reverse = _node_facts((cost + amount).sum(time=paid_at)).node
        assert isinstance(forward, SourceAggregate) and isinstance(reverse, SourceAggregate)
        assert forward.time == reverse.time == TimeBinding(_reference(paid_at))


def test_constructor_snapshots_accept_later_declared_permission_axis(
    catalog: SemanticCatalog,
) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        amount_ref = ms.measure_column(
            name="amount", entity=orders_ref, column="amount", additivity=ms.non_additive()
        )
        region_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")
        state = captured_catalog()
        amount = _resolved_field(state, amount_ref)
        region = _resolved_field(state, region_ref)

        @ms.measure(additivity=ms.additive_over(region))
        def regional() -> Deferred:
            return amount

    assert _measure_facts(regional).permission == SumAxes((_reference(region),))
    assert _measure_facts(amount).coordinates == (_reference(region),)


def test_constructor_snapshots_close_retained_ibis_forward_dependencies(
    catalog: SemanticCatalog,
) -> None:
    from marivo.semantic.validator import assembly_validate

    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        ms.domain(name="sales", owner="Analytics", default=True)
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        later_ref = ms.ref.dimension("sales.orders.region")

        @ms.dimension(entity=orders_ref, name="normalized")
        def normalized_ref(rows: ibis_ir.Table) -> ibis_ir.Value:
            return ms.bind(later_ref, rows).upper()

        region_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")
        state = captured_catalog()
        normalized = _resolved_field(state, normalized_ref)
        region = _resolved_field(state, region_ref)
    errors, _ = assembly_validate(state._state.registry, state._state.sidecar)
    assert errors == []
    assert _reference(region) == _reference(_resolved_field(state, later_ref))
    assert _reference(normalized) == _reference(
        resolve_private(state, ref_factory.dimension("sales.orders.normalized"))
    )


def test_ibis_bindings_use_completed_consumer_definitions(catalog: SemanticCatalog) -> None:
    from marivo.semantic._expression_binding import evaluate_expression_body

    def assert_binding(column: str, lookup: bool) -> None:
        datasource_irs = tuple(catalog._state.registry.datasources.values())
        first = LoaderContext(default_domain="sales", datasource_irs=datasource_irs)
        with loader_context(first):
            orders_ref = ms.entity(
                name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
            )
            region_ref = ms.dimension_column(name="region", entity=orders_ref, column="old_region")
            old_region = _resolved_field(captured_catalog(), region_ref)
        binding_field = region_ref if lookup else old_region
        second = LoaderContext(default_domain="sales", datasource_irs=datasource_irs)
        with loader_context(second):
            orders_ref = ms.entity(
                name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
            )

            @ms.dimension(entity=orders_ref)
            def normalized(rows: ibis_ir.Table) -> ibis_ir.Value:
                return ms.bind(binding_field, rows).upper()

            region_ref = ms.dimension_column(name="region", entity=orders_ref, column=column)
            state = captured_catalog()
        assert (_reference(_resolved_field(state, region_ref)) == _reference(old_region)) is (
            column == "old_region"
        )
        normalized_ref = as_ref(normalized)
        assert normalized_ref is not None
        entity_ref = as_ref(orders_ref)
        assert entity_ref is not None
        assert entity_ref == ref_factory.entity("sales.orders")
        expression = evaluate_expression_body(
            catalog_definition_fingerprint=state._state.definition_fingerprint,
            expression_sidecar=state._state.sidecar,
            owning_ref=normalized_ref,
            body=state._state.sidecar.bodies[normalized_ref],
            entity_refs=(ref_factory.entity(entity_ref.path),),
            aliases=(ibis.table({column: "string"}, name="orders"),),
        )

    for column, lookup in (("old_region", False), ("new_region", False), ("new_region", True)):
        assert_binding(column, lookup)


def test_physical_capture_uses_updated_consumer_definitions(catalog: SemanticCatalog) -> None:
    input_kind: str
    for input_kind in ("entity", "coordinate"):
        old_entity = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
        old_status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
        ctx = LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
        source = _OBJECTS_PY.replace('md.table("orders")', 'md.table("new_orders")')
        source = source.replace('column="status"', 'column="new_status"')
        with loader_context(ctx):
            namespace: dict[str, object] = {}
            exec(_DOMAIN_PY, namespace)
            exec(source, namespace)
            probe_ref = ms.measure_column(
                name="probe",
                entity=old_entity if input_kind == "entity" else ms.ref.entity("sales.orders"),
                column="amount",
                additivity=ms.additive_over(old_status)
                if input_kind == "coordinate"
                else ms.additive_all(),
            )
            state = captured_catalog()
        probe = _resolved_field(state, probe_ref)
        current_entity = _resolved_entity(state, ms.ref.entity("sales.orders"))
        current_status = _resolved_field(state, ms.ref.dimension("sales.orders.status"))
        assert _row_facts(probe).root == _reference(current_entity) and _row_facts(
            probe
        ).root != _reference(old_entity)
        assert _reference(current_status) != _reference(old_status)
        assert (
            state._state.sidecar.bodies[ref_factory.dimension("sales.orders.status")].source_column
            == "new_status"
        )
        if input_kind == "coordinate":
            assert _measure_facts(probe).permission == SumAxes((_reference(current_status),))


def test_pure_measure_uses_its_row_coordinate_definitions(catalog: SemanticCatalog) -> None:
    old_status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    ctx = LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    with loader_context(ctx):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace('column="status"', 'column="new_status"'), namespace)
        state = captured_catalog()
        amount = _resolved_field(state, ms.ref.measure("sales.orders.amount"))

    @ms.measure(additivity=ms.additive_over(old_status))
    def granted() -> Deferred:
        return amount

    current_status = _resolved_field(state, ms.ref.dimension("sales.orders.status"))
    assert current_status is not None and _reference(current_status) != _reference(old_status)
    assert _measure_facts(granted).permission == SumAxes((_reference(current_status),))
    assert (
        _row_facts(granted).root == _row_facts(amount).root
        and _unit_facts(granted).unit == _unit_facts(amount).unit
    )


def test_file_loader_resolves_owners_in_its_scope(
    catalog: SemanticCatalog,
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import ModuleType

    mode: str
    for mode in ("same", "updated", "lookup"):
        old = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
        module = ModuleType("_marivo_frozen_inputs")
        vars(module)["owner"] = old
        monkeypatch.setitem(sys.modules, module.__name__, module)
        source = (
            f"import marivo.datasource as md\nimport marivo.semantic as ms\nfrom _marivo_frozen_inputs import owner\norders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({('orders' if mode == 'same' else 'new_orders')!r}), primary_key=['order_id'])\namount = ms.measure_column(name='amount', entity="
            + ("ms.ref.entity('sales.orders')" if mode == "lookup" else "owner")
            + ", column='amount', additivity=ms.additive_all())\n"
        )
        root = tmp_path / mode
        root.mkdir()
        project = semantic_project_factory(
            {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": source}, workspace_dir=root
        )
        current = SemanticCatalog(project)
        amount = _physical(current, ms.ref.measure("sales.orders.amount"))
        assert (_row_facts(amount).root == _reference(old)) is (mode == "same")
        assert _row_facts(amount).root == _reference(
            resolve_private(current, ref_factory.entity("sales.orders"))
        )
        assert current._state.registry.entities["sales.orders"].source == md.table(
            "orders" if mode == "same" else "new_orders"
        )


def test_metric_reflected_comparisons_match_ordinary_composition(catalog: SemanticCatalog) -> None:
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(
        inspect.getsource(test_metric_reflected_comparisons_match_ordinary_composition)
    )
    literal_left = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant)
    ]
    assert {type(node.ops[0]) for node in literal_left} >= {
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Eq,
        ast.NotEq,
    }
    assert any(
        isinstance(node.left, ast.Constant) and node.left.value == "2026-01-01"
        for node in literal_left
    )
    amount = _measure(catalog, "amount")
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    # Ibis Deferred methods return Any in its stubs; these bodies are parsed as declarations.
    @ms.metric()
    def positive() -> Deferred:
        return amount.sum().where(0 < amount)  # type: ignore[no-any-return]  # noqa: SIM300

    @ms.metric()
    def bounded() -> Deferred:
        return amount.sum().where((0 <= amount) & (100 > amount) & (100 >= amount))  # type: ignore[no-any-return]  # noqa: SIM300

    @ms.metric()
    def literal_fields() -> Deferred:
        return amount.sum().where(("paid" == status) & ("void" != status))  # type: ignore[no-any-return]  # noqa: SIM300

    @ms.metric()
    def time_filter() -> Deferred:
        return amount.sum().where("2026-01-01" < paid_at)  # type: ignore[no-any-return]  # noqa: SIM300

    for declared, ordinary in (
        (positive, amount.sum().where(amount > 0)),
        (bounded, amount.sum().where((amount >= 0) & (amount < 100) & (amount <= 100))),
        (literal_fields, amount.sum().where((status == "paid") & (status != "void"))),
        (time_filter, amount.sum().where(paid_at > "2026-01-01")),
    ):
        assert computation_fingerprint(_node_facts(declared).node) == computation_fingerprint(
            _node_facts(ordinary).node
        )


def test_incomplete_scope_can_be_repaired_without_mutating_old_catalog(
    catalog: SemanticCatalog,
) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        region = ms.ref.dimension("sales.orders.region")

        @ms.dimension(entity=orders)
        def normalized(rows: ibis_ir.Table) -> ibis_ir.Value:
            return ms.bind(region, rows).upper()

        assert isinstance(normalized, Deferred)
        with pytest.raises(SemanticError) as incomplete:
            captured_catalog()
        ms.dimension_column(name="region", entity=orders, column="region")
        first = captured_catalog()
        paid_at = ms.time_dimension_column(name="paid_at", entity=orders, column="paid_at")
        second = captured_catalog()
    with pytest.raises(SemanticError):
        resolve(first, paid_at)
    assert _reference(resolve(second, paid_at)).ref.path == "sales.orders.paid_at"
    assert _reference(resolve(first, region)) == _reference(resolve(second, region))


def test_pure_declaration_captures_symbols_before_complete_binding(
    catalog: SemanticCatalog,
) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        amount = ms.measure_column(
            name="amount", entity=orders, column="amount", additivity=ms.additive_all()
        )

        @ms.measure()
        def named_amount() -> Deferred:
            return amount

        ctx.declarations.append(_as_deferred(named_amount))
        loaded = captured_catalog()
    assert (
        consume_row(catalog_value(loaded, "sales.orders.named_amount", kind="field")).node
        == consume_row(_resolved_field(loaded, amount)).node
    )


def test_later_file_adds_an_explicit_time_field_to_complete_scope(catalog: SemanticCatalog) -> None:
    first = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(first):
        orders = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        earlier = captured_catalog()
    later = LoaderContext(default_domain="sales", datasource_irs=first.datasource_irs)
    with loader_context(later):
        paid_at = ms.time_dimension_column(name="paid_at", entity=orders, column="paid_at")
        complete = captured_catalog((first, later), datasource_irs=first.datasource_irs)
    with pytest.raises(SemanticError):
        resolve(earlier, paid_at)
    resolved_time = _resolved_field(complete, paid_at)
    count = _resolved_entity(complete, orders).count(time=resolved_time)
    count_node = consume_metric(count).node
    assert isinstance(count_node, EntityCount)
    assert count_node.time == TimeBinding(_reference(resolved_time))


def test_finalized_capture_imports_once_and_matches_catalog(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    counter = tmp_path / "module-effects.txt"
    physical = 'import marivo.semantic as ms\nimport marivo.datasource as md\nfrom pathlib import Path\nwith Path(COUNTER).open("a") as output:\n    output.write("capture\\n")\norders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))\namount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all())\nlater_ref = ms.ref.dimension("sales.orders.region")\n@ms.dimension(entity=orders_ref)\ndef normalized(rows):\n    return ms.bind(later_ref, rows).upper()\npaid_at_ref = ms.time_dimension_column(name="paid_at", entity=orders_ref, column="paid_at")\ncost_ref = ms.measure_column(name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all())\nregion_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")\n'.replace(
        "COUNTER", repr(str(counter))
    )
    composition = """import marivo.semantic as ms
from .z_physical import orders_ref, amount_ref, paid_at_ref, cost_ref, normalized
@ms.metric()
def revenue():
    return (amount_ref + cost_ref).sum(time=paid_at_ref)
"""
    loaded = SemanticCatalog(
        semantic_project_factory(
            {
                "sales/_domain.py": _DOMAIN_PY,
                "sales/a_composition.py": composition,
                "sales/z_physical.py": physical,
            }
        )
    )
    assert counter.read_text() == "capture\n"
    amount = catalog_value(loaded, "sales.orders.amount", kind="measure")
    cost = catalog_value(loaded, "sales.orders.cost", kind="measure")
    normalized = catalog_value(loaded, "sales.orders.normalized", kind="dimension")
    paid_at = catalog_value(loaded, "sales.orders.paid_at", kind="time_dimension")
    expected = TimeBinding(_reference(paid_at))
    assert _aggregate_node((amount + cost).sum(time=paid_at)).time == expected
    assert _aggregate_node((cost + amount).sum(time=paid_at)).time == expected
    assert consume_field(normalized).reference.version.startswith("sha256:")


def test_retired_declaration_syntax_has_specific_reauthoring_errors(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    for factory, kwargs, token in (
        (ms.metric, {"entities": [ms.ref.entity("sales.orders")]}, "zero-argument"),
        (ms.metric, {"fanout_policy": "aggregate_then_join"}, "mv.membership"),
        (ms.metric, {"empty": "null"}, "without an empty policy"),
        (ms.measure_column, {"status_time_fold": None}, "per("),
        (ms.metric, {"time_via": (ms.ref.relationship("sales.orders_to_customers"),)}, "via("),
    ):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(factory(**kwargs))
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert token in caught.value.repair.action
    with pytest.raises(SemanticExpressionError) as approximate:
        _consume_value(ms.aggregate(measure=amount, agg="approx_median"))
    assert approximate.value.repair is not None


def test_measure_declaration_names_the_ad_hoc_graph_without_loader(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends
    from marivo.semantic.loader import _LOADER_CTX

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("measure declaration opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    assert _LOADER_CTX.get() is None

    @ms.measure(ai_context=ms.ai_context(business_definition="Order margin"))
    def order_margin() -> Deferred:
        """Margin of one order."""
        margin = amount - cost
        return margin

    @ms.measure()
    def trial_margin() -> Deferred:
        return amount - cost

    margin_alias = order_margin
    ad_hoc = amount - cost
    assert type(order_margin) is ms.RowExpression
    assert (
        _declaration(_as_deferred(order_margin)).name,
        _declaration(_as_deferred(trial_margin)).name,
    ) == (
        "order_margin",
        "trial_margin",
    )
    assert _declaration(_as_deferred(order_margin)).ai_context is not None
    assert (
        _row_facts(order_margin).root,
        _unit_facts(order_margin).unit,
        _measure_facts(order_margin).permission,
    ) == (_row_facts(amount).root, "CNY", SumAll())
    assert computation_fingerprint(_node_facts(order_margin).node) == computation_fingerprint(
        _node_facts(ad_hoc).node
    )
    sums = {
        computation_fingerprint(_node_facts(m.sum()).node)
        for m in (order_margin, trial_margin, margin_alias)
    }
    assert sums == {computation_fingerprint(_node_facts(ad_hoc.sum()).node)}
    assert _declaration(_as_deferred(order_margin)).name == "order_margin"


def test_measure_grant_authorizes_an_otherwise_unsummable_product(catalog: SemanticCatalog) -> None:
    quantity, unit_price = (_measure(catalog, "quantity"), _measure(catalog, "unit_price"))
    amount, cost, balance = (_measure(catalog, name) for name in ("amount", "cost", "balance"))

    @ms.measure()
    def priced() -> Deferred:
        return quantity * unit_price

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def order_amount() -> Deferred:
        return quantity * unit_price

    unsummable: tuple[Deferred | ms.RowExpression[Literal["numeric"]], ...] = (
        quantity * unit_price,
        priced,
        unit_price,
    )
    for operand in unsummable:
        with pytest.raises(SemanticExpressionError) as denied:
            _consume_value(operand.sum())
        assert denied.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value
    assert _unit_facts(order_amount.sum()).unit == "CNY"
    assert (_unit_facts(order_amount).unit, _measure_facts(order_amount).permission) == (
        "CNY",
        SumAll(),
    )
    definition = consume_row(order_amount).node
    assert isinstance(definition, MeasureAssertion)
    assert (_unit_facts(definition).unit, definition.permission) == ("CNY", SumAll())
    assert definition.authorization.source.ref == ref_factory.measure("sales.orders.order_amount")
    assert computation_fingerprint(definition) != computation_fingerprint(consume_row(priced).node)

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def margin() -> Deferred:
        return amount - cost

    assert computation_fingerprint(consume_row(margin).node) == computation_fingerprint(
        _node_facts(amount - cost).node
    )
    with pytest.raises(SemanticExpressionError) as conflict:

        @ms.measure(unit="USD")
        def usd_margin() -> Deferred:
            return amount - cost

    assert conflict.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    with pytest.raises(SemanticExpressionError) as erased:

        @ms.measure(additivity=ms.additive_all())
        def open_balance() -> Deferred:
            return balance * unit_price

    assert erased.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert "paid_at" in str(erased.value.received)


def test_measure_declaration_rejects_wrong_stage_and_retired_bodies(
    catalog: SemanticCatalog,
) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    total = amount.sum()

    def rejected(body: Callable[..., object]) -> SemanticExpressionError:
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.measure()(body))  # type: ignore[arg-type]  # Rejected declaration shapes.
        assert caught.value.expected and caught.value.received
        assert caught.value.repair is not None
        return caught.value

    def passthrough(value: Deferred) -> Deferred:
        return value

    def revenue() -> Deferred:
        return amount.sum()  # type: ignore[no-any-return]

    def reduced() -> Deferred:
        return total  # type: ignore[no-any-return]

    def old_amount(orders_table: Mapping[str, object]) -> object:
        return orders_table["amount"]

    def called() -> Deferred:
        return passthrough(amount - cost)

    def rebound() -> Deferred:
        margin = amount - cost
        margin = margin * 2
        return margin

    def _gross_margin() -> Deferred:
        return amount - cost

    stage = rejected(revenue)
    assert stage.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert stage.received == "MetricFacts"
    named_metric = rejected(reduced)
    assert named_metric.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert "Metric" in str(named_metric.received)
    for body, token in ((old_amount, "orders_table"), (called, "passthrough"), (rebound, "margin")):
        error = rejected(body)
        assert error.kind == ErrorKind.INVALID_COMPONENT_BODY.value
        assert token in str(error.received)
    name = rejected(_gross_margin)
    assert name.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert name.received == "_gross_margin"


def test_measure_body_resolves_receivers_without_executing_external_calls(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    calls: list[str] = []

    class Pretender:
        def divide(self, _other: int) -> Deferred:
            calls.append("divide")
            return amount * 2

    fake = Pretender()
    divide_alias = fake.divide

    def forged_receiver() -> Deferred:
        return fake.divide(2)

    def forged_alias() -> Deferred:
        return divide_alias(2)

    def effect_before_return() -> Deferred:
        calls.append("effect")
        return amount

    for body in (forged_receiver, forged_alias, effect_before_return):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.measure()(body))
        assert caught.value.kind in {
            ErrorKind.INVALID_COMPONENT_BODY,
            ErrorKind.INVALID_EXPRESSION_STAGE,
        }
        assert caught.value.expected and caught.value.received and caught.value.repair
    assert calls == []

    @ms.measure()
    def scaled_amount() -> Deferred:
        divisor = -2
        return amount / divisor

    assert computation_fingerprint(_node_facts(scaled_amount).node) == computation_fingerprint(
        _node_facts(amount / -2).node
    )
    location = _declaration(_as_deferred(scaled_amount)).location
    assert location is not None
    assert location.path == __file__
    assert location.line > 0


def test_measure_assertions_validate_real_axes_and_retain_fixed_time(
    catalog: SemanticCatalog,
) -> None:
    amount, balance = (_measure(catalog, "amount"), _measure(catalog, "balance"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    @ms.measure(additivity=ms.additive_over(ms.ref.dimension("sales.orders.status")))
    def by_status() -> Deferred:
        return amount

    assert _measure_facts(by_status).permission == SumAxes((_reference(status),))
    assert isinstance(_node_facts(by_status).node, MeasureAssertion)

    def current_amount() -> Deferred:
        return amount

    for axis in (ms.ref.dimension("sales.orders.missing"), ms.ref.dimension("sales.customers.id")):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.measure(additivity=ms.additive_over(axis))(current_amount))
        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        axis_ref = as_ref(axis)
        assert axis_ref is not None
        assert axis_ref.path in str(caught.value.received)

    @ms.measure(additivity=ms.non_additive())
    def balance_product() -> Deferred:
        return balance * amount

    @ms.measure(additivity=ms.additive_except(ms.ref.time_dimension("sales.orders.paid_at")))
    def fixed_product() -> ms.RowExpression[Literal["numeric"]]:
        return -balance_product * 2

    assert _measure_facts(fixed_product).permission == SumAllExcept((_reference(paid_at),))
    with pytest.raises(SemanticExpressionError) as erased:

        @ms.measure(additivity=ms.additive_all())
        def unrestricted_product() -> ms.RowExpression[Literal["numeric"]]:
            return fixed_product

    assert erased.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert _reference(_reference(paid_at)).path in str(erased.value.received)


def test_additivity_rejects_routed_time_coordinates(catalog: SemanticCatalog) -> None:
    policy: str
    for policy in ("over", "except_"):
        paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
        relationship = _resolved_relationship(
            catalog, ms.ref.relationship("sales.orders_to_customers")
        )
        routed = paid_at.via((relationship,))
        field_read = consume_field(routed).field_read
        assert field_read is not None and field_read.path
        with pytest.raises(SemanticError) as caught:
            if policy == "over":
                ms.additive_over(routed)
            else:
                ms.additive_except(routed)
        assert caught.value.kind == ErrorKind.INVALID_TIME_PATH
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert caught.value.repair.action
        assert ms.additive_over(paid_at).coordinates == (_reference(_reference(paid_at)).path,)
        assert ms.additive_except(paid_at).exceptions == (_reference(_reference(paid_at)).path,)


def test_measure_inherits_unknown_unit_and_normalizes_redundant_assertions(
    catalog: SemanticCatalog,
) -> None:
    quantity = _measure(catalog, "quantity")
    amount = _measure(catalog, "amount")

    @ms.measure(unit=None, additivity=None)
    def inherited() -> Deferred:
        return quantity

    @ms.measure(unit="CNY")
    def asserted() -> Deferred:
        return quantity

    @ms.measure(unit="CNY.CNY/CNY", additivity=ms.additive_all())
    def redundant() -> Deferred:
        return amount

    assert _unit_facts(inherited).unit is None
    assert _measure_facts(inherited).permission == _measure_facts(quantity).permission
    assert computation_fingerprint(_node_facts(inherited).node) == computation_fingerprint(
        _node_facts(quantity).node
    )
    assert _unit_facts(asserted).unit == "CNY"
    asserted_facts = _row_facts(asserted)
    assert isinstance(asserted_facts, RowFacts)
    asserted_node = asserted_facts.node
    assert isinstance(asserted_node, MeasureAssertion)
    assert asserted_node.unit == "CNY"
    assert asserted_node.permission is None
    assert _unit_facts(quantity).unit is None
    assert computation_fingerprint(_node_facts(redundant).node) == computation_fingerprint(
        _node_facts(amount).node
    )
    assert isinstance(redundant * 2, ms.RowExpression)
    assert root_declaration(_as_deferred(redundant * 2)) is None


def test_metric_declaration_matches_ad_hoc_graph_without_loader(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))

    @ms.metric(
        domain=ms.ref.domain("sales"), ai_context=ms.ai_context(business_definition="Paid margin")
    )
    def margin() -> Deferred:
        """One named computation."""
        row = amount - cost
        return row.sum(time=paid_at).where(status == "paid")  # type: ignore[no-any-return]

    @ms.metric()
    def order_count() -> Deferred:
        return orders.count(time=paid_at).where(status == "paid")  # type: ignore[no-any-return]

    @ms.metric()
    def average_margin() -> ms.MetricExpression[Literal["numeric"]]:
        return margin / order_count

    trial = (amount - cost).sum(time=paid_at).where(status == "paid")
    assert type(margin) is ms.MetricExpression
    assert _declaration(_as_deferred(margin)).name == "margin"
    assert _declaration(_as_deferred(margin)).domain == ref_factory.domain("sales")
    assert _declaration(_as_deferred(margin)).ai_context is not None
    assert computation_fingerprint(_node_facts(margin).node) == computation_fingerprint(
        _node_facts(trial).node
    )
    assert computation_fingerprint(_node_facts(average_margin).node) == computation_fingerprint(
        _node_facts(trial / _as_deferred(order_count)).node
    )
    assert isinstance(margin * 2, ms.MetricExpression)
    alias = margin
    assert alias is margin
    axes = (status,)

    @ms.metric()
    def customer_average() -> ms.MetricExpression[Literal["numeric"]]:
        return margin.per(axes).mean(nulls="error")  # type: ignore[arg-type]

    assert computation_fingerprint(_node_facts(customer_average).node) == computation_fingerprint(
        _node_facts(margin.per(status).mean(nulls="error")).node  # type: ignore[call-overload]
    )


def test_metric_declaration_rejects_wrong_stages_and_receivers(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")

    def row() -> Deferred:
        return amount

    def helper(value: Deferred) -> Deferred:
        return value > 0

    def arbitrary_call() -> Deferred:
        return amount.sum().where(helper(amount))  # type: ignore[no-any-return]

    def table(rows: object) -> object:
        return rows

    def invalid_samples() -> Deferred:
        return pending  # type: ignore[no-any-return]

    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    pending = amount.sum().per(status)
    for body in (row, arbitrary_call, table, invalid_samples):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.metric()(body))  # type: ignore[arg-type]  # Rejected declaration shapes.
        assert caught.value.expected and caught.value.received and caught.value.repair


def test_measure_permissions_map_declared_additivity(catalog: SemanticCatalog) -> None:
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    assert (
        _measure_facts(_physical(catalog, ref_factory.measure("sales.orders.amount"))).permission
        == SumAll()
    )
    assert (
        _measure_facts(
            _physical(catalog, ref_factory.measure("sales.orders.unit_price"))
        ).permission
        == SumNone()
    )
    quantity = _physical(catalog, ms.ref.measure("sales.orders.quantity"))
    assert _measure_facts(quantity).permission == SumAxes((_reference(status),))
    assert _unit_facts(quantity).unit is None
    balance = _physical(catalog, ms.ref.measure("sales.orders.balance"))
    assert _measure_facts(balance).permission == SumAllExcept((_reference(paid_at),))


def test_relationship_resolves_endpoints_and_cardinality(catalog: SemanticCatalog) -> None:
    relationship = _resolved_relationship(catalog, ms.ref.relationship("sales.orders_to_customers"))
    customers = _resolved_entity(catalog, ms.ref.entity("sales.customers"))
    assert type(relationship) is Relationship
    assert relationship._parameter.from_entity == _reference(
        resolve_private(catalog, ref_factory.entity("sales.orders"))
    )
    assert relationship._parameter.to_entity == _reference(customers)
    assert relationship.cardinality == "many_to_one"
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    assert _row_facts(created_at).root == _reference(customers)


def test_resolved_values_are_immutable_with_bounded_repr(catalog: SemanticCatalog) -> None:
    amount = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    contract = catalog.get("measure:sales.orders.amount")
    assert "\n" not in repr(contract) and len(repr(contract)) < 120
    assert isinstance(consume_field(amount).reference, ExactRef)


def test_unsupported_and_absent_refs_fail_structurally(catalog: SemanticCatalog) -> None:
    with pytest.raises(SemanticExpressionError) as metric:
        _consume_value(_resolved_field(catalog, ms.ref.metric("sales.revenue")))
    assert metric.value.kind == ErrorKind.METRIC_NOT_FOUND.value
    assert metric.value.received == "metric:sales.revenue"
    assert metric.value.repair is not None
    with pytest.raises(SemanticExpressionError) as raw:
        _consume_value(resolve_private(catalog, "sales.orders.amount"))  # type: ignore[call-overload]  # Invalid raw string.
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert raw.value.received == "str"
    with pytest.raises(SemanticError) as absent:
        _consume_value(_physical(catalog, ms.ref.measure("sales.orders.missing")))
    assert absent.value.kind == ErrorKind.NOT_FOUND.value


def _measure(catalog: SemanticCatalog, name: str) -> Deferred:
    return _physical(catalog, ms.ref.measure(f"sales.orders.{name}"))


def test_public_boolean_conditionals_preserve_predicate_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.shared_fixtures import write_minimal_catalog_project

    write_minimal_catalog_project(tmp_path)
    database = tmp_path / "facts.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "CREATE TABLE orders(order_id BIGINT, status VARCHAR, paid_at TIMESTAMP, amount DOUBLE)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1, 'paid', NULL, 10), (2, 'refund', NULL, 20), "
            "(3, NULL, NULL, 30), (4, 'paid', NULL, NULL)"
        )
    finally:
        connection.close()
    (tmp_path / "models/datasources/warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    session = mv.session.get_or_create(name="boolean_conditionals", timezone="UTC")
    status = session.catalog.get("dimension:sales.orders.status")
    amount = session.catalog.get("measure:sales.orders.amount")
    revenue = session.catalog.get("metric:sales.revenue")
    assert status.kind == "dimension" and amount.kind == "measure" and revenue.kind == "metric"
    paid = status == "paid"
    for condition, expected in (
        (paid & (amount > 5), 10.0),
        (paid | (status == "refund"), 30.0),
        (paid & True, 10.0),
        (True & paid, 10.0),
        (paid | False, 10.0),
        (False | paid, 10.0),
        (paid & False, 0.0),
        (False & paid, 0.0),
        (paid | True, 60.0),
        (True | paid, 60.0),
        (paid.ifelse(True, False), 10.0),
        (paid.coalesce(False), 10.0),
        (paid.nullif(False), 10.0),
        (status.cases(("paid", True), else_=False), 10.0),
        (paid.ifelse(False, True), 50.0),
        (paid.coalesce(True), 40.0),
        (paid.ifelse(paid, False), 10.0),
        (status.cases(("paid", paid), else_=False), 10.0),
        (paid.ifelse("yes", "no") == "yes", 10.0),
    ):
        result = session.observe({"total": revenue}, where=condition).compute().to_pandas()
        assert result["total"].tolist() == [expected]
    result = (
        session.observe(
            {
                "filled": amount.coalesce(4).mean(),
                "non_ten": amount.nullif(10).mean(),
                "selected": paid.ifelse(amount, 0).mean(),
                "classified": status.cases(("paid", 1), else_=0).mean(),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result["filled"].tolist() == [16.0]
    assert result["non_ten"].tolist() == [25.0]
    assert result["selected"].tolist() == pytest.approx([10 / 3])
    assert result["classified"].tolist() == [0.5]
    metric_condition = revenue > 0
    for metric_predicate, expected in (
        (metric_condition & (revenue < 100), 60.0),
        (metric_condition | (revenue < 0), 60.0),
        (metric_condition & True, 60.0),
        (True & metric_condition, 60.0),
        (metric_condition | False, 60.0),
        (False | metric_condition, 60.0),
        (metric_condition & False, 0.0),
        (False & metric_condition, 0.0),
        (metric_condition | True, 60.0),
        (True | metric_condition, 60.0),
        (metric_condition.ifelse(True, False), 60.0),
        (metric_condition.coalesce(False), 60.0),
        (metric_condition.cases((True, True), else_=False), 60.0),
    ):
        result = (
            session.observe({"total": metric_predicate.ifelse(revenue, 0)}).compute().to_pandas()
        )
        assert result["total"].tolist() == [expected]
        with pytest.raises(AnalysisError):
            session.observe(revenue, where=metric_predicate)  # type: ignore[arg-type]  # Metric predicate is invalid here.
    with pytest.raises(AnalysisError):
        session.observe(revenue, where=paid.ifelse(1, 0))  # type: ignore[arg-type]  # Numeric predicate.
    with pytest.raises(SemanticExpressionError):
        session.observe(revenue, where=paid.ifelse(paid, 1))  # type: ignore[arg-type]  # Incompatible branches.
    with pytest.raises(SemanticExpressionError):
        session.observe({"total": paid.ifelse(amount, revenue).mean()})  # type: ignore[call-overload]  # Mixed stages.


def test_row_subtraction_builds_one_fingerprint_on_one_root(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    margin = amount - cost
    again = amount - cost
    assert type(margin) is Deferred
    assert isinstance(_node_facts(margin).node, RowExpression)
    assert _row_node(margin).inputs == (_node_facts(amount).node, _node_facts(cost).node)
    assert computation_fingerprint(_node_facts(margin).node) == computation_fingerprint(
        _node_facts(again).node
    )
    assert _row_facts(margin).root == _row_facts(amount).root
    assert (_unit_facts(margin).unit, _measure_facts(margin).permission) == ("CNY", SumAll())
    assert _row_node(margin).recipe == resolver_of(var("slot_0") - var("slot_1"))


def test_row_division_uses_operator_and_rejects_retired_method(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    result = _row_facts(amount / cost)
    assert isinstance(result, RowFacts)
    assert isinstance(result.node, RowExpression)
    assert (result.unit, result.permission) == ("1", SumNone())
    for expression in (
        amount.divide(cost),
        amount.divide(cost, zero_denominator="null"),
        amount.divide(cost, zero_denominator="error"),
    ):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(expression)
        assert caught.value.expected and caught.value.received and caught.value.repair


def test_sum_permission_is_kept_narrowed_or_lost(catalog: SemanticCatalog) -> None:
    amount, balance = (_measure(catalog, "amount"), _measure(catalog, "balance"))
    quantity, unit_price = (_measure(catalog, "quantity"), _measure(catalog, "unit_price"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    scaled = amount * 2
    assert (_unit_facts(scaled).unit, _measure_facts(scaled).permission) == ("CNY", SumAll())
    assert _measure_facts(2 * amount).permission == SumAll()
    assert isinstance(_node_facts(2 * amount).node, RowExpression)
    assert _measure_facts(amount + 10).permission == SumAll()
    assert (
        isinstance(_node_facts(-amount).node, RowExpression)
        and _measure_facts(-amount).permission == SumAll()
    )
    assert _measure_facts(amount - balance).permission == SumAllExcept((_reference(paid_at),))
    assert _measure_facts(quantity + quantity).permission == _measure_facts(quantity).permission
    product = quantity * unit_price
    assert (_measure_facts(product).permission, _unit_facts(product).unit) == (SumNone(), None)
    inverse = 1 / amount
    assert _row_node(inverse).inputs == (_node_facts(amount).node,)
    assert (_unit_facts(inverse).unit, _measure_facts(inverse).permission) == ("1/CNY", SumNone())


def test_incompatible_units_and_roots_are_rejected(catalog: SemanticCatalog) -> None:
    amount, amount_usd = (_measure(catalog, "amount"), _measure(catalog, "amount_usd"))
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    with pytest.raises(SemanticExpressionError) as units:
        _consume_value(amount + amount_usd)
    assert units.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert units.value.received == "CNY + USD"
    assert units.value.repair is not None
    unknown = amount + _measure(catalog, "quantity")
    assert _unit_facts(unknown).unit is None
    with pytest.raises(SemanticExpressionError) as roots:
        _consume_value(amount - credit)
    assert roots.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    assert roots.value.received is not None
    assert "sales.orders" in roots.value.received and "sales.customers" in roots.value.received


def test_invalid_row_operands_fail_structurally(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    with pytest.raises(SemanticExpressionError) as boolean:
        _consume_value(amount + True)
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nan:
        _consume_value(amount * float("nan"))
    assert nan.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert nan.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as dimension:
        _consume_value(amount + status)
    assert dimension.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as text:
        _consume_value(amount + "1")
    assert text.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as policy:
        _consume_value(amount.divide(cost, zero_denominator="skip"))
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_field_and_row_predicates_are_closed_values(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    paid = status == "paid"
    positive = amount - cost > 0
    missing = status.isnull()
    combined = paid & positive | ~missing
    assert all(isinstance(value, Deferred) for value in (paid, positive, missing, combined))
    for predicate in (paid, positive, missing, combined):
        assert isinstance(consume_predicate(predicate).node, PredicateExpression)
        assert consume_predicate(predicate).roots == (orders._exact_ref,)
    fields = consume_predicate(combined).node.inputs[0]
    assert isinstance(fields, RowExpression)
    assert isinstance(fields.inputs[0], RowField) and isinstance(fields.inputs[-1], RowField)
    assert fields.inputs[0].ref == consume_field(status).reference
    assert fields.inputs[-1].ref == consume_field(status).reference
    assert computation_fingerprint(consume_predicate(combined).node) != computation_fingerprint(
        consume_predicate(paid & positive & ~missing).node
    )


def test_invalid_predicates_and_truth_conversion_fail(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    margin = amount - cost
    paid = status == "paid"
    with pytest.raises(TypeError):
        bool(paid)
    with pytest.raises(TypeError):
        _ = paid and paid
    with pytest.raises(SemanticExpressionError) as container:
        _ = _consume_value(status == ["paid"])
    assert container.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as boolean:
        _ = _consume_value(margin > True)
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as infinite:
        _ = _consume_value(margin > float("inf"))
    assert infinite.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as operand:
        _consume_value(paid & "status")
    assert operand.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def test_margin_sum_has_no_time_role_unless_supplied(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    implicit = _facts((amount - cost).sum())
    explicit = _facts((amount - cost).sum(time=paid_at))
    assert isinstance(implicit.node, SourceAggregate) and isinstance(explicit.node, SourceAggregate)
    assert implicit.node.reducer == "sum" and implicit.node.nulls == "ignore"
    assert implicit.node.time is None
    assert explicit.node.time == TimeBinding(_reference(paid_at))
    assert computation_fingerprint(implicit.node) != computation_fingerprint(explicit.node)
    assert (implicit.roots, implicit.unit) == ((_row_facts(amount).root,), "CNY")
    strict = (amount - cost).sum(nulls="error")
    assert computation_fingerprint(_node_facts(strict).node) != computation_fingerprint(
        implicit.node
    )
    independent = _facts((amount - cost).sum(time=None))
    assert isinstance(independent.node, SourceAggregate)
    assert independent.node.time is None
    assert independent.node.independent_origin == "explicit_none"


def test_unauthorized_product_sum_is_rejected_but_mean_is_allowed(catalog: SemanticCatalog) -> None:
    product = _measure(catalog, "quantity") * _measure(catalog, "unit_price")
    with pytest.raises(SemanticExpressionError) as missing:
        _consume_value(product.sum())
    assert missing.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value
    assert missing.value.repair is not None
    mean = product.mean()
    assert (
        isinstance(_node_facts(mean).node, SourceAggregate)
        and _aggregate_node(mean).reducer == "mean"
    )
    assert _unit_facts(mean).unit is None
    median = _node_facts(_measure(catalog, "unit_price").median()).node
    assert isinstance(median, SourceAggregate) and median.reducer == "median"


def test_counts_have_unit_one_and_entity_count_reads_whole_rows(catalog: SemanticCatalog) -> None:
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    customers = _resolved_entity(catalog, ms.ref.entity("sales.customers"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    distinct = status.count_distinct()
    assert isinstance(_node_facts(distinct).node, SourceAggregate)
    assert (_aggregate_node(distinct).reducer, _unit_facts(distinct).unit) == (
        "count_distinct",
        "1",
    )
    assert _unit_facts(_measure(catalog, "amount").count()).unit == "1"
    order_count = orders.count(time=paid_at)
    assert _node_facts(order_count).node == EntityCount(
        _reference(orders), TimeBinding(_reference(paid_at))
    )
    assert (_metric_facts(order_count).roots, _unit_facts(order_count).unit) == (
        (_reference(orders),),
        "1",
    )
    assert _node_facts(customers.count()).node == EntityCount(_reference(customers), None)


def test_quantile_and_policy_inputs_are_validated(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    amount_facts = _row_facts(amount)
    assert isinstance(amount_facts, RowFacts)
    assert _node_facts(amount.quantile(0.95, time=paid_at)).node == SourceAggregate(
        amount_facts.node,
        "quantile",
        TimeBinding(_reference(paid_at)),
        q=0.95,
        contribution_root=amount_facts.root,
    )
    for bad in (1.5, -0.1, float("nan")):
        with pytest.raises(SemanticExpressionError) as out_of_range:
            _consume_value(amount.quantile(bad))
        assert out_of_range.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert out_of_range.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as boolean_q:
        _consume_value(amount.quantile(True))
    assert boolean_q.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nulls:
        _consume_value(amount.sum(nulls="skip"))
    assert nulls.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as raw_time:
        _consume_value(amount.sum(time=ms.ref.time_dimension("sales.orders.paid_at")))
    assert raw_time.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_weighted_mean_pairs_one_root_and_rejects_foreign_time(catalog: SemanticCatalog) -> None:
    amount, quantity = (_measure(catalog, "amount"), _measure(catalog, "quantity"))
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    weighted = amount.weighted_mean(weight=quantity)
    assert isinstance(_node_facts(weighted).node, SourceWeightedMean)
    assert (_metric_facts(weighted).roots, _unit_facts(weighted).unit) == (
        (_row_facts(amount).root,),
        "CNY",
    )
    for policy in ("null", "error"):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(amount.weighted_mean(weight=quantity, zero_denominator=policy))
        assert caught.value.expected and caught.value.received and caught.value.repair
    with pytest.raises(SemanticExpressionError) as roots:
        _consume_value(amount.weighted_mean(weight=credit))
    assert roots.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    with pytest.raises(SemanticExpressionError) as stage:
        _consume_value(amount.weighted_mean(weight=2))
    assert stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as foreign:
        _consume_value(amount.sum(time=created_at))
    assert foreign.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert foreign.value.received == "entity:sales.customers"


def test_expression_construction_reads_no_business_source(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("expression construction opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    metrics = (
        (amount - cost).sum(),
        amount.quantile(0.5),
        amount.weighted_mean(weight=_measure(catalog, "quantity")),
        status.count_distinct(),
        orders.count(),
    )
    assert all(isinstance(metric, Deferred) for metric in metrics)


def test_aov_divides_completed_metrics_across_roots(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    customers = _resolved_entity(catalog, ms.ref.entity("sales.customers"))
    revenue = amount.sum()
    order_count = orders.count()
    aov = revenue / order_count
    assert type(aov) is Deferred
    assert _metric_node(aov).operands == (
        _node_facts(revenue).node,
        _node_facts(order_count).node,
    )
    assert (_metric_facts(aov).roots, _unit_facts(aov).unit) == ((_reference(orders),), "CNY")
    per_customer = revenue / customers.count()
    assert _metric_facts(per_customer).roots == (_reference(orders), _reference(customers))
    assert isinstance(aov, Deferred)


def test_repeated_metric_keeps_two_ordered_uses(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    doubled = revenue + revenue
    edges = children(_node_facts(doubled).node)
    assert isinstance(_node_facts(doubled).node, MetricExpression)
    assert [(edge.role, edge.index) for edge in edges] == [("operand", 0), ("operand", 1)]
    assert edges[0].child == edges[1].child == _node_facts(revenue).node
    assert (_metric_facts(doubled).roots, _unit_facts(doubled).unit) == (
        _metric_facts(revenue).roots,
        "CNY",
    )


def test_metric_division_uses_operator_and_rejects_retired_method(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    cost = _measure(catalog, "cost").sum()
    assert isinstance(_node_facts(revenue / cost).node, MetricExpression)
    assert _unit_facts(revenue / cost).unit == "1"
    for expression in (
        revenue.divide(cost),
        revenue.divide(cost, zero_denominator="null"),
        revenue.divide(cost, zero_denominator="error"),
    ):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(expression)
        assert caught.value.expected and caught.value.received and caught.value.repair


def test_metric_constants_and_negation_follow_unit_rules(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    offset = 100 - revenue
    assert _metric_node(offset).recipe == resolver_of(100 - var("slot_0"))
    assert _metric_node(offset).operands == (_node_facts(revenue).node,)
    assert _unit_facts(offset).unit == "CNY"
    assert (
        _unit_facts(revenue + 10).unit
        == _unit_facts(revenue * 0.9).unit
        == _unit_facts(2 * revenue).unit
        == "CNY"
    )
    inverse = 1 / revenue
    assert _metric_node(inverse).operands == (_node_facts(revenue).node,)
    assert _unit_facts(inverse).unit == "1/CNY"
    negated = -revenue
    assert _metric_node(negated).recipe == resolver_of(-var("slot_0"))
    assert (_metric_facts(negated).roots, _unit_facts(negated).unit) == (
        _metric_facts(revenue).roots,
        "CNY",
    )
    unknown = revenue + _measure(catalog, "quantity").sum()
    assert _unit_facts(unknown).unit is None


def test_metric_unit_stage_and_value_failures(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    revenue = amount.sum()
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    with pytest.raises(SemanticExpressionError) as units:
        _consume_value(revenue + _measure(catalog, "amount_usd").sum())
    assert units.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert units.value.received == "CNY + USD"
    with pytest.raises(SemanticExpressionError) as measure_stage:
        _consume_value(revenue + amount)
    assert measure_stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as dimension_stage:
        _consume_value(revenue * status)
    assert dimension_stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as row_side:
        _consume_value(amount + revenue)
    assert row_side.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert row_side.value.repair is not None
    with pytest.raises(SemanticExpressionError) as boolean:
        _consume_value(revenue + True)
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nan:
        _consume_value(revenue * float("nan"))
    assert nan.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as text:
        _consume_value(revenue - "1")
    assert text.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as policy:
        _consume_value(revenue.divide(revenue, zero_denominator="skip"))
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_paid_margin_and_aov_filter_each_source_reduction(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    paid = status == "paid"
    paid_margin = (amount - cost).sum().where(paid)
    assert isinstance(_node_facts(paid_margin).node, ContributionFilter)
    assert _filtered_node(paid_margin).predicate == consume_predicate(paid).node
    assert (_metric_facts(paid_margin).roots, _unit_facts(paid_margin).unit) == (
        (_reference(orders),),
        "CNY",
    )
    revenue = amount.sum().where(paid)
    order_count = orders.count().where(paid)
    aov = revenue / order_count
    assert _metric_node(aov).operands == (
        _node_facts(revenue).node,
        _node_facts(order_count).node,
    )
    again = amount.sum().where(status == "paid") / orders.count().where(status == "paid")
    assert computation_fingerprint(_node_facts(aov).node) == computation_fingerprint(
        _node_facts(again).node
    )
    positive = amount.sum().where(amount - cost > 0)
    assert isinstance(_node_facts(positive).node, ContributionFilter)
    assert isinstance(_filtered_node(positive).predicate, PredicateExpression)
    nested = revenue.where(status.isnull())
    assert isinstance(_node_facts(nested).node, ContributionFilter)
    assert _filtered_node(nested).operand == _node_facts(revenue).node
    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity")).where(paid)
    assert isinstance(_node_facts(weighted).node, ContributionFilter)


def test_where_rejects_composite_cross_root_and_non_predicates(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    aov = amount.sum() / orders.count()
    with pytest.raises(SemanticExpressionError) as composite:
        _consume_value(aov.where(status == "paid"))
    assert composite.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    assert composite.value.repair is not None
    with pytest.raises(SemanticExpressionError) as sampled:
        _consume_value(amount.sum().per(status).mean().where(status == "paid"))
    assert sampled.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    with pytest.raises(SemanticExpressionError) as foreign:
        _consume_value(amount.sum().where(created_at.isnull()))
    assert foreign.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    assert foreign.value.received == "entity:sales.customers"
    with pytest.raises(SemanticExpressionError) as raw:
        _consume_value(amount.sum().where(True))
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def test_per_field_samples_reduce_through_per_nodes(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = _resolved_field(catalog, ms.ref.dimension("sales.orders.customer_id"))
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    revenue = amount.sum()
    samples = revenue.per(customer_id)
    assert type(samples) is Deferred
    axis = AxisBinding(
        entity=_row_facts(customer_id).root, field=_reference(customer_id), path=(), role="field"
    )
    assert _sample_facts(samples).axes == (axis,)
    assert _facts(revenue.per((customer_id,))) == _facts(samples)
    observed_mean = samples.mean()
    assert _node_facts(observed_mean).node == PerReduce(
        _metric_facts(revenue).node, (axis,), "mean"
    )
    assert (_metric_facts(observed_mean).roots, _unit_facts(observed_mean).unit) == (
        _metric_facts(revenue).roots,
        "CNY",
    )
    assert isinstance(samples, Deferred)
    p95 = revenue.per(paid_at).quantile(0.95)
    assert isinstance(_node_facts(p95).node, PerReduce) and _per_node(p95).q == 0.95
    pair = revenue.per((customer_id, status))
    pair_axes = _sample_facts(pair).axes
    assert isinstance(pair_axes, tuple)
    assert len(pair_axes) == 2
    assert _unit_facts(pair.count()).unit == "1" and _unit_facts(pair.count_distinct()).unit == "1"
    for reduced in (pair.sum(), pair.min(), pair.max(), pair.median()):
        assert (
            isinstance(_node_facts(reduced).node, PerReduce) and _unit_facts(reduced).unit == "CNY"
        )
    strict = samples.sum(nulls="error")
    assert isinstance(_node_facts(strict).node, PerReduce) and _per_node(strict).nulls == "error"
    order_count = _resolved_entity(catalog, ms.ref.entity("sales.orders")).count()
    weighted = (revenue / order_count).per(customer_id).weighted_mean(weight=order_count)
    assert isinstance(_node_facts(weighted).node, PerWeightedMean)
    assert _unit_facts(weighted).unit == "CNY"


def test_per_rejects_invalid_axes_weights_and_policies(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = _resolved_field(catalog, ms.ref.dimension("sales.orders.customer_id"))
    revenue = amount.sum()
    samples = revenue.per(customer_id)
    with pytest.raises(SemanticExpressionError) as empty:
        _consume_value(revenue.per(()))
    assert empty.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as measure_axis:
        _consume_value(revenue.per(amount))
    assert measure_axis.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as raw_axis:
        _consume_value(revenue.per(ms.ref.dimension("sales.orders.customer_id")))
    assert raw_axis.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as duplicate:
        _consume_value(revenue.per((customer_id, customer_id)))
    assert duplicate.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as row_weight:
        _consume_value(samples.weighted_mean(weight=amount))
    assert row_weight.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as policy:
        _consume_value(samples.mean(nulls="skip"))
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as zero:
        _consume_value(samples.weighted_mean(weight=revenue, zero_denominator="skip"))
    assert zero.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as quantile:
        _consume_value(samples.quantile(2.0))
    assert quantile.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as stage:
        _consume_value(samples + revenue)
    assert stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def _relationship(catalog: SemanticCatalog, name: str) -> Relationship:
    return _resolved_relationship(catalog, ms.ref.relationship(f"sales.{name}"))


def test_via_binds_one_and_two_hop_time_paths_with_exact_refs(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    orders = _resolved_entity(catalog, ms.ref.entity("sales.orders"))
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = _resolved_field(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    buyer_created = created_at.via((buyer,))
    assert type(buyer_created) is Deferred
    field_read = consume_field(buyer_created).field_read
    assert field_read is not None and field_read.path == (buyer._parameter.ref,)
    assert _reference(buyer_created) == _reference(created_at)
    assert _row_facts(buyer_created).root == buyer._parameter.from_entity
    assert isinstance(buyer_created, Deferred)
    one_hop = amount.sum(time=buyer_created)
    assert isinstance(_node_facts(one_hop).node, SourceAggregate)
    assert _aggregate_node(one_hop).time == TimeBinding(
        _reference(created_at), (_reference(buyer),)
    )
    assert _reference(buyer).version.startswith("sha256:")
    two_hop = orders.count(time=opened_at.via((buyer, region)))
    assert _node_facts(two_hop).node == EntityCount(
        _reference(orders),
        TimeBinding(_reference(opened_at), (_reference(buyer), _reference(region))),
    )
    native = amount.sum()
    assert computation_fingerprint(_node_facts(one_hop).node) != computation_fingerprint(
        _node_facts(native).node
    )
    again = amount.sum(time=created_at.via((buyer,)))
    assert computation_fingerprint(_node_facts(again).node) == computation_fingerprint(
        _node_facts(one_hop).node
    )
    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity"), time=buyer_created)
    assert isinstance(_node_facts(weighted).node, SourceWeightedMean)
    assert _weighted_node(weighted).time == TimeBinding(
        _reference(created_at), (_reference(buyer),)
    )
    filtered = amount.sum(time=buyer_created).where(
        _resolved_field(catalog, ms.ref.dimension("sales.orders.status")) == "paid"
    )
    assert isinstance(_node_facts(filtered).node, ContributionFilter)


def test_via_rejects_malformed_disconnected_and_fanout_paths(catalog: SemanticCatalog) -> None:
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    fanout = _relationship(catalog, "customers_to_orders")
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = _resolved_field(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    cases: list[tuple[object, str]] = [
        ((), "()"),
        ([buyer], "list"),
        (buyer, "Relationship"),
        ((ms.ref.relationship("sales.orders_to_customers"),), "Ref"),
    ]
    for path, label in cases:
        with pytest.raises(SemanticExpressionError) as malformed:
            _consume_value(created_at.via(path))
        assert malformed.value.kind == (
            ErrorKind.INVALID_EXPRESSION_VALUE if label == "Ref" else ErrorKind.INVALID_TIME_PATH
        ), label
        assert malformed.value.repair is not None
    with pytest.raises(SemanticExpressionError) as many:
        _consume_value(paid_at.via((fanout,)))
    assert many.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert many.value.expected == "one_to_one or many_to_one relationships"
    with pytest.raises(SemanticExpressionError) as disconnected:
        _consume_value(opened_at.via((region, region)))
    assert disconnected.value.kind == ErrorKind.INVALID_TIME_PATH.value
    with pytest.raises(SemanticExpressionError) as wrong_end:
        _consume_value(opened_at.via((buyer,)))
    assert wrong_end.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert wrong_end.value.received == "disconnected or empty path"
    with pytest.raises(SemanticExpressionError) as twice:
        _consume_value(created_at.via((buyer,)).via((buyer,)))
    assert twice.value.kind == ErrorKind.INVALID_TIME_PATH.value


def test_via_time_requires_root_start_and_keeps_native_reductions(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customers = _resolved_entity(catalog, ms.ref.entity("sales.customers"))
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = _resolved_field(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    buyer_created = created_at.via((buyer,))
    with pytest.raises(SemanticExpressionError) as start:
        _consume_value(credit.sum(time=opened_at.via((buyer, region))))
    assert start.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert start.value.received == "entity:sales.orders"
    assert _metric_facts(credit.sum(time=opened_at.via((region,)))).roots == (
        _reference(customers),
    )
    with pytest.raises(SemanticExpressionError) as entity_start:
        _consume_value(customers.count(time=buyer_created))
    assert entity_start.value.kind == ErrorKind.INVALID_TIME_PATH.value
    for predicate in (buyer_created == "2026-01-01", buyer_created.isnull()):
        field_node = _row_node(predicate).inputs[0]
        assert isinstance(field_node, RowField) and field_node.read is not None
        assert field_node.read.path == (_reference(buyer),)
    misuses: tuple[Callable[[], object], ...] = (
        lambda: buyer_created.count(),
        lambda: amount.sum().per(buyer_created),
    )
    for misuse in misuses:
        with pytest.raises(SemanticExpressionError) as role:
            _consume_value(misuse())
        assert role.value.kind == ErrorKind.INVALID_TIME_PATH.value
        assert role.value.repair is not None
        assert role.value.expected and role.value.received
        assert role.value.repair.action


def test_via_construction_reads_no_business_source(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("via construction opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    buyer = _relationship(catalog, "orders_to_customers")
    created_at = _resolved_field(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    metric = _measure(catalog, "amount").sum(time=created_at.via((buyer,)))
    assert isinstance(metric, Deferred)


def test_measure_versions_follow_additivity_axis_definitions(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    first = SemanticCatalog(
        semantic_project_factory({"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _OBJECTS_PY})
    )
    changed_axis = _OBJECTS_PY.replace(
        'name="status", entity=orders, column="status"',
        'name="status", entity=orders, column="order_status"',
    ).replace(
        'name="paid_at",\n    entity=orders,\n    column="paid_at"',
        'name="paid_at",\n    entity=orders,\n    column="paid_ts"',
    )
    second_root = tmp_path / "changed_axis"
    second_root.mkdir()
    second = SemanticCatalog(
        semantic_project_factory(
            {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": changed_axis},
            workspace_dir=second_root,
        )
    )
    for name in ("quantity", "balance"):
        before, after = (_measure(first, name), _measure(second, name))
        assert _measure_facts(before).permission != _measure_facts(after).permission
        assert _reference(before) != _reference(after), name
    amount_ref = ms.ref.measure("sales.orders.amount")
    assert _reference(_physical(first, amount_ref)) == _reference(_physical(second, amount_ref))


def test_unary_positive_keeps_the_metric_unchanged(catalog: SemanticCatalog) -> None:
    revenue = (
        _measure(catalog, "amount")
        .sum(nulls="error")
        .where(_resolved_field(catalog, ms.ref.dimension("sales.orders.status")) == "paid")
    )
    with pytest.raises(TypeError):
        _ = +revenue
    assert consume_metric(revenue).unit == "CNY"


def test_oversized_integer_quantile_fails_structurally(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = _resolved_field(catalog, ms.ref.dimension("sales.orders.customer_id"))
    for huge in (10**400, 10**5000):
        with pytest.raises(SemanticExpressionError) as row:
            _consume_value(amount.quantile(huge))
        with pytest.raises(SemanticExpressionError) as samples:
            _consume_value(amount.sum().per(customer_id).quantile(huge))
        for oversized in (row, samples):
            assert oversized.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
            assert oversized.value.expected == "a finite float q with 0 <= q <= 1"
            assert oversized.value.received is not None
            assert oversized.value.received.startswith("int ")
            assert len(oversized.value.received) < 100
            assert oversized.value.repair is not None


def test_updated_root_identity_does_not_reject_stored_expression_inputs(
    catalog: SemanticCatalog,
) -> None:
    old_amount = _measure(catalog, "amount")
    old_cost = _measure(catalog, "cost")
    old_graph = _node_facts((old_amount - old_cost).sum()).node
    old_fingerprint = computation_fingerprint(old_graph)
    with loader_context(
        LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    ):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace('md.table("orders")', 'md.table("new_orders")'), namespace)
        current = captured_catalog()
    cost = _resolved_field(current, ms.ref.measure("sales.orders.cost"))
    status = _resolved_field(current, ms.ref.dimension("sales.orders.status"))
    paid_at = _resolved_field(current, ms.ref.time_dimension("sales.orders.paid_at"))
    assert (
        _row_facts(old_amount).root.ref == _row_facts(cost).root.ref
        and _row_facts(old_amount).root != _row_facts(cost).root
    )
    combined = old_amount - cost
    assert _row_node(combined).inputs == (
        _node_facts(old_amount).node,
        _node_facts(cost).node,
    )
    assert _unit_facts(combined).unit == _unit_facts(old_amount).unit
    assert _measure_facts(combined).permission == _measure_facts(old_amount).permission
    reduced = combined.sum(time=paid_at)
    assert isinstance(_node_facts(reduced).node, SourceAggregate)
    assert _aggregate_node(reduced).time == TimeBinding(_reference(paid_at))
    filtered = reduced.where(status == "paid")
    assert isinstance(_node_facts(filtered).node, ContributionFilter)
    assert _filtered_node(filtered).predicate == consume_predicate(status == "paid").node
    weighted = old_amount.weighted_mean(weight=cost, time=paid_at)
    assert isinstance(_node_facts(weighted).node, SourceWeightedMean)
    assert _weighted_node(weighted).value == _node_facts(old_amount).node
    assert _weighted_node(weighted).weight == _node_facts(cost).node
    assert computation_fingerprint(_node_facts(reduced).node) != old_fingerprint
    assert computation_fingerprint(old_graph) == old_fingerprint
    assert (
        _reference(resolve_private(catalog, ref_factory.entity("sales.orders")))
        == _row_facts(old_amount).root
    )


def test_permission_intersection_and_time_paths_compare_semantic_references(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    status = _resolved_field(catalog, ms.ref.dimension("sales.orders.status"))
    old_axis = _reference(status)
    new_axis = ExactRef(old_axis.ref, "updated-definition")
    old_row = semantic_input(
        dataclasses.replace(consume_field(amount), permission=SumAxes((old_axis,)))
    )
    new_row = semantic_input(
        dataclasses.replace(consume_field(amount), permission=SumAxes((new_axis,)))
    )
    assert _measure_facts(old_row + new_row).permission == SumAxes((old_axis,))
    excluded = semantic_input(
        dataclasses.replace(consume_field(new_row), permission=SumAllExcept((new_axis,)))
    )
    assert isinstance(_measure_facts(old_row + excluded).permission, SumNone)
    paid_at = _resolved_field(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    changed_root = ExactRef(_row_facts(paid_at).root.ref, "updated-entity-definition")
    path = Relationship(
        RelationshipParameter(
            ExactRef(ref_factory.relationship("sales.self_role"), "relationship-definition"),
            changed_root,
            changed_root,
            "many_to_one",
        )
    )
    routed = paid_at.via((path,))
    result = amount.sum(time=routed)
    assert isinstance(_node_facts(result).node, SourceAggregate)
    assert _aggregate_node(result).time == TimeBinding(_reference(paid_at), (_reference(path),))
