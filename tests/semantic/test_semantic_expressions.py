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

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import (
    ContributionFilter,
    EntityCount,
    FieldCompare,
    FieldIsNull,
    MeasureAssertion,
    MetricAdd,
    MetricConstant,
    MetricDivide,
    MetricNegate,
    MetricSubtract,
    PerReduce,
    PerWeightedMean,
    PredAnd,
    PredNot,
    PredOr,
    RowCompare,
    RowConstant,
    RowDivide,
    RowField,
    RowMultiply,
    RowNegate,
    RowSubtract,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import children, computation_fingerprint
from marivo._definition.types import (
    AxisBinding,
    ExactRef,
    SumAll,
    SumAllExcept,
    SumAxes,
    SumNone,
)
from marivo.refs import MeasureKind
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticExpressionError,
)
from marivo.semantic.expressions import (
    DeclaredMeasure,
    DeclaredMetric,
    Dimension,
    Entity,
    FinalizedDeclarations,
    Measure,
    Metric,
    MetricSamples,
    NumericRowExpr,
    Predicate,
    Relationship,
    RowOperand,
    TimeField,
    declaration_state,
    resolve,
)
from marivo.semantic.loader import LoaderContext, loader_context
from marivo.semantic.reader import SemanticProject

_DOMAIN_PY = """\
import marivo.semantic as ms
ms.domain(name="sales", owner="Analytics", default=True)
"""

_OBJECTS_PY = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(
    name="orders",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"),
    primary_key=["order_id"],
)
customers = ms.entity(
    name="customers",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("customers"),
    primary_key=["id"],
)

status = ms.dimension_column(name="status", entity=orders, column="status")
customer_id = ms.dimension_column(name="customer_id", entity=orders, column="customer_id")
id = ms.dimension_column(name="id", entity=customers, column="id")
paid_at = ms.time_dimension_column(
    name="paid_at",
    entity=orders,
    column="paid_at",
    granularity="day",
    parse=ms.timestamp(timezone="UTC"),
    is_default=True,
)
created_at = ms.time_dimension_column(
    name="created_at",
    entity=customers,
    column="created_at",
    granularity="day",
    parse=ms.timestamp(timezone="UTC"),
)

amount = ms.measure_column(
    name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY"
)
unit_price = ms.measure_column(
    name="unit_price", entity=orders, column="unit_price", additivity=ms.non_additive(), unit="CNY"
)
quantity = ms.measure_column(
    name="quantity", entity=orders, column="quantity", additivity=ms.additive(over=(status,))
)
cost = ms.measure_column(
    name="cost", entity=orders, column="cost", additivity=ms.additive_all(), unit="CNY"
)
amount_usd = ms.measure_column(
    name="amount_usd", entity=orders, column="amount_usd", additivity=ms.additive_all(), unit="USD"
)
credit = ms.measure_column(
    name="credit", entity=customers, column="credit", additivity=ms.additive_all(), unit="CNY"
)
balance = ms.measure_column(
    name="balance",
    entity=orders,
    column="balance",
    additivity=ms.additive_all(except_=(paid_at,)),
    unit="CNY",
)

ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(customer_id, id)],
)

regions = ms.entity(
    name="regions",
    datasource=ms.ref.datasource("warehouse"),
    source=md.table("regions"),
    primary_key=["id"],
)
region_id = ms.dimension_column(name="region_id", entity=customers, column="region_id")
region_key = ms.dimension_column(name="id", entity=regions, column="id")
opened_at = ms.time_dimension_column(
    name="opened_at",
    entity=regions,
    column="opened_at",
    granularity="day",
    parse=ms.timestamp(timezone="UTC"),
)
ms.relationship(
    name="customers_to_regions",
    from_entity=customers,
    to_entity=regions,
    keys=[ms.join_on(region_id, region_key)],
)
ms.relationship(
    name="customers_to_orders",
    from_entity=customers,
    to_entity=orders,
    keys=[ms.join_on(id, customer_id)],
)
"""


ProjectFactory: TypeAlias = Callable[..., SemanticProject]


def _physical(
    scope: SemanticCatalog | FinalizedDeclarations, target: ms.Ref[MeasureKind]
) -> Measure:
    """Resolve a physical measure column; published pure Measures resolve separately."""
    value = resolve(scope, target)
    assert type(value) is Measure
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

    assert type(amount) is Measure
    assert type(status) is Dimension
    assert type(paid_at) is TimeField
    assert type(orders) is Entity
    assert amount.ref.ref == ms.ref.measure("sales.orders.amount")
    assert amount.ref.version.startswith("sha256:")
    assert amount.root == orders.ref == status.root == paid_at.root
    assert amount.unit == "CNY"
    assert orders.primary_key == ("order_id",)
    assert orders.default_time == paid_at.ref
    again = _physical(catalog, ms.ref.measure("sales.orders.amount"))
    assert (again.ref, again.root, again.unit, again.permission) == (
        amount.ref,
        amount.root,
        amount.unit,
        amount.permission,
    )
    assert amount.ref.version != status.ref.version


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
    assert _physical(first, amount_ref).ref.version != _physical(second, amount_ref).ref.version
    assert resolve(first, status_ref).ref.version == resolve(second, status_ref).ref.version


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
    declared_entity, loaded_entity = resolve(state, entity_ref), resolve(catalog, entity_ref)
    declared, loaded = _physical(state, measure_ref), _physical(catalog, measure_ref)

    assert declared_entity == loaded_entity
    assert declared.ref == loaded.ref
    assert declared.ref.version.startswith("sha256:")
    assert (declared.root, declared.unit, declared.permission, declared.default_time) == (
        loaded.root,
        loaded.unit,
        loaded.permission,
        loaded.default_time,
    )
    assert declared.permission == SumAllExcept(
        (resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at")).ref,)
    )


def test_declared_constructors_capture_catalog_versions_without_business_reads(
    catalog: SemanticCatalog,
    monkeypatch: pytest.MonkeyPatch,
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
        assert not isinstance(value, (Metric, NumericRowExpr))
        assert not isinstance(loaded, (Metric, NumericRowExpr))
        assert value.ref == loaded.ref
    amount = finalized.resolve(ms.ref.measure("sales.orders.amount"))
    paid_at = finalized.resolve(ms.ref.time_dimension("sales.orders.paid_at"))
    assert isinstance(amount, Measure) and isinstance(paid_at, TimeField)
    assert computation_fingerprint(amount.sum(time=paid_at).node) == computation_fingerprint(
        _measure(catalog, "amount")
        .sum(time=resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at")))
        .node
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
    assert amount.ref == finalized.ref
    forward, reverse = (amount + cost).sum().node, (cost + amount).sum().node
    assert isinstance(forward, SourceAggregate) and isinstance(reverse, SourceAggregate)
    assert forward.time == reverse.time == TimeBinding(paid_at.ref)
    assert amount.default_time == finalized.default_time == paid_at.ref
    assert orders.default_time == paid_at.ref


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
        def regional() -> Measure:
            return amount

    assert regional.permission == SumAxes((region.ref,))
    assert amount.coordinates == (region.ref,)


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
    assert region.ref == resolve(state, later_ref).ref
    assert normalized.ref == resolve(state, ms.ref.dimension("sales.orders.normalized")).ref


def test_finalize_outside_capture_teaches_model_scope() -> None:
    with pytest.raises(SemanticError) as caught:
        ms.finalize()
    assert caught.value.kind == ErrorKind.OUTSIDE_LOADER_CONTEXT
    assert caught.value.expected and caught.value.received and caught.value.repair
    assert "ms.finalize()" in caught.value.repair.action


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

        # Even a matching constructed input is checked only after its lookup closure exists.
        region_ref = ms.dimension_column(name="region", entity=orders_ref, column=column)
        state = ms.finalize()
    assert (state.resolve(region_ref).ref == old_region.ref) is (column == "old_region")
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
    assert probe.root == current_entity.ref and probe.root != old_entity.ref
    assert current_status.ref != old_status.ref
    assert (
        state._state.sidecar.bodies[ms.ref.dimension("sales.orders.status")].source_column
        == "new_status"
    )
    if input_kind == "coordinate":
        assert probe.permission == SumAxes((current_status.ref,))


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
    def granted() -> Measure:
        return amount

    current_status = state.resolve(ms.ref.dimension("sales.orders.status"))
    assert current_status is not None and current_status.ref != old_status.ref
    assert granted.permission == SumAxes((current_status.ref,))
    assert granted.root == amount.root and granted.unit == amount.unit


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
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "from _marivo_frozen_inputs import owner\n"
        f"orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
        f"source=md.table({'orders' if mode == 'same' else 'new_orders'!r}), primary_key=['order_id'])\n"
        "amount = ms.measure_column(name='amount', entity="
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
    assert (amount.root == old.ref) is (mode == "same")
    assert amount.root == resolve(current, ms.ref.entity("sales.orders")).ref
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
    def positive() -> Metric:
        return amount.sum().where(0 < amount)  # noqa: SIM300 -- exercise reflected AST dispatch

    @ms.metric()
    def bounded() -> Metric:
        return amount.sum().where((0 <= amount) & (100 > amount) & (100 >= amount))  # noqa: SIM300 -- literal-left regression

    @ms.metric()
    def literal_fields() -> Metric:
        return amount.sum().where(("paid" == status) & ("void" != status))  # type: ignore[arg-type]  # noqa: SIM300 -- str reflection builds Predicate at runtime

    @ms.metric()
    def time_filter() -> Metric:
        return amount.sum().where("2026-01-01" < paid_at)  # noqa: SIM300 -- reflected time predicate

    for declared, ordinary in (
        (positive, amount.sum().where(amount > 0)),
        (bounded, amount.sum().where((amount >= 0) & (amount < 100) & (amount <= 100))),
        (literal_fields, amount.sum().where((status == "paid") & (status != "void"))),
        (time_filter, amount.sum().where(paid_at > "2026-01-01")),
    ):
        assert computation_fingerprint(declared.node) == computation_fingerprint(ordinary.node)


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
            ms.finalize()
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
            ms.dimension_column(name="late", entity=orders_ref, column="late")
        assert late.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
        assert late.value.expected and late.value.received and late.value.repair
        assert len(ctx.pending_definitions) == before
    assert finalized.resolve(region_ref).ref == value.ref
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.root = value.root  # type: ignore[misc]  # intentional immutability probe
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
            def rejected() -> Measure:
                return amount_ref  # type: ignore[return-value]  # intentional capture-stage rejection

        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert "ms.finalize()" in caught.value.repair.action
        amount = ms.finalize().resolve(amount_ref)

    @ms.metric()
    def total() -> Metric:
        return amount.sum()

    assert total.node == amount.sum().node


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
        ms.time_dimension_column(
            name="paid_at", entity=orders_ref, column="paid_at", granularity="day", is_default=True
        )
    assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE
    assert later.pending_definitions == []
    assert finalized.resolve(orders_ref).default_time is None


def test_finalized_capture_imports_once_and_matches_catalog(
    semantic_project_factory: ProjectFactory, tmp_path: Path
) -> None:
    counter, evidence = tmp_path / "module-effects.txt", tmp_path / "captured.json"
    physical = """import marivo.semantic as ms
import marivo.datasource as md
from pathlib import Path
with Path(COUNTER).open("a") as output:
    output.write("capture\\n")
orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all())
later_ref = ms.ref.dimension("sales.orders.region")
@ms.dimension(entity=orders_ref)
def normalized(rows):
    return ms.bind(later_ref, rows).upper()
paid_at_ref = ms.time_dimension_column(name="paid_at", entity=orders_ref, column="paid_at", granularity="day", is_default=True)
cost_ref = ms.measure_column(name="cost", entity=orders_ref, column="cost", additivity=ms.additive_all())
region_ref = ms.dimension_column(name="region", entity=orders_ref, column="region")
""".replace("COUNTER", repr(str(counter)))
    composition = """import json
from pathlib import Path
import marivo.semantic as ms
from .z_physical import orders_ref, amount_ref, paid_at_ref, cost_ref, normalized
finalized = ms.finalize()
orders = finalized.resolve(orders_ref)
amount = finalized.resolve(amount_ref)
cost = finalized.resolve(cost_ref)
paid_at = finalized.resolve(paid_at_ref)
normalized_value = finalized.resolve(normalized)
@ms.metric()
def revenue():
    return (amount + cost).sum()
Path(EVIDENCE).write_text(json.dumps({"version": normalized_value.ref.version, "same_time": (amount + cost).sum().node.time == (cost + amount).sum().node.time, "default": orders.default_time.ref.path}))
""".replace("EVIDENCE", repr(str(evidence)))
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
        == resolve(loaded, ms.ref.dimension("sales.orders.normalized")).ref.version
    )
    assert not loaded._state.registry.metrics


def test_retired_declaration_syntax_has_specific_reauthoring_errors(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    for factory, kwargs, token in (
        (ms.metric, {"entities": [ms.ref.entity("sales.orders")]}, "zero-argument"),
        (ms.metric, {"fanout_policy": "aggregate_then_join"}, "Overlapping"),
        (ms.metric, {"empty": "null"}, "does not preserve"),
        (ms.measure_column, {"status_time_fold": None}, "per("),
        (ms.metric, {"time_via": (ms.ref.relationship("sales.orders_to_customers"),)}, "via("),
    ):
        with pytest.raises(SemanticExpressionError) as caught:
            factory(**kwargs)
        assert caught.value.expected and caught.value.received and caught.value.repair
        assert token in caught.value.repair.action
    with pytest.raises(SemanticExpressionError) as approximate:
        ms.aggregate(measure=amount, agg="approx_median")
    assert approximate.value.repair is not None
    assert "Approximate" in approximate.value.repair.action


def test_measure_declaration_names_the_ad_hoc_graph_without_loader(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends
    from marivo.semantic.loader import _LOADER_CTX

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("measure declaration opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    assert _LOADER_CTX.get() is None

    @ms.measure(ai_context=ms.ai_context(business_definition="Order margin"))
    def order_margin() -> NumericRowExpr:
        """Margin of one order."""
        margin = amount - cost
        return margin

    @ms.measure()
    def trial_margin() -> NumericRowExpr:
        return amount - cost

    margin_alias = order_margin
    ad_hoc = amount - cost

    assert type(order_margin) is DeclaredMeasure
    assert (order_margin.declaration.name, trial_margin.declaration.name) == (
        "order_margin",
        "trial_margin",
    )
    assert order_margin.declaration.ai_context is not None
    assert (order_margin.root, order_margin.unit, order_margin.permission) == (
        amount.root,
        "CNY",
        SumAll(),
    )
    assert computation_fingerprint(order_margin.declaration.definition) == (
        computation_fingerprint(ad_hoc.node)
    )
    sums = {
        computation_fingerprint(m.sum().node) for m in (order_margin, trial_margin, margin_alias)
    }
    assert sums == {computation_fingerprint(ad_hoc.sum().node)}
    assert "sales.orders.order_margin" in repr(order_margin)


def test_measure_grant_authorizes_an_otherwise_unsummable_product(
    catalog: SemanticCatalog,
) -> None:
    quantity, unit_price = _measure(catalog, "quantity"), _measure(catalog, "unit_price")
    amount, cost, balance = (_measure(catalog, name) for name in ("amount", "cost", "balance"))

    @ms.measure()
    def priced() -> NumericRowExpr:
        return quantity * unit_price

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def order_amount() -> NumericRowExpr:
        return quantity * unit_price

    unsummable: tuple[RowOperand, ...] = (quantity * unit_price, priced, unit_price)
    for operand in unsummable:
        with pytest.raises(SemanticExpressionError) as denied:
            operand.sum()
        assert denied.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value

    assert order_amount.sum().unit == "CNY"
    assert (order_amount.unit, order_amount.permission) == ("CNY", SumAll())
    definition = order_amount.declaration.definition
    assert isinstance(definition, MeasureAssertion)
    assert (definition.unit, definition.permission) == ("CNY", SumAll())
    assert definition.authorization.source.ref == ms.ref.measure("sales.orders.order_amount")
    assert computation_fingerprint(definition) != (
        computation_fingerprint(priced.declaration.definition)
    )

    @ms.measure(additivity=ms.additive_all(), unit="CNY")
    def margin() -> NumericRowExpr:
        return amount - cost

    assert computation_fingerprint(margin.declaration.definition) == (
        computation_fingerprint((amount - cost).node)
    )

    with pytest.raises(SemanticExpressionError) as conflict:

        @ms.measure(unit="USD")
        def usd_margin() -> NumericRowExpr:
            return amount - cost

    assert conflict.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value

    with pytest.raises(SemanticExpressionError) as erased:

        @ms.measure(additivity=ms.additive_all())
        def open_balance() -> NumericRowExpr:
            return balance * unit_price

    assert erased.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert "paid_at" in str(erased.value.received)


def test_measure_declaration_rejects_wrong_stage_and_retired_bodies(
    catalog: SemanticCatalog,
) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    total = amount.sum()

    def rejected(body: Callable[..., object]) -> SemanticExpressionError:
        with pytest.raises(SemanticExpressionError) as caught:
            ms.measure()(body)  # type: ignore[arg-type]  # rejection inputs are mistyped on purpose
        assert caught.value.expected and caught.value.received
        assert caught.value.repair is not None
        return caught.value

    def passthrough(value: NumericRowExpr) -> NumericRowExpr:
        return value

    def revenue() -> Metric:
        return amount.sum()

    def reduced() -> Metric:
        return total

    def old_amount(orders_table: Mapping[str, object]) -> object:
        return orders_table["amount"]

    def called() -> NumericRowExpr:
        return passthrough(amount - cost)

    def rebound() -> NumericRowExpr:
        margin = amount - cost
        margin = margin * 2
        return margin

    def _gross_margin() -> NumericRowExpr:
        return amount - cost

    stage = rejected(revenue)
    assert stage.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert "sum" in str(stage.received)
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
        def divide(self, _other: int) -> NumericRowExpr:
            calls.append("divide")
            return amount * 2

    fake = Pretender()
    divide_alias = fake.divide

    def forged_receiver() -> NumericRowExpr:
        return fake.divide(2)

    def forged_alias() -> NumericRowExpr:
        return divide_alias(2)

    def effect_before_return() -> Measure:
        calls.append("effect")
        return amount

    for body in (forged_receiver, forged_alias, effect_before_return):
        with pytest.raises(SemanticExpressionError) as caught:
            ms.measure()(body)
        assert caught.value.kind == ErrorKind.INVALID_COMPONENT_BODY.value
        assert caught.value.expected and caught.value.received and caught.value.repair
    assert calls == []

    @ms.measure()
    def scaled_amount() -> NumericRowExpr:
        divisor = -2
        return amount.divide(other=divisor, zero_denominator="error")

    assert computation_fingerprint(scaled_amount.node) == computation_fingerprint(
        amount.divide(-2, zero_denominator="error").node
    )
    assert scaled_amount.declaration.location is not None
    assert scaled_amount.declaration.location.path == __file__
    assert scaled_amount.declaration.location.line > 0


def test_measure_assertions_validate_real_axes_and_retain_fixed_time(
    catalog: SemanticCatalog,
) -> None:
    amount, balance = _measure(catalog, "amount"), _measure(catalog, "balance")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    @ms.measure(additivity=ms.additive(over=(ms.ref.dimension("sales.orders.status"),)))
    def by_status() -> Measure:
        return amount

    assert by_status.permission == SumAxes((status.ref,))
    assert isinstance(by_status.node, MeasureAssertion)

    def current_amount() -> Measure:
        return amount

    for axis in (
        ms.ref.dimension("sales.orders.missing"),
        ms.ref.dimension("sales.customers.id"),
    ):
        with pytest.raises(SemanticExpressionError) as caught:
            ms.measure(additivity=ms.additive(over=(axis,)))(current_amount)
        assert caught.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert axis.path in str(caught.value.received)

    @ms.measure(additivity=ms.non_additive())
    def balance_product() -> NumericRowExpr:
        return balance * amount

    @ms.measure(
        additivity=ms.additive_all(except_=(ms.ref.time_dimension("sales.orders.paid_at"),))
    )
    def fixed_product() -> NumericRowExpr:
        return -balance_product * 2

    assert fixed_product.permission == SumAllExcept((paid_at.ref,))
    assert fixed_product.default_time == balance.default_time

    with pytest.raises(SemanticExpressionError) as erased:

        @ms.measure(additivity=ms.additive_all())
        def unrestricted_product() -> NumericRowExpr:
            return fixed_product

    assert erased.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert paid_at.ref.ref.path in str(erased.value.received)


@pytest.mark.parametrize("policy", ("over", "except_"))
def test_additivity_rejects_routed_time_coordinates(catalog: SemanticCatalog, policy: str) -> None:
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    relationship = resolve(catalog, ms.ref.relationship("sales.orders_to_customers"))
    routed = paid_at.via((relationship,))
    assert routed.path
    with pytest.raises(SemanticExpressionError) as caught:
        if policy == "over":
            ms.additive(over=(routed,))
        else:
            ms.additive_all(except_=(routed,))
    assert caught.value.kind == ErrorKind.INVALID_TIME_PATH
    assert caught.value.expected and caught.value.received and caught.value.repair
    assert "time=" in caught.value.repair.action
    assert ms.additive(over=(paid_at,)).coordinates == (paid_at.ref.ref.path,)
    assert ms.additive_all(except_=(paid_at,)).exceptions == (paid_at.ref.ref.path,)


def test_measure_inherits_unknown_unit_and_normalizes_redundant_assertions(
    catalog: SemanticCatalog,
) -> None:
    quantity = _measure(catalog, "quantity")
    amount = _measure(catalog, "amount")

    @ms.measure(unit=None, additivity=None)
    def inherited() -> Measure:
        return quantity

    @ms.measure(unit="CNY")
    def asserted() -> Measure:
        return quantity

    @ms.measure(unit="CNY.CNY/CNY", additivity=ms.additive_all())
    def redundant() -> Measure:
        return amount

    assert inherited.unit is None
    assert inherited.permission == quantity.permission
    assert computation_fingerprint(inherited.node) == computation_fingerprint(
        quantity._as_row().node
    )
    assert asserted.unit == "CNY"
    assert isinstance(asserted.node, MeasureAssertion)
    assert asserted.node.unit == "CNY"
    assert asserted.node.permission is None
    assert quantity.unit is None
    assert computation_fingerprint(redundant.node) == computation_fingerprint(amount._as_row().node)
    assert isinstance(redundant * 2, NumericRowExpr)
    assert not isinstance(redundant * 2, DeclaredMeasure)


def test_metric_declaration_matches_ad_hoc_graph_without_loader(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))

    @ms.metric(
        domain=ms.ref.domain("sales"), ai_context=ms.ai_context(business_definition="Paid margin")
    )
    def margin() -> Metric:
        """One named computation."""
        row = amount - cost
        return row.sum(time=paid_at).where(status == "paid")

    @ms.metric()
    def order_count() -> Metric:
        return orders.count(time=paid_at).where(status == "paid")

    @ms.metric()
    def average_margin() -> Metric:
        return margin / order_count

    trial = (amount - cost).sum(time=paid_at).where(status == "paid")
    assert type(margin) is DeclaredMetric
    assert margin.declaration.name == "margin"
    assert margin.declaration.domain == ms.ref.domain("sales")
    assert margin.declaration.ai_context is not None
    assert computation_fingerprint(margin.node) == computation_fingerprint(trial.node)
    assert computation_fingerprint(average_margin.node) == computation_fingerprint(
        (trial / order_count).node
    )
    assert type(margin * 2) is Metric
    alias = margin
    assert alias is margin

    axes = (status,)

    @ms.metric()
    def customer_average() -> Metric:
        return margin.per(axes).mean(nulls="error")

    assert computation_fingerprint(customer_average.node) == computation_fingerprint(
        margin.per(status).mean(nulls="error").node
    )


def test_metric_declaration_rejects_wrong_stages_and_receivers(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")

    def row() -> Measure:
        return amount

    def helper(value: Measure) -> Predicate:
        return value > 0

    def arbitrary_call() -> Metric:
        return amount.sum().where(helper(amount))

    def table(rows: object) -> object:
        return rows

    def invalid_samples() -> MetricSamples:
        return pending

    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    pending = amount.sum().per(status)
    for body in (row, arbitrary_call, table, invalid_samples):
        with pytest.raises(SemanticExpressionError) as caught:
            ms.metric()(body)  # type: ignore[arg-type]  # exercise rejected return stages and arity
        assert caught.value.expected and caught.value.received and caught.value.repair


def test_measure_permissions_map_declared_additivity(catalog: SemanticCatalog) -> None:
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    assert _physical(catalog, ms.ref.measure("sales.orders.amount")).permission == SumAll()
    assert _physical(catalog, ms.ref.measure("sales.orders.unit_price")).permission == SumNone()
    quantity = _physical(catalog, ms.ref.measure("sales.orders.quantity"))
    assert quantity.permission == SumAxes((status.ref,))
    assert quantity.unit is None
    balance = _physical(catalog, ms.ref.measure("sales.orders.balance"))
    assert balance.permission == SumAllExcept((paid_at.ref,))


def test_relationship_resolves_endpoints_and_cardinality(catalog: SemanticCatalog) -> None:
    relationship = resolve(catalog, ms.ref.relationship("sales.orders_to_customers"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))

    assert type(relationship) is Relationship
    assert relationship.from_entity == resolve(catalog, ms.ref.entity("sales.orders")).ref
    assert relationship.to_entity == customers.ref
    assert relationship.cardinality == "many_to_one"
    assert customers.default_time is None
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    assert created_at.root == customers.ref


def test_resolved_values_are_immutable_with_bounded_repr(catalog: SemanticCatalog) -> None:
    amount = _physical(catalog, ms.ref.measure("sales.orders.amount"))

    with pytest.raises(dataclasses.FrozenInstanceError):
        amount.unit = "USD"  # type: ignore[misc]
    rendered = repr(amount)
    assert rendered.startswith("Measure sales.orders.amount@sha256:")
    assert "\n" not in rendered and len(rendered) < 80
    assert isinstance(amount.ref, ExactRef)


def test_unsupported_and_absent_refs_fail_structurally(catalog: SemanticCatalog) -> None:
    with pytest.raises(SemanticExpressionError) as metric:
        resolve(catalog, ms.ref.metric("sales.revenue"))
    assert metric.value.kind == ErrorKind.METRIC_NOT_FOUND.value
    assert metric.value.received == "metric:sales.revenue"
    assert metric.value.repair is not None

    with pytest.raises(SemanticExpressionError) as raw:
        resolve(catalog, "sales.orders.amount")  # type: ignore[call-overload]
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert raw.value.received == "str"

    with pytest.raises(SemanticError) as absent:
        _physical(catalog, ms.ref.measure("sales.orders.missing"))
    assert absent.value.kind == ErrorKind.NOT_FOUND.value


def _measure(catalog: SemanticCatalog, name: str) -> Measure:
    return _physical(catalog, ms.ref.measure(f"sales.orders.{name}"))


def test_row_subtraction_builds_one_fingerprint_on_one_root(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")

    margin = amount - cost
    again = amount - cost

    assert type(margin) is NumericRowExpr
    assert isinstance(margin.node, RowSubtract)
    assert margin.node.left == RowField(amount.ref, amount.root, "CNY", SumAll())
    assert computation_fingerprint(margin.node) == computation_fingerprint(again.node)
    assert margin.root == amount.root
    assert (margin.unit, margin.permission) == ("CNY", SumAll())
    assert repr(margin).startswith("NumericRowExpr RowSubtract on sales.orders ")


def test_division_forms_normalize_to_one_node(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")

    fingerprints = {
        computation_fingerprint(expression.node)
        for expression in (
            amount / cost,
            amount.divide(cost),
            amount.divide(cost, zero_denominator="null"),
        )
    }
    strict = amount.divide(cost, zero_denominator="error")

    assert len(fingerprints) == 1
    assert computation_fingerprint(strict.node) not in fingerprints
    assert isinstance(strict.node, RowDivide) and strict.node.zero_denominator == "error"
    assert ((amount / cost).unit, (amount / cost).permission) == ("1", SumNone())


def test_sum_permission_is_kept_narrowed_or_lost(catalog: SemanticCatalog) -> None:
    amount, balance = _measure(catalog, "amount"), _measure(catalog, "balance")
    quantity, unit_price = _measure(catalog, "quantity"), _measure(catalog, "unit_price")
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    scaled = amount * 2
    assert (scaled.unit, scaled.permission) == ("CNY", SumAll())
    assert (2 * amount).permission == SumAll()
    assert isinstance((2 * amount).node, RowMultiply)
    assert (amount + 10).permission == SumAll()
    assert isinstance((-amount).node, RowNegate) and (-amount).permission == SumAll()
    assert (amount - balance).permission == SumAllExcept((paid_at.ref,))
    assert (quantity + quantity).permission == quantity.permission
    product = quantity * unit_price
    assert (product.permission, product.unit) == (SumNone(), None)
    inverse = 1 / amount
    assert isinstance(inverse.node, RowDivide) and inverse.node.numerator == RowConstant(1)
    assert (inverse.unit, inverse.permission) == ("1/CNY", SumNone())


def test_incompatible_units_and_roots_are_rejected(catalog: SemanticCatalog) -> None:
    amount, amount_usd = _measure(catalog, "amount"), _measure(catalog, "amount_usd")
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))

    with pytest.raises(SemanticExpressionError) as units:
        amount + amount_usd
    assert units.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert units.value.received == "CNY + USD"
    assert units.value.repair is not None

    unknown = amount + _measure(catalog, "quantity")
    assert unknown.unit is None

    with pytest.raises(SemanticExpressionError) as roots:
        amount - credit
    assert roots.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    assert roots.value.received == "sales.orders - sales.customers"


def test_invalid_row_operands_fail_structurally(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))

    with pytest.raises(SemanticExpressionError) as boolean:
        amount + True
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nan:
        amount * float("nan")
    assert nan.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert nan.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as dimension:
        amount + status  # type: ignore[operator]
    assert dimension.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as text:
        amount + "1"  # type: ignore[operator]
    assert text.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as policy:
        amount.divide(cost, zero_denominator="skip")  # type: ignore[arg-type]
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_field_and_row_predicates_are_closed_values(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))

    paid = status == "paid"
    positive = (amount - cost) > 0
    missing = status.is_null()
    combined = (paid & positive) | ~missing

    assert type(paid) is Predicate
    assert paid.node == FieldCompare(status.ref, "eq", "paid")
    assert isinstance(positive.node, RowCompare) and positive.node.op == "gt"
    assert missing.node == FieldIsNull(status.ref)
    assert isinstance(combined.node, PredOr)
    left, right = combined.node.operands
    assert isinstance(left, PredAnd) and isinstance(right, PredNot)
    assert combined.roots == (orders.ref,)
    assert repr(combined) == "Predicate PredOr on sales.orders (use .show())"


def test_invalid_predicates_and_truth_conversion_fail(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    margin = amount - cost
    paid = status == "paid"

    with pytest.raises(SemanticExpressionError) as truth:
        bool(paid)
    assert truth.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as conjunction:
        _ = paid and paid
    assert conjunction.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError, match="is_null"):
        _ = status == None  # noqa: E711
    with pytest.raises(SemanticExpressionError) as container:
        _ = status == ["paid"]
    assert container.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as boolean:
        _ = margin > True
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as infinite:
        _ = margin > float("inf")
    assert infinite.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as operand:
        paid & "status"  # type: ignore[operator]
    assert operand.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def test_margin_sum_binds_default_time_on_one_root(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    implicit = (amount - cost).sum()
    explicit = (amount - cost).sum(time=paid_at)

    assert type(implicit) is Metric
    assert isinstance(implicit.node, SourceAggregate)
    assert implicit.node.reducer == "sum" and implicit.node.nulls == "ignore"
    assert implicit.node.time == TimeBinding(paid_at.ref)
    assert computation_fingerprint(implicit.node) == computation_fingerprint(explicit.node)
    assert (implicit.roots, implicit.unit) == ((amount.root,), "CNY")
    strict = (amount - cost).sum(nulls="error")
    assert computation_fingerprint(strict.node) != computation_fingerprint(implicit.node)
    assert repr(implicit).startswith("Metric SourceAggregate on sales.orders ")


def test_unauthorized_product_sum_is_rejected_but_mean_is_allowed(
    catalog: SemanticCatalog,
) -> None:
    product = _measure(catalog, "quantity") * _measure(catalog, "unit_price")

    with pytest.raises(SemanticExpressionError) as missing:
        product.sum()
    assert missing.value.kind == ErrorKind.MISSING_SUM_PERMISSION.value
    assert missing.value.repair is not None
    assert "Declare a Measure" in missing.value.repair.action
    with pytest.raises(SemanticExpressionError):
        _measure(catalog, "unit_price").sum()

    mean = product.mean()
    assert isinstance(mean.node, SourceAggregate) and mean.node.reducer == "mean"
    assert mean.unit is None
    median = _measure(catalog, "unit_price").median().node
    assert isinstance(median, SourceAggregate) and median.reducer == "median"


def test_counts_have_unit_one_and_entity_count_reads_whole_rows(
    catalog: SemanticCatalog,
) -> None:
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    distinct = status.count_distinct()
    assert isinstance(distinct.node, SourceAggregate)
    assert (distinct.node.reducer, distinct.unit) == ("count_distinct", "1")
    assert _measure(catalog, "amount").count().unit == "1"
    order_count = orders.count()
    assert order_count.node == EntityCount(orders.ref, TimeBinding(paid_at.ref))
    assert (order_count.roots, order_count.unit) == ((orders.ref,), "1")
    assert customers.count().node == EntityCount(customers.ref, None)
    assert not hasattr(status, "sum")


def test_quantile_and_policy_inputs_are_validated(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    assert amount.quantile(0.95).node == SourceAggregate(
        amount._as_row().node, "quantile", TimeBinding(paid_at.ref), q=0.95
    )
    for bad in (1.5, -0.1, float("nan")):
        with pytest.raises(SemanticExpressionError) as out_of_range:
            amount.quantile(bad)
        assert out_of_range.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
        assert out_of_range.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as boolean_q:
        amount.quantile(True)
    assert boolean_q.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nulls:
        amount.sum(nulls="skip")  # type: ignore[arg-type]
    assert nulls.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as raw_time:
        amount.sum(time=ms.ref.time_dimension("sales.orders.paid_at"))  # type: ignore[arg-type]
    assert raw_time.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_weighted_mean_pairs_one_root_and_rejects_foreign_time(
    catalog: SemanticCatalog,
) -> None:
    amount, quantity = _measure(catalog, "amount"), _measure(catalog, "quantity")
    credit = _physical(catalog, ms.ref.measure("sales.customers.credit"))
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))

    weighted = amount.weighted_mean(weight=quantity)
    assert isinstance(weighted.node, SourceWeightedMean)
    assert weighted.node.zero_denominator == "null"
    assert (weighted.roots, weighted.unit) == ((amount.root,), "CNY")
    strict = amount.weighted_mean(weight=quantity, zero_denominator="error")
    assert computation_fingerprint(strict.node) != computation_fingerprint(weighted.node)

    with pytest.raises(SemanticExpressionError) as roots:
        amount.weighted_mean(weight=credit)
    assert roots.value.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION.value
    with pytest.raises(SemanticExpressionError) as stage:
        amount.weighted_mean(weight=2)  # type: ignore[arg-type]
    assert stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as foreign:
        amount.sum(time=created_at)
    assert foreign.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert foreign.value.received == "sales.customers.created_at on sales.customers"


def test_expression_construction_reads_no_business_source(
    catalog: SemanticCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("expression construction opened a datasource backend")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))

    metrics = (
        (amount - cost).sum(),
        amount.quantile(0.5),
        amount.weighted_mean(weight=_measure(catalog, "quantity")),
        status.count_distinct(),
        orders.count(),
    )
    assert all(isinstance(metric, Metric) for metric in metrics)


def test_aov_divides_completed_metrics_across_roots(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    customers = resolve(catalog, ms.ref.entity("sales.customers"))

    revenue = amount.sum()
    order_count = orders.count()
    aov = revenue / order_count

    assert type(aov) is Metric
    assert aov.node == MetricDivide(revenue.node, order_count.node)
    assert (aov.roots, aov.unit) == ((orders.ref,), "CNY")
    per_customer = revenue / customers.count()
    assert per_customer.roots == (orders.ref, customers.ref)
    assert repr(aov).startswith("Metric MetricDivide on sales.orders ")


def test_repeated_metric_keeps_two_ordered_uses(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()

    doubled = revenue + revenue
    edges = children(doubled.node)

    assert isinstance(doubled.node, MetricAdd)
    assert [(edge.role, edge.index) for edge in edges] == [("left", 0), ("right", 1)]
    assert edges[0].child is edges[1].child is revenue.node
    assert (doubled.roots, doubled.unit) == (revenue.roots, "CNY")


def test_metric_division_forms_normalize_to_one_node(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()
    cost = _measure(catalog, "cost").sum()

    fingerprints = {
        computation_fingerprint(metric.node)
        for metric in (
            revenue / cost,
            revenue.divide(cost),
            revenue.divide(cost, zero_denominator="null"),
        )
    }
    strict = revenue.divide(cost, zero_denominator="error")

    assert len(fingerprints) == 1
    assert computation_fingerprint(strict.node) not in fingerprints
    assert isinstance(strict.node, MetricDivide) and strict.node.zero_denominator == "error"
    assert (revenue / cost).unit == "1"


def test_metric_constants_and_negation_follow_unit_rules(catalog: SemanticCatalog) -> None:
    revenue = _measure(catalog, "amount").sum()

    offset = 100 - revenue
    assert offset.node == MetricSubtract(MetricConstant(100), revenue.node)
    assert offset.unit == "CNY"
    assert (revenue + 10).unit == (revenue * 0.9).unit == (2 * revenue).unit == "CNY"
    inverse = 1 / revenue
    assert inverse.node == MetricDivide(MetricConstant(1), revenue.node)
    assert inverse.unit == "1/CNY"
    negated = -revenue
    assert negated.node == MetricNegate(revenue.node)
    assert (negated.roots, negated.unit) == (revenue.roots, "CNY")
    unknown = revenue + _measure(catalog, "quantity").sum()
    assert unknown.unit is None


def test_metric_unit_stage_and_value_failures(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    revenue = amount.sum()
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))

    with pytest.raises(SemanticExpressionError) as units:
        revenue + _measure(catalog, "amount_usd").sum()
    assert units.value.kind == ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value
    assert units.value.received == "CNY + USD"
    with pytest.raises(SemanticExpressionError) as measure_stage:
        revenue + amount  # type: ignore[operator]
    assert measure_stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as dimension_stage:
        revenue * status  # type: ignore[operator]
    assert dimension_stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as row_side:
        amount + revenue  # type: ignore[operator]
    assert row_side.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert row_side.value.repair is not None
    assert "reduce it first" in row_side.value.repair.action
    with pytest.raises(SemanticExpressionError) as boolean:
        revenue + True
    assert boolean.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as nan:
        revenue * float("nan")
    assert nan.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as text:
        revenue - "1"  # type: ignore[operator]
    assert text.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as policy:
        revenue.divide(revenue, zero_denominator="skip")  # type: ignore[arg-type]
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value


def test_paid_margin_and_aov_filter_each_source_reduction(catalog: SemanticCatalog) -> None:
    amount, cost = _measure(catalog, "amount"), _measure(catalog, "cost")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    paid = status == "paid"

    paid_margin = (amount - cost).sum().where(paid)
    assert isinstance(paid_margin.node, ContributionFilter)
    assert paid_margin.node.predicate == paid.node
    assert (paid_margin.roots, paid_margin.unit) == ((orders.ref,), "CNY")

    revenue = amount.sum().where(paid)
    order_count = orders.count().where(paid)
    aov = revenue / order_count
    assert aov.node == MetricDivide(revenue.node, order_count.node)
    again = amount.sum().where(status == "paid") / orders.count().where(status == "paid")
    assert computation_fingerprint(aov.node) == computation_fingerprint(again.node)

    positive = amount.sum().where((amount - cost) > 0)
    assert isinstance(positive.node, ContributionFilter)
    assert isinstance(positive.node.predicate, RowCompare)
    nested = revenue.where(status.is_null())
    assert isinstance(nested.node, ContributionFilter)
    assert nested.node.operand == revenue.node
    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity")).where(paid)
    assert isinstance(weighted.node, ContributionFilter)


def test_where_rejects_composite_cross_root_and_non_predicates(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    aov = amount.sum() / orders.count()

    with pytest.raises(SemanticExpressionError) as composite:
        aov.where(status == "paid")
    assert composite.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    assert composite.value.repair is not None
    assert "each source child Metric" in composite.value.repair.action
    with pytest.raises(SemanticExpressionError) as sampled:
        amount.sum().per(status).mean().where(status == "paid")
    assert sampled.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    with pytest.raises(SemanticExpressionError) as foreign:
        amount.sum().where(created_at.is_null())
    assert foreign.value.kind == ErrorKind.INVALID_WHERE_SCOPE.value
    assert foreign.value.received == "sales.customers"
    with pytest.raises(SemanticExpressionError) as raw:
        amount.sum().where(True)  # type: ignore[arg-type]
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def test_per_field_samples_reduce_through_per_nodes(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    revenue = amount.sum()

    samples = revenue.per(customer_id)
    assert type(samples) is MetricSamples
    axis = AxisBinding(entity=customer_id.root, field=customer_id.ref, path=(), role="field")
    assert samples.samples == (axis,)
    assert revenue.per((customer_id,)) == samples
    observed_mean = samples.mean()
    assert observed_mean.node == PerReduce(revenue.node, (axis,), "mean")
    assert (observed_mean.roots, observed_mean.unit) == (revenue.roots, "CNY")
    assert (
        repr(samples) == "MetricSamples SourceAggregate per sales.orders.customer_id (use .show())"
    )

    p95 = revenue.per(paid_at).quantile(0.95)
    assert isinstance(p95.node, PerReduce) and p95.node.q == 0.95
    pair = revenue.per((customer_id, status))
    assert len(pair.samples) == 2
    assert pair.count().unit == "1" and pair.count_distinct().unit == "1"
    for reduced in (pair.sum(), pair.min(), pair.max(), pair.median()):
        assert isinstance(reduced.node, PerReduce) and reduced.unit == "CNY"
    strict = samples.sum(nulls="error")
    assert isinstance(strict.node, PerReduce) and strict.node.nulls == "error"

    order_count = resolve(catalog, ms.ref.entity("sales.orders")).count()
    weighted = (revenue / order_count).per(customer_id).weighted_mean(weight=order_count)
    assert isinstance(weighted.node, PerWeightedMean)
    assert weighted.node.zero_denominator == "null"
    assert weighted.unit == "CNY"


def test_per_rejects_invalid_axes_weights_and_policies(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
    revenue = amount.sum()
    samples = revenue.per(customer_id)

    with pytest.raises(SemanticExpressionError) as empty:
        revenue.per(())
    assert empty.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as measure_axis:
        revenue.per(amount)  # type: ignore[arg-type]
    assert measure_axis.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as raw_axis:
        revenue.per(ms.ref.dimension("sales.orders.customer_id"))  # type: ignore[arg-type]
    assert raw_axis.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as duplicate:
        revenue.per((customer_id, customer_id))
    assert duplicate.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as row_weight:
        samples.weighted_mean(weight=amount)  # type: ignore[arg-type]
    assert row_weight.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    with pytest.raises(SemanticExpressionError) as policy:
        samples.mean(nulls="skip")  # type: ignore[arg-type]
    assert policy.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as zero:
        samples.weighted_mean(
            weight=revenue,
            zero_denominator="skip",  # type: ignore[arg-type]
        )
    assert zero.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    with pytest.raises(SemanticExpressionError) as quantile:
        samples.quantile(2.0)
    assert quantile.value.details["definition_kind"] == "invalid_value"
    with pytest.raises(SemanticExpressionError) as stage:
        samples + revenue  # type: ignore[operator]
    assert stage.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value


def _relationship(catalog: SemanticCatalog, name: str) -> Relationship:
    return resolve(catalog, ms.ref.relationship(f"sales.{name}"))


def test_via_binds_one_and_two_hop_time_paths_with_exact_refs(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    orders = resolve(catalog, ms.ref.entity("sales.orders"))
    buyer = _relationship(catalog, "orders_to_customers")
    region = _relationship(catalog, "customers_to_regions")
    created_at = resolve(catalog, ms.ref.time_dimension("sales.customers.created_at"))
    opened_at = resolve(catalog, ms.ref.time_dimension("sales.regions.opened_at"))

    buyer_created = created_at.via((buyer,))
    assert type(buyer_created) is TimeField
    assert buyer_created.path == (buyer,)
    assert (buyer_created.ref, buyer_created.root) == (created_at.ref, created_at.root)
    assert repr(buyer_created).endswith(" via 1 step(s) (use .show())")
    with pytest.raises(dataclasses.FrozenInstanceError):
        buyer_created.path = ()  # type: ignore[misc]

    one_hop = amount.sum(time=buyer_created)
    assert isinstance(one_hop.node, SourceAggregate)
    assert one_hop.node.time == TimeBinding(created_at.ref, (buyer.ref,))
    assert buyer.ref.version.startswith("sha256:")
    two_hop = orders.count(time=opened_at.via((buyer, region)))
    assert two_hop.node == EntityCount(
        orders.ref, TimeBinding(opened_at.ref, (buyer.ref, region.ref))
    )
    native = amount.sum()
    assert computation_fingerprint(one_hop.node) != computation_fingerprint(native.node)
    again = amount.sum(time=created_at.via((buyer,)))
    assert computation_fingerprint(again.node) == computation_fingerprint(one_hop.node)

    weighted = amount.weighted_mean(weight=_measure(catalog, "quantity"), time=buyer_created)
    assert isinstance(weighted.node, SourceWeightedMean)
    assert weighted.node.time == TimeBinding(created_at.ref, (buyer.ref,))
    filtered = amount.sum(time=buyer_created).where(
        resolve(catalog, ms.ref.dimension("sales.orders.status")) == "paid"
    )
    assert isinstance(filtered.node, ContributionFilter)


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
            created_at.via(path)  # type: ignore[arg-type]
        assert malformed.value.kind == ErrorKind.INVALID_TIME_PATH.value, label
        assert malformed.value.repair is not None
    with pytest.raises(SemanticExpressionError) as many:
        paid_at.via((fanout,))
    assert many.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert many.value.received == "one_to_many"
    with pytest.raises(SemanticExpressionError) as disconnected:
        opened_at.via((region, region))
    assert disconnected.value.kind == ErrorKind.INVALID_TIME_PATH.value
    with pytest.raises(SemanticExpressionError) as wrong_end:
        opened_at.via((buyer,))
    assert wrong_end.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert wrong_end.value.received == "sales.orders_to_customers to sales.customers"
    with pytest.raises(SemanticExpressionError) as twice:
        created_at.via((buyer,)).via((buyer,))
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
        credit.sum(time=opened_at.via((buyer, region)))
    assert start.value.kind == ErrorKind.INVALID_TIME_PATH.value
    assert start.value.received == "sales.orders_to_customers from sales.orders"
    assert credit.sum(time=opened_at.via((region,))).roots == (customers.ref,)
    with pytest.raises(SemanticExpressionError) as entity_start:
        customers.count(time=buyer_created)
    assert entity_start.value.kind == ErrorKind.INVALID_TIME_PATH.value

    assert (buyer_created == "2026-01-01").node.read.path == (buyer.ref,)
    assert buyer_created.is_null().node.read.path == (buyer.ref,)
    for misuse in (
        lambda: buyer_created.count(),
        lambda: amount.sum().per(buyer_created),
    ):
        with pytest.raises(SemanticExpressionError) as role:
            misuse()
        assert role.value.kind == ErrorKind.INVALID_TIME_PATH.value
        assert role.value.repair is not None
        assert "time=" in role.value.repair.action
        assert "scalar" in role.value.repair.action


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
    assert isinstance(metric, Metric)


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
        before, after = _measure(first, name), _measure(second, name)
        assert before.permission != after.permission
        assert before.ref != after.ref, name
    amount_ref = ms.ref.measure("sales.orders.amount")
    assert _physical(first, amount_ref).ref == _physical(second, amount_ref).ref


def test_unary_positive_keeps_the_metric_unchanged(catalog: SemanticCatalog) -> None:
    revenue = (
        _measure(catalog, "amount")
        .sum(nulls="error")
        .where(resolve(catalog, ms.ref.dimension("sales.orders.status")) == "paid")
    )

    positive = +revenue

    assert positive is revenue
    assert (positive.node, positive.roots, positive.unit) == (
        revenue.node,
        revenue.roots,
        revenue.unit,
    )


def test_oversized_integer_quantile_fails_structurally(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customer_id = resolve(catalog, ms.ref.dimension("sales.orders.customer_id"))
    # 10**5000 exceeds Python's default int-to-str digit limit.
    for huge in (10**400, 10**5000):
        with pytest.raises(SemanticExpressionError) as row:
            amount.quantile(huge)
        with pytest.raises(SemanticExpressionError) as samples:
            amount.sum().per(customer_id).quantile(huge)
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
        json.dumps(entry)  # Exercise the real serialized event shape without I/O.
        entries.append(entry)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_TELEMETRY", "on")
    monkeypatch.setattr(telemetry, "_instance_id", lambda root: "in-memory-test")
    monkeypatch.setattr(telemetry, "_write_entry", write_memory)
    test_oversized_integer_quantile_fails_structurally(catalog)
    assert len(entries) == 4
    for entry in entries:
        rendered = json.dumps(entry)
        assert "SemanticExpressionError" in rendered
        assert "invalid_expression_value" in rendered
        assert "marivo.repair.help_target" in rendered
    assert not (tmp_path / ".marivo").exists()


def test_updated_root_identity_does_not_reject_stored_expression_inputs(
    catalog: SemanticCatalog,
) -> None:
    old_amount = _measure(catalog, "amount")
    old_cost = _measure(catalog, "cost")
    old_graph = (old_amount - old_cost).sum().node
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
    assert old_amount.root.ref == cost.root.ref and old_amount.root != cost.root
    combined = old_amount - cost
    assert combined.node == RowSubtract(old_amount._as_row().node, cost._as_row().node)
    assert combined.unit == old_amount.unit
    assert combined.permission == old_amount.permission
    reduced = combined.sum(time=paid_at)
    assert isinstance(reduced.node, SourceAggregate)
    assert reduced.node.time == TimeBinding(paid_at.ref)
    filtered = reduced.where(status == "paid")
    assert isinstance(filtered.node, ContributionFilter)
    assert filtered.node.predicate == FieldCompare(status.ref, "eq", "paid")
    weighted = old_amount.weighted_mean(weight=cost, time=paid_at)
    assert isinstance(weighted.node, SourceWeightedMean)
    assert weighted.node.value == old_amount._as_row().node
    assert weighted.node.weight == cost._as_row().node
    assert computation_fingerprint(reduced.node) != old_fingerprint
    assert computation_fingerprint(old_graph) == old_fingerprint
    assert resolve(catalog, ms.ref.entity("sales.orders")).ref == old_amount.root


def test_permission_intersection_and_time_paths_compare_semantic_references(
    catalog: SemanticCatalog,
) -> None:
    amount = _measure(catalog, "amount")
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    old_axis = status.ref
    new_axis = ExactRef(old_axis.ref, "updated-definition")
    old_row = dataclasses.replace(amount._as_row(), permission=SumAxes((old_axis,)))
    new_row = dataclasses.replace(amount._as_row(), permission=SumAxes((new_axis,)))
    assert (old_row + new_row).permission == SumAxes((old_axis,))
    excluded = dataclasses.replace(new_row, permission=SumAllExcept((new_axis,)))
    assert isinstance((old_row + excluded).permission, SumNone)
    with pytest.raises(SemanticExpressionError):
        amount.sum().per((status, dataclasses.replace(status, ref=new_axis)))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))
    changed_root = ExactRef(paid_at.root.ref, "updated-entity-definition")
    path = Relationship(
        ExactRef(ms.ref.relationship("sales.self_role"), "relationship-definition"),
        changed_root,
        changed_root,
        "many_to_one",
    )
    routed = paid_at.via((path,))
    result = amount.sum(time=routed)
    assert isinstance(result.node, SourceAggregate)
    assert result.node.time == TimeBinding(paid_at.ref, (path.ref,))


def test_computation_keys_include_consumed_default_time_facts(catalog: SemanticCatalog) -> None:
    original = _measure(catalog, "amount")
    original_key = computation_fingerprint(original.sum().node)
    with loader_context(
        LoaderContext(datasource_irs=tuple(catalog._state.registry.datasources.values()))
    ):
        namespace: dict[str, object] = {}
        exec(_DOMAIN_PY, namespace)
        exec(_OBJECTS_PY.replace("is_default=True", "is_default=False"), namespace)
        changed = ms.finalize().resolve(ms.ref.measure("sales.orders.amount"))
    # The leaf is unchanged; its selected time binding is part of the computation key.
    assert changed.ref == original.ref
    assert original.default_time is not None and changed.default_time is None
    assert computation_fingerprint(changed.sum().node) != original_key
    assert computation_fingerprint(original.sum().node) == original_key
