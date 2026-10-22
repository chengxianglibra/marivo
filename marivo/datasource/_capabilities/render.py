"""Bounded datasource help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from marivo._authoring.model import AuthoringCapability
from marivo._authoring.render import effect_lines
from marivo.datasource._capabilities.registry import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.datasource.constraints import iter_constraints
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.reflect import import_registered_callable as import_callable
from marivo.introspection.live.render import bounded_help as _bounded
from marivo.introspection.live.render import render_fingerprint
from marivo.introspection.live.resolve import ResolvedLiveTarget
from marivo.introspection.live.targets import canonical_target

if TYPE_CHECKING:
    from marivo.datasource._capabilities.model import DatasourceTypeContract


_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"
_ANALYSIS_IMPORT = "import marivo.analysis as mv"
_MARIVO_IMPORT = "import marivo"


def _target_text(target: LiveHelpTarget) -> str:
    return _help_invocation(target)


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


def _navigation_lines(canonical_id: str = "") -> tuple[str, ...]:
    return tuple(
        f'    {route.label}: {route.summary} -> marivo.help("{route.target.display}")'
        for route in REGISTRY.navigation_routes(canonical_id)
    )


def render_root_help() -> str:
    """Render the datasource root's bounded task choices from its native registry."""
    from marivo.introspection.live.model import EnvironmentFingerprint

    return _bounded(
        "\n".join(
            (
                "marivo.datasource",
                render_fingerprint(EnvironmentFingerprint.current(), reveal=False),
                "",
                "Python imports:",
                f"  {_DATASOURCE_IMPORT}",
                "",
                "Choose by current physical question:",
                *_navigation_lines(),
                "",
                "  A source schema or sample does not establish reusable business meaning.",
            )
        ),
        root=True,
    )


def _render_navigation(descriptor: AuthoringCapability) -> str:
    return _bounded(
        "\n".join(
            (
                descriptor.canonical_id,
                f"  {descriptor.summary}",
                "",
                "  Choose by need:",
                *_navigation_lines(descriptor.canonical_id),
            )
        )
    )


def _render_descriptor(descriptor: AuthoringCapability) -> str:
    if REGISTRY.navigation_routes(descriptor.canonical_id):
        return _render_navigation(descriptor)

    lines = [descriptor.canonical_id, f"  {descriptor.summary}", ""]
    if descriptor.kind == "boundary":
        lines.append(
            "  Continue: " + ", ".join(_target_text(target) for target in descriptor.see_also)
        )
        return _bounded("\n".join(lines))
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
    effects = descriptor.effects
    assert effects is not None
    lines.extend(effect_lines(effects))
    if descriptor.minimal_example is not None:
        lines.append("  Example:")
        lines.extend(
            f"    {line}" if line else "" for line in descriptor.minimal_example.splitlines()
        )
    constraints = _constraints(descriptor)
    if constraints:
        lines.append("  Constraints:")
        lines.extend(f"    {constraint}" for constraint in constraints)
    if descriptor.output_family in {contract.name for contract in TYPE_CONTRACTS.values()}:
        lines.append(
            f'  Result Help: marivo.help("datasource.{canonical_target(descriptor.output_family)}")'
        )
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
    lines.extend("  " + line for line in contract.guidance)
    if contract.consumers:
        lines.append(
            "  Consumers: " + ", ".join(_target_text(target) for target in contract.consumers)
        )
    if "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    return _bounded("\n".join(lines))


def _help_invocation(target: LiveHelpTarget) -> str:
    if target.canonical_id is None:
        return f'marivo.help("{target.surface}")'
    return f'marivo.help("{target.surface}.{target.canonical_id}")'


def _render_error_contract(error_name: str) -> str:
    lines = [
        error_name,
        "  Datasource error contract.",
        *(f"  {line}" for line in (inspect.getdoc(ERROR_TYPES[error_name]) or "").splitlines()),
        "  Concrete repair guidance is available only when an instance carries repair.help_target.",
    ]
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
    raise RuntimeError(f"unsupported datasource help resolution: {resolved.kind}")
