"""Semantic refs retain their canonical public owner."""

import marivo.analysis as mv
import marivo.semantic as ms
from tests.support.refs import as_ref


def test_semantic_refs_stay_on_the_semantic_surface():
    assert not hasattr(mv, "AlignmentKind")
    assert as_ref(ms.ref.metric("sales.revenue")).path == "sales.revenue"
    assert as_ref(ms.ref.dimension("sales.orders.region")).path == "sales.orders.region"
    assert not hasattr(mv, "SemanticRef")
    assert not hasattr(mv, "Ref")
    assert not hasattr(mv, "SemanticKind")
    assert not hasattr(mv, "CatalogObject")
    assert not hasattr(mv, "CalendarRef")

    assert not hasattr(mv, "ArtifactRef")
