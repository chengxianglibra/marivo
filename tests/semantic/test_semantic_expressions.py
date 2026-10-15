"""Catalog resolution into private semantic expression values (R1.2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, TypeAlias

import pytest

import marivo.semantic as ms
from marivo._definition.expressions import (
    ContributionFilter,
    EntityCount,
    FieldCompare,
    FieldIsNull,
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
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticError, SemanticExpressionError
from marivo.semantic.expressions import (
    Dimension,
    Entity,
    Measure,
    Metric,
    MetricSamples,
    NumericRowExpr,
    Predicate,
    Relationship,
    TimeField,
    resolve,
)
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


@pytest.fixture
def catalog(semantic_project_factory: ProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/objects.py": _OBJECTS_PY}
    )
    return SemanticCatalog(project)


def test_fields_resolve_with_exact_versions_and_owning_root(catalog: SemanticCatalog) -> None:
    amount = resolve(catalog, ms.ref.measure("sales.orders.amount"))
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
    again = resolve(catalog, ms.ref.measure("sales.orders.amount"))
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
    assert resolve(first, amount_ref).ref.version != resolve(second, amount_ref).ref.version
    assert resolve(first, status_ref).ref.version == resolve(second, status_ref).ref.version


def test_measure_permissions_map_declared_additivity(catalog: SemanticCatalog) -> None:
    status = resolve(catalog, ms.ref.dimension("sales.orders.status"))
    paid_at = resolve(catalog, ms.ref.time_dimension("sales.orders.paid_at"))

    assert resolve(catalog, ms.ref.measure("sales.orders.amount")).permission == SumAll()
    assert resolve(catalog, ms.ref.measure("sales.orders.unit_price")).permission == SumNone()
    quantity = resolve(catalog, ms.ref.measure("sales.orders.quantity"))
    assert quantity.permission == SumAxes((status.ref,))
    assert quantity.unit is None
    balance = resolve(catalog, ms.ref.measure("sales.orders.balance"))
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
    amount = resolve(catalog, ms.ref.measure("sales.orders.amount"))

    with pytest.raises(dataclasses.FrozenInstanceError):
        amount.unit = "USD"  # type: ignore[misc]
    rendered = repr(amount)
    assert rendered.startswith("Measure sales.orders.amount@sha256:")
    assert "\n" not in rendered and len(rendered) < 80
    assert isinstance(amount.ref, ExactRef)


def test_unsupported_and_absent_refs_fail_structurally(catalog: SemanticCatalog) -> None:
    with pytest.raises(SemanticExpressionError) as metric:
        resolve(catalog, ms.ref.metric("sales.revenue"))  # type: ignore[arg-type]
    assert metric.value.kind == ErrorKind.INVALID_EXPRESSION_STAGE.value
    assert metric.value.received == "metric"
    assert metric.value.repair is not None

    with pytest.raises(SemanticExpressionError) as raw:
        resolve(catalog, "sales.orders.amount")  # type: ignore[call-overload]
    assert raw.value.kind == ErrorKind.INVALID_EXPRESSION_VALUE.value
    assert raw.value.received == "str"

    with pytest.raises(SemanticError) as absent:
        resolve(catalog, ms.ref.measure("sales.orders.missing"))
    assert absent.value.kind == ErrorKind.NOT_FOUND.value


def _measure(catalog: SemanticCatalog, name: str) -> Measure:
    return resolve(catalog, ms.ref.measure(f"sales.orders.{name}"))


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
    credit = resolve(catalog, ms.ref.measure("sales.customers.credit"))

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
    assert repr(combined) == "Predicate PredOr on sales.orders"


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
    credit = resolve(catalog, ms.ref.measure("sales.customers.credit"))
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
    assert repr(samples) == "MetricSamples SourceAggregate per sales.orders.customer_id"

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
    assert repr(buyer_created).endswith(" via 1 step(s)")
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


def test_via_time_requires_root_start_and_stays_a_time_role(catalog: SemanticCatalog) -> None:
    amount = _measure(catalog, "amount")
    customers = resolve(catalog, ms.ref.entity("sales.customers"))
    credit = resolve(catalog, ms.ref.measure("sales.customers.credit"))
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

    for misuse in (
        lambda: buyer_created == "2026-01-01",
        lambda: buyer_created.is_null(),
        lambda: buyer_created.count(),
        lambda: amount.sum().per(buyer_created),
    ):
        with pytest.raises(SemanticExpressionError) as role:
            misuse()
        assert role.value.kind == ErrorKind.INVALID_TIME_PATH.value
        assert role.value.repair is not None
        assert "only as time=" in role.value.repair.action


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
    assert resolve(first, amount_ref).ref == resolve(second, amount_ref).ref


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
