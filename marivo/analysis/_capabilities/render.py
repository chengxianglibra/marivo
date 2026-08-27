"""Render typed help text from the capability registry.

Consumes the immutable registry, resolver, surface limits, and constraints
to produce bounded root and focused help text strings.

All names are private to ``marivo.analysis``.
"""

from __future__ import annotations

import ast
import inspect
from typing import TYPE_CHECKING

from marivo.analysis._capabilities.model import (
    ARTIFACT_FAMILIES,
    AnalysisArtifactFamilyContract,
    AnalysisHelpDescriptor,
    AnalysisHelpRenderClass,
    AnalysisMethodFamily,
    AnalysisNavigationTopic,
    ArtifactConsumerEdge,
    ArtifactProducerEdge,
    BoundaryCapability,
    CapabilityDescriptor,
    ConstructorCapability,
    OperatorCapability,
    ReadCapability,
    RecoveryCapability,
    SameAsInputFamily,
)
from marivo.analysis._capabilities.registry import (
    PUBLIC_FRAME_METHODS,
    PUBLIC_FRAME_PROPERTIES,
    PUBLIC_TYPE_VARIANTS,
    REGISTRY,
)
from marivo.analysis._capabilities.surface import TYPE_REGISTRY
from marivo.analysis.constraints import CONSTRAINTS, get_constraint
from marivo.introspection.constraints import Constraint
from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    EnvironmentFingerprint,
    LiveHelpTarget,
)
from marivo.introspection.live.reflect import import_registered_callable
from marivo.introspection.live.render import render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.refs import SemanticKind
from marivo.semantic._capabilities.catalog_members import (
    CATALOG_MEMBER_CONTRACTS,
    CatalogMemberContract,
)

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

# The analysis surface is consumed as ``mv`` (mirroring ``md``/``ms`` for the
# datasource/semantic surfaces). Help text uses ``mv.`` throughout, so every
# page states the import so examples run from a cold start (see issue #22).
_ANALYSIS_IMPORT = "import marivo.analysis as mv"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"
_MARIVO_IMPORT = "import marivo"

# Focused help pages that teach exact ``ms.ref.<kind>(path)`` construction.
_REF_ID_FORMAT_TARGETS: frozenset[str] = frozenset({"observe", "catalog.require"})

# Kind -> semantic path structure for the sole sealed ``ms.Ref`` type.
_REF_ID_FORMATS: tuple[tuple[SemanticKind, str], ...] = (
    (SemanticKind.METRIC, 'ms.ref.metric("<domain>.<metric_name>")'),
    (SemanticKind.DIMENSION, 'ms.ref.dimension("<domain>.<entity>.<dimension_name>")'),
    (SemanticKind.TIME_DIMENSION, 'ms.ref.time_dimension("<domain>.<entity>.<dimension_name>")'),
    (SemanticKind.MEASURE, 'ms.ref.measure("<domain>.<entity>.<measure_name>")'),
    (SemanticKind.ENTITY, 'ms.ref.entity("<domain>.<entity_name>")'),
    (SemanticKind.DOMAIN, 'ms.ref.domain("<domain_name>")'),
    (SemanticKind.STATE_MODEL, 'ms.ref.state_model("<domain>.<state_model_name>")'),
    (SemanticKind.TEMPORAL_SET, 'ms.ref.temporal_set("<domain>.<temporal_set_name>")'),
    (SemanticKind.WORK_SCHEDULE, 'ms.ref.work_schedule("<domain>.<work_schedule_name>")'),
)


def _ref_id_format_lines() -> list[str]:
    width = max(len(kind.value) for kind, _ in _REF_ID_FORMATS)
    rows = [f"    {kind.value:<{width}}  {template}" for kind, template in _REF_ID_FORMATS]
    return [
        "",
        "  Ref ID format:",
        "    catalog.require(ref) accepts one exact Ref. Common factories:",
        *rows,
    ]


# ---------------------------------------------------------------------------
# Budget and fingerprint helpers
# ---------------------------------------------------------------------------


def environment_fingerprint() -> EnvironmentFingerprint:
    """Return the environment fingerprint for root help."""
    return EnvironmentFingerprint.current()


def enforce_budget(text: str, *, max_lines: int, max_codepoints: int) -> str:
    """Normalize line endings and enforce the surface budget.

    Raises ``RuntimeError`` if the text exceeds the registered budget.
    """
    normalized = text.replace("\r\n", "\n")
    if len(normalized.splitlines()) > max_lines or len(normalized) > max_codepoints:
        raise RuntimeError("analysis help exceeds its registered surface budget")
    return normalized


def _enforce_analysis_render_budget(
    text: str,
    *,
    render_class: AnalysisHelpRenderClass,
    outgoing_routes: tuple[LiveHelpTarget, ...],
    examples_or_snippets: int,
) -> str:
    """Enforce every dimension of one analysis-owned static render budget."""

    budget = REGISTRY.render_budget(render_class)
    unique_routes = {(target.surface, target.canonical_id) for target in outgoing_routes}
    if len(unique_routes) > budget.max_outgoing_routes:
        raise RuntimeError(
            "analysis help exceeds its registered outgoing-route budget: "
            f"{len(unique_routes)} > {budget.max_outgoing_routes}"
        )
    if examples_or_snippets > budget.max_examples_or_snippets:
        raise RuntimeError(
            "analysis help exceeds its registered example/snippet budget: "
            f"{examples_or_snippets} > {budget.max_examples_or_snippets}"
        )
    return enforce_budget(
        text,
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
    )


def _with_python_imports(
    text: str,
    *,
    render_class: AnalysisHelpRenderClass | None = None,
    outgoing_routes: tuple[LiveHelpTarget, ...] = (),
    examples_or_snippets: int = 0,
) -> str:
    """Prefix a focused page with the surface imports its text references."""
    lines = text.splitlines()
    imports = [_MARIVO_IMPORT, _ANALYSIS_IMPORT]
    if "ms." in text:
        imports.insert(0, _SEMANTIC_IMPORT)
    rendered = enforce_budget(
        "\n".join(
            (
                lines[0],
                "  Python imports:",
                *(f"    {statement}" for statement in imports),
                "",
                *lines[1:],
            )
        ),
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )
    if render_class is None:
        return rendered
    return _enforce_analysis_render_budget(
        rendered,
        render_class=render_class,
        outgoing_routes=outgoing_routes,
        examples_or_snippets=examples_or_snippets,
    )


# ---------------------------------------------------------------------------
# Docstring section extraction
# ---------------------------------------------------------------------------


def _extract_docstring_section(doc: str, section_name: str) -> str | None:
    """Extract a named section (e.g. 'Example:', 'Raises:') from a docstring.

    Returns the section body (stripped) or None if not found.
    """
    if not doc:
        return None
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_name or stripped.startswith(section_name):
            # Collect indented lines after the section header.
            body: list[str] = []
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if next_line.strip() == "":
                    if body:
                        # Empty line within section — include it.
                        body.append("")
                        continue
                    else:
                        continue
                # Check if this line starts a new section (non-indented).
                if not next_line[0].isspace() and next_line.strip().endswith(":"):
                    break
                body.append(next_line.strip())
            # Strip trailing empty lines.
            while body and body[-1] == "":
                body.pop()
            return "\n".join(body) if body else None
    return None


def _extract_example(doc: str) -> str | None:
    """Extract the Example: section from a docstring."""
    return _extract_docstring_section(doc, "Example:")


def _extract_raises(doc: str) -> str | None:
    """Extract the Raises: section from a docstring."""
    return _extract_docstring_section(doc, "Raises:")


def _extract_guidance(doc: str) -> str | None:
    """Extract agent-facing business selection guidance from a docstring."""
    return _extract_docstring_section(doc, "Guidance:")


# ---------------------------------------------------------------------------
# Constraint lookup
# ---------------------------------------------------------------------------


def _constraints_for_descriptor(desc: CapabilityDescriptor) -> tuple[Constraint, ...]:
    """Return constraints whose ids appear in the descriptor's constraint_ids."""
    result: list[Constraint] = []
    for cid in desc.constraint_ids:
        constraint = get_constraint(cid)
        if constraint is not None:
            result.append(constraint)
    return tuple(result)


# ---------------------------------------------------------------------------
# Root help renderer
# ---------------------------------------------------------------------------

_GROUP_LABELS: dict[str, str] = {
    "semantic_inputs": "Semantic inputs",
    "policies_builders": "Policies and builders",
    "artifact_production": "Artifact production",
    "typed_analysis": "Typed analysis",
    "family_operations": "Family operations",
    "artifact_inspection": "Artifact inspection",
    "recovery": "Recovery",
    "boundaries": "Boundaries",
}


def render_root_help() -> str:
    """Render the root help page with fingerprint and first-observation guidance.

    ``root_visibility="direct"`` descriptors appear as individual entries.
    ``root_visibility="grouped"`` descriptors collapse to their grouping
    topic (e.g. ``discover``, ``transform``) so the root stays bounded.
    """
    lines: list[str] = []

    # Fingerprint (exact paths shown: root help uses reveal=True).
    fp = environment_fingerprint()
    lines.extend(render_fingerprint(fp, reveal=True).split("\n"))
    lines.extend(
        (
            "",
            "Python imports:",
            f"  {_MARIVO_IMPORT}",
            f"  {_ANALYSIS_IMPORT}",
            "",
            "First observation:",
            '  session = mv.session.get_or_create("<stable-session-name>", question="<business question>")',
            '  metric = session.catalog.metrics.get("<full semantic path or typed key>")',
            "  marivo.help(metric)",
            "  readiness = session.catalog.readiness(refs=[metric])",
            '  if readiness.status == "blocked":',
            "      readiness.show()",
            "      raise SystemExit",
            "  frame = session.observe(metric)",
            "  frame.show()",
            "",
            "Focused contract:",
            '  marivo.help("analysis.observe")',
            "",
        )
    )

    # Capability groups
    lines.append("Capabilities:")
    for group, descriptors in REGISTRY.discovery_groups():
        label = _GROUP_LABELS.get(group, group.replace("_", " ").title())
        lines.append(f"  {label} [{group}]:")
        for desc in descriptors:
            entrypoint = desc.public_entrypoint or (f'marivo.help("analysis.{desc.canonical_id}")')
            lines.append(f"    {entrypoint:<44} {REGISTRY.discovery_summary(desc)}")
        lines.append("")

    # Drill-down instruction
    lines.append('Call marivo.help("analysis.<target>") for detail on any capability.')

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.root_help_max_lines,
        max_codepoints=SURFACE_LIMITS.root_help_max_codepoints,
    )


# ---------------------------------------------------------------------------
# Focused descriptor renderer
# ---------------------------------------------------------------------------


def _format_input_families(desc: OperatorCapability) -> list[str]:
    """Format accepted input families for display."""
    display = {
        "MetricSemantic": "MetricSemantic (MetricEntry | Ref[metric])",
        "DimensionSemantic": ("DimensionSemantic (DimensionEntry | Ref[dimension])"),
        "TimeDimensionSemantic": (
            "TimeDimensionSemantic (TimeDimensionEntry | Ref[time_dimension])"
        ),
    }
    rows: list[str] = []
    for param, families in desc.accepted_inputs.items():
        family_list = ", ".join(display.get(family, family) for family in sorted(families))
        rows.append(f"  {param}: {family_list}")
    return rows


def _format_output_family(desc: OperatorCapability) -> str:
    """Format output family for display."""
    return desc.output_contract.render()


def _format_artifact_shape_admission(desc: OperatorCapability) -> list[str]:
    """Render exact family-to-shape admission from the capability registry."""
    rows: list[str] = []
    for parameter, admission in desc.artifact_admission.items():
        for family, shapes in sorted(admission.semantic_shapes.items()):
            rows.append(f"    {parameter}.{family}: {' | '.join(sorted(shapes))}")
    return rows


def _resolve_callable(desc: AnalysisHelpDescriptor) -> object | None:
    """Resolve the callable_path to a live callable object."""
    if desc.callable_path is None:
        return None
    try:
        return import_registered_callable(desc.callable_path)
    except (ImportError, AttributeError):
        return None


def _property_return_type(value: object) -> str | None:
    """Return the declared result type for a registered property."""
    if not isinstance(value, property) or value.fget is None:
        return None
    annotation = inspect.signature(value.fget).return_annotation
    if annotation is inspect.Signature.empty:
        return None
    if isinstance(annotation, str):
        return annotation
    return inspect.formatannotation(annotation)


def _producer_targets_for_input(
    desc: OperatorCapability,
    parameter: str,
) -> tuple[LiveHelpTarget, ...]:
    """Return producers compatible with one declared input."""

    accepted = desc.accepted_inputs.get(parameter, frozenset())
    targets: list[LiveHelpTarget] = []
    for family in sorted(accepted):
        for target in REGISTRY.producer_targets(family):
            if target.surface == "analysis":
                canonical_id = target.canonical_id
                if canonical_id is None:
                    raise RuntimeError("analysis producer target requires a canonical id")
                try:
                    producer = REGISTRY.by_help_target(canonical_id)
                except KeyError:
                    producer = None
                if isinstance(producer, OperatorCapability):
                    output_family = producer.output_contract.family
                    admission = desc.artifact_admission.get(parameter)
                    if admission is not None and not isinstance(
                        output_family,
                        SameAsInputFamily,
                    ):
                        shapes = admission.semantic_shapes.get(output_family)
                        if (
                            shapes
                            and producer.output_contract.semantic_shapes
                            and shapes.isdisjoint(producer.output_contract.semantic_shapes)
                        ):
                            continue
                        matching = admission.matching_kinds.get(output_family)
                        if (
                            matching
                            and producer.output_contract.matching_kinds
                            and matching.isdisjoint(producer.output_contract.matching_kinds)
                        ):
                            continue
            targets.append(target)
    return tuple(dict.fromkeys(targets))


def _help_call(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        raise RuntimeError("help prerequisite target requires a canonical id")
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _input_prerequisite_line(
    desc: OperatorCapability,
    parameter: str,
) -> str | None:
    """Render one copyable acquisition instruction for an operator input."""

    accepted = desc.accepted_inputs.get(parameter, frozenset())
    targets = _producer_targets_for_input(desc, parameter)
    target_text = ", ".join(_help_call(target) for target in targets)
    semantic_handoffs = tuple(
        handoff
        for family in sorted(accepted)
        for handoff in REGISTRY.semantic_handoffs_for_input_family(family)
    )
    if semantic_handoffs:
        collections = ", ".join(
            f'session.catalog.{handoff.collection_property}.get("<full semantic path>")'
            for handoff in semantic_handoffs
        )
        return f"{parameter}: select via {collections}; help: {target_text}"
    if "EventPattern" in accepted:
        return (
            f"{parameter}: build from session.catalog.events.get("
            '"<full semantic path>").ref; help: '
            f"{target_text}"
        )
    if target_text:
        return f"{parameter}: acquire via {target_text}"
    return None


def _parameter_help_line(desc: OperatorCapability, parameter: str) -> str:
    """Render one explicit construction/selection route for a parameter."""

    contract = desc.parameter_help[parameter]
    target_text = ", ".join(_help_call(target) for target in contract.help_targets)
    return f"{parameter}: {contract.acquisition}; help: {target_text}"


def _dotted_call_path(node: ast.expr) -> str | None:
    """Return one simple dotted callable path from an AST expression."""

    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _matches_public_entrypoint(actual: str | None, expected: str) -> bool:
    """Return whether a call uses the registered member path.

    The receiver name in an example may be more specific than the generic
    public entrypoint (for example, ``delta.transform.topk`` versus
    ``frame.transform.topk``). The registered member path after the receiver
    must still match exactly.
    """

    if actual is None:
        return False
    actual_parts = actual.split(".")
    expected_parts = expected.split(".")
    return len(actual_parts) == len(expected_parts) and actual_parts[1:] == expected_parts[1:]


def _assigned_result_name(code: str, *, public_entrypoint: str) -> str | None:
    """Return the assignment produced by the registered public entrypoint."""

    cleaned_lines: list[str] = []
    for line in code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith((">>> ", "... ")):
            cleaned_lines.append(stripped[4:])
        else:
            cleaned_lines.append(line)
    try:
        tree = ast.parse("\n".join(cleaned_lines))
    except SyntaxError:
        return None
    expected_call = public_entrypoint.partition("(")[0]
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.value, ast.Call)
            and _matches_public_entrypoint(
                _dotted_call_path(node.value.func),
                expected_call,
            )
        ):
            target = node.targets[0]
            if isinstance(target, ast.Name):
                return target.id
    return None


def _related_targets(desc: CapabilityDescriptor) -> list[str]:
    """Return bounded explicit parameter links and discovery siblings."""
    related: list[str] = []
    seen: set[str] = set()

    def _add(target: str) -> None:
        if target != desc.help_target and target not in seen:
            seen.add(target)
            related.append(target)

    for target in REGISTRY.cross_links(desc.help_target):
        if target.surface == "analysis" and target.canonical_id is not None:
            _add(target.canonical_id)
    owner = REGISTRY.discovery_owner(desc.help_target)
    if owner is not None and owner.canonical_id is not None:
        for sibling in REGISTRY.discovery_members(owner.canonical_id):
            if sibling.surface == "analysis" and sibling.canonical_id is not None:
                _add(sibling.canonical_id)

    return related[:5]


def _grouping_members(desc: AnalysisHelpDescriptor) -> tuple[AnalysisHelpDescriptor, ...]:
    """Return explicit registry-owned members in their declared order."""

    members: list[AnalysisHelpDescriptor] = []
    for target in REGISTRY.discovery_members(desc.canonical_id):
        if target.surface != "analysis" or target.canonical_id is None:
            continue
        members.append(REGISTRY.by_help_target(target.canonical_id))
    return tuple(members)


def _catalog_member_for_descriptor(desc: CapabilityDescriptor) -> CatalogMemberContract | None:
    """Return the closed catalog contract represented by one analysis member."""

    if not desc.id.startswith("catalog."):
        return None
    property_name = desc.id.removeprefix("catalog.")
    return next(
        (member for member in CATALOG_MEMBER_CONTRACTS if member.property_name == property_name),
        None,
    )


def _catalog_collection_guidance(
    desc: CapabilityDescriptor,
    member: CatalogMemberContract,
) -> list[str]:
    """Render the live-help loop from a catalog collection to one entry."""

    collection = desc.public_entrypoint
    return [
        "",
        "  Semantic object loop:",
        f"    1. Unknown identity: {collection}.show()",
        "    2. Exact selection accepts a full path, displayed same-kind typed key, or Ref:",
        f'       entry = {collection}.get("<full semantic path or typed key>")',
        f"       entry = {collection}.get(ref)",
        "    3. Inspect: entry.show(); entry.details().show(); marivo.help(entry)",
        "    4. Handoff: pass entry to the focused analysis API; use entry.ref for stable identity.",
        '  Type contracts: marivo.help("semantic.CatalogCollection"), '
        'marivo.help("semantic.CatalogEntry"), marivo.help("semantic.Ref").',
        f"  Object kind: {member.kind.value}; entry type: {member.entry_type_name}.",
    ]


def _catalog_group_guidance() -> list[str]:
    """Render bounded discovery guidance for the catalog grouping topic."""

    properties = tuple(member.property_name for member in CATALOG_MEMBER_CONTRACTS)
    midpoint = (len(properties) + 1) // 2
    return [
        "  Analysis entry:",
        "    catalog = session.catalog",
        "    catalog.show()",
        "  Object families:",
        "    " + ", ".join(properties[:midpoint]),
        "    " + ", ".join(properties[midpoint:]),
        "  Discovery rule: select only the collection relevant to the question.",
        '  Focused collection contract: marivo.help("analysis.catalog.<family>").',
        "  Object-level continuation: inspect the selected entry with marivo.help(entry).",
    ]


def _catalog_exact_ref_guidance() -> list[str]:
    """Render exact-ref handoff guidance for ``catalog.require`` help."""

    return [
        "  Exact identity handoff:",
        "    catalog = session.catalog",
        "    entry = catalog.require(ref)",
        "    entry.show(); entry.details().show(); marivo.help(entry)",
        "    Use catalog.require only for an exact Ref from configuration, logs, or persistence.",
        '  Entry contract: marivo.help("semantic.CatalogEntry").',
    ]


def _has_semantic_object_handoff(desc: OperatorCapability) -> bool:
    """Return whether an operator consumes a governed semantic object family."""

    return any(
        REGISTRY.semantic_handoffs_for_input_family(family)
        for families in desc.accepted_inputs.values()
        for family in families
    )


def _discover_strategy_lines(desc: OperatorCapability) -> list[str]:
    """Render the scored-objective strategy contract for one discover operator.

    Strategy facts live in :mod:`marivo.analysis.intents.discover` as the single
    source of truth; this renderer only reads them so focused help and the
    latest site docs cannot drift into independent copies.
    """
    if not desc.help_target.startswith("discover."):
        return []
    objective = desc.help_target.split(".", 1)[1]
    from marivo.analysis.intents.discover import (
        _DEFAULT_STRATEGY,
        _OBJECTIVE_SEMANTIC_KINDS,
        _OBJECTIVE_STRATEGY_APPLICABILITY,
        _OBJECTIVE_THRESHOLD,
        _STRATEGY_ALTERNATIVES,
    )

    if objective not in _DEFAULT_STRATEGY:
        return []
    default = _DEFAULT_STRATEGY[objective]
    alternatives = sorted(_STRATEGY_ALTERNATIVES.get(objective, set()))
    kinds = sorted(_OBJECTIVE_SEMANTIC_KINDS[objective])
    threshold_info = _OBJECTIVE_THRESHOLD[objective]

    strategy = f"    default: {default}"
    if alternatives:
        strategy += f"; alternatives: {', '.join(alternatives)}"
    lines = ["", "  Strategy:", strategy]
    lines.append(f"    applies to semantic kinds: {', '.join(kinds)}")
    if threshold_info is not None:
        lines.append(
            f"    threshold: {threshold_info['description']}, default {threshold_info['default']}"
        )
    else:
        lines.append("    threshold: not accepted")
    lines.append(f"    when to use: {_OBJECTIVE_STRATEGY_APPLICABILITY[objective]}")
    return lines


def _render_navigation_help(desc: AnalysisNavigationTopic) -> str:
    """Render one bounded registry-owned decision or navigation page."""

    lines = [
        desc.canonical_id,
        f'  Entrypoint: marivo.help("analysis.{desc.canonical_id}")',
        f"  {REGISTRY.focused_summary(desc)}",
        "",
        "  Members:",
    ]
    for member in _grouping_members(desc):
        entrypoint = member.public_entrypoint or (f'marivo.help("analysis.{member.canonical_id}")')
        member_return_type: str | None = None
        if isinstance(member, CapabilityDescriptor):
            member_obj = _resolve_callable(member)
            member_return_type = _property_return_type(member_obj)
        if member_return_type is not None:
            lines.append(
                f"    {entrypoint}  "
                f"(property -> {member_return_type}; inspect with .show())  "
                f"[{member.help_target}]"
            )
        else:
            lines.append(f"    {entrypoint}  [{member.help_target}]")
    cross_links = REGISTRY.cross_links(desc.canonical_id)
    if cross_links:
        lines.extend(("", "  Cross-links:"))
        lines.extend(f"    {target.display}" for target in cross_links)
    budget = REGISTRY.render_budget(desc.render_class)
    return enforce_budget(
        "\n".join(lines),
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
    )


def _render_method_family_help(desc: AnalysisMethodFamily) -> str:
    """Render deterministic computation facts without signatures or examples."""

    lines = [
        desc.canonical_id,
        f'  Entrypoint: marivo.help("analysis.{desc.canonical_id}")',
        f"  {desc.summary}",
        "",
        "  Epistemic kinds: " + ", ".join(desc.epistemic_kinds),
        "",
        "  Members:",
    ]
    lines.extend(
        f'    marivo.help("analysis.{target.canonical_id}")'
        for target in desc.members
        if target.canonical_id is not None
    )
    if desc.input_routes:
        lines.extend(("", "  Input contracts:"))
        lines.extend(f"    {target.display}" for target in desc.input_routes)
    if desc.output_routes:
        lines.extend(("", "  Output contracts:"))
        lines.extend(f"    {target.display}" for target in desc.output_routes)
    budget = REGISTRY.render_budget("navigation")
    return enforce_budget(
        "\n".join(lines),
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
    )


def _render_descriptor_help(desc: AnalysisHelpDescriptor) -> str:
    """Render focused help for a single capability descriptor."""
    if isinstance(desc, AnalysisNavigationTopic):
        return _render_navigation_help(desc)
    if isinstance(desc, AnalysisMethodFamily):
        return _render_method_family_help(desc)
    if isinstance(desc, AnalysisArtifactFamilyContract):
        return _render_artifact_type_help(desc)

    lines: list[str] = []

    callable_obj = _resolve_callable(desc)
    is_property = isinstance(callable_obj, property)
    is_value_contract = (
        isinstance(desc, ConstructorCapability)
        and desc.callable_path is None
        and bool(desc.output_type)
    )

    # Identity / entrypoint
    lines.append(f"{desc.help_target}")
    label = "Property" if is_property else "Values" if is_value_contract else "Entrypoint"
    lines.append(f"  {label}: {desc.public_entrypoint}")
    lines.append(f"  {desc.summary}")
    lines.append("")

    if desc.id == "alignment":
        lines.extend(
            (
                "  Admission matrix:",
                "    window_bucket          compare / correlate / hypothesis_test",
                "    day_of_week            MetricFrame.compare day-grain time-series or panel",
                "    period_progress        MetricFrame.compare cumulative or one target period",
                "    period_correspondence  MetricFrame.compare complete exact semantic grain",
                "    occurrence_progress   MetricFrame.compare day-grain exact occurrence scopes",
                "    working_day_progress  MetricFrame.compare day-grain time-series/panel under one certified work schedule",
                "    EventFrame.compare     alignment=None (mechanical step/axis pairing)",
                "    segmented frames      window_bucket only",
            )
        )
        lines.append("")

    if is_property:
        return_type = _property_return_type(callable_obj)
        if return_type is not None:
            lines.append(f"  Returns: {return_type}")
        lines.append(f"  Inspect: {desc.public_entrypoint}.show()")
        catalog_member = _catalog_member_for_descriptor(desc)
        if catalog_member is not None:
            lines.extend(_catalog_collection_guidance(desc, catalog_member))

    if desc.id == "catalog":
        lines.extend(_catalog_group_guidance())
    elif desc.id == "catalog.require":
        lines.extend(_catalog_exact_ref_guidance())

    # Live signature (for invokable capabilities)
    if callable_obj is not None and callable(callable_obj):
        try:
            sig = inspect.signature(callable_obj)
            params = list(sig.parameters.values())
            # Remove 'self' for methods.
            filtered = [p for p in params if p.name != "self"]
            param_strs: list[str] = []
            for p in filtered:
                if p.kind == inspect.Parameter.KEYWORD_ONLY and not any(
                    s.startswith("*") for s in param_strs
                ):
                    param_strs.append("*")
                prefix = ""
                if p.kind == inspect.Parameter.VAR_POSITIONAL:
                    prefix = "*"
                elif p.kind == inspect.Parameter.VAR_KEYWORD:
                    prefix = "**"
                part = f"{prefix}{p.name}"
                if p.annotation is not inspect.Parameter.empty:
                    ann = p.annotation
                    if isinstance(ann, type):
                        part += f": {ann.__name__}"
                    elif isinstance(ann, str):
                        part += f": {ann.replace('_SemanticInput', 'SemanticInput')}"
                if p.default is not inspect.Parameter.empty:
                    if p.default is None:
                        part += " = None"
                    elif isinstance(p.default, str):
                        part += f" = {p.default!r}"
                    else:
                        part += f" = {p.default!r}"
                param_strs.append(part)
            func_name = desc.help_target.split(".")[-1]
            sig_str = f"{func_name}(" + ", ".join(param_strs) + ")"
            lines.append(f"  Signature: {sig_str}")
        except (ValueError, TypeError):
            pass

    # Accepted/output families (for operators)
    if isinstance(desc, OperatorCapability):
        lines.append("")
        lines.append("  Accepted inputs:")
        lines.extend(_format_input_families(desc))
        shape_admission = _format_artifact_shape_admission(desc) if desc.artifact_admission else []
        if shape_admission:
            lines.append("  Accepted artifact shapes:")
            lines.extend(shape_admission)
        artifact_input = any(
            family in ARTIFACT_FAMILIES
            for families in desc.accepted_inputs.values()
            for family in families
        )
        output = f"  Output family: {_format_output_family(desc)}"
        if artifact_input:
            output += f"; authority: {desc.authority_policy}"
        lines.append(output)
        lines.extend(_discover_strategy_lines(desc))

    if isinstance(desc, BoundaryCapability):
        lines.append("")
        lines.append("  Accepted inputs:")
        for param, families in desc.accepted_inputs.items():
            family_list = ", ".join(sorted(families))
            lines.append(f"    {param}: {family_list}")
        lines.append(f"  Output family: {desc.output_family}")
        if desc.preserves:
            lines.append(f"  Preserves: {', '.join(desc.preserves)}")
        if desc.does_not_preserve:
            lines.append(f"  Does not preserve: {', '.join(desc.does_not_preserve)}")

    if isinstance(desc, ConstructorCapability) and desc.output_type:
        lines.append(f"  Output type: {desc.output_type}")

    if isinstance(desc, ReadCapability):
        lines.append(f"  Result kind: {desc.result_kind}")
        lines.append(f"  Read bound: {desc.read_bound}")
        if desc.output_type:
            lines.append(f"  Output type: {desc.output_type}")

    if isinstance(desc, RecoveryCapability):
        if desc.restored_family:
            lines.append(f"  Restored family: {desc.restored_family}")
        lines.append(f"  Identity input: {desc.identity_input}")
        lines.append(f"  Query behavior: {desc.query_behavior}")

    if callable_obj is not None:
        guidance = _extract_guidance(inspect.getdoc(callable_obj) or "")
        if guidance:
            lines.append("")
            lines.append("  Guidance:")
            lines.extend(f"    {line}" if line else "" for line in guidance.splitlines())

    members = _grouping_members(desc)
    if members:
        if lines[-1] != "":
            lines.append("")
        lines.append("  Members:")
        for member in members:
            member_obj = _resolve_callable(member)
            member_return_type = _property_return_type(member_obj)
            if member_return_type is None:
                lines.append(f"    {member.public_entrypoint}  [{member.help_target}]")
            else:
                lines.append(
                    f"    {member.public_entrypoint}  "
                    f"(property -> {member_return_type}; inspect with .show())  "
                    f"[{member.help_target}]"
                )

    if isinstance(desc, OperatorCapability):
        prerequisite_lines: list[str] = []
        if desc.receiver.startswith("Session"):
            prerequisite_lines.append(
                'session = mv.session.get_or_create("<stable-session-name>", question="<business question>")'
            )
        for parameter in desc.accepted_inputs:
            prerequisite = _input_prerequisite_line(desc, parameter)
            if prerequisite is not None:
                prerequisite_lines.append(prerequisite)
        if prerequisite_lines:
            lines.append("")
            lines.append("  Prerequisites:")
            lines.extend(f"    {line}" for line in prerequisite_lines)
        if desc.parameter_help:
            lines.append("")
            lines.append("  Parameter construction:")
            lines.extend(
                f"    {_parameter_help_line(desc, parameter)}" for parameter in desc.parameter_help
            )
        if _has_semantic_object_handoff(desc):
            lines.extend(
                (
                    "",
                    "  Semantic object handoff:",
                    "    A current CatalogEntry or exact Ref can satisfy the semantic input.",
                    "    Inspect object-specific details and continuation with marivo.help(entry).",
                    '    Contract: marivo.help("semantic.CatalogEntry") and marivo.help("semantic.Ref").',
                )
            )

    result_names: list[str] = []

    # Example (from docstring)
    if callable_obj is not None:
        doc = inspect.getdoc(callable_obj) or ""
        example = _extract_example(doc)
        if example:
            assigned = _assigned_result_name(
                example,
                public_entrypoint=desc.public_entrypoint,
            )
            if assigned is not None:
                result_names.append(assigned)
            lines.append("")
            lines.append("  Example:")
            # Clean up REPL continuation markers (>>> and ...) to produce
            # a single runnable code block without ellipsis.
            cleaned_lines: list[str] = []
            for ex_line in example.splitlines():
                stripped = ex_line.lstrip()
                if stripped.startswith(">>> "):
                    cleaned_lines.append("    " + stripped[4:])
                elif stripped.startswith(">>>"):
                    cleaned_lines.append("    " + stripped[3:].lstrip())
                elif stripped.startswith("... "):
                    cleaned_lines.append("    " + stripped[4:])
                elif stripped.startswith("..."):
                    cleaned_lines.append("    " + stripped[3:].lstrip())
                else:
                    cleaned_lines.append(f"    {ex_line}")
            for cl in cleaned_lines:
                lines.append(cl)

    for additional_example in desc.additional_examples:
        assigned = _assigned_result_name(
            additional_example.code,
            public_entrypoint=desc.public_entrypoint,
        )
        if assigned is not None:
            result_names.append(assigned)
        lines.append("")
        lines.append(f"  {additional_example.label}:")
        if additional_example.requires:
            lines.append(
                "    Requires from prerequisites or the preceding example: "
                + ", ".join(additional_example.requires)
            )
        lines.extend(f"    {line}" for line in additional_example.code.splitlines())

    if isinstance(desc, OperatorCapability) and result_names:
        result_name = result_names[0]
        lines.append("")
        lines.append("  After success:")
        if desc.output_contract.nullable:
            lines.append(f"    if {result_name} is not None:")
            lines.append(f"        {result_name}.show()")
            lines.append(f"        {result_name}.contract().show()")
        else:
            lines.append(f"    {result_name}.show()")
            lines.append(f"    {result_name}.contract().show()")

    # Exact Ref path format for the focused semantic-input pages.
    if desc.help_target in _REF_ID_FORMAT_TARGETS:
        lines.extend(_ref_id_format_lines())

    # Constraints
    constraints = _constraints_for_descriptor(desc)
    if constraints:
        lines.append("")
        lines.append("  Constraints:")
        for constraint in constraints:
            lines.append(f"    {constraint.id}: {constraint.title}")

    # Producer/consumer edges
    if isinstance(desc, OperatorCapability):
        consumers = REGISTRY.compatible_consumers(desc.output_contract)
        if consumers:
            lines.append("")
            lines.append("  Consumed by:")
            for consumer_id in sorted(consumers)[:5]:
                lines.append(f"    {consumer_id}")
            if not desc.output_contract.semantic_shapes:
                lines.append("    conditional: inspect the concrete artifact with .contract()")

    # Optional related targets
    related = _related_targets(desc)
    if related:
        lines.append("")
        lines.append("  Related:")
        for target in related:
            lines.append(f"    {target}")

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


# ---------------------------------------------------------------------------
# Type contract renderer
# ---------------------------------------------------------------------------


def _producer_edge_text(edge: ArtifactProducerEdge) -> str:
    qualifiers: list[str] = []
    if edge.semantic_shapes:
        qualifiers.append("shapes=" + "|".join(sorted(edge.semantic_shapes)))
    if edge.matching_kinds:
        qualifiers.append("matching=" + "|".join(sorted(edge.matching_kinds)))
    if edge.same_as_parameter is not None:
        qualifiers.append(f"same-as={edge.same_as_parameter}")
    if edge.nullable:
        qualifiers.append("nullable")
    suffix = f" [{', '.join(qualifiers)}]" if qualifiers else ""
    return f"{edge.target.display}{suffix}"


def _consumer_edge_text(edge: ArtifactConsumerEdge) -> str:
    qualifiers = [f"parameter={edge.parameter}"]
    if edge.semantic_shapes:
        qualifiers.append("shapes=" + "|".join(sorted(edge.semantic_shapes)))
    if edge.matching_kinds:
        qualifiers.append("matching=" + "|".join(sorted(edge.matching_kinds)))
    if edge.coverage_statuses:
        qualifiers.append("coverage=" + "|".join(sorted(edge.coverage_statuses)))
    return f"{edge.target.display} [{', '.join(qualifiers)}]"


def _render_artifact_type_help(contract: AnalysisArtifactFamilyContract) -> str:
    """Render one complete static Artifact-family consumption contract."""

    type_name = contract.type_name
    props = tuple(
        dict.fromkeys(
            (
                *PUBLIC_FRAME_PROPERTIES.get(type_name, ()),
                *PUBLIC_FRAME_PROPERTIES.get("BaseFrame", ()),
            )
        )
    )
    lines = [
        type_name,
        f"  {contract.summary}",
        "",
        "  Epistemic kinds: " + ", ".join(contract.epistemic_kinds),
    ]
    if contract.semantic_shapes:
        lines.extend(
            (
                "",
                "  Closed shapes or variants: "
                + ", ".join(f"{type_name}[{shape}]" for shape in contract.semantic_shapes),
            )
        )
    if type_name == "SubjectSet":
        lines.extend(("", "  Row contract:", "    subject_identity: governed identity tuple"))
    if props:
        lines.extend(("", "  Properties:", "    " + ", ".join(props)))
    if contract.specialized_member_targets:
        lines.extend(
            (
                "",
                "  Methods:",
                "    "
                + ", ".join(target.display for target in contract.specialized_member_targets),
            )
        )
    reading_members = tuple(
        REGISTRY.by_help_target(target.canonical_id)
        for target in REGISTRY.discovery_members("artifacts.reading")
        if target.surface == "analysis" and target.canonical_id is not None
    )
    lines.extend(("", "  Common reads:"))
    lines.append(
        "    "
        + ", ".join(
            f"{member.public_entrypoint} -> {member.help_target}" for member in reading_members
        )
    )

    producer_edges = REGISTRY.artifact_producer_edges(contract.artifact_family)
    if producer_edges:
        lines.extend(
            (
                "",
                "  Produced by:",
                "    " + "; ".join(_producer_edge_text(edge) for edge in producer_edges),
            )
        )

    consumer_edges = REGISTRY.artifact_consumer_edges(contract.artifact_family)
    if consumer_edges:
        lines.extend(
            (
                "",
                "  Consumed by in principle:",
                "    " + "; ".join(_consumer_edge_text(edge) for edge in consumer_edges),
            )
        )

    if type_name == "QualityReport":
        lines.extend(
            (
                "",
                "  Quality verdict:",
                "    report.overall_status is the quality verdict; report.state is ArtifactState materialization metadata.",
                "    Blocking correctness issues stop use; warnings remain advisory and",
                "    must be disclosed when analysis continues.",
            )
        )

    artifact_links = REGISTRY.cross_links(contract.canonical_id)
    reading_links = (
        REGISTRY.cross_links("artifacts.reading")
        if any(target.canonical_id == "artifacts.reading" for target in artifact_links)
        else ()
    )
    evidence_targets_values: list[LiveHelpTarget] = []
    for target in reading_links:
        if target.canonical_id is None:
            continue
        descriptor = REGISTRY.by_help_target(target.canonical_id)
        if (
            isinstance(descriptor, ReadCapability)
            and descriptor.receiver_family == "EvidenceNamespace"
        ):
            evidence_targets_values.append(target)
    evidence_targets = tuple(evidence_targets_values)
    recovery_targets = tuple(
        target
        for target in artifact_links
        if target.canonical_id is not None
        and isinstance(REGISTRY.by_help_target(target.canonical_id), RecoveryCapability)
    )
    terminal_targets = tuple(
        target
        for target in artifact_links
        if target.canonical_id is not None
        and isinstance(REGISTRY.by_help_target(target.canonical_id), BoundaryCapability)
    )
    if evidence_targets:
        lines.extend(
            (
                "",
                "  Typed Evidence reads:",
                "    " + ", ".join(target.display for target in evidence_targets),
            )
        )
    if recovery_targets:
        lines.extend(
            ("", "  Recovery:", "    " + ", ".join(target.display for target in recovery_targets))
        )
    lines.extend(("", "  Terminal boundary:"))
    lines.extend(
        f"    {REGISTRY.by_help_target(target.canonical_id).public_entrypoint} -> {target.display}"
        for target in terminal_targets
        if target.canonical_id is not None
    )
    lines.extend(("", "  Help routes:"))
    lines.extend(
        f'    marivo.help("analysis.{target.canonical_id}")'
        for target in artifact_links
        if target.surface == "analysis" and target.canonical_id is not None
    )
    lines.extend(
        (
            "",
            "  Static consumers are possibilities, not current admission; inspect the concrete Artifact with .contract().",
        )
    )
    budget = REGISTRY.render_budget("public_type")
    return enforce_budget(
        "\n".join(lines),
        max_lines=budget.max_lines,
        max_codepoints=budget.max_codepoints,
    )


def _render_type_help(type_name: str) -> str:
    """Render focused help for a registered public type.

    Never render dataclass/Pydantic constructors, ``_df``,
    ``_NEXT_INTENTS``, ``_GATED_INTENTS``, private fields, or inherited
    Pydantic mechanics.
    """
    if type_name in ARTIFACT_FAMILIES:
        return _render_artifact_type_help(REGISTRY.artifact_contract(type_name))

    # Find the type object.
    type_obj: type | None = None
    for t, name in TYPE_REGISTRY.items():
        if name == type_name:
            type_obj = t
            break
    if type_obj is None:
        # Should not happen — resolver already validated.
        raise RuntimeError(f"unknown type: {type_name}")

    lines: list[str] = []
    lines.append(type_name)

    # Module docstring first line (not the constructor signature).
    # For dataclass/Pydantic models, getdoc returns the class docstring
    # which may include the constructor signature — we want only the
    # first prose line.
    doc = inspect.getdoc(type_obj) or ""
    if doc:
        doc_lines = doc.strip().splitlines()
        # Skip lines that look like constructor signatures.
        first_prose_line = ""
        for dl in doc_lines:
            stripped = dl.strip()
            if not stripped:
                continue
            # Skip lines that look like constructor signatures.
            if stripped.startswith(type_name + "(") or stripped.startswith("_"):
                continue
            # Type docstrings may point callers to help as their first line;
            # that instruction is redundant inside the focused help page.
            if stripped.startswith("Call marivo.help("):
                continue
            first_prose_line = stripped
            break
        if first_prose_line:
            lines.append(f"  {first_prose_line}")
    lines.append("")

    variants = PUBLIC_TYPE_VARIANTS.get(type_name)
    if variants:
        lines.append("  Closed variants:")
        for variant in variants:
            rendered_variant = (
                variant
                if type_name in {"ArtifactIssue", "CandidateSelection"}
                else f"{type_name}[{variant}]"
            )
            lines.append(f"    {rendered_variant}")
        lines.append("")

    if type_name == "SubjectSet":
        lines.append("  Row contract:")
        lines.append("    subject_identity: governed identity tuple")
        lines.append("")

    model_fields = getattr(type_obj, "model_fields", None)
    if isinstance(model_fields, dict) and model_fields:
        lines.append("  Fields:")
        for field_name in model_fields:
            lines.append(f"    {field_name}")
        lines.append("")
    elif type_name == "FrameSummaryEntry":
        from dataclasses import fields

        lines.append("  Fields:")
        for field in fields(type_obj):
            lines.append(f"    {field.name}")
        lines.append("")

    # Properties (from registry allowlist, including inherited BaseFrame
    # for frame subtypes only).
    from marivo.analysis.frames.base import BaseFrame

    props, methods = REGISTRY.public_object_members(type_name)
    props = tuple(dict.fromkeys((*props, *PUBLIC_FRAME_PROPERTIES.get(type_name, ()))))
    if isinstance(type_obj, type) and type_obj is not BaseFrame and issubclass(type_obj, BaseFrame):
        base_props = PUBLIC_FRAME_PROPERTIES.get("BaseFrame", ())
        props = tuple(dict.fromkeys((*props, *base_props)))
    if props:
        lines.append("  Properties:")
        for prop in props:
            lines.append(f"    {prop}")
        lines.append("")

    # Methods (from registry allowlist, including inherited BaseFrame
    # for frame subtypes only).
    methods = tuple(dict.fromkeys((*methods, *PUBLIC_FRAME_METHODS.get(type_name, ()))))
    if isinstance(type_obj, type) and type_obj is not BaseFrame and issubclass(type_obj, BaseFrame):
        base_methods = PUBLIC_FRAME_METHODS.get("BaseFrame", ())
        methods = tuple(dict.fromkeys((*methods, *base_methods)))
    if methods:
        lines.append("  Methods:")
        for method in methods:
            lines.append(f"    .{method}()")
        lines.append("")

    # Producer/consumer edges
    producers: list[str] = []
    for desc in REGISTRY.descriptors:
        if isinstance(desc, OperatorCapability):
            output = desc.output_family
            if isinstance(output, SameAsInputFamily):
                continue
            if str(output) == type_name:
                producers.append(desc.help_target)
        if isinstance(desc, BoundaryCapability) and desc.output_family == type_name:
            producers.append(desc.help_target)

    if producers:
        lines.append("  Produced by:")
        for p in sorted(producers):
            lines.append(f"    {p}")
        lines.append("")

    recovery_paths = [
        desc.help_target
        for desc in REGISTRY.descriptors
        if isinstance(desc, RecoveryCapability) and desc.restored_family == type_name
    ]
    if recovery_paths:
        lines.append("  Acquired or recovered by:")
        for target in sorted(recovery_paths):
            lines.append(f"    {target}")
        lines.append("")

    consumers = REGISTRY.constructor_consumers.get(type_name, ())
    if consumers:
        lines.append("  Consumed by:")
        for c in sorted(consumers)[:5]:
            lines.append(f"    {c}")
        lines.append("")

    if type_name == "QualityReport":
        lines.append(
            "  Quality verdict: report.overall_status; report.state is "
            "ArtifactState materialization metadata."
        )
        lines.append(
            "  Stop only for blocking correctness issues; warnings are advisory "
            "and must be disclosed when analysis continues."
        )
        lines.append("")

    lines.append(f'  Call marivo.help("analysis.{type_name}") for updates.')

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


# ---------------------------------------------------------------------------
# Error renderer
# ---------------------------------------------------------------------------


def _render_error_contract(error_name: str) -> str:
    """Render static error contract for an error class."""
    # Strip "Error" suffix to get the kind.
    kind = error_name[:-5] if error_name.endswith("Error") else error_name

    lines: list[str] = []
    lines.append(error_name)
    lines.append(f"  kind: {kind}")
    lines.append("  base: AnalysisError")
    lines.append("")

    # Find constraints that map to this error kind.
    matching = [c for c in CONSTRAINTS.values() if c.error_kind == kind]
    if matching:
        lines.append("  Constraints:")
        for c in matching:
            lines.append(f"    {c.id}: {c.title}")
        lines.append("")

    lines.append(f"  Call marivo.help({error_name}) for the concrete repair on an instance.")

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


def _render_next_help_call(target: LiveHelpTarget) -> str:
    """Render the exact owning public help invocation for a repair target."""
    if target.canonical_id is None:
        return "marivo.help()"
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _render_error_briefing(error_name: str, error_kind: str | None, error_instance: object) -> str:
    """Render concrete repair for an error instance."""
    lines: list[str] = []
    lines.append(error_name)
    if error_kind:
        lines.append(f"  kind: {error_kind}")
    lines.append("")

    # Extract stable fields from the instance.
    err = error_instance
    message = getattr(err, "message", None)
    if message:
        lines.append(f"  message: {message}")

    expected = getattr(err, "expected", None)
    if expected:
        lines.append(f"  expected: {expected}")

    received = getattr(err, "received", None)
    if received:
        lines.append(f"  received: {received}")

    location = getattr(err, "location", None)
    if location:
        lines.append(f"  location: {location}")

    hint = getattr(err, "hint", None)
    if hint:
        lines.append(f"  hint: {hint}")

    repair = getattr(err, "repair", None)
    if repair is not None:
        lines.append("")
        lines.append("  Repair:")
        action = getattr(repair, "action", None)
        if action:
            lines.append(f"    action: {action}")
        help_target = getattr(repair, "help_target", None)
        if isinstance(help_target, LiveHelpTarget):
            lines.append(f"    next_help: {_render_next_help_call(help_target)}")
        snippet = getattr(repair, "snippet", None)
        if snippet:
            lines.append("    snippet:")
            for sline in snippet.splitlines():
                lines.append(f"      {sline}")
        candidates = getattr(repair, "candidates", None)
        if candidates:
            lines.append(f"    candidates: {', '.join(candidates)}")
    else:
        lines.append("")
        lines.append("  No concrete repair attached.")

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


# ---------------------------------------------------------------------------
# Reference briefing renderer
# ---------------------------------------------------------------------------


def _render_reference_briefing(
    reference_id: str,
    ref: object,
    project: SemanticProject | None,
) -> str:
    """Render bounded semantic object briefing."""
    del reference_id, project
    from marivo._help.object_briefing import render_semantic_object

    return render_semantic_object(ref)


def _cumulative_composition_briefing(composition: object) -> list[str]:
    """Anchor-aware briefing lines for a CumulativeComposition IR."""
    anchor = getattr(composition, "anchor", "all_history")
    if isinstance(anchor, tuple) and anchor and anchor[0] == "trailing":
        return [
            "cumulative: trailing rolling-window",
            (
                "note: trailing values are a rolling window; rolling-series "
                "autocorrelation can pollute correlation and hypothesis-test "
                "interpretation. compare requires an identical anchor payload."
            ),
        ]
    if isinstance(anchor, tuple) and anchor and anchor[0] == "grain_to_date":
        grain = anchor[1] if len(anchor) > 1 else "?"
        return [
            f"cumulative: grain_to_date reset grain={grain}",
            (
                "note: grain_to_date values reset at period boundaries; "
                "non-stationary within and across periods. compare is conditional "
                "(single-period, boundary-anchored windows)."
            ),
        ]
    return [
        "cumulative: all_history running total",
        (
            "note: cumulative values are running totals anchored to all history; "
            "shared monotonic trend can pollute correlation and hypothesis-test "
            "interpretation. compare accepts compatible observed levels with exact "
            "evaluation endpoints; it does not assert interval-flow equivalence and "
            "source revision remains unverified."
        ),
    ]


# ---------------------------------------------------------------------------
# Public render entry point
# ---------------------------------------------------------------------------


def _descriptor_render_budget(
    descriptor: AnalysisHelpDescriptor,
) -> tuple[AnalysisHelpRenderClass, tuple[LiveHelpTarget, ...], int] | None:
    """Return the active Slice 2 render contract for one descriptor."""

    if isinstance(descriptor, AnalysisNavigationTopic):
        return (
            descriptor.render_class,
            (*descriptor.members, *REGISTRY.cross_links(descriptor.canonical_id)),
            0,
        )
    if isinstance(descriptor, AnalysisMethodFamily):
        return (
            "navigation",
            (*descriptor.members, *REGISTRY.cross_links(descriptor.canonical_id)),
            0,
        )
    if isinstance(descriptor, AnalysisArtifactFamilyContract):
        return "public_type", REGISTRY.cross_links(descriptor.canonical_id), 0
    return None


def render_help_target(
    resolved: (
        ResolvedLiveTarget[AnalysisHelpDescriptor] | ResolvedLiveTarget[CapabilityDescriptor]
    ),
    *,
    project: SemanticProject | None = None,
    original_target: object = None,
) -> str:
    """Render a resolved help target to a bounded text string.

    Parameters
    ----------
    resolved:
        The resolved help target from ``resolve_help_target``.
    project:
        Optional SemanticProject for semantic briefing resolution.
    original_target:
        The original target object (needed for semantic ref and error instance
        rendering, since the resolver only extracts the id/kind).
    """
    if resolved.kind == "descriptor" and resolved.descriptor is not None:
        rendered = _render_descriptor_help(resolved.descriptor)
        budget = _descriptor_render_budget(resolved.descriptor)
        if budget is None:
            return _with_python_imports(rendered)
        render_class, routes, examples = budget
        return _with_python_imports(
            rendered,
            render_class=render_class,
            outgoing_routes=routes,
            examples_or_snippets=examples,
        )

    if resolved.kind == "type_contract" and resolved.type_name is not None:
        rendered = _render_type_help(resolved.type_name)
        if resolved.type_name not in ARTIFACT_FAMILIES:
            return _with_python_imports(rendered)
        contract = REGISTRY.artifact_contract(resolved.type_name)
        return _with_python_imports(
            rendered,
            render_class="public_type",
            outgoing_routes=REGISTRY.cross_links(contract.canonical_id),
            examples_or_snippets=0,
        )

    if resolved.kind == "error_contract" and resolved.error_name is not None:
        return _with_python_imports(_render_error_contract(resolved.error_name))

    if resolved.kind == "error_briefing" and resolved.error_name is not None:
        return _with_python_imports(
            _render_error_briefing(
                resolved.error_name,
                resolved.error_kind,
                resolved.original,
            )
        )

    if resolved.kind == "reference_briefing" and resolved.reference_id is not None:
        if resolved.original is None:
            raise RuntimeError("reference_briefing requires original target")
        return _with_python_imports(
            _render_reference_briefing(resolved.reference_id, resolved.original, project)
        )

    raise RuntimeError(f"cannot render resolved target: {resolved}")
