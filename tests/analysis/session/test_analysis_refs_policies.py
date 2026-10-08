"""Artifact identity and semantic refs retain separate canonical public owners."""

import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.refs import ArtifactRef


def test_semantic_refs_stay_on_the_semantic_surface():
    assert not hasattr(mv, "AlignmentKind")
    assert ms.ref.metric("sales.revenue").path == "sales.revenue"
    assert ms.ref.dimension("sales.orders.region").path == "sales.orders.region"
    assert not hasattr(mv, "SemanticRef")
    assert not hasattr(mv, "Ref")
    assert not hasattr(mv, "SemanticKind")
    assert not hasattr(mv, "CatalogObject")
    assert not hasattr(mv, "CalendarRef")


def test_artifact_ref_is_exported_and_preserves_ref():
    assert mv.ArtifactRef is ArtifactRef
    assert ArtifactRef("frame_abc123").ref == "frame_abc123"
    assert str(ArtifactRef("frame_abc123")) == "frame_abc123"


def test_refs_reject_empty_refs():
    with pytest.raises(ValidationError):
        ArtifactRef(" ")
