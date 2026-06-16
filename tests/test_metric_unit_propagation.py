"""End-to-end tests for unit on measure dimensions and loader propagation."""

from __future__ import annotations

import dataclasses

import pytest

from marivo.semantic.ir import (
    AiContextIR,
    DimensionIR,
    DimensionKind,
    SourceLocation,
)


def test_dimension_ir_has_unit_field() -> None:
    names = {f.name for f in dataclasses.fields(DimensionIR)}
    assert "unit" in names


def test_dimension_ir_unit_only_on_measure() -> None:
    loc = SourceLocation(file="t.py", line=1)
    base = {
        "semantic_id": "sales.orders.amount",
        "domain": "sales",
        "entity": "sales.orders",
        "name": "amount",
        "description": None,
        "ai_context": AiContextIR(),
        "is_time_dimension": False,
        "data_type": None,
        "granularity": None,
        "required_prefix": None,
        "python_symbol": "amount",
        "location": loc,
    }
    # measure + unit is allowed
    measure = DimensionIR(kind=DimensionKind.MEASURE, unit="CNY", **base)
    assert measure.unit == "CNY"
    # categorical + unit is rejected
    with pytest.raises(ValueError, match="unit is only valid on measure"):
        DimensionIR(kind=DimensionKind.CATEGORICAL, unit="CNY", **base)


_DIM_UNIT = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue", unit="USD")
"""


def test_dimension_unit_stored_on_ir() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_DIM_UNIT) as result:
        assert result.registry.fields["test.orders.amount"].unit == "CNY"


def test_aggregate_unit_override_lands_on_metric_ir() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_DIM_UNIT) as result:
        # author override wins over the measure-derived value
        assert result.registry.metrics["test.revenue"].unit == "USD"


def test_dimension_unit_on_categorical_is_rejected() -> None:
    # The measure-only guard fires at the ms.dimension(...) factory call, before
    # entity resolution, so it needs an active loader context (authoring_session)
    # but no real entity. Mirrors the decorator-guard tests in
    # tests/test_metric_split_foundation.py.
    import marivo.semantic as ms
    from marivo.semantic.errors import SemanticDecoratorError
    from tests.shared_fixtures import authoring_session

    with (
        authoring_session(domain="sales"),
        pytest.raises(SemanticDecoratorError, match="unit is only valid on kind='measure'"),
    ):
        ms.dimension(entity="sales.orders", unit="CNY")


_INLINE_UNITS = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="non_additive")
def latency(orders): return orders.latency_ms

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
avg_latency = ms.aggregate(measure=latency, agg="mean", name="avg_latency")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
margin = ms.ratio(name="margin", numerator=revenue, denominator=revenue)
arpu = ms.ratio(name="arpu", numerator=revenue, denominator=order_count)
net = ms.linear(name="net", add=[revenue, revenue])
"""


def test_tier1_unit_preserves_measure_unit() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNITS) as result:
        reg = result.registry
        assert reg.metrics["test.revenue"].unit == "CNY"  # sum preserves
        assert reg.metrics["test.avg_latency"].unit is None  # measure unannotated
        assert reg.metrics["test.order_count"].unit is None  # count -> no noun


def test_derived_unit_algebra() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNITS) as result:
        reg = result.registry
        assert reg.metrics["test.margin"].unit == "1"  # CNY / CNY cancels
        assert reg.metrics["test.arpu"].unit is None  # CNY / None -> no compound
        assert reg.metrics["test.net"].unit == "CNY"  # CNY + CNY


_INLINE_UNIT_OVERRIDE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
share = ms.ratio(name="share", numerator=revenue, denominator=revenue, unit="%")
"""


def test_author_override_wins_over_derivation() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNIT_OVERRIDE) as result:
        # would derive "1", but the author declared "%"
        assert result.registry.metrics["test.share"].unit == "%"


def test_linear_unit_error_taxonomy_registered() -> None:
    from marivo.semantic.constraints import ConstraintId, get_constraint
    from marivo.semantic.errors import ErrorKind

    assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value == "incommensurable_linear_units"
    assert ConstraintId.LINEAR_UNIT_COMMENSURABLE.value == "linear_unit_commensurable"
    assert get_constraint(ConstraintId.LINEAR_UNIT_COMMENSURABLE) is not None


_INLINE_LINEAR_CONFLICT = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="{order}")
def lines(orders): return orders.line_count

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
line_total = ms.aggregate(measure=lines, agg="sum", name="line_total")
bad = ms.linear(name="bad", add=[revenue, line_total])
"""


def test_linear_incommensurable_units_rejected() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_CONFLICT) as result:
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS in {e.kind for e in result.errors}


_INLINE_LINEAR_OK = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

gross = ms.aggregate(measure=amount, agg="sum", name="gross")
refunds = ms.aggregate(measure=amount, agg="sum", name="refunds")
net = ms.linear(name="net", add=[gross], subtract=[refunds])
"""


def test_linear_same_unit_no_error() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_OK) as result:
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS not in {e.kind for e in result.errors}


_INLINE_LINEAR_OVERRIDE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="{order}")
def lines(orders): return orders.line_count

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
line_total = ms.aggregate(measure=lines, agg="sum", name="line_total")
bad = ms.linear(name="bad", add=[revenue, line_total], unit="CNY")
"""


def test_linear_override_does_not_suppress_conflict() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_OVERRIDE) as result:
        # author labelled the result CNY, but CNY + {order} is still invalid
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS in {e.kind for e in result.errors}
