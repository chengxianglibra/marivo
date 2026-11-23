"""Bounded ontology help rendering."""

from __future__ import annotations

from marivo._authoring.render import effect_lines
from marivo.introspection.capabilities.render import type_contract, type_lines
from marivo.introspection.live.model import EnvironmentFingerprint
from marivo.introspection.live.reflect import signature_line
from marivo.introspection.live.render import bounded_help as _bounded
from marivo.introspection.live.render import render_fingerprint
from marivo.ontology._help_specs.manifest import (
    REGISTRY,
    TYPE_CONTRACTS,
    OntologyDescriptor,
)


def render_root_help() -> str:
    lines = [
        "marivo.ontology",
        render_fingerprint(EnvironmentFingerprint.current(), reveal=False),
        "",
        "  Python imports:",
        "    import marivo",
        "    import marivo.ontology as mo",
        "    import marivo.semantic as ms",
        "",
        "  Optional knowledge context over exact semantic refs:",
    ]
    lines.extend(
        f'    {descriptor.canonical_id}: {descriptor.summary} -> marivo.help("ontology.{descriptor.canonical_id}")'
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


def render_type(type_name: str) -> str:
    contract = type_contract(TYPE_CONTRACTS, type_name)
    lines = type_lines(f"ontology.{type_name}", contract)
    if "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    if type_name == "OntologyCatalog":
        lines.append(
            "  .show() and .render() summarize at most the first 8 edges by name; guardrails are omitted."
        )
        lines.append("  There is no public edge search, full enumeration, or individual edge read.")
    if type_name == "SemanticEdgeRef":
        lines.append("  Serialization: call .to_dict() for marivo.ontology_ref/v1 identity.")
    return _bounded("\n".join(lines))


def render_error_contract(error_name: str) -> str:
    lines = [
        f"ontology.{error_name}",
        "  Ontology error contract.",
        "  Concrete instances render their available message, expected, received, location, and structured repair fields.",
    ]
    if error_name == "OntologyLoadError":
        lines.append("  Inspect error.issues and print each issue for its details and repair.")
    return _bounded("\n".join(lines))


def render_descriptor(descriptor: OntologyDescriptor) -> str:
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
        lines.append(signature_line(descriptor.callable_path))
    lines.append(f"  Output family: {descriptor.output_family}")
    if descriptor.body:
        lines.append("  Contract:")
        lines.extend(f"    {line}" for line in descriptor.body)
    lines.append("  Example:")
    lines.extend(f"    {line}" if line else "" for line in descriptor.minimal_example.splitlines())
    if descriptor.constraints:
        lines.append("  Constraints:")
        lines.extend(f"    {constraint}" for constraint in descriptor.constraints)
    lines.extend(effect_lines(descriptor.effects))
    return _bounded("\n".join(lines))
