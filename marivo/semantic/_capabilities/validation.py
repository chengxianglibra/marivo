"""Mechanical validation for the private semantic live registry."""

from __future__ import annotations

import ast
import inspect
from typing import Literal

from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.semantic._capabilities.registry import (
    ERROR_TYPES,
    INPUT_FAMILIES,
    OUTPUT_FAMILIES,
    REGISTRY,
    TYPE_CONTRACTS,
)
from marivo.semantic.constraints import CONSTRAINTS

_ROOT_GROUP_LABELS = {
    "browse_load": "Browse and load",
    "author_families": "Author by object family",
    "verify_preview": "Verify and preview",
    "readiness": "Readiness",
    "diagnostics_boundaries": "Diagnostics and boundaries",
}


def _target_text(target: LiveHelpTarget) -> str:
    return f"{target.surface}.{target.canonical_id}"


def _call_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _validate_minimal_example_signature(
    *, example: str, public_entrypoint: str, callable_obj: object
) -> None:
    tree = ast.parse(example)
    matching_calls = tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == public_entrypoint
    )
    assert len(matching_calls) == 1, (
        f"minimal example must call {public_entrypoint!r} exactly once: {example}"
    )

    call = matching_calls[0]
    assert all(not isinstance(argument, ast.Starred) for argument in call.args), (
        f"minimal example must not use starred positional arguments: {example}"
    )
    assert all(keyword.arg is not None for keyword in call.keywords), (
        f"minimal example must not use expanded keyword arguments: {example}"
    )
    assert callable(callable_obj)
    signature = inspect.signature(callable_obj)
    positional: list[object] = [object() for _ in call.args]
    parameters = tuple(signature.parameters.values())
    if parameters and parameters[0].name in {"self", "cls"}:
        positional.insert(0, object())
    keywords = {keyword.arg: object() for keyword in call.keywords if keyword.arg is not None}
    try:
        signature.bind(*positional, **keywords)
    except TypeError as exc:
        raise AssertionError(
            f"minimal example for {public_entrypoint!r} does not match {signature}: {example}"
        ) from exc


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
            descriptor.produced_state.id if descriptor.produced_state is not None else "",
            ", ".join(state.id for state in descriptor.required_states),
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


def validate_semantic_live_surface() -> None:
    """Assert that private registry facts remain aligned with the live surface."""
    canonical_ids = REGISTRY.canonical_ids()
    callable_ids = REGISTRY.callable_ids()
    assert len(canonical_ids) == len(set(canonical_ids))
    assert len(callable_ids) == len(set(callable_ids))

    callable_paths = tuple(
        REGISTRY.by_canonical_id(canonical_id).callable_path for canonical_id in callable_ids
    )
    assert len(callable_paths) == len(set(callable_paths))

    produced_state_ids = {
        descriptor.produced_state.id
        for canonical_id in canonical_ids
        if (descriptor := REGISTRY.by_canonical_id(canonical_id)).produced_state is not None
    }
    required_state_ids = {
        state.id
        for canonical_id in canonical_ids
        for state in REGISTRY.by_canonical_id(canonical_id).required_states
    }
    assert required_state_ids <= produced_state_ids

    registered_constraints = {str(constraint_id) for constraint_id in CONSTRAINTS}
    for canonical_id in canonical_ids:
        descriptor = REGISTRY.by_canonical_id(canonical_id)
        assert descriptor.surface == "semantic"
        assert descriptor.effects is not None
        assert descriptor.output_family is None or descriptor.output_family in OUTPUT_FAMILIES
        assert set(descriptor.constraints) <= registered_constraints
        assert all(
            requirement.family in INPUT_FAMILIES for requirement in descriptor.input_requirements
        )
        assert all(target.surface and target.canonical_id for target in descriptor.see_also)
        if descriptor.callable_path is not None:
            callable_obj = import_callable(descriptor.callable_path)
            assert REGISTRY.by_callable(callable_obj) is descriptor
            assert descriptor.minimal_example is not None
            assert "..." not in descriptor.minimal_example
            assert descriptor.public_entrypoint is not None
            _validate_minimal_example_signature(
                example=descriptor.minimal_example,
                public_entrypoint=descriptor.public_entrypoint,
                callable_obj=callable_obj,
            )

    group_names: tuple[
        Literal[
            "browse_load",
            "author_families",
            "verify_preview",
            "readiness",
            "diagnostics_boundaries",
        ],
        ...,
    ] = (
        "browse_load",
        "author_families",
        "verify_preview",
        "readiness",
        "diagnostics_boundaries",
    )
    group_members = tuple(member for group in group_names for member in REGISTRY.group(group))
    group_ids = tuple(member.canonical_id for member in group_members)
    assert len(group_ids) == len(set(group_ids))
    assert set(group_ids) <= set(canonical_ids)

    for contract in TYPE_CONTRACTS.values():
        assert all(
            not property_name.startswith("_") for property_name in contract.public_properties
        )
        assert all(not method_name.startswith("_") for method_name in contract.public_methods)
        assert all(
            REGISTRY.by_canonical_id(target.canonical_id or "").surface == target.surface
            for target in (*contract.producers, *contract.consumers)
        )
        assert not contract.state_bearing or "contract" in contract.public_methods

    root_text = "\n".join(
        (
            *(
                "\n".join(
                    (
                        _ROOT_GROUP_LABELS[group],
                        *(
                            f"{descriptor.canonical_id}: {descriptor.summary}"
                            for descriptor in REGISTRY.group(group)
                        ),
                    )
                )
                for group in group_names
            ),
            "Consumed types and errors: "
            + ", ".join(
                (*tuple(contract.name for contract in TYPE_CONTRACTS.values()), *ERROR_TYPES)
            ),
        )
    )
    assert root_text.count("\n") + 1 <= SURFACE_LIMITS.root_help_max_lines
    assert len(root_text) <= SURFACE_LIMITS.root_help_max_codepoints
    for canonical_id in canonical_ids:
        focused_text = _focused_budget_text(canonical_id)
        assert focused_text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
        assert len(focused_text) <= SURFACE_LIMITS.focused_help_max_codepoints
