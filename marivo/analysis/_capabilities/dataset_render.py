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
    ReadCapability,
)


def render(registry: DatasetDisclosureRegistry, target: object = "") -> str:
    resolved = registry.resolve(target)
    if resolved.kind in ("error_contract", "error_briefing"):
        from marivo.analysis.errors import AnalysisError

        lines = [resolved.error_name or "AnalysisError", "  Analysis error contract."]
        original = resolved.original
        if isinstance(original, AnalysisError):
            for name in ("message", "expected", "received", "location"):
                value = getattr(original, name)
                if value is not None:
                    lines.append(f"  {name.title()}: {value}")
        if isinstance(original, AnalysisError) and original.repair is not None:
            repair = original.repair
            lines.extend(("  Repair:", "    Kind: " + repair.kind, "    Action: " + repair.action))
            if repair.snippet:
                lines.extend("    " + line for line in repair.snippet.splitlines())
            if repair.candidates:
                lines.append("    Candidates: " + ", ".join(repair.candidates))
            help_target = repair.help_target
            qualified = help_target.surface + (
                "." + help_target.canonical_id if help_target.canonical_id else ""
            )
            lines.append('    Next help: marivo.help("' + qualified + '")')
        else:
            lines.append(
                "  Fields: message, expected, received, location and optional repair. Inspect the instance facts before choosing a repair."
            )
        text = "\n".join(lines) + "\n"
        budget = ANALYSIS_HELP_RENDER_BUDGETS["current_briefing"]
        if len(text.splitlines()) > budget.max_lines or len(text) > budget.max_codepoints:
            raise invalid("bounded analysis error Help", "error briefing exceeds the native budget")
        return text
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
        if descriptor.render_class == "root":
            from marivo.introspection.live.model import EnvironmentFingerprint
            from marivo.introspection.live.render import render_fingerprint

            lines.insert(0, render_fingerprint(EnvironmentFingerprint.current(), reveal=True))
        render_class = descriptor.render_class
        routes = descriptor.members + descriptor.related
        lines.extend(descriptor.guidance)
        for t in routes:
            lines.append(
                "  marivo.help('analysis." + t + "') — " + registry.by_canonical_id(t).summary
            )
    elif isinstance(descriptor, ReadCapability):
        render_class = "exact_callable"
        lines.append("Call: " + descriptor.public_entrypoint)
        lines.append("Result: " + (descriptor.output_type or descriptor.result_kind))
        lines.append("Bound: " + descriptor.read_bound)
        lines.append("Owner: " + descriptor.receiver_family)
        import inspect

        from marivo.introspection.live.reflect import import_registered_callable

        if descriptor.callable_path is not None:
            value = import_registered_callable(descriptor.callable_path)
            if callable(value):
                lines.append("Signature: " + str(inspect.signature(value)))
        lines.extend(
            (
                "Requires: " + descriptor.acquisition,
                "Example:",
                descriptor.example,
            )
        )
        routes = descriptor.related
        lines.extend("See: marivo.help('analysis." + t + "')" for t in routes)
        examples = 1
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
        routes = registry.callable_routes(descriptor)
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
        exports = {e.name: e for p in registry.providers for e in p.exports if e.name != "session"}
        bindings = tuple(name for name in descriptor.example.requires if name in exports)
        required = tuple(name for name in descriptor.example.requires if name not in exports)
        lines.append("Example inputs: " + (", ".join(required) or "none"))
        prelude = (
            (
                "import marivo.analysis as mv\n"
                + "\n".join(name + " = mv." + name for name in bindings)
                + "\n"
            )
            if bindings
            else ""
        )
        lines.extend(
            (
                "Example:",
                prelude + descriptor.example.code,
                "Expected: " + descriptor.example.outcome,
            )
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
            routes = registry.family_routes(descriptor)
            lines.extend("Methods: marivo.help('analysis." + t + "')" for t in routes)
            lines.append("Current legal calls and exact Help targets: dataset.contract().show()")
        elif isinstance(descriptor, TypeInput):
            for index, variant in enumerate(descriptor.variants, 1):
                lines.append(
                    f"Value variant {index}: "
                    + "; ".join(f.name + ": " + f.annotation for f in variant.fields)
                )
            routes = tuple(dict.fromkeys(descriptor.producers + descriptor.consumers))
            lines.extend("Producer: " + t for t in descriptor.producers)
            lines.extend("Consumer: " + t for t in descriptor.consumers)
            lines.extend("See: marivo.help('analysis." + t + "')" for t in routes)
            if any(b.methods for b in descriptor.bindings):
                lines.append(
                    "Exact member contract: pass the bound public method to marivo.help(value.method)."
                )
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
