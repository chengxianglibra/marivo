"""Bounded semantic help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo._authoring.render import effect_lines
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import bounded_help as _bounded
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
    from marivo.semantic.errors import SemanticError

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"
_ANALYSIS_IMPORT = "import marivo.analysis as mv"
_MARIVO_IMPORT = "import marivo"


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


def _runtime_check_lines(kind: str) -> tuple[str, ...]:
    """Return only the owning object contract's applicable runtime checks."""
    from marivo.refs import SemanticKind

    try:
        contract = REGISTRY.object_contract(SemanticKind(kind))
    except (KeyError, ValueError):
        return ()

    labels = {
        "preview": "Scoped preview",
        "source_health": "Current source health",
    }
    return tuple(
        f'{labels[target_id]}: marivo.help("semantic.{target_id}")'
        for target in contract.check_targets
        if (target_id := target.canonical_id) in labels
    )


def render_reference_briefing(
    target: object,
    *,
    analysis_handoff: tuple[str, ...] = (),
) -> str:
    """Render shared no-I/O Ref or CatalogEntry facts under one budget."""
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    if type(target) is Ref:
        ref = target
        lines = [
            f"{ref.kind.value}: {ref.path}",
            "  Object: Ref",
            "  Authority: typed identity only; project membership and readiness are unknown.",
            "",
            "  Resolve membership in the caller's loaded catalog:",
            "    entry = catalog.require(ref)",
            "",
            '  Capability help: marivo.help("semantic.Ref")',
        ]
    elif isinstance(target, CatalogEntry):
        ref = target.ref
        lines = [
            f"{ref.kind.value}: {ref.path}",
            f"  Object: {type(target).__name__}",
            "  Authority: current compiled catalog entry.",
            "",
            "  Object-near inspection:",
            "    entry.ref",
            "    entry.show()",
            "    entry.details().show()",
        ]
        runtime_checks = _runtime_check_lines(ref.kind.value)
        if runtime_checks:
            lines.extend(
                ("", "  Applicable runtime checks:", *(f"    {line}" for line in runtime_checks))
            )
        if analysis_handoff:
            lines.extend(
                (
                    "",
                    "  Analysis handoff (kind-level; readiness and companion inputs still apply):",
                    *(f"    {line}" for line in analysis_handoff),
                    "  After an analysis operator returns an artifact:",
                    "    result.contract().show()",
                )
            )
        lines.extend(
            (
                "",
                '  Analysis preflight: marivo.help("semantic.readiness").',
                "  Readiness and operation-specific admission are not inferred here.",
                "  No datasource connectivity or inspection evidence was queried.",
            )
        )
    else:
        raise RuntimeError(f"unsupported semantic object: {type(target).__name__}")

    return enforce_semantic_help_budget(
        "\n".join(lines),
        render_class="current_briefing",
        examples_or_snippets=0,
    )


def _target_text(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        return target.surface
    if target.surface != "semantic":
        return f"{target.surface}.{target.canonical_id}"
    return target.canonical_id


def _python_help_text(text: str) -> str:
    """Add the imports needed by one semantic help page."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    imports = [_MARIVO_IMPORT, _SEMANTIC_IMPORT]
    if "md." in text:
        imports.insert(0, _DATASOURCE_IMPORT)
    if "mv." in text:
        imports.insert(0, _ANALYSIS_IMPORT)
    lines = text.splitlines()
    return "\n".join(
        (
            lines[0],
            f"  Marivo: {EnvironmentFingerprint.current().marivo_version}",
            "  Python imports:",
            *(f"    {statement}" for statement in imports),
            "",
            *lines[1:],
        )
    )


def _with_python_imports(
    text: str,
    *,
    render_class: SemanticHelpRenderClass,
    examples_or_snippets: int,
) -> str:
    """Make a focused semantic help page executable from a cold start."""
    return enforce_semantic_help_budget(
        _bounded(_python_help_text(text)),
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
    lines.extend(effect_lines(effects))
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
            "  Usage briefing: marivo.help(entry) reports identity, usage navigation, "
            "and kind-level analysis handoff; entry.show() and entry.details() own definitions."
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
        "  Instance help preserves concrete failure facts; repair is optional.",
    ]
    if error_name == "SemanticLoadFailed":
        lines.append("  Public fields: errors (ordered SemanticError instances).")
    else:
        lines.extend(
            (
                "  Public fields: kind, message, expected, received, semantic_refs, location, "
                "location_label, hint, constraint_id, repair.",
                "  Repair: kind, action, snippet, candidates, help_target when present.",
            )
        )
    return _bounded("\n".join(lines))


def _error_fact_lines(error: SemanticError) -> list[str]:
    """Project concrete error facts without reloading or resolving evidence."""
    lines = [f"  Kind: {error.kind}", f"  Message: {error.message}"]
    if error.expected is not None:
        lines.append(f"  Expected: {error.expected}")
    if error.received is not None:
        lines.append(f"  Received: {error.received}")
    if error.semantic_refs:
        lines.append("  Refs: " + ", ".join(error.semantic_refs))
    if error.location is not None:
        lines.append(f"  Location: {error.location.file}:{error.location.line}")
    if error.location_label is not None:
        lines.append(f"  Location: {error.location_label}")
    if error.hint is not None:
        lines.append(f"  Hint: {error.hint}")
    if error.repair is not None:
        lines.extend((f"  Repair kind: {error.repair.kind}", f"  Action: {error.repair.action}"))
        if error.repair.candidates:
            lines.append("  Candidates: " + ", ".join(error.repair.candidates))
        lines.append(f"  Next help: {_help_invocation(error.repair.help_target)}")
    return lines


def _render_error_briefing(error_name: str, original: object) -> str:
    """Bound dynamic error facts while retaining an explicit full read path."""
    from marivo.semantic.errors import SemanticError, SemanticLoadFailed

    if isinstance(original, SemanticLoadFailed):
        errors = original.errors
        recovery = "  Full errors: for error in exc.errors: print(error)"
    elif isinstance(original, SemanticError):
        errors = (original,)
        recovery = (
            "  Full facts: exc.message, exc.expected, exc.received, exc.semantic_refs, exc.repair"
        )
    else:
        raise RuntimeError("error_briefing requires a registered semantic error instance")

    budget = REGISTRY.render_budget("current_briefing")
    lines = [error_name, f"  Semantic failure facts ({len(errors)} errors)."]
    omitted_fields = 0
    shown = 0
    snippets = 0

    def fits(candidate: list[str]) -> bool:
        text = _python_help_text("\n".join(candidate))
        return (
            len(text.splitlines()) <= budget.max_lines
            and len(text) <= budget.max_codepoints
            and len(_rendered_help_targets(text)) <= budget.max_outgoing_routes
        )

    for index, error in enumerate(errors):
        block = [f"  Error {index + 1}: {type(error).__name__}"]
        facts = _error_fact_lines(error)
        value_limit = budget.max_codepoints // (2 * max(8, len(facts)))
        for fact in facts:
            # Keep all fact labels visible even when one dynamic value is very large.
            fact = fact.replace("\r\n", "\n").replace("\n", "\\n")
            if len(fact) > value_limit:
                fact = fact[:value_limit] + " [value omitted; read full facts]"
                omitted_fields += 1
            block.append(fact)
        remaining = len(errors) - index - 1
        footer = [
            f"  Omitted errors: {remaining}",
            "  Some facts/snippets omitted; read full facts.",
            recovery,
        ]
        if not fits([*lines, *block, *footer]):
            break
        lines.extend(block)
        shown += 1
        if error.repair is not None and error.repair.snippet is not None:
            snippet = ["  Snippet:", *(f"    {line}" for line in error.repair.snippet.splitlines())]
            if snippets < budget.max_examples_or_snippets and fits([*lines, *snippet, *footer]):
                lines.extend(snippet)
                snippets += 1
            else:
                omitted_fields += 1

    if len(errors) > shown:
        lines.append(f"  Omitted errors: {len(errors) - shown}")
    if omitted_fields:
        lines.append("  Some facts/snippets omitted; read full facts.")
    lines.append(recovery)
    return _with_python_imports(
        "\n".join(lines), render_class="current_briefing", examples_or_snippets=snippets
    )


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
        return render_reference_briefing(resolved.original)
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(
            _render_error_contract(resolved.error_name),
            render_class="exact_contract",
            examples_or_snippets=0,
        )
    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        if resolved.original is None:
            raise RuntimeError("error_briefing requires original target")
        return _render_error_briefing(resolved.error_name, resolved.original)
    raise RuntimeError(f"unsupported semantic help resolution: {resolved.kind}")
