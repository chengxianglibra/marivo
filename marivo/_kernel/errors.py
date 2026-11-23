"""Neutral owner marker for structured errors across public surfaces."""

from typing import Literal, NoReturn, TypeAlias


class MarivoError(Exception):
    """Preserve an owning surface's typed repair across backend boundaries."""


DefinitionErrorKind: TypeAlias = Literal[
    "invalid_value",
    "cycle",
    "ownership_conflict",
    "unresolved",
    "unknown_rule_version",
]


class DefinitionGraphError(ValueError):
    """Structured failure raised by the private definition layer.

    Semantic and analysis owners translate it into their public error families
    at their boundaries; ``path`` locates the failing node or value.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: DefinitionErrorKind,
        path: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind: DefinitionErrorKind = kind
        self.path = path


def invalid(
    message: str,
    *,
    kind: DefinitionErrorKind = "invalid_value",
    path: tuple[str, ...] = (),
) -> NoReturn:
    raise DefinitionGraphError(message, kind=kind, path=path)
