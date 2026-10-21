"""Metric value objects, registration invariants and aggregation policies."""

import pytest

import marivo.semantic as ms
from marivo.semantic import authoring, ir
from marivo.semantic.ir import AiContextIR, SourceLocation

# ---------------------------------------------------------------------------
# Metric IR invariants
# ---------------------------------------------------------------------------


def _loc():
    return SourceLocation(file="t.py", line=1)


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
