"""Tests for metric-split Plan 2: additivity resolution + validation."""

from __future__ import annotations

from marivo.semantic import ir
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind
from tests.shared_fixtures import load_inline_semantic

# ---------------------------------------------------------------------------
# Task 1: IR — shared helpers
# ---------------------------------------------------------------------------


def test_additivity_bucket_maps_all_three() -> None:
    assert ir.additivity_bucket("additive") == "additive"
    assert ir.additivity_bucket("non_additive") == "non_additive"
    sa = ir.SemiAdditive(over="d.e.t", fold=ir.TimeFoldIR(kind="last"))
    assert ir.additivity_bucket(sa) == "semi_additive"


def test_composition_components_per_kind() -> None:
    assert ir.composition_components(ir.RatioComposition(numerator="d.a", denominator="d.b")) == {
        "numerator": "d.a",
        "denominator": "d.b",
    }
    assert ir.composition_components(ir.WeightedAverageComposition(value="d.v", weight="d.w")) == {
        "value": "d.v",
        "weight": "d.w",
    }
    lin = ir.LinearComposition(terms=(ir.LinearTerm("+", "d.a"), ir.LinearTerm("-", "d.b")))
    assert ir.composition_components(lin) == {"term0": "d.a", "term1": "d.b"}


# ---------------------------------------------------------------------------
# Task 2: errors + constraints — composition rename + measure kinds
# ---------------------------------------------------------------------------


def test_composition_and_measure_enum_members() -> None:
    assert ErrorKind.INVALID_COMPOSITION.value == "invalid_composition"
    assert ErrorKind.UNKNOWN_MEASURE.value == "unknown_measure"
    assert ErrorKind.MISSING_MEASURE_ADDITIVITY.value == "missing_measure_additivity"
    assert ErrorKind.INVALID_MEASURE_AGGREGATION.value == "invalid_measure_aggregation"
    assert ConstraintId.COMPOSITION_SHAPE.value == "composition_shape"
    assert not hasattr(ErrorKind, "INVALID_DECOMPOSITION")
    assert not hasattr(ConstraintId, "DECOMPOSITION_SHAPE")


# ---------------------------------------------------------------------------
# Task 3: loader — _resolve_metric_additivity resolution pass
# ---------------------------------------------------------------------------

_INLINE_METRICS = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="non_additive")
def unit_price(orders): return orders.unit_price

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
avg_price = ms.aggregate(measure=unit_price, agg="mean", name="avg_price")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
aov = ms.ratio(name="aov", numerator=revenue, denominator=order_count)
gross_plus = ms.linear(name="gross_plus", add=[revenue, revenue])
"""


def test_resolution_fills_additivity() -> None:
    with load_inline_semantic(_INLINE_METRICS) as result:
        reg = result.registry
        assert reg.metrics["test.revenue"].additivity == "additive"
        assert reg.metrics["test.avg_price"].additivity == "non_additive"
        assert reg.metrics["test.order_count"].additivity == "additive"
        assert reg.metrics["test.aov"].additivity == "non_additive"
        assert reg.metrics["test.gross_plus"].additivity == "additive"


# ---------------------------------------------------------------------------
# Task 4: validator — semi-additive over must be time dimension
# ---------------------------------------------------------------------------

_INLINE_BAD_OVER = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
snap = ms.entity(name="snap", datasource=wh, source=ms.table("snap"))

@ms.dimension(entity=snap)
def region(snap): return snap.region

@ms.dimension(kind="measure", entity=snap, additivity=ms.semi_additive(over="test.snap.region", fold="last"))
def quantity(snap): return snap.qty

ending = ms.aggregate(measure=quantity, agg="sum", name="ending")
"""


def test_semi_additive_over_must_be_time_dimension() -> None:
    with load_inline_semantic(_INLINE_BAD_OVER) as result:
        kinds = {e.kind for e in result.errors}
        assert ErrorKind.INVALID_STATUS_TIME_DIMENSION in kinds


# ---------------------------------------------------------------------------
# Task 5: validator — sum on non-additive measure is rejected
# ---------------------------------------------------------------------------

_INLINE_SUM_ON_INTENSIVE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
o = ms.entity(name="o", datasource=wh, source=ms.table("o"))

@ms.dimension(kind="measure", entity=o, additivity="non_additive")
def unit_price(o): return o.unit_price

bad = ms.aggregate(measure=unit_price, agg="sum", name="bad")
"""


def test_sum_on_non_additive_measure_is_rejected() -> None:
    with load_inline_semantic(_INLINE_SUM_ON_INTENSIVE) as result:
        kinds = {e.kind for e in result.errors}
        assert ErrorKind.INVALID_MEASURE_AGGREGATION in kinds


# ---------------------------------------------------------------------------
# Task 6: validator — composition component refs + cycle
# ---------------------------------------------------------------------------

_INLINE_BAD_COMPONENT = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
o = ms.entity(name="o", datasource=wh, source=ms.table("o"))

@ms.dimension(kind="measure", entity=o, additivity="additive")
def amount(o): return o.amount

rev = ms.aggregate(measure=amount, agg="sum", name="rev")
bad_ratio = ms.ratio(name="bad_ratio", numerator=rev, denominator="test.missing")
"""


def test_unknown_composition_component_is_reported() -> None:
    with load_inline_semantic(_INLINE_BAD_COMPONENT) as result:
        assert ErrorKind.MISSING_METRIC_REF in {e.kind for e in result.errors}


_INLINE_CYCLE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
o = ms.entity(name="o", datasource=wh, source=ms.table("o"))

@ms.dimension(kind="measure", entity=o, additivity="additive")
def amount(o): return o.amount

base = ms.aggregate(measure=amount, agg="sum", name="base")
a = ms.linear(name="a", add=[base, "test.b"])
b = ms.linear(name="b", add=[base, "test.a"])
"""


def test_metric_cycle_detected_over_composition() -> None:
    with load_inline_semantic(_INLINE_CYCLE) as result:
        assert ErrorKind.CROSS_MODEL_CYCLE in {e.kind for e in result.errors}
