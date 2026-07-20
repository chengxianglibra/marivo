"""Unit tests for the sealed cross-layer ``Ref`` value type."""

from __future__ import annotations

import pytest

from marivo.refs import Ref, SemanticKind
from marivo.semantic.errors import SemanticRuntimeError


def test_factory_identity_path_kind_and_key() -> None:
    ref = Ref.metric("sales.revenue")
    assert ref.path == "sales.revenue"
    assert ref.kind is SemanticKind.METRIC
    assert ref.key == "metric:sales.revenue"
    assert str(ref) == ref.key


def test_factory_rejects_whitespace_instead_of_normalizing() -> None:
    with pytest.raises(ValueError, match="surrounding whitespace"):
        Ref.entity("  sales.x  ")
    with pytest.raises(ValueError, match="exactly 2 segments"):
        Ref.entity("")


def test_eq_and_hash_by_kind_and_path() -> None:
    a = Ref.entity("sales.x")
    b = Ref.entity("sales.x")
    assert a == b
    assert hash(a) == hash(b)
    assert a != Ref.entity("other.x")
    assert a != Ref.metric("sales.x")


def test_non_field_ref_call_rejects_wrong_kind() -> None:
    ref = Ref.metric("sales.revenue")
    with pytest.raises(SemanticRuntimeError) as exc_info:
        ref("anything")
    assert exc_info.value.kind == "invalid_binding_ref"
    assert "field_ref(entity_alias)" in str(exc_info.value)


def test_datasource_ref_uses_the_same_sealed_value_type() -> None:
    ref = Ref.datasource("warehouse")
    assert type(ref) is Ref
    assert ref.path == "warehouse"
    assert ref.name == "warehouse"
    assert ref.kind is SemanticKind.DATASOURCE
    assert str(ref) == "datasource:warehouse"
    assert repr(ref) == "Ref[datasource](datasource:warehouse)"
    assert ref == Ref.datasource("warehouse")


def test_semantic_kind_has_eight_members() -> None:
    assert {str(k) for k in SemanticKind} == {
        "domain",
        "datasource",
        "entity",
        "dimension",
        "measure",
        "time_dimension",
        "metric",
        "relationship",
    }
