"""Unit tests for exact semantic ref factories and public boundaries."""

from __future__ import annotations

import pytest
from ibis.common.deferred import Deferred

import marivo.semantic as ms
from marivo.semantic.errors import SemanticRuntimeError


def test_metric_ref_requires_two_segment_path() -> None:
    assert isinstance(ms.ref.metric("sales.revenue"), Deferred)
    with pytest.raises(ValueError):
        ms.ref.metric("revenue")


def test_field_ref_bind_without_binding_context_raises() -> None:
    with pytest.raises(SemanticRuntimeError) as exc_info:
        ms.bind(ms.ref.dimension("sales.orders.country"), "table")
    assert exc_info.value.kind == "binding_context_missing"
