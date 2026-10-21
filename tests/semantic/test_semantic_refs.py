"""Unit tests for exact semantic ref factories and public boundaries."""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo.semantic.errors import SemanticRuntimeError


def test_metric_ref_requires_two_segment_path() -> None:
    assert ms.ref.metric("sales.revenue").path == "sales.revenue"
    with pytest.raises(ValueError, match="exactly 2 segments"):
        ms.ref.metric("revenue")


def test_ref_factory_namespace_is_immutable() -> None:
    with pytest.raises(AttributeError):
        ms.ref.metric = lambda _path: None  # type: ignore[method-assign]


def test_field_ref_bind_without_binding_context_raises() -> None:
    with pytest.raises(SemanticRuntimeError) as exc_info:
        ms.bind(ms.ref.dimension("sales.orders.country"), "table")  # type: ignore[arg-type]
    assert exc_info.value.kind == "binding_context_missing"
