"""Semantic live capability registry invariants."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType
from typing import get_args

import pytest

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import CATALOG_COLLECTION_PROPERTIES
from marivo.semantic._capabilities.model import (
    SEMANTIC_HELP_RENDER_BUDGETS,
    AuthoringSourceContract,
    ConstructionMode,
    SemanticBuilderTopic,
    SemanticCapabilityRegistry,
    SemanticCheckRoute,
    SemanticCheckTopic,
    SemanticHelpDescriptor,
    SemanticHelpRenderBudget,
    SemanticHelpRenderClass,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectDecision,
    SemanticObjectRelationship,
    SemanticRootGroup,
    SemanticTypeContract,
)
from marivo.semantic._capabilities.registry import (
    REGISTRY,
    TYPE_CONTRACTS,
    _finalize_registry,
    _validate_registry,
)
from marivo.semantic._capabilities.surface import SEMANTIC_LIVE_SURFACE


def _semantic_target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="semantic", canonical_id=canonical_id)


def _finalize_fixture(
    descriptors: tuple[AuthoringCapability, ...] | None = None,
    *,
    help_descriptors: tuple[SemanticHelpDescriptor, ...] | None = None,
    render_budgets: Mapping[SemanticHelpRenderClass, SemanticHelpRenderBudget] | None = None,
) -> SemanticCapabilityRegistry:
    return _finalize_registry(
        REGISTRY.descriptors if descriptors is None else descriptors,
        groups=REGISTRY._groups,
        source_contracts=REGISTRY._source_contracts,
        repair_contracts=REGISTRY._repair_contracts,
        help_descriptors=help_descriptors,
        render_budgets=(SEMANTIC_HELP_RENDER_BUDGETS if render_budgets is None else render_budgets),
    )


def test_semantic_surface_uses_the_native_registry_without_copying() -> None:
    assert SEMANTIC_LIVE_SURFACE.registry is REGISTRY
    for canonical_id in REGISTRY.canonical_ids():
        native = REGISTRY.by_canonical_id(canonical_id)
        assert SEMANTIC_LIVE_SURFACE.registry.by_canonical_id(canonical_id) is native


def test_registry_surface_is_semantic() -> None:
    assert isinstance(REGISTRY, SemanticCapabilityRegistry)
    assert REGISTRY.surface == "semantic"


def test_semantic_help_descriptor_union_is_closed() -> None:
    assert set(get_args(SemanticHelpDescriptor)) == {
        AuthoringCapability,
        SemanticNavigationTopic,
        SemanticBuilderTopic,
        SemanticCheckTopic,
        SemanticObjectContract,
    }


def test_semantic_help_render_classes_and_budgets_are_closed() -> None:
    assert set(get_args(SemanticHelpRenderClass)) == {
        "root",
        "decision_hub",
        "navigation",
        "exact_contract",
        "current_briefing",
    }
    expected = {
        "root": (32, 3_000, 10, 0),
        "decision_hub": (40, 4_000, 8, 0),
        "navigation": (64, 6_000, 24, 0),
        "exact_contract": (72, 7_000, 8, 1),
        "current_briefing": (64, 6_000, 6, 1),
    }
    assert {
        render_class: (
            budget.max_lines,
            budget.max_codepoints,
            budget.max_outgoing_routes,
            budget.max_examples_or_snippets,
        )
        for render_class, budget in REGISTRY.render_budgets.items()
    } == expected
    assert REGISTRY.render_budgets is not SEMANTIC_HELP_RENDER_BUDGETS
    with pytest.raises(TypeError):
        REGISTRY.render_budgets["root"] = SemanticHelpRenderBudget(1, 1, 1, 0)  # type: ignore[index]


def test_native_descriptor_models_are_frozen_and_non_invokable() -> None:
    target = _semantic_target("load")
    route = SemanticCheckRoute(
        question="Does the project load?",
        targets=(target,),
        proves="Static project assembly succeeds.",
        does_not_prove="The datasource is healthy.",
    )
    decision = SemanticObjectDecision(
        decision_id="identity",
        question="What does this object identify?",
        determine_from="Current business authority.",
        basis="business_authority",
        encoding_status="supported",
        next_targets=(target,),
    )
    relationship = SemanticObjectRelationship(
        relation="consumed_by",
        target=target,
        explanation="The loader consumes the declaration.",
    )
    descriptors: tuple[SemanticHelpDescriptor, ...] = (
        SemanticNavigationTopic("objects", "Browse object kinds.", (target,)),
        SemanticBuilderTopic("builders.values", "Values", "Build values.", (target,)),
        SemanticCheckTopic("checks", "Choose a check.", (route,)),
        SemanticObjectContract(
            canonical_id="objects.domain",
            summary="A governed business namespace.",
            semantic_kind=SemanticKind.DOMAIN,
            ref_target=target,
            catalog_collection="domains",
            placement_kind="domain_entrypoint",
            decisions=(decision,),
            construction_modes=(ConstructionMode("Declare a domain.", "default", target),),
            relationships=(relationship,),
            supporting_targets=(),
            check_targets=(target,),
        ),
    )

    for descriptor in descriptors:
        assert descriptor.canonical_id
        assert descriptor.summary
        assert descriptor.public_entrypoint is None
        assert descriptor.callable_path is None
        with pytest.raises(FrozenInstanceError):
            descriptor.summary = "changed"  # type: ignore[misc]


def test_slice1_keeps_native_navigation_descriptors_inactive() -> None:
    assert REGISTRY.help_descriptors == REGISTRY.descriptors
    assert not any(
        isinstance(
            descriptor,
            (
                SemanticNavigationTopic,
                SemanticBuilderTopic,
                SemanticCheckTopic,
                SemanticObjectContract,
            ),
        )
        for descriptor in REGISTRY.help_descriptors
    )
    assert {"objects", "builders", "checks"}.isdisjoint(REGISTRY.canonical_ids())


def test_every_active_descriptor_has_one_registry_owned_render_class() -> None:
    assert set(REGISTRY._render_classes) == set(REGISTRY.canonical_ids())
    assert REGISTRY.render_class("authoring") == "decision_hub"
    assert all(
        REGISTRY.render_class(canonical_id) == "exact_contract"
        for canonical_id in REGISTRY.canonical_ids()
        if canonical_id != "authoring"
    )


def test_registry_canonical_ids_are_unique() -> None:
    ids = REGISTRY.canonical_ids()
    assert len(ids) == len(set(ids))


def test_registry_rejects_duplicate_help_canonical_ids_eagerly() -> None:
    duplicate = REGISTRY.descriptors[0].model_copy()
    with pytest.raises(ValueError, match="duplicate semantic help canonical id"):
        _finalize_fixture((*REGISTRY.descriptors, duplicate))


def test_registry_rejects_duplicate_callable_paths_eagerly() -> None:
    first = next(
        descriptor for descriptor in REGISTRY.descriptors if descriptor.callable_path is not None
    )
    duplicate = first.model_copy(update={"canonical_id": "synthetic.duplicate_callable"})
    with pytest.raises(ValueError, match="duplicate semantic callable path"):
        _finalize_fixture((*REGISTRY.descriptors, duplicate))


@pytest.mark.parametrize("extra", (False, True))
def test_registry_requires_exact_render_budget_coverage(extra: bool) -> None:
    budgets: dict[object, SemanticHelpRenderBudget] = dict(REGISTRY.render_budgets)
    if extra:
        budgets["unknown"] = SemanticHelpRenderBudget(1, 1, 1, 0)
    else:
        budgets.pop("navigation")
    with pytest.raises(ValueError, match="budgets must cover every render class"):
        _finalize_fixture(render_budgets=budgets)  # type: ignore[arg-type]


def test_registry_rejects_non_positive_render_budget() -> None:
    budgets = dict(REGISTRY.render_budgets)
    budgets["navigation"] = replace(budgets["navigation"], max_lines=0)
    with pytest.raises(ValueError, match="invalid semantic Help render budget"):
        _finalize_fixture(render_budgets=budgets)


def test_registry_rejects_missing_render_assignment_before_rendering() -> None:
    render_classes = dict(REGISTRY._render_classes)
    render_classes.pop("authoring")
    invalid = replace(REGISTRY, _render_classes=MappingProxyType(render_classes))
    with pytest.raises(ValueError, match="assignments must cover every descriptor"):
        _validate_registry(invalid)


@pytest.mark.parametrize("field_name", ("public_entrypoint", "callable_path"))
def test_registry_rejects_invokable_navigation_before_rendering(field_name: str) -> None:
    topic = SemanticNavigationTopic(
        canonical_id="synthetic.navigation",
        summary="Synthetic navigation.",
        members=(_semantic_target("load"),),
    )
    object.__setattr__(topic, field_name, "marivo.semantic.load")
    with pytest.raises(ValueError, match="navigation descriptor must not be invokable"):
        _finalize_fixture(help_descriptors=(*REGISTRY.help_descriptors, topic))


def test_registry_group_members_are_registered() -> None:
    groups: tuple[SemanticRootGroup, ...] = (
        "browse_load",
        "author_families",
        "runtime_probes",
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
        if inspect.isroutine(exported) and not isinstance(exported, type):
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


def test_source_contracts_cover_every_source_authored_ref_constructor() -> None:
    expected = {
        descriptor.canonical_id
        for descriptor in REGISTRY._descriptors
        if descriptor.output_family is not None
        and descriptor.output_family.startswith("Ref[")
        and descriptor.effects is not None
        and "semantic_source" in descriptor.effects.mutations
    }

    assert set(REGISTRY._source_contracts) == expected
    assert all(
        isinstance(contract, AuthoringSourceContract)
        for contract in REGISTRY._source_contracts.values()
    )


def test_catalog_type_contract_uses_the_closed_member_contract() -> None:
    from marivo.semantic.catalog import CatalogCollection, SemanticCatalog

    catalog_contract = TYPE_CONTRACTS[SemanticCatalog]
    collection_contract = TYPE_CONTRACTS[CatalogCollection]

    assert catalog_contract.public_properties == CATALOG_COLLECTION_PROPERTIES
    assert catalog_contract.public_methods == (
        "items",
        "require",
        "preview",
        "preview_many",
        "source_health",
        "readiness",
        "render",
        "show",
    )
    assert collection_contract.public_properties == ("items", "refs")
    assert collection_contract.public_methods == ("get", "show", "render")
    assert not hasattr(collection_contract, "state_bearing")


def test_preview_capability_is_one_entry_or_exact_ref() -> None:
    import marivo.semantic as ms

    preview = REGISTRY.by_canonical_id("preview")
    subject = next(
        requirement for requirement in preview.input_requirements if requirement.role == "subject"
    )

    assert preview.output_family == "PreviewResult"
    assert subject.min_count == 1
    assert subject.max_count == 1
    assert subject.family == "CatalogEntry | Ref"
    assert preview.minimal_example is not None
    assert "catalog.preview(revenue" in preview.minimal_example
    assert preview.effects is not None
    assert preview.effects.mutations == ()
    assert "may_publish_certified_artifact" in preview.effects.flags
    preview_many = REGISTRY.by_canonical_id("preview_many")
    assert preview_many.output_family == "PreviewBatchResult"
    assert preview_many.effects is not None
    assert "may_publish_certified_artifact" not in preview_many.effects.flags
    assert ms.PreviewBatchResult in TYPE_CONTRACTS
