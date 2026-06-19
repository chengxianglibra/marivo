"""Unit tests for the cross-layer SemanticRef base and SymbolKind."""

from __future__ import annotations

import pytest

from marivo.refs import SemanticRef, SymbolKind


def test_base_identity_id_and_kind() -> None:
    ref = SemanticRef("sales.revenue", SymbolKind.METRIC)
    assert ref.id == "sales.revenue"
    assert ref.kind is SymbolKind.METRIC
    assert str(ref) == "sales.revenue"


def test_base_strips_and_rejects_empty() -> None:
    assert SemanticRef("  sales.x  ", SymbolKind.ENTITY).id == "sales.x"
    with pytest.raises(ValueError, match="non-empty"):
        SemanticRef("   ", SymbolKind.ENTITY)


def test_eq_and_hash_by_type_and_id() -> None:
    a = SemanticRef("sales.x", SymbolKind.ENTITY)
    b = SemanticRef("sales.x", SymbolKind.ENTITY)
    assert a == b
    assert hash(a) == hash(b)
    assert a != SemanticRef("other.x", SymbolKind.ENTITY)


def test_base_call_raises_teaching_error() -> None:
    ref = SemanticRef("sales.revenue", SymbolKind.METRIC)
    with pytest.raises(TypeError, match="declared semantic object"):
        ref("anything")


def test_datasource_ref_is_semantic_ref() -> None:
    from marivo.datasource.authoring import DatasourceRef

    ref = DatasourceRef("warehouse")
    assert isinstance(ref, SemanticRef)
    assert ref.id == "warehouse"
    assert ref.kind is SymbolKind.DATASOURCE
    assert str(ref) == "warehouse"
    assert repr(ref) == "DatasourceRef('warehouse')"
    assert ref == DatasourceRef("warehouse")


def test_symbolkind_has_eight_members() -> None:
    assert {str(k) for k in SymbolKind} == {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "measure",
        "time_dimension",
        "metric",
        "relationship",
    }
