"""Tests for the metric-split foundation (Plan 1: IR + authoring).

Covers: Additivity/SemiAdditive/AggKind types, Composition union,
MetricIR rewrite, DimensionIR.additivity, _BaseRef.__call__ teaching
error, and the full authoring surface (ms.semi_additive, ms.aggregate,
@ms.simple_metric, ms.ratio/ms.weighted_average/ms.linear,
dimension(additivity=)).
"""

import pytest

import marivo.semantic as ms
from marivo.semantic import authoring, ir
from marivo.semantic.errors import SemanticDecoratorError
from marivo.semantic.ir import AiContextIR, ProvenanceIR, SourceLocation
from tests.shared_fixtures import authoring_session

# ---------------------------------------------------------------------------
# Task 2: Additivity/SemiAdditive/AggKind types
# ---------------------------------------------------------------------------


def test_semi_additive_holds_axis_and_fold():
    sa = ir.SemiAdditive(over="sales.orders.order_date", fold=ir.TimeFoldIR(kind="last"))
    assert sa.over == "sales.orders.order_date"
    assert sa.fold.kind == "last"


def test_additivity_accepts_literals_and_semi_variant():
    # Literals and the variant are all valid Additivity values (runtime smoke).
    values = [
        "additive",
        "non_additive",
        ir.SemiAdditive(over="d.e.t", fold=ir.TimeFoldIR(kind="max")),
    ]
    assert values[0] == "additive"
    assert isinstance(values[2], ir.SemiAdditive)


# ---------------------------------------------------------------------------
# Task 3: Composition union
# ---------------------------------------------------------------------------


def test_composition_variants_carry_roles():
    r = ir.RatioComposition(numerator="d.lost", denominator="d.total")
    assert (r.kind, r.numerator, r.denominator) == ("ratio", "d.lost", "d.total")

    w = ir.WeightedAverageComposition(value="d.rate", weight="d.sessions")
    assert (w.kind, w.value, w.weight) == ("weighted_average", "d.rate", "d.sessions")

    lin = ir.LinearComposition(terms=(ir.LinearTerm("+", "d.a"), ir.LinearTerm("-", "d.b")))
    assert lin.kind == "linear"
    assert [(t.sign, t.metric) for t in lin.terms] == [("+", "d.a"), ("-", "d.b")]


# ---------------------------------------------------------------------------
# Task 4: MetricIR rewrite + invariants
# ---------------------------------------------------------------------------


def _loc():
    return SourceLocation(file="t.py", line=1)


def _mk(**over):
    base = {
        "semantic_id": "d.m",
        "domain": "d",
        "name": "m",
        "metric_type": "simple",
        "entities": ("d.e",),
        "aggregation": None,
        "measure": None,
        "composition": None,
        "additivity": "additive",
        "provenance": ProvenanceIR(source_sql=None, source_dialect=None),
        "description": None,
        "ai_context": AiContextIR(),
        "body_ast_hash": "h",
        "python_symbol": "m",
        "location": _loc(),
    }
    base.update(over)
    return ir.MetricIR(**base)


def test_metricir_tier2_simple_ok():
    m = _mk()  # body-form simple, declared additivity
    assert m.metric_type == "simple" and m.composition is None


def test_metricir_simple_rejects_composition():
    with pytest.raises(ValueError):
        _mk(composition=ir.RatioComposition(numerator="d.a", denominator="d.b"))


def test_metricir_tier1_requires_aggregation_and_measure_together():
    with pytest.raises(ValueError):
        _mk(aggregation="sum", measure=None, additivity=None)  # measure missing


def test_metricir_derived_ok_and_rejects_entities():
    d = _mk(
        metric_type="derived",
        entities=(),
        aggregation=None,
        measure=None,
        additivity=None,
        composition=ir.RatioComposition(numerator="d.a", denominator="d.b"),
    )
    assert d.metric_type == "derived"
    with pytest.raises(ValueError):
        _mk(
            metric_type="derived",
            entities=("d.e",),
            composition=ir.RatioComposition(numerator="d.a", denominator="d.b"),
            additivity=None,
        )


# ---------------------------------------------------------------------------
# Task 5: DimensionIR.additivity
# ---------------------------------------------------------------------------


def _dim(kind, additivity):
    return ir.DimensionIR(
        semantic_id="d.e.x",
        domain="d",
        entity="d.e",
        name="x",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=(kind == ir.DimensionKind.TIME),
        kind=kind,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="x",
        location=_loc(),
        additivity=additivity,
    )


def test_measure_dimension_carries_additivity():
    d = _dim(ir.DimensionKind.MEASURE, "additive")
    assert d.additivity == "additive"


def test_non_measure_dimension_rejects_additivity():
    with pytest.raises(ValueError):
        _dim(ir.DimensionKind.CATEGORICAL, "additive")


# ---------------------------------------------------------------------------
# Task 6: _BaseRef.__call__ teaching error
# ---------------------------------------------------------------------------


def test_ref_is_not_callable_teaches():
    r = ir.MetricRef("d.loss_rate")
    with pytest.raises(SemanticDecoratorError) as exc:
        r(lambda: None)  # simulates `@ms.ratio(...) def loss_rate(): ...`
    assert "not a decorator" in str(exc.value)


# ---------------------------------------------------------------------------
# Task 7: ms.semi_additive builder + _normalize_additivity
# ---------------------------------------------------------------------------


def test_semi_additive_builder_normalizes_fold():
    sa = authoring.semi_additive(over="sales.orders.order_date", fold="last")
    assert isinstance(sa, ir.SemiAdditive)
    assert sa.over == "sales.orders.order_date"
    assert sa.fold.kind == "last"


def test_semi_additive_builder_quantile():
    sa = authoring.semi_additive(over="d.e.t", fold=("quantile", 0.9))
    assert sa.fold.kind == "quantile" and sa.fold.q == 0.9


# ---------------------------------------------------------------------------
# Task 8: ms.aggregate (tier-1)
# ---------------------------------------------------------------------------


def test_aggregate_builds_tier1_simple_metric():
    with authoring_session(domain="sales") as sess:
        amount = sess.measure(entity="sales.orders", name="amount", additivity="additive")
        rev = authoring.aggregate(measure=amount, agg="sum", name="revenue")
        m = sess.pending_metric("sales.revenue")
    assert m.metric_type == "simple"
    assert m.aggregation == "sum"
    assert m.measure == "sales.orders.amount"
    assert m.entities == ("sales.orders",)
    assert m.additivity is None  # resolved at load (Plan 2)
    assert m.composition is None


# ---------------------------------------------------------------------------
# Task 9: @ms.simple_metric (tier-2 body)
# ---------------------------------------------------------------------------


def test_simple_metric_body_form_declares_additivity():
    with authoring_session(domain="sales") as sess:

        @authoring.simple_metric(entities=["sales.orders"], additivity="additive")
        def gmv(orders):
            return (orders.price * orders.qty).sum()

        m = sess.pending_metric("sales.gmv")
    assert m.metric_type == "simple"
    assert m.aggregation is None and m.measure is None
    assert m.additivity == "additive"
    assert m.entities == ("sales.orders",)


def test_simple_metric_semi_additive_via_builder():
    with authoring_session(domain="ops") as sess:

        @authoring.simple_metric(
            entities=["ops.samples"],
            additivity=authoring.semi_additive(over="ops.samples.t", fold="max"),
        )
        def peak_bw(samples):
            return samples.bw.sum()

        m = sess.pending_metric("ops.peak_bw")
    assert isinstance(m.additivity, ir.SemiAdditive)
    assert m.additivity.fold.kind == "max"


# ---------------------------------------------------------------------------
# Task 10: ms.ratio / ms.weighted_average / ms.linear
# ---------------------------------------------------------------------------


def _m(sess, entity, col):
    """Declare a measure dimension and return its ref."""
    return sess.measure(entity=entity, name=col, additivity="additive")


def test_ratio_constructor_is_flat_and_derived():
    with authoring_session(domain="net") as sess:
        lost = authoring.aggregate(measure=_m(sess, "net.pkts", "lost"), agg="sum", name="lost")
        total = authoring.aggregate(measure=_m(sess, "net.pkts", "total"), agg="sum", name="total")
        loss_rate = authoring.ratio(name="loss_rate", numerator=lost, denominator=total, unit="1")
        m = sess.pending_metric("net.loss_rate")
    assert m.metric_type == "derived"
    assert isinstance(m.composition, ir.RatioComposition)
    assert m.composition.numerator == "net.lost" and m.composition.denominator == "net.total"
    assert m.entities == () and m.aggregation is None and m.measure is None


def test_weighted_average_keeps_public_roles():
    with authoring_session(domain="net") as sess:
        rate = authoring.aggregate(measure=_m(sess, "net.s", "rate"), agg="mean", name="rate")
        sess_n = authoring.aggregate(measure=_m(sess, "net.s", "n"), agg="sum", name="sessions")
        wa = authoring.weighted_average(name="overall_rate", value=rate, weight=sess_n)
        m = sess.pending_metric("net.overall_rate")
    assert isinstance(m.composition, ir.WeightedAverageComposition)
    assert m.composition.value == "net.rate" and m.composition.weight == "net.sessions"


def test_linear_signs_and_min_terms():
    with authoring_session(domain="fin") as sess:
        g = authoring.aggregate(measure=_m(sess, "fin.o", "gross"), agg="sum", name="gross")
        r = authoring.aggregate(measure=_m(sess, "fin.o", "refund"), agg="sum", name="refunds")
        net = authoring.linear(name="net_revenue", add=[g], subtract=[r])
        m = sess.pending_metric("fin.net_revenue")
    assert [(t.sign, t.metric) for t in m.composition.terms] == [
        ("+", "fin.gross"),
        ("-", "fin.refunds"),
    ]
    with pytest.raises(SemanticDecoratorError):
        authoring.linear(name="bad", add=[g])  # < 2 terms


# ---------------------------------------------------------------------------
# Task 13: package exports
# ---------------------------------------------------------------------------


def test_package_exports_new_surface():
    for present in (
        "aggregate",
        "simple_metric",
        "ratio",
        "weighted_average",
        "linear",
        "semi_additive",
    ):
        assert hasattr(ms, present), f"ms.{present} missing"
