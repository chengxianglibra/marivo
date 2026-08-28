"""Mechanical validation for the private semantic live registry."""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Callable
from typing import cast

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.semantic._capabilities.catalog_members import CATALOG_COLLECTION_PROPERTIES
from marivo.semantic._capabilities.registry import (
    INPUT_FAMILIES,
    OUTPUT_FAMILIES,
    REGISTRY,
    TYPE_CONTRACTS,
)
from marivo.semantic.constraints import CONSTRAINTS

_RETURN_FAMILY_ALIASES = {
    "Ref[DomainKind]": "Ref[domain]",
    "Ref[DatasourceKind]": "Ref[datasource]",
    "Ref[EntityKind]": "Ref[entity]",
    "Ref[DimensionKind]": "Ref[dimension]",
    "Ref[DimensionKindTag]": "Ref[dimension]",
    "Ref[TimeDimensionKind]": "Ref[time_dimension]",
    "Ref[MeasureKind]": "Ref[measure]",
    "Ref[MetricKind]": "Ref[metric]",
    "Ref[RelationshipKind]": "Ref[relationship]",
    "Ref[EventKind]": "Ref[event]",
    "Ref[StateModelKind]": "Ref[state_model]",
    "Ref[PeriodCalendarKind]": "Ref[period_calendar]",
    "Ref[TemporalSetKind]": "Ref[temporal_set]",
    "Ref[WorkScheduleKind]": "Ref[work_schedule]",
    "ir.BooleanValue": "IbisValue",
    "ir.Value": "IbisValue",
    "SnapshotVersioningIR": "ValiditySpec",
    "ValidityVersioningIR": "ValiditySpec",
    "SemiAdditive": "Additivity",
    "DatetimeParse": "DateTimeSpec",
    "TimestampParse": "TimestampSpec",
    "StrptimeParse": "StrptimeSpec",
    "HourPrefixParse": "HourPrefixSpec",
    "Trailing": "TrailingSpec",
}


def _target_text(target: LiveHelpTarget) -> str:
    return f"{target.surface}.{target.canonical_id}"


def _is_registered_semantic_target(target: LiveHelpTarget) -> bool:
    if target.surface != "semantic" or target.canonical_id is None:
        return False
    try:
        REGISTRY.by_canonical_id(target.canonical_id)
    except KeyError:
        return False
    return True


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


def _public_signature(callable_obj: object) -> inspect.Signature:
    assert callable(callable_obj)
    signature = inspect.signature(callable_obj)
    parameters = tuple(signature.parameters.values())
    if parameters and parameters[0].name in {"self", "cls"}:
        return signature.replace(parameters=parameters[1:])
    return signature


def _validate_parameter_metadata(
    descriptor: AuthoringCapability,
    callable_obj: object,
) -> None:
    signature = _public_signature(callable_obj)
    for requirement in descriptor.input_requirements:
        for parameter_name in requirement.parameter_names:
            assert parameter_name in signature.parameters, (
                f"{descriptor.canonical_id} input fact names missing live parameter "
                f"{parameter_name!r}"
            )
        if not requirement.parameter_names:
            continue
        parameters = tuple(signature.parameters[name] for name in requirement.parameter_names)
        optional = all(parameter.default is not inspect.Parameter.empty for parameter in parameters)
        if requirement.min_count == 0:
            assert optional, (
                f"{descriptor.canonical_id} marks required live parameters optional: "
                f"{requirement.parameter_names!r}"
            )
        elif len(parameters) == 1:
            assert not optional, (
                f"{descriptor.canonical_id} marks optional live parameter required: "
                f"{requirement.parameter_names!r}"
            )


def _annotation_output_family(
    descriptor: AuthoringCapability,
    callable_obj: object,
) -> str | None:
    annotation = inspect.signature(cast("Callable[..., object]", callable_obj)).return_annotation
    if annotation is inspect.Signature.empty:
        return None
    text = (
        annotation.strip() if isinstance(annotation, str) else inspect.formatannotation(annotation)
    )
    if descriptor.invocation_shape == "decorator":
        ref_products = re.findall(r"Ref\[[A-Za-z_][A-Za-z0-9_]*\]", text)
        assert len(ref_products) == 1, (
            f"{descriptor.canonical_id} decorator return does not expose one Ref product: {text}"
        )
        text = ref_products[0]
    if text.startswith("CatalogCollection["):
        return "CatalogCollection"
    if text.startswith("CatalogEntry["):
        return "CatalogEntry"
    return _RETURN_FAMILY_ALIASES.get(text, text)


def _validate_output_metadata(
    descriptor: AuthoringCapability,
    callable_obj: object,
) -> None:
    if descriptor.output_family is None:
        return
    actual = _annotation_output_family(descriptor, callable_obj)
    assert actual == descriptor.output_family, (
        f"{descriptor.canonical_id} output family drift: "
        f"registered={descriptor.output_family!r}, installed={actual!r}"
    )


def _focused_budget_text(canonical_id: str) -> str:
    descriptor = REGISTRY.by_canonical_id(canonical_id)
    assert isinstance(descriptor, AuthoringCapability)
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


def validate_semantic_live_surface() -> None:
    """Assert that private registry facts remain aligned with the live surface."""
    canonical_ids = REGISTRY.canonical_ids()
    callable_ids = REGISTRY.callable_ids()
    assert len(canonical_ids) == len(set(canonical_ids))
    assert len(callable_ids) == len(set(callable_ids))

    callable_paths = tuple(
        descriptor.callable_path
        for descriptor in REGISTRY.descriptors
        if descriptor.callable_path is not None
    )
    assert len(callable_paths) == len(set(callable_paths))

    registered_constraints = {str(constraint_id) for constraint_id in CONSTRAINTS}
    for descriptor in REGISTRY.descriptors:
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
            _validate_parameter_metadata(descriptor, callable_obj)
            _validate_output_metadata(descriptor, callable_obj)

    source_authored_ids = {
        descriptor.canonical_id
        for descriptor in REGISTRY.descriptors
        if descriptor.output_family is not None
        and descriptor.output_family.startswith("Ref[")
        and descriptor.effects is not None
        and "semantic_source" in descriptor.effects.mutations
    }
    assert set(REGISTRY._source_contracts) == source_authored_ids
    for source_contract in REGISTRY._source_contracts.values():
        assert source_contract.catalog_collection in CATALOG_COLLECTION_PROPERTIES
        assert source_contract.path_template.startswith("models/semantic/")
        assert source_contract.canonical_identity_template.startswith("<domain>")

    root_routes = tuple(target for section in REGISTRY.root_sections for target in section.members)
    assert len(root_routes) == len(set(root_routes))
    assert len(root_routes) <= REGISTRY.render_budget("root").max_outgoing_routes
    assert set(REGISTRY.discovery_ids()) <= set(canonical_ids)
    for help_descriptor in REGISTRY.help_descriptors:
        assert (
            len(REGISTRY.routes(help_descriptor.canonical_id))
            <= REGISTRY.render_budget(
                REGISTRY.render_class(help_descriptor.canonical_id)
            ).max_outgoing_routes
        )

    for contract in TYPE_CONTRACTS.values():
        assert all(
            not property_name.startswith("_") for property_name in contract.public_properties
        )
        assert all(not method_name.startswith("_") for method_name in contract.public_methods)
        # Cross-surface producer edges are resolved by unified-help tests so this
        # semantic-owned validator does not reverse the semantic -> analysis boundary.
        assert all(
            target.surface != "semantic" or _is_registered_semantic_target(target)
            for target in (*contract.producers, *contract.consumers)
        )
    for canonical_id in tuple(descriptor.canonical_id for descriptor in REGISTRY.descriptors):
        focused_text = _focused_budget_text(canonical_id)
        assert focused_text.count("\n") + 1 <= SURFACE_LIMITS.focused_help_max_lines
        assert len(focused_text) <= SURFACE_LIMITS.focused_help_max_codepoints
