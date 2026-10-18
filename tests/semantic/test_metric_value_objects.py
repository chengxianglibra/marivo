"""Metric value objects, registration invariants and aggregation policies."""

import pytest

import marivo.semantic as ms
from marivo.semantic import authoring, ir
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import AiContextIR, SourceLocation

# ---------------------------------------------------------------------------
# Additivity/SemiAdditive/AggKind types
# ---------------------------------------------------------------------------


def test_semi_additive_holds_axis_and_fold():
    sa = ir.SemiAdditive(over="sales.orders.order_date", fold=ir.TimeFoldIR(kind="last"))
    assert sa.over == "sales.orders.order_date"
    assert sa.fold.kind == "last"


# ---------------------------------------------------------------------------
# Metric IR invariants
# ---------------------------------------------------------------------------


def _loc():
    return SourceLocation(file="t.py", line=1)


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
# @ms.metric (tier-2 body)
# ---------------------------------------------------------------------------


def test_metric_global_additivity_requires_reauthoring():
    from marivo.semantic.errors import SemanticExpressionError

    with pytest.raises(SemanticExpressionError) as caught:
        authoring.metric(entities=[ms.ref.entity("sales.orders")], additivity=ms.additive_all())
    assert caught.value.kind == "invalid_component_body"
    assert caught.value.repair is not None
    assert "sum permission on source Measures" in caught.value.repair.action


def test_metric_global_status_fold_requires_explicit_sample_reduction():
    from marivo.semantic.errors import SemanticExpressionError

    with pytest.raises(SemanticExpressionError) as caught:
        authoring.metric(
            entities=[ms.ref.entity("ops.samples")],
            additivity=authoring.additive_all(except_=(ms.ref.time_dimension("ops.samples.t"),)),
            status_time_dimension=ms.ref.time_dimension("ops.samples.t"),
            status_time_fold="max",
        )
    assert caught.value.kind == "invalid_component_body"
    assert caught.value.repair is not None
    assert "per(status_time_field).reducer()" in caught.value.repair.action
