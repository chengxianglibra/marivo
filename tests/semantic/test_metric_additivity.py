"""Metric additivity resolution and composition validation."""

from __future__ import annotations

import pytest

from marivo.refs import ref as ref_factory
from marivo.semantic import authoring, ir
from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError
from tests.shared_fixtures import load_inline_semantic

# ---------------------------------------------------------------------------
# IR — shared helpers
# ---------------------------------------------------------------------------


def test_additivity_bucket_maps_all_three() -> None:
    assert ir.additivity_bucket("additive") == "additive"
    assert ir.additivity_bucket("non_additive") == "non_additive"
    sa = ir.SemiAdditive(over="d.e.t", fold=ir.TimeFoldIR(kind="last"))
    assert ir.additivity_bucket(sa) == "semi_additive"


# ---------------------------------------------------------------------------
# loader — _resolve_metric_additivity resolution pass
# ---------------------------------------------------------------------------

_PHYSICAL = """import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("wh")
orders_ref = ms.entity(name="orders", datasource=wh, source=md.table("orders"))
refunds_ref = ms.entity(name="refunds", datasource=wh, source=md.table("refunds"))
amount_ref = ms.measure_column(
    name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all(), unit="CNY"
)
unit_price_ref = ms.measure_column(
    name="unit_price", entity=orders_ref, column="unit_price", additivity=ms.non_additive(),
    unit="CNY",
)
refund_ref = ms.measure_column(
    name="refund", entity=refunds_ref, column="refund", additivity=ms.additive_all(), unit="CNY"
)

orders = orders_ref
amount = amount_ref
unit_price = unit_price_ref
refund = refund_ref
"""

_PUBLISHED = (
    _PHYSICAL
    + """

@ms.metric()
def revenue():
    return amount.sum()


@ms.metric()
def avg_price():
    return unit_price.mean()


@ms.metric()
def order_count():
    return orders.count()


@ms.metric()
def aov():
    return revenue / order_count


@ms.metric()
def gross_plus():
    return revenue + revenue


@ms.metric()
def weighted_price():
    return unit_price.weighted_mean(weight=amount)
"""
)


def test_published_metrics_carry_units_from_their_computation() -> None:
    with load_inline_semantic(_PUBLISHED) as result:
        assert result.registry is not None, result.errors
        declared = result.registry.declared_metrics
        assert {path: consume_metric(metric).unit for path, metric in declared.items()} == {
            "test.revenue": "CNY",
            "test.avg_price": "CNY",
            "test.order_count": "1",
            "test.aov": "CNY",
            "test.gross_plus": "CNY",
            "test.weighted_price": "CNY",
        }


def test_weighted_mean_pairs_value_and_weight_on_one_root() -> None:
    source = (
        _PHYSICAL
        + """

@ms.metric()
def bad():
    return amount.weighted_mean(weight=refund)
"""
    )
    with load_inline_semantic(source) as result:
        errors = [e for e in result.errors if e.kind == ErrorKind.CROSS_ROOT_ROW_EXPRESSION]
        assert len(errors) == 1
        assert "test.orders weighted by test.refunds" in (errors[0].received or "")


# ---------------------------------------------------------------------------
# additivity coordinates require Dimension refs
# ---------------------------------------------------------------------------


def test_additivity_coordinate_must_be_dimension() -> None:
    entity = ref_factory.entity("test.snap")

    with pytest.raises(SemanticDecoratorError) as exc_info:
        authoring.additive_all(except_=(entity,))

    assert exc_info.value.kind == ErrorKind.INVALID_REF


# ---------------------------------------------------------------------------
# validator — sum on non-additive measure is rejected
# ---------------------------------------------------------------------------


def test_sum_on_non_additive_measure_is_rejected() -> None:
    source = (
        _PHYSICAL
        + """

@ms.metric()
def bad():
    return unit_price.sum()
"""
    )
    with load_inline_semantic(source) as result:
        kinds = {e.kind for e in result.errors}
        assert ErrorKind.MISSING_SUM_PERMISSION in kinds
        assert result.registry is None
