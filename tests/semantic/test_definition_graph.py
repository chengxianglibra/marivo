"""Acceptance for the neutral definition graph (R1.1)."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal, localcontext

import pytest

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    FieldIsNull,
    MeasureAssertion,
    MetricAdd,
    MetricConstant,
    MetricDeclaration,
    MetricDivide,
    MetricNode,
    PredAnd,
    RowCompare,
    RowField,
    RowSubtract,
    SourceAggregate,
    TimeBinding,
)
from marivo._definition.graph import children, computation_fingerprint, walk
from marivo._definition.relations import (
    LiteralPopulation,
    ObserveQuery,
    PopulationProgram,
    QueryKeys,
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


def _margin() -> SourceAggregate:
    return SourceAggregate(RowSubtract(_field("amount"), _field("cost")), "sum", time=PAID)


def _no_declarations(_ref: ExactRef) -> MetricNode | None:
    return None


def test_repeated_use_keeps_two_ordered_edges() -> None:
    margin = _margin()
    doubled = MetricAdd(margin, margin)

    edges = children(doubled)
    closure = walk([doubled], resolve=_no_declarations)

    assert [(edge.role, edge.index) for edge in edges] == [("left", 0), ("right", 1)]
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
    product = RowSubtract(_field("amount", unit=None), _field("cost", unit=None))
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

    default = MetricDivide(cost, revenue)
    explicit_null = MetricDivide(cost, revenue, zero_denominator="null")
    strict = MetricDivide(cost, revenue, zero_denominator="error")

    assert computation_fingerprint(default) == computation_fingerprint(explicit_null)
    assert computation_fingerprint(default) != computation_fingerprint(strict)


def test_metric_population_query_cycle_is_located() -> None:
    self_ref = ExactRef(ref.metric("sales.cohort_revenue"), "v1")
    query = ObserveQuery(
        metrics=(("revenue", DeclaredMetricRef(self_ref)),), by=(CUSTOMER,), owner=None
    )
    cohort = PopulationProgram(root=QueryKeys(query=query, axes=(CUSTOMER,)), owner=None)
    definition = ContributionFilter(SourceAggregate(_field("amount"), "sum", time=PAID), cohort)

    def resolve(target: ExactRef) -> MetricNode | None:
        return definition if target == self_ref else None

    with pytest.raises(DefinitionGraphError) as raised:
        walk([DeclaredMetricRef(self_ref)], resolve=resolve)

    assert raised.value.kind == "cycle"
    assert raised.value.path == (
        "DeclaredMetricRef.metric[0]",
        "ContributionFilter.predicate[1]",
        "PopulationProgram.population[0]",
        "QueryKeys.query[0]",
        "ObserveQuery.metric[0]",
    )

    def resolve_fresh(target: ExactRef) -> MetricNode | None:
        if target != self_ref:
            return None
        return ContributionFilter(SourceAggregate(_field("amount"), "sum", time=PAID), cohort)

    with pytest.raises(DefinitionGraphError) as fresh:
        walk([DeclaredMetricRef(self_ref)], resolve=resolve_fresh)
    assert fresh.value.kind == "cycle"
    assert fresh.value.path == raised.value.path

    with pytest.raises(DefinitionGraphError) as unresolved:
        walk([DeclaredMetricRef(self_ref)], resolve=_no_declarations)
    assert unresolved.value.kind == "unresolved"


def test_walk_keeps_equal_but_distinct_typed_nodes() -> None:
    integer = MetricConstant(1)
    real = MetricConstant(1.0)
    mixed = MetricAdd(integer, real)

    closure = walk([mixed], resolve=_no_declarations)

    assert computation_fingerprint(integer) != computation_fingerprint(real)
    assert [id(node) for node in closure.order] == [id(integer), id(real), id(mixed)]
    visited = {id(node) for node in closure.order}
    assert all(id(edge.child) in visited for _, edge in closure.edges)


def test_walk_visits_each_shared_dag_node_once() -> None:
    shared: MetricNode = MetricConstant(1)
    for _ in range(64):
        shared = MetricAdd(shared, shared)

    closure = walk([shared], resolve=_no_declarations)

    assert len(closure.order) == 65
    assert len(closure.edges) == 128


def test_ownership_conflict_and_unknown_rule_version_fail() -> None:
    revenue = SourceAggregate(_field("amount"), "sum", time=PAID)
    first = PopulationProgram(SourceIdentities(CUSTOMER.entity), Owner("session-a", "owner-a"))
    second = PopulationProgram(SourceIdentities(CUSTOMER.entity), Owner("session-b", "owner-b"))
    combined = MetricAdd(ContributionFilter(revenue, first), ContributionFilter(revenue, second))

    with pytest.raises(DefinitionGraphError) as conflict:
        walk([combined], resolve=_no_declarations)
    assert conflict.value.kind == "ownership_conflict"
    assert conflict.value.path == ("MetricAdd.right[1]", "ContributionFilter.predicate[1]")

    stale = SourceAggregate(_field("amount"), "sum", time=PAID, rule_version=99)
    with pytest.raises(DefinitionGraphError) as version:
        walk([MetricDivide(revenue, stale)], resolve=_no_declarations)
    assert version.value.kind == "unknown_rule_version"
    assert version.value.path == ("MetricDivide.denominator[1]",)


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

    assert computation_fingerprint(MetricConstant(first)) != computation_fingerprint(
        MetricConstant(second)
    )
    population = LiteralPopulation(axes=(TIER,), keys=((first,), (second,)), version="v1")
    assert population.keys == ((first,), (second,))

    precise = MetricConstant(Decimal("1.2345"))
    expected = computation_fingerprint(precise)
    with localcontext() as context:
        context.prec = 3
        assert computation_fingerprint(precise) == expected
    assert computation_fingerprint(MetricConstant(Decimal("1.50"))) == computation_fingerprint(
        MetricConstant(Decimal("1.5"))
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
    margin = RowSubtract(_field("amount"), _field("cost"))
    status = ExactRef(ref.dimension("sales.orders.status"), "v1")
    positive = RowCompare(margin, "gt", 0)
    predicate = PredAnd((FieldIsNull(status), positive))
    filtered = ContributionFilter(SourceAggregate(_field("amount"), "sum", time=PAID), predicate)

    edges = children(filtered)
    assert [(edge.role, edge.index) for edge in edges] == [("operand", 0), ("predicate", 1)]
    assert edges[1].child is margin
    revenue = SourceAggregate(_field("amount"), "sum", time=PAID)
    assert computation_fingerprint(
        ContributionFilter(revenue, RowCompare(margin, "gt", 0))
    ) != computation_fingerprint(ContributionFilter(revenue, RowCompare(margin, "ge", 0)))
    with pytest.raises(DefinitionGraphError, match="finite"):
        RowCompare(margin, "gt", float("inf"))
    with pytest.raises(DefinitionGraphError, match="at least one fixed axis"):
        SumAllExcept(())


def test_definition_layer_imports_no_semantic_analysis_or_engine_modules() -> None:
    probe = (
        "import sys\n"
        "import marivo._definition.graph\n"
        "forbidden = ('marivo.semantic', 'marivo.analysis', 'marivo.datasource', 'ibis')\n"
        "print(sorted(name for name in sys.modules"
        " if any(name == root or name.startswith(root + '.') for root in forbidden)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
