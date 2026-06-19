"""Unit tests for the semantic ref subclasses, factory, and normalizers."""

from __future__ import annotations

import pytest

from marivo.refs import SemanticRef, SymbolKind
from marivo.semantic.refs import (
    DimensionRef,
    EntityRef,
    MetricRef,
    as_ref_id,
    make_ref,
)


def test_subclasses_are_semantic_refs_with_fixed_kind() -> None:
    assert isinstance(EntityRef("sales.orders"), SemanticRef)
    assert EntityRef("sales.orders").kind is SymbolKind.ENTITY
    assert DimensionRef("sales.orders.country").kind is SymbolKind.DIMENSION


def test_metric_ref_requires_dotted_id() -> None:
    assert MetricRef("sales.revenue").id == "sales.revenue"
    with pytest.raises(ValueError, match=r"model.*metric"):
        MetricRef("revenue")


def test_make_ref_dispatches_all_eight_kinds() -> None:
    expected = {
        SymbolKind.DOMAIN: "DomainRef",
        SymbolKind.DATASOURCE: "DatasourceRef",
        SymbolKind.ENTITY: "EntityRef",
        SymbolKind.DIMENSION: "DimensionRef",
        SymbolKind.MEASURE: "MeasureRef",
        SymbolKind.TIME_DIMENSION: "TimeDimensionRef",
        SymbolKind.METRIC: "MetricRef",
        SymbolKind.RELATIONSHIP: "RelationshipRef",
    }
    ids = {SymbolKind.DATASOURCE: "warehouse"}  # datasource names disallow dots
    for kind, cls_name in expected.items():
        ref = make_ref(ids.get(kind, "sales.x"), kind)
        assert type(ref).__name__ == cls_name
        assert ref.kind is kind


def test_as_ref_id_is_string_tolerant() -> None:
    assert as_ref_id("sales.orders") == "sales.orders"
    assert as_ref_id(EntityRef("sales.orders")) == "sales.orders"


def test_field_ref_call_without_resolver_raises() -> None:
    with pytest.raises(RuntimeError, match="no resolver"):
        DimensionRef("sales.orders.country")("table")
