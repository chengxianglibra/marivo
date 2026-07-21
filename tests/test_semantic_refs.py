"""Unit tests for exact semantic ref factories and public boundaries."""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.semantic.errors import SemanticRuntimeError


def test_factories_return_exact_ref_with_fixed_kind() -> None:
    assert type(ms.ref.entity("sales.orders")) is ms.Ref
    assert ms.ref.entity("sales.orders").kind is ms.SemanticKind.ENTITY
    assert ms.ref.dimension("sales.orders.country").kind is ms.SemanticKind.DIMENSION


def test_metric_ref_requires_two_segment_path() -> None:
    assert ms.ref.metric("sales.revenue").path == "sales.revenue"
    with pytest.raises(ValueError, match="exactly 2 segments"):
        ms.ref.metric("revenue")


def test_all_eight_exact_kind_factories() -> None:
    expected = {
        ms.SemanticKind.DOMAIN: ms.ref.domain("sales"),
        ms.SemanticKind.DATASOURCE: ms.ref.datasource("warehouse"),
        ms.SemanticKind.ENTITY: ms.ref.entity("sales.orders"),
        ms.SemanticKind.DIMENSION: ms.ref.dimension("sales.orders.country"),
        ms.SemanticKind.MEASURE: ms.ref.measure("sales.orders.amount"),
        ms.SemanticKind.TIME_DIMENSION: ms.ref.time_dimension("sales.orders.ordered_at"),
        ms.SemanticKind.METRIC: ms.ref.metric("sales.revenue"),
        ms.SemanticKind.RELATIONSHIP: ms.ref.relationship("sales.orders_to_customers"),
    }
    for kind, ref in expected.items():
        assert type(ref) is ms.Ref
        assert ref.kind is kind


def test_ref_factory_namespace_is_immutable() -> None:
    with pytest.raises(AttributeError):
        ms.ref.metric = lambda _path: None  # type: ignore[method-assign]


def test_raw_constructor_and_legacy_helpers_are_absent() -> None:
    with pytest.raises(TypeError, match="no public raw constructor"):
        ms.Ref()  # type: ignore[call-arg]
    assert not hasattr(ms, "SemanticRef")
    assert not hasattr(ms, "MetricRef")
    assert hasattr(ms, "ref")
    assert not hasattr(ms.Ref, "metric")


def test_field_ref_bind_without_binding_context_raises() -> None:
    with pytest.raises(SemanticRuntimeError) as exc_info:
        ms.bind(ms.ref.dimension("sales.orders.country"), "table")  # type: ignore[arg-type]
    assert exc_info.value.kind == "binding_context_missing"
