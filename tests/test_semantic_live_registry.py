"""Semantic live capability registry invariants."""

from __future__ import annotations

from marivo.semantic._capabilities.model import (
    SemanticCapabilityRegistry,
    SemanticRootGroup,
    SemanticTypeContract,
)
from marivo.semantic._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE


def test_semantic_surface_uses_the_native_registry_without_copying() -> None:
    assert SEMANTIC_LIVE_SURFACE.registry is REGISTRY
    for canonical_id in REGISTRY.canonical_ids():
        native = REGISTRY.by_canonical_id(canonical_id)
        assert SEMANTIC_LIVE_SURFACE.registry.by_canonical_id(canonical_id) is native


def test_registry_surface_is_semantic() -> None:
    assert isinstance(REGISTRY, SemanticCapabilityRegistry)
    assert REGISTRY.surface == "semantic"


def test_registry_canonical_ids_are_unique() -> None:
    ids = REGISTRY.canonical_ids()
    assert len(ids) == len(set(ids))


def test_registry_group_members_are_registered() -> None:
    groups: tuple[SemanticRootGroup, ...] = (
        "browse_load",
        "author_families",
        "verify_preview",
        "readiness",
        "diagnostics_boundaries",
    )
    for group in groups:
        members = REGISTRY.group(group)
        for member in members:
            assert member.surface == "semantic"


def test_type_contract_type_is_dataclass() -> None:
    import dataclasses

    assert dataclasses.is_dataclass(SemanticTypeContract)


def test_validate_semantic_live_surface_passes() -> None:
    from marivo.semantic._capabilities.validation import validate_semantic_live_surface

    validate_semantic_live_surface()


def test_registry_covers_all_public_callables() -> None:
    import marivo.semantic as ms

    for name in ms.__all__:
        exported = getattr(ms, name)
        if callable(exported) and not isinstance(exported, type):
            if name in {"help", "help_text"}:
                continue
            assert REGISTRY.by_callable(exported), (
                f"{name} is not registered in the semantic registry"
            )


def test_registry_covers_all_public_types() -> None:
    import marivo.semantic as ms

    for name in ms.__all__:
        exported = getattr(ms, name)
        if isinstance(exported, type):
            assert exported in TYPE_CONTRACTS, f"{name} ({exported}) is not in TYPE_CONTRACTS"


def test_registry_includes_authoring_topic() -> None:
    assert "authoring" in REGISTRY.canonical_ids()


def test_preview_capability_teaches_readiness_batch_repair() -> None:
    import marivo.semantic as ms

    preview = REGISTRY.by_canonical_id("preview")
    subject = next(
        requirement for requirement in preview.input_requirements if requirement.role == "subject"
    )

    assert preview.output_family == "PreviewResult | PreviewBatchResult"
    assert subject.min_count == 1
    assert subject.max_count is None
    assert preview.minimal_example is not None
    assert "report.preview_required_refs" in preview.minimal_example
    assert ms.PreviewBatchResult in TYPE_CONTRACTS
