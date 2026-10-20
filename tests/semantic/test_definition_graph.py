"""Acceptance for the neutral definition graph (R1.1)."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal, localcontext

import pytest
from ibis.common.deferred import const, var

from marivo._definition.deferred import resolver_of
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    MeasureAssertion,
    MetricDeclaration,
    MetricExpression,
    MetricNode,
    PredicateExpression,
    RowExpression,
    RowField,
    SourceAggregate,
    TimeBinding,
)
from marivo._definition.graph import children, computation_fingerprint, walk
from marivo._definition.relations import (
    LiteralPopulation,
    PopulationContains,
    PopulationProgram,
    SourceIdentities,
)
from marivo._definition.types import (
    Authorization,
    AxisBinding,
    DefinitionGraphError,
    ExactRef,
    Owner,
    SumAll,
    SumAllExcept,
)
from marivo.refs import ref

ORDERS = ExactRef(ref.entity("sales.orders"), "v1")
PAID_AT = ExactRef(ref.time_dimension("sales.orders.paid_at"), "v1")
PAID = TimeBinding(PAID_AT)
CUSTOMER = AxisBinding(
    entity=ExactRef(ref.entity("sales.customers"), "v1"), field=None, path=(), role="identity"
)
ORDER_IDENTITY = AxisBinding(entity=ORDERS, field=None, path=(), role="identity")
TIER = AxisBinding(
    entity=CUSTOMER.entity,
    field=ExactRef(ref.dimension("sales.customers.tier"), "v1"),
    path=(),
    role="field",
)


def _field(name: str, *, unit: str | None = "CNY") -> RowField:
    return RowField(
        ref=ExactRef(ref.measure(f"sales.orders.{name}"), "v1"),
        root=ORDERS,
        unit=unit,
        permission=SumAll(),
    )


def _combine(left: MetricNode, right: MetricNode) -> MetricExpression:
    return MetricExpression(resolver_of(var("slot_0") + var("slot_1")), (left, right))


def _constant(value: int | float | Decimal) -> MetricExpression:
    return MetricExpression(resolver_of(const(value)), ())


def _divide(
    left: MetricNode, right: MetricNode, *, zero_denominator: str = "null"
) -> MetricExpression:
    return MetricExpression(
        resolver_of(var("slot_0").divide(var("slot_1"), zero_denominator=zero_denominator)),
        (left, right),
    )


def _margin() -> SourceAggregate:
    return SourceAggregate(
        RowExpression(
            resolver_of(var("slot_0") - var("slot_1")), (_field("amount"), _field("cost"))
        ),
        "sum",
        time=PAID,
    )


def _no_declarations(_ref: ExactRef) -> MetricNode | None:
    return None


def test_repeated_use_keeps_two_ordered_edges() -> None:
    margin = _margin()
    doubled = _combine(margin, margin)

    edges = children(doubled)
    closure = walk([doubled], resolve=_no_declarations)

    assert [(edge.role, edge.index) for edge in edges] == [("operand", 0), ("operand", 1)]
    assert edges[0].child is edges[1].child
    assert [edge for parent, edge in closure.edges if parent is doubled] == list(edges)
    assert closure.order[-1] is doubled


def test_declaration_metadata_does_not_change_computation_fingerprint() -> None:
    definition = _margin()
    named = MetricDeclaration(name="gross_margin", definition=definition)
    renamed = MetricDeclaration(
        name="trial_margin", definition=definition, domain=ref.domain("sales")
    )

    assert computation_fingerprint(named.definition) == computation_fingerprint(renamed.definition)
    assert computation_fingerprint(definition) == computation_fingerprint(_margin())


def test_measure_assertion_enters_fingerprint() -> None:
    product = RowExpression(
        resolver_of(var("slot_0") - var("slot_1")),
        (_field("amount", unit=None), _field("cost", unit=None)),
    )
    asserted = MeasureAssertion(
        product,
        unit="CNY",
        permission=SumAll(),
        authorization=Authorization(ExactRef(ref.measure("sales.orders.order_margin"), "v1")),
    )

    assert computation_fingerprint(asserted) != computation_fingerprint(product)
    with pytest.raises(DefinitionGraphError, match="unit or sum permission"):
        MeasureAssertion(product, unit=None, permission=None, authorization=asserted.authorization)


def test_zero_denominator_policy_is_part_of_the_computation() -> None:
    revenue = SourceAggregate(_field("amount"), "sum", time=PAID)
    cost = SourceAggregate(_field("cost"), "sum", time=PAID, nulls="error")

    default = _divide(cost, revenue)
    explicit_null = _divide(cost, revenue, zero_denominator="null")
    strict = _divide(cost, revenue, zero_denominator="error")

    assert computation_fingerprint(default) == computation_fingerprint(explicit_null)
    assert computation_fingerprint(default) != computation_fingerprint(strict)


def test_declared_metric_cycle_is_located() -> None:
    self_ref = ExactRef(ref.metric("sales.cohort_revenue"), "v1")
    definition = _combine(_constant(1), DeclaredMetricRef(self_ref))

    def resolve(target: ExactRef) -> MetricNode | None:
        return definition if target == self_ref else None

    with pytest.raises(DefinitionGraphError) as raised:
        walk([DeclaredMetricRef(self_ref)], resolve=resolve)
    assert raised.value.kind == "cycle"
    assert raised.value.path == (
        "DeclaredMetricRef.metric[0]",
        "MetricExpression.operand[1]",
    )

    def resolve_fresh(target: ExactRef) -> MetricNode | None:
        return _combine(_constant(1), DeclaredMetricRef(self_ref)) if target == self_ref else None

    with pytest.raises(DefinitionGraphError) as fresh:
        walk([DeclaredMetricRef(self_ref)], resolve=resolve_fresh)
    assert fresh.value.kind == "cycle"
    assert fresh.value.path == raised.value.path

    with pytest.raises(DefinitionGraphError) as unresolved:
        walk([DeclaredMetricRef(self_ref)], resolve=_no_declarations)
    assert unresolved.value.kind == "unresolved"


def test_walk_keeps_equal_but_distinct_typed_nodes() -> None:
    integer = _constant(1)
    real = _constant(1.0)
    mixed = _combine(integer, real)

    closure = walk([mixed], resolve=_no_declarations)

    assert computation_fingerprint(integer) != computation_fingerprint(real)
    assert [id(node) for node in closure.order] == [id(integer), id(real), id(mixed)]
    visited = {id(node) for node in closure.order}
    assert all(id(edge.child) in visited for _, edge in closure.edges)


def test_walk_visits_each_shared_dag_node_once() -> None:
    shared: MetricNode = _constant(1)
    for _ in range(64):
        shared = _combine(shared, shared)

    closure = walk([shared], resolve=_no_declarations)

    assert len(closure.order) == 65
    assert len(closure.edges) == 128


def test_ownership_conflict_and_unknown_rule_version_fail() -> None:
    revenue = SourceAggregate(_field("amount"), "sum", time=PAID)
    first = PopulationProgram(SourceIdentities(CUSTOMER.entity), Owner("session-a", "owner-a"))
    second = PopulationProgram(SourceIdentities(CUSTOMER.entity), Owner("session-b", "owner-b"))
    combined = _combine(
        ContributionFilter(
            revenue, PredicateExpression(resolver_of(var("slot_0")), (PopulationContains(first),))
        ),
        ContributionFilter(
            revenue, PredicateExpression(resolver_of(var("slot_0")), (PopulationContains(second),))
        ),
    )

    with pytest.raises(DefinitionGraphError) as conflict:
        walk([combined], resolve=_no_declarations)
    assert conflict.value.kind == "ownership_conflict"
    assert conflict.value.path == (
        "MetricExpression.operand[1]",
        "ContributionFilter.predicate[1]",
        "PredicateExpression.predicate[0]",
        "PopulationContains.population[0]",
    )

    stale = SourceAggregate(_field("amount"), "sum", time=PAID, rule_version=99)
    with pytest.raises(DefinitionGraphError) as version:
        walk([_divide(revenue, stale)], resolve=_no_declarations)
    assert version.value.kind == "unknown_rule_version"
    assert version.value.path == ("MetricExpression.operand[1]",)


def test_literal_population_keys_match_axes_and_distinguish_empty_shapes() -> None:
    population = LiteralPopulation(axes=(TIER,), keys=(("b",), ("a",), ("b",)), version="v1")
    assert population.keys == (("a",), ("b",))
    assert LiteralPopulation(axes=(TIER,), keys=((1,), (True,)), version="v1").keys == (
        (True,),
        (1,),
    )

    with pytest.raises(DefinitionGraphError, match="1 values"):
        LiteralPopulation(axes=(TIER,), keys=(("a", "b"),), version="v1")

    single_sample = LiteralPopulation(axes=(), keys=((),), version="v1")
    no_samples = LiteralPopulation(axes=(), keys=(), version="v1")
    assert single_sample.keys == ((),)
    assert computation_fingerprint(single_sample) != computation_fingerprint(no_samples)


def test_identity_axis_keys_are_complete_primary_key_tuples() -> None:
    composite = LiteralPopulation(
        axes=(ORDER_IDENTITY, TIER),
        keys=((("t2", "o1"), "gold"), (("t1", "o9"), None), (("t1", "o9"), None)),
        version="v1",
    )
    assert composite.keys == ((("t1", "o9"), None), (("t2", "o1"), "gold"))

    with pytest.raises(DefinitionGraphError, match="complete primary-key tuple"):
        LiteralPopulation(axes=(ORDER_IDENTITY,), keys=(("o1",),), version="v1")
    with pytest.raises(DefinitionGraphError, match="cannot be None"):
        LiteralPopulation(axes=(ORDER_IDENTITY,), keys=((("t1", None),),), version="v1")
    with pytest.raises(DefinitionGraphError, match="same number of components"):
        LiteralPopulation(axes=(ORDER_IDENTITY,), keys=((("t1", "o1"),), (("o2",),)), version="v1")
    with pytest.raises(DefinitionGraphError, match="field axis"):
        LiteralPopulation(axes=(TIER,), keys=((("gold",),),), version="v1")


def test_decimal_tokens_are_exact_and_independent_of_context() -> None:
    first = Decimal("123456789012345678901234567891")
    second = Decimal("123456789012345678901234567892")

    assert computation_fingerprint(_constant(first)) != computation_fingerprint(_constant(second))
    population = LiteralPopulation(axes=(TIER,), keys=((first,), (second,)), version="v1")
    assert population.keys == ((first,), (second,))

    precise = _constant(Decimal("1.2345"))
    expected = computation_fingerprint(precise)
    with localcontext() as context:
        context.prec = 3
        assert computation_fingerprint(precise) == expected
    assert computation_fingerprint(_constant(Decimal("1.50"))) == computation_fingerprint(
        _constant(Decimal("1.5"))
    )


def test_definition_records_reject_invalid_names_and_values() -> None:
    with pytest.raises(DefinitionGraphError, match="declaration name"):
        MetricDeclaration(name="GrossMargin", definition=_margin())
    with pytest.raises(DefinitionGraphError, match="finite"):
        LiteralPopulation(axes=(TIER,), keys=((float("nan"),),), version="v1")
    with pytest.raises(DefinitionGraphError, match="role='identity'"):
        AxisBinding(entity=ORDERS, field=PAID_AT, path=(), role="identity")


def test_time_binding_path_is_part_of_the_computation() -> None:
    buyer = ExactRef(ref.relationship("sales.orders_to_customers"), "v1")
    created_at = ExactRef(ref.time_dimension("sales.customers.created_at"), "v1")
    native = SourceAggregate(_field("amount"), "sum", time=TimeBinding(created_at))
    via = SourceAggregate(_field("amount"), "sum", time=TimeBinding(created_at, (buyer,)))

    assert computation_fingerprint(native) != computation_fingerprint(via)
    with pytest.raises(DefinitionGraphError, match="time_dimension"):
        TimeBinding(ExactRef(ref.dimension("sales.orders.status"), "v1"))
    with pytest.raises(DefinitionGraphError, match="relationship references"):
        TimeBinding(created_at, (ORDERS,))


def test_row_comparisons_are_predicate_edges_and_sum_except_needs_an_axis() -> None:
    amount, cost = _field("amount"), _field("cost")
    row_predicate = RowExpression(resolver_of((var("slot_0") - var("slot_1")) > 0), (amount, cost))
    predicate = PredicateExpression(resolver_of(var("slot_0")), (row_predicate,))
    revenue = SourceAggregate(amount, "sum", time=PAID)
    filtered = ContributionFilter(revenue, predicate)
    edges = children(filtered)
    assert [(edge.role, edge.index) for edge in edges] == [("operand", 0), ("predicate", 1)]
    assert edges[1].child is predicate
    assert [(edge.role, edge.index) for edge in children(predicate)] == [("predicate", 0)]
    assert children(predicate)[0].child is row_predicate
    assert children(row_predicate)[0].child is amount
    inclusive = PredicateExpression(
        resolver_of(var("slot_0")),
        (RowExpression(resolver_of((var("slot_0") - var("slot_1")) >= 0), (amount, cost)),),
    )
    assert computation_fingerprint(filtered) != computation_fingerprint(
        ContributionFilter(revenue, inclusive)
    )
    with pytest.raises(DefinitionGraphError, match="at least one fixed axis"):
        SumAllExcept(())


def test_definition_layer_imports_no_semantic_analysis_or_engine_modules() -> None:
    probe = (
        "import sys\n"
        "import marivo._definition.graph\n"
        "forbidden = ('marivo.semantic', 'marivo.analysis', 'marivo.datasource')\n"
        "print(sorted(name for name in sys.modules"
        " if any(name == root or name.startswith(root + '.') for root in forbidden)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
