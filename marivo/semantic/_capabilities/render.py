"""Bounded semantic help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo._authoring.render import effect_lines, input_lines
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import bounded_help as _bounded
from marivo.introspection.live.render import enforce_budget, render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.introspection.live.targets import canonical_target
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
    return target.display


def _python_help_text(text: str) -> str:
    """Add the imports needed by one semantic help page."""
    imports = [_MARIVO_IMPORT, _SEMANTIC_IMPORT]
    if "md." in text:
        imports.insert(0, _DATASOURCE_IMPORT)
    if "mv." in text:
        imports.insert(0, _ANALYSIS_IMPORT)
    lines = text.splitlines()
    return "\n".join(
        (
            lines[0],
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
        for target, summary in zip(section.members, section.route_summaries, strict=True):
            target_id = target.canonical_id
            if target_id is None:
                raise RuntimeError("semantic root route requires a canonical id")
            routes.append(target)
            lines.append(f'  {target_id}: {summary} -> marivo.help("{target.surface}.{target_id}")')
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

    lines.extend(("", f"  {descriptor.member_heading}:"))
    for member in descriptor.members:
        summary = f": {member.summary} ->" if member.summary is not None else ":"
        lines.append(f"    {member.label}{summary} {_help_invocation(member.target)}")
        if (
            isinstance(member, SemanticObjectIndexEntry)
            and len(member.contract.construction_modes) == 1
        ):
            mode = member.contract.construction_modes[0]
            lines.append(f"      Construct: {_help_invocation(mode.target)}")
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
        "  Exact operations:",
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
            "  load success != readiness != preview success != online validation",
            "  online validation != operation-shaped analysis execution",
        )
    )
    return _bounded("\n".join(lines))


def _render_object_contract(descriptor: SemanticObjectContract) -> str:
    lines = [
        descriptor.canonical_id,
        f"  Meaning: {descriptor.summary}",
        f"  lookup symbol: ms.ref.{descriptor.semantic_kind.value}(path); catalog.search(kind={descriptor.semantic_kind.value!r}); {descriptor.placement_kind}",
        f"  Forward/cross-file refs: {_help_invocation(descriptor.ref_target)}",
        "",
        "  Construction modes:",
    ]
    shown = {descriptor.ref_target}

    def new_routes(targets: tuple[LiveHelpTarget, ...]) -> str:
        fresh = tuple(target for target in targets if target not in shown)
        shown.update(fresh)
        return _route_list(fresh)

    if descriptor.semantic_kind.value == "metric":
        lines[-1:] = [
            "  Existing closed values: compose Metric arithmetic for the current question without publishing a definition.",
            "  Use where/per/window only under their exact receiver constraints; reduce internal samples before observation.",
            "",
            "  Reusable definition construction modes:",
        ]
    for mode in descriptor.construction_modes:
        construction = REGISTRY.by_canonical_id(mode.target.canonical_id or "")
        assert isinstance(construction, AuthoringCapability)
        lines.append(
            f"    {mode.role}: {mode.intent} -> {new_routes((mode.target,))}; "
            f"returns {construction.output_family}"
        )
    lines.extend(
        ("", "  Decide before authoring (use established facts; read only unresolved routes):")
    )
    authority = tuple(dict.fromkeys(decision.determine_from for decision in descriptor.decisions))
    limits = tuple(
        dict.fromkeys(
            decision.does_not_establish
            for decision in descriptor.decisions
            if decision.does_not_establish is not None
        )
    )
    lines.extend(f"    Authority: {text}" for text in authority)
    lines.extend(f"    Evidence limit: {text}" for text in limits)
    for decision in descriptor.decisions:
        routes = new_routes(decision.next_targets)
        detail = (
            (f" -> {routes}" if routes else "")
            if decision.encoding_status == "supported"
            else f" Unsupported: {decision.unsupported_reason}"
        )
        lines.append(f"    - {decision.question}{detail}")
    for relationship in descriptor.relationships:
        routes = new_routes((relationship.target,))
        lines.append(
            f"  {relationship.relation}: {relationship.explanation}"
            + (f" {routes}" if routes else "")
        )
    supporting = new_routes(descriptor.supporting_targets)
    if supporting:
        lines.append(f"  Optional supporting builders: {supporting}")
    if descriptor.check_guidance:
        lines.append(f"  Check scope: {descriptor.check_guidance}")
    checks = new_routes(descriptor.check_targets)
    if checks:
        lines.append(f"  Checks: {checks}")
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
        if descriptor.callable_path is None:
            lines.append(f"  Syntax: {descriptor.public_entrypoint}(...)")
        else:
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
            lines.extend(input_lines(requirement))
    lines.append(f"  Output family: {descriptor.output_family or 'None'}")
    if descriptor.preconditions:
        lines.append(f"  Preconditions: {', '.join(descriptor.preconditions)}")
    effects = descriptor.effects
    assert effects is not None
    lines.extend(effect_lines(effects))
    if descriptor.telemetry:
        lines.append(
            "  Usage telemetry may write local .marivo/telemetry when enabled; effects above describe business operations."
        )
    lines.extend(f"  {line}" for line in descriptor.guidance)
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
                (
                    f"    entry = catalog.get({(next(contract.semantic_kind.value for contract in REGISTRY.object_contracts if descriptor.output_family in contract.construction_families) + chr(58) + identity)!r})"
                ),
                "    entry.show()",
            )
        )
    if descriptor.output_family in {contract.name for contract in TYPE_CONTRACTS.values()}:
        lines.append(
            f'  Result Help: marivo.help("semantic.{canonical_target(descriptor.output_family)}")'
        )
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
    lines.extend("  " + line for line in contract.guidance)
    if contract.consumers:
        lines.append(
            "  Consumers: " + ", ".join(_target_text(target) for target in contract.consumers)
        )
    lines.extend(f"  {note}" for note in contract.notes)
    if contract.example is not None:
        lines.append("  Example:")
        lines.extend(f"    {line}" for line in contract.example.splitlines())
    if type_name == "ref":
        lines.append(
            "  Construction namespace: use ms.ref.<kind>(path); every factory returns "
            "one immutable native Deferred lookup symbol. The loader binds authoring recipes; catalog.get(kind:path) returns typed objects for direct consumption after kind narrowing."
        )
    if type_name == "SourceCheckNamespace":
        lines.append(
            "  Data boundary: pass these values only to catalog.validate(..., "
            "checks=[...], scope=<explicit bounded scope>); no expectation is inferred."
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
        return f'marivo.help("{target.surface}")'
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
    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(
            _render_error_contract(resolved.error_name),
            render_class="exact_contract",
            examples_or_snippets=0,
        )
    raise RuntimeError(f"unsupported semantic help resolution: {resolved.kind}")
