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
    ("runtime_probes", "Runtime probes"),
    ("readiness", "Readiness"),
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
    """Make a focused semantic help page executable from a cold start."""
    imports = [_MARIVO_IMPORT, _SEMANTIC_IMPORT]
    if "md." in text:
        imports.insert(0, _DATASOURCE_IMPORT)
    if "mv." in text:
        imports.insert(0, _ANALYSIS_IMPORT)
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
            lines.append(f"    {descriptor.canonical_id:<34} [output: {output}; effects: {badges}]")
    lines.extend(
        (
            "",
            "Identity handoff: pass a current CatalogEntry directly to preview, "
            "source health, readiness, or qualifying analysis APIs; use entry.ref or "
            "ms.ref.<kind>(path) for persisted, configured, or already-known identity.",
            "",
            "Consumed types: " + ", ".join(contract.name for contract in TYPE_CONTRACTS.values()),
            "Errors: " + ", ".join(ERROR_TYPES),
            "",
            'Call marivo.help("semantic.<target>") for a capability, public type, result, or semantic error.',
        )
    )
    return _bounded("\n".join(lines), root=True)


def _render_authoring(descriptor: AuthoringCapability) -> str:
    route_groups = (
        ("domain", ("domain",)),
        ("entity", ("entity",)),
        (
            "direct fields",
            ("dimension_column", "time_dimension_column", "measure_column"),
        ),
        ("aggregate metrics", ("where", "count", "aggregate")),
        ("load and scoped readiness", ("load", "readiness")),
        ("targeted runtime probes", ("preview", "source_health")),
    )
    lines = [
        "authoring",
        f"  {descriptor.summary}",
        "",
        "  Source layout:",
        "    models/datasources/<datasource>.py",
        "    models/semantic/<domain>/_domain.py",
        "    models/semantic/<domain>/<module>.py",
        "",
        "  Coherent-slice checkpoint:",
        "    catalog = ms.load()",
        "    entry = catalog.require(ms.ref.<kind>('<canonical identity>'))",
        "    report = catalog.readiness(refs=[entry])",
        "",
        "  Minimal focused-help routing:",
        '    datasource -> marivo.help("datasource.authoring")',
    ]
    for label, canonical_ids in route_groups:
        targets = tuple(
            REGISTRY.by_canonical_id(canonical_id).canonical_id for canonical_id in canonical_ids
        )
        lines.append(
            f"    {label} -> "
            + ", ".join(f'marivo.help("semantic.{target}")' for target in targets)
        )
    lines.extend(
        (
            "",
            "  Author one dependency-coherent slice before loading; ms.load() owns project-level static validation.",
            "  Preview only when it answers a concrete runtime risk.",
            "  Before first typed analysis use, stop only for unresolved business meaning not already settled by a current authority.",
            '  Continue datasource authoring with marivo.help("datasource.authoring").',
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
    if descriptor.see_also:
        lines.append(
            "  See also: " + ", ".join(_target_text(target) for target in descriptor.see_also)
        )
    return _bounded("\n".join(lines))


def _render_descriptor(descriptor: AuthoringCapability) -> str:
    if descriptor.canonical_id == "authoring":
        return _render_authoring(descriptor)
    if descriptor.canonical_id == "source_check":
        type_lines = _render_type("SourceCheckNamespace", None).splitlines()
        return _bounded("\n".join((descriptor.canonical_id, *type_lines[1:])))
    if descriptor.kind == "boundary":
        return _render_boundary(descriptor)

    lines = [descriptor.canonical_id, f"  {descriptor.summary}", ""]
    if descriptor.public_entrypoint is not None:
        lines.append(f"  Entrypoint: {descriptor.public_entrypoint}")
    if descriptor.callable_path is not None:
        callable_obj = import_callable(descriptor.callable_path)
        assert callable(callable_obj)
        signature = str(inspect.signature(callable_obj)).replace(
            "_SemanticInput",
            "SemanticInput",
        )
        lines.append(f"  Signature: {signature}")
    if descriptor.input_requirements:
        lines.append("  Input families:")
        for requirement in descriptor.input_requirements:
            detail = f" ({', '.join(requirement.exact_keys)})" if requirement.exact_keys else ""
            optional = " optional" if requirement.min_count == 0 else ""
            lines.append(f"    {requirement.role}: {requirement.family}{detail}{optional}")
    lines.append(f"  Output family: {descriptor.output_family or 'None'}")
    if descriptor.preconditions:
        lines.append(f"  Preconditions: {', '.join(descriptor.preconditions)}")
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
    source_contract = REGISTRY.source_contract(descriptor.canonical_id)
    if source_contract is not None:
        lines.extend(
            (
                f"  Loader placement: {source_contract.placement_kind}",
                f"  Source path: {source_contract.path_template}",
            )
        )
        if source_contract.prerequisite_targets:
            lines.append("  Prerequisite help:")
            lines.extend(
                f"    {_help_invocation(target)}" for target in source_contract.prerequisite_targets
            )
    if descriptor.minimal_example is not None:
        lines.append("  Example:")
        if source_contract is not None:
            lines.append(
                "    # Declaration fragment; execute only when ms.load() evaluates the source file."
            )
        lines.extend(
            f"    {line}" if line else "" for line in descriptor.minimal_example.splitlines()
        )
    constraints = _constraints(descriptor)
    if constraints:
        lines.append("  Constraints:")
        lines.extend(f"    {constraint}" for constraint in constraints)
    if source_contract is not None:
        identity = source_contract.canonical_identity_template
        lines.extend(
            (
                "  Postcondition after saving:",
                "    catalog = ms.load()",
                (f"    entry = catalog.{source_contract.catalog_collection}.get({identity!r})"),
                "    entry.show()",
                "    catalog.readiness(refs=[entry]).show()",
            )
        )
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
                "  Persisted/config identity: "
                "entry = catalog.metrics.get('sales.revenue'); metric_ref = entry.ref.",
                "  Membership: catalog.require(ref) resolves the exact ref to the current "
                "catalog; marivo.help(ref) reports identity only.",
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
    if type_name == "SourceCheckNamespace":
        lines.extend(
            (
                "  Closed constructors:",
                "    ms.source_check.not_null(field_ref)",
                "    ms.source_check.allowed_values(field_ref, values=(...))",
                "    ms.source_check.unique(fields=(...))",
                "    ms.source_check.freshness(time_dimension_ref, max_age=timedelta(...))",
                "    ms.source_check.relationship_matches(relationship_ref, side='from'|'both')",
                "    ms.source_check.relationship_cardinality(relationship_ref, expected='one_to_one'|'many_to_one'|'one_to_many'|'many_to_many')",
                "  Data boundary: pass these values only to catalog.source_health(..., "
                "checks=[...], scope=<explicit bounded scope>); no expectation is inferred.",
            )
        )
    if type_name == "CatalogEntry":
        lines.append(
            "  Runtime handoff: pass the current entry directly to catalog.preview, "
            "catalog.readiness, or qualifying analysis APIs; use "
            "entry.ref only when a stable configured or persisted identity is needed."
        )
        lines.append(
            "  Agent briefing: marivo.help(entry) combines current details, semantic "
            "continuation, and the kind-level analysis handoff."
        )
    if type_name == "CatalogCollection":
        lines.extend(
            (
                "  Lookup: pass a local name, full semantic path, displayed same-kind "
                "typed key, or exact same-kind Ref.",
                "  Copyable key example: catalog.metrics.get('metric:sales.revenue').",
                "  Handoff: inspect the selected entry with marivo.help(entry), then "
                "pass the entry or entry.ref to its consuming capability.",
            )
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
    return _bounded("\n".join(lines))


def _help_invocation(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        return "marivo.help()"
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _render_error_contract(error_name: str) -> str:
    lines = [
        error_name,
        "  Semantic error contract.",
        "  Concrete repair guidance is available only when an instance carries repair.help_target.",
    ]
    return _bounded("\n".join(lines))


def _render_error_briefing(error_name: str, original: object) -> str:
    lines = [error_name, "  Semantic error repair."]
    for name in ("message", "expected", "received", "location", "location_label"):
        value = getattr(original, name, None)
        if value is not None:
            label = "Location" if name == "location_label" else name.title()
            lines.append(f"  {label}: {value}")

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


def _render_reference(reference_id: str, original: object) -> str:
    """Render an object-near semantic identity without loading or querying."""
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    inspection_calls: tuple[str, ...]
    if isinstance(original, CatalogEntry):
        ref = original.ref
        object_name = type(original).__name__
        inspection_calls = (
            "entry.ref",
            "entry.show()",
            "entry.details().show()",
            "catalog.readiness(refs=[entry]).show()",
        )
    elif type(original) is Ref:
        ref = original
        object_name = "Ref"
        inspection_calls = (
            "ref.kind",
            "ref.path",
            "entry = catalog.require(ref)",
            "entry.show()",
            "entry.details().show()",
            "catalog.readiness(refs=[entry]).show()",
        )
    else:
        raise RuntimeError(f"expected exact Ref or CatalogEntry, got {type(original).__name__}")
    if ref.path != reference_id:
        raise RuntimeError("reference briefing identity mismatch")

    lines = [
        f"{ref.kind.value}: {ref.path}",
        f"  Object: {object_name}",
        f"  Kind: {ref.kind.value}",
        f"  Path: {ref.path}",
        "  Object-near inspection:",
        *(f"    {call}" for call in inspection_calls),
    ]
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
    if resolved.kind == "reference_briefing" and resolved.reference_id is not None:
        if resolved.original is None:
            raise RuntimeError("reference_briefing requires original target")
        return _with_python_imports(_render_reference(resolved.reference_id, resolved.original))
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(_render_error_contract(resolved.error_name))
    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        if resolved.original is None:
            raise RuntimeError("error_briefing requires original target")
        return _with_python_imports(_render_error_briefing(resolved.error_name, resolved.original))
    raise RuntimeError(f"unsupported semantic help resolution: {resolved.kind}")
