"""Common formatting for registered Help signatures, types, and links."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from marivo._kernel.errors import LiveHelpTarget
from marivo.introspection.capabilities.registry import TypeContractSpec


def help_invocation(target: LiveHelpTarget) -> str:
    return f'marivo.help("{target.display}")'


def type_contract(contracts: Mapping[type, TypeContractSpec], type_name: str) -> TypeContractSpec:
    for contract in contracts.values():
        if contract.name == type_name:
            return contract
    raise RuntimeError(f"unknown type contract: {type_name}")


def type_lines(
    title: str,
    contract: TypeContractSpec,
    *,
    format_target: Callable[[LiveHelpTarget], str] = help_invocation,
) -> list[str]:
    lines = [title]
    if contract.producers:
        lines.append("  Producers: " + ", ".join(map(format_target, contract.producers)))
    if contract.public_properties:
        lines.append("  Public fields: " + ", ".join(contract.public_properties))
    if contract.public_methods:
        lines.append("  Public consumption: " + ", ".join(contract.public_methods))
    lines.extend("  " + line for line in contract.guidance)
    if contract.consumers:
        lines.append("  Consumers: " + ", ".join(map(format_target, contract.consumers)))
    lines.extend(f"  {note}" for note in contract.notes)
    if contract.example is not None:
        lines.append("  Example:")
        lines.extend(f"    {line}" for line in contract.example.splitlines())
    return lines


def with_imports(text: str, imports: tuple[str, ...]) -> str:
    title, *body = text.splitlines()
    return "\n".join((title, "  Python imports:", *(f"    {item}" for item in imports), "", *body))
