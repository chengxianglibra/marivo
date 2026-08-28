"""Bounded semantic help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo.introspection.live.model import SURFACE_LIMITS, LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import enforce_budget, render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.semantic._capabilities.model import (
    SemanticBuilderTopic,
    SemanticCheckTopic,
    SemanticNavigationTopic,
    SemanticObjectContract,
    SemanticObjectIndexEntry,
)
from marivo.semantic._capabilities.registry import REGISTRY, TYPE_CONTRACTS
from marivo.semantic.constraints import iter_constraints

if TYPE_CHECKING:
    from marivo.semantic._capabilities.model import (
        SemanticHelpDescriptor,
        SemanticHelpRenderClass,
        SemanticTypeContract,
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


_HELP_CALL_RE = re.compile(
    r'marivo\.help\("(analysis|datasource|semantic|ontology)\.'
    r'([A-Za-z_][A-Za-z0-9_.]*)"\)'
)


def _rendered_help_targets(text: str) -> tuple[LiveHelpTarget, ...]:
    """Return unique canonical routes actually advertised by one page."""

    return tuple(
        dict.fromkeys(
            LiveHelpTarget(surface=surface, canonical_id=canonical_id)
            for surface, canonical_id in _HELP_CALL_RE.findall(text)
        )
    )


def enforce_semantic_help_budget(
    text: str,
    *,
    render_class: SemanticHelpRenderClass,
    examples_or_snippets: int,
) -> str:
    """Enforce every dimension of one semantic-owned render budget."""

    budget = REGISTRY.render_budget(render_class)
    routes = _rendered_help_targets(text)
    if len(routes) > budget.max_outgoing_routes:
        raise RuntimeError(
            "semantic help exceeds its registered outgoing-route budget: "
            f"{len(routes)} > {budget.max_outgoing_routes}"
        )
    if examples_or_snippets > budget.max_examples_or_snippets:
        raise RuntimeError(
            "semantic help exceeds its registered example/snippet budget: "
            f"{examples_or_snippets} > {budget.max_examples_or_snippets}"
        )
    return enforce_budget(
        text,
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
    )


def _target_text(target: LiveHelpTarget) -> str:
    return target.canonical_id or target.surface


def _with_python_imports(
    text: str,
    *,
    render_class: SemanticHelpRenderClass,
    examples_or_snippets: int,
) -> str:
    """Make a focused semantic help page executable from a cold start."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    imports = [_MARIVO_IMPORT, _SEMANTIC_IMPORT]
    if "md." in text:
        imports.insert(0, _DATASOURCE_IMPORT)
    if "mv." in text:
        imports.insert(0, _ANALYSIS_IMPORT)
    lines = text.splitlines()
    rendered = _bounded(
        "\n".join(
            (
                lines[0],
                f"  Marivo: {EnvironmentFingerprint.current().marivo_version}",
                "  Python imports:",
                *(f"    {statement}" for statement in imports),
                "",
                *lines[1:],
            )
        )
    )
    return enforce_semantic_help_budget(
        rendered,
        render_class=render_class,
        examples_or_snippets=examples_or_snippets,
    )


def _constraints(descriptor: AuthoringCapability) -> tuple[str, ...]:
    catalog = {constraint.id: constraint for constraint in iter_constraints()}
    return tuple(
        f"{constraint_id}: {catalog[constraint_id].title}"
        for constraint_id in descriptor.constraints
        if constraint_id in catalog
    )


def render_root_help() -> str:
    """Render the compact semantic root from registry-owned sections."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    lines = [
        "marivo.semantic",
        render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
        "",
        "Python imports:",
        f"  {_SEMANTIC_IMPORT}",
    ]
    routes: list[LiveHelpTarget] = []
    for section in REGISTRY.root_sections:
        lines.extend(("", f"{section.label}:"))
        for target in section.members:
            target_id = target.canonical_id
            if target_id is None:
                raise RuntimeError("semantic root route requires a canonical id")
            routes.append(target)
            lines.append(f'  {target_id:<18} marivo.help("{target.surface}.{target_id}")')
    lines.extend(
        (
            "",
            'Call marivo.help("semantic.<target>") for one exact contract.',
        )
    )
    rendered = "\n".join(lines)
    if tuple(dict.fromkeys(routes)) != tuple(routes):
        raise RuntimeError("semantic root contains duplicate routes")
    if _rendered_help_targets(rendered) != tuple(routes):
        raise RuntimeError("semantic root rendered routes drift from its registry sections")
    return enforce_semantic_help_budget(
        rendered,
        render_class="root",
        examples_or_snippets=0,
    )


def _route_list(targets: tuple[LiveHelpTarget, ...]) -> str:
    return ", ".join(_help_invocation(target) for target in targets)


def _render_navigation_topic(
    descriptor: SemanticNavigationTopic,
    *,
    render_class: SemanticHelpRenderClass,
) -> str:
    """Render one registry-owned semantic decision or navigation page."""

    lines = [descriptor.canonical_id, f"  {descriptor.summary}"]
    if render_class == "decision_hub":
        lines.extend(
            (
                "",
                "  Source layout:",
                "    models/datasources/<datasource>.py",
                "    models/semantic/<domain>/_domain.py",
                "    models/semantic/<domain>/<module>.py",
                "",
                "  Author one dependency-coherent slice, then load once:",
                "    catalog = ms.load()",
                "    entry = catalog.require(ms.ref.<kind>('<canonical identity>'))",
                "    catalog.readiness(refs=[entry]).show()",
            )
        )

    object_entries = tuple(
        member for member in descriptor.members if isinstance(member, SemanticObjectIndexEntry)
    )
    if object_entries:
        lines.extend(("", "  Relationship overview:"))
        object_targets = {entry.target for entry in object_entries}
        for entry in object_entries:
            contract = entry.contract
            relationships = tuple(
                f"{relationship.relation} {relationship.target.display}"
                for relationship in contract.relationships
                if relationship.target in object_targets
            )
            if relationships:
                lines.append(f"    {contract.semantic_kind.value}: " + "; ".join(relationships))

    lines.extend(("", f"  {descriptor.member_heading}:"))
    for member in descriptor.members:
        summary = f": {member.summary} ->" if member.summary is not None else ":"
        lines.append(f"    {member.label}{summary} {_help_invocation(member.target)}")
    if render_class == "decision_hub":
        lines.extend(
            (
                "",
                "  Help never settles unresolved business meaning from physical evidence.",
                "  Preview only when it answers a concrete runtime risk.",
            )
        )
    return _bounded("\n".join(lines))


def _render_builder_topic(descriptor: SemanticBuilderTopic) -> str:
    lines = [
        descriptor.canonical_id,
        f"  {descriptor.label}: {descriptor.summary}",
        "",
        "  Exact builders:",
        *(f"    {_help_invocation(target)}" for target in descriptor.members),
    ]
    return _bounded("\n".join(lines))


def _render_check_topic(descriptor: SemanticCheckTopic) -> str:
    lines = [descriptor.canonical_id, f"  {descriptor.summary}", "", "  Proof routing:"]
    for route in descriptor.routes:
        lines.extend(
            (
                f"    Question: {route.question}",
                f"      Route: {_route_list(route.targets)}",
                f"      Proves: {route.proves}",
                f"      Does not prove: {route.does_not_prove}",
            )
        )
    lines.extend(
        (
            "",
            "  load success != readiness != preview success != source health",
            "  source health != operation-shaped analysis execution",
        )
    )
    return _bounded("\n".join(lines))


def _render_object_contract(descriptor: SemanticObjectContract) -> str:
    lines = [
        descriptor.canonical_id,
        f"  Meaning: {descriptor.summary}",
        "",
        "  Identity:",
        f"    output: Ref[{descriptor.semantic_kind.value}]",
        f"    forward/cross-file: {_help_invocation(descriptor.ref_target)}",
        f"    placement: {descriptor.placement_kind}",
        f"    catalog: catalog.{descriptor.catalog_collection}",
        "",
        "  Decide before authoring:",
    ]
    for decision in descriptor.decisions:
        lines.append(f"    - {decision.question}")
        guidance = f"basis={decision.basis}; determine from: {decision.determine_from}"
        if decision.does_not_establish is not None:
            guidance += f" Does not establish: {decision.does_not_establish}"
        if decision.encoding_status == "supported":
            guidance += f" Encode with: {_route_list(decision.next_targets)}"
        else:
            guidance += f" Unsupported: {decision.unsupported_reason}"
        lines.append(f"      {guidance}")

    lines.extend(("", "  Construction modes:"))
    lines.extend(
        f"    {mode.role}: {mode.intent} -> {_help_invocation(mode.target)}"
        for mode in descriptor.construction_modes
    )
    if descriptor.relationships:
        lines.extend(("", "  Relationships:"))
        lines.extend(
            f"    {relationship.relation}: {_help_invocation(relationship.target)}; "
            f"{relationship.explanation}"
            for relationship in descriptor.relationships
        )
    if descriptor.supporting_targets:
        lines.extend(
            (
                "",
                f"  Supporting builders: {_route_list(descriptor.supporting_targets)}",
            )
        )
    if descriptor.check_targets:
        lines.extend(("", f"  Applicable checks: {_route_list(descriptor.check_targets)}"))
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
    if descriptor.canonical_id == "ref":
        return _render_factory_descriptor(descriptor, "ref")
    if descriptor.canonical_id == "source_check":
        return _render_factory_descriptor(descriptor, "SourceCheckNamespace")
    if descriptor.kind == "boundary":
        return _render_boundary(descriptor)

    lines = [descriptor.canonical_id, f"  {descriptor.summary}", ""]
    if descriptor.public_entrypoint is not None:
        lines.append(f"  Entrypoint: {descriptor.public_entrypoint}")
    if descriptor.callable_path is not None:
        callable_obj = import_callable(descriptor.callable_path)
        assert callable(callable_obj)
        installed = inspect.signature(callable_obj)
        parameters = tuple(installed.parameters.values())
        if descriptor.kind == "method" and parameters and parameters[0].name in {"self", "cls"}:
            installed = installed.replace(parameters=parameters[1:])
        signature = str(installed).replace(
            "_SemanticInput",
            "SemanticInput",
        )
        lines.append(f"  Signature: {signature}")
    if descriptor.input_requirements:
        lines.append("  Input families:")
        for requirement in descriptor.input_requirements:
            details: list[str] = []
            if requirement.parameter_names:
                details.append("parameters: " + ", ".join(requirement.parameter_names))
            if requirement.exact_keys:
                details.append("keys: " + ", ".join(requirement.exact_keys))
            detail = f" ({'; '.join(details)})" if details else ""
            optional = " optional" if requirement.min_count == 0 else ""
            lines.append(f"    {requirement.role}: {requirement.family}{optional}{detail}")
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
        for other in REGISTRY.descriptors
        if descriptor.output_family is not None
        and any(
            requirement.family == descriptor.output_family
            for requirement in other.input_requirements
        )
    ]
    if consumers:
        lines.append("  Consumers: " + ", ".join(consumers))
    if descriptor.see_also:
        lines.append("  See also: " + _route_list(descriptor.see_also))
    return _bounded("\n".join(lines))


def _render_factory_descriptor(descriptor: AuthoringCapability, type_name: str) -> str:
    """Render one factory namespace from its registry-owned ordered membership."""

    type_lines = tuple(
        line
        for line in _render_type(type_name, None).splitlines()[1:]
        if not line.startswith("  Public consumption:")
    )
    lines = [descriptor.canonical_id, *type_lines]
    if descriptor.see_also:
        lines.append("  Exact factory help:")
        lines.extend(f"    {_help_invocation(target)}" for target in descriptor.see_also)
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
        lines.append(
            "  Data boundary: pass these values only to catalog.source_health(..., "
            "checks=[...], scope=<explicit bounded scope>); no expectation is inferred."
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
    resolved: ResolvedLiveTarget[SemanticHelpDescriptor],
    *,
    original_target: object | None = None,
) -> str:
    """Render a resolved semantic target without invoking runtime operations."""
    if resolved.kind == "descriptor" and resolved.descriptor is not None:
        descriptor = resolved.descriptor
        if isinstance(descriptor, AuthoringCapability):
            rendered = _render_descriptor(descriptor)
            examples = int(descriptor.minimal_example is not None)
        elif isinstance(descriptor, SemanticNavigationTopic):
            rendered = _render_navigation_topic(
                descriptor,
                render_class=REGISTRY.render_class(descriptor.canonical_id),
            )
            examples = 0
        elif isinstance(descriptor, SemanticBuilderTopic):
            rendered = _render_builder_topic(descriptor)
            examples = 0
        elif isinstance(descriptor, SemanticCheckTopic):
            rendered = _render_check_topic(descriptor)
            examples = 0
        elif isinstance(descriptor, SemanticObjectContract):
            rendered = _render_object_contract(descriptor)
            examples = 0
        else:
            raise RuntimeError(f"unsupported semantic Help descriptor: {type(descriptor).__name__}")
        return _with_python_imports(
            rendered,
            render_class=REGISTRY.render_class(descriptor.canonical_id),
            examples_or_snippets=examples,
        )
    if resolved.kind == "type_contract" and resolved.type_name is not None:
        return _with_python_imports(
            _render_type(resolved.type_name, original_target),
            render_class="exact_contract",
            examples_or_snippets=0,
        )
    if resolved.kind == "reference_briefing" and resolved.reference_id is not None:
        if resolved.original is None:
            raise RuntimeError("reference_briefing requires original target")
        return _with_python_imports(
            _render_reference(resolved.reference_id, resolved.original),
            render_class="current_briefing",
            examples_or_snippets=0,
        )
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(
            _render_error_contract(resolved.error_name),
            render_class="exact_contract",
            examples_or_snippets=0,
        )
    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        if resolved.original is None:
            raise RuntimeError("error_briefing requires original target")
        repair = getattr(resolved.original, "repair", None)
        return _with_python_imports(
            _render_error_briefing(resolved.error_name, resolved.original),
            render_class="current_briefing",
            examples_or_snippets=int(getattr(repair, "snippet", None) is not None),
        )
    raise RuntimeError(f"unsupported semantic help resolution: {resolved.kind}")
