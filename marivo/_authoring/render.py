"""Shared rendering for domain-owned authoring effect declarations."""

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringRepair,
)
from marivo.introspection.live.reflect import signature_line


def input_lines(requirement: AuthoringInputRequirement) -> tuple[str, ...]:
    """Render native input facts and acquisition without inferring producers."""
    details: list[str] = []
    if requirement.parameter_names:
        details.append("parameters: " + ", ".join(requirement.parameter_names))
    if requirement.exact_keys:
        details.append("keys: " + ", ".join(requirement.exact_keys))
    detail = f" ({'; '.join(details)})" if details else ""
    optional = " optional" if requirement.min_count == 0 else ""
    lines = [f"    {requirement.role}: {requirement.family}{optional}{detail}"]
    if requirement.description:
        lines.append(f"      {requirement.description}")
    if requirement.acquisition:
        lines.append(
            "      Acquire: "
            + ", ".join(f'marivo.help("{target.display}")' for target in requirement.acquisition)
        )
    return tuple(lines)


def effect_lines(effects: AuthoringEffects) -> tuple[str, ...]:
    """Render the common effect block without inferring any capabilities."""
    return (
        "  Effects:",
        f"    data access: {effects.data_access}",
        f"    connection: {effects.connection}",
        f"    mutations: {', '.join(effects.mutations) or 'none'}",
        f"    flags: {', '.join(effects.flags) or 'none'}",
    )


def capability_lines(descriptor: AuthoringCapability, *, hide_receiver: bool = False) -> list[str]:
    """Render the common callable, input, output and effects contract."""
    lines = [descriptor.canonical_id, f"  {descriptor.summary}", ""]
    if descriptor.public_entrypoint is not None:
        if descriptor.callable_path is None:
            lines.append(f"  Syntax: {descriptor.public_entrypoint}(...)")
        else:
            lines.append(f"  Entrypoint: {descriptor.public_entrypoint}")
    if descriptor.callable_path is not None:
        lines.append(
            signature_line(
                descriptor.callable_path,
                hide_receiver=hide_receiver and descriptor.kind == "method",
            ).replace("_SemanticInput", "SemanticInput")
        )
    if descriptor.input_requirements:
        lines.append("  Input families:")
        for requirement in descriptor.input_requirements:
            lines.extend(input_lines(requirement))
    lines.append(f"  Output family: {descriptor.output_family or 'None'}")
    if descriptor.preconditions:
        lines.append(f"  Preconditions: {', '.join(descriptor.preconditions)}")
    if descriptor.effects is None:
        raise RuntimeError(f"Help capability lacks effects: {descriptor.canonical_id}")
    lines.extend(effect_lines(descriptor.effects))
    if descriptor.telemetry:
        lines.append(
            "  Usage telemetry may write local .marivo/telemetry when enabled; effects above describe business operations."
        )
    return lines


def _repair_summary(repair: AuthoringRepair, *, optional: bool = False) -> str:
    """Render an existing repair's action and qualified Help route."""
    label = "optional fix" if optional else "fix"
    target = repair.help_target
    qualified: str = target.surface
    if target.canonical_id is not None:
        qualified += f".{target.canonical_id}"
    return f'{label}: {repair.action}; help: marivo.help("{qualified}")'
