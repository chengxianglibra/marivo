"""Unit tests for the sealed cross-layer ``Ref`` value type."""

from __future__ import annotations

import pytest

from marivo.refs import Ref, SemanticKind
from marivo.refs import ref as ref_factory


def test_factory_rejects_whitespace_instead_of_normalizing() -> None:
    with pytest.raises(ValueError, match="surrounding whitespace"):
        ref_factory.entity("  sales.x  ")
    with pytest.raises(ValueError, match="exactly 2 segments"):
        ref_factory.entity("")


def test_eq_and_hash_by_kind_and_path() -> None:
    a = ref_factory.entity("sales.x")
    b = ref_factory.entity("sales.x")
    assert a == b
    assert hash(a) == hash(b)
    assert a != ref_factory.entity("other.x")
    assert a != ref_factory.metric("sales.x")


def test_datasource_ref_uses_the_same_sealed_value_type() -> None:
    ref = ref_factory.datasource("warehouse")
    assert type(ref) is Ref
    assert ref.path == "warehouse"
    assert ref.name == "warehouse"
    assert ref.kind is SemanticKind.DATASOURCE
    assert str(ref) == "datasource:warehouse"
    assert repr(ref) == "Ref[datasource](datasource:warehouse)"
    assert ref == ref_factory.datasource("warehouse")
