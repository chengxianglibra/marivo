"""Metric value objects, registration invariants and aggregation policies."""

from typing import Literal

import pytest

import marivo.semantic as ms
from marivo.semantic import authoring, ir
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError
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
            sample_interval=None,
            python_symbol="x",
            location=_loc(),
            additivity=ms.additive_all(),
        )


# ---------------------------------------------------------------------------
# Closed additivity policy builders
# ---------------------------------------------------------------------------


def test_status_time_policy_keeps_fixed_axis():
    order_date = ms.ref.time_dimension("sales.orders.order_date")
    policy = authoring.additive_except(order_date)
    assert policy.exceptions == ("sales.orders.order_date",)


def test_additivity_builders_accept_positional_axes_and_unpacked_collections() -> None:
    region = ms.ref.dimension("sales.orders.region")
    order_date = ms.ref.time_dimension("sales.orders.order_date")
    axes = (region, order_date)
    paths = ("sales.orders.region", "sales.orders.order_date")

    assert ms.additive_over(region).coordinates == paths[:1]
    assert ms.additive_over(region, order_date).coordinates == paths
    assert ms.additive_over(*axes).coordinates == paths
    assert ms.additive_except(region, order_date).exceptions == paths
    assert ms.additive_except(*axes).exceptions == paths
    assert ms.additive_all().exceptions == ()


@pytest.mark.parametrize("kind", ("over", "except"))
def test_additivity_builders_require_nonempty_unique_native_axes(
    kind: Literal["over", "except"],
) -> None:
    from marivo._help.render import render_help_text

    builder = ms.additive_over if kind == "over" else ms.additive_except
    with pytest.raises(SemanticDecoratorError) as empty:
        builder()
    assert empty.value.kind == ErrorKind.INVALID_REF
    assert empty.value.expected and empty.value.received and empty.value.repair
    assert render_help_text(f"semantic.{empty.value.repair.help_target.canonical_id}")[0]
    assert "additive_all()" in str(empty.value)

    region = ms.ref.dimension("sales.orders.region")
    with pytest.raises(SemanticDecoratorError) as duplicate:
        builder(region, ms.ref.dimension("sales.orders.region"))
    assert duplicate.value.kind == ErrorKind.INVALID_REF
    assert "unique" in str(duplicate.value)

    with pytest.raises(SemanticDecoratorError) as packed:
        builder((region,))  # type: ignore[arg-type]  # intentional rejection input
    assert packed.value.kind == ErrorKind.INVALID_REF
    assert packed.value.expected and packed.value.received and packed.value.repair


def test_additivity_retired_entrypoint_and_keyword_forms_are_removed() -> None:
    assert not hasattr(ms, "additive")
    assert not hasattr(authoring, "additive")
    region = ms.ref.dimension("sales.orders.region")
    with pytest.raises(TypeError):
        ms.additive_all(except_=(region,))  # type: ignore[call-arg]  # intentional rejection input
    with pytest.raises(TypeError):
        ms.additive_all(region)  # type: ignore[call-arg]  # intentional rejection input
    with pytest.raises(TypeError):
        ms.additive_over(over=(region,))  # type: ignore[call-arg]  # intentional rejection input
    with pytest.raises(TypeError):
        ms.additive_except(axes=(region,))  # type: ignore[call-arg]  # intentional rejection input


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
            additivity=authoring.additive_except(ms.ref.time_dimension("ops.samples.t")),
            status_time_dimension=ms.ref.time_dimension("ops.samples.t"),
            status_time_fold="max",
        )
    assert caught.value.kind == "invalid_component_body"
    assert caught.value.repair is not None
    assert "per(status_time_field).reducer()" in caught.value.repair.action
