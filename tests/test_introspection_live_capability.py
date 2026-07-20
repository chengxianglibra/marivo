"""AuthoringCapability descriptor and LiveSurfaceRegistry protocol contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringStateRef,
)
from marivo.introspection.live.model import LiveSurfaceRegistry


def _preview_capability() -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id="preview",
        kind="method",
        surface="semantic",
        public_entrypoint="catalog.preview",
        callable_path="marivo.semantic.catalog.SemanticCatalog.preview",
        summary="Scoped runtime preview of one loaded semantic object.",
        input_requirements=(
            AuthoringInputRequirement(role="subject", family="Ref"),
            AuthoringInputRequirement(role="evidence", family="DiscoverySnapshot"),
        ),
        output_family="PreviewResult",
        preconditions=("semantic.loaded",),
        produced_state=AuthoringStateRef(id="semantic.previewed"),
        effects=AuthoringEffects(
            data_access="scoped_data_read",
            connection="opens_connection",
            mutations=("project_state",),
            flags=("requires_existing_snapshot_binding",),
        ),
        constraints=("preview_scope_required",),
        minimal_example="catalog.preview(obj, using=snapshot)",
    )


def test_live_capability_carries_required_facts():
    cap = _preview_capability()
    assert cap.canonical_id == "preview"
    assert cap.surface == "semantic"
    assert cap.effects.data_access == "scoped_data_read"
    assert cap.see_also == ()
    assert cap.repair_kinds == ()


def test_live_capability_live_target_property():
    cap = _preview_capability()
    target = cap.live_target
    assert target.surface == "semantic"
    assert target.canonical_id == "preview"


def test_live_capability_rejects_unknown_surface():
    with pytest.raises(ValidationError):
        AuthoringCapability(
            canonical_id="x",
            kind="callable",
            surface="not_a_surface",
            summary="bad",
        )


def test_live_surface_registry_is_a_protocol():
    # Protocol exists and is usable as a type for typing only.
    assert LiveSurfaceRegistry is not None
    assert hasattr(LiveSurfaceRegistry, "_is_protocol")
