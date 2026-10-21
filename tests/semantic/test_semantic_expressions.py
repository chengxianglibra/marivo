"""Catalog resolution into private semantic expression values (R1.2)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn, TypeAlias

import ibis
import ibis.expr.types as ibis_ir
import pytest
from ibis.common.deferred import Deferred, var

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.deferred import (
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
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import children, computation_fingerprint
from marivo._definition.types import AxisBinding, ExactRef, SumAll, SumAllExcept, SumAxes, SumNone
from marivo.refs import MeasureKind
from marivo.semantic._deferred_semantics import (
    _consume,
    _inspect,
    consume_field,
    consume_metric,
    consume_predicate,
    consume_row,
)
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticExpressionError
from marivo.semantic.expressions import (
    Entity,
    FinalizedDeclarations,
    Relationship,
    declaration_state,
    resolve,
)
from marivo.semantic.loader import LoaderContext, loader_context
from marivo.semantic.reader import SemanticProject

_DOMAIN_PY = (
    'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n'
)
_OBJECTS_PY = 'import marivo.datasource as md\nimport marivo.semantic as ms\n\norders = ms.entity(\n    name="orders",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("orders"),\n    primary_key=["order_id"],\n)\ncustomers = ms.entity(\n    name="customers",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("customers"),\n    primary_key=["id"],\n)\n\nstatus = ms.dimension_column(name="status", entity=orders, column="status")\ncustomer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")\nid = ms.dimension_column(name="id", entity=customers, column="id")\npaid_at = ms.time_dimension_column(\n    name="paid_at",\n    entity=orders,\n    column="paid_at",\n    granularity="day",\n    parse=ms.timestamp(timezone="UTC"),\n    is_default=True,\n)\ncreated_at = ms.time_dimension_column(\n    name="created_at",\n    entity=customers,\n    column="created_at",\n    granularity="day",\n    parse=ms.timestamp(timezone="UTC"),\n)\n\namount = ms.measure_column(\n    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY"\n)\nunit_price = ms.measure_column(\n    name="unit_price", entity=orders, column="unit_price", additivity=ms.non_additive(), unit="CNY"\n)\nquantity = ms.measure_column(\n    name="quantity", entity=orders, column="quantity", additivity=ms.additive(over=(status,))\n)\ncost = ms.measure_column(\n    name="cost", entity=orders, column="cost", additivity=ms.additive_all(), unit="CNY"\n)\namount_usd = ms.measure_column(\n    name="amount_usd", entity=orders, column="amount_usd", additivity=ms.additive_all(), unit="USD"\n)\ncredit = ms.measure_column(\n    name="credit", entity=customers, column="credit", additivity=ms.additive_all(), unit="CNY"\n)\nbalance = ms.measure_column(\n    name="balance",\n    entity=orders,\n    column="balance",\n    additivity=ms.additive_all(except_=(paid_at,)),\n    unit="CNY",\n)\n\nms.relationship(\n    name="orders_to_customers",\n    from_entity=orders,\n    to_entity=customers,\n    keys=[ms.join_on(customer_id, id)],\n)\n\nregions = ms.entity(\n    name="regions",\n    datasource=ms.ref.datasource("warehouse"),\n    source=md.table("regions"),\n    primary_key=["id"],\n)\nregion_id = ms.dimension_column(name="region_id", entity=customers, column="region_id")\nregion_key = ms.dimension_column(name="id", entity=regions, column="id")\nopened_at = ms.time_dimension_column(\n    name="opened_at",\n    entity=regions,\n    column="opened_at",\n    granularity="day",\n    parse=ms.timestamp(timezone="UTC"),\n)\nms.relationship(\n    name="customers_to_regions",\n    from_entity=customers,\n    to_entity=regions,\n    keys=[ms.join_on(region_id, region_key)],\n)\nms.relationship(\n    name="customers_to_orders",\n    from_entity=customers,\n    to_entity=orders,\n    keys=[ms.join_on(id, customer_id)],\n)\n'
ProjectFactory: TypeAlias = Callable[..., SemanticProject]


def _facts(value):
    return _consume(_inspect(value).recipe) if isinstance(value, Deferred) else value


def _reference(value):
    return consume_field(value).reference if isinstance(value, Deferred) else value.ref


def _consume_value(value):
    return _facts(value)


def _physical(
    scope: SemanticCatalog | FinalizedDeclarations, target: ms.Ref[MeasureKind]
) -> Deferred:
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
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    assert type(amount) is Deferred
    assert type(status) is Deferred
    assert type(paid_at) is Deferred
    assert type(orders) is Entity
    assert _reference(_reference(amount)) == ms.ref.measure("sales.orders.amount")
    assert _reference(amount).version.startswith("sha256:")
    assert _facts(amount).root == _reference(orders) == _facts(status).root == _facts(paid_at).root
    assert _facts(amount).unit == "CNY"
    assert orders.primary_key == ("order_id",)
    assert _facts(orders).default_time == _reference(paid_at)
    again = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    assert (
        _reference(again),
        _facts(again).root,
        _facts(again).unit,
        _facts(again).permission,
    ) == (_reference(amount), _facts(amount).root, _facts(amount).unit, _facts(amount).permission)
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
        _reference(resolve(first, status_ref)).version
        == _reference(resolve(second, status_ref)).version
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
    state = declaration_state(
        [ctx], datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    entity_ref = ms.ref.entity("sales.orders")
    measure_ref = ms.ref.measure("sales.orders.balance")
    declared_entity, loaded_entity = (resolve(state, entity_ref), resolve(catalog, entity_ref))
    declared, loaded = (_physical(state, measure_ref), _physical(catalog, measure_ref))
    assert declared_entity == loaded_entity
    assert _reference(declared) == _reference(loaded)
    assert _reference(declared).version.startswith("sha256:")
    assert (
        _facts(declared).root,
        _facts(declared).unit,
        _facts(declared).permission,
        _facts(declared).default_time,
    ) == (
        _facts(loaded).root,
        _facts(loaded).unit,
        _facts(loaded).permission,
        _facts(loaded).default_time,
    )
    assert _facts(declared).permission == SumAllExcept(
        (_reference(resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))),)
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
        finalized = ms.finalize()
    for name, key in (
        ("orders", ms.ref.entity("sales.orders")),
        ("amount", ms.ref.measure("sales.orders.amount")),
        ("paid_at", ms.ref.time_dimension("sales.orders.paid_at")),
        ("status", ms.ref.dimension("sales.orders.status")),
        ("relationship_value", ms.ref.relationship("sales.orders_to_customers")),
    ):
        captured = namespace[name]
        assert isinstance(captured, ms.Ref)
        value = finalized.resolve(captured)
        loaded = resolve(catalog, key)
        assert isinstance(value, (Deferred, Entity, Relationship))
        assert isinstance(loaded, (Deferred, Entity, Relationship))
        assert _reference(value) == _reference(loaded)
    amount = finalized.resolve(ms.ref.measure("sales.orders.amount"))
    paid_at = finalized.resolve(ms.ref.time_dimension("sales.orders.paid_at"))
    assert isinstance(amount, Deferred) and isinstance(paid_at, Deferred)
    assert computation_fingerprint(
        _facts(amount.sum(time=paid_at)).node
    ) == computation_fingerprint(
        _facts(
            _measure(catalog, "amount").sum(
                time=resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
            )
        ).node
    )


@pytest.mark.parametrize("default_first", (False, True))
def test_constructor_snapshots_keep_final_default_time(
    catalog: SemanticCatalog, default_first: bool
) -> None:
    from marivo.semantic.validator import assembly_validate

    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        ms.domain(name="sales", owner="Analytics", default=True)
        orders_ref = ms.entity(
            name="orders",
            datasource=ms.ref.datasource("warehouse"),
            source=md.table("orders"),
            primary_key=["order_id"],
        )
        if default_first:
            paid_at_ref = ms.time_dimension_column(
                name="paid_at",
                entity=orders_ref,
                column="paid_at",
                granularity="day",
                parse=ms.timestamp(timezone="UTC"),
                is_default=True,
            )
        amount_ref = ms.measure_column(
            name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all()
        )
        if not default_first:
            paid_at_ref = ms.time_dimension_column(
                name="paid_at",
                entity=orders_ref,
                column="paid_at",
                granularity="day",
                parse=ms.timestamp(timezone="UTC"),
                is_default=True,
            )
        cost_ref = ms.measure_column(
            name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all()
        )
        state = ms.finalize()
        orders = state.resolve(orders_ref)
        amount = state.resolve(amount_ref)
        paid_at = state.resolve(paid_at_ref)
        cost = state.resolve(cost_ref)
    errors, _ = assembly_validate(state._state.registry, state._state.sidecar)
    assert errors == []
    finalized = _physical(state, ms.ref.measure("sales.orders.amount"))
    assert _reference(amount) == _reference(finalized)
    forward, reverse = (_facts((amount + cost).sum()).node, _facts((cost + amount).sum()).node)
    assert isinstance(forward, SourceAggregate) and isinstance(reverse, SourceAggregate)
    assert forward.time == reverse.time == TimeBinding(_reference(paid_at))
    assert _facts(amount).default_time == _facts(finalized).default_time == _reference(paid_at)
    assert _facts(orders).default_time == _reference(paid_at)


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
        state = ms.finalize()
        amount = state.resolve(amount_ref)
        region = state.resolve(region_ref)

        @ms.measure(additivity=ms.additive(over=(region,)))
        def regional() -> Deferred:
            return amount

    assert _facts(regional).permission == SumAxes((_reference(region),))
    assert _facts(amount).coordinates == (_reference(region),)


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
        state = ms.finalize()
        normalized = state.resolve(normalized_ref)
        region = state.resolve(region_ref)
    errors, _ = assembly_validate(state._state.registry, state._state.sidecar)
    assert errors == []
    assert _reference(region) == _reference(resolve(state, later_ref))
    assert _reference(normalized) == _reference(
        resolve(state, ms.ref.dimension("sales.orders.normalized"))
    )


def test_finalize_outside_capture_teaches_model_scope() -> None:
    with pytest.raises(SemanticError) as caught:
        _consume_value(ms.finalize())
    assert caught.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT
    assert caught.value.expected and caught.value.received and caught.value.repair
    assert caught.value.expected and caught.value.received


@pytest.mark.parametrize(
    ("column", "lookup"), (("old_region", False), ("new_region", False), ("new_region", True))
)
def test_ibis_bindings_use_completed_consumer_definitions(
    catalog: SemanticCatalog, column: str, lookup: bool
) -> None:
    from marivo.semantic._expression_binding import evaluate_expression_body

    datasource_irs = tuple(catalog._state.registry.datasources.values())
    first = LoaderContext(default_domain="sales", datasource_irs=datasource_irs)
    with loader_context(first):
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        region_ref = ms.dimension_column(name="region", entity=orders_ref, column="old_region")
        old_region = ms.finalize().resolve(region_ref)
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
        state = ms.finalize()
    assert (_reference(state.resolve(region_ref)) == _reference(old_region)) is (
        column == "old_region"
    )
    expression = evaluate_expression_body(
        catalog_definition_fingerprint=state._state.definition_fingerprint,
        expression_sidecar=state._state.sidecar,
        owning_ref=normalized,
        body=state._state.sidecar.bodies[normalized],
        entity_refs=(orders_ref,),
        aliases=(ibis.table({column: "string"}, name="orders"),),
    )
    assert f"Uppercase(r0.{column})" in str(expression)


@pytest.mark.parametrize("input_kind", ("entity", "coordinate"))
def test_physical_capture_uses_updated_consumer_definitions(
    catalog: SemanticCatalog, input_kind: str
) -> None:
    old_entity = resolve(catalog, ms.ref.entity("sales.orders"))
    old_status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
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
            additivity=ms.additive(over=(old_status,))
            if input_kind == "coordinate"
            else ms.additive_all(),
        )
        state = ms.finalize()
    probe = state.resolve(probe_ref)
    current_entity = state.resolve(ms.ref.entity("sales.orders"))
    current_status = state.resolve(ms.ref.dimension("sales.orders.status"))
    assert _facts(probe).root == _reference(current_entity) and _facts(probe).root != _reference(
        old_entity
    )
    assert _reference(current_status) != _reference(old_status)
    assert (
        state._state.sidecar.bodies[ms.ref.dimension("sales.orders.status")].source_column
        == "new_status"
    )
    if input_kind == "coordinate":
        assert _facts(probe).permission == SumAxes((_reference(current_status),))


def test_pure_measure_uses_its_row_coordinate_definitions(catalog: SemanticCatalog) -> None:
    old_status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    ctx = LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    with loader_context(ctx):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace('column="status"', 'column="new_status"'), namespace)
        state = ms.finalize()
        amount = state.resolve(ms.ref.measure("sales.orders.amount"))

    @ms.measure(additivity=ms.additive(over=(old_status,)))
    def granted() -> Deferred:
        return amount

    current_status = state.resolve(ms.ref.dimension("sales.orders.status"))
    assert current_status is not None and _reference(current_status) != _reference(old_status)
    assert _facts(granted).permission == SumAxes((_reference(current_status),))
    assert (
        _facts(granted).root == _facts(amount).root and _facts(granted).unit == _facts(amount).unit
    )


@pytest.mark.parametrize("mode", ("same", "updated", "lookup"))
def test_file_loader_resolves_owners_in_its_scope(
    catalog: SemanticCatalog,
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import sys
    from types import ModuleType

    old = resolve(catalog, ms.ref.entity("sales.orders"))
    module = ModuleType("_marivo_frozen_inputs")
    vars(module)["owner"] = old
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = (
        f"import marivo.datasource as md\nimport marivo.semantic as ms\nfrom _marivo_frozen_inputs import owner\norders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({('orders' if mode == 'same' else 'new_orders')!r}), primary_key=['order_id'])\namount = ms.measure_column(name='amount', entity="
        + ("ms.ref.entity('sales.orders')" if mode == "lookup" else "owner")
        + ", column='amount', additivity=ms.additive_all())\n"
    )
    root = tmp_path / "other_scope"
    root.mkdir()
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": source}, workspace_dir=root
    )
    current = SemanticCatalog(project)
    amount = _physical(current, ms.ref.measure("sales.orders.amount"))
    assert (_facts(amount).root == _reference(old)) is (mode == "same")
    assert _facts(amount).root == _reference(resolve(current, ms.ref.entity("sales.orders")))
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
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    @ms.metric()
    def positive() -> Deferred:
        return amount.sum().where(0 < amount)  # noqa: SIM300

    @ms.metric()
    def bounded() -> Deferred:
        return amount.sum().where((0 <= amount) & (100 > amount) & (100 >= amount))  # noqa: SIM300

    @ms.metric()
    def literal_fields() -> Deferred:
        return amount.sum().where(("paid" == status) & ("void" != status))  # noqa: SIM300

    @ms.metric()
    def time_filter() -> Deferred:
        return amount.sum().where("2026-01-01" < paid_at)  # noqa: SIM300

    for declared, ordinary in (
        (positive, amount.sum().where(amount > 0)),
        (bounded, amount.sum().where((amount >= 0) & (amount < 100) & (amount <= 100))),
        (literal_fields, amount.sum().where((status == "paid") & (status != "void"))),
        (time_filter, amount.sum().where(paid_at > "2026-01-01")),
    ):
        assert computation_fingerprint(_facts(declared).node) == computation_fingerprint(
            _facts(ordinary).node
        )


def test_finalization_is_atomic_and_closes_physical_capture(catalog: SemanticCatalog) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        later_ref = ms.ref.dimension("sales.orders.region")

        @ms.dimension(entity=orders_ref)
        def normalized(rows: ibis_ir.Table) -> ibis_ir.Value:
            return ms.bind(later_ref, rows).upper()

        assert isinstance(normalized, ms.Ref)
        with pytest.raises(SemanticExpressionError) as incomplete:
            _consume_value(ms.finalize())
        assert incomplete.value.expected and incomplete.value.received and incomplete.value.repair
        assert "region" in str(incomplete.value)
        assert ctx.finalized is None
        region_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")
        finalized = ms.finalize()
        assert isinstance(finalized, FinalizedDeclarations)
        assert ms.finalize() is finalized
        value = finalized.resolve(region_ref)
        before = len(ctx.pending_definitions)
        with pytest.raises(SemanticExpressionError) as late:
            _consume_value(ms.dimension_column(name="late", entity=orders_ref, column="late"))
        assert late.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
        assert late.value.expected and late.value.received and late.value.repair
        assert len(ctx.pending_definitions) == before
    assert _reference(finalized.resolve(region_ref)) == _reference(value)
    with pytest.raises(AttributeError):
        value.root = _consume_value(_facts(value).root)
    assert "use .show()" in repr(finalized) and "\n" not in repr(finalized)


def test_pure_declaration_teaches_finalization_for_capture_refs(catalog: SemanticCatalog) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        amount_ref = ms.measure_column(
            name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all()
        )
        with pytest.raises(SemanticExpressionError) as caught:

            @ms.measure()
            def rejected() -> Deferred:
                return amount_ref

        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert caught.value.expected and caught.value.received
        amount = ms.finalize().resolve(amount_ref)

    @ms.metric()
    def total() -> Deferred:
        return amount.sum()

    assert _facts(total).node == _facts(amount.sum()).node


def test_finalization_seals_prior_contribution_roots(catalog: SemanticCatalog) -> None:
    ctx = LoaderContext(
        default_domain="sales", datasource_irs=tuple(catalog._state.registry.datasources.values())
    )
    with loader_context(ctx):
        orders_ref = ms.entity(
            name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
        )
        finalized = ms.finalize()
    later = LoaderContext(
        default_domain="sales", datasource_irs=ctx.datasource_irs, prior_contexts=(ctx,)
    )
    with loader_context(later), pytest.raises(SemanticExpressionError) as caught:
        _consume_value(
            ms.time_dimension_column(
                name="paid_at",
                entity=orders_ref,
                column="paid_at",
                granularity="day",
                is_default=True,
            )
        )
    assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    assert later.pending_definitions == []
    assert _facts(finalized.resolve(orders_ref)).default_time is None


def test_finalized_capture_imports_once_and_matches_catalog(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    counter, evidence = (tmp_path / "module-effects.txt", tmp_path / "captured.json")
    physical = 'import marivo.semantic as ms\nimport marivo.datasource as md\nfrom pathlib import Path\nwith Path(COUNTER).open("a") as output:\n    output.write("capture\\n")\norders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))\namount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all())\nlater_ref = ms.ref.dimension("sales.orders.region")\n@ms.dimension(entity=orders_ref)\ndef normalized(rows):\n    return ms.bind(later_ref, rows).upper()\npaid_at_ref = ms.time_dimension_column(name="paid_at", entity=orders_ref, column="paid_at", granularity="day", is_default=True)\ncost_ref = ms.measure_column(name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all())\nregion_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")\n'.replace(
        "COUNTER", repr(str(counter))
    )
    composition = 'import json\nfrom marivo.semantic._deferred_semantics import consume_field, consume_metric\nfrom pathlib import Path\nimport marivo.semantic as ms\nfrom .z_physical import orders_ref, amount_ref, paid_at_ref, cost_ref, normalized\nfinalized = ms.finalize()\norders = finalized.resolve(orders_ref)\namount = finalized.resolve(amount_ref)\ncost = finalized.resolve(cost_ref)\npaid_at = finalized.resolve(paid_at_ref)\nnormalized_value = finalized.resolve(normalized)\n@ms.metric()\ndef revenue():\n    return (amount + cost).sum()\nPath(EVIDENCE).write_text(json.dumps({"version": consume_field(normalized_value).reference.version, "same_time": consume_metric((amount + cost).sum()).node.time == consume_metric((cost + amount).sum()).node.time, "default": orders.default_time.ref.path}))\n'.replace(
        "EVIDENCE", repr(str(evidence))
    )
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
    actual = json.loads(evidence.read_text())
    assert actual["same_time"] is True
    assert actual["default"] == "sales.orders.paid_at"
    assert (
        actual["version"]
        == _reference(resolve(loaded, ms.ref.dimension("sales.orders.normalized"))).version
    )


def test_retired_declaration_syntax_has_specific_reauthoring_errors(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    for factory, kwargs, token in (
        (ms.metric, {"entities": [ms.ref.entity("sales.orders")]}, "zero-argument"),
        (ms.metric, {"fanout_policy": "aggregate_then_join"}, "mv.membership"),
        (ms.metric, {"empty": "null"}, "each actual source/internal sum"),
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
    assert "approximate reducer" in approximate.value.repair.action
    assert "approx_median to approx_median()" in approximate.value.repair.action
    assert "choose a supported exact definition" not in approximate.value.repair.action


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
    assert type(order_margin) is Deferred
    assert (root_declaration(order_margin).name, root_declaration(trial_margin).name) == (
        "order_margin",
        "trial_margin",
    )
    assert root_declaration(order_margin).ai_context is not None
    assert (
        _facts(order_margin).root,
        _facts(order_margin).unit,
        _facts(order_margin).permission,
    ) == (_facts(amount).root, "CNY", SumAll())
    assert computation_fingerprint(_facts(order_margin).node) == computation_fingerprint(
        _facts(ad_hoc).node
    )
    sums = {
        computation_fingerprint(_facts(m.sum()).node)
        for m in (order_margin, trial_margin, margin_alias)
    }
    assert sums == {computation_fingerprint(_facts(ad_hoc.sum()).node)}
    assert root_declaration(order_margin).name == "order_margin"


def test_measure_grant_authorizes_an_otherwise_unsummable_product(catalog: SemanticCatalog) -> None:
    quantity, unit_price = (_measure(catalog, "quantity"), _measure(catalog, "unit_price"))
    amount, cost, balance = (_measure(catalog, name) for name in ("amount", "cost", "balance"))

    @ms.measure()
    def priced() -> Deferred:
        return quantity * unit_price

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def order_amount() -> Deferred:
        return quantity * unit_price

    unsummable: tuple[Deferred, ...] = (quantity * unit_price, priced, unit_price)
    for operand in unsummable:
        with pytest.raises(SemanticExpressionError) as denied:
            _consume_value(operand.sum())
        assert denied.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value
    assert _facts(order_amount.sum()).unit == "CNY"
    assert (_facts(order_amount).unit, _facts(order_amount).permission) == ("CNY", SumAll())
    definition = consume_row(order_amount).node
    assert isinstance(definition, MeasureAssertion)
    assert (_facts(definition).unit, _facts(definition).permission) == ("CNY", SumAll())
    assert definition.authorization.source.ref == ms.ref.measure("sales.orders.order_amount")
    assert computation_fingerprint(definition) != computation_fingerprint(consume_row(priced).node)

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def margin() -> Deferred:
        return amount - cost

    assert computation_fingerprint(consume_row(margin).node) == computation_fingerprint(
        _facts(amount - cost).node
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
            _consume_value(ms.measure()(body))
        assert caught.value.expected and caught.value.received
        assert caught.value.repair is not None
        return caught.value

    def passthrough(value: Deferred) -> Deferred:
        return value

    def revenue() -> Deferred:
        return amount.sum()

    def reduced() -> Deferred:
        return total

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
        return amount.divide(other=divisor, zero_denominator="error")

    assert computation_fingerprint(_facts(scaled_amount).node) == computation_fingerprint(
        _facts(amount.divide(-2, zero_denominator="error")).node
    )
    assert root_declaration(scaled_amount).location is not None
    assert root_declaration(scaled_amount).location.path == __file__
    assert root_declaration(scaled_amount).location.line > 0


def test_measure_assertions_validate_real_axes_and_retain_fixed_time(
    catalog: SemanticCatalog,
) -> None:
    amount, balance = (_measure(catalog, "amount"), _measure(catalog, "balance"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    @ms.measure(additivity=ms.additive(over=(ms.ref.dimension("sales.orders.status"),)))
    def by_status() -> Deferred:
        return amount

    assert _facts(by_status).permission == SumAxes((_reference(status),))
    assert isinstance(_facts(by_status).node, MeasureAssertion)

    def current_amount() -> Deferred:
        return amount

    for axis in (ms.ref.dimension("sales.orders.missing"), ms.ref.dimension("sales.customers.id")):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.measure(additivity=ms.additive(over=(axis,)))(current_amount))
        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert axis.path in str(caught.value.received)

    @ms.measure(additivity=ms.non_additive())
    def balance_product() -> Deferred:
        return balance * amount

    @ms.measure(
        additivity=ms.additive_all(except_=(ms.ref.time_dimension("sales.orders.paid_at"),))
    )
    def fixed_product() -> Deferred:
        return -balance_product * 2

    assert _facts(fixed_product).permission == SumAllExcept((_reference(paid_at),))
    assert _facts(fixed_product).default_time == _facts(balance).default_time
    with pytest.raises(SemanticExpressionError) as erased:

        @ms.measure(additivity=ms.additive_all())
        def unrestricted_product() -> Deferred:
            return fixed_product

    assert erased.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert _reference(_reference(paid_at)).path in str(erased.value.received)


@pytest.mark.parametrize("policy", ("over", "except_"))
def test_additivity_rejects_routed_time_coordinates(catalog: SemanticCatalog, policy: str) -> None:
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    relationship = resolve(catalog, ms.ref.relationship("sales.orders_to_customers"))
    routed = paid_at.via((relationship,))
    assert consume_field(routed).field_read.path
    with pytest.raises(SemanticError) as caught:
        if policy == "over":
            ms.additive(over=(routed,))
        else:
            ms.additive_all(except_=(routed,))
    assert caught.value.kind == ErrorKind.INVALID_TIME_PATH
    assert caught.value.expected and caught.value.received and caught.value.repair
    assert caught.value.repair.action
    assert _facts(ms.additive(over=(paid_at,))).coordinates == (
        _reference(_reference(paid_at)).path,
    )
    assert ms.additive_all(except_=(paid_at,)).exceptions == (_reference(_reference(paid_at)).path,)


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

    assert _facts(inherited).unit is None
    assert _facts(inherited).permission == _facts(quantity).permission
    assert computation_fingerprint(_facts(inherited).node) == computation_fingerprint(
        _facts(quantity).node
    )
    assert _facts(asserted).unit == "CNY"
    assert isinstance(_facts(asserted).node, MeasureAssertion)
    assert _facts(_facts(asserted).node).unit == "CNY"
    assert _facts(_facts(asserted).node).permission is None
    assert _facts(quantity).unit is None
    assert computation_fingerprint(_facts(redundant).node) == computation_fingerprint(
        _facts(amount).node
    )
    assert isinstance(redundant * 2, Deferred)
    assert root_declaration(redundant * 2) is None


def test_metric_declaration_matches_ad_hoc_graph_without_loader(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))

    @ms.metric(
        domain=ms.ref.domain("sales"), ai_context=ms.ai_context(business_definition="Paid margin")
    )
    def margin() -> Deferred:
        """One named computation."""
        row = amount - cost
        return row.sum(time=paid_at).where(status == "paid")

    @ms.metric()
    def order_count() -> Deferred:
        return orders.count(time=paid_at).where(status == "paid")

    @ms.metric()
    def average_margin() -> Deferred:
        return margin / order_count

    trial = (amount - cost).sum(time=paid_at).where(status == "paid")
    assert type(margin) is Deferred
    assert root_declaration(margin).name == "margin"
    assert root_declaration(margin).domain == ms.ref.domain("sales")
    assert root_declaration(margin).ai_context is not None
    assert computation_fingerprint(_facts(margin).node) == computation_fingerprint(
        _facts(trial).node
    )
    assert computation_fingerprint(_facts(average_margin).node) == computation_fingerprint(
        _facts(trial / order_count).node
    )
    assert type(margin * 2) is Deferred
    alias = margin
    assert alias is margin
    axes = (status,)

    @ms.metric()
    def customer_average() -> Deferred:
        return margin.per(axes).mean(nulls="error")

    assert computation_fingerprint(_facts(customer_average).node) == computation_fingerprint(
        _facts(margin.per(status).mean(nulls="error")).node
    )


def test_metric_declaration_rejects_wrong_stages_and_receivers(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")

    def row() -> Deferred:
        return amount

    def helper(value: Deferred) -> Deferred:
        return value > 0

    def arbitrary_call() -> Deferred:
        return amount.sum().where(helper(amount))

    def table(rows: object) -> object:
        return rows

    def invalid_samples() -> Deferred:
        return pending

    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    pending = amount.sum().per(status)
    for body in (row, arbitrary_call, table, invalid_samples):
        with pytest.raises(SemanticExpressionError) as caught:
            _consume_value(ms.metric()(body))
        assert caught.value.expected and caught.value.received and caught.value.repair


def test_measure_permissions_map_declared_additivity(catalog: SemanticCatalog) -> None:
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    assert _facts(_physical(catalog, ms.ref.measure("sales.orders.amount"))).permission == SumAll()
    assert (
        _facts(_physical(catalog, ms.ref.measure("sales.orders.unit_price"))).permission
        == SumNone()
    )
    quantity = _physical(catalog, ms.ref.measure("sales.orders.quantity"))
    assert _facts(quantity).permission == SumAxes((_reference(status),))
    assert _facts(quantity).unit is None
    balance = _physical(catalog, ms.ref.measure("sales.orders.balance"))
    assert _facts(balance).permission == SumAllExcept((_reference(paid_at),))


def test_relationship_resolves_endpoints_and_cardinality(catalog: SemanticCatalog) -> None:
    relationship = resolve(catalog, ms.ref.relationship("sales.orders_to_customers"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    assert type(relationship) is Relationship
    assert relationship.from_entity == _reference(resolve(catalog, ms.ref.entity("sales.orders")))
    assert relationship.to_entity == _reference(customers)
    assert relationship.cardinality == "many_to_one"
    assert _facts(customers).default_time is None
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    assert _facts(created_at).root == _reference(customers)


def test_resolved_values_are_immutable_with_bounded_repr(catalog: SemanticCatalog) -> None:
    amount = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    with pytest.raises(AttributeError):
        amount.unit = _consume_value("USD")
    contract = catalog.describe(ms.ref.measure("sales.orders.amount"))
    assert "\n" not in repr(contract) and len(repr(contract)) < 120
    assert isinstance(consume_field(amount).reference, ExactRef)


def test_unsupported_and_absent_refs_fail_structurally(catalog: SemanticCatalog) -> None:
    with pytest.raises(SemanticExpressionError) as metric:
        _consume_value(resolve(catalog, ms.ref.metric("sales.revenue")))
    assert metric.value.kind == ErrorKind.METRIC_NOT_FOUND.value
    assert metric.value.received == "metric:sales.revenue"
    assert metric.value.repair is not None
    with pytest.raises(SemanticExpressionError) as raw:
        _consume_value(resolve(catalog, "sales.orders.amount"))
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert raw.value.received == "str"
    with pytest.raises(SemanticError) as absent:
        _consume_value(_physical(catalog, ms.ref.measure("sales.orders.missing")))
    assert absent.value.kind == ErrorKind.NOT_FOUND.value


def _measure(catalog: SemanticCatalog, name: str) -> Deferred:
    return _physical(catalog, ms.ref.measure(f"sales.orders.{name}"))


def test_row_subtraction_builds_one_fingerprint_on_one_root(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    margin = amount - cost
    again = amount - cost
    assert type(margin) is Deferred
    assert isinstance(_facts(margin).node, RowExpression)
    assert _facts(margin).node.inputs == (_facts(amount).node, _facts(cost).node)
    assert computation_fingerprint(_facts(margin).node) == computation_fingerprint(
        _facts(again).node
    )
    assert _facts(margin).root == _facts(amount).root
    assert (_facts(margin).unit, _facts(margin).permission) == ("CNY", SumAll())
    assert _facts(margin).node.recipe == resolver_of(var("slot_0") - var("slot_1"))


def test_division_forms_normalize_to_one_node(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    fingerprints = {
        computation_fingerprint(_facts(expression).node)
        for expression in (
            amount / cost,
            amount.divide(cost),
            amount.divide(cost, zero_denominator="null"),
        )
    }
    strict = amount.divide(cost, zero_denominator="error")
    assert len(fingerprints) == 1
    assert computation_fingerprint(_facts(strict).node) not in fingerprints
    assert isinstance(_facts(strict).node, RowExpression)
    assert (_facts(amount / cost).unit, _facts(amount / cost).permission) == ("1", SumNone())


def test_sum_permission_is_kept_narrowed_or_lost(catalog: SemanticCatalog) -> None:
    amount, balance = (_measure(catalog, "amount"), _measure(catalog, "balance"))
    quantity, unit_price = (_measure(catalog, "quantity"), _measure(catalog, "unit_price"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    scaled = amount * 2
    assert (_facts(scaled).unit, _facts(scaled).permission) == ("CNY", SumAll())
    assert _facts(2 * amount).permission == SumAll()
    assert isinstance(_facts(2 * amount).node, RowExpression)
    assert _facts(amount + 10).permission == SumAll()
    assert (
        isinstance(_facts(-amount).node, RowExpression) and _facts(-amount).permission == SumAll()
    )
    assert _facts(amount - balance).permission == SumAllExcept((_reference(paid_at),))
    assert _facts(quantity + quantity).permission == _facts(quantity).permission
    product = quantity * unit_price
    assert (_facts(product).permission, _facts(product).unit) == (SumNone(), None)
    inverse = 1 / amount
    assert _facts(inverse).node.inputs == (_facts(amount).node,)
    assert (_facts(inverse).unit, _facts(inverse).permission) == ("1/CNY", SumNone())


def test_incompatible_units_and_roots_are_rejected(catalog: SemanticCatalog) -> None:
    amount, amount_usd = (_measure(catalog, "amount"), _measure(catalog, "amount_usd"))
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    with pytest.raises(SemanticExpressionError) as units:
        _consume_value(amount + amount_usd)
    assert units.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert units.value.received == "CNY + USD"
    assert units.value.repair is not None
    unknown = amount + _measure(catalog, "quantity")
    assert _facts(unknown).unit is None
    with pytest.raises(SemanticExpressionError) as roots:
        _consume_value(amount - credit)
    assert roots.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    assert "sales.orders" in roots.value.received and "sales.customers" in roots.value.received


def test_invalid_row_operands_fail_structurally(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
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
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid = status == "paid"
    positive = amount - cost > 0
    missing = status.isnull()
    combined = paid & positive | ~missing
    assert all(isinstance(value, Deferred) for value in (paid, positive, missing, combined))
    for predicate in (paid, positive, missing, combined):
        assert isinstance(consume_predicate(predicate).node, PredicateExpression)
        assert consume_predicate(predicate).roots == (orders.ref,)
    fields = consume_predicate(combined).node.inputs[0]
    assert isinstance(fields, RowExpression)
    assert fields.inputs[0].ref == consume_field(status).reference
    assert fields.inputs[-1].ref == consume_field(status).reference
    assert computation_fingerprint(consume_predicate(combined).node) != computation_fingerprint(
        consume_predicate(paid & positive & ~missing).node
    )


def test_invalid_predicates_and_truth_conversion_fail(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    margin = amount - cost
    paid = status == "paid"
    with pytest.raises(TypeError, match="truth value"):
        bool(paid)
    with pytest.raises(TypeError, match="truth value"):
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


def test_margin_sum_binds_default_time_on_one_root(catalog: SemanticCatalog) -> None:
    amount, cost = (_measure(catalog, "amount"), _measure(catalog, "cost"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    implicit = (amount - cost).sum()
    explicit = (amount - cost).sum(time=paid_at)
    assert type(implicit) is Deferred
    assert isinstance(_facts(implicit).node, SourceAggregate)
    assert _facts(implicit).node.reducer == "sum" and _facts(implicit).node.nulls == "ignore"
    assert _facts(implicit).node.time == TimeBinding(_reference(paid_at))
    assert computation_fingerprint(_facts(implicit).node) == computation_fingerprint(
        _facts(explicit).node
    )
    assert (_facts(implicit).roots, _facts(implicit).unit) == ((_facts(amount).root,), "CNY")
    strict = (amount - cost).sum(nulls="error")
    assert computation_fingerprint(_facts(strict).node) != computation_fingerprint(
        _facts(implicit).node
    )
    assert isinstance(implicit, Deferred)


def test_unauthorized_product_sum_is_rejected_but_mean_is_allowed(catalog: SemanticCatalog) -> None:
    product = _measure(catalog, "quantity") * _measure(catalog, "unit_price")
    with pytest.raises(SemanticExpressionError) as missing:
        _consume_value(product.sum())
    assert missing.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value
    assert missing.value.repair is not None
    assert "Declare a Measure" in missing.value.repair.action
    with pytest.raises(SemanticExpressionError):
        _consume_value(_measure(catalog, "unit_price").sum())
    mean = product.mean()
    assert isinstance(_facts(mean).node, SourceAggregate) and _facts(mean).node.reducer == "mean"
    assert _facts(mean).unit is None
    median = _facts(_measure(catalog, "unit_price").median()).node
    assert isinstance(median, SourceAggregate) and median.reducer == "median"


def test_counts_have_unit_one_and_entity_count_reads_whole_rows(catalog: SemanticCatalog) -> None:
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    distinct = status.count_distinct()
    assert isinstance(_facts(distinct).node, SourceAggregate)
    assert (_facts(distinct).node.reducer, _facts(distinct).unit) == ("count_distinct", "1")
    assert _facts(_measure(catalog, "amount").count()).unit == "1"
    order_count = orders.count()
    assert _facts(order_count).node == EntityCount(
        _reference(orders), TimeBinding(_reference(paid_at))
    )
    assert (_facts(order_count).roots, _facts(order_count).unit) == ((_reference(orders),), "1")
    assert _facts(customers.count()).node == EntityCount(_reference(customers), None)
    with pytest.raises(SemanticExpressionError):
        consume_metric(status.sum())


def test_quantile_and_policy_inputs_are_validated(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    assert _facts(amount.quantile(0.95)).node == SourceAggregate(
        _facts(amount).node, "quantile", TimeBinding(_reference(paid_at)), q=0.95, empty="zero"
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
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    weighted = amount.weighted_mean(weight=quantity)
    assert isinstance(_facts(weighted).node, SourceWeightedMean)
    assert _facts(weighted).node.zero_denominator == "null"
    assert (_facts(weighted).roots, _facts(weighted).unit) == ((_facts(amount).root,), "CNY")
    strict = amount.weighted_mean(weight=quantity, zero_denominator="error")
    assert computation_fingerprint(_facts(strict).node) != computation_fingerprint(
        _facts(weighted).node
    )
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
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
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
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    revenue = amount.sum()
    order_count = orders.count()
    aov = revenue / order_count
    assert type(aov) is Deferred
    assert _facts(aov).node.operands == (_facts(revenue).node, _facts(order_count).node)
    assert (_facts(aov).roots, _facts(aov).unit) == ((_reference(orders),), "CNY")
    per_customer = revenue / customers.count()
    assert _facts(per_customer).roots == (_reference(orders), _reference(customers))
    assert isinstance(aov, Deferred)


def test_repeated_metric_keeps_two_ordered_uses(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    doubled = revenue + revenue
    edges = children(_facts(doubled).node)
    assert isinstance(_facts(doubled).node, MetricExpression)
    assert [(edge.role, edge.index) for edge in edges] == [("operand", 0), ("operand", 1)]
    assert edges[0].child == edges[1].child == _facts(revenue).node
    assert (_facts(doubled).roots, _facts(doubled).unit) == (_facts(revenue).roots, "CNY")


def test_metric_division_forms_normalize_to_one_node(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    cost = _measure(catalog, "cost").sum()
    fingerprints = {
        computation_fingerprint(_facts(metric).node)
        for metric in (
            revenue / cost,
            revenue.divide(cost),
            revenue.divide(cost, zero_denominator="null"),
        )
    }
    strict = revenue.divide(cost, zero_denominator="error")
    assert len(fingerprints) == 1
    assert computation_fingerprint(_facts(strict).node) not in fingerprints
    assert isinstance(_facts(strict).node, MetricExpression)
    assert _facts(revenue / cost).unit == "1"


def test_metric_constants_and_negation_follow_unit_rules(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    offset = 100 - revenue
    assert _facts(offset).node.recipe == resolver_of(100 - var("slot_0"))
    assert _facts(offset).node.operands == (_facts(revenue).node,)
    assert _facts(offset).unit == "CNY"
    assert (
        _facts(revenue + 10).unit == _facts(revenue * 0.9).unit == _facts(2 * revenue).unit == "CNY"
    )
    inverse = 1 / revenue
    assert _facts(inverse).node.operands == (_facts(revenue).node,)
    assert _facts(inverse).unit == "1/CNY"
    negated = -revenue
    assert _facts(negated).node.recipe == resolver_of(-var("slot_0"))
    assert (_facts(negated).roots, _facts(negated).unit) == (_facts(revenue).roots, "CNY")
    unknown = revenue + _measure(catalog, "quantity").sum()
    assert _facts(unknown).unit is None


def test_metric_unit_stage_and_value_failures(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    revenue = amount.sum()
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
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
    assert "reduce rows" in row_side.value.repair.action
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
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid = status == "paid"
    paid_margin = (amount - cost).sum().where(paid)
    assert isinstance(_facts(paid_margin).node, ContributionFilter)
    assert _facts(paid_margin).node.predicate == consume_predicate(paid).node
    assert (_facts(paid_margin).roots, _facts(paid_margin).unit) == ((_reference(orders),), "CNY")
    revenue = amount.sum().where(paid)
    order_count = orders.count().where(paid)
    aov = revenue / order_count
    assert _facts(aov).node.operands == (_facts(revenue).node, _facts(order_count).node)
    again = amount.sum().where(status == "paid") / orders.count().where(status == "paid")
    assert computation_fingerprint(_facts(aov).node) == computation_fingerprint(_facts(again).node)
    positive = amount.sum().where(amount - cost > 0)
    assert isinstance(_facts(positive).node, ContributionFilter)
    assert isinstance(_facts(positive).node.predicate, PredicateExpression)
    nested = revenue.where(status.isnull())
    assert isinstance(_facts(nested).node, ContributionFilter)
    assert _facts(nested).node.operand == _facts(revenue).node
    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity")).where(paid)
    assert isinstance(_facts(weighted).node, ContributionFilter)


def test_where_rejects_composite_cross_root_and_non_predicates(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    aov = amount.sum() / orders.count()
    with pytest.raises(SemanticExpressionError) as composite:
        _consume_value(aov.where(status == "paid"))
    assert composite.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    assert composite.value.repair is not None
    assert "each source child Metric" in composite.value.repair.action
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
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    revenue = amount.sum()
    samples = revenue.per(customer_id)
    assert type(samples) is Deferred
    axis = AxisBinding(
        entity=_facts(customer_id).root, field=_reference(customer_id), path=(), role="field"
    )
    assert _facts(samples).axes == (axis,)
    assert _facts(revenue.per((customer_id,))) == _facts(samples)
    observed_mean = samples.mean()
    assert _facts(observed_mean).node == PerReduce(
        _facts(revenue).node, (axis,), "mean", empty="zero"
    )
    assert (_facts(observed_mean).roots, _facts(observed_mean).unit) == (
        _facts(revenue).roots,
        "CNY",
    )
    assert isinstance(samples, Deferred)
    p95 = revenue.per(paid_at).quantile(0.95)
    assert isinstance(_facts(p95).node, PerReduce) and _facts(p95).node.q == 0.95
    pair = revenue.per((customer_id, status))
    assert len(_facts(pair).axes) == 2
    assert _facts(pair.count()).unit == "1" and _facts(pair.count_distinct()).unit == "1"
    for reduced in (pair.sum(), pair.min(), pair.max(), pair.median()):
        assert isinstance(_facts(reduced).node, PerReduce) and _facts(reduced).unit == "CNY"
    strict = samples.sum(nulls="error")
    assert isinstance(_facts(strict).node, PerReduce) and _facts(strict).node.nulls == "error"
    order_count = resolve(catalog, ms.ref.entity("sales.orders")).count()
    weighted = (revenue / order_count).per(customer_id).weighted_mean(weight=order_count)
    assert isinstance(_facts(weighted).node, PerWeightedMean)
    assert _facts(weighted).node.zero_denominator == "null"
    assert _facts(weighted).unit == "CNY"


def test_per_rejects_invalid_axes_weights_and_policies(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
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
    assert zero.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as quantile:
        _consume_value(samples.quantile(2.0))
    assert quantile.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as stage:
        _consume_value(samples + revenue)
    assert stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def _relationship(catalog: SemanticCatalog, name: str) -> Relationship:
    return resolve(catalog, ms.ref.relationship(f"sales.{name}"))


def test_via_binds_one_and_two_hop_time_paths_with_exact_refs(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = resolve(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    buyer_created = created_at.via((buyer,))
    assert type(buyer_created) is Deferred
    assert consume_field(buyer_created).field_read.path == (buyer.ref,)
    assert _reference(buyer_created) == _reference(created_at)
    assert _facts(buyer_created).root == buyer.from_entity
    assert isinstance(buyer_created, Deferred)
    with pytest.raises(AttributeError):
        buyer_created.path = _consume_value(())
    one_hop = amount.sum(time=buyer_created)
    assert isinstance(_facts(one_hop).node, SourceAggregate)
    assert _facts(one_hop).node.time == TimeBinding(_reference(created_at), (_reference(buyer),))
    assert _reference(buyer).version.startswith("sha256:")
    two_hop = orders.count(time=opened_at.via((buyer, region)))
    assert _facts(two_hop).node == EntityCount(
        _reference(orders),
        TimeBinding(_reference(opened_at), (_reference(buyer), _reference(region))),
    )
    native = amount.sum()
    assert computation_fingerprint(_facts(one_hop).node) != computation_fingerprint(
        _facts(native).node
    )
    again = amount.sum(time=created_at.via((buyer,)))
    assert computation_fingerprint(_facts(again).node) == computation_fingerprint(
        _facts(one_hop).node
    )
    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity"), time=buyer_created)
    assert isinstance(_facts(weighted).node, SourceWeightedMean)
    assert _facts(weighted).node.time == TimeBinding(_reference(created_at), (_reference(buyer),))
    filtered = amount.sum(time=buyer_created).where(
        resolve(catalog, ms.ref.dimension("sales.orders.status")) == "paid"
    )
    assert isinstance(_facts(filtered).node, ContributionFilter)


def test_via_rejects_malformed_disconnected_and_fanout_paths(catalog: SemanticCatalog) -> None:
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    fanout = _relationship(catalog, "customers_to_orders")
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = resolve(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
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
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = resolve(catalog, ms.ref.time_dimension("sales.regions.opened_at"))
    buyer_created = created_at.via((buyer,))
    with pytest.raises(SemanticExpressionError) as start:
        _consume_value(credit.sum(time=opened_at.via((buyer, region))))
    assert start.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert start.value.received == "entity:sales.orders"
    assert _facts(credit.sum(time=opened_at.via((region,)))).roots == (_reference(customers),)
    with pytest.raises(SemanticExpressionError) as entity_start:
        _consume_value(customers.count(time=buyer_created))
    assert entity_start.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert _facts(buyer_created == "2026-01-01").node.inputs[0].read.path == (_reference(buyer),)
    assert _facts(buyer_created.isnull()).node.inputs[0].read.path == (_reference(buyer),)
    for misuse in (lambda: buyer_created.count(), lambda: amount.sum().per(buyer_created)):
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
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
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
        assert _facts(before).permission != _facts(after).permission
        assert _reference(before) != _reference(after), name
    amount_ref = ms.ref.measure("sales.orders.amount")
    assert _reference(_physical(first, amount_ref)) == _reference(_physical(second, amount_ref))


def test_unary_positive_keeps_the_metric_unchanged(catalog: SemanticCatalog) -> None:
    revenue = (
        _measure(catalog, "amount")
        .sum(nulls="error")
        .where(resolve(catalog, ms.ref.dimension("sales.orders.status")) == "paid")
    )
    with pytest.raises(TypeError, match="unary"):
        _ = +revenue
    assert consume_metric(revenue).unit == "CNY"


def test_oversized_integer_quantile_fails_structurally(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
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
            assert oversized.value.repair.action == "Pass q such as 0.95."


def test_enabled_telemetry_preserves_oversized_quantile_errors(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    import marivo.telemetry as telemetry

    entries: list[dict[str, object]] = []

    def write_memory(root: Path, entry: dict[str, object]) -> None:
        assert root == tmp_path
        json.dumps(entry)
        entries.append(entry)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_TELEMETRY", "on")
    monkeypatch.setattr(telemetry, "_instance_id", lambda root: "in-memory-test")
    monkeypatch.setattr(telemetry, "_write_entry", write_memory)
    test_oversized_integer_quantile_fails_structurally(catalog)
    assert entries == []  # Native Deferred methods have no Marivo telemetry wrappers.
    assert not (tmp_path / ".marivo").exists()


def test_updated_root_identity_does_not_reject_stored_expression_inputs(
    catalog: SemanticCatalog,
) -> None:
    old_amount = _measure(catalog, "amount")
    old_cost = _measure(catalog, "cost")
    old_graph = _facts((old_amount - old_cost).sum()).node
    old_fingerprint = computation_fingerprint(old_graph)
    with loader_context(
        LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    ):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace('md.table("orders")', 'md.table("new_orders")'), namespace)
        current = ms.finalize()
    cost = current.resolve(ms.ref.measure("sales.orders.cost"))
    status = current.resolve(ms.ref.dimension("sales.orders.status"))
    paid_at = current.resolve(ms.ref.time_dimension("sales.orders.paid_at"))
    assert (
        _facts(old_amount).root.ref == _facts(cost).root.ref
        and _facts(old_amount).root != _facts(cost).root
    )
    combined = old_amount - cost
    assert _facts(combined).node.inputs == (_facts(old_amount).node, _facts(cost).node)
    assert _facts(combined).unit == _facts(old_amount).unit
    assert _facts(combined).permission == _facts(old_amount).permission
    reduced = combined.sum(time=paid_at)
    assert isinstance(_facts(reduced).node, SourceAggregate)
    assert _facts(reduced).node.time == TimeBinding(_reference(paid_at))
    filtered = reduced.where(status == "paid")
    assert isinstance(_facts(filtered).node, ContributionFilter)
    assert _facts(filtered).node.predicate == consume_predicate(status == "paid").node
    weighted = old_amount.weighted_mean(weight=cost, time=paid_at)
    assert isinstance(_facts(weighted).node, SourceWeightedMean)
    assert _facts(weighted).node.value == _facts(old_amount).node
    assert _facts(weighted).node.weight == _facts(cost).node
    assert computation_fingerprint(_facts(reduced).node) != old_fingerprint
    assert computation_fingerprint(old_graph) == old_fingerprint
    assert _reference(resolve(catalog, ms.ref.entity("sales.orders"))) == _facts(old_amount).root


def test_permission_intersection_and_time_paths_compare_semantic_references(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    old_axis = _reference(status)
    new_axis = ExactRef(old_axis.ref, "updated-definition")
    old_row = semantic_input(
        dataclasses.replace(consume_field(amount), permission=SumAxes((old_axis,)))
    )
    new_row = semantic_input(
        dataclasses.replace(consume_field(amount), permission=SumAxes((new_axis,)))
    )
    assert _facts(old_row + new_row).permission == SumAxes((old_axis,))
    excluded = semantic_input(
        dataclasses.replace(consume_field(new_row), permission=SumAllExcept((new_axis,)))
    )
    assert isinstance(_facts(old_row + excluded).permission, SumNone)
    with pytest.raises(SemanticExpressionError):
        _consume_value(
            amount.sum().per(
                (
                    status,
                    semantic_input(dataclasses.replace(consume_field(status), reference=new_axis)),
                )
            )
        )
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    changed_root = ExactRef(_facts(paid_at).root.ref, "updated-entity-definition")
    path = Relationship(
        ExactRef(ms.ref.relationship("sales.self_role"), "relationship-definition"),
        changed_root,
        changed_root,
        "many_to_one",
    )
    routed = paid_at.via((path,))
    result = amount.sum(time=routed)
    assert isinstance(_facts(result).node, SourceAggregate)
    assert _facts(result).node.time == TimeBinding(_reference(paid_at), (_reference(path),))


def test_computation_keys_include_consumed_default_time_facts(catalog: SemanticCatalog) -> None:
    original = _measure(catalog, "amount")
    original_key = computation_fingerprint(_facts(original.sum()).node)
    with loader_context(
        LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    ):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace("is_default=True", "is_default=False"), namespace)
        changed = ms.finalize().resolve(ms.ref.measure("sales.orders.amount"))
    assert _reference(changed) == _reference(original)
    assert _facts(original).default_time is not None and _facts(changed).default_time is None
    assert computation_fingerprint(_facts(changed.sum()).node) != original_key
    assert computation_fingerprint(_facts(original.sum()).node) == original_key
