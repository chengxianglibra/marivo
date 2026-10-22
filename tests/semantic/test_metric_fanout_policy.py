"""Retired Metric-wide fanout authoring cannot bypass source permissions."""

from __future__ import annotations

import pytest

from marivo.semantic._deferred_semantics import consume_metric
from marivo.semantic.errors import ErrorKind
from tests.shared_fixtures import load_inline_semantic


@pytest.mark.parametrize("permission", ("ms.additive_all()", "ms.non_additive()"))
def test_metric_fanout_policy_requires_explicit_reauthoring(permission: str) -> None:
    source = f"""\
import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
@ms.metric(entities=[orders], additivity={permission}, fanout_policy="aggregate_then_join")
def invalid(rows):
    return rows.amount.sum()
"""
    with load_inline_semantic(source) as result:
        (error,) = result.errors
        assert error.kind == ErrorKind.INVALID_COMPONENT_BODY
        assert error.received is not None and "fanout_policy" in error.received
        assert error.repair is not None
        assert "Population.preimage" in error.repair.action
        assert "mv.membership" in error.repair.action
        assert "separately keyed Entity" in error.repair.action
        assert "preserve each source root" in error.repair.action


def test_derived_metric_uses_source_permission_without_global_fanout_policy() -> None:
    source = """import marivo.datasource as md
import marivo.semantic as ms
orders_ref = ms.entity(name="orders", datasource=ms.ref.datasource("wh"), source=md.table("orders"))
amount_ref = ms.measure_column(name="amount", entity=orders_ref, column="amount", additivity=ms.additive_all())
amount = amount_ref
orders = orders_ref
@ms.metric()
def aov():
    return amount.sum() / orders.count()
"""
    with load_inline_semantic(source) as result:
        assert result.registry is not None, result.errors
        metric = result.registry.declared_metrics["test.aov"]
        assert consume_metric(metric).roots == (consume_metric(metric).roots[0],)
        assert consume_metric(metric).roots[0].ref.path == "test.orders"
        assert not hasattr(consume_metric(metric), "fanout_policy")
