"""Bounded datasource help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo.datasource._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.datasource.constraints import iter_constraints
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import enforce_budget, render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget

if TYPE_CHECKING:
    from marivo.datasource._capabilities.model import DatasourceTypeContract


_GROUPS = (
    ("declare_manage", "Declare and manage"),
    ("physical_sources", "Physical sources"),
    ("inspect_scope", "Inspect and scope"),
    ("acquire_project", "Acquire and project evidence"),
    ("diagnostics_boundaries", "Diagnostics and boundaries"),
)

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"
_ANALYSIS_IMPORT = "import marivo.analysis as mv"
_MARIVO_IMPORT = "import marivo"


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
    """Make a focused datasource help page executable from a cold start."""
    lines = text.splitlines()
    imports = [_MARIVO_IMPORT, _DATASOURCE_IMPORT]
    if "ms." in text:
        imports.append(_SEMANTIC_IMPORT)
    if "mv." in text:
        imports.append(_ANALYSIS_IMPORT)
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
    """Render the datasource root index with its exact environment fingerprint."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    lines = [
        "marivo.datasource",
        render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
        "",
        "Python imports:",
        f"  {_DATASOURCE_IMPORT}",
        "",
        "Capabilities:",
    ]
    for group, label in _GROUPS:
        descriptors = REGISTRY.group(group)  # type: ignore[arg-type]
        if not descriptors:
            continue
        lines.append(f"  {label}:")
        for descriptor in descriptors:
            effects = descriptor.effects
            assert effects is not None
            effect_values = (
                *(value for value in (effects.data_access, effects.connection) if value != "none"),
                *effects.mutations,
                *effects.flags,
            )
            annotations: list[str] = []
            if descriptor.output_family is not None:
                annotations.append(f"-> {descriptor.output_family}")
            if effect_values:
                annotations.append(f"effects: {', '.join(effect_values)}")
            suffix = f" [{'; '.join(annotations)}]" if annotations else ""
            lines.append(f"    {descriptor.canonical_id:<34} {descriptor.summary}{suffix}")
    lines.extend(
        (
            "",
            'Call marivo.help("datasource.<target>") for a capability, public type, result, or datasource error.',
        )
    )
    return _bounded("\n".join(lines), root=True)


def _render_authoring(descriptor: AuthoringCapability) -> str:
    state_rows = [
        candidate
        for candidate in (REGISTRY.by_canonical_id(value) for value in REGISTRY.canonical_ids())
        if candidate.produced_state is not None
        and candidate.produced_state.id.startswith(
            ("datasource.", "source.", "scope.", "evidence.")
        )
    ]
    route_groups = (
        (
            "declare",
            tuple(
                candidate.canonical_id
                for candidate in REGISTRY.group("declare_manage")
                if candidate.produced_state is not None
                and candidate.produced_state.id == "datasource.declared"
            ),
        ),
        ("register and test", ("register", "test")),
        ("physical source", ("table", "parquet", "csv", "json")),
        ("metadata", ("inspect",)),
        ("explicit scope", ("partition", "time_range", "unpruned")),
        ("bounded acquisition", ("SourceInspection.sample",)),
        (
            "query-free projections",
            (
                "DiscoverySnapshot.entity",
                "DiscoverySnapshot.dimensions",
                "DiscoverySnapshot.values",
                "DiscoverySnapshot.time_dimensions",
                "DiscoverySnapshot.measures",
                "DiscoverySnapshot.relationships",
            ),
        ),
    )
    lines = [
        "authoring",
        f"  {descriptor.summary}",
        "",
        "  Minimal focused-help routing:",
    ]
    for label, canonical_ids in route_groups:
        targets = tuple(
            REGISTRY.by_canonical_id(canonical_id).canonical_id for canonical_id in canonical_ids
        )
        lines.append(
            f"    {label} -> "
            + ", ".join(f'marivo.help("datasource.{target}")' for target in targets)
        )
    lines.extend(("", "  Registered datasource states:"))
    for candidate in state_rows:
        assert candidate.produced_state is not None
        lines.append(f"    {candidate.produced_state.id} <- {candidate.canonical_id}")
    lines.extend(
        (
            "",
            "  Datasource guidance ends at evidence.projected.",
            '  Continue semantic authoring with marivo.help("semantic.authoring").',
        )
    )
    return _bounded("\n".join(lines))


def _render_descriptor(descriptor: AuthoringCapability) -> str:
    if descriptor.canonical_id == "authoring":
        return _render_authoring(descriptor)

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
        lines.append("  Example:")
        lines.extend(
            f"    {line}" if line else "" for line in descriptor.minimal_example.splitlines()
        )
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


def _contract_for_name(type_name: str) -> DatasourceTypeContract:
    for contract in TYPE_CONTRACTS.values():
        if contract.name == type_name:
            return contract
    raise RuntimeError(f"unknown datasource type contract: {type_name}")


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
    if "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    if "contract" in contract.public_methods:
        lines.append("  Continuation: call .contract() for mechanically valid next actions.")
    evidence_result_types = {
        "EntityEvidenceResult",
        "DimensionEvidenceResult",
        "DimensionValuesResult",
        "TimeEvidenceResult",
        "MeasureEvidenceResult",
        "RelationshipEvidenceResult",
    }
    if original is not None and type_name not in evidence_result_types:
        try:
            stored = vars(original)
        except TypeError:
            stored = {}
        public_values = [
            f"{name}={stored[name]!r}" for name in contract.public_properties if name in stored
        ]
        if public_values:
            lines.append("  Runtime fields: " + ", ".join(public_values))
    return _bounded("\n".join(lines))


def _help_invocation(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        return "marivo.help()"
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _render_error_contract(error_name: str) -> str:
    lines = [
        error_name,
        "  Datasource error contract.",
        "  Concrete repair guidance is available only when an instance carries repair.help_target.",
    ]
    return _bounded("\n".join(lines))


def _render_error_briefing(error_name: str, original: object) -> str:
    lines = [error_name, "  Datasource error repair."]
    for name in ("message", "expected", "received", "location"):
        value = getattr(original, name, None)
        if value is not None:
            lines.append(f"  {name.title()}: {value}")

    repair = getattr(original, "repair", None)
    help_target = getattr(repair, "help_target", None)
    if repair is None or not isinstance(help_target, LiveHelpTarget):
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
    resolved: ResolvedLiveTarget[AuthoringCapability],
    *,
    original_target: object | None = None,
) -> str:
    """Render a resolved datasource target without invoking runtime operations."""
    if resolved.kind == "descriptor" and resolved.descriptor is not None:
        return _with_python_imports(_render_descriptor(resolved.descriptor))
    if resolved.kind == "type_contract" and resolved.type_name is not None:
        return _with_python_imports(_render_type(resolved.type_name, original_target))
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(_render_error_contract(resolved.error_name))
    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        if resolved.original is None:
            raise RuntimeError("error_briefing requires original target")
        return _with_python_imports(_render_error_briefing(resolved.error_name, resolved.original))
    raise RuntimeError(f"unsupported datasource help resolution: {resolved.kind}")
