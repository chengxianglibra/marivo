"""Metric value objects, registration invariants and aggregation policies."""

import pytest

import marivo.semantic as ms
from marivo.semantic import authoring, ir
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import AiContextIR, SourceLocation
from tests.shared_fixtures import authoring_session

# ---------------------------------------------------------------------------
# Additivity/SemiAdditive/AggKind types
# ---------------------------------------------------------------------------


def test_semi_additive_holds_axis_and_fold():
    sa = ir.SemiAdditive(over="sales.orders.order_date", fold=ir.TimeFoldIR(kind="last"))
    assert sa.over == "sales.orders.order_date"
    assert sa.fold.kind == "last"


# ---------------------------------------------------------------------------
# Composition union
# ---------------------------------------------------------------------------


def test_composition_variants_carry_roles():
    r = ir.RatioComposition(numerator="d.lost", denominator="d.total")
    assert (r.kind, r.numerator, r.denominator) == ("ratio", "d.lost", "d.total")

    w = ir.WeightedMeanAggregation(value="d.e.rate", weight="d.e.sessions")
    assert (w.kind, w.value, w.weight) == ("weighted_mean", "d.e.rate", "d.e.sessions")

    lin = ir.LinearComposition(terms=(ir.LinearTerm("+", "d.a"), ir.LinearTerm("-", "d.b")))
    assert lin.kind == "linear"
    assert [(t.sign, t.metric) for t in lin.terms] == [("+", "d.a"), ("-", "d.b")]


# ---------------------------------------------------------------------------
# Metric IR invariants
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
# MeasureIR.additivity
# ---------------------------------------------------------------------------


def _measure(additivity):
    return ir.MeasureIR(
        semantic_id="d.e.x",
        domain="d",
        entity="d.e",
        name="x",
        ai_context=AiContextIR(),
        additivity=additivity,
        unit=None,
        python_symbol="x",
        location=_loc(),
    )


def test_measure_ir_carries_additivity():
    m = _measure("additive")
    assert m.additivity == "additive"


def test_categorical_dimension_rejects_additivity():
    with pytest.raises(TypeError):
        ir.DimensionIR(
            semantic_id="d.e.x",
            domain="d",
            entity="d.e",
            name="x",
            ai_context=AiContextIR(),
            is_time_dimension=False,
            kind=ir.DimensionKind.CATEGORICAL,
            granularity=None,
            python_symbol="x",
            location=_loc(),
            additivity=ms.additive_all(),
        )


# ---------------------------------------------------------------------------
# ms.bind kind guard
# ---------------------------------------------------------------------------


def test_non_field_ref_rejects_binding_call():
    r = ms.ref.metric("d.loss_rate")
    with pytest.raises(SemanticRuntimeError) as exc_info:
        ms.bind(r, lambda: None)  # type: ignore[arg-type]
    assert exc_info.value.kind == "invalid_binding_ref"
    assert "ms.bind(field_ref, entity_alias)" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Closed additivity policy builders
# ---------------------------------------------------------------------------


def test_status_time_policy_keeps_fixed_axis():
    order_date = ms.ref.time_dimension("sales.orders.order_date")
    policy = authoring.additive_all(except_=(order_date,))
    assert policy.exceptions == ("sales.orders.order_date",)


# ---------------------------------------------------------------------------
# ms.aggregate (tier-1)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# @ms.metric (tier-2 body)
# ---------------------------------------------------------------------------


def test_metric_body_form_declares_additivity():
    with authoring_session(domain="sales") as sess:

        @authoring.metric(entities=[ms.ref.entity("sales.orders")], additivity=ms.additive_all())
        def gmv(orders):
            return (orders.price * orders.qty).sum()

        m = sess.pending_metric("sales.gmv")
    assert m.metric_type == "simple"
    assert m.aggregation is None and m.measure is None
    assert m.additivity == "additive"
    assert m.entities == ("sales.orders",)


def test_metric_semi_additive_via_builder():
    with authoring_session(domain="ops") as sess:
        t = ms.ref.time_dimension("ops.samples.t")

        @authoring.metric(
            entities=[ms.ref.entity("ops.samples")],
            additivity=authoring.additive_all(except_=(t,)),
            status_time_dimension=t,
            status_time_fold="max",
        )
        def peak_bw(samples):
            return samples.bw.sum()

        m = sess.pending_metric("ops.peak_bw")
    assert isinstance(m.additivity, ir.SemiAdditive)
    assert m.additivity.fold.kind == "max"


# ---------------------------------------------------------------------------
# ms.ratio / ms.weighted_mean / ms.linear
# ---------------------------------------------------------------------------


def _m(sess, entity, col):
    """Declare a measure dimension and return its ref."""
    return sess.measure(entity=ms.ref.entity(entity), name=col, additivity=ms.additive_all())


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cumulative composition
# ---------------------------------------------------------------------------


def test_cumulative_composition_components_and_anchor() -> None:
    from marivo.semantic.ir import CumulativeComposition, composition_components

    comp = CumulativeComposition(base="sales.active_users", over="sales.events.event_time")

    assert comp.kind == "cumulative"
    assert comp.anchor == "all_history"
    assert composition_components(comp) == {"base": "sales.active_users"}


def test_cumulative_composition_allows_unresolved_over_for_load_resolution() -> None:
    from marivo.semantic.ir import CumulativeComposition, composition_components

    comp = CumulativeComposition(base="sales.active_users", over=None)

    assert comp.over is None
    assert composition_components(comp) == {"base": "sales.active_users"}
