"""Mechanical validation for the private datasource live registry."""

from __future__ import annotations

from typing import Literal

from marivo.datasource._capabilities.registry import (
    ERROR_TYPES,
    INPUT_FAMILIES,
    OUTPUT_FAMILIES,
    REGISTRY,
    TYPE_CONTRACTS,
)
from marivo.datasource.constraints import CONSTRAINTS
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable


def _target_text(target: LiveHelpTarget) -> str:
    surface = target.surface
    canonical_id = target.canonical_id
    return f"{surface}.{canonical_id}"


def _focused_budget_text(canonical_id: str) -> str:
    descriptor = REGISTRY.by_canonical_id(canonical_id)
    requirements = ", ".join(
        f"{requirement.role}:{requirement.family}:{','.join(requirement.exact_keys)}"
        for requirement in descriptor.input_requirements
    )
    effects = descriptor.effects
    assert effects is not None
    return "\n".join(
        (
            descriptor.canonical_id,
            descriptor.public_entrypoint or "",
            descriptor.callable_path or "",
            descriptor.summary,
            requirements,
            descriptor.output_family or "",
            ", ".join(descriptor.preconditions),
            effects.data_access,
            effects.connection,
            ", ".join(effects.mutations),
            ", ".join(effects.flags),
            ", ".join(descriptor.constraints),
            descriptor.minimal_example or "",
            ", ".join(_target_text(target) for target in descriptor.see_also),
            ", ".join(descriptor.repair_kinds),
        )
    )


def validate_datasource_live_surface() -> None:
    """Assert that private registry facts remain aligned with the live surface."""
    import marivo.datasource as md

    canonical_ids = REGISTRY.canonical_ids()
    callable_ids = REGISTRY.callable_ids()
    assert len(canonical_ids) == len(set(canonical_ids))
    assert len(callable_ids) == len(set(callable_ids))

    callable_paths = tuple(
        REGISTRY.by_canonical_id(canonical_id).callable_path for canonical_id in callable_ids
    )
    assert len(callable_paths) == len(set(callable_paths))

    registered_constraints = {str(constraint_id) for constraint_id in CONSTRAINTS}
    for canonical_id in canonical_ids:
        descriptor = REGISTRY.by_canonical_id(canonical_id)
        assert descriptor.surface == "datasource"
        assert descriptor.effects is not None
        assert descriptor.output_family is None or descriptor.output_family in OUTPUT_FAMILIES
        assert set(descriptor.constraints) <= registered_constraints
        assert all(
            requirement.family in INPUT_FAMILIES for requirement in descriptor.input_requirements
        )
        assert all(target.surface for target in descriptor.see_also)
        if descriptor.callable_path is not None:
            callable_obj = import_callable(descriptor.callable_path)
            assert REGISTRY.by_callable(callable_obj) is descriptor
            assert descriptor.minimal_example is not None
            assert "..." not in descriptor.minimal_example

    group_names: tuple[Literal["connections", "sources", "inspection", "root"], ...] = (
        "connections",
        "sources",
        "inspection",
        "root",
    )
    group_members = tuple(member for group in group_names for member in REGISTRY.group(group))
    group_ids = tuple(member.canonical_id for member in group_members)
    assert len(group_ids) == len(set(group_ids))
    assert set(group_ids) <= set(canonical_ids)
    assert set(REGISTRY.discovery_ids()) <= set(group_ids)

    for type_obj, contract in TYPE_CONTRACTS.items():
        assert all(
            not property_name.startswith("_") for property_name in contract.public_properties
        )
        assert all(not method_name.startswith("_") for method_name in contract.public_methods)
        assert all(
            callable(getattr(type_obj, method_name, None))
            for method_name in contract.public_methods
        )
        for method_name in contract.public_methods:
            method = getattr(type_obj, method_name)
            if method.__module__.startswith("marivo.datasource"):
                REGISTRY.by_callable(method)
        assert all(
            REGISTRY.by_canonical_id(target.canonical_id or "").surface == target.surface
            for target in (*contract.producers, *contract.consumers)
        )
    from marivo.datasource._capabilities.render import render_root_help

    root_text = render_root_help()
    assert root_text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(root_text) <= SURFACE_LIMITS.root_help_max_codepoints
    for canonical_id in ("", "connections", "sources", "inspection"):
        routes = REGISTRY.navigation_routes(canonical_id)
        assert routes and len(routes) <= 18
        assert len({route.target for route in routes}) == len(routes)
        for route in routes:
            assert route.label and route.summary
            REGISTRY.by_canonical_id(route.target.canonical_id or "")
    for canonical_id in canonical_ids:
        focused_text = _focused_budget_text(canonical_id)
        assert focused_text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
        assert len(focused_text) <= SURFACE_LIMITS.focused_help_max_codepoints

    for exported_name in md.__all__:
        exported = getattr(md, exported_name)
        if isinstance(exported, type):
            assert exported in TYPE_CONTRACTS or exported in ERROR_TYPES.values()
        if callable(exported) and not isinstance(exported, type):
            assert REGISTRY.by_callable(exported)
