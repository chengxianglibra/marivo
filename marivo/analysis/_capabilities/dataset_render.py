"""Bounded native private rendering; all facts and routes come from owner inputs."""

from __future__ import annotations

import inspect

from typing_extensions import get_overloads

from marivo.analysis._capabilities.dataset_model import (
    CallableInput,
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


def _help_target(target: str) -> str:
    """Keep explicitly qualified links with their native owner."""
    if target == "authoring" or target.startswith(("semantic.", "datasource.", "ontology.")):
        return target
    return "analysis." + target


def render(registry: DatasetDisclosureRegistry, target: object = "") -> str:
    resolved = registry.resolve(target)
    if resolved.kind == "error_contract":
        lines = [
            resolved.error_name or "AnalysisError",
            "  Analysis error contract.",
            "  Fields: message, expected, received, location and repair. Inspect the instance facts before choosing a repair.",
        ]
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
        summaries = dict(descriptor.route_summaries)
        rendered_families: set[str] = set()
        for t in routes:
            if t in summaries:
                lines.append("  marivo.help('" + _help_target(t) + "') — " + summaries[t])
                continue
            member = registry.by_canonical_id(t)
            if isinstance(member, CallableInput) and member.discovery_family is not None:
                family = member.discovery_family
                if family in rendered_families:
                    continue
                rendered_families.add(family)
                lines.append("  " + registry.by_canonical_id(family).summary)
                variants = tuple(
                    route
                    for route in routes
                    if isinstance((entry := registry.by_canonical_id(route)), CallableInput)
                    and entry.discovery_family == family
                )
                lines.extend("    marivo.help('analysis." + variant + "')" for variant in variants)
            else:
                lines.append("  marivo.help('analysis." + t + "') — " + member.summary)
    elif isinstance(descriptor, ReadCapability):
        render_class = "exact_callable"
        lines.append("Call: " + descriptor.public_entrypoint)
        lines.append("Result: " + (descriptor.output_type or descriptor.result_kind))
        lines.append("Bound: " + descriptor.read_bound)
        lines.append("Owner: " + descriptor.receiver_family)
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
        lines.extend("See: marivo.help('" + _help_target(t) + "')" for t in routes)
        examples = 1
    elif isinstance(descriptor, CallableInput):
        render_class = "exact_callable"
        signatures = tuple(
            dict.fromkeys(
                str(
                    signature.replace(
                        parameters=tuple(
                            p
                            for name, p in signature.parameters.items()
                            if name not in ("self", "cls")
                        ),
                        return_annotation=inspect.Signature.empty,
                    )
                )
                for b in descriptor.bindings
                for signature in (
                    tuple(
                        inspect.signature(overload) for overload in get_overloads(b.implementation)
                    )
                    if callable(b.implementation) and get_overloads(b.implementation)
                    else (b.signature,)
                )
            )
        )
        lines.extend("Signature: " + descriptor.public_entrypoint + s for s in signatures)
        lines.append("Returns: " + descriptor.output)
        for parameter in descriptor.parameters:
            lines.append("Input " + parameter.name + ": " + parameter.acquisition)
            lines.extend(
                "  Acquire " + parameter.name + ": marivo.help('" + _help_target(t) + "')"
                for t in parameter.targets
            )
        disclosed = {descriptor.summary}
        for label, facts in (
            ("Constraint", descriptor.constraints),
            ("Effects", (descriptor.effects,)),
            ("Failure/repair", descriptor.failures),
        ):
            for fact in facts:
                if fact not in disclosed:
                    lines.append(label + ": " + fact)
                    disclosed.add(fact)
        exports = {e.name: e for p in registry.providers for e in p.exports if e.name != "session"}
        bindings = tuple(name for name in descriptor.example.requires if name in exports)
        required = tuple(name for name in descriptor.example.requires if name not in exports)
        lines.append("Example inputs: " + (", ".join(required) or "none"))
        prelude_lines = []
        if bindings or "mv." in descriptor.example.code:
            prelude_lines.append("import marivo.analysis as mv")
        if "ms." in descriptor.example.code:
            prelude_lines.append("import marivo.semantic as ms")
        prelude_lines.extend(name + " = mv." + name for name in bindings)
        # Owner examples may already include the canonical imports or bindings.
        # Add only the missing prelude lines so copyable snippets stay concise.
        existing_lines = set(descriptor.example.code.splitlines())
        prelude_lines = [line for line in prelude_lines if line not in existing_lines]
        prelude = "\n".join(prelude_lines) + ("\n" if prelude_lines else "")
        lines.extend(
            (
                "Example:",
                prelude + descriptor.example.code,
            )
        )
        if descriptor.example.outcome not in (descriptor.output, "The receiver-bound result."):
            lines.append("Expected: " + descriptor.example.outcome)
        routes = registry.callable_routes(descriptor)
        parameter_routes = {t for p in descriptor.parameters for t in p.targets}
        lines.extend(
            "See: marivo.help('" + _help_target(t) + "')"
            for t in routes
            if t not in parameter_routes
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
            lines.append("Methods (exact targets):")
            for name in binding.methods:
                method = registry.by_callable(getattr(binding.implementation, name))
                lines.append(f"  analysis.{method.canonical_id}: {method.summary}")
            operators = tuple(
                member
                for member in registry.descriptors
                if isinstance(member, CallableInput)
                and any(
                    b.receiver is binding.implementation
                    and getattr(b.implementation, "__name__", "").startswith("__")
                    for b in member.bindings
                )
            )
            if operators:
                lines.append("Operators (exact targets):")
                lines.extend(f"  analysis.{member.canonical_id}" for member in operators)
        if isinstance(descriptor, TypeInput):
            for index, variant in enumerate(descriptor.variants, 1):
                lines.append(
                    f"Value variant {index}"
                    + (f" ({variant.applicability})" if variant.applicability else "")
                    + ": "
                    + "; ".join(f.name + ": " + f.annotation for f in variant.fields)
                )
            routes = tuple(dict.fromkeys(descriptor.producers + descriptor.consumers))
            if len(routes) > ANALYSIS_HELP_RENDER_BUDGETS["public_type"].max_outgoing_routes:
                grouped_routes: list[str] = []
                for route in routes:
                    leaf = (
                        None
                        if registry.is_type_attribute(route)
                        else registry.by_canonical_id(route)
                    )
                    group = leaf.discovery_group if isinstance(leaf, CallableInput) else None
                    grouped_routes.append(group or route)
                routes = tuple(dict.fromkeys(grouped_routes))
            lines.extend("See: marivo.help('" + _help_target(t) + "')" for t in routes)
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
