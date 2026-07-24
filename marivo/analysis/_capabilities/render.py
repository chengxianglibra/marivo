"""Render typed help text from the capability registry.

Consumes the immutable registry, resolver, surface limits, and constraints
to produce bounded root and focused help text strings.

All names are private to ``marivo.analysis``.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from marivo.analysis._capabilities.model import (
    ROOT_GROUP_ORDER,
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
    PUBLIC_OBJECT_METHODS,
    PUBLIC_OBJECT_PROPERTIES,
    REGISTRY,
)
from marivo.analysis._capabilities.surface import TYPE_REGISTRY
from marivo.analysis.constraints import CONSTRAINTS, get_constraint
from marivo.introspection.constraints import Constraint
from marivo.introspection.live.model import SURFACE_LIMITS, EnvironmentFingerprint
from marivo.introspection.live.reflect import import_registered_callable
from marivo.introspection.live.render import render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.refs import SemanticKind

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

# The analysis surface is consumed as ``mv`` (mirroring ``md``/``ms`` for the
# datasource/semantic surfaces). Help text uses ``mv.`` throughout, so every
# page states the import so examples run from a cold start (see issue #22).
_ANALYSIS_IMPORT = "import marivo.analysis as mv"

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


def _with_python_imports(text: str) -> str:
    """Prefix a focused help page with the ``mv`` import so it runs cold-start."""
    lines = text.splitlines()
    return enforce_budget(
        "\n".join(
            (
                lines[0],
                "  Python imports:",
                f"    {_ANALYSIS_IMPORT}",
                "",
                *lines[1:],
            )
        ),
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
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
    "session_state": "Session state",
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
    """Render the root help page with fingerprint, groups, and type algebra.

    ``root_visibility="direct"`` descriptors appear as individual entries.
    ``root_visibility="grouped"`` descriptors collapse to their grouping
    topic (e.g. ``discover``, ``transform``) so the root stays bounded.
    """
    lines: list[str] = []

    # Fingerprint (exact paths shown: root help uses reveal=True).
    fp = environment_fingerprint()
    lines.extend(render_fingerprint(fp, reveal=True).split("\n"))
    lines.extend(("", "Python imports:", f"  {_ANALYSIS_IMPORT}", ""))

    # Capability groups
    lines.append("Capabilities:")
    for group in ROOT_GROUP_ORDER:
        group_descs = [d for d in REGISTRY.descriptors if d.root_group == group]
        if not group_descs:
            continue

        # Separate direct entries from grouped entries.
        direct_descs = [d for d in group_descs if d.root_visibility == "direct"]
        grouped_descs = [d for d in group_descs if d.root_visibility == "grouped"]

        # Collect the unique grouping topics (e.g. "discover", "transform").
        seen_topics: set[str] = set()
        topic_descs: list[CapabilityDescriptor] = []
        for desc in grouped_descs:
            topic = _grouping_topic_for(desc)
            if topic is not None and topic not in seen_topics:
                seen_topics.add(topic)
                topic_descs.append(REGISTRY.by_help_target(topic))

        # Skip groups that have no visible entries (no direct, no topic).
        if not direct_descs and not topic_descs:
            continue

        label = _GROUP_LABELS.get(group, group.replace("_", " ").title())
        lines.append(f"  {label} [{group}]:")

        # Direct entries get their own line.
        for desc in direct_descs:
            lines.append(f"    {desc.public_entrypoint:<44} {desc.summary}")

        # Grouped entries with a topic collapse to the topic.
        for desc in topic_descs:
            lines.append(f"    {desc.public_entrypoint:<44} {desc.summary}")

        lines.append("")

    # Type algebra
    lines.append("Type algebra:")
    for row in REGISTRY.type_algebra_rows():
        lines.append(f"  {row.render()}")
    lines.append("")

    # Drill-down instruction
    lines.append('Call mv.help("<target>") for detail on any capability.')

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.root_help_max_lines,
        max_codepoints=SURFACE_LIMITS.root_help_max_codepoints,
    )


def _grouping_topic_for(desc: CapabilityDescriptor) -> str | None:
    """Return the grouping topic for a grouped descriptor, or None.

    Grouped descriptors that share a prefix (e.g. ``discover.*``,
    ``transform.*``) collapse to the prefix topic.  Other grouped
    descriptors (e.g. ``MetricFrame.as_scalar``) do not have a
    grouping topic and are omitted from the root index.
    """
    if desc.root_visibility != "grouped":
        return None
    # Check for dotted prefixes that have a registered grouping topic.
    for prefix in ("discover.", "transform.", "catalog.", "runtime_metric."):
        if desc.id.startswith(prefix):
            topic = prefix.rstrip(".")
            try:
                REGISTRY.by_help_target(topic)
                return topic
            except KeyError:
                return None
    # session.evidence.* collapses under recovery.
    if desc.id.startswith("session.evidence."):
        try:
            REGISTRY.by_help_target("recovery")
            return "recovery"
        except KeyError:
            return None
    # session.jobs, session.get_frame, etc. collapse under recovery.
    if desc.id.startswith("session.") and desc.root_group == "recovery":
        try:
            REGISTRY.by_help_target("recovery")
            return "recovery"
        except KeyError:
            return None
    # BaseFrame.show, BaseFrame.contract collapse under artifacts.
    if desc.id.startswith("BaseFrame."):
        try:
            REGISTRY.by_help_target("artifacts")
            return "artifacts"
        except KeyError:
            return None
    return None


# ---------------------------------------------------------------------------
# Focused descriptor renderer
# ---------------------------------------------------------------------------


def _format_input_families(desc: OperatorCapability) -> list[str]:
    """Format accepted input families for display."""
    rows: list[str] = []
    for param, families in desc.accepted_inputs.items():
        family_list = ", ".join(sorted(families))
        rows.append(f"  {param}: {family_list}")
    return rows


def _format_output_family(desc: OperatorCapability) -> str:
    """Format output family for display."""
    output = desc.output_family
    if isinstance(output, SameAsInputFamily):
        return f"same as {output.parameter}"
    return str(output)


def _resolve_callable(desc: CapabilityDescriptor) -> object | None:
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


def _related_targets(desc: CapabilityDescriptor) -> list[str]:
    """Compute bounded related help targets for a descriptor.

    Related targets are other capabilities that share an input family
    or output family, or siblings under the same grouping topic (e.g.
    other ``discover.*`` objectives).  The result is de-duplicated,
    excludes the descriptor itself, and is capped at 5 entries.
    """
    related: list[str] = []
    seen: set[str] = set()

    def _add(target: str) -> None:
        if target != desc.help_target and target not in seen:
            seen.add(target)
            related.append(target)

    if desc.help_target == "attribute":
        _add("AttributionMode")
    elif desc.help_target == "AttributionMode":
        _add("attribute")

    # Grouping-topic siblings (e.g. discover.*, transform.*).
    for prefix in ("discover.", "transform.", "catalog.", "session.evidence."):
        if desc.id.startswith(prefix):
            for other in REGISTRY.descriptors:
                if other.id.startswith(prefix) and other.id != desc.id:
                    _add(other.help_target)
            break

    # Shared input families — other operators accepting the same family.
    if isinstance(desc, (OperatorCapability, BoundaryCapability)):
        desc_families: set[str] = set()
        for families in desc.accepted_inputs.values():
            desc_families.update(families)
        for other in REGISTRY.descriptors:
            if not isinstance(other, (OperatorCapability, BoundaryCapability)):
                continue
            if other.id == desc.id:
                continue
            other_families: set[str] = set()
            for families in other.accepted_inputs.values():
                other_families.update(families)
            if desc_families & other_families:
                _add(other.help_target)

    # Shared output family — other operators producing the same family.
    if isinstance(desc, OperatorCapability):
        output = desc.output_family
        output_str = (
            _format_output_family(desc) if isinstance(output, SameAsInputFamily) else str(output)
        )
        for other in REGISTRY.descriptors:
            if not isinstance(other, OperatorCapability):
                continue
            if other.id == desc.id:
                continue
            other_output = other.output_family
            other_output_str = (
                _format_output_family(other)
                if isinstance(other_output, SameAsInputFamily)
                else str(other_output)
            )
            if output_str == other_output_str:
                _add(other.help_target)

    return related[:5]


def _grouping_members(desc: CapabilityDescriptor) -> list[CapabilityDescriptor]:
    """Return the real registered members taught by a non-invokable topic."""
    if desc.callable_path is not None:
        return []
    members: list[CapabilityDescriptor] = []
    for candidate in REGISTRY.descriptors:
        if candidate is desc or candidate.callable_path is None:
            continue
        if desc.id in {
            "discover",
            "transform",
            "catalog",
            "runtime_metric",
            "boundary",
            "session",
        }:
            if candidate.id.startswith(f"{desc.id}."):
                members.append(candidate)
        elif (desc.id == "recovery" and candidate.root_group == "recovery") or (
            desc.id == "artifacts"
            and (candidate.id.startswith("BaseFrame.") or candidate.id == "boundary.to_pandas")
        ):
            members.append(candidate)
    if desc.id == "artifacts":
        read_order = {
            "BaseFrame.show": 0,
            "BaseFrame.contract": 1,
            "boundary.to_pandas": 2,
        }
        return sorted(
            members,
            key=lambda item: (read_order.get(item.id, len(read_order)), item.help_target),
        )
    return sorted(members, key=lambda item: item.help_target)


def _render_descriptor_help(desc: CapabilityDescriptor) -> str:
    """Render focused help for a single capability descriptor."""
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

    if is_property:
        return_type = _property_return_type(callable_obj)
        if return_type is not None:
            lines.append(f"  Returns: {return_type}")
        lines.append(f"  Inspect: {desc.public_entrypoint}.show()")

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
                        part += f": {ann}"
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
        lines.append(f"  Output family: {_format_output_family(desc)}")

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

    # Example (from docstring)
    if callable_obj is not None:
        doc = inspect.getdoc(callable_obj) or ""
        example = _extract_example(doc)
        if example:
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
        output = desc.output_family
        output_str = (
            _format_output_family(desc) if isinstance(output, SameAsInputFamily) else str(output)
        )
        consumers = REGISTRY.constructor_consumers.get(output_str, ())
        if consumers:
            lines.append("")
            lines.append("  Consumed by:")
            for consumer_id in sorted(consumers)[:5]:
                lines.append(f"    {consumer_id}")

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


def _render_type_help(type_name: str) -> str:
    """Render focused help for a registered public type.

    Never render dataclass/Pydantic constructors, ``_df``,
    ``_NEXT_INTENTS``, ``_GATED_INTENTS``, private fields, or inherited
    Pydantic mechanics.
    """
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
            first_prose_line = stripped
            break
        if first_prose_line:
            lines.append(f"  {first_prose_line}")
    lines.append("")

    variants = {
        "ArtifactIssue": (
            "DataQualityIssue",
            "ComparabilityIssue",
            "EvidenceAvailabilityIssue",
        ),
        "CandidateSelection": (
            "PointAnomalySelection",
            "PeriodShiftSelection",
            "DriverAxisSelection",
            "SliceSelection",
            "WindowSelection",
            "CrossSectionalOutlierSelection",
        ),
        "QualityReport": (
            "QualityReport[metric]",
            "QualityReport[event_journey]",
        ),
    }.get(type_name)
    if variants:
        lines.append("  Closed variants:")
        for variant in variants:
            lines.append(f"    {variant}")
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

    props = PUBLIC_OBJECT_PROPERTIES.get(type_name, ())
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
    methods = PUBLIC_OBJECT_METHODS.get(type_name, ())
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

    consumers = REGISTRY.constructor_consumers.get(type_name, ())
    if consumers:
        lines.append("  Consumed by:")
        for c in sorted(consumers)[:5]:
            lines.append(f"    {c}")
        lines.append("")

    lines.append(f'  Call mv.help("{type_name}") for updates.')

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

    lines.append(f"  Call mv.help({error_name}) for the concrete repair on an instance.")

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


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
        if help_target:
            display = getattr(help_target, "display", None) or str(help_target)
            lines.append(f"    help_target: {display}")
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
    from marivo.refs import Ref
    from marivo.semantic.catalog import CatalogEntry

    # CatalogEntry wraps a Ref; unwrap it so the renderer
    # works with the untyped id that IR lookups expect.
    if isinstance(ref, CatalogEntry):
        ref = ref.ref

    if type(ref) is not Ref:
        raise RuntimeError(f"expected exact Ref, got {type(ref).__name__}")

    # Resolve project if not provided.
    resolved_project = project
    if resolved_project is None:
        try:
            from marivo.semantic.loader import find_project

            resolved_project = find_project()
            if resolved_project is not None:
                resolved_project.load()
        except Exception:
            resolved_project = None

    if resolved_project is None:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise

        _raise(
            ErrorKind.INVALID_REF,
            (
                f"Cannot resolve project for mv.help({ref.path!r}). "
                "No loaded semantic project found. "
                "Pass project=project explicitly: mv.help(ref, project=project)."
            ),
            cls=SemanticRuntimeError,
        )

    reg = getattr(resolved_project, "_registry", None)
    if reg is None:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise

        _raise(
            ErrorKind.INVALID_REF,
            f"Call ms.load() to load the semantic project before mv.help({ref.path!r}).",
            cls=SemanticRuntimeError,
        )

    # Look up the IR.
    ir: object = None
    kind_str = str(ref.kind) if hasattr(ref, "kind") else "semantic"

    if hasattr(ref, "kind"):
        ref_kind = ref.kind
        if ref_kind == SemanticKind.METRIC:
            ir = reg.metrics.get(ref.path)
        elif ref_kind == SemanticKind.ENTITY:
            ir = reg.entities.get(ref.path)
        elif ref_kind in (SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION):
            ir = reg.dimensions.get(ref.path)
        elif ref_kind == SemanticKind.MEASURE:
            ir = reg.measures.get(ref.path)
        elif ref_kind == SemanticKind.RELATIONSHIP:
            ir = reg.relationships.get(ref.path)

    if ir is None:
        from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise

        _raise(
            ErrorKind.INVALID_REF,
            (
                f"{kind_str} {ref.path!r} not found in loaded project. "
                "Call catalog.metrics.show() to browse available refs."
            ),
            cls=SemanticRuntimeError,
        )

    # Build the briefing lines.
    lines: list[str] = [f"{kind_str}: {ref.path}"]

    unit = getattr(ir, "unit", None)
    if unit:
        lines.append(f"unit: {unit}")

    ai = getattr(ir, "ai_context", None)
    if ai is not None:
        if getattr(ai, "business_definition", None):
            lines.append(f"business_definition: {ai.business_definition}")
        if getattr(ai, "guardrails", None):
            lines.append("guardrails:")
            for g in list(ai.guardrails)[:3]:
                lines.append(f"  - {g}")
    composition = getattr(ir, "composition", None)
    comp_kind = getattr(composition, "kind", None)
    if comp_kind == "cumulative":
        lines.extend(_cumulative_composition_briefing(composition))

    lines.append("")
    lines.append(
        "use: catalog.metrics.show() to enumerate; "
        "pass catalog.metrics.get('<local_name>').ref to session.observe(...)"
    )

    text = "\n".join(lines)
    return enforce_budget(
        text,
        max_lines=SURFACE_LIMITS.focused_help_max_lines,
        max_codepoints=SURFACE_LIMITS.focused_help_max_codepoints,
    )


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
            "interpretation. compare is gated; observe the base flow metric instead."
        ),
    ]


# ---------------------------------------------------------------------------
# Public render entry point
# ---------------------------------------------------------------------------


def render_help_target(
    resolved: ResolvedLiveTarget[CapabilityDescriptor],
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
        return _with_python_imports(_render_descriptor_help(resolved.descriptor))

    if resolved.kind == "type_contract" and resolved.type_name is not None:
        return _with_python_imports(_render_type_help(resolved.type_name))

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
