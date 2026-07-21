"""Bounded semantic help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import enforce_budget, render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.semantic._capabilities.registry import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.semantic.constraints import iter_constraints

if TYPE_CHECKING:
    from marivo.semantic._capabilities.model import SemanticTypeContract

_GROUPS = (
    ("browse_load", "Browse and load"),
    ("author_families", "Author by object family"),
    ("verify_preview", "Verify and preview"),
    ("readiness", "Readiness"),
    ("diagnostics_boundaries", "Diagnostics and boundaries"),
)

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"


def _bounded(text: str, *, root: bool = False) -> str:
    """Apply the one shared registered render budget."""
    return enforce_budget(
        text,
        max_lines=(
            SURFACE_LIMITS.root_help_max_lines if root else SURFACE_LIMITS.focused_help_max_lines
        ),
        max_codepoints=(
            SURFACE_LIMITS.root_help_max_codepoints
            if root
            else SURFACE_LIMITS.focused_help_max_codepoints
        ),
    )


def _target_text(target: LiveHelpTarget) -> str:
    return target.canonical_id or target.surface


def _with_python_imports(text: str) -> str:
    """Make a focused semantic help page executable from a cold start."""
    imports = [_SEMANTIC_IMPORT]
    if "md." in text:
        imports.insert(0, _DATASOURCE_IMPORT)
    lines = text.splitlines()
    return _bounded(
        "\n".join(
            (
                lines[0],
                "  Python imports:",
                *(f"    {statement}" for statement in imports),
                "",
                *lines[1:],
            )
        )
    )


def _constraints(descriptor: AuthoringCapability) -> tuple[str, ...]:
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return tuple(
        f"{constraint_id}: {catalog[constraint_id].title}"
        for constraint_id in descriptor.constraints
        if constraint_id in catalog
    )


def render_root_help() -> str:
    """Render the semantic root index with its exact environment fingerprint."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    lines = [
        "marivo.semantic",
        render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
        "",
        "Python imports:",
        f"  {_SEMANTIC_IMPORT}",
        "",
        "Capabilities:",
    ]
    for group, label in _GROUPS:
        descriptors = REGISTRY.group(group)  # type: ignore[arg-type]
        if not descriptors:
            continue
        lines.append(f"  {label}:")
        for descriptor in descriptors:
            output = descriptor.output_family or "None"
            effects = descriptor.effects
            assert effects is not None
            effect_values = (
                *(value for value in (effects.data_access, effects.connection) if value != "none"),
                *effects.mutations,
                *effects.flags,
            )
            badges = ", ".join(effect_values) or "none"
            lines.append(
                f"    {descriptor.canonical_id:<34} {descriptor.summary} "
                f"[output: {output}; effects: {badges}]"
            )
    lines.extend(
        (
            "",
            "Identity handoff: navigate to a CatalogEntry, then pass entry.ref to "
            "verify, preview, readiness, or analysis; use ms.ref.<kind>(path) when "
            "the exact identity is already known.",
            "",
            "Consumed types: " + ", ".join(contract.name for contract in TYPE_CONTRACTS.values()),
            "Errors: " + ", ".join(ERROR_TYPES),
            "",
            'Call ms.help("<target>") for a capability, public type, result, or semantic error.',
        )
    )
    return _bounded("\n".join(lines), root=True)


def _render_authoring(descriptor: AuthoringCapability) -> str:
    state_rows = [
        candidate
        for candidate in (REGISTRY.by_canonical_id(value) for value in REGISTRY.canonical_ids())
        if candidate.produced_state is not None
        and candidate.produced_state.id.startswith("semantic.")
    ]
    lines = ["authoring", f"  {descriptor.summary}", "", "  Registered semantic states:"]
    for candidate in state_rows:
        assert candidate.produced_state is not None
        lines.append(f"    {candidate.produced_state.id} <- {candidate.canonical_id}")
    lines.extend(
        (
            "",
            "  Semantic guidance ends at semantic.ready and the analysis handoff.",
            '  Continue datasource authoring with md.help("authoring").',
        )
    )
    return _bounded("\n".join(lines))


def _render_boundary(descriptor: AuthoringCapability) -> str:
    """Render a non-callable boundary capability.

    Boundary capabilities are concepts carried on result fields of other
    capabilities, not callable entrypoints. Rendering them with the callable
    ``Output family`` / ``Effects`` block advertises a call that does not
    exist (see issue #19). Point agents at the producing capability and the
    result field instead.
    """
    lines = [descriptor.canonical_id, f"  {descriptor.summary}", "", "  Not a callable entrypoint."]
    if descriptor.canonical_id == "analysis_handoff":
        lines.extend(
            (
                "  The typed handoff is produced by readiness and carried on a result",
                "  field: catalog.readiness(refs=...) returns a ReadinessReport whose",
                "  .analysis_handoff is a SemanticToAnalysisHandoff (None while any ref",
                "  is blocked). Analysis consumes it via",
                "  Session.validate_semantic_handoff(report.analysis_handoff).",
            )
        )
    if descriptor.see_also:
        lines.append(
            "  See also: " + ", ".join(_target_text(target) for target in descriptor.see_also)
        )
    return _bounded("\n".join(lines))


def _render_descriptor(descriptor: AuthoringCapability) -> str:
    if descriptor.canonical_id == "authoring":
        return _render_authoring(descriptor)
    if descriptor.kind == "boundary":
        return _render_boundary(descriptor)

    lines = [descriptor.canonical_id, f"  {descriptor.summary}", ""]
    if descriptor.public_entrypoint is not None:
        lines.append(f"  Entrypoint: {descriptor.public_entrypoint}")
    if descriptor.callable_path is not None:
        callable_obj = import_callable(descriptor.callable_path)
        assert callable(callable_obj)
        lines.append(f"  Signature: {inspect.signature(callable_obj)}")
    if descriptor.input_requirements:
        lines.append("  Input families:")
        for requirement in descriptor.input_requirements:
            detail = f" ({', '.join(requirement.exact_keys)})" if requirement.exact_keys else ""
            optional = " optional" if requirement.min_count == 0 else ""
            lines.append(f"    {requirement.role}: {requirement.family}{detail}{optional}")
    lines.append(f"  Output family: {descriptor.output_family or 'None'}")
    if descriptor.preconditions:
        lines.append(f"  Preconditions: {', '.join(descriptor.preconditions)}")
    if descriptor.required_states:
        lines.append(
            "  Required state: " + ", ".join(state.id for state in descriptor.required_states)
        )
    if descriptor.produced_state is not None:
        lines.append(f"  Produces state: {descriptor.produced_state.id}")
    effects = descriptor.effects
    assert effects is not None
    lines.extend(
        (
            "  Effects:",
            f"    data access: {effects.data_access}",
            f"    connection: {effects.connection}",
            f"    mutations: {', '.join(effects.mutations) or 'none'}",
            f"    flags: {', '.join(effects.flags) or 'none'}",
        )
    )
    if descriptor.minimal_example is not None:
        lines.extend(("  Example:", f"    {descriptor.minimal_example}"))
    constraints = _constraints(descriptor)
    if constraints:
        lines.append("  Constraints:")
        lines.extend(f"    {constraint}" for constraint in constraints)
    consumers = [
        other.canonical_id
        for other in (REGISTRY.by_canonical_id(value) for value in REGISTRY.canonical_ids())
        if descriptor.output_family is not None
        and any(
            requirement.family == descriptor.output_family
            for requirement in other.input_requirements
        )
    ]
    if consumers:
        lines.append("  Consumers: " + ", ".join(consumers))
    if descriptor.see_also:
        lines.append(
            "  See also: " + ", ".join(_target_text(target) for target in descriptor.see_also)
        )
    return _bounded("\n".join(lines))


def _contract_for_name(type_name: str) -> SemanticTypeContract:
    for contract in TYPE_CONTRACTS.values():
        if contract.name == type_name:
            return contract
    raise RuntimeError(f"unknown semantic type contract: {type_name}")


def _render_type(type_name: str, original: object | None) -> str:
    contract = _contract_for_name(type_name)
    lines = [type_name]
    if contract.producers:
        lines.append(
            "  Producers: " + ", ".join(_target_text(target) for target in contract.producers)
        )
    if contract.public_properties:
        lines.append("  Public fields: " + ", ".join(contract.public_properties))
    if contract.public_methods:
        lines.append("  Public consumption: " + ", ".join(contract.public_methods))
    if contract.consumers:
        lines.append(
            "  Consumers: " + ", ".join(_target_text(target) for target in contract.consumers)
        )
    if type_name == "Ref":
        lines.extend(
            (
                "  Construction: use one exact factory such as "
                "ms.ref.metric('sales.revenue') or "
                "ms.ref.dimension('sales.orders.region').",
                "  Catalog handoff: entry = catalog.require(ms.ref.metric('sales.revenue')); "
                "metric_ref = entry.ref.",
                "  Field application: Ref values are never callable; use "
                "ms.bind(field_ref, entity_alias) inside a registered semantic "
                "expression body.",
            )
        )
    if type_name == "ref":
        lines.append(
            "  Construction namespace: use ms.ref.<kind>(path); every factory returns "
            "one immutable Ref[kind]."
        )
    if type_name == "CatalogEntry":
        lines.append(
            "  Identity handoff: pass entry.ref to catalog.verify, catalog.preview, "
            "catalog.readiness, or analysis APIs."
        )
    if "details" in contract.public_methods:
        lines.append(
            "  Inspection: call .details() for structured semantic metadata; "
            ".details().show() for bounded readable detail."
        )
    elif "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    if "show" in contract.public_methods and "render" in contract.public_methods:
        lines.append("  Display: .show() prints the same bounded card returned by .render().")
    if "contract" in contract.public_methods:
        if type_name == "Metric":
            lines.append(
                "  Continuation: .contract() only exposes mechanically executable "
                "verify, preview, and readiness actions."
            )
        else:
            lines.append("  Continuation: call .contract() for mechanically valid next actions.")
    return _bounded("\n".join(lines))


def _render_error(error_name: str, original: object | None) -> str:
    lines = [error_name, "  Semantic error contract."]
    if original is not None:
        for name in ("message", "location", "expected", "received"):
            value = getattr(original, name, None)
            if value is not None:
                lines.append(f"  {name.title()}: {value}")
        repair = getattr(original, "repair", None)
        if repair is not None:
            lines.append(f"  Repair: {repair.action}")
            if repair.candidates:
                lines.append("  Candidates: " + ", ".join(repair.candidates))
    lines.append('  Use ms.help("<target>") to inspect the recommended capability.')
    return _bounded("\n".join(lines))


def render_help_target(
    resolved: ResolvedLiveTarget[AuthoringCapability],
    *,
    original_target: object | None = None,
) -> str:
    """Render a resolved semantic target without invoking runtime operations."""
    if resolved.kind == "descriptor" and resolved.descriptor is not None:
        return _with_python_imports(_render_descriptor(resolved.descriptor))
    if resolved.kind == "type_contract" and resolved.type_name is not None:
        return _with_python_imports(_render_type(resolved.type_name, original_target))
    if resolved.kind in {"error_contract", "error_briefing"} and resolved.error_name is not None:
        return _with_python_imports(_render_error(resolved.error_name, resolved.original))
    raise RuntimeError(f"unsupported semantic help resolution: {resolved.kind}")
