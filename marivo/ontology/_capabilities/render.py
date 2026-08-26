"""Bounded ontology help rendering."""

from __future__ import annotations

import inspect

from marivo.introspection.live.model import SURFACE_LIMITS, EnvironmentFingerprint, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import enforce_budget, render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.ontology._capabilities.registry import (
    REGISTRY,
    TYPE_CONTRACTS,
    OntologyDescriptor,
    OntologyTypeContract,
)


def _bounded(text: str, *, root: bool = False) -> str:
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.root_help_max_lines
        if root
        else SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=(
            SURFACE_LIMITS.root_help_max_codepoints
            if root
            else SURFACE_LIMITS.focused_help_max_codepoints
        ),
    )


def render_root_help() -> str:
    lines = [
        "marivo.ontology",
        render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
        "",
        "  Python imports:",
        "    import marivo",
        "    import marivo.ontology as mo",
        "    import marivo.semantic as ms",
        "",
        "  Optional knowledge context over exact semantic refs:",
    ]
    lines.extend(
        f"    {descriptor.canonical_id}: {descriptor.summary}"
        for descriptor in (REGISTRY.by_canonical_id(value) for value in REGISTRY.canonical_ids())
    )
    lines.extend(
        (
            "",
            "  Ontology is not executable semantic authority and is never required for ordinary analysis.",
            '  Call marivo.help("ontology.<target>") for one exact contract.',
        )
    )
    return _bounded("\n".join(lines), root=True)


def _contract_for_name(type_name: str) -> OntologyTypeContract:
    for contract in TYPE_CONTRACTS.values():
        if contract.name == type_name:
            return contract
    raise RuntimeError(f"unknown ontology type contract: {type_name}")


def _help_invocation(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        return f'marivo.help("{target.surface}")'
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _render_type(type_name: str, original: object | None) -> str:
    contract = _contract_for_name(type_name)
    lines = [f"ontology.{type_name}"]
    if contract.producers:
        lines.append(
            "  Producers: " + ", ".join(_help_invocation(target) for target in contract.producers)
        )
    if contract.public_properties:
        lines.append("  Public fields: " + ", ".join(contract.public_properties))
    if contract.public_methods:
        lines.append("  Public consumption: " + ", ".join(contract.public_methods))
    if "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    if type_name == "SemanticEdgeRef":
        lines.append("  Serialization: call .to_dict() for marivo.ontology_ref/v1 identity.")
    if original is not None and TYPE_CONTRACTS.get(type(original)) is contract:
        public_values = []
        for name in contract.public_properties:
            try:
                value = getattr(original, name)
            except (AttributeError, RuntimeError):
                continue
            public_values.append(f"{name}={value!r}")
        if public_values:
            lines.append("  Runtime fields: " + ", ".join(public_values))
    return _bounded("\n".join(lines))


def _render_error_contract(error_name: str) -> str:
    return _bounded(
        "\n".join(
            (
                f"ontology.{error_name}",
                "  Ontology error contract.",
                "  Concrete instances render their available message, expected, received, location, and structured repair fields.",
            )
        )
    )


def _render_error_briefing(error_name: str, original: object) -> str:
    lines = [f"ontology.{error_name}", "  Ontology error briefing."]
    for name in ("message", "expected", "received", "location", "location_label"):
        value = getattr(original, name, None)
        if value is not None:
            label = "Location" if name == "location_label" else name.title()
            lines.append(f"  {label}: {value}")

    repair = getattr(original, "repair", None)
    if repair is None:
        return _bounded("\n".join(lines))
    help_target = getattr(repair, "help_target", None)
    if not isinstance(help_target, LiveHelpTarget):
        raise RuntimeError("error_briefing requires repair.help_target")
    lines.extend(
        (
            "  Repair:",
            f"    Kind: {repair.kind}",
            f"    Action: {repair.action}",
        )
    )
    if repair.snippet is not None:
        lines.append("    Snippet:")
        lines.extend(f"      {line}" for line in repair.snippet.splitlines())
    if repair.candidates:
        lines.append("    Candidates: " + ", ".join(repair.candidates))
    lines.append(f"    Next help: {_help_invocation(help_target)}")
    return _bounded("\n".join(lines))


def render_help_target(
    resolved: ResolvedLiveTarget[OntologyDescriptor], *, original_target: object
) -> str:
    if resolved.kind == "descriptor":
        descriptor = resolved.descriptor
        assert descriptor is not None
        lines = [
            f"ontology.{descriptor.canonical_id}",
            "  Python imports:",
            "    import marivo",
            "    import marivo.ontology as mo",
            "    import marivo.semantic as ms",
            "",
            f"  {descriptor.summary}",
        ]
        if descriptor.public_entrypoint is not None:
            lines.append(f"  Entrypoint: {descriptor.public_entrypoint}")
        if descriptor.callable_path is not None:
            callable_obj = import_callable(descriptor.callable_path)
            assert callable(callable_obj)
            lines.append(f"  Signature: {inspect.signature(callable_obj)}")
        lines.append(f"  Output family: {descriptor.output_family}")
        if descriptor.body:
            lines.append("  Contract:")
            lines.extend(f"    {line}" for line in descriptor.body)
        lines.append("  Example:")
        if descriptor.canonical_id in {"influences", "related_to"}:
            lines.append(
                "    # Declaration fragment; execute only when mo.load() evaluates models/ontology.py."
            )
        lines.extend(
            f"    {line}" if line else "" for line in descriptor.minimal_example.splitlines()
        )
        if descriptor.constraints:
            lines.append("  Constraints:")
            lines.extend(f"    {constraint}" for constraint in descriptor.constraints)
        lines.extend(
            (
                "  Effects:",
                f"    data access: {descriptor.effects.data_access}",
                f"    connection: {descriptor.effects.connection}",
                ("    mutations: " + (", ".join(descriptor.effects.mutations) or "none")),
                f"    flags: {', '.join(descriptor.effects.flags) or 'none'}",
            )
        )
        return _bounded("\n".join(lines))
    if resolved.kind == "type_contract" and resolved.type_name is not None:
        return _render_type(resolved.type_name, original_target)
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _render_error_contract(resolved.error_name)
    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        if resolved.original is None:
            raise RuntimeError("error_briefing requires original target")
        return _render_error_briefing(resolved.error_name, resolved.original)
    raise AssertionError(f"unsupported ontology help resolution {resolved.kind!r}")


__all__ = ["render_help_target", "render_root_help"]
