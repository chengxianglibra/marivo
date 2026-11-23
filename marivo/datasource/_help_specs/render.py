"""Bounded datasource help renderers backed by the live capability registry."""

from __future__ import annotations

import inspect

from marivo._authoring.model import AuthoringCapability
from marivo._authoring.render import capability_lines
from marivo.datasource._help_specs.manifest import ERROR_TYPES, REGISTRY, TYPE_CONTRACTS
from marivo.datasource.constraints import iter_constraints
from marivo.introspection.capabilities.render import (
    help_invocation,
    type_contract,
    type_lines,
    with_imports,
)
from marivo.introspection.live.model import EnvironmentFingerprint
from marivo.introspection.live.render import bounded_help as _bounded
from marivo.introspection.live.render import render_fingerprint
from marivo.introspection.live.targets import canonical_target

_DATASOURCE_IMPORT = "import marivo.datasource as md"
_SEMANTIC_IMPORT = "import marivo.semantic as ms"
_ANALYSIS_IMPORT = "import marivo.analysis as mv"
_MARIVO_IMPORT = "import marivo"


def _with_python_imports(text: str) -> str:
    imports = [_MARIVO_IMPORT, _DATASOURCE_IMPORT]
    if "ms." in text:
        imports.append(_SEMANTIC_IMPORT)
    if "mv." in text:
        imports.append(_ANALYSIS_IMPORT)
    return _bounded(with_imports(text, tuple(imports)))


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
    return _bounded(
        "\n".join(
            (
                "marivo.datasource",
                render_fingerprint(EnvironmentFingerprint.current(), reveal=True),
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
            "  Continue: " + ", ".join(help_invocation(target) for target in descriptor.see_also)
        )
        return _bounded("\n".join(lines))
    lines = capability_lines(descriptor)
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
            "  See also: " + ", ".join(help_invocation(target) for target in descriptor.see_also)
        )
    return _bounded("\n".join(lines))


def _render_type(type_name: str) -> str:
    contract = type_contract(TYPE_CONTRACTS, type_name)
    lines = type_lines(type_name, contract)
    if "show" in contract.public_methods:
        lines.append("  Detail: call .show() for bounded readable state.")
    return _bounded("\n".join(lines))


def _render_error_contract(error_name: str) -> str:
    lines = [
        error_name,
        "  Datasource error contract.",
        *(f"  {line}" for line in (inspect.getdoc(ERROR_TYPES[error_name]) or "").splitlines()),
        "  Concrete repair guidance is available only when an instance carries repair.help_target.",
    ]
    return _bounded("\n".join(lines))


def render_descriptor(descriptor: AuthoringCapability) -> str:
    return _with_python_imports(_render_descriptor(descriptor))


def render_type(type_name: str) -> str:
    return _with_python_imports(_render_type(type_name))


def render_error_contract(error_name: str) -> str:
    return _with_python_imports(_render_error_contract(error_name))
