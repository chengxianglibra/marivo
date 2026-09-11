"""Bounded native private rendering; all facts and routes come from owner inputs."""

from __future__ import annotations

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
    FamilyInput,
    NavigationInput,
    TypeInput,
    invalid,
)
from marivo.analysis._capabilities.dataset_registry import DatasetDisclosureRegistry
from marivo.analysis._capabilities.model import (
    ANALYSIS_HELP_RENDER_BUDGETS,
    AnalysisHelpRenderClass,
)


def render(registry: DatasetDisclosureRegistry, target: object = "") -> str:
    resolved = registry.resolve(target)
    canonical = resolved.canonical_id if resolved.canonical_id is not None else resolved.type_name
    if canonical is None:
        raise invalid("native static target", "no descriptor identity")
    descriptor = registry.by_canonical_id(canonical)
    title = "analysis" + ("." + canonical if canonical else "")
    lines = [title, descriptor.summary]
    routes: tuple[str, ...] = ()
    render_class: AnalysisHelpRenderClass
    examples = 0
    if isinstance(descriptor, NavigationInput):
        render_class = descriptor.render_class
        routes = descriptor.members
        lines.extend("  marivo.help('analysis." + t + "')" for t in routes)
    elif isinstance(descriptor, CallableInput):
        render_class = "exact_callable"
        signatures = tuple(dict.fromkeys(str(b.signature) for b in descriptor.bindings))
        lines.append("Call: " + descriptor.public_entrypoint)
        lines.extend("Signature: " + s for s in signatures)
        lines.append("Returns: " + descriptor.output)
        lines.append("Effects: " + descriptor.effects)
        lines.extend("Constraint: " + text for text in descriptor.constraints)
        lines.extend("Failure/repair: " + text for text in descriptor.failures)
        lines.extend("Input " + p.name + ": " + p.acquisition for p in descriptor.parameters)
        routes = tuple(dict.fromkeys(t for p in descriptor.parameters for t in p.targets))
        for f in registry.families.registrations:
            for consumer in f.consumers:
                if consumer.id in descriptor.registration_ids:
                    shapes = ", ".join(str(s) for s in consumer.accepted_shape_ids)
                    operands = "; ".join(
                        role + "=" + ",".join(str(s) for s in accepted)
                        for role, accepted in zip(
                            consumer.input_roles, consumer.operand_shape_ids, strict=False
                        )
                    )
                    lines.append(
                        f"Admission {consumer.id}: {shapes}; roles={consumer.input_roles}; output={consumer.output_family}"
                        + ("; " + operands if operands else "")
                    )
                    if consumer.requirements:
                        lines.append("Requirements: " + ", ".join(consumer.requirements))
        lines.extend("See: marivo.help('analysis." + t + "')" for t in routes)
        lines.append("Example inputs: " + ", ".join(descriptor.example.requires))
        lines.extend(
            ("Example:", descriptor.example.code, "Expected: " + descriptor.example.outcome)
        )
        examples = 1
    else:
        render_class = "public_type"
        lines.append("Acquire: " + descriptor.acquisition)
        lines.extend("Constraint: " + text for text in descriptor.constraints)
        for binding in descriptor.bindings:
            name = next(
                (
                    e.name
                    for p in registry.providers
                    for e in p.exports
                    if e.implementation is binding.implementation
                ),
                binding.implementation.__name__,
            )
            lines.append("Type: " + name)
            lines.extend("  " + f.name + ": " + f.annotation for f in binding.fields)
            lines.append("Methods: " + (", ".join(binding.methods) or "none"))
        if isinstance(descriptor, FamilyInput):
            lines.append("Shapes: " + ", ".join(str(s) for s in descriptor.registration.shape_ids))
            for index, variant in enumerate(descriptor.variants, 1):
                lines.append(
                    f"Row semantics variant {index}: "
                    + "; ".join(f.name + ": " + f.annotation for f in variant.fields)
                )
            routes = ("methods",)
            lines.append("Static methods: marivo.help('analysis.methods')")
            lines.append("Current legal calls: dataset.contract()")
        elif isinstance(descriptor, TypeInput):
            for index, variant in enumerate(descriptor.variants, 1):
                lines.append(
                    f"Value variant {index}: "
                    + "; ".join(f.name + ": " + f.annotation for f in variant.fields)
                )
            routes = tuple(dict.fromkeys(descriptor.producers + descriptor.consumers))
            lines.extend("Producer: " + t for t in descriptor.producers)
            lines.extend("Consumer: " + t for t in descriptor.consumers)
    text = "\n".join(lines) + "\n"
    # Translate only explicitly registered public type bindings. Reflection
    # remains attached to the real implementation, never to a signature stub.
    for provider in registry.providers:
        for export in provider.exports:
            if isinstance(export.implementation, type) and export.name != "session":
                text = text.replace(export.implementation.__name__, export.name)
    budget = ANALYSIS_HELP_RENDER_BUDGETS[render_class]
    if (
        len(text.splitlines()) > budget.max_lines
        or len(text) > budget.max_codepoints
        or len(routes) > budget.max_outgoing_routes
        or examples > budget.max_examples_or_snippets
    ):
        raise invalid("native Help within existing " + render_class + " budget", title)
    return text
